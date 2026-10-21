"""Actual controller tool schemas over dummy stdio, with strict synthetic audit."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession
from regen.infrastructure.llm_transcript import inspect_call_artifact
from regen.infrastructure.workspace_transcript import audit_workspace_session, record_workspace_action
from regen.planning.proof_workspace_agent import WorkspaceController, workspace_tools
from regen.tests.current.test_proof_commits import fixture

SERVER = r'''
import json,sys
calls=[('list_workspace',{}),('read_theory',{'theory':'Demo'}),
       ('proof_action',{'theory':'Demo','proposal':{'action':'submit_batch','proofs':{'helper':'by simp'}}}),
       ('read_theory',{'theory':'Demo'}),('proof_action',{'theory':'Demo','proposal':{'action':'finish'}})]
i=0
def send(x):print(json.dumps(x),flush=True)
def tool():
 name,args=calls[i]
 send({'id':100+i,'method':'item/tool/call','params':{'threadId':'workspace-thread','turnId':'workspace-turn',
      'callId':'call-'+str(i),'namespace':None,'tool':name,'arguments':args}})
for line in sys.stdin:
 m=json.loads(line);method=m.get('method')
 if method=='initialize':send({'id':m['id'],'result':{}})
 elif method=='thread/start':
  names={t['name'] for t in m['params']['dynamicTools']}
  assert names=={'list_workspace','read_theory','list_generated','read_generated','list_attempts','read_attempt','prepare_reuse','proof_action'}
  send({'id':m['id'],'result':{'thread':{'id':'workspace-thread'}}})
 elif method=='turn/start':
  send({'id':m['id'],'result':{'turn':{'id':'workspace-turn'}}});tool()
 elif 'result' in m:
  assert m['result']['success']==(calls[i][0]!='proof_action')
  i+=1
  if i<len(calls):tool()
  else:send({'method':'turn/completed','params':{'threadId':'workspace-thread','turn':{'id':'workspace-turn','status':'completed'}}})
'''


class WorkspaceControllerTransportTest(unittest.TestCase):
    def test_real_schemas_continuous_reads_and_synthetic_proof_finish_rejection(self):
        package, state = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            controller=object.__new__(WorkspaceController)
            controller.root=root;controller.selected=['Demo'];controller.model='test'
            controller.options={'model':'test','call_timeout':2,'check_timeout':1800,'candidate_timeout':120}
            controller.runtime_cache={};controller.verify_implementation=Mock()
            controller._state=Mock(side_effect=lambda theory:(package,deepcopy(state)))
            controller.list_workspace=Mock(return_value={'all_selected_sealed':False,'theories':[
                {'theory':'Demo','status':'planned','unsealed_providers':[]}]})
            calls=[];native=Mock()
            def batch(*args,decision_source,**kwargs):
                prefix=root/'candidate-call'
                response=decision_source('current target context',{},prefix,theory='Demo',model='test')
                self.assertEqual(json.loads(response),{'action':'submit_batch','proofs':{'helper':'by simp'}})
                # Same required audit gate used by the real batch controller.
                inspect_call_artifact(prefix,require_real=True)
                native()
            def callback(name,arguments,metadata):
                calls.append((name,arguments,metadata['thread_id'],metadata['turn_id']))
                return controller.handle(name,arguments,metadata)
            with patch('regen.planning.proof_workspace_agent._run_batch_file',side_effect=batch) as dispatch, \
                 patch('regen.infrastructure.progress_events.emit_event'), \
                 CodexWorkspaceSession(tools=workspace_tools(),allow_external_call=True,
                    command=[sys.executable,'-u','-c',SERVER]) as session:
                result=session.run('shared workspace',callback,artifact_dir=root/'session',model='test',timeout=2)
            self.assertEqual(result['status'],'completed')
            self.assertEqual([c[0] for c in calls],['list_workspace','read_theory','proof_action','read_theory','proof_action'])
            self.assertEqual({c[2:] for c in calls},{('workspace-thread','workspace-turn')})
            self.assertEqual(dispatch.call_count,1)  # synthetic finish rejected before batch dispatch
            native.assert_not_called()
            self.assertEqual(state['proofs'],{})
            self.assertFalse(inspect_call_artifact(root/'candidate-call',require_real=False)['sealable_generation'])
            with self.assertRaises(ValueError):inspect_call_artifact(root/'candidate-call',require_real=True)
            audit=audit_workspace_session(root/'session/session-transcript.jsonl',require_real=False)
            self.assertEqual(audit['tool_calls'],5)
            self.assertTrue(audit['terminal'])
            self.assertFalse(audit['sealable_generation'])


    def test_queued_calls_before_turn_response_bind_distinct_receipts(self):
        server = r'''import json,sys
responses=0
def send(v): print(json.dumps(v),flush=True)
for line in sys.stdin:
 m=json.loads(line)
 if m.get('method')=='initialize':send({'id':m['id'],'result':{}})
 elif m.get('method')=='thread/start':send({'id':m['id'],'result':{'thread':{'id':'thread-q'}}})
 elif m.get('method')=='turn/start':
  for n in (1,2):
   args={'theory':'Demo','proposal':{'action':'query','request':{'premise_query':str(n)}}}
   send({'method':'item/started','params':{'threadId':'thread-q','turnId':'turn-q',
        'item':{'type':'dynamicToolCall','id':'call-'+str(n),'tool':'proof_action','namespace':None,
                'arguments':args,'status':'inProgress'}}})
   send({'id':100+n,'method':'item/tool/call','params':{'threadId':'thread-q','turnId':'turn-q',
        'callId':'call-'+str(n),'tool':'proof_action','namespace':None,'arguments':args}})
  send({'id':m['id'],'result':{'turn':{'id':'turn-q'}}})
 elif 'result' in m:
  assert m['result']['success'],m
  responses+=1
  if responses==2:send({'method':'turn/completed','params':{'threadId':'thread-q','turn':{'id':'turn-q','status':'completed'}}})
'''
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); bindings=[]
            def callback(name, arguments, metadata):
                prefix=root/metadata['call_id']
                record_workspace_action(prefix,'harness context',arguments['proposal'],metadata,
                                        theory=arguments['theory'],model='test')
                inspect_call_artifact(prefix,require_real=False)
                raw=Path(metadata['transcript_path']).read_bytes()[:metadata['tool_request_offset']]
                self.assertEqual(json.loads(raw.splitlines()[-1])['message']['parsed'],metadata['raw_request'])
                bindings.append(metadata)
                return {'status':'observed'}
            with CodexWorkspaceSession(tools=workspace_tools(),allow_external_call=True,
                    command=[sys.executable,'-u','-c',server]) as session:
                session.run('queued',callback,artifact_dir=root/'session',model='test',timeout=2)
            self.assertEqual([m['call_id'] for m in bindings],['call-1','call-2'])
            self.assertLess(bindings[0]['tool_request_offset'],bindings[1]['tool_request_offset'])
            self.assertLess(bindings[1]['tool_request_offset'],bindings[0]['transcript_offset'])
            self.assertEqual(audit_workspace_session(root/'session/session-transcript.jsonl',require_real=False)['tool_calls'],2)


if __name__=='__main__':unittest.main()
