"""Versioned scope/evidence bindings; these transport fixtures are not proofs."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from regen.execution.contract_refutation import (
    OWNED_CONTEXT_SCHEMA, OWNED_CONTEXT_POLICY, STRICT_GLOBAL_SCOPE,
    _audit, check_refutation, render_refutation, refutation_capability,
    validate_refutation_certificate,
)
from regen.tests.current.test_contract_refutation import contract


def native_audit(identity='fixed'):
    root = ET.Element('contract_refutation',version='3',contract_id=identity)
    for key,value in {'expected_term':'6e65676174697665','proved_term':'6e65676174697665',
            'skip_proof':'false','oracles':'0','hypotheses':'0','changed_axioms':'false',
            'exact_owned_context_negation':'true'}.items():
        ET.SubElement(root,key).text=value
    scope=ET.SubElement(root,'scope_evidence')
    for key,value in {'result_scope':OWNED_CONTEXT_POLICY,
            'environment_evidence_status':'not_established_by_negative_certificate',
            'assumption_boundary':'Expression.read_statement:ctxt-to-owned-lthy',
            'statement_parameters':'x :: nat'}.items():
        ET.SubElement(scope,key).text=value
    for name in ('environment','statement_assumptions','local_target','positive_obligation','negative_obligation'):
        text='negative' if name=='negative_obligation' else name
        ET.SubElement(scope,name+'_term').text=text.encode().hex()
        ET.SubElement(scope,name+'_text').text=text
    ET.SubElement(root,'dependencies')
    return ET.tostring(root,encoding='unicode')


class AuditTransport:
    refutation_local_io=True
    def __init__(self,root):self.root=root
    def imports(self):return ['Main']
    def _check(self,source,label,*,writable_mounts):
        p=self.root/(label+'.thy');p.write_text(source)
        log=p.with_suffix('.log');log.write_text('synthetic transport fixture, not Isabelle')
        (writable_mounts[0][0]/'refutation.xml').write_text(native_audit())
        return {'source':str(p),'log':str(log),'returncode':0,'verdict':'green'}


class OwnedContextRefutationTests(unittest.TestCase):
    def test_legacy_render_default_is_unchanged_and_new_scope_is_explicit(self):
        target=contract(context=['context assumes E: "True" begin'])
        args=dict(theory_name='Refutation_'+'a'*32,audit_path='/audit-output/refutation.xml')
        old=render_refutation(target,'by simp',['Main'],**args)
        explicit=render_refutation(target,'by simp',['Main'],result_scope=STRICT_GLOBAL_SCOPE,**args)
        self.assertEqual(old,explicit)
        self.assertIn('exact_closed_negation',old)
        self.assertNotIn('exact_owned_context_negation',old)
        scoped=render_refutation(target,'by simp',['Main'],result_scope=OWNED_CONTEXT_POLICY,**args)
        self.assertIn('exact_owned_context_negation',scoped)
        self.assertIn('Assumption.local_assms_of ctxt lthy',scoped)
        self.assertIn('Assumption.local_assms_of lthy outer',scoped)
        self.assertIn('end\n\nregen_refute_exported',scoped)

    def test_capability_does_not_claim_environment_truth_or_native_elaboration(self):
        value=refutation_capability(contract(),result_scope=OWNED_CONTEXT_POLICY)
        self.assertEqual(value['native_obligation_status'],'requires_native_elaboration')
        self.assertEqual(value['environment_evidence_status'],'not_established_by_negative_certificate')
        self.assertEqual(value['certificate_schema'],OWNED_CONTEXT_SCHEMA)
        with self.assertRaises(ValueError):refutation_capability(contract(),result_scope='auto')

    def test_owned_kernel_audit_rejects_unsafe_missing_and_wrong_scope(self):
        raw=native_audit()
        audit=_audit(raw,'fixed',OWNED_CONTEXT_SCHEMA)
        self.assertEqual(audit['scope_evidence']['result_scope'],OWNED_CONTEXT_POLICY)
        for bad in (raw.replace('<oracles>0','<oracles>1'),
                    raw.replace('<hypotheses>0','<hypotheses>1'),
                    raw.replace('not_established_by_negative_certificate','established'),
                    raw.replace('Expression.read_statement:ctxt-to-owned-lthy','all-assumptions'),
                    raw.replace('<expected_term>6e65676174697665','<expected_term>00')):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                _audit(bad,'fixed',OWNED_CONTEXT_SCHEMA)
        with self.assertRaises(ValueError):_audit(raw,'fixed')

    def test_certificate_has_distinct_status_and_cannot_cross_frozen_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); checker=AuditTransport(root); target=contract()
            result=check_refutation(checker,target,'by simp',root/'negative',result_scope=OWNED_CONTEXT_POLICY)
            self.assertEqual(result['status'],'context_refuted')
            certificate=result['certificate']
            self.assertTrue(validate_refutation_certificate(certificate,target,checker,
                expected_result_scope=OWNED_CONTEXT_POLICY))
            with self.assertRaisesRegex(ValueError,'frozen result scope'):
                validate_refutation_certificate(certificate,target,checker,expected_result_scope=STRICT_GLOBAL_SCOPE)
            self.assertEqual(certificate['scope_evidence'],certificate['audit']['scope_evidence'])
            for key,value in [('status','refuted'),('result_scope',STRICT_GLOBAL_SCOPE),
                              ('scope_evidence',{})]:
                bad=deepcopy(certificate);bad[key]=value
                with self.subTest(key=key),self.assertRaises(ValueError):
                    validate_refutation_certificate(bad,target,checker)
            changed=contract(context=['context assumes E: "False" begin'])
            with self.assertRaisesRegex(ValueError,'fixed contract binding'):
                validate_refutation_certificate(certificate,changed,checker)
            with self.assertRaisesRegex(ValueError,'import binding'):
                validate_refutation_certificate(certificate,target,expected_imports=['Other'])
            checker.checker_context_id='different-frozen-semantic-environment'
            with self.assertRaisesRegex(ValueError,'checker binding'):
                validate_refutation_certificate(certificate,target,checker)


if __name__=='__main__':unittest.main()
