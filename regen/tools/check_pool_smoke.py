"""Opt-in native concurrency smoke using generic declarations, not benchmark proofs.

No LLM call and no proof-run mutation. Every case has a separate source directory;
same theory/lemma names deliberately exercise process/context isolation.
"""
import argparse
import json
from pathlib import Path
import time

from regen.execution.check_executor import CheckExecutor, NativeResources, CURRENT_RESOURCES
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import durable
from regen.infrastructure.progress_events import event_scope
from regen.preparation.checker_context import load_context


def run(dataset, output, *, timeout=180, threads=2):
    ctx=load_context(Path(dataset)); output=Path(output)
    output.mkdir(parents=True,exist_ok=True)
    results=[]
    for slots in (1,2):
        resources=NativeResources(slots,threads,2)
        executor=CheckExecutor(output/('slots-'+str(slots)),workers=2)
        marker=CURRENT_RESOURCES.set(resources)
        started=time.monotonic()
        try:
            tasks=[]
            for kind,typ in [('proof_batch','nat'),('statement','bool')]:
                def execute(directory,kind=kind,typ=typ):
                    with event_scope(directory,run_id='native-pool-smoke',theory=typ):
                        checker=IsabelleChecker(directory,directory/'source',image=ctx['image'],
                            architecture=ctx['architecture'],timeout=timeout,heaps_dir=ctx['heaps'],
                            l4v_dir=ctx['l4v'],semantic_workspace=ctx['semantic_workspace'],
                            base_session=ctx['base_session'],base_import=ctx['base_import'],
                            base_catalog=ctx['base_catalog'],isabelle_dir=ctx['isabelle'],
                            generated_dir=directory/'generated')
                        original=checker._command
                        checker._command=lambda *a,**kw:[value+',readonly' if value.startswith('type=bind,') and value.endswith('dst=/root/.isabelle/heaps') else value for value in original(*a,**kw)]
                        source=('theory Pool_Context imports "'+ctx['base_import']+'" begin\n'
                                'context fixes x :: '+typ+' begin\nlemma same_name: "x = x"\n'+
                                ('by simp' if kind=='proof_batch' else 'oops')+'\nend\nend\n')
                        result=checker._check(source,'Pool_Context')
                        return {**result,'proofs_checked':kind=='proof_batch','fixture_type':typ}
                tasks.append(executor.submit(kind=kind,theory=typ,
                    binding={'checker_context_id':ctx['checker_context_id']},request={'fixture':'reflexivity','kind':kind},
                    execute=execute)['task_id'])
            pending=set(tasks)
            while pending:
                for row in executor.wait(sorted(pending),timeout=10):
                    if row['status'] in {'completed','error','cancelled','interrupted'}:
                        pending.remove(row['task_id'])
            rows=[executor.get(key) for key in tasks]
            results.append({'native_slots':slots,'threads':threads,'wall_seconds':time.monotonic()-started,
                            'tasks':rows,'pass':all(row['status']=='completed' and row['result']['verdict']=='green' for row in rows)})
        finally:
            executor.close();resources.close();CURRENT_RESOURCES.reset(marker)
    report={'kind':'generic-native-isolation-smoke','model_calls':0,'benchmark_contracts_proved':0,
            'checker_context_id':ctx['checker_context_id'],'cases':results,'pass':all(row['pass'] for row in results),
            'notice':'One cold-process smoke, not a proof-generation speed benchmark or memory sizing study.'}
    durable(output/'report.json',report,immutable=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--timeout',type=int,default=180)
    parser.add_argument('--threads',type=int,default=2)
    args=parser.parse_args()
    report=run(args.dataset_root,args.output,timeout=args.timeout,threads=args.threads)
    print(json.dumps({'pass':report['pass'],'cases':[{k:row[k] for k in ('native_slots','wall_seconds','pass')} for row in report['cases']]},indent=2))
    return 0 if report['pass'] else 1


if __name__=='__main__':
    raise SystemExit(main())
