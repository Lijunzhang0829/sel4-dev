import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from regen.execution.proof_commits import digest, durable
from regen.planning.proof_obstacles import compare, history_view


def failure(proof='by simp', diagnostic='Failed to finish proof (line 7 of "/candidate/A.thy"):\ngoal (1 subgoal):\n 1. P x'):
    return {'run_id': 'run', 'entry': {'id': 'helper:h'}, 'proof': proof,
            'check': {'verdict': 'red', 'returncode': 1, 'diagnostic': diagnostic},
            'obstacle_context': {'entry': {'id': 'helper:h', 'statement': 'P x'},
                                 'imports': ['Base'], 'prefix': []},
            'previous_failure_id': None}


class ComparisonTests(unittest.TestCase):
    def test_changed_candidate_same_observed_obstacle(self):
        old = failure()
        new = failure('by auto', old['check']['diagnostic'].replace('line 7', 'line 20').replace('A.thy', 'B.thy'))
        result = compare(new, old)
        self.assertEqual(result['status'], 'same_observation')
        self.assertTrue(result['candidate_changed'])
        self.assertEqual(result['changed_line_spans'][0]['current_start'], 1)

    def test_changed_feedback_is_not_claimed_as_progress(self):
        old = failure(); new = failure(diagnostic=old['check']['diagnostic'].replace('P x', 'Q x'))
        result = compare(new, old)
        self.assertEqual(result['status'], 'changed_observation')
        self.assertNotIn('progress', result)

    def test_signature_only_comparison_does_not_compute_proof_diff(self):
        from unittest.mock import patch
        with patch('regen.planning.proof_obstacles._changes', side_effect=AssertionError('unexpected diff')):
            result = compare(failure('by auto'), failure(), include_changes=False)
        self.assertEqual(result['status'], 'same_observation')
        self.assertNotIn('changed_line_spans', result)

    def test_context_and_legacy_boundaries(self):
        old = failure(); new = copy.deepcopy(old)
        new['obstacle_context']['prefix'] = ['new_fact']
        self.assertEqual(compare(new, old)['reason'], 'context_changed')
        del old['obstacle_context']
        self.assertEqual(compare(new, old)['reason'], 'context_binding_unavailable')

    def test_timeout_and_infrastructure_are_not_repeated_residuals(self):
        for fields, expected in [({'returncode': 124}, 'timeout'),
                                 ({'failure_scope': 'native_preparation'}, 'infrastructure_or_scoped_failure')]:
            old = failure(); old['check'].update(fields)
            result = compare(old, old)
            self.assertEqual(result['status'], 'unknown')
            self.assertEqual(result['current_kind'], expected)

    def test_excerpt_or_incomplete_native_state_is_not_compared(self):
        for fields in [{'diagnostic_excerpt': True},
                       {'native_proof_state_full': {'origin': 'before_command', 'goals': ['P ...'], 'local_assumptions': []}}]:
            record = failure(); record['check'].update(fields)
            self.assertEqual(compare(record, record)['status'], 'unknown')

    def test_exact_variables_and_assumptions_are_retained(self):
        old = failure(); new = failure(diagnostic=old['check']['diagnostic'].replace('P x', 'P y'))
        self.assertEqual(compare(new, old)['status'], 'changed_observation')
        old['check']['native_proof_state_full'] = {'origin': 'before_command', 'goals': ['P x'], 'local_assumptions': ['A x']}
        new = copy.deepcopy(old); new['check']['native_proof_state_full']['local_assumptions'] = ['B x']
        self.assertEqual(compare(new, old)['status'], 'changed_observation')


class HistoryTests(unittest.TestCase):
    def test_linked_history_includes_repair_plan_and_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SimpleNamespace(root=Path(directory), state={'graph': {'run_id': 'run'}})
            previous = None; ids = []
            for proof in ['by simp', 'by auto', 'by blast']:
                record = failure(proof); record['previous_failure_id'] = previous
                previous = digest(record); ids.append(previous)
                durable(store.root/'workflow'/'failures'/(previous+'.json'), record, immutable=True)
            repair = {'run_id': 'run', 'classification': 'mathematical', 'plan': 'Prove a local bridge', 'failure_id': ids[1]}
            repair_id = digest(repair)
            durable(store.root/'workflow'/'repairs'/(repair_id+'.json'), repair, immutable=True)
            durable(store.root/'workflow'/'repair-index'/(ids[1]+'.json'), {'repair_id': repair_id})
            view = history_view(store, ids[-1], limit=2)
            self.assertEqual(view['observed_failure_count'], 2)
            self.assertEqual(view['same_observation_count_in_window'], 2)
            self.assertEqual(view['older_failure_id'], ids[0])
            self.assertFalse(view['history_complete'])
            self.assertEqual(view['previous_diagnosis']['plan'], 'Prove a local bridge')
            full = history_view(store, ids[-1])
            self.assertTrue(full['history_complete'])
            self.assertEqual(full['same_observation_count_in_window'], 3)

    def test_legacy_history_is_explicitly_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SimpleNamespace(root=Path(directory), state={'graph': {'run_id': 'run'}})
            record = failure(); del record['previous_failure_id']; del record['obstacle_context']
            identity = digest(record)
            durable(store.root/'workflow'/'failures'/(identity+'.json'), record, immutable=True)
            self.assertFalse(history_view(store, identity)['history_complete'])

    def test_valid_receipts_from_different_targets_cannot_form_one_history(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SimpleNamespace(root=Path(directory), state={'graph': {'run_id': 'run'}})
            older = failure(); older['entry'] = {'id': 'helper:other'}
            old_id = digest(older)
            durable(store.root/'workflow'/'failures'/(old_id+'.json'), older, immutable=True)
            latest = failure(); latest['previous_failure_id'] = old_id
            latest_id = digest(latest)
            durable(store.root/'workflow'/'failures'/(latest_id+'.json'), latest, immutable=True)
            with self.assertRaisesRegex(ValueError, 'crosses target'):
                history_view(store, latest_id)

    def test_corrupt_receipt_is_not_used_as_guidance(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SimpleNamespace(root=Path(directory), state={'graph': {'run_id': 'run'}})
            record = failure(); identity = digest(record)
            record['proof'] = 'tampered'
            durable(store.root/'workflow'/'failures'/(identity+'.json'), record, immutable=True)
            with self.assertRaisesRegex(ValueError, 'binding differs'):
                history_view(store, identity)


if __name__ == '__main__':
    unittest.main()
