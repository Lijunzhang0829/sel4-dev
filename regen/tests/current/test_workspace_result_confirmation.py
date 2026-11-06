"""Real stdio protocol fixtures; no model, Docker, or Isabelle calls."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from regen.execution.check_tasks import NativePreparationError
from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession, SessionProtocolError
from regen.infrastructure.progress_events import observe_events
from regen.infrastructure.workspace_transcript import _audit, audit_workspace_session
from regen.tests.current.test_codex_workspace_session import TOOLS


SERVER = r'''
import json,sys
mode=sys.argv[1]
def send(v): print(json.dumps(v),flush=True)
def tool(number):
 send({'id':100+number,'method':'item/tool/call','params':{'threadId':'thread','turnId':'turn',
   'callId':'call-'+str(number),'tool':'proof_action','namespace':None,'arguments':{'action':'inspect'}}})
def completion(number,response):
 send({'method':'item/completed','params':{'threadId':'thread','turnId':'turn','item':{
   'type':'dynamicToolCall','id':'call-'+str(number),'tool':'proof_action','namespace':None,
   'arguments':{'action':'inspect'},'status':'completed',**response}}})
def terminal(status='completed',owner='thread'):
 send({'method':'turn/completed','params':{'threadId':owner,'turn':{'id':'turn','status':status}}})
for line in sys.stdin:
 m=json.loads(line);method=m.get('method')
 if method=='initialize':send({'id':m['id'],'result':{}})
 elif method=='thread/start':send({'id':m['id'],'result':{'thread':{'id':'thread'}}})
 elif method=='turn/start':
  send({'id':m['id'],'result':{'turn':{'id':'turn'}}})
  if mode.startswith('interrupt') or mode=='orphan':
   send({'method':'item/started','params':{'threadId':'thread','turnId':'turn',
     'item':{'type':'agentMessage','id':'orphan','phase':'commentary'}}})
  if mode=='early':completion(1,{'success':True,'contentItems':[{'type':'inputText','text':'{"accepted": true}'}]})
  tool(1)
  if mode=='queued':tool(2)
 elif method=='turn/interrupt':
  if mode=='interrupt_queued':tool(2)
  if mode=='interrupt_terminal_first':terminal('interrupted')
  if mode!='interrupt_no_ack':send({'id':m['id'],'result':{}})
  if mode=='interrupt_wrong_owner':terminal('interrupted','foreign')
  elif mode not in ('interrupt_no_terminal','interrupt_terminal_first'):terminal('interrupted')
 elif 'result' in m:
  number=m['id']-100
  if mode not in ('missing','early','orphan'):
   response=m['result']
   if mode=='mismatch':response={**response,'contentItems':[{'type':'inputText','text':'different'}]}
   completion(number,response)
  if mode=='orphan':
   send({'method':'item/started','params':{'threadId':'thread','turnId':'turn',
     'item':{'type':'agentMessage','id':'later','phase':'commentary'}}})
   send({'method':'item/completed','params':{'threadId':'thread','turnId':'turn',
     'item':{'type':'agentMessage','id':'later','phase':'commentary','text':'continued'}}})
  if mode!='queued' or number==2:terminal()
'''


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


class WorkspaceResultConfirmationTests(unittest.TestCase):
    def session(self, mode):
        return CodexWorkspaceSession(tools=TOOLS, allow_external_call=True,
            command=[sys.executable, '-u', '-c', SERVER, mode])

    def test_server_receipt_matches_sent_result_without_reexecuting_callback(self):
        for mode, count in [('normal', 1), ('early', 1), ('queued', 2), ('missing', 1)]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp, self.session(mode) as session:
                calls = []
                result = session.run('prove', lambda *a: calls.append(a) or {'accepted': True},
                    artifact_dir=tmp, model='test', timeout=1)
                self.assertEqual(len(calls), count)
                expected = 0 if mode == 'missing' else count
                self.assertEqual(result['tool_result_confirmation']['server_confirmed'], expected)
                path = Path(tmp)/'session-transcript.jsonl'
                audit = audit_workspace_session(path, require_real=False)
                self.assertEqual(audit['tool_result_confirmation']['server_confirmed'], expected)
                self.assertEqual(audit['tool_calls'], count)
                local = [x['message']['event'] for x in rows(path) if x['direction'] == 'local']
                self.assertEqual(local.count('tool_result_persisted'), count)
                self.assertEqual(local.count('tool_result_sent'), count)

    def test_mismatched_ack_aborts_but_does_not_replay_executed_tool(self):
        with tempfile.TemporaryDirectory() as tmp, self.session('mismatch') as session:
            calls = []
            with self.assertRaisesRegex(SessionProtocolError, 'differs from sent result'):
                session.run('prove', lambda *a: calls.append(1) or {'accepted': True},
                    artifact_dir=tmp, model='test', timeout=1)
            self.assertEqual(calls, [1])

    def test_late_receipt_for_previous_turn_does_not_touch_current_model_items(self):
        with self.session('normal') as session:
            session.thread_id, session.turn_id = 'thread', 'next-turn'
            key = ('thread', 'turn', 'call-1')
            response = {'success': True, 'contentItems': [{'type': 'inputText', 'text': 'accepted'}]}
            session._tool_results[key] = {'state': 'sent', 'tool': 'proof_action',
                'rpc_id': 101, 'response': response, 'response_sha256': 'fixture'}
            current = ('thread', 'next-turn', 'agentMessage', 'current')
            session._model_work_items.add(current)
            completion = {'method': 'item/completed', 'params': {'threadId': 'thread', 'turnId': 'turn',
                'item': {'type': 'dynamicToolCall', 'id': 'call-1', 'tool': 'proof_action',
                         'namespace': None, 'status': 'completed', **deepcopy(response)}}}
            session._notification(completion)
            self.assertEqual(session._tool_results[key]['state'], 'server_confirmed')
            self.assertEqual(session._model_work_items, {current})
            # A duplicate is harmless only when its actual result still matches.
            session._notification(completion)
            completion['params']['item']['contentItems'][0]['text'] = 'changed'
            with self.assertRaisesRegex(SessionProtocolError, 'differs from sent result'):
                session._notification(completion)

    def test_ack_tracking_does_not_apply_provider_deadline_inside_long_native_callback(self):
        with tempfile.TemporaryDirectory() as tmp, self.session('normal') as session:
            def work(*args):
                time.sleep(.25)
                return {'accepted': True}
            result = session.run('prove', work, artifact_dir=tmp, model='test', timeout=.15)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['tool_result_confirmation']['server_confirmed'], 1)

    def test_suspected_orphan_is_diagnostic_and_real_terminal_closes_turn(self):
        events = []
        with tempfile.TemporaryDirectory() as tmp, observe_events(
                lambda name, fields: events.append(name)), self.session('orphan') as session:
            result = session.run('prove', lambda *a: {'accepted': True},
                artifact_dir=tmp, model='test', timeout=1)
            self.assertEqual(result['status'], 'completed')
            data = rows(Path(tmp)/'session-transcript.jsonl')
            diagnostics = [x['message'] for x in data if x['direction'] == 'local'
                and x['message'].get('event') == 'model_message_completion_pending']
            self.assertTrue(diagnostics)
            self.assertTrue(all(x['diagnostic_only'] and x['clock_effect'] == 'none' for x in diagnostics))
            self.assertFalse(session._model_work_items)
            self.assertNotIn('generation_clock_untrusted', events)
            self.assertFalse(any(x['direction'] == 'out' and x['message'].get('method') == 'turn/interrupt'
                                 for x in data))

    def test_trusted_failure_requires_rpc_and_real_interrupted_terminal_in_either_order(self):
        for mode in ('interrupt', 'interrupt_terminal_first', 'interrupt_queued'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp, observe_events(
                    lambda name, fields: events.append((name, fields))), self.session(mode) as session:
                events, calls = [], []
                def fail(*args):
                    calls.append(args)
                    raise NativePreparationError('original native failure')
                with self.assertRaisesRegex(NativePreparationError, 'original native failure'):
                    session.run('prove', fail, artifact_dir=tmp, model='test', timeout=1)
                self.assertEqual(len(calls), 1, 'queued tools must never execute during teardown')
                self.assertNotIn('generation_clock_untrusted', [name for name, _ in events])
                interruptions = [value for name, value in events if name == 'generation_items_interrupted']
                self.assertEqual(len(interruptions), 1)
                self.assertEqual(interruptions[0]['item_ids'], ['orphan'])
                data = rows(Path(tmp)/'session-transcript.jsonl')
                self.assertTrue(any(x['message'].get('event') == 'trusted_failure_interrupt_confirmed' for x in data))
                self.assertFalse(any(x['direction'] == 'out' and 'result' in x['message'] for x in data))
                # The exact prefix before session_failed remains independently auditable,
                # but an interrupted turn cannot authorize completed-session sealing.
                prefix = data[:next(i for i, x in enumerate(data) if x['message'].get('event') == 'session_failed')]
                raw = ''.join(json.dumps(x)+'\n' for x in prefix).encode()
                report = _audit(raw, require_terminal=False, require_real=False)
                self.assertFalse(report['terminal'])
                self.assertEqual(report['interrupted_turns'], [('thread', 'turn')])
                with self.assertRaises(ValueError):
                    _audit(raw, require_terminal=True, require_real=False)

    def test_missing_or_foreign_interrupt_terminal_preserves_original_failure_and_clock_uncertainty(self):
        for mode in ('interrupt_no_terminal', 'interrupt_no_ack', 'interrupt_wrong_owner'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp, self.session(mode) as session:
                events = []
                with observe_events(lambda name, fields: events.append(name)), patch(
                        'regen.infrastructure.codex_workspace_session.TRUSTED_FAILURE_INTERRUPT_SECONDS', .15):
                    with self.assertRaisesRegex(NativePreparationError, 'original native failure') as raised:
                        session.run('prove', lambda *a: (_ for _ in ()).throw(
                            NativePreparationError('original native failure')),
                            artifact_dir=tmp, model='test', timeout=1)
                self.assertTrue(raised.exception.__notes__)
                self.assertIn('generation_clock_untrusted', events)
                self.assertNotIn('generation_items_interrupted', events)
                self.assertIsNotNone(session.process.poll())

    def test_auditor_checks_raw_confirmation_without_trusting_local_ack_claim(self):
        with tempfile.TemporaryDirectory() as tmp, self.session('normal') as session:
            session.run('prove', lambda *a: {'accepted': True}, artifact_dir=tmp, model='test', timeout=1)
            data = rows(Path(tmp)/'session-transcript.jsonl')
            changed = deepcopy(data)
            for x in changed:
                if x['direction'] != 'in':
                    continue
                m = x['message']['parsed']
                if m.get('method') == 'item/completed':
                    m['params']['item']['contentItems'][0]['text'] = 'forged result'
                    x['message']['raw_json'] = json.dumps(m)
            with self.assertRaisesRegex(ValueError, 'differs from sent result'):
                _audit(''.join(json.dumps(x)+'\n' for x in changed).encode(),
                    require_terminal=True, require_real=False)


if __name__ == '__main__':
    unittest.main()
