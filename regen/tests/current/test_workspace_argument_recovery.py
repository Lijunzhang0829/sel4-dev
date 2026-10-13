"""Real stdio transport with a local scripted provider; no model or Isabelle."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

from regen.infrastructure.codex_workspace_session import (
    CodexWorkspaceSession, SessionProtocolError, SessionStalled,
)


TOOLS = [
    {'name':'list_generated', 'description':'Search generated examples',
     'inputSchema':{'type':'object'}},
    {'name':'read_generated', 'description':'Read generated examples',
     'inputSchema':{'type':'object', 'properties':{
         'source_refs':{'type':'array', 'minItems':1, 'maxItems':8,
                        'items':{'type':'object'}}},
         'required':['source_refs'], 'additionalProperties':False}},
]
SERVER = r'''
import json, sys, time
mode=sys.argv[1]
def send(value): print(json.dumps(value), flush=True)
def call(number, name, arguments, notify=True):
 params={'threadId':'thread', 'turnId':'turn', 'callId':'call-'+str(number),
         'namespace':None, 'tool':name, 'arguments':arguments}
 if notify:
  send({'method':'item/started','params':{'threadId':'thread','turnId':'turn',
       'item':{'type':'dynamicToolCall','id':'item-'+str(number),
               'namespace':None,'tool':name,'arguments':arguments}}})
 send({'id':number, 'method':'item/tool/call','params':params})
def finish():
 send({'method':'turn/completed','params':{'threadId':'thread',
       'turn':{'id':'turn','status':'completed'}}})
for line in sys.stdin:
 message=json.loads(line)
 method=message.get('method')
 if method=='initialize': send({'id':message['id'],'result':{}})
 elif method=='thread/start': send({'id':message['id'],'result':{'thread':{'id':'thread'}}})
 elif method=='turn/start':
  if mode=='before_start_response':
   send({'method':'turn/started','params':{'threadId':'thread','turn':{'id':'turn'}}})
   call(102,'read_generated',{'source_refs':[]})
   send({'id':message['id'],'result':{'turn':{'id':'turn'}}})
  elif mode in ('before_response_without_started', 'before_response_wrong_turn'):
   call(102,'read_generated',{'source_refs':[]})
   owner='foreign-turn' if mode=='before_response_wrong_turn' else 'turn'
   send({'id':message['id'],'result':{'turn':{'id':owner}}})
  elif mode=='invalid_loop':
   send({'id':message['id'],'result':{'turn':{'id':'turn'}}})
   call(102,'read_generated',{'source_refs':[]})
  else:
   send({'id':message['id'],'result':{'turn':{'id':'turn'}}})
   call(101,'list_generated',{'theory':'Scheduler_Queues','query':'runnable_relation','limit':5})
   if mode in ('queued','terminal'):
    time.sleep(.1)
    call(102,'read_generated',{'source_refs':[]})
    if mode=='terminal': finish()
 elif 'result' in message:
  if mode=='invalid_loop':
   assert message['result']['success'] is False
   error=json.loads(message['result']['contentItems'][0]['text'])
   assert error['code']=='invalid_tool_arguments'
   time.sleep(.04)
   call(message['id']+1,'read_generated',{'source_refs':[]})
  elif message['id']==101:
   assert message['result']['success'] is True
   assert json.loads(message['result']['contentItems'][0]['text'])['records']==[]
   if mode not in ('queued','terminal'):
    arguments=None if mode=='null' else {'source_refs':[]}
    call(102,'read_generated',arguments,notify=mode!='direct')
  elif message['id']==102:
   assert message['result']['success'] is False
   error=json.loads(message['result']['contentItems'][0]['text'])
   assert error['code']=='invalid_tool_arguments'
   assert error['error_type']=='ToolArgumentValidationError'
   if mode!='terminal': call(103,'read_generated',{'source_refs':[{'id':'legal-example'}]})
  elif message['id']==103:
   assert message['result']['success'] is True
   finish()
'''


class WorkspaceArgumentRecoveryTest(unittest.TestCase):
    def test_invalid_argument_loop_does_not_reset_progress_watchdog(self):
        with tempfile.TemporaryDirectory() as tmp, CodexWorkspaceSession(
                tools=TOOLS, allow_external_call=True, progress_timeout=.4,
                command=[sys.executable, '-u', '-c', SERVER, 'invalid_loop']) as session:
            calls=[]
            started=time.monotonic()
            with self.assertRaisesRegex(SessionStalled, 'no new tool information'):
                session.run('Read examples', lambda *args:calls.append(args),
                    artifact_dir=tmp, model='synthetic', timeout=3)
            self.assertLess(time.monotonic()-started, 8)
            self.assertEqual(calls, [])
            self.assertEqual(session._progress.generation, 0)
            self.assertEqual(session._progress.seen, set())
            self.assertGreaterEqual(time.monotonic()-session._progress.last_progress, .4)
            self.assertIsNotNone(session.process.poll())
            self.assertIsNone(session._callback_reader)
            events=[json.loads(line) for line in (Path(tmp)/'session-transcript.jsonl').read_text().splitlines()]
            rejections=[event for event in events if event['direction']=='local'
                and event['message'].get('event')=='tool_arguments_rejected']
            self.assertGreaterEqual(len(rejections), 2)

    def test_empty_search_then_invalid_read_is_correctable_over_real_stdio(self):
        for mode in ('normal', 'direct', 'queued', 'before_start_response',
                     'before_response_without_started', 'null', 'terminal'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                calls, aborts = [], []
                with CodexWorkspaceSession(tools=TOOLS, allow_external_call=True,
                        command=[sys.executable, '-u', '-c', SERVER, mode]) as session:
                    def handler(name, arguments, metadata):
                        calls.append((name, arguments))
                        if name=='list_generated':
                            if mode in ('queued', 'terminal'):
                                time.sleep(.3)  # Reader must accept the bad RPC during this callback.
                            return {'records':[], 'total':0}
                        self.assertTrue(arguments['source_refs'])
                        return {'body':'legal generated example'}
                    result=session.run('Inspect examples', handler, artifact_dir=tmp,
                        model='synthetic', timeout=5, on_abort=lambda:aborts.append(True))
                    self.assertEqual(result['status'], 'completed')
                    self.assertEqual(aborts, [])
                    self.assertIsNone(session._callback_reader)
                    self.assertFalse(session._broken)
                    self.assertEqual(session._calls[('thread','turn','call-102')], 'finished')
                    if mode=='terminal':
                        self.assertEqual([name for name, _ in calls], ['list_generated'])
                    elif mode in ('before_start_response', 'before_response_without_started'):
                        self.assertEqual([name for name, _ in calls], ['read_generated'])
                    else:
                        self.assertEqual([name for name, _ in calls], ['list_generated','read_generated'])
                    events=[json.loads(line) for line in (Path(tmp)/'session-transcript.jsonl').read_text().splitlines()]
                    local=[event['message'] for event in events if event['direction']=='local']
                    self.assertEqual(local[0]['tool_argument_policy_version'], 2)
                    rejections=[event for event in local if event.get('event')=='tool_arguments_rejected']
                    self.assertEqual(len(rejections), 1)
                    self.assertEqual(rejections[0]['call_id'], 'call-102')
                    self.assertEqual(rejections[0]['error']['path'], [] if mode=='null' else ['source_refs'])
                    self.assertFalse(any(event.get('nonsealable') for event in local))
                    if mode=='terminal':
                        self.assertTrue(any(event.get('event')=='terminal_deferred_until_tool_results' for event in local))
                    from regen.infrastructure.workspace_transcript import audit_workspace_session
                    audit = audit_workspace_session(Path(tmp)/'session-transcript.jsonl', require_real=False)
                    self.assertTrue(audit['terminal'])
                    self.assertEqual(audit['rejected_tool_calls'], 1)
                    self.assertEqual(audit['tool_calls'], len(calls)+1)

    def test_early_notification_owner_must_match_confirmed_turn(self):
        with tempfile.TemporaryDirectory() as tmp, CodexWorkspaceSession(
                tools=TOOLS, allow_external_call=True,
                command=[sys.executable, '-u', '-c', SERVER, 'before_response_wrong_turn']) as session:
            calls, notifications=[], []
            with self.assertRaisesRegex(SessionProtocolError, 'another thread/turn'):
                session.run('Inspect examples', lambda *args:calls.append(args),
                    artifact_dir=tmp, model='synthetic', timeout=3,
                    on_notification=lambda *args:notifications.append(args))
            self.assertEqual(calls, [])
            self.assertFalse(any(method in ('item/started','item/completed')
                                 for method, _ in notifications))
            self.assertEqual(session.tool_call_count, 0)

    def session(self):
        session=CodexWorkspaceSession(tools=TOOLS)
        session.thread_id, session.turn_id = 'thread', 'turn'
        return session

    def request(self):
        return {'id':102, 'method':'item/tool/call', 'params':{
            'threadId':'thread','turnId':'turn','callId':'call-102',
            'tool':'read_generated','namespace':None,'arguments':{'source_refs':[]}}}

    def test_invalid_arguments_do_not_relax_request_security(self):
        for field, value in (('threadId','foreign'), ('turnId','foreign'),
                             ('tool','exec'), ('tool',[]), ('namespace','shell'), ('callId','')):
            with self.subTest(field=field, value=value):
                session=self.session()
                message=self.request()
                message['params'][field]=value
                with self.assertRaises(SessionProtocolError):
                    session._queue_callback_tool(message)
                self.assertEqual(session._deferred, [])

    def test_missing_arguments_is_malformed_envelope_not_schema_feedback(self):
        session=self.session()
        message=self.request()
        del message['params']['arguments']
        with self.assertRaisesRegex(SessionProtocolError, 'malformed'):
            session._validate_tool_request(message)

    def test_duplicates_and_post_terminal_requests_still_fail(self):
        session=self.session()
        message=self.request()
        session._queue_callback_tool(message)
        with self.assertRaisesRegex(SessionProtocolError, 'duplicate call ID'):
            session._queue_callback_tool(deepcopy(message))
        duplicate_request=deepcopy(message)
        duplicate_request['params']['callId']='another-call'
        with self.assertRaisesRegex(SessionProtocolError, 'duplicate pending server request ID'):
            session._queue_callback_tool(duplicate_request)
        session._deferred=[]
        session._calls[('thread','turn','call-102')]='finished'
        with self.assertRaisesRegex(SessionProtocolError, 'duplicate call ID'):
            session._queue_callback_tool(message)
        session._deferred_terminal={'terminal':True}
        with self.assertRaisesRegex(SessionProtocolError, 'after turn completion'):
            session._queue_callback_tool(message)

    def test_lifecycle_schema_is_not_execution_but_identity_is_strict(self):
        session=self.session()
        params={'threadId':'thread','turnId':'turn','item':{
            'type':'dynamicToolCall','tool':'read_generated','namespace':None,
            'id':'item', 'arguments':{'source_refs':[]}}}
        for method in ('item/started','item/completed'):
            session._notification({'method':method,'params':params})
        self.assertEqual(session.tool_call_count, 0)
        for field, value in (('threadId','foreign'), ('turnId','foreign')):
            bad=deepcopy(params)
            bad[field]=value
            with self.assertRaises(SessionProtocolError):
                session._notification({'method':'item/started','params':bad})
        for field, value in (('tool','exec'), ('namespace','shell')):
            bad=deepcopy(params)
            bad['item'][field]=value
            with self.assertRaises(SessionProtocolError):
                session._notification({'method':'item/started','params':bad})


if __name__=='__main__':
    unittest.main()
