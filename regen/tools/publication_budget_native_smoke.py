"""Synthetic warm-worker deadline smoke; no model or benchmark proofs."""
import argparse
from pathlib import Path
from time import monotonic
from regen.execution.batch_checker import BatchWorker
from regen.execution.check_executor import NativeResources, CURRENT_RESOURCES
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import durable
from regen.planning.publication_admission import complete_document_budget
from regen.preparation.checker_context import load_context


def run(dataset, output):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    ctx=load_context(Path(dataset))
    resources=NativeResources(1,4,1,cpus=4,memory_mb=16384)
    token=CURRENT_RESOURCES.set(resources)
    started=monotonic();checker=None;worker=None
    result={'pass':False,'model_calls':0,'benchmark_contracts_proved':0,
            'synthetic_tactic_sleep_seconds':1.0,'candidate_seconds':0.25,'whole_check_seconds':5}
    try:
        work=output/'native';work.mkdir()
        checker=IsabelleChecker(output,work,image=ctx['image'],architecture=ctx['architecture'],
            timeout=5,heaps_dir=ctx['heaps'],l4v_dir=ctx['l4v'],semantic_workspace=ctx['semantic_workspace'],
            base_session=ctx['base_session'],base_import=ctx['base_import'],base_catalog=ctx['base_catalog'],
            isabelle_dir=ctx['isabelle'],generated_dir=output/'generated')
        checker.batch_candidate_timeout=0.25
        prefix='theory Publication_Budget_Smoke imports "'+ctx['base_import']+'"\nbegin\n'
        source=prefix+'lemma delayed: True\nby (tactic \\<open>(OS.Process.sleep (Time.fromReal 1.0); all_tac)\\<close>, simp)\nend\n'
        with resources.lease(checker):
            worker=BatchWorker(checker,prefix+'\nend\n',[],checker.imports())
            first=worker.check_batch(source,['delayed'])
            with complete_document_budget(checker):
                whole=worker.check_batch(source,['delayed'])
            last=worker.check_batch(source,['delayed'])
        rows=[{k:c.get(k) for k in ('verdict','timed_out','timeout_seconds','timeout_scope','worker','wall_ms')}
              for c in (first,whole,last)]
        result.update(checks=rows,restored_candidate_seconds=checker.batch_candidate_timeout,
                      same_worker=len({c['worker'] for c in rows})==1)
        result['pass']=(first['timed_out'] and whole['verdict']=='green' and last['timed_out']
                        and result['same_worker'] and checker.batch_candidate_timeout==0.25)
    finally:
        if worker is not None:worker.close()
        if checker is not None:checker.close_prefix()
        resources.close();CURRENT_RESOURCES.reset(token)
        result['wall_seconds']=monotonic()-started
        durable(output/'report.json',result,immutable=True)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--dataset',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result=run(args.dataset,args.output);print(result)
    raise SystemExit(0 if result['pass'] else 1)
