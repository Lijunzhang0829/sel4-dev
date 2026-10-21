"""Real native warm-prefix/complete-check/audit/build validation on generic goals.

No model call, benchmark contract, or mutation of an existing proof run.
"""
import argparse
import json
from pathlib import Path
import shutil
import time

from regen.execution.check_executor import CheckExecutor, NativeResources, CURRENT_RESOURCES
from regen.execution.check_tasks import TERMINAL
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_batches import prepare_batch, check_batch, validate_batch_commit
from regen.execution.proof_commits import durable
from regen.execution.repository_execution import context_graph
from regen.infrastructure.progress_events import event_scope, emit_event
from regen.planning.proof_graph import build_graph
from regen.preparation.checker_context import load_context


def run(dataset, output, timeout=180):
    ctx=load_context(Path(dataset));output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=False)
    resources=NativeResources(1,2,1);executor=CheckExecutor(output/'tasks',workers=1)
    marker=CURRENT_RESOURCES.set(resources)
    try:
        def execute(directory):
            with event_scope(directory,run_id='native-generic-validation',theory='Generic_Check'):
                heaps=directory/'heaps'
                shutil.copytree(ctx['heaps'],heaps)
                for path in heaps.rglob('Generated_Refine*'):
                    if path.is_file():path.unlink()
                checker=IsabelleChecker(directory,directory/'checks',image=ctx['image'],
                    architecture=ctx['architecture'],timeout=timeout,heaps_dir=heaps,
                    l4v_dir=ctx['l4v'],semantic_workspace=ctx['semantic_workspace'],
                    base_session=ctx['base_session'],base_import=ctx['base_import'],
                    base_catalog=ctx['base_catalog'],isabelle_dir=ctx['isabelle'],
                    generated_dir=directory/'generated')
                package={'theory':'Generic_Check','file_slot':'Generic_Check.thy',
                    'repository_projection':{'nodes':[]},'contracts':[{'contract_id':'target',
                    'name':'target','exact_statement':'lemma target: "True"','contract_owned_context':[]}]}
                graph=context_graph(build_graph(package,{'generated_nodes':[{'id':'helper','name':'helper',
                    'role':'helper','statement':'lemma helper: "True & True"','context':[],
                    'dependencies':[],'strategy':''}]},run_id='native-generic',revision=1),[])
                state={'graph':graph,'proofs':{},'accepted_order':[],'rounds':{},'revisions':{},
                       'status':'planned','seal_id':None}
                phases=[]
                def phase(name,call):
                    emit_event('validation_phase_started',phase=name)
                    start=time.monotonic();value=call()
                    durable(directory/(name+'.json'),value,immutable=True)
                    phases.append({'phase':name,'wall_seconds':time.monotonic()-start})
                    emit_event('validation_phase_finished',phase=name)
                    return value
                try:
                    first=prepare_batch(package,state,{'proofs':{'helper':'by simp'}})
                    evidence=phase('batch-helper',lambda:check_batch(checker,state,first))
                    validate_batch_commit(state,first,evidence)
                    if evidence.get('incremental_fallback') or not evidence['check'].get('worker'):
                        raise RuntimeError('expected real warm checker, got fallback')
                    worker=checker._batch_worker
                    second=prepare_batch(package,first,{'graph':{'contract_dependencies':{'target':['helper']}},
                        'proofs':{'target':'using helper by simp'}})
                    evidence=phase('batch-consumer',lambda:check_batch(checker,first,second))
                    validate_batch_commit(first,second,evidence)
                    reused=checker._batch_worker is worker and worker.process.poll() is None
                    if not reused:raise RuntimeError('accepted helper prefix was not reused')
                    complete=phase('complete',lambda:checker.check_complete(second['graph'],second['proofs']))
                    if complete['verdict']!='green':raise RuntimeError('complete check failed')
                    released=worker.closed and worker.process.poll() is not None and checker._batch_worker is None
                    if not released:raise RuntimeError('hot worker survived cold complete check')
                    audit=phase('audit',lambda:checker.audit_complete_theory(second['graph'],second['proofs']))
                    if audit['verdict']!='green':raise RuntimeError('dependency audit failed')
                    name='Generic_Check'
                    (checker.generated_dir/(name+'.thy')).write_text(checker.complete_source(second['graph'],second['proofs'],theory_name=name))
                    build=phase('build',lambda:checker.build_generated_session([name]))
                    if build['verdict']!='green':raise RuntimeError('generated session build failed')
                    return {'pass':True,'warm_prefix_reused':reused,'hot_closed_before_end_of_complete':released,
                            'phases':phases,'native_operations':resources.metrics}
                finally:
                    checker.close_prefix()
        row=executor.submit(kind='proof_batch',theory='Generic_Check',
            binding={'checker_context_id':ctx['checker_context_id']},
            request={'kind':'generic-native-validation','model_calls':0},execute=execute)
        while row['status'] not in TERMINAL:
            row=executor.wait([row['task_id']],timeout=10)[0]
        report={'pass':row['status']=='completed' and row['result']['pass'],
            'model_calls':0,'benchmark_contracts_proved':0,'task':row,
            'notice':'Generic native lifecycle check; no File Planner/model or full repository success claim.'}
        durable(output/'report.json',report,immutable=True)
        return report
    finally:
        executor.close();resources.close();CURRENT_RESOURCES.reset(marker)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset-root',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--timeout',type=int,default=180);a=p.parse_args()
    result=run(a.dataset_root,a.output,a.timeout)
    print(json.dumps({'pass':result['pass'],'task_status':result['task']['status'],
        'result':result['task'].get('result'), 'diagnostic':result['task'].get('diagnostic')},indent=2))
    return 0 if result['pass'] else 1


if __name__=='__main__':raise SystemExit(main())
