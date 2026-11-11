import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.tools.real_contract_sketch_diagnostic import (
    ADMISSION, ACTIVE_RESERVE, DIAGNOSTIC_INSTRUCTIONS, DiagnosticTrace,
    allocation, frozen_models, initialize, native_checks, resource_gate, run, _checked_ids,
)


def payload():
    return {'benchmark': {'id': 'diagnostic', 'target_session': 'Calg',
                          'cross_experiment_inputs': []},
        'planning_input_id': 'input:diagnostic',
        'contracts': [{'contract_id': 'goal:' + str(i), 'name': f'spec_{i:04d}',
                       'exact_statement': f'lemma spec_{i:04d}: "True"',
                       'contract_owned_context': []} for i in range(1, 92)],
        'context_profiles': [{'context_profile_id': 'current-parent'}],
        'contract_relation_candidates': []}


class AllocationTests(unittest.TestCase):
    def test_complete_scope_with_two_isolated_cases_and_no_suggested_proofs(self):
        data = payload()
        proposal, selected = allocation(data)
        self.assertEqual(selected, {'0056': 'goal:56', '0066': 'goal:66'})
        self.assertEqual([len(t['contract_ids']) for t in proposal['theories']], [1, 1, 89])
        self.assertEqual({c for t in proposal['theories'] for c in t['contract_ids']},
                         {c['contract_id'] for c in data['contracts']})
        self.assertTrue(all(not t['helper_decisions'] and not t['suggested_predecessors']
                            for t in proposal['theories']))

    def test_initializer_uses_normal_complete_scope_run_initializer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dataset = root / 'dataset'
            dataset.mkdir()
            (dataset / 'repository-planning-input.json').write_text(json.dumps(payload()))
            output = root / 'new-diagnostic'
            with patch('regen.execution.repository_execution.init_run', return_value={'run_id': 'new'}) as init:
                proof, _, selected = initialize(output, dataset)
            listing = json.loads((output/'allocation/file-generation-list.json').read_text())
            self.assertEqual(listing['contract_count'], 91)
            self.assertEqual(listing['file_count'], 3)
            self.assertEqual(init.call_args.args[0], proof)
            self.assertEqual(init.call_args.args[1], dataset)
            self.assertEqual(init.call_args.kwargs['unresolved_evidence_policy'], 'proof-or-refutation-v1')
            self.assertEqual(set(selected), {'0056', '0066'})

    def test_model_config_is_low_and_environment_restored(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'REGEN_MODEL_CONFIG': 'prior'}):
            with patch('regen.infrastructure.audited_llm.model_settings', return_value={
                    'reasoning_effort': 'medium', 'role_models': {'File Planner': 'different'}}):
                with frozen_models(Path(temp)) as config:
                    self.assertEqual(config['reasoning_effort'], 'low')
                    self.assertEqual(config['role_models']['File Planner'], 'gpt-6-astra')
                    self.assertEqual(os.environ['REGEN_MODEL_CONFIG'], str(Path(temp)/'models.json'))
                self.assertEqual(os.environ['REGEN_MODEL_CONFIG'], 'prior')

    def test_prompt_has_no_historical_route_or_proof(self):
        self.assertIn('actually invoke\ncheck_sketch', DIAGNOSTIC_INSTRUCTIONS)
        for text in ('tp_release', 'sf_rep_frame', 'BN p', 'capture', 'spec_0056'):
            self.assertNotIn(text, DIAGNOSTIC_INSTRUCTIONS)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.trace = DiagnosticTrace('parent')

    def observe(self, action, result):
        result = {**result, 'task_id': result.get('task_id', 'task-' + str(len(self.trace.actions)))}
        self.trace.observe('proof_action', {'proposal': {'action': action}}, result, .1)

    def close_sketch(self, **extra):
        self.observe('check_sketch', {'groups': [{'target_entry_id': 'parent',
            'helper_ids': ['helper:h'], 'status': 'conditional_closed',
            'check': {'verdict': 'green', 'returncode': 0, 'wall_ms': 5}, **extra}]})

    def test_conditional_green_does_not_complete_or_count_as_formal(self):
        self.close_sketch()
        self.assertFalse(self.trace.complete)
        self.assertEqual(self.trace.sketch_calls, 1)
        self.observe('probe_helpers', {'checked_delta_ids': ['helper:h']})
        self.assertFalse(self.trace.complete)

    def test_actual_red_helper_is_evidence_not_helper_green(self):
        self.close_sketch()
        self.observe('check_theory', {'feedback': {'checked_delta_ids': ['helper:h'],
            'entry_results': [{'entry_id': 'helper:h', 'status': 'failed'}],
            'check': {'verdict': 'red', 'wall_ms': 6}}})
        self.assertTrue(self.trace.complete)
        self.assertEqual(self.trace.summary()['reported_native_wall_ms'], 11)
        self.assertNotIn('green', self.trace.summary())

    def test_cached_or_unreached_or_only_selected_helpers_do_not_complete(self):
        self.close_sketch()
        for feedback in [
            {'cached_failure': True, 'checked_delta_ids': ['helper:h']},
            {'entry_results': [{'entry_id': 'helper:h', 'status': 'unreached'}]},
            {'planned_delta_ids': ['helper:h'], 'requested_entry_ids': ['helper:h']},
        ]:
            self.observe('check_theory', {'feedback': feedback})
            self.assertFalse(self.trace.complete)

    def test_revised_rejected_sketch_revokes_trace_closure(self):
        self.close_sketch()
        self.observe('check_sketch', {'groups': [{'target_entry_id': 'parent',
            'helper_ids': ['helper:h'], 'status': 'sketch_rejected',
            'check': {'verdict': 'red', 'wall_ms': 4}}]})
        self.observe('check_theory', {'checked_delta_ids': ['helper:h']})
        self.assertFalse(self.trace.complete)

    def test_reused_sketch_not_charged_as_native_call(self):
        self.close_sketch(reused_sketch_check=True)
        self.assertEqual(self.trace.sketch_calls, 0)

    def test_native_feedback_copies_are_not_summed_twice(self):
        c = {'verdict': 'red', 'wall_ms': 5, 'diagnostic': 'failure'}
        self.assertEqual(len(native_checks({'check': c, 'feedback': {'check': c}})), 1)

    def test_independent_wrapper_failed_does_not_mean_proof_body_executed(self):
        self.close_sketch()
        for vocabulary in ('entry_results', 'node_results'):
            key = 'entry_id' if vocabulary == 'entry_results' else 'node_id'
            for status in ('declaration_rejected', 'not_checked', 'not_reached', 'unknown'):
                wrapped = {vocabulary: [{key: 'helper:h', 'status': 'failed',
                    'check': {'verdict': 'red', 'wall_ms': 3},
                    vocabulary: [{key: 'helper:h', 'status': status}]}]}
                self.assertEqual(_checked_ids(wrapped), set())
                self.observe('check_theory', wrapped)
                self.assertFalse(self.trace.complete)

    def test_executed_failed_leaf_below_wrapper_counts(self):
        self.close_sketch()
        self.observe('check_theory', {'entry_results': [{'entry_id': 'helper:h',
            'status': 'failed', 'check': {'verdict': 'red', 'wall_ms': 3},
            'entry_results': [{'entry_id': 'helper:h', 'status': 'failed'}]}]})
        self.assertTrue(self.trace.complete)

    def test_empty_observed_delta_does_not_fall_back_to_nested_failed_status(self):
        self.assertEqual(_checked_ids({'checked_delta_ids': [], 'entry_results': [
            {'entry_id': 'helper:h', 'status': 'failed'}]}), set())

    def test_nonexecution_or_infrastructure_results_do_not_count(self):
        leaf = {'entry_results': [{'entry_id': 'helper:h', 'status': 'failed'}]}
        for detail in ({'native_check_executed': False}, {'cached_failure': True},
                       {'failure_scope': 'native_preparation'},
                       {'check': {'failure_scope': 'trusted_audit_target'}},
                       {'check': {'cached_failure': True}}):
            self.assertEqual(_checked_ids({**leaf, **detail}), set())

    def test_reads_and_duplicate_task_do_not_charge_native_again(self):
        result = {'task_id': 'native-task', 'groups': [{'target_entry_id': 'parent',
            'helper_ids': ['helper:h'], 'status': 'conditional_closed',
            'check': {'verdict': 'green', 'wall_ms': 658}}]}
        self.observe('check_sketch', result)
        self.observe('read_sketch', result)
        self.trace.observe('read_theory', {}, result, .1)
        self.trace.observe('get_check_result', {'task_id': 'native-task'}, result, .1)
        self.observe('check_sketch', result)
        self.assertEqual(self.trace.sketch_calls, 1)
        self.assertEqual(self.trace.summary()['reported_native_wall_ms'], 658)
        self.assertEqual(sum(row['native_task_counted'] for row in self.trace.actions), 1)

    def test_reused_sketch_and_cached_failure_do_not_charge_old_time(self):
        self.close_sketch(reused_sketch_check=True)
        self.observe('check_theory', {'feedback': {'cached_failure': True,
            'check': {'verdict': 'red', 'wall_ms': 6}}})
        self.assertEqual(self.trace.summary()['reported_native_wall_ms'], 0)
        self.assertFalse(self.trace.complete)

    def test_distinct_tasks_with_equal_check_results_are_both_charged(self):
        for task in ('first', 'second'):
            self.observe('check_sketch', {'task_id': task, 'groups': [{
                'target_entry_id': 'parent', 'helper_ids': ['helper:h'],
                'status': 'sketch_rejected', 'check': {'verdict': 'red', 'wall_ms': 658}}]})
        self.assertEqual(self.trace.summary()['reported_native_wall_ms'], 1316)
        self.assertEqual(self.trace.sketch_calls, 2)

    def test_response_without_task_identity_cannot_charge_or_complete(self):
        self.close_sketch()
        self.trace.observe('proof_action', {'proposal': {'action': 'check_theory'}},
            {'checked_delta_ids': ['helper:h'], 'check': {'verdict': 'green', 'wall_ms': 9}}, .1)
        self.assertFalse(self.trace.complete)
        self.assertEqual(self.trace.summary()['reported_native_wall_ms'], 5)


class GateTests(unittest.TestCase):
    def test_no_model_optin_and_bounded_timeouts(self):
        with self.assertRaises(PermissionError):
            run('unused', 'unused')
        for options in [{'session_seconds': 1201}, {'check_timeout': 121}, {'candidate_timeout': 61}]:
            with self.assertRaises(ValueError):
                run('unused', 'unused', allow_external_call=True, **options)

    def test_resource_gate_retains_existing_thresholds(self):
        with patch('regen.tools.parallel_workspace_service._resource_admission', return_value={}) as gate:
            resource_gate(Path('/tmp'))
            self.assertEqual(gate.call_args.args[1], ADMISSION)
            resource_gate(Path('/tmp'), active=True)
            self.assertEqual(gate.call_args.args[1], ACTIVE_RESERVE)


if __name__ == '__main__':
    unittest.main()
