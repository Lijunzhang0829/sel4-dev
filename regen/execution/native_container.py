"""Shared limits and owner leases for cold checks and builds.

The in-container guardian survives loss of the Python owner. A client exit is
not evidence that its container stopped; cleanup errors remain visible.
"""
from contextlib import contextmanager
from pathlib import Path
import subprocess
import tempfile
import threading
import time

DEFAULT_LIMITS = dict(cpus=4, memory_mb=16384, pids_limit=1024)


def docker_limits():
    from regen.execution.check_executor import CURRENT_RESOURCES
    pool = CURRENT_RESOURCES.get()
    policy = pool.policy if pool is not None else DEFAULT_LIMITS
    memory = str(policy['memory_mb']) + 'm'
    return ['--cpus', str(policy['cpus']), '--memory', memory,
            '--memory-swap', memory, '--pids-limit', str(policy['pids_limit'])]


def remove_owned(name):
    result = subprocess.run(['docker', 'rm', '-f', name],
                            capture_output=True, text=True, timeout=30)
    diagnostic = (result.stderr or '') + (result.stdout or '')
    if result.returncode and name in diagnostic and 'is already in progress' in diagnostic:
        wait_for_owned_removal(name)
        return
    if result.returncode and 'No such container:' not in (result.stderr or ''):
        raise RuntimeError('owned container cleanup failed: ' + name + ': ' +
                           (result.stderr or '') + (result.stdout or ''))


def wait_for_owned_removal(name):
    """Reconcile Docker --rm/rm races; existence or daemon errors are not success."""
    for attempt in range(10):
        observed = subprocess.run(['docker', 'inspect', '--format', '{{.State.Status}}', name],
                                  capture_output=True, text=True, timeout=2)
        diagnostic = ((observed.stderr or '') + (observed.stdout or '')).casefold()
        if observed.returncode:
            if any(f'no such {kind}: {name}'.casefold() in diagnostic for kind in ('object', 'container')):
                return
            raise RuntimeError('cannot confirm owned container removal: '+name+': '+diagnostic.strip())
        if attempt < 9:
            time.sleep(.1)
    raise RuntimeError('owned container still exists after bounded removal wait: '+name)


@contextmanager
def owner_command(command, *, lease_seconds=60):
    # Only the harness's named Docker Isabelle operations use this protocol.
    executable = '/workspace/verification/isabelle/bin/isabelle'
    if command[:2] != ['docker', 'run'] or executable not in command:
        yield command
        return
    if '--name' not in command:
        raise ValueError('owned native container requires an explicit name')
    name = command[command.index('--name') + 1]
    from regen.execution.persistent_checker import _renew_owner_lease
    with tempfile.TemporaryDirectory(prefix='regen-native-owner-') as directory:
        root = Path(directory)
        heartbeat = root / 'owner.heartbeat'
        heartbeat.touch()
        (root / 'owner.sh').write_bytes(Path(__file__).with_name('worker_owner.sh').read_bytes())
        owned = list(command)
        owned[2:2] = ['--mount', f'type=bind,src={root},dst=/native-owner,readonly']
        at = owned.index(executable)
        owned[at:at] = ['/bin/sh', '/native-owner/owner.sh', str(lease_seconds),
                       '/native-owner/owner.heartbeat']
        stopped = threading.Event()
        thread = threading.Thread(target=_renew_owner_lease,
            args=(heartbeat, stopped, min(5, lease_seconds/3)), daemon=True)
        thread.start()
        try:
            yield owned
        finally:
            stopped.set()
            thread.join(timeout=6)
            remove_owned(name)
