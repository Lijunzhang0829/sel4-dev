"""Document feedback projections and bound reads; no model/native subprocesses."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest, durable
from regen.infrastructure.incremental_log import read_artifact
from regen.planning.batch_file_planner import (apply_feedback, compact_feedback,
    workspace_action_input)
from regen.planning.planner_efficiency import feedback_semantics, next_proof_observation
from regen.planning.planner_workspace import read_observation
from regen.planning.theory_document import execution_scope
from regen.tests.current.test_proof_commits import fixture


class FeedbackProjectionTests(unittest.TestCase):
    def test_virtual_source_maps_exact_theory_not_imported_file(self):
        source = 'theory Batch_Check_test\nimports Main\nbegin\nlemma p: "True"\nby fail\nend\n'
        raw = {'source': '/worker/30.thy', 'source_content': source,
               'diagnostic': 'At command "by" (line 5 of "/candidate/Batch_Check_test.thy")'}
        out = compact_feedback(raw)
        self.assertEqual(out['source_error_line'], 5)
        self.assertEqual(next(row['text'] for row in out['source_excerpt'] if row['line'] == 5), 'by fail')
        self.assertNotIn('source_content', out)
        foreign = {**raw, 'diagnostic': 'line 5 of "/candidate/Other.thy"'}
        self.assertNotIn('source_excerpt', compact_feedback(foreign))
        self.assertEqual(raw['source_content'], source)

    def test_compaction_preserves_full_history_and_excerpt_disclosure(self):
        raw = {'diagnostic': 'D' * 20000, 'native_proof_state_full': {'goals': ['P']}}
        out = compact_feedback(raw)
        self.assertEqual(len(out['diagnostic']), 12000)
        self.assertTrue(out['diagnostic_excerpt']['incomplete'])
        self.assertNotIn('native_proof_state_full', out)
        full = {'view': 'full', 'bindings': {}, 'content_id': 'hash', 'feedback': raw}
        self.assertEqual(compact_feedback(full), full)

    def test_semantics_distinguish_tool_success_and_publication(self):
        red = feedback_semantics({'status': 'rejected_batch', 'check': {'verdict': 'red'}})
        self.assertEqual((red['tool_status'], red['proof_verdict'], red['publication']),
                         ('completed', 'red', 'not_published'))
        green = {'status': 'accepted', 'node_results': [{'accepted': False, 'status': 'audited_green'}]}
        self.assertEqual(feedback_semantics(green)['publication'], 'accepted')
        self.assertFalse(green['node_results'][0]['accepted'])  # Evidence is not rewritten.
        diagnostic = feedback_semantics({'status': 'draft_checked'})
        self.assertEqual(diagnostic['publication'], 'diagnostic_only')
        self.assertEqual(feedback_semantics({'status': 'tool_failed'})['tool_status'], 'failed')

    def test_light_dynamic_input_binds_proofs_but_does_not_copy_history(self):
        package, state = fixture()
        state['batch_history'] = [{'diagnostic': 'x' * 500000}]
        payload = workspace_action_input({'run_id': 'run'}, package, state, 1)
        self.assertLess(len(json.dumps(payload)), 1000)
        self.assertFalse(payload['provider_called'])
        self.assertNotIn('batch_history', payload)
        changed = deepcopy(state)
        changed['proofs']['helper'] = 'by simp'
        self.assertNotEqual(payload['accepted_binding'],
            workspace_action_input({'run_id': 'run'}, package, changed, 1)['accepted_binding'])
        self.assertNotEqual(payload['package_sha256'],
            workspace_action_input({'run_id': 'run'}, {**package, 'changed': True}, state, 1)['package_sha256'])

    def test_direct_small_native_observation_without_full_theory_read(self):
        _, state = fixture()
        proposal = {'action': 'submit_batch', 'proofs': {'helper': 'by fail'}}
        feedback = {'status': 'rejected_batch', 'check': {'verdict': 'red',
            'diagnostic': 'Undefined method', 'native_proof_state_full': {
                'origin': 'before_failed_command', 'command': 'apply', 'line': '5',
                'goals': ['P ==> Q'], 'local_assumptions': ['H']}}}
        state = apply_feedback(state, proposal, feedback, 1)
        state['batch_requests'] = 1
        result = next_proof_observation(state, feedback=feedback)
        self.assertEqual(result['complete_native_before_command']['goals'], ['P ==> Q'])
        self.assertEqual(result['complete_native_before_command']['local_assumptions'], ['H'])
        self.assertEqual(result['native_goal_index'][0]['sha256'], digest([['H'], 'P ==> Q']))
        state['batch_requests'] = 2
        self.assertIsNone(next_proof_observation(state, feedback=feedback))

    def test_large_native_observation_only_has_exact_read_locator(self):
        state = {'batch_proof_feedback': {'request_number': 1, 'candidate': {}, 'feedback': {}}}
        raw = {'check': {'verdict': 'red', 'diagnostic': 'failure', 'native_proof_state_full': {
            'origin': 'before_failed_command', 'goals': ['P' * 20000], 'local_assumptions': []}}}
        result = next_proof_observation(state, feedback=raw)
        self.assertNotIn('complete_native_before_command', result)
        self.assertEqual(result['native_read_action']['origin'], 'before_command')
        self.assertEqual(result['native_read_action']['goal_indices'], [1])
        raw['check']['native_proof_state_full']['goals'] = ['P' * 61000]
        result = next_proof_observation(state, feedback=raw)
        self.assertEqual(result['native_read_action'],
            {'action': 'read_attempts', 'request_numbers': [1], 'view': 'full'})

    def test_scope_excludes_unreached_and_cached_work(self):
        def node(key):
            return {'id': key, 'name': key, 'role': 'helper',
                    'statement': f'lemma {key}: "True"', 'context': []}
        old = {'accepted_order': ['old'], 'proofs': {'old': 'by simp'},
               'graph': {'nodes': [node('old')]}}
        new = {'accepted_order': ['old', 'a', 'b'],
               'proofs': {'old': 'by simp', 'a': 'by simp', 'b': 'by simp'},
               'graph': {'nodes': [node(key) for key in ('old', 'a', 'b')]}}
        evidence = {'document_check_mode': 'append', 'check': {'verdict': 'red',
            'incremental': 'audited-batch-worker-v1', 'check_scope': 'committed-prefix-and-incremental-batch'},
            'node_results': [{'node_id': 'a', 'status': 'failed'}, {'node_id': 'b', 'status': 'not_reached'}]}
        scope = execution_scope(old, new, evidence)
        self.assertEqual(scope['planned_delta_ids'], ['a', 'b'])
        self.assertEqual(scope['checked_delta_ids'], ['a'])
        self.assertEqual(scope['reused_prefix_ids'], ['old'])
        self.assertTrue(scope['execution_scope_complete'])
        cached = execution_scope(old, new, {**evidence, 'cached_failure': True})
        self.assertEqual(cached['checked_delta_ids'], [])
        self.assertEqual(cached['reused_prefix_ids'], [])
        evidence['node_results'][0]['status'] = 'unknown'
        self.assertFalse(execution_scope(old, new, evidence)['execution_scope_complete'])


class NativeGoalReadTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        _, self.state = fixture()
        self.proposal = {'action': 'submit_batch', 'proofs': {'contract': 'by fail'}}
        self.feedback = {'status': 'rejected_batch', 'check': {'diagnostic': 'Undefined method: bad',
            'native_proof_state_full': {'origin': 'before_failed_command', 'command': 'apply',
                'line': '19', 'goals': ['P ==> Q'], 'local_assumptions': ['H']}}}
        self.save()

    def save(self):
        directory = self.root/'batch-requests/000001'
        durable(directory/'proposal.json', self.proposal)
        durable(directory/'feedback.json', self.feedback)
        self.state = apply_feedback(self.state, self.proposal, self.feedback, 1)

    def read(self, **kwargs):
        return read_observation(self.root, self.state, {
            'action': 'read_proof_state', 'request_number': 1, **kwargs})['proof_state']

    def test_explicit_native_selector_works_without_numbered_diagnostic(self):
        result = self.read(origin='before_command', goal_indices=[1])
        self.assertEqual(result['selected_origin'], 'before_command')
        self.assertEqual(result['native_before_command']['goals'][0]['text'], 'P ==> Q')
        self.assertEqual(result['native_before_command']['local_assumptions'], ['H'])
        with self.assertRaisesRegex(ValueError, 'diagnostic'):
            self.read(goal_indices=[1])  # Default remains historical diagnostic semantics.

    def test_selector_never_falls_back_and_rejects_invalid_origin(self):
        for origin in ('fabricated', None):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                self.read(origin=origin)
        with self.assertRaisesRegex(ValueError, 'missing'):
            self.read(origin='before_command', goal_indices=[2])
        self.feedback['check'].pop('native_proof_state_full')
        self.save()
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.read(origin='before_command', goal_indices=[1])

    def test_exact_native_read_budget_and_tamper_detection(self):
        self.feedback['check']['native_proof_state_full']['goals'] = ['x' * 61000]
        self.save()
        with self.assertRaisesRegex(ValueError, '60000'):
            self.read(origin='before_command', goal_indices=[1])
        durable(self.root/'batch-requests/000001/feedback.json', {})
        with self.assertRaises(ValueError):
            self.read(origin='before_command', goal_indices=[1])


class DynamicFeedbackTests(unittest.TestCase):
    def test_document_candidate_built_once_before_independent_commit_validation(self):
        from regen.tests.current.test_theory_document import DocumentControllerTests
        from regen.planning import theory_document
        case = DocumentControllerTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        draft = case.read()['document']
        saved = case.call({'action': 'edit_theory', 'base_sha256': draft['source_sha256'],
                          'edits': [{'old': 'oops', 'new': 'by simp'}]})
        with patch.object(theory_document, 'candidate', wraps=theory_document.candidate) as build:
            result = case.call({'action': 'check_theory', 'source_sha256': saved['source_sha256']})
        self.assertEqual(result['publication'], 'accepted')
        self.assertEqual(result['proof_verdict'], 'green')
        # One action projection and one independent authority-boundary rebuild.
        self.assertEqual(build.call_count, 2)
        self.assertEqual(result['feedback']['checked_delta_ids'], ['target_contract'])

    def test_callback_uses_small_audit_and_direct_feedback(self):
        from regen.tests.current.test_proof_workspace_agent import ProofWorkspaceAgentTest
        fixture_case = ProofWorkspaceAgentTest()
        fixture_case.setUp()
        self.addCleanup(fixture_case.doCleanups)
        controller = fixture_case.controller(theories=['Target'])
        fixture_case.local_runtime(controller)
        red = {'check': {'verdict': 'red', 'returncode': 1, 'diagnostic': 'Undefined method',
            'native_proof_state_full': {'origin': 'before_failed_command',
                'goals': ['P'], 'local_assumptions': []}},
            'node_results': [{'node_id': 'target_contract', 'status': 'failed'}]}
        with patch('regen.planning.batch_file_planner.planner_input', side_effect=AssertionError('duplicate model input')), \
             patch('regen.execution.proof_batches.check_batch', return_value=red):
            result = fixture_case.call(controller, 'Target',
                {'action': 'submit_batch', 'proofs': {'target_contract': 'by fail'}})
        self.assertEqual(result['tool_status'], 'completed')
        self.assertEqual(result['proof_verdict'], 'red')
        self.assertEqual(result['publication'], 'not_published')
        self.assertEqual(result['next_proof_observation']['complete_native_before_command']['goals'], ['P'])
        payload = read_artifact(fixture_case.root/'theories/Target/batch-requests/000001/input.json')
        self.assertEqual(payload['schema'], 'workspace-action-input-v2')
        self.assertNotIn('recent_actions', payload)
        fixture_case.llm.assert_not_called()


if __name__ == '__main__':
    unittest.main()
