"""Native readiness checks with synthetic controls, never benchmark proofs."""
import argparse
import json
import time
from pathlib import Path

from regen.preparation.intervaltree_dataset import save
from regen.preparation.checker_context import load_context
from regen.infrastructure.resource_budget import clone_heaps
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.check_executor import NativeResources, CURRENT_RESOURCES
from regen.execution.contract_refutation import check_export_scan, check_refutation
from regen.execution.result_scope import expected_negative_status, resolve_result_scope
from regen.planning.workspace_preflight import native_checker
from regen.planning.proof_graph import build_graph
from regen.planning.batch_file_planner import seal_theory


def run(root):
    root=Path(root).resolve();output=root/('native-acceptance-'+str(time.time_ns()));output.mkdir(exist_ok=False)
    dataset=root/'dataset';context=load_context(dataset)
    outcome_policy=dict(result_scope=context.get('result_scope'),
        contract_outcome_policy='contract-dispositions-v1')
    scope=resolve_result_scope(outcome_policy)
    payload=json.loads((dataset/'repository-planning-input.json').read_text())
    resources=NativeResources(1,4,1,cpus=4,memory_mb=16384,build_ml_heap_mb=8192)
    token=CURRENT_RESOURCES.set(resources)
    checker=None
    try:
        proposal=dict(schema_version=6,theories=[dict(name='Acceptance_Declarations',
            contract_ids=[c['contract_id'] for c in payload['contracts']],required_imports=[])])
        declared=native_checker(dataset,900,threads=4)(payload,proposal,output/'declarations')
        save(output/'declarations.json',declared)
        if not declared['valid']:raise RuntimeError('fixed declaration check failed')
        clone_heaps(context['heaps'],output/'heaps')
        checker=IsabelleChecker(output,output/'checks',image=context['image'],architecture=context['architecture'],
            timeout=300,generated_dir=output/'generated',heaps_dir=output/'heaps',l4v_dir=context['l4v'],
            semantic_workspace=context['semantic_workspace'],base_session=context['base_session'],
            base_import=context['base_import'],base_catalog=context['base_catalog'],isabelle_dir=context['isabelle'])
        exported=check_export_scan(checker,payload['contracts'],output/'export-scan',result_scope=scope)
        save(output/'export-scan.json',exported)
        if not exported['passed']:raise RuntimeError('fixed contextual export failed')
        false=dict(contract_id='control-false',name='control_false',kind='lemma',
            exact_statement='lemma control_false: "False"',contract_owned_context=[])
        graph=build_graph(dict(theory='Acceptance_Negative',file_slot='Acceptance_Negative.thy',contracts=[false]),
            {},run_id='synthetic-acceptance',revision=0)
        rejected=checker.check_node(graph,{'control-false':'by simp'},'control-false')
        save(output/'false-positive.json',rejected)
        if rejected['verdict']!='red':raise RuntimeError('false positive control not rejected')
        refuted=check_refutation(checker,false,'by simp',output/'refutation',result_scope=scope)
        save(output/'refutation.json',refuted)
        if refuted['status']!=expected_negative_status(outcome_policy):raise RuntimeError('negative certificate control failed')
        # Complete check, audit, build and seal in all exact owned context forms.
        contexts=[]
        for c in payload['contracts']:
            if c['contract_owned_context'] not in contexts:contexts.append(c['contract_owned_context'])
        controls=[dict(contract_id='control-'+str(i),name='control_'+str(i),kind='lemma',
            exact_statement='lemma control_'+str(i)+': "'+('True','Suc (0::nat) = 1','(2::nat) + 2 = 4')[i]+'"',contract_owned_context=owned)
            for i,owned in enumerate(contexts)]
        package=dict(theory='Acceptance_Seal',file_slot='Acceptance_Seal.thy',contracts=controls)
        graph=build_graph(package,{},run_id='synthetic-acceptance',revision=0)
        proofs={c['contract_id']:'by simp' for c in controls}
        state=dict(graph=graph,proofs=proofs,accepted_order=list(proofs),status='generating')
        sealed,evidence=seal_theory(output,dict(premise_snapshot=str(context['premise_audit_context_path'])),
            package,output,checker,[],state)
        save(output/'seal.json',evidence)
        if sealed is None:raise RuntimeError('synthetic seal failed: '+str(evidence.get('diagnostic')))
        result=dict(status='pass',contracts=len(payload['contracts']),context_forms=len(contexts),
            checker_context_id=context['checker_context_id'],result_scope=scope,
            evidence_directory=str(output.relative_to(root)),statements_only=True,
            benchmark_proofs=0,benchmark_refutations=0,model_calls=0,
            false_positive_rejected=True,formal_refutation_control=True,seal_control=True,
            exported_targets=exported['exported_targets'],background_facts_imported=0)
        if 'comparison_groups_sha256' in context:
            result['comparison_counts']=json.loads((dataset/'comparison-groups.json').read_text())['counts']
        save(root/'dataset-acceptance.json',result)
        return result
    finally:
        try:
            if checker is not None:checker.close_prefix()
        finally:
            try:resources.close()
            finally:CURRENT_RESOURCES.reset(token)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(run(args.root)))
