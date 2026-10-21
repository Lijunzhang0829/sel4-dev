"""Strict clock authority and missing model-item lifecycle regression controls."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession
from regen.tools import theory_effective_clock as clock


TOOLS = [{'name': 'query', 'description': 'test', 'inputSchema': {'type': 'object'}}]


class ControlledClockTrustTests(unittest.TestCase):
    def setUp(self):
        self.session = CodexWorkspaceSession(tools=TOOLS)
        self.session.thread_id, self.session.turn_id = 'thread', 'turn'
        self.session._callback_executing = True
        self.now = 0
        self.events = []
        patcher = patch('regen.infrastructure.progress_events.emit_event', side_effect=self.emit)
        patcher.start(); self.addCleanup(patcher.stop)
        self.session._generation_start()

    def emit(self, event, **fields):
        self.events.append(dict(event=event, at=self.now, run_dir='run', theory='T', **fields))

    def notify(self, method, identity='item', kind='agentMessage', **extra):
        self.session._model_work_notification(method, {
            'threadId': 'thread', 'turnId': 'turn',
            'item': {'type': kind, 'id': identity}, **extra})

    def state(self):
        result = {'clock_mode': clock.MODE}
        clock.clock_state(result)['events'] = deepcopy(self.events)
        return result

    def evaluate(self, state=None, now=5000, **options):
        return clock.evaluate(state or self.state(), {'run': {'running': True}}, now, 3600, **options)

    def test_normal_silence_still_consumes_whole_generation_budget(self):
        row = self.evaluate()
        self.assertTrue(row['evidence_trusted'])
        self.assertEqual(row['theories']['T']['effective_seconds'], 5000)
        self.assertEqual(row['first_crossing_at'], 3600)

    def test_missing_item_at_callback_end_is_durable_untrusted_not_a_refund(self):
        self.now = 10; self.notify('item/started')
        self.now = 20; self.session._model_work_boundary('callback_end')
        state = self.state()
        for policy in ('no_new_contract', 'fixed_budget'):
            with self.assertRaises(clock.ClockEvidenceError):
                self.evaluate(state, budget_policy=policy)
            snapshot = state['effective_clock']['snapshot']
            self.assertFalse(snapshot['evidence_trusted'])
            self.assertIsNone(snapshot['first_crossing_at'])
        saved = json.loads(json.dumps(state))
        with self.assertRaises(clock.ClockEvidenceError):
            self.evaluate(saved, now=6000)
        self.assertEqual(len(saved['effective_clock']['events']), len(self.events))

    def test_cross_callback_does_not_reopen_old_item_and_late_completion_is_safe(self):
        self.notify('item/started')
        self.session._callback_executing = False
        self.session._model_work_boundary('callback_end')
        count = sum(e['event']=='model_work_observed' for e in self.events)
        self.session._callback_executing = True
        self.session._model_work_boundary('callback_start')
        self.notify('item/completed')
        self.assertFalse(self.session._model_work_items)
        self.assertEqual(sum(e['event']=='model_work_observed' for e in self.events), count)

    def test_completed_only_and_duplicate_completion_are_idempotent(self):
        self.notify('item/completed', 'old')
        self.notify('item/completed', 'old')
        self.notify('item/started', 'new')
        self.notify('item/started', 'new')
        self.notify('item/completed', 'new')
        self.notify('item/completed', 'new')
        self.assertFalse(any(e['event']=='generation_clock_untrusted' for e in self.events))
        self.assertTrue(self.evaluate()['evidence_trusted'])

    def test_late_delta_cannot_reopen_completed_item(self):
        self.notify('item/started'); self.notify('item/completed')
        self.notify('item/agentMessage/delta', itemId='item', delta='late')
        self.assertFalse(self.session._model_work_items)
        self.assertFalse(self.session._model_work_observed)
        with self.assertRaises(clock.ClockEvidenceError):self.evaluate()

    def test_start_after_completed_is_untrusted_without_active_override(self):
        self.notify('item/completed'); self.notify('item/started')
        self.assertFalse(self.session._model_work_items)
        with self.assertRaises(clock.ClockEvidenceError):self.evaluate()

    def test_delta_before_start_can_complete_without_uncertainty(self):
        self.notify('item/agentMessage/delta', itemId='item', delta='first')
        self.notify('item/started'); self.notify('item/completed')
        self.assertTrue(self.evaluate()['evidence_trusted'])

    def test_fixed_budget_does_not_mutate_contract_progress(self):
        state = self.state()
        progress = [{'at': 3500, 'theory': 'T', 'proof_ids': ['contract:new'], 'run_dir': 'run'}]
        state['effective_clock']['progress'] = deepcopy(progress)
        self.assertEqual(self.evaluate(state, now=4000)['expired_theories'], [])
        fixed = self.evaluate(state, now=4000, budget_policy='fixed_budget')
        self.assertEqual(fixed['first_crossing_at'], 3600)
        self.assertEqual(fixed['theories']['T']['new_contract_count'], 1)
        self.assertEqual(fixed['theories']['T']['last_new_contract_at'], 3500)
        self.assertEqual(fixed['theories']['T']['idle_effective_seconds'], 500)
        self.assertEqual(fixed['theories']['T']['budget_effective_seconds'], 4000)
        self.assertEqual(state['effective_clock']['progress'], progress)

    def test_candidate_audit_is_charged_and_full_theory_seal_excluded(self):
        self.now=100; self.emit('isabelle_started', label='Semantic_Audit_Candidate')
        self.now=300; self.emit('isabelle_finished', label='Semantic_Audit_Candidate')
        self.now=400; self.emit('check_task_started', kind='seal', task_id='s')
        self.now=1400; self.emit('check_task_stopped', kind='seal', task_id='s')
        row = self.evaluate(now=1500)
        self.assertEqual(row['theories']['T']['effective_seconds'], 500)

    def test_legacy_model_override_cannot_certify_new_budget(self):
        self.now=10; self.emit('model_work_observed')
        with self.assertRaises(clock.ClockEvidenceError):self.evaluate()

    def test_sanitized_real_delete_trace_does_not_leak_into_later_prefix_wait(self):
        fixture = Path(__file__).with_name('fixtures')/'model_unclosed_delete_baseline.json'
        data = json.loads(fixture.read_text())
        self.events[0]['at'] = data['rows'][0]['time_unix']-1
        for row in data['rows']:
            self.now = row['time_unix']
            if 'message' in row:
                self.session._notification(deepcopy(row['message']))
            else:
                self.session._callback_executing = row['boundary']=='tool_started'
                self.session._model_work_boundary(row['boundary'])
        self.assertFalse(self.session._model_work_items)
        self.assertFalse(self.session._model_work_observed)
        self.assertEqual(sum(e['event']=='model_work_observed' for e in self.events), 1)
        self.now += 19.707
        self.emit('generation_exclusion_started', phase_id='later-prefix', category='prefix_preparation')
        start = self.now
        windows, _, _, _ = clock._root_timeline(self.events, 'run', start, True)
        before = clock.measure(windows['T'])
        windows, _, _, _ = clock._root_timeline(self.events, 'run', start+371.731, True)
        self.assertAlmostEqual(clock.measure(windows['T']), before, places=6)
        with self.assertRaises(clock.ClockEvidenceError):self.evaluate(now=start+371.731)

    def test_uncertainty_cannot_be_reset_by_a_new_generation(self):
        self.notify('item/started'); self.session._model_work_boundary('callback_end')
        self.now=100; self.session._generation_stop('test_restart')
        self.now=200; self.session._generation_start()
        with self.assertRaises(clock.ClockEvidenceError):self.evaluate()


if __name__ == '__main__':unittest.main()
