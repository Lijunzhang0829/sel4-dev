"""Real Calg/Isabelle scope controls; synthetic contracts, no model or benchmark proofs."""
import argparse
import json
from pathlib import Path
import time
from uuid import uuid4

from regen.execution.contract_refutation import (OWNED_CONTEXT_POLICY, OWNED_CONTEXT_SCHEMA,
    _audit, check_export_scan, check_refutation, render_refutation, validate_refutation_certificate)
from regen.execution.check_executor import NativeResources, CURRENT_RESOURCES
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import durable
from regen.preparation.checker_context import load_context
from regen.tools.document_acceptance_validation import readonly_heaps


def target(identity,statement,context=()):
    return {'contract_id':identity,'exact_statement':statement,'contract_owned_context':list(context)}


def controls():
    return [
        target('contextual_false','lemma fixed: "False"',
            ['context fixes n :: nat assumes E: "n = 0" begin']),
        target('statement_witness','lemma fixed: fixes x :: nat assumes A: "x > 0" shows "x > 1"',
            ['context fixes n :: nat assumes E: "n = 0" begin']),
        target('inconsistent_environment','lemma fixed: "False"',
            ['context assumes impossible: "False" begin']),
        target('local_environment_definition','lemma fixed: fixes x :: nat assumes A: "x = n" shows "x > 0"',
            ['context fixes n :: nat defines n_def: "n \\<equiv> 0" begin']),
        target('local_statement_definition','lemma fixed: fixes x y :: nat defines y_def: "y \\<equiv> x + 1" assumes A: "x = 0" shows "y = 0"'),
        target('nested_context','lemma fixed: fixes z :: nat assumes A: "z = m" shows "z > 0"',
            ['context fixes n :: nat assumes E: "n = 0" begin',
             'context fixes m :: nat assumes E2: "m = n" begin']),
        target('same_assumption_label','lemma fixed: fixes x :: nat assumes hyp: "x = 1" shows "x > 1"',
            ['context fixes n :: nat assumes hyp: "n = 0" begin']),
        target('typeclass_parameter','lemma fixed: fixes x :: "\'a::order" shows "x \\<noteq> x"',
            ['context fixes u :: "\'a::order" begin']),
        target('shadowed_bound_variable','lemma fixed: "ALL x::nat. x \\<noteq> x"',
            ['context fixes x :: bool assumes E: "x" begin']),
        target('multiple_conclusions','lemma fixed: fixes x :: nat assumes A: "x = 0" shows "x = 0" and "x = 1"'),
        target('parameter_instance','lemma fixed: fixes x :: nat assumes A: "x = n" shows "x = 8"',
            ['context fixes n :: nat assumes E: "n = 7" begin']),
    ]


def run(dataset,output):
    root=Path(output).resolve();root.mkdir(parents=True,exist_ok=False)
    (root/'validation-source.py').write_bytes(Path(__file__).read_bytes())
    context=load_context(Path(dataset));start=time.monotonic()
    resources=NativeResources(1,4,1,cpus=4,memory_mb=16384)
    marker=CURRENT_RESOURCES.set(resources)
    checker=IsabelleChecker(root,root/'isabelle',image=context['image'],architecture=context['architecture'],timeout=240,
        heaps_dir=context['heaps'],l4v_dir=context['l4v'],semantic_workspace=context['semantic_workspace'],
        base_session=context['base_session'],base_import=context['base_import'],base_catalog=context['base_catalog'],
        isabelle_dir=context['isabelle'],generated_dir=root/'generated')
    command=checker._command;checker._command=lambda *a,**k:readonly_heaps(command(*a,**k))
    report={'passed':False,'model_calls':0,'benchmark_contracts_proved':0,'cases':[],
        'resource_policy':resources.policy,'checker_context_id':context['checker_context_id'],
        'scope':'synthetic owned-context obligations, kernel-audited; old benchmark results unchanged'}
    def save():
        report['seconds']=round(time.monotonic()-start,3);durable(root/'report.json',report)
    def passed(name,**fields):
        report['cases'].append({'case':name,'passed':True,**fields});save()
        print(json.dumps({'case':name,'passed':True}),flush=True)
    try:
        cases=controls()
        scan=check_export_scan(checker,cases,root/'scan',result_scope=OWNED_CONTEXT_POLICY)
        if not scan['passed']:raise AssertionError('scope export failed: '+scan['native_check'].get('diagnostic',''))
        passed('scope_export',targets=len(cases))
        label='Refutation_'+uuid4().hex;audits=root/'batch-audits';audits.mkdir()
        args=dict(theory_name=label,audit_path='/audit-output/refutation.xml',_modern=True,
                  result_scope=OWNED_CONTEXT_POLICY)
        template=render_refutation(target('template','lemma fixed: "False"'),'by auto',list(checker.imports()),**args)
        preamble=template.split('\n\nregen_refute_capture "template" ',1)[0]
        additions=[]
        # Two nested locales are test-owned definitions, not benchmark premises.
        locale_declarations=('locale scope_outer = fixes n :: nat assumes zero: "n = 0"\n'
            'locale scope_inner = scope_outer n for n + fixes m :: nat assumes same: "m = n"\n')
        batch_cases=cases+[target('nested_locale','lemma fixed: fixes x :: nat assumes A: "x = m" shows "x > 0"',
            ['context scope_inner begin'])]
        for i,case in enumerate(batch_cases):
            proof='by (auto simp: scope_inner_def scope_inner_axioms_def scope_outer_def)' if case['contract_id']=='nested_locale' else 'by auto'
            source=render_refutation(case,proof,list(checker.imports()),**args)
            if not source.startswith(preamble):raise AssertionError('batch preamble changed')
            fragment=source[len(preamble):].removesuffix('\n\nend\n')
            fragment=fragment.replace('regen_refute_exported '+json.dumps(case['contract_id'])+' refuted_contract',
                'regen_refute_exported '+json.dumps(case['contract_id'])+' checked_'+str(i))
            fragment+='\nML \\<open>File.write (Path.explode "/audit-output/'+case['contract_id']+'.xml") '+ \
                '(File.read (Path.explode "/audit-output/refutation.xml"))\\<close>\n'
            additions.append(fragment)
        witness='''
lemma parameter_environment_witness: "EX n::nat. n = 7" by auto
ML \\<open>
val witness = @{thm parameter_environment_witness};
val _ = if Thm_Deps.has_skip_proof [witness] orelse not (null (Thm_Deps.all_oracles [witness]))
  orelse not (null (Thm.hyps_of witness)) then error "unsafe parameter environment witness" else ();
val _ = File.write (Path.explode "/audit-output/parameter-instance.txt")
  (Syntax.string_of_term @{context} (Thm.full_prop_of witness));
\\<close>
'''
        source=preamble.replace('\nbegin\n','\nbegin\n'+locale_declarations,1)+''.join(additions)+witness+'\nend\n'
        result=checker._check(source,label,writable_mounts=[(audits,'/audit-output')])
        durable(root/'batch-native.json',result,immutable=True)
        if result['verdict']!='green':raise AssertionError('native scope controls failed: '+result.get('diagnostic',''))
        for case in batch_cases:
            identity=case['contract_id'];audit=_audit((audits/(identity+'.xml')).read_text(),identity,OWNED_CONTEXT_SCHEMA)
            scope=audit['scope_evidence']
            passed(identity,positive=scope['positive_obligation_text'],negative=scope['negative_obligation_text'],
                environment=scope['environment_text'],assumptions=scope['statement_assumptions_text'],
                environment_status=scope['environment_evidence_status'],skip_proof=audit['skip_proof'],oracles=audit['oracles'])
        if not (audits/'parameter-instance.txt').is_file():raise AssertionError('parameter witness missing')
        passed('parameter_environment_witness',claim=(audits/'parameter-instance.txt').read_text(),
            fixed_globals_established=False,skip_proof=False,oracles=0,residual_hypotheses=0)
        standalone=check_refutation(checker,cases[1],'by auto',root/'standalone',result_scope=OWNED_CONTEXT_POLICY)
        if standalone['status']!='context_refuted':raise AssertionError(standalone['diagnostic'])
        certificate=standalone['certificate'];validate_refutation_certificate(certificate,cases[1],checker,
            expected_result_scope=OWNED_CONTEXT_POLICY)
        for field in ('environment','statement_assumptions','local_target','positive_obligation','negative_obligation'):
            key=field+'_term_sha256'
            if scan['records'][cases[1]['contract_id']][key]!=certificate['scope_evidence'][key]:
                raise AssertionError('scan and proof obligation changed: '+key)
        passed('scan_vs_certificate_binding',certificate_id=certificate['certificate_id'])
        for name,statement,owned in [
                ('false_statement_assumption','lemma fixed: assumes A: "False" shows "False"',[]),
                ('true_under_environment','lemma fixed: "n = 0"',['context fixes n :: nat assumes E: "n = 0" begin'])]:
            value=check_refutation(checker,target(name,statement,owned),'by auto',root/name,result_scope=OWNED_CONTEXT_POLICY)
            if value['status']!='not_refuted':raise AssertionError(name+' incorrectly accepted: '+value['status'])
            passed(name,status=value['status'],certificate_published=value['certificate'] is not None)
        report['passed']=True;save();return report
    finally:
        checker.close_prefix();CURRENT_RESOURCES.reset(marker);resources.close();save()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--dataset',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();result=run(args.dataset,args.output)
    print(json.dumps({'passed':result['passed'],'cases':len(result['cases']),'seconds':result['seconds']}))
