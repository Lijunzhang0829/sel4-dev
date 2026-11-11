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
    refutation_capability,
    check_export_scan, LEGACY_SCHEMA, LEGACY_SUPPORT,
)


def contract(statement='lemma fixed: "False"', context=None):
    return {'contract_id':'fixed', 'exact_statement':statement,
            'contract_owned_context':context or []}


class RefutationTests(unittest.TestCase):
    def test_large_scope_record_does_not_hide_real_failure_or_change_raw_evidence(self):
        import hashlib
        from regen.execution.contract_refutation import OWNED_CONTEXT_POLICY
        raw = ('Running Draft ...\nREGEN_REFUTATION_SCOPE 6669786564 ' + 'ab' * 300000 +
               '\n*** Failed to finish proof (line 193):\n*** goal (1 subgoal):\n'
               '***  1. external_storage s slot\n*** At command "by"\n')
        class Red:
            def imports(self): return ['Main']
            def _check(self, text, label, **kw):
                source = self.root / (label + '.thy'); source.write_text(text)
                log = source.with_suffix('.log'); log.write_text(raw)
                return {'returncode':1, 'verdict':'red', 'source':str(source),
                        'log':str(log), 'diagnostic':raw[-12000:]}
        with tempfile.TemporaryDirectory() as directory:
            checker = Red(); checker.root = Path(directory)
            result = check_refutation(checker, contract(), 'by fail', checker.root/'negative',
                                      result_scope=OWNED_CONTEXT_POLICY)
            self.assertEqual(result['status'], 'not_refuted')
            self.assertIsNone(result['certificate'])
            self.assertTrue(result['diagnostic'].startswith('*** Failed to finish proof'))
            self.assertIn('external_storage s slot', result['diagnostic'])
            self.assertLess(len(result['diagnostic']), 300)
            self.assertEqual(result['scope_records_omitted'], 1)
            self.assertFalse(result['diagnostic_truncated'])
            self.assertEqual(result['native_check']['diagnostic'], raw[-12000:])
            self.assertEqual(Path(result['native_check']['log']).read_text(), raw)
            self.assertEqual(result['log_sha256'], hashlib.sha256(raw.encode()).hexdigest())
            self.assertEqual(json.loads((checker.root/'negative/result.json').read_text()), result)

    def test_refutation_error_rendering_preserves_errors_fallback_and_truncation(self):
        from regen.execution.contract_refutation import _failure_diagnostic
        text = ('REGEN_REFUTATION_SCOPE 01 abcd\n'
                '*** REGEN_REFUTATION_UNSUPPORTED: unsupported statement\n'
                '*** ordinary diagnostic quoting REGEN_REFUTATION_SCOPE is retained\n'
                'REGEN_REFUTATION_SCOPE 01 ef01')
        result = _failure_diagnostic({}, text)
        self.assertEqual(result['scope_records_omitted'], 2)
        self.assertIn('REGEN_REFUTATION_UNSUPPORTED', result['diagnostic'])
        self.assertIn('ordinary diagnostic quoting REGEN_REFUTATION_SCOPE', result['diagnostic'])
        text = '*** Failed to apply method (line 10)\n' + 'x\n' * 10000 + '*** remaining goal: Q'
        result = _failure_diagnostic({}, text)
        self.assertTrue(result['diagnostic_truncated'])
        self.assertEqual(len(result['diagnostic']), 12000)
        self.assertTrue(result['diagnostic'].startswith('*** Failed to apply method (line 10)'))
        self.assertTrue(result['diagnostic'].endswith('remaining goal: Q'))
        self.assertEqual(_failure_diagnostic({}, 'process timeout')['diagnostic'], 'process timeout')
        self.assertEqual(_failure_diagnostic({'diagnostic':'startup failed'}, '')['diagnostic'].strip(), 'startup failed')
        self.assertIn('process timeout', _failure_diagnostic(
            {'diagnostic':'process timeout'}, 'Running Draft ...')['diagnostic'])

    def test_refutation_capability_exposes_scope_without_claiming_a_verdict(self):
        self.assertTrue(refutation_capability(contract())['can_attempt'])
        scoped=contract(context=['context begin interpretation Arch .'])
        result=refutation_capability(scoped)
        self.assertTrue(result['can_attempt'])
        self.assertTrue(result['context_export_required'])
        self.assertEqual(result['reason'],'contextual_target_export')
        self.assertFalse(refutation_capability(contract('text "not a theorem"'))['can_attempt'])
        rendered=render_refutation(scoped,'by simp',['Main'],theory_name='Refutation_'+'a'*32,
                                   audit_path='/audit-output/refutation.xml')
        self.assertIn('end\n\nregen_refute_exported "fixed" refuted_contract\nby simp',rendered)

    def test_native_goal_uses_exact_tail_not_string_negation(self):
        c=contract('lemma q: fixes n :: nat assumes a: "n > 0" shows "n > 1"')
        text=render_refutation(c,'by auto',['Main'],theory_name='Refutation_'+'a'*32,audit_path='/audit-output/refutation.xml')
        self.assertIn('regen_refute_capture "fixed" refuted_contract: fixes n :: nat assumes a: "n > 0" shows "n > 1"',text)
        self.assertIn('Expression.read_statement elems concl lthy',text)
        self.assertIn('Logic.list_implies (premises, Logic.mk_conjunction_list goals)',text)
        self.assertIn('HOLogic.mk_not (hol_prop closed)',text)
        self.assertNotIn('sorry',text)

    def test_unsupported_declaration_fails_closed_before_native_work(self):
        class Never:
            def imports(self):return ['Main']
            def _check(self,*args,**kw):raise AssertionError('native check must not run')
        with tempfile.TemporaryDirectory() as d:
            result=check_refutation(Never(),contract('text "not a theorem"'),
                                    'by simp',Path(d)/'case')
        self.assertEqual(result['status'],'unsupported')
        self.assertIsNone(result['certificate'])

    def test_untrusted_body_commands_rejected(self):
        for proof in ('sorry','by (tactic \\<open>all_tac\\<close>)',
                      'by simp\nregen_refute "bad" other: "False" by simp',
                      'by simp\nregen_refute_capture "bad" other: "False"',
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

    def test_native_io_and_response_watchdog_are_infrastructure_not_negative_proof_red(self):
        class Failed:
            def imports(self):return ['Main']
            def _check(self,text,label,**kw):
                p=self.root/(label+'.thy');p.write_text(text)
                log=p.with_suffix('.log');log.write_text(self.feedback['diagnostic'])
                self.original={'source':str(p),'log':str(log),**self.feedback}
                return self.original
        cases=[({'verdict':'red','returncode':1,'diagnostic':'exception Io {cause=SysErr("disk full",NONE)}'},'infrastructure_error'),
               ({'verdict':'red','returncode':124,'timeout_scope':'candidate-watchdog','diagnostic':'No response'},'infrastructure_error'),
               ({'verdict':'red','returncode':1,'diagnostic':'Failed to apply proof method'},'not_refuted'),
               ({'verdict':'red','returncode':124,'timeout_scope':'candidate-execution','diagnostic':'Candidate execution timeout'},'not_refuted')]
        for feedback,status in cases:
            with self.subTest(feedback=feedback),tempfile.TemporaryDirectory() as d:
                checker=Failed();checker.root=Path(d);checker.feedback=feedback
                result=check_refutation(checker,contract(),'by fail',Path(d)/'negative')
                self.assertEqual(result['status'],status)
                self.assertIsNone(result['certificate'])
                self.assertEqual(checker.original['verdict'],'red')
                self.assertEqual(bool(result.get('framework_blocked')),status=='infrastructure_error')

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
    def contextual(self,name,statement,context,proof):
        target=contract(statement,context)
        result=check_refutation(self.checker,target,proof,self.root/name)
        if result['status']=='refuted':
            self.assertTrue(validate_refutation_certificate(result['certificate'],target,self.checker))
            self.assertEqual(result['certificate']['audit']['context_exported'],'true')
        elif result['status']=='not_refuted':
            self.assertIn('REGEN_REFUTATION_TARGET ',Path(result['native_check']['log']).read_text())
        return result

    def test_outer_assumptions_are_preserved_and_not_available_to_negative_proof(self):
        cases=[('true', 'lemma fixed: "x = 0"', ['context fixes x :: nat assumes h: "x = 0" begin'], 'not_refuted'),
               ('false', 'lemma fixed: "False"', ['context fixes x :: nat assumes h: "x = 0" begin'], 'refuted'),
               ('inconsistent', 'lemma fixed: "False"', ['context assumes impossible: "False" begin'], 'not_refuted'),
               ('nested', 'lemma fixed: "False"', ['context fixes x :: nat assumes h: "x = 0" begin',
                                                   'context fixes y :: nat assumes h2: "y = x" begin'], 'refuted')]
        for name,statement,context,status in cases:
            with self.subTest(name=name):
                result=self.contextual(name,statement,context,'by auto')
                self.assertEqual(result['status'],status,result['diagnostic'])

    def test_locale_assumptions_and_local_definitions_are_exported(self):
        (self.root/'Toy_Context.thy').write_text('theory Toy_Context imports Main begin\n'
            'locale tiny = fixes n :: nat assumes zero: "n = 0"\nend\n')
        self.checker.imports=lambda:['Toy_Context']
        for name,statement,status in [('locale_true','lemma fixed: "n = 0"','not_refuted'),
                                      ('locale_false','lemma fixed: "False"','refuted')]:
            result=self.contextual(name,statement,['context tiny begin'],'by (auto simp: tiny_def)')
            self.assertEqual(result['status'],status,result['diagnostic'])
        result=self.contextual('local_definition','lemma fixed: "n = 0"',
            ['context fixes n :: nat defines n_def: "n \\<equiv> 0" begin'],'by auto')
        self.assertEqual(result['status'],'not_refuted',result['diagnostic'])

    def test_sorted_context_parameters_keep_their_types(self):
        result=self.contextual('sorted','lemma fixed: "x \\<noteq> x"',
            ['context fixes x :: "\'a::order" begin'],'by simp')
        self.assertEqual(result['status'],'refuted',result['diagnostic'])
        self.assertIn('Orderings.order',bytes.fromhex(result['certificate']['audit']['closed_target_term']).decode())

    def test_export_scan_covers_false_targets_without_publishing_a_proof(self):
        targets=[{**contract('lemma fixed: "False"'),'contract_id':'a'},
                 {**contract('lemma fixed: "False"',
                             ['context fixes n :: nat assumes h: "n = 0" begin']), 'contract_id':'b'}]
        result=check_export_scan(self.checker,targets,self.root/'scan')
        self.assertTrue(result['passed'],result['native_check'].get('diagnostic'))
        self.assertEqual(set(result['records']),{'a','b'})
        self.assertEqual(result['benchmark_contracts_proved'],0)
        self.assertNotEqual(result['records']['a'],result['records']['b'])

    def test_legacy_context_free_certificate_remains_replayable(self):
        import hashlib
        from regen.execution.proof_commits import digest
        from regen.execution.native_check_receipt import checker_binding
        target=contract();body='by simp';label='Refutation_'+'e'*32
        audit_path=str(self.root/'legacy.xml')
        source=render_refutation(target,body,['Main'],theory_name=label,audit_path=audit_path,
                                 _schema=LEGACY_SCHEMA)
        native=self.checker._check(source,label)
        self.assertEqual(native['returncode'],0,native['diagnostic'])
        xml=Path(audit_path).read_text();log=Path(native['log']).read_text()
        sha=lambda value:hashlib.sha256(value.encode() if isinstance(value,str) else value).hexdigest()
        certificate={'schema':LEGACY_SCHEMA,'status':'refuted','contract':contract_binding(target),
            'contract_sha256':digest(contract_binding(target)),'theory_name':label,'audit_path':audit_path,
            'imports':['Main'],'checker_binding_sha256':checker_binding(self.checker),
            'support_sha256':sha(LEGACY_SUPPORT.read_bytes()),'proof_body':body,'source':source,
            'log':log,'audit_xml':xml,'returncode':0,'native_verdict':'green',
            'audit':_audit(xml,'fixed',LEGACY_SCHEMA)}
        for field in ('source','proof_body','log','audit_xml'):
            certificate[field+'_sha256']=sha(certificate[field])
        certificate['certificate_id']='refutation:'+digest(certificate)
        self.assertTrue(validate_refutation_certificate(certificate,target,self.checker))
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
