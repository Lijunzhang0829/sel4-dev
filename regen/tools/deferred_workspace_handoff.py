"""One-shot service handoff after named theory sessions settle.

This supervisor never runs proof actions. It waits for the existing coordinator,
uses its normal stop/cleanup path, forks verified journals with the existing
continuation API, then launches the ordinary frozen runner. No live-code edits.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import time

from regen.execution.proof_commits import durable
from regen.infrastructure.incremental_log import read_artifact
from regen.tools import parallel_workspace_service as service
from regen.tools.application_release import verify


class HandoffPaused(RuntimeError):
    pass


def check_pause(config):
    if config.get('pause_marker') and Path(config['pause_marker']).exists():
        raise HandoffPaused('experiment paused by proof-progress watchdog')


@contextmanager
def launch_interlock(config):
    if not config.get('pause_marker'):
        yield
        return
    with Path(config['pause_marker']).with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        check_pause(config)
        yield


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def latest_attempt(sessions, theory):
    base = Path(sessions)/theory
    candidates = [base, *base.glob('retry-*')]
    existing = [p for p in candidates if (p/'attempt.json').is_file()]
    return max(existing, key=lambda p: read(p/'attempt.json')['attempt']) if existing else None


def settled(sessions, theories):
    rows = {}
    for theory in theories:
        directory = latest_attempt(sessions, theory)
        row = {'settled': False}
        if directory is not None:
            row['directory'] = str(directory)
            status_path = directory/'runner-status.json'
            monitor = read(status_path) if status_path.exists() else {}
            # A seal/result alone is too early: the model/controller may still
            # own callbacks and workers. Require monitor closure as well.
            stopped = monitor.get('running') is False and bool(monitor.get('stopped_at'))
            for kind in ('result', 'failure'):
                path = directory/(kind+'.json')
                if path.exists():
                    receipt = read(path)
                    compact = {k:receipt[k] for k in ('theory', 'status', 'error_type',
                               'diagnostic', 'attempt', 'retry_scheduled') if k in receipt}
                    row.update(kind=kind, receipt=compact,
                               settled=stopped and not receipt.get('retry_scheduled', False))
                    break
        rows[theory] = row
    return all(r['settled'] for r in rows.values()), rows


def write_status(config, phase, **extra):
    value = {'phase': phase, 'observed_at': datetime.now(timezone.utc).isoformat(),
             'source_run': config['source_run'], 'destination': config['destination'], **extra}
    durable(Path(config['status_path']), value)
    print(json.dumps(value, ensure_ascii=False), flush=True)
    return value


def validate(config):
    for key in ('source_run', 'sessions', 'destination', 'application', 'manifest', 'status_path'):
        if not Path(config[key]).is_absolute():
            raise ValueError('handoff paths must be absolute: '+key)
    source, destination = Path(config['source_run']), Path(config['destination'])
    if source == destination or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError('source and destination must be disjoint')
    if not Path(config['sessions']).is_relative_to(source/'parallel-sessions'):
        raise ValueError('sessions do not belong to source')
    if not config['wait_theories'] or len(set(config['wait_theories'])) != len(config['wait_theories']):
        raise ValueError('wait theories must be nonempty and distinct')
    names = {f['theory'] for f in read(source/'execution-input.json')['files']}
    if not set(config['wait_theories']+config['priority_theories']) <= names:
        raise ValueError('unknown handoff theory')
    for filename, key in [('service-launch.json', 'source_launch_sha256'),
                          ('service-invocation.json', 'source_invocation_sha256')]:
        if digest(source/filename) != config[key]:
            raise ValueError('source service identity changed: '+filename)
    if digest(source/'experiment.json') != read(source/'service-launch.json')['experiment_sha256']:
        raise ValueError('source experiment changed')
    if digest(config['manifest']) != config['manifest_sha256']:
        raise ValueError('release manifest changed')
    verify(config['application'], config['manifest'])
    from regen.infrastructure import codex_workspace_session
    if not Path(codex_workspace_session.__file__).resolve().is_relative_to(Path(config['application']).resolve()):
        raise ValueError('handoff must execute from the verified target application')


def make_experiment(config):
    source, destination = Path(config['source_run']), Path(config['destination'])
    manifest = read(source/'experiment.json')
    command = list(manifest['command'])
    command[command.index('--run-dir')+1] = str(destination)
    policy_path = destination/'handoff-execution-policy.json'
    policy = read(source/'parallel-workspace-policy.json')['execution_policy']
    policy = {**policy, 'stages': [config['priority_theories']]}
    durable(policy_path, policy, immutable=True)
    if '--execution-policy' in command:
        command[command.index('--execution-policy')+1] = str(policy_path)
    else:
        command.extend(['--execution-policy', str(policy_path)])
    return {**manifest, 'command':command, 'cwd':config['application']}


def switch(config, evidence):
    source, destination = Path(config['source_run']), Path(config['destination'])
    validate(config)  # Recheck immediately before the first external mutation.
    check_pause(config)
    if destination.exists():
        raise ValueError('destination already exists; never overwrite/replay a partial handoff')
    observed = service.status(source)
    if not observed['identity_verified']:
        raise RuntimeError('source process identity cannot be verified')
    incidental = []
    for p in Path(config['sessions']).glob('*/runner-status.json'):
        if read(p).get('running'):
            incidental.append(p.parent.name)
    write_status(config, 'stopping_source_after_gate', gate=evidence,
                 automatically_refilled_theories=incidental)
    if observed['running']:
        observed = service.stop(source)
    if observed['running']:
        raise RuntimeError('source still running after stop')
    from regen.execution.proof_session_runner import acquire_run_mutation_lock, release_run_mutation_lock
    from regen.execution.repository_execution import fork_workspace_continuation
    token = acquire_run_mutation_lock(source, operation='deferred-handoff')
    try:
        pending = []
        for path in (source/'check-tasks').glob('*/task.json'):
            from regen.execution.check_tasks import TERMINAL
            if read_artifact(path).get('status') not in TERMINAL:
                pending.append(path.parent.name)
        if pending:
            raise RuntimeError('unsettled source checker tasks: '+str(pending))
        check_pause(config)
        write_status(config, 'forking_verified_continuation', gate=evidence)
        fork_workspace_continuation(source, destination)
        durable(destination/'experiment.json', make_experiment(config), immutable=True)
        durable(destination/'handoff-origin.json', {'source_run':str(source),
            'gate':evidence, 'priority_theories':config['priority_theories'],
            'automatically_refilled_theories':incidental,
            'policy':'preserve all verified proofs, seals, drafts and cumulative costs'}, immutable=True)
    finally:
        release_run_mutation_lock(token)
    # Preserve the existing admission requirements; wait, do not weaken them.
    requirements = read(destination/'experiment.json').get('resource_requirements', {})
    while True:
        check_pause(config)
        try:
            service._resource_admission(destination, requirements)
            break
        except RuntimeError as error:
            write_status(config, 'waiting_for_resource_admission', reason=str(error))
            time.sleep(config.get('poll_seconds', 30))
    validate(config)
    write_status(config, 'starting_continuation')
    with launch_interlock(config):
        result = service.start(destination)
    if result['status'] not in {'running', 'process_completed'}:
        raise RuntimeError('continuation failed to start: '+str(result))
    write_status(config, 'handoff_complete', target_service=result,
                 priority_theories=config['priority_theories'])
    return result


def run(config):
    validate(config)
    lock_path = Path(config['status_path']).with_suffix('.lock')
    with lock_path.open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if Path(config['destination']).exists():
            raise ValueError('destination exists; inspect previous handoff rather than replay')
        while True:
            check_pause(config)
            complete, evidence = settled(config['sessions'], config['wait_theories'])
            observed = service.status(Path(config['source_run']))
            if not observed['identity_verified']:
                raise RuntimeError('source identity changed while waiting')
            if complete:
                return switch(config, evidence)
            if not observed['running']:
                raise RuntimeError('source stopped before watched theories settled; no automatic replay')
            write_status(config, 'waiting_for_named_theories', gate=evidence,
                         source_service=observed['status'])
            time.sleep(config.get('poll_seconds', 30))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--config-sha256', required=True)
    args = parser.parse_args()
    if digest(args.config) != args.config_sha256:
        raise ValueError('handoff config changed')
    config = read(args.config)
    try:
        run(config)
    except HandoffPaused as error:
        write_status(config, 'paused_by_proof_watchdog', reason=str(error))
    except Exception as error:
        write_status(config, 'blocked', error_type=type(error).__name__, reason=str(error))
        raise


if __name__ == '__main__':
    main()
