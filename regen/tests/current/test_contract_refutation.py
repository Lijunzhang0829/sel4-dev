"""Fixed-goal refutation: native negation, isolation and durable binding."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from regen.execution.contract_refutation import (
    _audit, check_refutation, contract_binding, render_refutation,
    validate_refutation_certificate, UnsupportedRefutation,
)


def contract(statement='lemma fixed: "False"', context=None):
    return {'contract_id':'fixed', 'exact_statement':statement,
            'contract_owned_context':context or []}


class RefutationTests(unittest.TestCase):
    def test_native_goal_uses_exact_tail_not_string_negation(self):
        c=contract('lemma q: fixes n :: nat assumes a: "n > 0" shows "n > 1"')
        text=render_refutation(c,'by auto',['Main'],theory_name='Refutation_'+'a'*32,audit_path='/audit-output/refutation.xml')
        self.assertIn('regen_refute "fixed" refuted_contract: fixes n :: nat assumes a: "n > 0" shows "n > 1"',text)
        self.assertIn('Expression.read_statement elems concl lthy',text)
        self.assertIn('Logic.list_implies (premises, Logic.mk_conjunction_list goals)',text)
        self.assertIn('HOLogic.mk_not (hol_prop closed)',text)
        self.assertNotIn('sorry',text)

    def test_external_context_fails_closed_before_native_work(self):
        class Never:
            def imports(self):return ['Main']
            def _check(self,*args,**kw):raise AssertionError('native check must not run')
        with tempfile.TemporaryDirectory() as d:
            result=check_refutation(Never(),contract(context=['context assumes false: "False" begin']),
                                    'by simp',Path(d)/'case')
        self.assertEqual(result['status'],'unsupported')
        self.assertIsNone(result['certificate'])

    def test_untrusted_body_commands_rejected(self):
        for proof in ('sorry','by (tactic \\<open>all_tac\\<close>)',
                      'by simp\nregen_refute "bad" other: "False" by simp',
                      'by simp\naxiomatization bad :: bool'):
            with self.subTest(proof=proof),self.assertRaises(ValueError):
                render_refutation(contract(),proof,['Main'],theory_name='Refutation_'+'a'*32,audit_path='/audit-output/refutation.xml')

    def test_green_exit_without_kernel_audit_is_not_refuted(self):
        class Missing:
            def imports(self):return ['Main']
            def _check(self,text,label,**kw):
                p=self.root/(label+'.thy');p.write_text(text)
                log=p.with_suffix('.log');log.write_text('success without audit')
                return {'returncode':0,'verdict':'green','source':str(p),'log':str(log)}
        with tempfile.TemporaryDirectory() as d:
            checker=Missing();checker.root=Path(d)
            with self.assertRaisesRegex(ValueError,'lacks refutation audit'):
                check_refutation(checker,contract(),'by simp',Path(d)/'case')

    def test_unsafe_or_wrong_native_audit_rejected(self):
        raw='<contract_refutation version="1" contract_id="fixed"><expected_term>x</expected_term><proved_term>x</proved_term><skip_proof>false</skip_proof><oracles>0</oracles><hypotheses>0</hypotheses><changed_axioms>false</changed_axioms><exact_closed_negation>true</exact_closed_negation><dependencies/></contract_refutation>'
        self.assertEqual(_audit(raw,'fixed')['oracles'],'0')
        for bad in (raw.replace('<oracles>0','<oracles>1'),raw.replace('contract_id="fixed"','contract_id="other"')):
            with self.assertRaises(ValueError):_audit(bad,'fixed')


class LocalChecker:
    """Real Isabelle transport, using only generic HOL fixtures."""
    base_session='HOL'
    base_import='Main'
    previous_theories=[]
    refutation_local_io=True
    def __init__(self,root,executable):self.root,self.executable=Path(root),executable
    def imports(self):return ['Main']
    def _check(self,source,label,**kw):
        p=self.root/(label+'.thy');p.write_text(source)
        result=subprocess.run([self.executable,'process','-o','threads=2','-l','HOL','-T',str(p.with_suffix(''))],
            text=True,capture_output=True,timeout=90)
        log=p.with_suffix('.log');log.write_text(result.stdout+result.stderr)
        return {'returncode':result.returncode,'verdict':'green' if result.returncode==0 else 'red',
                'source':str(p),'log':str(log),'diagnostic':result.stdout+result.stderr}


@unittest.skipUnless(os.environ.get('REGEN_TEST_ISABELLE'), 'set REGEN_TEST_ISABELLE for real HOL checks')
class NativeRefutationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.checker=LocalChecker(self.root,os.environ['REGEN_TEST_ISABELLE'])
    def tearDown(self):self.temp.cleanup()
    def check(self,name,statement,proof):
        result=check_refutation(self.checker,contract(statement),proof,self.root/name)
        if result['status']=='refuted':
            self.assertTrue(validate_refutation_certificate(result['certificate'],contract(statement),self.checker))
        return result
    def test_false_contract_refutes_and_binds_every_input(self):
        r=self.check('false','lemma fixed: "False"','by simp');self.assertEqual(r['status'],'refuted',r['diagnostic'])
        c=r['certificate']
        for key,value in [('proof_body','by auto'),('source','changed'),('log','changed'),('imports',['Other'])]:
            bad=deepcopy(c);bad[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate_refutation_certificate(bad,contract(),self.checker)
        with self.assertRaises(ValueError):validate_refutation_certificate(c,contract('lemma fixed: "True"'),self.checker)
    def test_true_or_vacuously_true_contract_is_not_refuted(self):
        for name,statement in [('true','lemma fixed: "True"'),
                               ('vacuous','lemma fixed: assumes impossible: "False" shows "False"')]:
            r=self.check(name,statement,'by auto');self.assertEqual(r['status'],'not_refuted',r['diagnostic'])
    def test_own_assumptions_and_meta_quantifier_bindings(self):
        statement='lemma fixed: fixes P :: "bool => bool" assumes a: "!!s::bool. P s" and b: "!!s::nat. s = s" shows "False"'
        r=self.check('quantified',statement,'by auto');self.assertEqual(r['status'],'refuted',r['diagnostic'])
    def test_unsupported_obtains_is_not_a_refutation(self):
        r=self.check('obtains','lemma fixed: obtains n :: nat where "n > 0"','by auto')
        self.assertEqual(r['status'],'unsupported',r['diagnostic'])
    def test_free_variables_are_universally_closed(self):
        r=self.check('free','lemma fixed: "(n::nat) = 0"','by auto')
        self.assertEqual(r['status'],'refuted',r['diagnostic'])


if __name__=='__main__':unittest.main()
