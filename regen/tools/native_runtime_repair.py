"""Reviewed latest-state native repair, preserving model, limits and historical costs."""
import argparse
from pathlib import Path

from regen.execution.proof_commits import digest, durable
from regen.infrastructure.incremental_log import read_artifact, logged_copy_batch
from regen.tools.parallel_repair_continuation import (
    _implementation_repair_review, native_watchdog_repair_experiment,
)


def review(source, application, manifest, *, authorization):
    return {**_implementation_repair_review(source, application, manifest,
            authorization=authorization), 'policy':'native-stage-runtime-repair-v1',
            'native_timing_policy':'native-stages-and-timeout-exclusion-v1'}


def prepare(source, destination, manifest, *, authorization, implementation_review, run_id=None):
    from regen.execution.proof_session_runner import acquire_run_mutation_lock, release_run_mutation_lock
    from regen.execution.repository_execution import fork_workspace_continuation
    from regen.execution.check_tasks import TERMINAL
    from regen.infrastructure.parallel_generation_budget import carry_parallel_budget, review_parallel_budget_clock
    from regen.tools import parallel_workspace_service as service
    source,destination,manifest=map(lambda p:Path(p).resolve(),(source,destination,manifest))
    application=Path(__file__).resolve().parents[2]
    if destination.exists() or source==destination or destination.is_relative_to(source):
        raise ValueError('native runtime repair needs a fresh separate continuation directory')
    expected=review(source,application,manifest,authorization=authorization)
    supplied=read_artifact(Path(implementation_review).resolve())
    if supplied!=expected:
        raise ValueError('native runtime implementation review differs')
    observed=service.status(source)
    if observed['running'] or (not observed['identity_verified'] and observed['status'] not in {'previous_boot','not_loaded'}):
        raise ValueError('source service must have verifiably exited')
    experiment=native_watchdog_repair_experiment(source,destination,application,authorization)
    experiment.pop('native_watchdog_repair_policy',None)
    lock=acquire_run_mutation_lock(source,operation='native-runtime-repair-continuation')
    try:
        for path in (source/'check-tasks').glob('*/task.json'):
            if read_artifact(path).get('status') not in TERMINAL:
                raise ValueError('source native task remains unsettled: '+path.parent.name)
        clock=review_parallel_budget_clock(source)
        if review(source,application,manifest,authorization=authorization)!=supplied:
            raise ValueError('native runtime implementation changed during preflight')
        policy=read_artifact(source/'parallel-workspace-policy.json')
        with logged_copy_batch():
            run=fork_workspace_continuation(source,destination,run_id=run_id)
        if digest(run['implementation'])!=supplied['target_implementation_sha256']:
            raise ValueError('forked native implementation differs from review')
        budget=carry_parallel_budget(source,destination,authorization=authorization,
            clock_review=clock['clock_review'],native_runtime_review=supplied)
        durable(destination/'harness-repair-execution-policy.json',policy['execution_policy'],immutable=True)
        durable(destination/'harness-repair-implementation-review.json',supplied,immutable=True)
        durable(destination/'native-runtime-policy.json',{
            'policy':supplied['native_timing_policy'], 'authorization':authorization,
            'source_run':str(source),'historical_costs_reset':False,
            'historical_timeout_refund_seconds':0,'ordinary_candidate_limits_unchanged':True,
            'prefix_preparation_excluded_even_during_messages':True,
            'timeout_credit':'only previously charged non-model native contributors',
            'progress_authority':'diagnostic only'},immutable=True)
        experiment.update(source_run=str(source),native_runtime_repair_policy=supplied['policy'],
            implementation_review_sha256=digest(supplied),
            application_manifest_sha256=supplied['target_application_manifest_sha256'])
        durable(destination/'experiment.json',experiment,immutable=True)
        receipt=dict(state='prepared',service_started=False,authorization=authorization,
            source_run=str(source),run_dir=str(destination),budget=budget,
            implementation_review_sha256=digest(supplied), mathematical_proof_edits=False,
            historical_costs_reset=False,planning_repeated=False,limits_changed=False)
        durable(destination/'harness-repair-prepared.json',receipt,immutable=True)
        return receipt
    finally:
        release_run_mutation_lock(lock)


def main():
    import json
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation',choices=['review','prepare'])
    for name in ['source','manifest','output']:p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--authorization',required=True);p.add_argument('--implementation-review',type=Path)
    p.add_argument('--run-id')
    a=p.parse_args()
    if a.operation=='review':
        result=review(a.source,Path(__file__).resolve().parents[2],a.manifest,authorization=a.authorization)
        durable(a.output,result,immutable=True)
    else:
        if a.implementation_review is None:p.error('prepare requires --implementation-review')
        result=prepare(a.source,a.output,a.manifest,authorization=a.authorization,
                       implementation_review=a.implementation_review,run_id=a.run_id)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
