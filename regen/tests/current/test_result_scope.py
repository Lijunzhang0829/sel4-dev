"""Frozen result scopes and environment receipts, with explicit transport fakes.

These tests establish runtime routing/integrity, not Isabelle propositions.
Real quantifier/locale checks live in owned_context_refutation_smoke.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest
from regen.execution.result_scope import (
    OWNED_CONTEXT_POLICY, environment_bundle, validate_environment_bundle,
    contract_environment_view, validate_negative_scope, dataset_result_binding,
    result_scope_binding,
)
from regen.tests.current import test_contract_dispositions as disposition_fixtures


def synthetic_scope():
    row={'result_scope':OWNED_CONTEXT_POLICY,
         'environment_evidence_status':'not_established_by_negative_certificate',
         'assumption_boundary':'Expression.read_statement:ctxt-to-owned-lthy',
         'statement_parameters':'x'}
    for name in ('environment','statement_assumptions','local_target','positive_obligation','negative_obligation'):
        raw=('synthetic-native-transport-'+name).encode()
        row[name+'_term']=raw.hex()
        row[name+'_term_sha256']=hashlib.sha256(raw).hexdigest()
        row[name+'_text']=name
    row['scope_evidence_sha256']=digest(row)
    return row


class ResultScopeTests(unittest.TestCase):
    def setUp(self):
        self.fixture=disposition_fixtures.ContractDispositionsTests();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.c=self.fixture.c;self.contracts=self.fixture.package['contracts']
        self.scan={'passed':True,'records':{c['contract_id']:synthetic_scope() for c in self.contracts}}
        from regen.execution.contract_refutation import OWNED_CONTEXT_SUPPORT
        self.scan['support_sha256']=hashlib.sha256(OWNED_CONTEXT_SUPPORT.read_bytes()).hexdigest()
        self.bundle=environment_bundle(self.contracts,self.scan,'checker:synthetic')
        path=self.fixture.root/'scope-input';path.mkdir()
        (path/'result-scope.json').write_text(json.dumps(self.bundle))
        self.c.run.update(dataset_result_binding(path,self.contracts,'checker:synthetic'),
                          checker_context_id='checker:synthetic')

    def test_scope_is_frozen_and_entry_view_does_not_claim_concrete_environment(self):
        view=contract_environment_view(self.c.run,self.contracts[1])
        self.assertEqual(view['fixed_environment_status'],'not_established')
        self.assertFalse(view['strict_global_refutation'])
        self.assertIsNone(view['parameter_instance_evidence'])
        self.assertNotIn('environment_term',view['native_obligations'])
        self.assertEqual(result_scope_binding({}),{})
        self.assertEqual(result_scope_binding(self.c.run)['environment_evidence_sha256'],self.bundle['bundle_sha256'])

    def test_rebound_target_or_environment_is_rejected_even_if_outer_hash_recomputed(self):
        for alteration in ('target','term','claim','checker'):
            with self.subTest(alteration=alteration):
                bundle=deepcopy(self.bundle);record=bundle['contracts']['b']
                if alteration=='target':record['contract_sha256']='foreign-target'
                elif alteration=='term':record['native_obligations']['environment_term']='00'
                elif alteration=='claim':record['fixed_environment_status']='proved'
                else:bundle['checker_context_id']='foreign-checker'
                bundle['bundle_sha256']=digest({k:v for k,v in bundle.items() if k!='bundle_sha256'})
                with self.assertRaises(ValueError):
                    validate_environment_bundle(bundle,self.contracts,checker_context_id='checker:synthetic')

    def test_legacy_certificate_is_not_reclassified(self):
        certificate=self.fixture.certificate()
        with self.assertRaisesRegex(ValueError,'frozen result scope'):
            validate_negative_scope(self.c.run,certificate,self.contracts[1])
        validate_negative_scope({},certificate)
        foreign={'status':'context_refuted','result_scope':OWNED_CONTEXT_POLICY,
                 'scope_evidence':synthetic_scope()}
        validate_negative_scope(self.c.run,foreign,self.contracts[1])
        foreign['scope_evidence']['environment_term_sha256']='wrong'
        with self.assertRaisesRegex(ValueError,'prepared environment'):
            validate_negative_scope(self.c.run,foreign,self.contracts[1])

    def test_scope_file_requires_exact_contract_coverage_and_checker_binding(self):
        path=self.fixture.root/'result-scope.json'
        self.assertEqual(dataset_result_binding(path.parent,self.contracts,'checker:synthetic'),{})
        path.write_text(json.dumps(self.bundle))
        self.assertEqual(dataset_result_binding(path.parent,self.contracts,'checker:synthetic')['result_scope'],OWNED_CONTEXT_POLICY)
        with self.assertRaisesRegex(ValueError,'checker identity'):
            dataset_result_binding(path.parent,self.contracts,'different')
        with self.assertRaisesRegex(ValueError,'exactly'):
            dataset_result_binding(path.parent,self.contracts[:1],'checker:synthetic')

    def test_incomplete_native_preparation_cannot_silently_revert_to_legacy_scope(self):
        root=self.fixture.root
        (root/'checker-context.json').write_text(json.dumps({'result_scope':OWNED_CONTEXT_POLICY}))
        with self.assertRaisesRegex(ValueError,'incomplete'):
            dataset_result_binding(root,self.contracts,'checker:synthetic')

    def test_reference_read_detects_changed_prepared_evidence_after_warm_read(self):
        path=self.fixture.root/'result-scope.json';path.write_text(json.dumps(self.bundle))
        run={**self.c.run,**dataset_result_binding(path.parent,self.contracts,'checker:synthetic')}
        self.assertNotIn('contracts',run['environment_evidence'])
        contract_environment_view(run,self.contracts[1])
        with self.assertRaisesRegex(ValueError,'current run checker'):
            contract_environment_view({**run,'checker_context_id':'foreign'},self.contracts[1])
        changed=deepcopy(self.bundle)
        changed['contracts']['b']['fixed_environment_status']='proved'
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError,'evidence changed'):
            contract_environment_view(run,self.contracts[1])

    def test_tool_vocabulary_states_same_environment_and_keeps_lemma_assumptions_inside_negation(self):
        from regen.planning.document_interface import tool_contracts,proof_outcome_instructions,PROOF_OR_REFUTATION_INSTRUCTIONS
        self.assertEqual(proof_outcome_instructions({}),PROOF_OR_REFUTATION_INSTRUCTIONS)
        view=tool_contracts(run=self.c.run)
        text=view['source_action_schemas']['check_refutation']['description']
        self.assertIn('E implies NOT (ALL x. A x implies Q x)',text)
        self.assertIn('context_refuted',text)
        self.assertIn('does not establish E',text)
        self.assertEqual(view['result_scope'],OWNED_CONTEXT_POLICY)

    def test_context_disposition_recovers_without_becoming_positive_fact(self):
        from regen.planning.theory_outcomes import _publish,current_dispositions,pending_contract_ids,_disposition_feedback
        certificate={'status':'context_refuted','result_scope':OWNED_CONTEXT_POLICY,
                     'scope_evidence':synthetic_scope(),'certificate_id':'synthetic-transport',
                     'schema':'contract-refutation-owned-context-v1'}
        # Only native validation is substituted. Real ledger receipts, runtime
        # scope checks and replay remain enabled.
        with patch('regen.execution.contract_refutation.validate_refutation_certificate',return_value=True):
            result=_publish(self.c,status='context_refuted',reason='synthetic context-negative transport',
                origin='native_checker',details={'certificate':certificate},entry_ids=['b'])
            self.assertEqual(result['environment_id'],self.bundle['contracts']['b']['environment_id'])
            self.assertEqual(current_dispositions(self.c)['b']['truth_status'],'context_refuted')
            self.assertEqual(pending_contract_ids(self.c),['c'])
            self.assertNotIn('b',self.fixture.state['proofs'])
            self.assertFalse(result['theory_sealed'])
            feedback=_disposition_feedback(self.c,result)
            self.assertEqual(feedback['contract_disposition']['view'],'summary')
            self.assertNotIn('details',feedback['contract_disposition'])
            self.assertEqual(current_dispositions(self.c)['b']['details']['certificate'],certificate)
            from regen.execution.import_revisions import _guard_refuted_context
            with self.assertRaisesRegex(ValueError,'imports cannot change'):
                _guard_refuted_context(self.c.root,self.c.run,self.fixture.package,self.fixture.state)


if __name__=='__main__':unittest.main()
