"""Regression cases for observations recovered after a planner commit window."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from regen.planning import batch_file_planner as planner
from regen.tests.current.test_proof_commits import fixture


class BatchMemoryRecoveryTest(unittest.TestCase):
    def artifact(self, root, number, **artifacts):
        directory = root / 'batch-requests' / f'{number:06d}'
        directory.mkdir(parents=True, exist_ok=True)
        for name, value in artifacts.items():
            (directory / (name + '.json')).write_text(json.dumps(value))

    def query(self, target='contract'):
        return {'action': 'query', 'request': {'goal_query': True,
                'context_node_id': target, 'purpose': 'helper'}}

    def facts(self):
        return {'kind': 'fact_selection', 'status': 'available', 'queries': [],
                'records': [{'record_id': 'premise:known', 'statement': 'True'}]}

    def test_feedback_updates_are_pure_and_request_idempotent(self):
        _, state = fixture()
        original = deepcopy(state)
        proposal, feedback = self.query(), self.facts()
        first = planner.apply_feedback(state, proposal, feedback, 1)
        self.assertEqual(state, original)
        self.assertEqual(first['batch_facts'], feedback['records'])
        self.assertEqual(planner.apply_feedback(first, proposal, feedback, 1), first)
        feedback['records'][0]['statement'] = 'False'
        self.assertEqual(first['batch_facts'][0]['statement'], 'True')

    def test_recovery_replays_query_and_read_proofs_missing_from_committed_state(self):
        _, state = fixture()
        state.update(batch_requests=2, proofs={'helper': 'by simp'}, accepted_order=['helper'])
        original = deepcopy(state)
        example = {'id': 'helper', 'statement': 'lemma helper: "True & True"',
                   'context': [], 'proof_body': 'by simp'}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.artifact(root, 1, proposal=self.query(), feedback=self.facts())
            self.artifact(root, 2, proposal={'action': 'read_proofs', 'node_ids': ['helper']},
                          feedback={'status': 'available', 'proofs': [example]})
            before = {p: p.read_bytes() for p in root.rglob('*.json')}
            restored = planner.restore_batch_memory(root, state)
            self.assertEqual(restored['batch_facts'], self.facts()['records'])
            self.assertEqual(restored['batch_examples']['helper'], example)
            self.assertEqual(restored['batch_feedback']['proofs'], [example])
            self.assertEqual(planner.restore_batch_memory(root, restored), restored)
            self.assertEqual({p: p.read_bytes() for p in root.rglob('*.json')}, before)
        self.assertEqual(state, original)
        for key in ('proofs', 'accepted_order', 'graph', 'batch_requests'):
            self.assertEqual(restored[key], original[key])

    def test_recovery_retains_rejected_json_without_a_proposal(self):
        _, state = fixture()
        state['batch_requests'] = 1
        feedback = {'status': 'rejected_request', 'diagnostic': 'expected one JSON action object'}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.artifact(root, 1, feedback=feedback)
            restored = planner.restore_batch_memory(root, state)
        self.assertEqual(restored['batch_feedback'], feedback)
        self.assertEqual(restored['batch_history'][0]['request_number'], 1)
        self.assertEqual(restored['batch_history'][0]['status'], 'rejected_request')

    def test_later_same_proof_does_not_turn_earlier_red_into_acceptance(self):
        _, state = fixture()
        state.update(batch_requests=2, proofs={'helper': 'by simp'}, accepted_order=['helper'],
                     batch_history=[{'request_number': 2, 'action': 'submit_batch', 'status': 'accepted'}])
        proposal = {'action': 'submit_batch', 'proofs': {'helper': 'by simp'}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.artifact(root, 1, proposal=proposal,
                          check={'check': {'verdict': 'red', 'diagnostic': 'context was insufficient'}})
            self.artifact(root, 2, proposal=proposal, check={'check': {'verdict': 'green'}})
            restored = planner.restore_batch_memory(root, state, accepted_results={2:{'status':'accepted'}})
        history = {item['request_number']: item for item in restored['batch_history']}
        self.assertEqual(history[1]['status'], 'rejected_batch')
        self.assertEqual(history[2]['status'], 'accepted')
        self.assertEqual(restored['proofs'], state['proofs'])

    def test_uncommitted_green_cannot_be_accepted_by_matching_proof_text(self):
        _, state = fixture()
        state.update(batch_requests=1, proofs={'helper': 'by simp'}, accepted_order=['helper'])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.artifact(root, 1, proposal={'action': 'submit_batch', 'proofs': {'helper': 'by simp'}},
                          check={'check': {'verdict': 'green'}})
            restored = planner.restore_batch_memory(root, state)
        self.assertEqual(restored['batch_history'][0]['status'], 'checked_not_committed')

    def test_unaccounted_future_request_is_not_replayed(self):
        _, state = fixture()
        state['batch_requests'] = 1
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.artifact(root, 1, proposal=self.query(), feedback=self.facts())
            self.artifact(root, 2, proposal={'action': 'pause', 'reason': 'future'},
                          feedback={'status': 'paused', 'reason': 'future'})
            restored = planner.restore_batch_memory(root, state)
        self.assertEqual([r['request_number'] for r in restored['batch_history']], [1])
        self.assertNotEqual(restored['status'], 'paused')
        self.assertEqual(restored['batch_requests'], 1)

    def test_target_failure_survives_other_target_and_more_than_twelve_queries(self):
        package, state = fixture()
        state = planner.apply_feedback(state,
            {'action': 'submit_batch', 'proofs': {'contract': 'by simp'}},
            {'status': 'rejected_batch', 'check': {'diagnostic': 'MISSING_LOCALE_INSTANCE'}}, 1)
        state = planner.apply_feedback(state,
            {'action': 'submit_batch', 'proofs': {'helper': 'by blast'}},
            {'status': 'rejected_batch', 'check': {'diagnostic': 'OTHER_TARGET_FAILURE'}}, 2)
        for number in range(3, 17):
            state = planner.apply_feedback(state, self.query('helper'), self.facts(), number)
        view = planner.planner_input(package, state)
        memory = view['target_memory']['contract']
        self.assertEqual(memory['request_number'], 1)
        self.assertIn('MISSING_LOCALE_INSTANCE', json.dumps(memory))
        self.assertIn('OTHER_TARGET_FAILURE', json.dumps(view['target_memory']['helper']))
        self.assertNotIn(1, [a['request_number'] for a in view['recent_actions']])
        self.assertIn('progress_signals', view)
        self.assertIn('query_memory', view)


if __name__ == '__main__':
    unittest.main()
