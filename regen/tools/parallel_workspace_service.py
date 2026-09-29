#!/usr/bin/env python3
"""Host the frozen parallel-workspace command in the user service manager.

This is process supervision only. Proof scheduling, budgets and acceptance stay
with proof_session_runner. No restart or request replay is automatic.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

from regen.execution.proof_commits import durable


PROPERTIES = ('LoadState', 'ActiveState', 'SubState', 'MainPID', 'ExecMainPID',
              'ExecMainCode', 'ExecMainStatus', 'Result', 'InvocationID', 'Description')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _unit(root):
    return 'regen-proof-' + hashlib.sha256(str(root).encode()).hexdigest()[:24] + '.service'


def _boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def _start_ticks(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return None


@contextmanager
def _control_lock(root):
    with (root/'service-control.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def _show(unit):
    command = ['systemctl', '--user', 'show', unit, '--no-pager']
    command.extend('--property='+key for key in PROPERTIES)
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if result.returncode and values.get('LoadState') != 'not-found':
        raise RuntimeError('cannot query user service manager: '+result.stderr.strip())
    if not values.get('LoadState'):
        raise RuntimeError('service manager returned no unit identity')
    return values


def _manifest(root):
    raw = (root/'experiment.json').read_bytes()
    manifest = json.loads(raw)
    command = manifest.get('command')
    if (not isinstance(command, list) or not command
            or not all(isinstance(value, str) and value and '\x00' not in value for value in command)):
        raise ValueError('experiment command must be a nonempty argv list')
    module = ['-m', 'regen.execution.proof_session_runner', 'parallel-workspace']
    if command[1:5] != ['-u', *module] and command[1:4] != module:
        raise ValueError('only the existing parallel-workspace runner may be supervised')
    # Accept only exact, separated runner options. argparse accepts abbreviations
    # and --key=value, so counting just a literal --run-dir does not bind the
    # actual parsed target. Keep this narrow operational surface unambiguous.
    valued = {'--run-dir', '--theory', '--planner-model', '--call-timeout',
              '--check-timeout', '--candidate-timeout', '--codex-home',
              '--file-planner-interface', '--max-parallel-theories',
              '--native-slots', '--native-threads', '--resident-limit', '--execution-policy'}
    switches = {'--allow-external-call'}
    arguments = command[5:] if command[1] == '-u' else command[4:]
    options = {}
    index = 0
    while index < len(arguments):
        option = arguments[index]
        if option not in valued | switches:
            raise ValueError('unsupported or ambiguous runner option: '+option)
        if option in options and option != '--theory':
            raise ValueError('duplicate runner option: '+option)
        index += 1
        if option in valued:
            if index >= len(arguments) or arguments[index].startswith('-'):
                raise ValueError('missing runner option value: '+option)
            value = arguments[index]
            index += 1
        else:
            value = True
        options.setdefault(option, []).append(value)
    if '--run-dir' not in options:
        raise ValueError('frozen command requires exactly one --run-dir')
    if Path(options['--run-dir'][0]).resolve() != root:
        raise ValueError('frozen command targets another run directory')
    executable = Path(command[0])
    cwd = Path(manifest.get('cwd', ''))
    if not executable.is_absolute() or not executable.is_file():
        raise ValueError('frozen command requires an absolute executable')
    if not cwd.is_absolute() or not cwd.is_dir():
        raise ValueError('experiment cwd must be an existing absolute directory')
    return manifest, hashlib.sha256(raw).hexdigest()


def _resource_admission(root, requirements):
    allowed = {'min_free_disk_bytes', 'min_available_memory_bytes'}
    if (not isinstance(requirements, dict) or set(requirements)-allowed
            or any(type(value) is not int or value < 0 for value in requirements.values())):
        raise ValueError('invalid experiment resource_requirements')
    memory = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        if key == 'MemAvailable':
            memory[key] = int(value.strip().split()[0])*1024
    actual = {'min_free_disk_bytes': shutil.disk_usage(root).free,
              'min_available_memory_bytes': memory.get('MemAvailable', 0)}
    deficient = [key for key, minimum in requirements.items() if actual[key] < minimum]
    if deficient:
        raise RuntimeError('insufficient resources for service launch: '+json.dumps({
            key: {'required': requirements[key], 'available': actual[key]} for key in deficient}))
    return {'observed_at': _now(), 'requirements': requirements,
            'free_disk_bytes': actual['min_free_disk_bytes'],
            'available_memory_bytes': actual['min_available_memory_bytes']}


def status(run_dir):
    """Read liveness from systemd and process birth identity, never a running flag."""
    root = Path(run_dir).resolve()
    receipt = json.loads((root/'service-launch.json').read_text())
    if receipt['run_dir'] != str(root) or receipt['unit'] != _unit(root):
        raise ValueError('service receipt does not identify this run')
    result = {'observed_at': _now(), 'unit': receipt['unit'], 'running': False,
              'identity_verified': False, 'proof_status_authority': 'immutable proof commits'}
    if receipt['boot_id'] != _boot_id():
        return {**result, 'status': 'previous_boot'}
    unit = _show(receipt['unit'])
    result['service'] = unit
    if unit['LoadState'] == 'not-found':
        return {**result, 'status': 'not_loaded'}
    if unit.get('Description') != receipt['description']:
        return {**result, 'status': 'identity_mismatch'}
    invocation_path = root/'service-invocation.json'
    invocation = json.loads(invocation_path.read_text()) if invocation_path.exists() else None
    if invocation and unit.get('InvocationID') and unit['InvocationID'] != invocation['invocation_id']:
        return {**result, 'status': 'identity_mismatch'}
    result['identity_verified'] = True
    pid = int(unit.get('MainPID', '0'))
    if pid:
        if not unit.get('InvocationID'):
            return {**result, 'identity_verified': False, 'status': 'identity_mismatch'}
        ticks = _start_ticks(pid)
        result.update(main_pid=pid, start_ticks=ticks)
        if ticks is None or (invocation and invocation.get('main_pid') == pid
                            and invocation.get('start_ticks') not in (None, ticks)):
            return {**result, 'identity_verified': False, 'status': 'process_identity_unavailable'}
        if invocation and invocation.get('main_pid', 0) not in (0, pid):
            return {**result, 'identity_verified': False, 'status': 'identity_mismatch'}
        result['running'] = unit.get('ActiveState') in {'active', 'activating', 'deactivating'}
    if result['running']:
        result['status'] = 'stopping' if unit['ActiveState'] == 'deactivating' else 'running'
    elif unit.get('ActiveState') == 'failed':
        result['status'] = 'failed'
    elif unit.get('SubState') == 'exited' and unit.get('ExecMainStatus') == '0':
        result['status'] = 'process_completed'
    else:
        result['status'] = 'stopped'
    return result


def start(run_dir, *, stop_timeout=45):
    root = Path(run_dir).resolve()
    if type(stop_timeout) is not int or not 1 <= stop_timeout <= 300:
        raise ValueError('stop timeout must be between 1 and 300 seconds')
    with _control_lock(root):
        if (root/'service-launch.json').exists():
            raise RuntimeError('this run already has a service launch receipt; inspect status or use a continuation')
        manifest, manifest_hash = _manifest(root)
        admission = _resource_admission(root, manifest.get('resource_requirements', {}))
        unit = _unit(root)
        if _show(unit)['LoadState'] != 'not-found':
            raise RuntimeError('a service already owns this run identity')
        receipt = {'schema_version': 1, 'run_dir': str(root), 'unit': unit,
                   'description': 'regen parallel workspace '+uuid4().hex,
                   'boot_id': _boot_id(), 'created_at': _now(),
                   'experiment_sha256': manifest_hash, 'command': manifest['command'],
                   'cwd': manifest['cwd'], 'stop_timeout_seconds': stop_timeout,
                   'restart_policy': 'never', 'resource_admission': admission}
        # Persist ownership before launch. A lost client response never permits
        # replay: status can still discover the exact unit from this receipt.
        durable(root/'service-launch.json', receipt, immutable=True)
        log = root/'service-runner.log'
        command = ['systemd-run', '--user', '--no-ask-password', '--quiet',
                   '--unit='+unit, '--description='+receipt['description'],
                   '--service-type=exec', '--expand-environment=no', '--remain-after-exit',
                   '--working-directory='+manifest['cwd'], '--property=Restart=no',
                   '--property=KillMode=mixed', '--property=KillSignal=SIGTERM',
                   '--property=SendSIGKILL=yes', '--property=TimeoutStopSec='+str(stop_timeout),
                   '--property=StandardInput=null', '--property=StandardOutput=append:'+str(log),
                   '--property=StandardError=append:'+str(log), '--property=UMask=0077']
        # Preserve explicit operational overrides without capturing credentials
        # or the caller's terminal/IDE environment in the receipt.
        for key in ('CODEX_HOME', 'REGEN_CODEX_BIN'):
            if key in os.environ:
                command.append('--setenv='+key+'='+os.environ[key])
        command.extend(['--', *manifest['command']])
        launched = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if launched.returncode:
            raise RuntimeError('service launch failed; inspect status before retry: '+launched.stderr.strip())
        observed = status(root)
        if not observed['identity_verified']:
            raise RuntimeError('service launch identity could not be verified: '+observed['status'])
        durable(root/'service-invocation.json', {
            'invocation_id': observed['service']['InvocationID'],
            'main_pid': observed.get('main_pid', 0), 'start_ticks': observed.get('start_ticks'),
        }, immutable=True)
        durable(root/'service-status.json', observed)
        return observed


def stop(run_dir):
    root = Path(run_dir).resolve()
    with _control_lock(root):
        observed = status(root)
        if observed['status'] in {'previous_boot', 'not_loaded'}:
            return observed
        if not observed['identity_verified']:
            raise RuntimeError('refusing to stop a service whose identity differs from the receipt')
        receipt = json.loads((root/'service-launch.json').read_text())
        stopped = subprocess.run(['systemctl', '--user', 'stop', receipt['unit']],
                                 capture_output=True, text=True,
                                 timeout=receipt['stop_timeout_seconds']+15)
        if stopped.returncode:
            raise RuntimeError('service stop failed: '+stopped.stderr.strip())
        result = status(root)
        durable(root/'service-status.json', result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'status', 'stop'))
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--stop-timeout', type=int, default=45)
    args = parser.parse_args()
    result = (start(args.run_dir, stop_timeout=args.stop_timeout) if args.action == 'start'
              else {'status': status, 'stop': stop}[args.action](args.run_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if args.action == 'start' and result['status'] not in {'running', 'process_completed'}:
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
