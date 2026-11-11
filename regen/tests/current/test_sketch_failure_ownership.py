"""Conditional failures must identify the obligation actually checked."""
import json
import tempfile
import unittest

from regen.planning.sketch_check import classify_conditional_failure, brief_check
from regen.planning.sketch_workflow import record_conditional_result, workflow_view
from regen.planning.theory_document import DocumentStore, parse
from regen.tests.current.test_proof_commits import fixture


class SketchFailureOwnershipTests(unittest.TestCase):
    signature = 'a' * 64
    group = {'target_entry_id': 'contract', 'helper_ids': ['helper']}

    def failed(self, phase, entry, diagnostic, command=None):
        marker = json.dumps({'signature': self.signature, 'phase': phase, 'entry_id': entry})
        check = {'verdict': 'red', 'returncode': 1, 'diagnostic': diagnostic,
                 'log_content': 'REGEN_SKETCH_PHASE ' + marker + '\n',
                 'native_proof_state_full': {'command': command} if command else None}
        return classify_conditional_failure(check, self.group, self.signature)

    def test_helper_type_error_does_not_blame_consumer(self):
        check = self.failed('helper_declaration', 'helper',
                            'Type unification failed\nAt command "lemma"')
        self.assertEqual(check['failure_stage'], 'helper_declaration')
        self.assertEqual(check['failed_entry_id'], 'helper')
        self.assertEqual(check['next_action'], 'edit_helper_statement')
        self.assertFalse(check['consumer_check_started'])
        self.assertEqual(brief_check(check)['failure_owner'], 'helper')

    def test_admission_runtime_error_is_harness_failure(self):
        check = self.failed('helper_declaration', 'helper',
                            'Cheating requires interactive or quick_and_dirty mode!', 'sorry')
        self.assertEqual(check['failure_owner'], 'harness')
        self.assertEqual(check['failure_stage'], 'helper_admission')
        self.assertEqual(check['next_action'], 'retry_after_backend_repair')
        self.assertFalse(check['consumer_check_started'])

    def test_consumer_failure_remains_a_proof_repair(self):
        check = self.failed('consumer_proof', 'contract', 'Failed to finish proof', 'by')
        self.assertEqual(check['failure_owner'], 'consumer')
        self.assertEqual(check['failed_entry_id'], 'contract')
        self.assertTrue(check['consumer_check_started'])

    def test_trusted_instrumentation_compile_error_is_not_parent_failure(self):
        check = self.failed('consumer_proof', 'contract',
                            'ML error: Value or constructor (content_of) has not been declared', 'ML_val')
        self.assertEqual(check['failure_owner'], 'harness')
        self.assertEqual(check['failure_stage'], 'consumer_instrumentation')
        record_conditional_result(self.store, self.nodes, self.proofs, 'contract', check, 'compile', False)
        self.assertEqual(workflow_view(self.store)['pending_failures'], [])

    def test_invalid_fact_expression_is_consumer_repair_not_backend_failure(self):
        check = self.failed('consumer_proof', 'contract', 'Undefined fact: helper(2)', 'ML_val')
        self.assertEqual(check['failure_owner'], 'consumer')
        self.assertEqual(check['next_action'], 'edit_consumer_proof')

    def test_unrelated_prefix_markers_cannot_change_failure_owner(self):
        check = {'verdict': 'red', 'diagnostic': 'Failed to finish proof',
                 'log_content': 'REGEN_SKETCH_PHASE ' + json.dumps(
                     {'signature': 'b' * 64, 'phase': 'helper_declaration', 'entry_id': 'foreign'})}
        classify_conditional_failure(check, self.group, self.signature)
        self.assertEqual(check['failed_entry_id'], 'contract')

    def setUp(self):
        self.package, self.state = fixture()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = DocumentStore(self.temp.name, self.package, self.state,
                                   ['Semantic_Base.Semantic_Root'], require_sketch=True)
        source = self.store.read()['source'].replace(
            'lemma target: "True"\n(* REGEN-PROOF *)\noops',
            'lemma target: "True"\n(* REGEN-PROOF *)\nusing helper by simp')
        self.store.edit({'action': 'edit_theory', 'base_sha256': self.store.read()['source_sha256'],
                         'source': source, 'sketches': [self.group]}, {})
        entries, self.proofs = parse(source, self.package, self.store.imports)
        self.nodes = {n['id']: n for n in entries}

    def test_backend_error_does_not_create_parent_repair_requirement(self):
        check = self.failed('helper_declaration', 'helper', 'Cheating requires interactive mode!', 'sorry')
        record_conditional_result(self.store, self.nodes, self.proofs, 'contract', check, 'first', False)
        self.assertEqual(workflow_view(self.store)['pending_failures'], [])

    def test_repaired_helper_declaration_clears_only_conditional_failure(self):
        check = self.failed('helper_declaration', 'helper', 'Type unification failed', 'lemma')
        record_conditional_result(self.store, self.nodes, self.proofs, 'contract', check, 'first', False)
        self.assertEqual([r['entry_id'] for r in workflow_view(self.store)['pending_failures']], ['helper'])
        record_conditional_result(self.store, self.nodes, self.proofs, 'contract',
                                  {'verdict': 'green'}, 'second', True)
        self.assertEqual(workflow_view(self.store)['pending_failures'], [])


if __name__ == '__main__':
    unittest.main()
