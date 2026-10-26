"""Prepare a verified legacy feedback continuation; never launch the service."""
import argparse
import hashlib
import json
from pathlib import Path

from regen.execution.proof_commits import durable, digest
from regen.infrastructure.incremental_log import read_artifact
from regen.infrastructure.provider_feedback_continuation import plan_legacy_feedback_import
from regen.tools import parallel_workspace_service as service
from regen.tools.application_release import verify


def replacement_experiment(source, destination, application, authorization):
    original = read_artifact(source/'experiment.json')
    command = list(original['command'])
    if (command.count('--run-dir')!=1 or command[command.index('--run-dir')+1]!=str(source)
            or original.get('model')!='gpt-5.6-sol' or original.get('reasoning_effort')!='xhigh'
            or original.get('effective_seconds')!=172800 or original.get('include_planning') is not True
            or original.get('contract_visibility')!='owner-blind-v1'):
        raise ValueError('source experiment does not match the authorized CRefine configuration')
    command[command.index('--run-dir')+1] = str(destination)
    # The entire proof ledger already contains the one-time planning transfer.
    # Replaying the original planning claim would target the wrong run identity.
    for option in ('--planning-budget','--execution-policy','--resume-authorization'):
        if command.count(option)>1:
            raise ValueError('ambiguous source command option: '+option)
        if option in command:
            index=command.index(option);del command[index:index+2]
    if '--extend-effective-seconds' in command:
        raise ValueError('this authorized continuation does not extend the experiment budget')
    command.extend(['--execution-policy',str(destination/'provider-feedback-execution-policy.json'),
                    '--resume-authorization',authorization])
    return {**original,'command':command,'cwd':str(application),
        'historical_costs_reset':False,'planning_repeated':False,
        'provider_feedback_retry_policy':'provider-feedback-retry-v1'}


def prepare(source, destination, manifest, *, authorization):
    from regen.execution.proof_session_runner import acquire_run_mutation_lock, release_run_mutation_lock
    from regen.execution.repository_execution import fork_workspace_continuation
    from regen.execution.check_tasks import TERMINAL
    from regen.infrastructure.audited_llm import model_settings
    from regen.infrastructure.parallel_generation_budget import carry_parallel_budget
    from regen.planning.parallel_file_planner import generation_strategy
    source,destination,manifest=map(lambda p:Path(p).resolve(),(source,destination,manifest))
    application=Path(__file__).resolve().parents[2]
    if not isinstance(authorization,str) or not authorization.strip():
        raise ValueError('explicit continuation authorization required')
    if destination.exists() or source==destination or destination.is_relative_to(source):
        raise ValueError('a fresh separate continuation output is required')
    verify(application,manifest)
    settings=model_settings()
    if settings['reasoning_effort']!='xhigh' or set(settings['role_models'].values())!={'gpt-5.6-sol'}:
        raise ValueError('current model configuration differs from Sol/xhigh')
    expected=application/'regen/config/proof-models-crefine-owner-blind-sol-xhigh-local.json'
    import os
    if Path(os.environ.get('REGEN_MODEL_CONFIG','')).resolve()!=expected:
        raise ValueError('REGEN_MODEL_CONFIG must select the new frozen local profile')
    experiment=replacement_experiment(source,destination,application,authorization)
    observed=service.status(source)
    if observed['running'] or (not observed['identity_verified'] and observed['status'] not in {'previous_boot','not_loaded'}):
        raise ValueError('source service must have verifiably exited')
    lock=acquire_run_mutation_lock(source,operation='provider-feedback-continuation')
    try:
        for path in (source/'check-tasks').glob('*/task.json'):
            if read_artifact(path).get('status') not in TERMINAL:
                raise ValueError('source native task remains unsettled: '+path.parent.name)
        run=read_artifact(source/'run.json')
        dataset=Path(run['checker_context_root'])
        raw=(dataset/'repository-planning-input.json').read_bytes()
        if hashlib.sha256(raw).hexdigest()!='ed996b3328b67f63a789a737e434d44e4052453274c0a62b62ae20b8539d94f1':
            raise ValueError('authorized anonymous CRefine input changed')
        proposal=plan_legacy_feedback_import(source)
        source_policy=read_artifact(source/'parallel-workspace-policy.json')
        target_policy={**source_policy['execution_policy'],'provider_feedback_retries':1}
        fork_workspace_continuation(source,destination)
        carried=carry_parallel_budget(source,destination,authorization=authorization,
            clock_review=proposal['clock_review'],provider_feedback_review=proposal,
            target_execution_policy=target_policy,
            target_strategy=generation_strategy(source_policy['generation_strategy']['name']))
        durable(destination/'provider-feedback-execution-policy.json',target_policy,immutable=True)
        experiment.update(application_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
            source_run=str(source),provider_feedback_review_id=proposal['review_id'])
        durable(destination/'experiment.json',experiment,immutable=True)
        receipt={'state':'prepared','source_run':str(source),'run_dir':str(destination),
            'service_started':False,'source_service':observed,'budget':carried,
            'provider_feedback_review_id':proposal['review_id'],
            'experiment_sha256':digest(experiment),'application_manifest':str(manifest),
            'application_manifest_sha256':experiment['application_manifest_sha256']}
        durable(destination/'provider-feedback-prepared.json',receipt,immutable=True)
        return receipt
    finally:
        release_run_mutation_lock(lock)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--authorization',required=True)
    args=parser.parse_args()
    print(json.dumps(prepare(args.source,args.output,args.manifest,authorization=args.authorization),indent=2))


if __name__=='__main__':main()
