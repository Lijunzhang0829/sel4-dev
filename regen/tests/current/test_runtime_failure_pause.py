"""Runtime-integrity failures pause generation, not a retrying proof-feedback loop.

The callback tests run a real local stdio peer and the production budget bridge;
the scheduler tests use real threads/ledger but no paid model or Isabelle process.
"""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from regen.execution.check_tasks import CheckCancelled
from regen.infrastructure.codex_runtime import CodexRuntimeError
from regen.infrastructure.progress_events import emit_event
from regen.tests.current import test_codex_workspace_session as sessions
from regen.tests.current import test_parallel_effective_budget as clocks
from regen.tests.current import test_parallel_file_planner as controllers
from regen.tests.current import test_parallel_provider_cooldown as schedulers


class RuntimeCallbackTests(unittest.TestCase):
    setUp = clocks.ParallelEffectiveBudgetTests.setUp
    event = clocks.ParallelEffectiveBudgetTests.event

    def test_real_callback_stops_both_lanes_before_slow_abort_and_no_failed_tool_reply(self):
        self.event('theory_generation_started', 'B')
        calls, cleanup = [], []

        def handler(*args):
            calls.append(args)
            self.now[0] = 12
            raise CodexRuntimeError('trusted binary disappeared')

        def abort():
            # Cancellation may block on native tasks. Infrastructure cleanup
            # after the actual fault must not be charged as model generation.
            self.assertTrue(self.stage.event.is_set())
            self.assertEqual(self.stage.reason, 'runtime_integrity_failure')
            self.now[0] = 75
            cleanup.append(True)

        with redirect_stdout(io.StringIO()), self.bridge.worker('A'), \
                sessions.CodexWorkspaceSessionTest().session() as session:
            with self.assertRaisesRegex(CodexRuntimeError, 'disappeared'):
                session.run('fixture', handler, artifact_dir=self.root/'session',
                    model='fixture', timeout=3, cancel_event=self.stage.event, on_abort=abort)
            self.assertIsNotNone(session.process.poll())
        self.assertEqual(len(calls), 1)
        self.assertEqual(cleanup, [True])
        state = self.stage.snapshot()
        self.assertEqual(state['effective_seconds'], 12)
        self.assertEqual(state['worker_seconds'], 24)
        self.assertTrue(state['paused'])
        errors = [row for row in self.stage.ledger.rows
                  if row['kind'] == 'generation_runtime_integrity_failure']
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['data']['evidence']['error_type'], 'CodexRuntimeError')
        rows = [json.loads(line) for line in
                (self.root/'session'/'session-transcript.jsonl').read_text().splitlines()]
        local = [row['message'] for row in rows if row['direction'] == 'local']
        self.assertTrue(any(row.get('event') == 'session_failed'
                            and row.get('exception_type') == 'CodexRuntimeError' for row in local))
        self.assertFalse(any(row.get('event') == 'tool_finished' for row in local))
        self.assertFalse(any(row['kind'] == 'provider_http_retry_scheduled'
                             for row in self.stage.ledger.rows))

    def test_ordinary_candidate_value_error_remains_model_feedback_and_charged(self):
        def handler(*args):
            self.now[0] = 9
            raise ValueError('candidate references an unknown lemma')

        with redirect_stdout(io.StringIO()), self.bridge.worker('A'), \
                sessions.CodexWorkspaceSessionTest().session() as session:
            result = session.run('fixture', handler, artifact_dir=self.root/'ordinary',
                model='fixture', timeout=3, cancel_event=self.stage.event)
        self.assertEqual(result['status'], 'completed')
        self.assertFalse(self.stage.event.is_set())
        self.assertEqual(self.stage.snapshot()['effective_seconds'], 9)
        rows = [json.loads(line) for line in
                (self.root/'ordinary'/'session-transcript.jsonl').read_text().splitlines()]
        replies = [row['message']['response'] for row in rows
                   if row['direction'] == 'local' and row['message'].get('event') == 'tool_finished']
        self.assertEqual(len(replies), 1)
        self.assertFalse(replies[0]['success'])
        self.assertEqual(json.loads(replies[0]['contentItems'][0]['text'])['error_type'], 'ValueError')


class RuntimeExecutorTests(unittest.TestCase):
    setUp = controllers.ParallelControllerTests.setUp

    def test_integrity_error_survives_executor_receipt_as_fatal_type(self):
        with patch('regen.planning.proof_workspace_agent.WorkspaceController.proof_action',
                   side_effect=CodexRuntimeError('trusted binary hash changed')):
            with self.assertRaisesRegex(CodexRuntimeError, 'hash changed'):
                self.controller.handle('proof_action', {'theory':'Target', 'proposal':{
                    'action':'submit_batch', 'proofs':{'target_contract':'by simp'}}}, self.metadata)
        row = self.executor.get(next(iter(self.controller.task_ids)))
        self.assertEqual(row['error_type'], 'CodexRuntimeError')
        self.assertEqual(row['status'], 'error')
        self.assertEqual(self.controller._state('Target')[1]['proofs'], {})


class RuntimeSchedulerTests(unittest.TestCase):
    setUp = schedulers.ProviderCooldownTests.setUp
    seal = schedulers.ProviderCooldownTests.seal
    exercise = schedulers.ProviderCooldownTests.exercise

    def test_startup_fault_outside_session_stops_scheduler_without_http_retry(self):
        def failed(theory):
            emit_event('theory_generation_started')
            self.now = 13
            raise CodexRuntimeError('trusted launch hash changed')

        with self.assertRaises(CodexRuntimeError) as caught:
            self.exercise(failed, ('A', 'B'), workers=1, http_retries=1)
        self.assertEqual(self.attempts, {'A':1})
        self.assertEqual(caught.exception.regen_pause_result['reason'], 'runtime_integrity_failure')
        self.assertEqual(self.budget.snapshot()['effective_seconds'], 13)
        row = json.loads((self.paths['A'][0]/'failure.json').read_text())
        self.assertEqual(row['error_type'], 'CodexRuntimeError')
        self.assertFalse(row['retry_scheduled'])
        self.assertFalse(any(row['kind'] == 'provider_http_retry_scheduled'
                             for row in self.budget.ledger.rows))

    def test_two_workers_observe_immediate_pause_no_new_theory_and_no_cleanup_charge(self):
        peer_started, fault_sent, peer_cancelled = [threading.Event() for _ in range(3)]

        def session(theory):
            emit_event('theory_generation_started')
            if theory == 'A':
                self.assertTrue(peer_started.wait(3))
                self.now = 12
                emit_event('generation_runtime_integrity_failure', generation_id='fixture-A',
                    error_type='CodexRuntimeError', reason='binary missing')
                self.assertTrue(self.budget.event.is_set())
                fault_sent.set()
                self.now = 75
                raise CodexRuntimeError('binary missing')
            self.assertEqual(theory, 'B', 'coordinator launched a replacement after fatal fault')
            peer_started.set()
            self.assertTrue(fault_sent.wait(3))
            self.assertTrue(self.budget.event.is_set())
            peer_cancelled.set()
            raise CheckCancelled('shared runtime unavailable')

        with self.assertRaises((CodexRuntimeError, CheckCancelled)) as caught:
            self.exercise(session, ('A', 'B', 'C'), workers=2, http_retries=1)
        self.assertTrue(peer_cancelled.is_set())
        self.assertEqual(self.attempts, {'A':1, 'B':1})
        self.assertEqual(caught.exception.regen_pause_result['reason'], 'runtime_integrity_failure')
        state = self.budget.snapshot()
        self.assertEqual(state['effective_seconds'], 12)
        self.assertEqual(state['worker_seconds'], 24)
        self.assertFalse(self.waits)
        self.assertFalse(any(row['kind'] == 'provider_http_retry_scheduled'
                             for row in self.budget.ledger.rows))


class RuntimeFocusTests(unittest.TestCase):
    _empty_turn_scenario = controllers.SchedulerTests._empty_turn_scenario

    def test_target_green_does_not_swallow_global_runtime_failure(self):
        def accept_then_fail(turn):
            self.empty_controller.state['proofs']['goal'] = 'by simp'
            raise CodexRuntimeError('global trusted runtime missing')

        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(io.StringIO()):
            with self.assertRaises(CodexRuntimeError):
                self._empty_turn_scenario(temporary, focus=True, on_turn=accept_then_fail)
        self.assertTrue(self.empty_controller.closed)
        self.assertEqual(self.empty_controller.state['proofs'], {'goal':'by simp'})


if __name__ == '__main__':
    unittest.main()
