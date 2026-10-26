"""Real local stdio sessions: no provider, credentials, Docker, or native prover."""
import json
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from regen.infrastructure.codex_workspace_session import (
    CodexWorkspaceSession, ProviderFeedbackError, SessionProtocolError, SessionStalled)
from regen.infrastructure.progress_events import observe_events
from regen.tests.current.test_codex_workspace_session import SERVER, TOOLS


SERVER = SERVER.replace(
    "if mode=='timeout': time.sleep(30)",
    """if mode in ('slow_feedback', 'metadata_only', 'foreign_feedback'):
   until=time.monotonic()+.7
   while time.monotonic()<until:
    if mode=='metadata_only':
     send({'method':'thread/tokenUsage/updated','params':{'threadId':'thread-one',
         'turnId':tid,'tokenUsage':{}}})
    else:
     send({'method':'item/reasoning/textDelta','params':{
         'threadId':'foreign' if mode=='foreign_feedback' else 'thread-one',
         'turnId':tid,'itemId':'long-reasoning','delta':'working'}})
    time.sleep(.04)
   send({'method':'turn/completed','params':{'threadId':'thread-one',
       'turn':{'id':tid,'status':'completed'}}})
  elif mode=='open_silence':
   send({'method':'item/started','params':{'threadId':'thread-one','turnId':tid,
       'item':{'id':'unclosed','type':'agentMessage'}}})
   time.sleep(30)
  elif mode=='network_error':
   send({'method':'error','params':{'threadId':'thread-one','turnId':tid,
       'willRetry':False,'error':{'codexErrorInfo':{'responseStreamDisconnected':
       {'httpStatusCode':None}},'additionalDetails':'tls handshake eof'}}})
  elif mode=='timeout': time.sleep(30)""").replace(
    "elif mode=='duplicate':",
    "elif mode=='silent_after_tool': time.sleep(30)\n  elif mode=='duplicate':")


class ProviderFeedbackSessionTests(unittest.TestCase):
    def exercise(self, mode, handler=None, **options):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            session = CodexWorkspaceSession(tools=TOOLS,
                command=[sys.executable, '-u', '-c', SERVER, mode],
                allow_external_call=True, feedback_retry_mode=True,
                progress_timeout=options.pop('progress_timeout', 0), **options)
            with observe_events(lambda name, fields: events.append((name, fields))):
                try:
                    with session, redirect_stdout(io.StringIO()):
                        result = session.run('fixed prompt', handler or (lambda *a: {}),
                            artifact_dir=temp, model='fixture', timeout=.4)
                except BaseException as error:
                    result = error
            transcript = [json.loads(line) for line in
                (Path(temp)/'session-transcript.jsonl').read_text().splitlines()]
            self.assertIsNotNone(session.process.poll(), 'old owner must exit before coordinator retry')
        return result, events, transcript

    def test_silence_with_unclosed_item_has_explicit_interruption_not_clock_fault(self):
        result, events, _ = self.exercise('open_silence')
        self.assertIsInstance(result, ProviderFeedbackError)
        self.assertEqual(result.failure['kind'], 'no_complete_response')
        names = [name for name, _ in events]
        self.assertIn('generation_items_interrupted', names)
        self.assertNotIn('generation_clock_untrusted', names)
        failed = [v for n, v in events if n=='generation_provider_request_finished']
        self.assertEqual([v['outcome'] for v in failed], ['failed'])
        self.assertEqual(failed[0]['request_id'], result.failed_request_id)

    def test_failure_is_last_response_interval_not_completed_tool_or_whole_session(self):
        calls = []
        result, events, transcript = self.exercise('silent_after_tool',
            lambda *a: calls.append(a) or {'answer':'already completed'})
        self.assertIsInstance(result, ProviderFeedbackError)
        self.assertEqual(len(calls), 1)
        finishes = [v for n,v in events if n=='generation_provider_request_finished']
        self.assertEqual([v['outcome'] for v in finishes], ['success', 'failed'])
        self.assertNotEqual(finishes[0]['request_id'], result.failed_request_id)
        self.assertEqual(finishes[1]['request_id'], result.failed_request_id)
        messages = [row['message'] for row in transcript]
        sent = next(i for i, row in enumerate(transcript)
            if row['direction']=='out' and 'result' in row['message'])
        began = next(i for i,m in enumerate(messages)
            if m.get('event')=='provider_request_started' and m.get('source')=='tool_result_sent')
        self.assertGreater(began, sent)

    def test_bound_network_failure_uses_same_one_resend_class(self):
        result, events, _ = self.exercise('network_error')
        self.assertIsInstance(result, ProviderFeedbackError)
        self.assertEqual(result.failure['kind'], 'response_stream')
        self.assertNotIn('generation_network_pause_requested', [n for n,_ in events])

    def test_first_complete_response_binds_success_to_old_failed_request(self):
        result, events, _ = self.exercise('normal', retry_failed_request_id='old-failed')
        self.assertEqual(result['status'], 'completed')
        succeeded = [v for n,v in events if n=='provider_feedback_retry_succeeded']
        self.assertEqual(len(succeeded), 1)
        self.assertEqual(succeeded[0]['failed_request_id'], 'old-failed')
        valid = [v for n,v in events if n=='generation_provider_request_finished'
            and v['outcome']=='success']
        self.assertEqual(succeeded[0]['successful_request_id'], valid[0]['request_id'])

    def test_partial_or_invalid_response_never_confirms_retry(self):
        for mode in ('open_silence', 'unknown', 'sleep_unfinished'):
            with self.subTest(mode=mode):
                result, events, _ = self.exercise(mode, retry_failed_request_id='old-failed')
                self.assertIsInstance(result, SessionProtocolError)
                if mode != 'sleep_unfinished':
                    self.assertNotIn('provider_feedback_retry_succeeded', [n for n,_ in events])
                # sleep_unfinished has delivered a valid tool request first;
                # that response alone legitimately establishes transport recovery.

    def test_repeated_tool_read_loop_is_not_misclassified_as_network_failure(self):
        result, _, _ = self.exercise('readloop', progress_timeout=10)
        self.assertIsInstance(result, SessionStalled)
        self.assertNotIsInstance(result, ProviderFeedbackError)

    def test_real_partial_feedback_keeps_long_response_alive_without_early_credit(self):
        result, events, transcript = self.exercise('slow_feedback',
            retry_failed_request_id='old-failed')
        self.assertEqual(result['status'], 'completed')
        started = next(r['time_unix'] for r in transcript
            if r['message'].get('event')=='provider_request_started')
        finished = next(r['time_unix'] for r in transcript
            if r['message'].get('event')=='provider_request_finished')
        self.assertGreater(finished-started, .6)
        succeeds = [v for n,v in events if n=='provider_feedback_retry_succeeded']
        self.assertEqual(len(succeeds), 1)
        boundaries = [v for n,v in events if n=='generation_provider_request_finished']
        self.assertEqual([v['response_kind'] for v in boundaries], ['turn_completed'])

    def test_metadata_and_foreign_feedback_cannot_extend_silence_or_confirm_retry(self):
        for mode in ('metadata_only', 'foreign_feedback'):
            with self.subTest(mode=mode):
                result, events, _ = self.exercise(mode, retry_failed_request_id='old-failed')
                self.assertIsInstance(result, ProviderFeedbackError)
                self.assertNotIn('provider_feedback_retry_succeeded', [n for n,_ in events])

    def test_broken_stdin_transport_is_retriable_but_other_io_errors_are_not(self):
        for error in (BrokenPipeError('closed'), ConnectionResetError('reset'), OSError('disk fault')):
            with self.subTest(error=type(error).__name__):
                events = []
                session = CodexWorkspaceSession(tools=TOOLS, feedback_retry_mode=True)
                session.process = SimpleNamespace(stdin=Mock())
                session.process.stdin.write.side_effect = error
                with observe_events(lambda name, fields: events.append((name, fields))), \
                        redirect_stdout(io.StringIO()):
                    if isinstance(error, (BrokenPipeError, ConnectionResetError)):
                        with self.assertRaises(ProviderFeedbackError) as caught:
                            session._send({'id':123, 'result':{'success':True}})
                        self.assertEqual(caught.exception.failure['kind'], 'app_server_pipe_closed')
                        self.assertTrue(caught.exception.failed_request_id)
                        session._generation_stop('ProviderFeedbackError')
                        self.assertNotIn('generation_clock_untrusted', [n for n,_ in events])
                    else:
                        with self.assertRaises(OSError):
                            session._send({'id':123, 'result':{'success':True}})
                        self.assertNotIn('generation_provider_request_failed', [n for n,_ in events])

    def test_real_budget_bridge_credits_only_completed_replacement_response(self):
        from regen.infrastructure.effective_time_budget import EffectiveTimeBudget
        from regen.infrastructure.experiment_ledger import Ledger
        from regen.infrastructure.parallel_generation_budget import ParallelGenerationBudgetBridge
        now = [0.]
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
            budget = EffectiveTimeBudget(Ledger(Path(temp)/'budget', 'test'),
                monotonic=lambda: now[0])
            budget.register({'A':['contract']})
            bridge = ParallelGenerationBudgetBridge(budget)
            failed = CodexWorkspaceSession(tools=TOOLS, feedback_retry_mode=True)
            with bridge.worker('A'):
                failed._generation_start()
                # Completed earlier work must stay charged.
                now[0] = 10.
                failed._provider_request_begin('last_tool_result')
                now[0] = 25.
                with self.assertRaises(ProviderFeedbackError) as caught:
                    failed._raise_feedback_failure({'kind':'no_complete_response'}, source='test')
                failed._generation_stop('ProviderFeedbackError')
            self.assertEqual(budget.snapshot()['effective_seconds'], 25.)
            replacement = CodexWorkspaceSession(tools=TOOLS, feedback_retry_mode=True,
                retry_failed_request_id=caught.exception.failed_request_id)
            with bridge.worker('A'):
                now[0] = 30.  # offline gap is never counted
                replacement._generation_start()
                replacement._provider_request_begin('replacement')
                now[0] = 37.
                replacement._provider_request_finish('success', response_kind='tool_request')
                replacement._generation_stop('test')
            state = budget.snapshot()
            self.assertEqual(state['raw_effective_seconds'], 32.)
            self.assertEqual(state['effective_seconds'], 17.)
            self.assertEqual(state['excluded_failed_request_seconds'], 15.)
            self.assertEqual(len([r for r in budget.ledger.rows
                if r['kind']=='provider_feedback_retry_succeeded']), 1)
            budget.close()


if __name__ == '__main__':
    unittest.main()
