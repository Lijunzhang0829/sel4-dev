"""No provider calls: a local dummy app-server exercises the actual stdio transport."""
import json
import os
import signal
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession, SessionProtocolError, SessionCancelled

TOOLS = [{'name':'proof_action','description':'trusted proof harness',
          'inputSchema':{'type':'object','properties':{'action':{'type':'string'}},
                         'required':['action'],'additionalProperties':False}}]
SERVER = r'''
import json,sys,time
mode=sys.argv[1]
def send(v): print(json.dumps(v),flush=True)
turn=0
for line in sys.stdin:
 m=json.loads(line); method=m.get('method')
 if method=='initialize': send({'id':m['id'],'result':{'userAgent':'dummy'}})
 elif method=='thread/start':
  assert m['params']['sandbox']=='read-only'
  assert m['params']['dynamicTools'][0]['name']=='proof_action'
  send({'id':m['id'],'result':{'thread':{'id':'thread-one'}}})
 elif method=='turn/start':
  turn+=1; tid='turn-'+str(turn)
  send({'id':m['id'],'result':{'turn':{'id':tid}}})
  if mode=='timeout': time.sleep(30)
  elif mode=='builtin':
   send({'method':'item/started','params':{'item':{'type':'commandExecution'}}})
  elif mode=='approval':
   send({'id':900,'method':'item/commandExecution/requestApproval','params':{}})
  else:
   send({'id':100+turn,'method':'item/tool/call','params':{'threadId':'thread-one','turnId':tid,
      'callId':'call-'+str(turn),'namespace':None,'tool':'exec' if mode=='unknown' else 'proof_action','arguments':{'bad':'argument'} if mode=='schema' else {'action':'inspect'}}})
 elif 'result' in m:
  assert m['result']['contentItems'][0]['type']=='inputText'
  if mode=='duplicate':
   send({'id':200,'method':'item/tool/call','params':{'threadId':'thread-one','turnId':tid,
      'callId':'call-'+str(turn),'namespace':None,'tool':'proof_action','arguments':{'action':'inspect'}}})
  else:
   send({'method':'thread/tokenUsage/updated','params':{'threadId':'thread-one','tokenUsage':{'total':{'inputTokens':12}}}})
   send({'method':'turn/completed','params':{'threadId':'thread-one','turn':{'id':tid,'status':'completed'}}})
'''


class CodexWorkspaceSessionTest(unittest.TestCase):
    def session(self, mode='normal', **kwargs):
        return CodexWorkspaceSession(tools=TOOLS, command=[sys.executable,'-u','-c',SERVER,mode],
                                     allow_external_call=True, **kwargs)

    def test_one_process_one_thread_multiple_turns_and_durable_callback_binding(self):
        calls=[]
        with tempfile.TemporaryDirectory() as tmp, self.session() as session:
            def handler(name, args, meta):
                raw=Path(meta['transcript_path']).read_bytes()[:meta['transcript_offset']]
                self.assertIn(b'item/tool/call',raw)
                self.assertEqual(meta['raw_request']['params']['callId'],meta['call_id'])
                self.assertEqual(name,'proof_action'); calls.append(meta)
                return {'graph':'same working graph'}
            first=session.run('first',handler,artifact_dir=tmp,model='test',timeout=2)
            pid=session.process.pid
            second=session.run('continue same theory',handler,artifact_dir=tmp,model='test',timeout=2)
            self.assertEqual(session.process.pid,pid)
            self.assertEqual(first['thread_id'],second['thread_id'])
            self.assertNotEqual(first['turn_id'],second['turn_id'])
            self.assertEqual(len(calls),2)
            self.assertEqual(second['usage']['total']['inputTokens'],12)
            self.assertEqual(first['assurance'],'audited-tool-allowlist')
            self.assertFalse(first['exclusive_os_isolation'])

    def test_coordinator_cancel_interrupts_remote_wait_and_closes_process(self):
        import threading
        with tempfile.TemporaryDirectory() as tmp, self.session('timeout') as session:
            cancel=threading.Event()
            timer=threading.Timer(.3,cancel.set);timer.start()
            self.addCleanup(timer.cancel)
            start=time.monotonic()
            with self.assertRaisesRegex(SessionCancelled,'coordinator cancelled'):
                session.run('x',lambda *a: {},artifact_dir=tmp,model='test',timeout=30,cancel_event=cancel)
            self.assertLess(time.monotonic()-start,5)
            self.assertIsNotNone(session.process.poll())
            self.assertIn('session_failed',(Path(tmp)/'session-transcript.jsonl').read_text())

    def test_default_refuses_provider_launch(self):
        with tempfile.TemporaryDirectory() as tmp, CodexWorkspaceSession(tools=TOOLS) as session:
            with patch('regen.infrastructure.codex_workspace_session.subprocess.Popen') as popen:
                with self.assertRaises(PermissionError):
                    session.run('x',lambda *a: {},artifact_dir=tmp,model='test',timeout=1)
                popen.assert_not_called()

    def test_duplicate_call_is_not_reexecuted_and_session_is_nonsealable(self):
        calls=[]
        with tempfile.TemporaryDirectory() as tmp, self.session('duplicate') as session:
            with self.assertRaisesRegex(SessionProtocolError,'duplicate call'):
                session.run('x',lambda *a: calls.append(1),artifact_dir=tmp,model='test',timeout=2)
            self.assertEqual(calls,[1])
            self.assertIsNotNone(session.process.poll())
            self.assertIn('"nonsealable": true',(Path(tmp)/'session-transcript.jsonl').read_text())
            with self.assertRaises(RuntimeError):
                session.run('retry',lambda *a: {},artifact_dir=tmp,model='test',timeout=2)

    def test_builtin_and_approval_requests_abort_before_harness_callback(self):
        for mode in ('builtin','approval'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp, self.session(mode) as session:
                calls=[]
                with self.assertRaises(SessionProtocolError):
                    session.run('x',lambda *a: calls.append(1),artifact_dir=tmp,model='test',timeout=2)
                self.assertEqual(calls,[])
                self.assertIsNotNone(session.process.poll())
                if mode=='approval':
                    self.assertIn('Only whitelisted item/tool/call is allowed',
                                  (Path(tmp)/'session-transcript.jsonl').read_text())

    def test_callback_time_does_not_consume_model_inactivity_budget(self):
        with tempfile.TemporaryDirectory() as tmp, self.session() as session:
            def callback(*args): time.sleep(.3); return {'native':'complete'}
            result=session.run('x',callback,artifact_dir=tmp,model='test',timeout=.2)
            self.assertEqual(result['status'],'completed')

    def test_model_inactivity_timeout_cleans_up_process_and_logs(self):
        with tempfile.TemporaryDirectory() as tmp, self.session('timeout') as session:
            with self.assertRaises(TimeoutError):
                session.run('x',lambda *a: {},artifact_dir=tmp,model='test',timeout=.2)
            self.assertIsNotNone(session.process.poll())
            self.assertIn('TimeoutError',(Path(tmp)/'session-transcript.jsonl').read_text())

    def test_nonallowlisted_tool_or_schema_violation_never_reaches_handler(self):
        for mode in ('unknown','schema'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp, self.session(mode) as session:
                calls=[]
                with self.assertRaises(Exception):
                    session.run('x',lambda *a:calls.append(1),artifact_dir=tmp,model='test',timeout=2)
                self.assertEqual(calls,[])
                self.assertIsNotNone(session.process.poll())
                self.assertIn('"nonsealable": true',(Path(tmp)/'session-transcript.jsonl').read_text())

    def test_signal_during_callback_cancels_instead_of_becoming_tool_failure(self):
        with tempfile.TemporaryDirectory() as tmp, self.session() as session:
            def handler(*args): os.kill(os.getpid(), signal.SIGTERM)
            with self.assertRaises(SessionCancelled):
                session.run('x', handler, artifact_dir=tmp, model='test', timeout=2)
            self.assertIsNotNone(session.process.poll())
            log=(Path(tmp)/'session-transcript.jsonl').read_text()
            self.assertIn('SessionCancelled',log)
            self.assertNotIn('tool_finished',log)

    def test_external_schema_reference_and_configured_home_fail_closed(self):
        with self.assertRaises(ValueError):
            CodexWorkspaceSession(tools=[{**TOOLS[0],'inputSchema':{'$ref':'https://invalid/schema'}}])
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)/'home';home.mkdir();(home/'config.toml').write_text('inherited = true')
            with self.session(codex_home=home) as session:
                with self.assertRaisesRegex(ValueError,'clean codex_home'):
                    session.run('x',lambda *a: {},artifact_dir=Path(tmp)/'log',model='test',timeout=1)
                self.assertIsNone(session.process)


if __name__=='__main__': unittest.main()
