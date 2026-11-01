#!/usr/bin/env python3
"""Bounded validation handoffs to the existing planner/workspace/service APIs.

This is not a generation loop. Prior repository planning is reused explicitly;
old target proofs are never imported into fresh stages. An optional controlled
unknown tests scheduler plumbing and is not a model or mathematical conclusion.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def setup(root, source):
    from regen.tools.application_release import verify
    from regen.preparation.checker_context import load_context
    verify(root/'application', root/'application-manifest.json')
    target = root/'dataset'
    target.mkdir()
    for path in (source/'dataset').iterdir():
        if path.is_file():
            shutil.copy2(path, target/path.name)
    assets = target/'checker-assets'
    assets.mkdir()
    for path in (source/'dataset/checker-assets').iterdir():
        (assets/path.name).symlink_to(path.resolve(), target_is_directory=True)
    (target/'native-evidence').symlink_to((source/'dataset/native-evidence').resolve(), target_is_directory=True)
    context = load_context(target)
    frozen = read(source/'planning-002/frozen-plan.json')
    old_listing = Path(frozen['file_generation_list'])
    plan = root/'planning-reused'
    plan.mkdir()
    for name in ['repository-plan.json', 'file-generation-list.json']:
        shutil.copy2(old_listing.parent/name, plan/name)
    shutil.copytree(old_listing.parent/'planning', plan/'planning')
    listing = read(old_listing)
    for row in listing['files']:
        path = Path(row['projection'])
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('unsafe projection path')
        (plan/path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old_listing.parent/path, plan/path)
    if sha(plan/'repository-plan.json') != frozen['plan_sha256']:
        raise ValueError('reused plan hash mismatch')
    config = root/'config'
    config.mkdir()
    models = read(source/'config/models.json')
    if models['reasoning_effort'] != 'low' or set(models['role_models'].values()) != {'gpt-6-astra'}:
        raise ValueError('unexpected source model')
    models['codex_runtime_root'] = str(root/'runtime')
    save(config/'models.json', models)
    save(config/'execution-policy.json', {'stages':[], 'session_stall_retries':0,
        'provider_feedback_retries':1, 'no_progress_turn_limit':3, 'retry_unresolved':False})
    receipt = {'source':str(source), 'dataset_sha256':sha(target/'repository-planning-input.json'),
        'checker_context_id':context['checker_context_id'], 'reused_plan_sha256':frozen['plan_sha256'],
        'repository_planning_repeated':False, 'prior_target_proofs_imported':False,
        'fixed_contracts':len(read(target/'repository-planning-input.json')['contracts']),
        'validation_only':True, 'benchmark_comparison':False}
    save(root/'preparation.json', receipt)
    return receipt


def fixture(proof):
    from regen.execution.check_executor import NativeResources, CheckExecutor
    from regen.planning.parallel_file_planner import TheoryController
    from regen.planning.theory_outcomes import record_unresolved
    resources = NativeResources(1, 2, 1, cpus=4, memory_mb=8192,
        pids_limit=1024, provider_build_timeout=1800, build_ml_heap_mb=2048)
    executor = CheckExecutor(proof/'check-tasks', workers=1)
    try:
        controller = TheoryController(proof, 'Tree_Execution', resources=resources,
            executor=executor, document_mode=True, generation_strategy_name='sketch-reviewed-v1',
            model='gpt-6-astra', call_timeout=900, check_timeout=900, candidate_timeout=300)
        outcome = record_unresolved(controller, origin='controller', classification='no_progress',
            reason='Controlled stability fixture: exercise retained stop and ready-theory scheduling',
            details={'controlled_fixture':True, 'fresh_model_conclusion':False,
                     'mathematical_claim':False})
        save(proof/'controlled-unknown-fixture.json', outcome)
    finally:
        executor.close()
        resources.close()


def start_stage(root, name):
    from regen.execution.proof_session_runner import init_run
    from regen.execution.proof_commits import durable
    from regen.tools.application_release import verify
    from regen.tools.parallel_workspace_service import start
    verify(root/'application', root/'application-manifest.json')
    if read(root/'adapter-preflight.json')['status'] != 'pass':
        raise ValueError('full adapter preflight required')
    workers, wall = (1, 480) if name == 'single' else (2, 720)
    theories = ['Tree_Execution', 'Interval_Overlap']
    if workers == 2:
        theories.append('Tree_Observations')
    proof = root/('proof-'+name)
    init_run(proof, root/'dataset', run_id=root.name+'-'+name,
        schedule_path=root/'planning-reused/file-generation-list.json', log_storage='regen-merkle-json-v1')
    fixture(proof)
    command = [sys.executable, '-u', '-m', 'regen.execution.proof_session_runner', 'parallel-workspace',
        '--run-dir',str(proof), '--planner-model','gpt-6-astra', '--file-planner-interface','document',
        '--max-parallel-theories',str(workers), '--native-slots',str(workers), '--resident-limit',str(workers),
        '--native-threads','2', '--native-cpus','4', '--native-memory-mb','8192', '--native-pids-limit','1024',
        '--build-ml-heap-mb','2048', '--provider-build-timeout','1800', '--call-timeout','900',
        '--check-timeout','900', '--candidate-timeout','300', '--generation-strategy','sketch-reviewed-v1',
        '--effective-seconds',str(workers*wall), '--wall-seconds',str(wall),
        '--execution-policy',str(root/'config/execution-policy.json'), '--allow-external-call']
    for theory in theories:
        command.extend(['--theory', theory])
    durable(proof/'experiment.json', {'schema_version':1, 'cwd':str(root/'application'), 'command':command,
        'model':'gpt-6-astra', 'reasoning_effort':'low', 'validation_only':True,
        'controlled_unknown_fixture':True, 'wall_seconds':wall, 'effective_seconds':workers*wall,
        'prior_target_proofs_imported':False, 'repository_plan_reused':True,
        'resource_requirements':{'min_free_disk_bytes':8*1024**3, 'min_available_memory_bytes':24*1024**3}}, immutable=True)
    return start(proof)


def resume_stage(root, source, destination):
    from regen.execution.proof_session_runner import acquire_run_mutation_lock, release_run_mutation_lock
    from regen.execution.repository_execution import fork_workspace_continuation
    from regen.execution.proof_commits import durable
    from regen.execution.check_tasks import TERMINAL
    from regen.infrastructure.incremental_log import read_artifact, logged_copy_batch
    from regen.infrastructure.parallel_generation_budget import carry_parallel_budget, review_parallel_budget_clock
    from regen.tools.parallel_workspace_service import status, start
    observed = status(source)
    if observed['running'] or not observed['identity_verified']:
        raise ValueError('source service must be verifiably stopped')
    authorization = 'user-authorized-intervaltree-staged-stability-validation'
    lock = acquire_run_mutation_lock(source, operation='stability-continuation')
    try:
        for path in (source/'check-tasks').glob('*/task.json'):
            if read_artifact(path)['status'] not in TERMINAL:
                raise ValueError('source task not settled: '+str(path))
        review = review_parallel_budget_clock(source)
        with logged_copy_batch():
            fork_workspace_continuation(source, destination)
        carried = carry_parallel_budget(source, destination, authorization=authorization,
            clock_review=review['clock_review'])
        experiment = read(source/'experiment.json')
        command = experiment['command']
        command[command.index('--run-dir')+1] = str(destination)
        command.extend(['--resume-authorization', authorization])
        experiment.update(source_run=str(source), continuation_authorization=authorization)
        durable(destination/'experiment.json', experiment, immutable=True)
        save(destination/'stability-continuation.json', {'source_service':observed,
            'budget':carried, 'refund_seconds':0, 'limit_extended':False})
    finally:
        release_run_mutation_lock(lock)
    return start(destination)


def inspect_stage(proof):
    # The production writer owns its journal lock within another process. An
    # immutable inode's final unlink can race this external observer's ctime
    # fingerprint. Retry the complete authoritative read; never suppress a
    # persistent mismatch or turn incomplete evidence into proof progress.
    for attempt in range(3):
        try:
            result = _inspect_stage_once(proof)
            result['observation_retries'] = attempt
            return result
        except ValueError as error:
            if str(error) != 'proof commit changed during replay' or attempt == 2:
                raise
            time.sleep(0.2)


def _inspect_stage_once(proof):
    from regen.tools.parallel_workspace_service import status
    from regen.execution.shared_proof_workspace import SharedProofWorkspace
    from regen.infrastructure.experiment_ledger import Ledger
    from regen.infrastructure.effective_time_budget import CHECKPOINT_KIND
    from regen.infrastructure.incremental_log import read_artifact
    workspace = SharedProofWorkspace(proof)
    rows = Ledger(proof/'effective-generation-budget', workspace.run['run_id']).rows
    checkpoints = [r['data']['state'] for r in rows if r['kind'] == CHECKPOINT_KIND]
    wall = [r['data'] for r in rows if r['kind'] == 'generation_wall_limit']
    theories = []
    for item in read_artifact(proof/'execution-input.json')['files']:
        package = workspace._package(item['theory'])
        snap = workspace._snapshot(item['theory'])
        state = snap['state'] if snap else {}
        fixed = {c['contract_id'] for c in package['contracts']}
        green = set(state.get('proofs', {}))
        theories.append({'theory':item['theory'], 'fixed':len(fixed),
            'fixed_green':len(fixed & green), 'helper_green':len(green-fixed),
            'status':state.get('status','not_started'), 'commit_id':snap['commit_id'] if snap else None})
    outcomes = []
    for directory in (proof/'theory-outcomes').glob('*'):
        if directory.is_dir():
            events = Ledger(directory, workspace.run['run_id']).rows
            if events:
                event = events[-1]
                outcomes.append({'theory':event['data']['binding']['theory'],
                    'status':event['data']['status'], 'reason':event['data']['reason'],
                    'event_id':event['event_id']})
    tasks = {}
    for path in (proof/'check-tasks').glob('*/task.json'):
        value = read_artifact(path)
        key = value['status']
        tasks[key] = tasks.get(key, 0) + 1
    return {'observed_unix':time.time(), 'proof':str(proof), 'service':status(proof),
        'theories':theories, 'outcomes':outcomes, 'tasks':tasks,
        'budget':checkpoints[-1] if checkpoints else None, 'wall':wall,
        'fixed_green':sum(t['fixed_green'] for t in theories),
        'helper_green':sum(t['helper_green'] for t in theories),
        'sealed':sum(t['status']=='sealed' for t in theories)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['setup', 'preflight', 'start', 'resume', 'status'])
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--stage', choices=['single', 'dual'])
    args = parser.parse_args()
    root = args.root.resolve()
    sys.path.insert(0, str(root/'application'))
    os.chdir(root/'application')
    os.environ['CODEX_HOME'] = str(root/'auth')
    os.environ['REGEN_MODEL_CONFIG'] = str(root/'config/models.json')
    if args.action == 'setup':
        result = setup(root, args.source.resolve())
    elif args.action == 'preflight':
        from project_experiment import preflight
        preflight(root)
        result = read(root/'adapter-preflight.json')
    elif args.action == 'start':
        if args.stage is None:
            parser.error('--stage required')
        result = start_stage(root, args.stage)
    elif args.action == 'resume':
        result = resume_stage(root, args.source.resolve(), args.destination.resolve())
    else:
        result = inspect_stage(args.source.resolve())
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
