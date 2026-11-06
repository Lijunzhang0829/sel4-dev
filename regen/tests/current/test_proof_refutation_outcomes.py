"""Proof/refutation-only outcomes preserve pending work and proof authority.

The certificate I/O adapter is explicitly synthetic. Real Isabelle construction
and kernel audits are covered separately by test_contract_refutation and the
native smoke; these tests cover disposition and export orchestration.
"""
from copy import deepcopy
import unittest
from unittest.mock import patch

from regen.execution.check_tasks import NativePreparationError
from regen.execution.contract_refutation import UnsupportedRefutation, contract_binding
from regen.execution.partial_theory_export import build_export_scope
from regen.execution.proof_commits import digest
from regen.planning.declaration_index import initial_index, accepted_declarations
from regen.planning.theory_outcomes import (
    _publish, _snapshot, current_dispositions, current_outcome, handle_outcome,
    pending_contract_ids, record_unresolved, validate_dispositions,
)
from regen.tests.current import test_contract_dispositions as fixtures


POLICY = 'proof-or-refutation-v1'


class ProofRefutationOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ContractDispositionsTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.c.run.update(unresolved_evidence_policy=POLICY, base_import='Main')
        self.f.package['file_slot'] = 'T.thy'
        for contract in self.f.package['contracts']:
            contract['name'] = contract['contract_id']
        graph = initial_index(self.f.package, run_id=self.f.c.run['run_id'])
        self.f.state['graph'] = accepted_declarations(graph, ['a'])

    def synthetic_unknown(self):
        """Valid envelope/checksums cannot grant a forbidden UNKNOWN outcome."""
        package, _, binding = _snapshot(self.f.c)
        contract = next(c for c in package['contracts'] if c['contract_id'] == 'b')
        outcome = {'schema':'contract-disposition-v1', 'policy':'contract-dispositions-v1',
            'scope':'contract', 'status':'unknown', 'reason':'unproved is not refuted',
            'origin':'model', 'entry_ids':['b'], 'binding':binding,
            'contract_bindings':{'b':contract_binding(contract)}, 'details':{},
            'positive_proofs_added':0, 'theory_sealed':False,
            'truth_status':'undetermined', 'classification':'proof_search_exhausted',
            'metadata':{}}
        core = {'run_id':binding['run_id'], 'sequence':1, 'parent':None,
                'time_unix':0, 'kind':'contract_disposition', 'data':outcome}
        receipt = {k:core[k] for k in ('sequence','parent','time_unix','kind')}
        receipt.update(event_id=digest(core), directory=str(self.f.root/'synthetic-receipt'))
        return {**outcome, 'receipt':receipt}

    def test_model_and_controller_cannot_stop_pending_work_as_unknown(self):
        before = deepcopy(self.f.state)
        for origin in ('model', 'controller'):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                record_unresolved(self.f.c, reason='many candidates failed', origin=origin,
                                  entry_ids=['b'])
        self.assertEqual(current_dispositions(self.f.c), {})
        self.assertEqual(pending_contract_ids(self.f.c), ['b', 'c'])
        self.assertEqual(self.f.state, before)

    def test_direct_publication_cannot_bypass_the_unknown_policy(self):
        with self.assertRaises(ValueError):
            _publish(self.f.c, status='unknown', reason='bypass action validation',
                     origin='controller', details={}, entry_ids=['b'],
                     classification='no_progress')
        self.assertEqual(current_dispositions(self.f.c), {})

    def test_rehashed_unknown_receipt_rejected_when_runtime_is_known(self):
        outcome = self.synthetic_unknown()
        # The legacy semantics still support replay when no new run is supplied.
        self.assertTrue(validate_dispositions(self.f.package, self.f.state, {'b':outcome}))
        with self.assertRaises(ValueError):
            validate_dispositions(self.f.package, self.f.state, {'b':outcome},
                                  expected_binding=_snapshot(self.f.c)[2], runtime=self.f.c.run)

    def test_partial_export_cannot_smuggle_a_rehashed_unknown(self):
        self.f.state['proofs']['c'] = 'by simp'
        self.f.state['accepted_order'].append('c')
        self.f.state['graph'] = accepted_declarations(self.f.state['graph'], ['a', 'c'])
        with self.assertRaises(ValueError):
            build_export_scope(self.f.package, self.f.state, {'b':self.synthetic_unknown()},
                               runtime=self.f.c.run, expected_imports=['Main'])

    def test_negative_failure_unsupported_and_timeout_leave_contract_pending(self):
        metadata = self.f.native_controller()
        before = deepcopy(self.f.state)
        for index, status in enumerate(('not_refuted', 'unsupported', 'timeout')):
            with self.subTest(status=status), patch(
                    'regen.execution.contract_refutation.check_refutation',
                    return_value={'status':status, 'diagnostic':'synthetic unsuccessful check',
                                  'certificate':None}):
                result = handle_outcome(self.f.c, {'action':'check_refutation', 'entry_id':'b',
                    'proof_body':'by simp'}, {**metadata, 'call_id':str(index)})
            self.assertFalse(result['theory_stop'])
            self.assertEqual(result['status'], status)
            self.assertEqual(current_dispositions(self.f.c), {})
            self.assertEqual(pending_contract_ids(self.f.c), ['b', 'c'])
            self.assertNotIn('report_unresolved', result.get('notice', ''))
        self.assertEqual(self.f.state, before)

    def test_checked_negative_stops_only_its_contract_and_preserves_green(self):
        metadata = self.f.native_controller()
        certificate = self.f.certificate()
        with patch('regen.execution.contract_refutation.check_refutation',
                   return_value={'status':'refuted', 'certificate':certificate}):
            result = handle_outcome(self.f.c, {'action':'check_refutation', 'entry_id':'b',
                'proof_body':'by simp'}, metadata)
        self.assertTrue(result['contract_stop'])
        self.assertFalse(result['theory_stop'])
        self.assertEqual(result['remaining_contract_ids'], ['c'])
        self.assertIsNone(current_outcome(self.f.c))
        self.assertEqual(self.f.state['proofs'], {'a':'by simp'})
        self.assertEqual(current_dispositions(self.f.c)['b']['status'], 'refuted')

    def test_unsupported_negative_construction_does_not_start_native_work(self):
        metadata = self.f.native_controller()
        with patch('regen.execution.contract_refutation.render_refutation',
                   side_effect=UnsupportedRefutation('unsupported declaration')):
            result = handle_outcome(self.f.c, {'action':'check_refutation', 'entry_id':'b',
                'proof_body':'by simp'}, metadata)
        self.assertEqual(result['status'], 'unsupported')
        self.assertFalse(result['theory_stop'])
        self.assertNotIn('report_unresolved', result['notice'])
        self.assertEqual(self.f.c.executor.records, {})
        self.assertEqual(current_dispositions(self.f.c), {})
        self.assertEqual(pending_contract_ids(self.f.c), ['b', 'c'])

    def test_partial_export_requires_other_pending_contract_to_be_proved(self):
        outcome = self.f.refute()
        with self.assertRaisesRegex(ValueError, 'every unproved'):
            build_export_scope(self.f.package, self.f.state, {'b':outcome},
                               runtime=self.f.c.run, expected_imports=['Main'])
        # A later independent accepted proof does not mutate the refutation.
        self.f.state['proofs']['c'] = 'by simp'
        self.f.state['accepted_order'].append('c')
        self.f.state['graph'] = accepted_declarations(self.f.state['graph'], ['a', 'c'])
        scope = build_export_scope(self.f.package, self.f.state, {'b':outcome},
                                   runtime=self.f.c.run, expected_imports=['Main'])
        self.assertEqual(scope['exported_node_ids'], ['a', 'c'])
        self.assertEqual(scope['omitted_node_ids'], ['b'])
        self.assertEqual(pending_contract_ids(self.f.c), [])

    def test_positive_and_negative_for_same_contract_are_never_both_valid(self):
        outcome = self.f.refute()
        self.f.state['proofs']['b'] = 'synthetic conflicting positive'
        with self.assertRaisesRegex(ValueError, 'positive proof'):
            validate_dispositions(self.f.package, self.f.state, {'b':outcome},
                                  runtime=self.f.c.run)

    def test_tampered_negative_certificate_is_rejected_before_publication(self):
        metadata = self.f.native_controller()
        certificate = self.f.certificate()
        certificate['log'] = 'tampered certificate evidence'
        with patch('regen.execution.contract_refutation.check_refutation',
                   return_value={'status':'refuted', 'certificate':certificate}), \
                self.assertRaisesRegex(NativePreparationError, 'evidence hash mismatch'):
            handle_outcome(self.f.c, {'action':'check_refutation', 'entry_id':'b',
                'proof_body':'by simp'}, metadata)
        self.assertEqual(current_dispositions(self.f.c), {})
        self.assertEqual(pending_contract_ids(self.f.c), ['b', 'c'])

    def test_native_refutation_cannot_target_a_helper_or_already_green_contract(self):
        metadata = self.f.native_controller()
        for target in ('a', 'unregistered-helper'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                handle_outcome(self.f.c, {'action':'check_refutation', 'entry_id':target,
                                        'proof_body':'by simp'}, metadata)
        self.assertEqual(self.f.c.executor.records, {})


if __name__ == '__main__':
    unittest.main()
