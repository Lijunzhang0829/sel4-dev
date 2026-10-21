"""Actual protocol-frame regression plus bounded lifecycle controls; no provider."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession
from regen.tools.theory_effective_clock import _root_timeline, measure

FIXTURE = Path(__file__).with_name('fixtures')/'model_overlap_actual_notifications.json'
TOOLS = [{'name':'query','description':'test','inputSchema':{'type':'object'}}]


class ModelObservationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.session=CodexWorkspaceSession(tools=TOOLS)
        self.session.thread_id='thread'
        self.session.turn_id='turn'
        self.session._callback_executing=True
        self.events=[]
        self.now=0
        self.patch=patch('regen.infrastructure.progress_events.emit_event',side_effect=self.emit)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def emit(self,event,**fields):
        self.events.append({'event':event,'at':self.now,'run_dir':'run','theory':'T',**fields})

    def notify(self,method,item_id='item',kind='reasoning',**extra):
        params={'threadId':'thread','turnId':'turn','item':{'id':item_id,'type':kind},**extra}
        self.session._notification({'method':method,'params':params})

    def effective(self,now,*,prepare=True,legacy=False):
        start=1791280517.929
        events=[{'event':'theory_generation_started','generation_id':'g',
                 'clock_schema':'theory_effective_v1','at':start,'run_dir':'run','theory':'T'}]
        if prepare:
            events += [dict(event='generation_exclusion_started',phase_id='runtime',category='runtime_setup',at=start,run_dir='run',theory='T'),
                       dict(event='generation_exclusion_started',phase_id='provider',category='provider_build',at=1791280557.017,run_dir='run',theory='T')]
        events += self.events[:1] if legacy else self.events
        intervals,_,_,_=_root_timeline(events,'run',now,True)
        return measure(intervals['T'])

    def test_actual_empty_reasoning_completions_close_before_long_provider_wait(self):
        data=json.loads(FIXTURE.read_text())
        owner=data['rows'][0]['message']['params']
        self.session.thread_id,self.session.turn_id=owner['threadId'],owner['turnId']
        for row in data['rows']:
            self.now=row['time_unix']
            self.session._notification(deepcopy(row['message']))
        expected=sum(data['rows'][i+1]['time_unix']-data['rows'][i]['time_unix'] for i in (0,2))
        for now in (1791280610.,1791287810.):
            self.assertAlmostEqual(self.effective(now),expected,places=6)
            self.assertAlmostEqual(self.effective(now,legacy=True),now-data['rows'][0]['time_unix'],places=6)
            self.assertAlmostEqual(self.effective(now,prepare=False),now-1791280517.929,places=6)
        self.assertFalse(self.session._model_work_observed)
        self.assertEqual(len(self.events),4)

    def test_overlapping_items_only_close_when_last_completes(self):
        self.notify('item/started','a')
        self.notify('item/started','b',kind='agentMessage')
        self.notify('item/completed','a')
        self.assertTrue(self.session._model_work_observed)
        self.notify('item/completed','b',kind='agentMessage')
        self.assertEqual([e['event'] for e in self.events],['model_work_observed','model_work_observation_finished'])

    def test_duplicate_late_or_foreign_completion_does_not_open_or_close_other_item(self):
        self.notify('item/completed','old')
        self.notify('item/started','active')
        self.notify('item/completed','active',turnId='other')
        self.notify('item/completed','old')
        self.assertTrue(self.session._model_work_observed)
        self.notify('item/completed','active')
        self.notify('item/completed','active')
        self.assertEqual(len(self.events),2)

    def test_delta_before_start_has_matching_lifecycle(self):
        self.notify('item/reasoning/textDelta',itemId='item',delta='text')
        self.notify('item/started')
        self.assertEqual(len(self.events),1)
        self.notify('item/completed')
        self.assertFalse(self.session._model_work_observed)
        self.assertFalse(self.session._model_work_items)

    def test_unknown_unclosed_item_remains_charged(self):
        self.now=1791280558.
        self.notify('item/started')
        self.assertEqual(self.effective(1791280610.),52.)
        self.assertEqual(self.effective(1791287810.),7252.)

    def test_dynamic_tool_lifecycle_never_marks_tool_wait_as_model_work(self):
        params={'threadId':'thread','turnId':'turn','item':{'id':'tool','type':'dynamicToolCall','tool':'query','namespace':None}}
        for method in ('item/started','item/completed'):
            self.session._notification({'method':method,'params':params})
        self.assertEqual(self.events,[])

    def test_failed_session_boundary_quarantines_unfinished_item_and_marks_uncertainty(self):
        self.notify('item/started')
        self.session._callback_executing=False
        self.session._model_work_boundary('session_stopped_with_open_model_item')
        self.assertFalse(self.session._model_work_observed)
        self.assertFalse(self.session._model_work_items)
        self.session._callback_executing=True
        self.session._model_work_boundary('session_stopped_with_open_model_item')
        self.notify('item/completed')
        self.assertEqual([e['event'] for e in self.events],
                         ['model_work_observed','model_work_observation_finished',
                          'generation_clock_untrusted'])

    def test_terminal_clears_items_and_cannot_leak_to_next_turn(self):
        self.notify('item/started')
        self.session._callback_notification({'method':'turn/completed','params':{
            'threadId':'thread','turn':{'id':'turn','status':'completed'}}})
        self.assertFalse(self.session._model_work_items)
        self.assertFalse(self.session._model_work_observed)

        self.session.turn_id='next'
        self.notify('item/started','stale')
        self.assertFalse(self.session._model_work_observed)

    def test_live_item_spans_callback_without_fabricated_completion(self):
        self.notify('item/started')
        self.session._end_model_callback()
        self.assertFalse(self.session._model_work_observed)
        self.assertTrue(self.session._model_work_items)
        self.assertFalse(self.session._model_work_closed_items)
        self.session._begin_model_callback()
        self.assertTrue(self.session._model_work_observed)
        self.notify('item/completed')
        self.session._end_model_callback()
        self.session._begin_model_callback()
        self.assertFalse(self.session._model_work_observed)
        self.assertFalse(self.session._model_work_items)
        self.assertNotIn('generation_clock_untrusted', [e['event'] for e in self.events])

    def test_002_reasoning_item_survives_later_tool_callbacks_until_turn_terminal(self):
        # Run 002 received a reasoning start without its completion, then a
        # second completed reasoning item and a repository-plan callback.
        # A callback boundary is not a model-item completion.
        self.session._generation_active=True
        self.session._callback_executing=False
        self.notify('item/started','rs_002_unclosed')
        self.notify('item/started','rs_002_later')
        self.notify('item/completed','rs_002_later')
        self.assertFalse(self.session._model_work_observed)
        self.session._begin_model_callback()
        self.assertTrue(self.session._model_work_observed)
        self.session._end_model_callback()
        self.assertIn(('thread','turn','reasoning','rs_002_unclosed'),
                      self.session._model_work_items)
        self.session._begin_model_callback()
        self.session._callback_notification({'method':'turn/completed','params':{
            'threadId':'thread','turn':{'id':'turn','status':'completed'}}})
        self.session._end_model_callback()
        self.session._generation_stop('completed')
        self.assertFalse(self.session._model_work_items)
        self.assertNotIn('generation_clock_untrusted',
                         [e['event'] for e in self.events])

    def test_completion_between_callbacks_does_not_reopen_override(self):
        self.notify('item/started')
        self.session._end_model_callback()
        self.notify('item/completed')
        self.session._begin_model_callback()
        self.assertFalse(self.session._model_work_observed)
        self.assertFalse(self.session._model_work_items)

    def test_session_abort_still_marks_truly_unclosed_item_untrusted(self):
        self.session._generation_active=True
        self.notify('item/started')
        self.session._end_model_callback()
        self.session._generation_stop('SessionCancelled')
        self.assertIn('generation_clock_untrusted', [e['event'] for e in self.events])


if __name__=='__main__': unittest.main()
