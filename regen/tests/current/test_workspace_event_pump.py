"""Real local stdio peers exercise busy-callback event intake and fail-closed order.

No model or Isabelle is launched. Sleep durations are protocol payloads, not a
claim that this small regression suite waits four real forty-second intervals.
"""
from contextvars import ContextVar
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
import unittest

from regen.infrastructure.codex_workspace_session import (
    CodexWorkspaceSession, SessionCancelled, SessionProtocolError,
)
from regen.infrastructure.workspace_transcript import audit_workspace_session


TOOLS = [{'name': 'proof_action', 'description': 'scripted test action',
          'inputSchema': {'type': 'object', 'properties': {'number': {'type': 'integer'}},
                          'required': ['number'], 'additionalProperties': False}}]

SERVER = r'''
import json,os,sys,threading,time
from pathlib import Path
mode,root=sys.argv[1:3]
lock=threading.Lock()
def send(message):
 with lock: print(json.dumps(message),flush=True)
def tool_message(number, *, owner='thread', call_id=None, request_id=None):
 return {'id':100+number if request_id is None else request_id,'method':'item/tool/call','params':{
  'threadId':owner,'turnId':'turn','callId':call_id or 'call-'+str(number),
  'namespace':None,'tool':'proof_action','arguments':{'number':number}}}
def tool(number, **kwargs):send(tool_message(number,**kwargs))
def terminal_message(owner='thread'):
 return {'method':'turn/completed','params':{
  'threadId':owner,'turn':{'id':'turn','status':'completed'}}}
def terminal(owner='thread'):
 send(terminal_message(owner))
def preflight_batch(request_id):
 response={'id':request_id,'result':{'turn':{'id':'turn'}}}
 unsafe={'method':'item/started','params':{'threadId':'thread','turnId':'turn',
  'item':{'type':'commandExecution','id':'unsafe-prebuffered'}}}
 batches={
  'preflight_unsafe':[response,tool_message(1),tool_message(2),unsafe],
  'preflight_terminal':[response,tool_message(1),terminal_message()],
  'preflight_before_response_unsafe':[tool_message(1),response,unsafe],
  'preflight_before_response_duplicate':[tool_message(1),tool_message(2,call_id='call-1'),response],
  'preflight_before_response_terminal':[tool_message(1),terminal_message(),response],
  'preflight_clean':[response,tool_message(1),tool_message(2)],
  'preflight_before_response_clean':[tool_message(1),tool_message(2),response],
 }
 # One syscall makes the preflight buffer case deterministic, not a timing race.
 with lock:os.write(sys.stdout.fileno(),(''.join(json.dumps(x)+'\n' for x in batches[mode])).encode())
def no_progress_stream(request_id):
 send({'id':request_id,'result':{'turn':{'id':'turn'}}})
 # Finite peer lifetime prevents a broken timeout implementation hanging tests.
 # These are display payloads only; no forty-second wall-clock sleep occurs.
 for index in range(125):
  if mode=='no_progress_sleep':
   params={'threadId':'thread','turnId':'turn','item':{
    'type':'sleep','id':'idle-'+str(index),'durationMs':40000}}
   send({'method':'item/started','params':params})
   send({'method':'item/completed','params':params})
  else:
   send({'method':'item/agentMessage/delta','params':{
    'threadId':'thread','turnId':'turn','delta':'passive-'+str(index)}})
  time.sleep(.02)
 terminal()
def inject():
 deadline=time.monotonic()+3
 while not (Path(root)/'callback-entered').exists():
  if time.monotonic()>deadline: return
  time.sleep(.005)
 if mode in ('pump','sleep_four'):
  if mode=='sleep_four':
   for index in range(4):
    params={'threadId':'thread','turnId':'turn','item':{
     'type':'sleep','id':'sleep-'+str(index),'durationMs':40000}}
    send({'method':'item/started','params':params})
    send({'method':'item/completed','params':params})
  send({'method':'item/agentMessage/delta','params':{
   'threadId':'thread','turnId':'turn','delta':'busy-callback-tail-marker'}})
 elif mode in ('serial','cancel','signal'):
  tool(2)
 elif mode in ('reasoning_after_reply','reasoning_across_two'):
  send({'method':'item/started','params':{'threadId':'thread','turnId':'turn',
   'item':{'type':'reasoning','id':'spanning-reasoning','summary':[],'content':[]}}})
 elif mode=='network_pause':
  send({'method':'item/started','params':{'threadId':'thread','turnId':'turn',
   'item':{'type':'reasoning','id':'interrupted-reasoning','summary':[],'content':[]}}})
  send({'method':'error','params':{'threadId':'thread','turnId':'turn','willRetry':True,
   'error':{'codexErrorInfo':{'responseStreamDisconnected':{}},
            'message':'stream disconnected before completion'}}})
 elif mode=='duplicate':
  tool(2,call_id='call-1')
 elif mode=='duplicate_active_rpc':
  tool(2,request_id=101)
 elif mode=='duplicate_queued_rpc':
  tool(2)
  tool(3,request_id=102)
 elif mode=='queue_overflow':
  for number in range(2,67):tool(number)
 elif mode=='queued_then_builtin':
  tool(2)
  send({'method':'item/started','params':{'threadId':'thread','turnId':'turn',
   'item':{'type':'commandExecution','id':'unsafe-after-queued-tool'}}})
 elif mode=='builtin':
  send({'method':'item/started','params':{'threadId':'thread','turnId':'turn',
   'item':{'type':'commandExecution','id':'unsafe-command'}}})
  tool(2)
 elif mode=='approval':
  send({'id':900,'method':'item/commandExecution/requestApproval','params':{}})
  tool(2)
 elif mode=='wrong_owner':
  send({'method':'item/started','params':{'threadId':'foreign','turnId':'turn',
   'item':{'type':'sleep','id':'foreign-sleep','durationMs':1}}})
  tool(2)
 elif mode=='wrong_tool_owner':
  tool(2,owner='foreign')
  tool(3)
 elif mode in ('terminal_pending', 'terminal_queued', 'terminal_duplicate', 'terminal_failed'):
  if mode=='terminal_queued':tool(2)
  value=terminal_message()
  if mode=='terminal_failed':value['params']['turn']['status']='failed'
  send(value)
  if mode=='terminal_duplicate':send(value)
 elif mode=='early_terminal':
  terminal()
  tool(2)
 elif mode=='unfinished_sleep':
  send({'method':'item/started','params':{'threadId':'thread','turnId':'turn',
   'item':{'type':'sleep','id':'unfinished','durationMs':1}}})
 elif mode=='wrong_terminal_owner':
  terminal('foreign')
  tool(2)
for line in sys.stdin:
 message=json.loads(line);method=message.get('method')
 if method=='initialize':send({'id':message['id'],'result':{}})
 elif method=='thread/start':send({'id':message['id'],'result':{'thread':{'id':'thread'}}})
 elif method=='turn/start':
  if mode=='before_response_queue65':
   for number in range(1,66):tool(number)
   # Deliberately never confirm turn/start: _await must reject the 65th call.
  elif mode.startswith('no_progress_'):no_progress_stream(message['id'])
  elif mode.startswith('preflight_'):preflight_batch(message['id'])
  else:
   send({'id':message['id'],'result':{'turn':{'id':'turn'}}})
   tool(1)
   threading.Thread(target=inject,daemon=True).start()
 elif 'result' in message:
  if mode in ('reasoning_after_reply','reasoning_across_two'):
   if message['id']==101:
    if mode=='reasoning_after_reply':
     send({'method':'item/completed','params':{'threadId':'thread','turnId':'turn',
      'item':{'type':'reasoning','id':'spanning-reasoning','summary':[],'content':[]}}})
    tool(2)
   elif message['id']==102:
    if mode=='reasoning_across_two':
     send({'method':'item/completed','params':{'threadId':'thread','turnId':'turn',
      'item':{'type':'reasoning','id':'spanning-reasoning','summary':[],'content':[]}}})
    terminal()
   continue
  if mode in ('terminal_pending','terminal_queued','terminal_duplicate','terminal_failed'):continue
  if mode in ('serial','preflight_clean','preflight_before_response_clean'):
   if message['id']==102:terminal()
  elif message['id']==101:terminal()
'''


def events(path):
    if not path.exists():
        return []
    output = []
    for line in path.read_text().splitlines():
        try:
            output.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # Concurrent append may expose the not-yet-complete final line.
    return output


class WorkspaceEventPumpTests(unittest.TestCase):
    def test_reconnecting_during_callback_preserves_owner_and_finishes_real_transport(self):
        from regen.infrastructure.effective_time_budget import EffectiveTimeBudget
        from regen.infrastructure.experiment_ledger import Ledger
        from regen.infrastructure.parallel_generation_budget import ParallelGenerationBudgetBridge
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            stage=EffectiveTimeBudget(Ledger(root/'budget','test'),seconds=100)
            stage.register({'T':['contract']})
            bridge=ParallelGenerationBudgetBridge(stage)
            self.addCleanup(stage.close);self.addCleanup(bridge.close)
            calls=[];aborted=threading.Event()
            def callback(name,arguments,metadata):
                calls.append(arguments['number']);self.mark_entered(root)
                self.assertFalse(aborted.wait(.15),'reconnecting aborted active callback')
                return {'unpublished_candidate':True}
            with bridge.worker('T'),self.session(root,'network_pause') as session:
                result=session.run('x',callback,artifact_dir=root,model='test',timeout=4,
                                   cancel_event=stage.event,on_abort=aborted.set)
                self.assertEqual(result['status'],'completed')
            self.assertEqual(calls,[1])
            self.assertIsNone(stage.reason)
            self.assertFalse(stage.event.is_set())
            self.assertFalse(stage.snapshot()['gate_open'])
            self.assertFalse(bridge.phases)
            self.assertFalse(session._model_work_items)
            self.assertNotIn('generation_clock_untrusted',[r['kind'] for r in stage.ledger.rows])
            self.assertIsNotNone(session.process.poll())

    def test_model_item_can_finish_after_tool_reply_without_budget_cancellation(self):
        from regen.infrastructure.progress_events import observe_events
        for mode in ('reasoning_after_reply','reasoning_across_two'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                opened=threading.Event()
                cancel=threading.Event()
                observed=[]
                calls=[]
                def observer(event, fields):
                    observed.append(event)
                    if event=='model_work_observed':opened.set()
                    if event=='generation_clock_untrusted':cancel.set()
                def callback(name, arguments, metadata):
                    calls.append(arguments['number'])
                    if arguments['number']==1:
                        self.mark_entered(root)
                        self.assertTrue(opened.wait(2), 'reasoning start was not ingested')
                    return {'done':True}
                with observe_events(observer), self.session(root,mode) as session:
                    result=session.run('x',callback,artifact_dir=root,model='test',
                                       timeout=3,cancel_event=cancel)
                self.assertEqual(result['status'],'completed')
                self.assertEqual(calls,[1,2])
                self.assertFalse(cancel.is_set())
                self.assertNotIn('generation_clock_untrusted',observed)
                self.assertEqual(observed.count('model_work_observed'),
                                 2 if mode=='reasoning_across_two' else 1)
                self.assertEqual(observed.count('model_work_observed'),
                                 observed.count('model_work_observation_finished'))
                self.assertTrue(audit_workspace_session(root/'session-transcript.jsonl',
                                                       require_real=False)['terminal'])

    def session(self, root, mode, **kwargs):
        return CodexWorkspaceSession(tools=TOOLS, allow_external_call=True,
            command=[sys.executable, '-u', '-c', SERVER, mode, str(root)], **kwargs)

    def mark_entered(self, root):
        (root/'callback-entered').touch()

    def test_event_is_durably_ingested_before_busy_callback_returns(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            observed = []
            def callback(*args):
                self.mark_entered(root)
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    found = any(row.get('direction') == 'in' and
                        row['message']['parsed'].get('params', {}).get('delta') ==
                        'busy-callback-tail-marker' for row in events(root/'session-transcript.jsonl'))
                    if found:
                        observed.append(True)
                        break
                    time.sleep(.005)
                return {'finished': True}
            with self.session(root, 'pump', progress_timeout=.2) as session:
                result = session.run('x', callback, artifact_dir=root, model='test', timeout=.2)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(observed, [True], 'protocol event stayed unread until callback finished')
            audit = audit_workspace_session(root/'session-transcript.jsonl', require_real=False)
            self.assertTrue(audit['terminal'])

    def test_four_distinct_forty_second_sleep_items_during_callback_are_legal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            calls = []
            def callback(name, arguments, metadata):
                calls.append(arguments['number'])
                self.mark_entered(root)
                time.sleep(.2)
                return {'done': True}
            with self.session(root, 'sleep_four') as session:
                result = session.run('x', callback, artifact_dir=root, model='test', timeout=2)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(calls, [1])
            raw = events(root/'session-transcript.jsonl')
            sleeps = [row['message']['parsed']['params']['item'] for row in raw
                if row.get('direction') == 'in' and row['message']['parsed'].get('method') == 'item/started'
                and row['message']['parsed'].get('params', {}).get('item', {}).get('type') == 'sleep']
            self.assertEqual([item['durationMs'] for item in sleeps], [40000] * 4)
            self.assertTrue(audit_workspace_session(root/'session-transcript.jsonl', require_real=False)['terminal'])

    def test_queued_tools_execute_serially_and_keep_contextvars(self):
        scope = ContextVar('event-pump-test-scope', default='missing')
        token = scope.set('bound-run-context')
        self.addCleanup(scope.reset, token)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seen, active, peak = [], [0], [0]
            lock = threading.Lock()
            def callback(name, arguments, metadata):
                with lock:
                    active[0] += 1
                    peak[0] = max(peak[0], active[0])
                try:
                    seen.append((arguments['number'], scope.get()))
                    self.mark_entered(root)
                    if arguments['number'] == 1:
                        time.sleep(.2)
                    return {'number': arguments['number']}
                finally:
                    with lock:
                        active[0] -= 1
            with self.session(root, 'serial') as session:
                result = session.run('x', callback, artifact_dir=root, model='test', timeout=2)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(seen, [(1, 'bound-run-context'), (2, 'bound-run-context')])
            self.assertEqual(peak[0], 1)
            self.assertTrue(audit_workspace_session(root/'session-transcript.jsonl', require_real=False)['terminal'])

    def test_duplicate_identity_is_never_dispatched_twice(self):
        self.assert_fail_closed('duplicate')

    def test_preflight_rejects_buffered_unsafe_work_before_any_callback(self):
        for mode in ('preflight_unsafe', 'preflight_terminal',
                     'preflight_before_response_unsafe', 'preflight_before_response_duplicate',
                     'preflight_before_response_terminal'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root, calls = Path(temporary), []
                def callback(name, arguments, metadata):
                    calls.append(arguments['number'])
                    return {'unexpected_dispatch': True}
                with self.session(root, mode) as session:
                    with self.assertRaises(SessionProtocolError):
                        session.run('x', callback, artifact_dir=root, model='test', timeout=2)
                    self.assertIsNotNone(session.process.poll())
                self.assertEqual(calls, [], 'already buffered unsafe event allowed mathematical work')

    def test_preflight_preserves_two_tool_order_across_turn_response(self):
        for mode in ('preflight_clean', 'preflight_before_response_clean'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root, calls = Path(temporary), []
                def callback(name, arguments, metadata):
                    calls.append(arguments['number'])
                    return {'ordered_dispatch': True}
                with self.session(root, mode) as session:
                    result = session.run('x', callback, artifact_dir=root, model='test', timeout=2)
                self.assertEqual(result['status'], 'completed')
                self.assertEqual(calls, [1, 2])
                self.assertTrue(audit_workspace_session(root/'session-transcript.jsonl', require_real=False)['terminal'])

    def test_sixty_fifth_tool_before_turn_response_is_rejected_without_dispatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, calls = Path(temporary), []
            def callback(name, arguments, metadata):
                calls.append(arguments['number'])
                return {'unexpected_dispatch': True}
            with self.session(root, 'before_response_queue65') as session:
                with self.assertRaisesRegex(SessionProtocolError, 'too many queued serial tool requests'):
                    session.run('x', callback, artifact_dir=root, model='test', timeout=2)
                self.assertIsNotNone(session.process.poll())
            self.assertEqual(calls, [])
            requests = [event for event in events(root/'session-transcript.jsonl')
                if event.get('direction') == 'in' and
                event['message']['parsed'].get('method') == 'item/tool/call']
            self.assertEqual(len(requests), 65)

    def test_default_progress_timeout_bounds_continuous_nonprogress_events(self):
        for mode in ('no_progress_sleep', 'no_progress_passive'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root, calls = Path(temporary), []
                def callback(name, arguments, metadata):
                    calls.append(arguments['number'])
                    return {'unexpected_dispatch': True}
                started, timeout = time.monotonic(), .4
                with self.session(root, mode) as session:  # No progress_timeout override.
                    with self.assertRaisesRegex(TimeoutError, 'without tool progress'):
                        session.run('x', callback, artifact_dir=root, model='test', timeout=timeout)
                    self.assertIsNotNone(session.process.poll())
                elapsed = time.monotonic() - started
                self.assertGreaterEqual(elapsed, timeout * .8)
                self.assertLess(elapsed, timeout + 1, 'live event stream kept resetting the deadline')
                self.assertEqual(calls, [])
                stream = [event for event in events(root/'session-transcript.jsonl')
                    if event.get('direction') == 'in' and event['message']['parsed'].get('method')
                    in {'item/started', 'item/agentMessage/delta'}]
                self.assertGreaterEqual(len(stream), 4, 'peer failed to provide a live legal event stream')
                with self.assertRaises(ValueError):
                    audit_workspace_session(root/'session-transcript.jsonl', require_real=False)

    def test_pending_server_request_ids_are_unique_and_queue_is_bounded(self):
        for mode in ('duplicate_active_rpc', 'duplicate_queued_rpc', 'queue_overflow'):
            with self.subTest(mode=mode):
                self.assert_callback_abort(mode)

    def test_unsafe_event_discards_already_queued_tool_without_dispatch(self):
        self.assert_callback_abort('queued_then_builtin')

    def assert_callback_abort(self, mode):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            aborted = threading.Event()
            calls, observed = [], []
            def callback(name, arguments, metadata):
                calls.append(arguments['number'])
                self.mark_entered(root)
                observed.append(aborted.wait(1))
                return {'uncommitted': True}
            with self.session(root, mode) as session:
                with self.assertRaises(SessionProtocolError):
                    session.run('x', callback, artifact_dir=root, model='test', timeout=2,
                                on_abort=aborted.set)
                self.assertIsNotNone(session.process.poll())
            self.assertEqual(calls, [1])
            self.assertEqual(observed, [True])
            self.assertFalse(any(event.get('direction') == 'out' and
                event['message'].get('id') == 101 and 'result' in event['message']
                for event in events(root/'session-transcript.jsonl')),
                'unsafe callback produced a successful tool response')
            with self.assertRaises(ValueError):
                audit_workspace_session(root/'session-transcript.jsonl', require_real=False)

    def test_unsafe_event_before_next_tool_prevents_its_dispatch(self):
        for mode in ('builtin', 'approval', 'wrong_owner', 'wrong_tool_owner'):
            with self.subTest(mode=mode):
                self.assert_fail_closed(mode)

    def test_completed_turn_drains_preterminal_callbacks_once_and_audits(self):
        for mode, expected in [('terminal_pending', [1]), ('terminal_queued', [1, 2])]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                calls = []
                def callback(name, arguments, metadata):
                    calls.append(arguments['number'])
                    self.mark_entered(root)
                    time.sleep(.15)
                    return {'done': arguments['number']}
                with self.session(root, mode) as session:
                    result = session.run('x', callback, artifact_dir=root, model='test', timeout=2)
                    self.assertEqual(result['status'], 'completed')
                self.assertEqual(calls, expected)
                path = root/'session-transcript.jsonl'
                report = audit_workspace_session(path, require_real=False)
                self.assertTrue(report['terminal'])
                rows = events(path)
                terminal = next(i for i, e in enumerate(rows) if e['direction']=='in'
                    and e['message']['parsed'].get('method')=='turn/completed')
                results = [i for i, e in enumerate(rows) if e['direction']=='out'
                    and 'result' in e['message']]
                self.assertEqual(len(results), len(expected))
                self.assertTrue(all(i > terminal for i in results))
                prefix = root/'pending-prefix.jsonl'
                prefix.write_text(''.join(json.dumps(e)+'\n' for e in rows[:terminal+1]))
                with self.assertRaises(ValueError):
                    audit_workspace_session(prefix, require_real=False)
                self.assertFalse(audit_workspace_session(prefix, require_real=False,
                    require_terminal=False)['terminal'])
                rows[0]['message']['pending_terminal_policy_version'] = 1
                legacy = root/'legacy.jsonl'
                legacy.write_text(''.join(json.dumps(e)+'\n' for e in rows))
                with self.assertRaisesRegex(ValueError, 'before pending tool results'):
                    audit_workspace_session(legacy, require_real=False)

    def test_terminal_with_pending_tool_or_sleep_cannot_complete(self):
        for mode in ('early_terminal', 'unfinished_sleep', 'wrong_terminal_owner',
                     'terminal_duplicate', 'terminal_failed'):
            with self.subTest(mode=mode):
                self.assert_fail_closed(mode)

    def assert_fail_closed(self, mode):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            calls = []
            def callback(name, arguments, metadata):
                calls.append(arguments['number'])
                self.mark_entered(root)
                time.sleep(.2)
                return {'done': True}
            with self.session(root, mode) as session:
                with self.assertRaises(SessionProtocolError):
                    session.run('x', callback, artifact_dir=root, model='test', timeout=2)
                self.assertIsNotNone(session.process.poll())
                with self.assertRaises(RuntimeError):
                    session.run('again', callback, artifact_dir=root, model='test', timeout=2)
            self.assertEqual(calls, [1], 'unsafe buffered work was dispatched')
            with self.assertRaises(ValueError):
                audit_workspace_session(root/'session-transcript.jsonl', require_real=False)

    def test_cancel_during_callback_never_dispatches_buffered_second_tool(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cancel = threading.Event()
            calls, timers = [], []
            def callback(name, arguments, metadata):
                calls.append(arguments['number'])
                self.mark_entered(root)
                timer = threading.Timer(.03, cancel.set)
                timers.append(timer)
                timer.start()
                time.sleep(.2)
                return {'late': True}
            try:
                with self.session(root, 'cancel') as session:
                    with self.assertRaises(SessionCancelled):
                        session.run('x', callback, artifact_dir=root, model='test', timeout=2, cancel_event=cancel)
                    self.assertIsNotNone(session.process.poll())
                self.assertEqual(calls, [1])
            finally:
                for timer in timers:
                    timer.cancel()
                    timer.join(timeout=1)

    def test_abort_hook_releases_busy_callback_before_unsafe_work_dispatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            aborted = threading.Event()
            calls, observed, abort_count = [], [], []
            def abort():
                abort_count.append(1)
                aborted.set()
            def callback(name, arguments, metadata):
                calls.append(arguments['number'])
                self.mark_entered(root)
                observed.append(aborted.wait(1))
                return {'cancelled_native_work': True}
            with self.session(root, 'builtin') as session:
                with self.assertRaises(SessionProtocolError):
                    session.run('x', callback, artifact_dir=root, model='test', timeout=2, on_abort=abort)
                self.assertIsNotNone(session.process.poll())
            self.assertEqual(observed, [True], 'unsafe protocol was detected only after callback returned')
            self.assertEqual(calls, [1])
            self.assertEqual(abort_count, [1])

    def test_cancel_event_triggers_cooperative_abort_hook_while_callback_is_busy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cancel, aborted = threading.Event(), threading.Event()
            observed, calls, timers = [], [], []
            def callback(name, arguments, metadata):
                calls.append(arguments['number'])
                self.mark_entered(root)
                timer = threading.Timer(.03, cancel.set)
                timers.append(timer)
                timer.start()
                observed.append(aborted.wait(1))
                return {'cancelled_native_work': True}
            try:
                with self.session(root, 'cancel') as session:
                    with self.assertRaises(SessionCancelled):
                        session.run('x', callback, artifact_dir=root, model='test', timeout=2,
                                    cancel_event=cancel, on_abort=aborted.set)
                    self.assertIsNotNone(session.process.poll())
                self.assertEqual(observed, [True])
                self.assertEqual(calls, [1])
            finally:
                for timer in timers:
                    timer.cancel()
                    timer.join(timeout=1)

    @unittest.skipUnless(hasattr(signal, 'SIGTERM'), 'requires POSIX SIGTERM')
    def test_signal_during_callback_cancels_and_restores_original_handler(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = signal.getsignal(signal.SIGTERM)
            calls, timers = [], []
            def callback(name, arguments, metadata):
                calls.append(arguments['number'])
                self.mark_entered(root)
                timer = threading.Timer(.03, lambda: os.kill(os.getpid(), signal.SIGTERM))
                timers.append(timer)
                timer.start()
                time.sleep(.2)
                return {'late': True}
            try:
                with self.session(root, 'signal') as session:
                    with self.assertRaises(SessionCancelled):
                        session.run('x', callback, artifact_dir=root, model='test', timeout=2)
                    self.assertIsNotNone(session.process.poll())
                self.assertEqual(calls, [1])
                self.assertIs(signal.getsignal(signal.SIGTERM), original)
            finally:
                for timer in timers:
                    timer.cancel()
                    timer.join(timeout=1)


if __name__ == '__main__':
    unittest.main()
