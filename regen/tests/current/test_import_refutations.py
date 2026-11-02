"""Synthetic orchestration evidence only; these tests do not run Isabelle."""
from copy import deepcopy
import threading
import unittest
from unittest.mock import patch

from regen.execution.import_revisions import prepare_import_change
from regen.execution.proof_commits import digest
from regen.planning.theory_outcomes import (
    CONTRACT_POLICY, _snapshot, current_dispositions, current_outcome,
    handle_outcome, record_unresolved,
)
from regen.tests.current import test_partial_export_publication as publication_fixtures
from regen.tests.current import test_theory_outcomes as outcome_fixtures


class ImportRefutationTests(unittest.TestCase):
    def native_case(self):
        case = outcome_fixtures.TheoryOutcomesTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        metadata = case._native_controller()
        case.c.run['base_import'] = 'Semantic_Base.Semantic_Root'
        case.c.document_mode = True
        case.c._action_lock = threading.RLock()
        return case, metadata

    def test_pending_imports_apply_before_negative_check_and_preserve_submitted_binding(self):
        case, metadata = self.native_case()
        original = _snapshot(case.c)[2]
        new_checker = object()
        def apply(runtime, state, directory):
            case.state.update(context_revision=1, context_imports=['Regen_Provider'])
            return [None, None, None, None, new_checker], case.state, None
        case.c._apply_pending_imports = apply
        def native(checker, contract, proof, directory):
            self.assertIs(checker, new_checker)
            self.assertEqual(case.state['context_revision'], 1)
            return {'status': 'refuted', 'certificate': {'explicit_synthetic_fixture': True}}
        with patch('regen.execution.contract_refutation.check_refutation', side_effect=native), patch(
                'regen.execution.contract_refutation.validate_refutation_certificate', return_value=True) as validate:
            result = handle_outcome(case.c, {'action': 'check_refutation', 'entry_id': 'b',
                                            'proof_body': 'by simp'}, metadata)
            self.assertEqual(result['theory_outcome']['binding']['context_revision'], 1)
            self.assertTrue(any(len(call.args) == 3 and call.args[2] is new_checker
                                for call in validate.call_args_list))
        row = case.c.executor.get(result['task_id'])
        self.assertEqual(row['binding'], original)
        self.assertEqual(row['result']['checked_binding']['context_imports'], ['Regen_Provider'])

    def test_rejected_pending_imports_prevent_refutation_and_leave_state_unchanged(self):
        case, metadata = self.native_case()
        before = deepcopy(case.state)
        case.c._apply_pending_imports = lambda runtime, state, directory: (
            runtime, state, {'status': 'imports_rejected', 'diagnostic': 'uncertified provider'})
        with patch('regen.execution.contract_refutation.check_refutation') as native:
            result = handle_outcome(case.c, {'action': 'check_refutation', 'entry_id': 'b',
                                            'proof_body': 'by simp'}, metadata)
            native.assert_not_called()
        self.assertEqual(result['status'], 'imports_rejected')
        self.assertEqual(case.state, before)
        self.assertIsNone(current_outcome(case.c))

    def test_context_only_change_during_refutation_cannot_publish(self):
        from regen.execution.check_tasks import NativePreparationError
        case, metadata = self.native_case()
        case.state.update(context_revision=1, context_imports=['Regen_Provider'])
        case.c._apply_pending_imports = lambda runtime, state, directory: (runtime, state, None)
        def native(*args):
            case.state.update(context_revision=2, context_imports=[])
            return {'status': 'refuted', 'certificate': {'explicit_synthetic_fixture': True}}
        with patch('regen.execution.contract_refutation.check_refutation', side_effect=native), patch(
                'regen.execution.contract_refutation.validate_refutation_certificate', return_value=True):
            with self.assertRaisesRegex(NativePreparationError, 'context changed during native check'):
                handle_outcome(case.c, {'action': 'check_refutation', 'entry_id': 'b',
                                       'proof_body': 'by simp'}, metadata)
        self.assertIsNone(current_outcome(case.c))

    def test_completed_task_recovery_uses_actual_checked_context_not_submitted_context(self):
        case, metadata = self.native_case()
        submitted = _snapshot(case.c)[2]
        task_id = digest({'kind': 'refutation', 'binding': submitted, 'call': metadata})
        def execute(directory):
            case.state.update(context_revision=1, context_imports=['Regen_Provider'])
            return {'status': 'refuted', 'certificate': {'explicit_synthetic_fixture': True},
                    'checked_binding': _snapshot(case.c)[2]}
        row = case.c.executor.submit(kind='refutation', theory='T', binding=submitted,
            request={'proposal': {'action': 'check_refutation', 'entry_id': 'b', 'proof_body': 'by simp'},
                     'metadata': metadata}, owner='T', task_id=task_id, execute=execute)
        while row['status'] not in {'completed', 'cancelled', 'error'}:
            row = case.c.executor.wait([task_id], timeout=2)[0]
        self.assertEqual(row['status'], 'completed')
        with patch('regen.execution.contract_refutation.validate_refutation_certificate', return_value=True) as validate:
            outcome = current_outcome(case.c)
            self.assertEqual(outcome['binding']['context_revision'], 1)
            self.assertTrue(outcome['details']['recovered_from_completed_task'])
            self.assertTrue(any(call.kwargs.get('expected_imports') == ['Generated_Refine.Regen_Provider']
                                for call in validate.call_args_list))

    def test_unknown_disposition_survives_context_change_without_becoming_a_fact(self):
        case, _ = self.native_case()
        case.c.run['contract_outcome_policy'] = CONTRACT_POLICY
        before = record_unresolved(case.c, reason='Synthetic search stopped', origin='model', entry_ids=['b'])
        case.state.update(context_revision=1, context_imports=['Regen_Provider'])
        after = current_dispositions(case.c)['b']
        self.assertEqual(before, after)
        self.assertEqual(after['truth_status'], 'undetermined')
        self.assertNotIn('b', case.state['proofs'])

    def test_native_refutation_blocks_real_import_change_before_provider_preparation(self):
        case = publication_fixtures.PartialExportPublicationTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.refute_b()
        before = case.immutable_hashes()
        with patch('regen.execution.import_revisions._provider_bindings') as providers, patch(
                'regen.execution.repository_execution.setup') as setup:
            with self.assertRaisesRegex(ValueError, 'retain its certified environment'):
                prepare_import_change(case.root, 'Provider', ['Generated_Refine.Regen_Consumer'], 30)
            providers.assert_not_called()
            setup.assert_not_called()
            # A native request in the unchanged certified environment remains legal.
            providers.return_value = []
            prepared = prepare_import_change(case.root, 'Provider', [case.run['base_import']], 30)
            self.assertFalse(prepared['changed'])
        self.assertEqual(case.immutable_hashes(), before)

    def test_unknown_does_not_block_import_preparation(self):
        case = publication_fixtures.PartialExportPublicationTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        record_unresolved(case.controller(), reason='Synthetic search stopped', origin='model', entry_ids=['b'])
        with patch('regen.execution.import_revisions._provider_bindings', return_value=[]), patch(
                'regen.execution.repository_execution.setup') as setup:
            prepared = prepare_import_change(case.root, 'Provider', ['Generated_Refine.Regen_Consumer'], 30)
            self.assertTrue(prepared['changed'])
            setup.assert_called_once()


if __name__ == '__main__':
    unittest.main()
