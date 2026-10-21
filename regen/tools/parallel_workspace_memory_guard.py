"""Stop one verified experiment on memory or disk pressure; never restart it.

This is monitoring, not a cgroup memory limit. MemAvailable includes pressure
from Docker workers even though their cgroups are outside the runner service.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
import time

from regen.execution.proof_commits import durable
from regen.tools import parallel_workspace_service as service


GiB = 1024 ** 3
EMERGENCY_BYTES = 2 * GiB
DISK_EMERGENCY_BYTES = 2 * GiB


class _CheckpointFailure(RuntimeError):
    """The monitor must not leave a verified task running without its guard."""


def free_disk_bytes(root):
    """Available bytes on the run directory's filesystem, not root's reserve."""
    return shutil.disk_usage(root).free


def available_memory():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            fields = line.split()
            if len(fields) != 3 or fields[2] != 'kB':
                break
            value = int(fields[1]) * 1024
            if value >= 0:
                return value
    raise RuntimeError('cannot read valid global MemAvailable')


def monitor(run_dir, *, min_available_bytes=6 * GiB, interval_seconds=2,
            pressure_seconds=4, status=None, stop=None, memory=None,
            monotonic=None, sleep=None, min_free_disk_bytes=None,
            emergency_free_disk_bytes=DISK_EMERGENCY_BYTES,
            disk_pressure_seconds=30, disk=None, checkpoint_seconds=30,
            status_query_attempts=3, status_retry_seconds=2):
    """Monitor one receipt-bound task; all sensors are injectable for tests.

    The Python API keeps memory-only behavior unless min_free_disk_bytes is
    supplied. The CLI enables disk protection by default. This guard neither
    deletes files nor restarts tasks and never claims to impose a hard limit.
    """
    if (type(min_available_bytes) is not int or min_available_bytes < EMERGENCY_BYTES
            or any(type(v) not in (int, float) or not math.isfinite(v)
                   for v in (interval_seconds, pressure_seconds,
                             disk_pressure_seconds, checkpoint_seconds,
                             status_retry_seconds))
            or interval_seconds <= 0 or pressure_seconds < 0
            or disk_pressure_seconds < 0 or checkpoint_seconds <= 0
            or status_retry_seconds < 0 or type(status_query_attempts) is not int
            or not 1 <= status_query_attempts <= 10
            or type(emergency_free_disk_bytes) is not int
            or emergency_free_disk_bytes <= 0
            or (min_free_disk_bytes is not None
                and (type(min_free_disk_bytes) is not int
                     or min_free_disk_bytes < emergency_free_disk_bytes))):
        raise ValueError('invalid resource guard policy')
    status, stop = status or service.status, stop or service.stop
    memory, monotonic = memory or available_memory, monotonic or time.monotonic
    sleep = sleep or time.sleep
    disk = disk or free_disk_bytes
    root = Path(run_dir).resolve()
    receipt_path = root / 'service-launch.json'
    raw = receipt_path.read_bytes()
    receipt = json.loads(raw)
    if receipt.get('run_dir') != str(root) or receipt.get('unit') != service._unit(root):
        raise ValueError('memory guard receipt does not identify this run')
    receipt_sha = hashlib.sha256(raw).hexdigest()
    record = {'schema_version': 1, 'run_dir': str(root), 'unit': receipt['unit'],
              'service_launch_sha256': receipt_sha,
              'started_at': datetime.now(timezone.utc).isoformat(),
              'policy': {'min_available_bytes': min_available_bytes,
                         'emergency_available_bytes': EMERGENCY_BYTES,
                         'interval_seconds': interval_seconds,
                         'pressure_seconds': pressure_seconds,
                         'min_free_disk_bytes': min_free_disk_bytes,
                         'emergency_free_disk_bytes': emergency_free_disk_bytes,
                         'disk_pressure_seconds': disk_pressure_seconds,
                         'disk_measurement': 'run directory filesystem available bytes',
                         'checkpoint_seconds': checkpoint_seconds,
                         'status_query_attempts': status_query_attempts,
                         'status_retry_seconds': status_retry_seconds,
                         'measurement': '/proc/meminfo MemAvailable',
                         'protection': 'monitor-and-stop; not a hard memory limit'},
              'samples': 0, 'recoveries': 0, 'disk_recoveries': 0,
              'status_query_failures': 0, 'stop_confirmed': False, 'events': []}

    def checkpoint(event=None):
        now = datetime.now(timezone.utc).isoformat()
        record['observed_at'] = now
        if event is not None:
            record['status'] = event
            record['events'] = (record['events'] + [{'event': event, 'at': now}])[-32:]
        try:
            durable(root / 'memory-guard.json', record)
        except Exception as error:
            record['checkpoint_error'] = f'{type(error).__name__}: {error}'[:1024]
            raise _CheckpointFailure(record['checkpoint_error']) from error

    def best_effort_checkpoint(event):
        try:
            checkpoint(event)
        except _CheckpointFailure as error:
            # Including ENOSPC at startup or during an ordinary checkpoint.
            # Failure to report an error must not block an owned-service stop.
            try:
                print('resource guard checkpoint failed: ' + str(error), file=sys.stderr)
            except Exception:
                pass

    def owned_status():
        for attempt in range(status_query_attempts):
            if hashlib.sha256(receipt_path.read_bytes()).hexdigest() != receipt_sha:
                raise RuntimeError('service receipt changed; refusing to stop')
            try:
                observed = status(root)
            except Exception as error:
                record['status_query_failures'] += 1
                record['status_query_error'] = f'{type(error).__name__}: {error}'[:1024]
                if attempt + 1 == status_query_attempts:
                    raise RuntimeError(
                        'service status unavailable after bounded retries; '
                        'identity and safe stop cannot be confirmed') from error
                sleep(status_retry_seconds)
            else:
                break
        if hashlib.sha256(receipt_path.read_bytes()).hexdigest() != receipt_sha:
            raise RuntimeError('service receipt changed; refusing to stop')
        record['service_status'] = observed
        if observed.get('unit') != receipt['unit']:
            raise RuntimeError('service unit identity mismatch; refusing to stop')
        if not observed.get('identity_verified'):
            # These terminal states have no current service to stop.
            if not observed.get('running') and observed.get('status') in {'previous_boot', 'not_loaded'}:
                return observed
            raise RuntimeError('service identity unverified; refusing to stop')
        return observed

    def stop_owned(reason):
        observed = owned_status()
        record['stop_reason'] = reason
        if not observed.get('running'):
            record['stop_confirmed'] = True
            best_effort_checkpoint('service_finished')
            return record
        best_effort_checkpoint('stopping')
        try:
            result = stop(root, expected_receipt_sha256=receipt_sha)
        except OSError as error:
            # service.stop may have stopped the task before its own status
            # checkpoint encounters ENOSPC. Inspect; never repeat the stop.
            record['stop_checkpoint_error'] = f'{type(error).__name__}: {error}'[:1024]
            result = owned_status()
            if result.get('running'):
                raise RuntimeError('resource guard could not confirm owned service stopped') from error
        record['stop_receipt'] = result
        terminal = result.get('status') in {'previous_boot', 'not_loaded'}
        if (result.get('unit') != receipt['unit'] or result.get('running')
                or (not result.get('identity_verified') and not terminal)):
            raise RuntimeError('memory guard could not confirm owned service stopped')
        record['stop_confirmed'] = True
        resource = ('disk' if reason in {'emergency_low_disk', 'sustained_low_disk',
                                       'disk_sampling_failed'}
                    else 'guard' if reason in {'checkpoint_failed',
                                              'service_status_query_failed'} else 'memory')
        best_effort_checkpoint('stopped_for_' + resource)
        return record

    low_since = None
    disk_low_since = None
    try:
        checkpoint('started')
        next_checkpoint = monotonic() + checkpoint_seconds
        while True:
            observed = owned_status()
            if not observed.get('running'):
                record['stop_confirmed'] = True
                checkpoint('service_finished')
                return record
            if record['status_query_failures']:
                # An unreliable supervisor must not silently leave a task
                # running. Recovery of its query permits one bound stop.
                return stop_owned('service_status_query_failed')
            try:
                available = memory()
                if type(available) is not int or available < 0:
                    raise ValueError('invalid global available-memory sample')
            except Exception as error:
                record['diagnostic'] = f'{type(error).__name__}: {error}'[:1024]
                return stop_owned('memory_sampling_failed')
            now = monotonic()
            record.update(available_bytes=available, samples=record['samples'] + 1)
            if available < EMERGENCY_BYTES:
                return stop_owned('emergency_low_memory')
            if available < min_available_bytes:
                if low_since is None:
                    low_since = now
                    checkpoint('low_memory')
                record['pressure_elapsed_seconds'] = now - low_since
                if now - low_since >= pressure_seconds:
                    return stop_owned('sustained_low_memory')
            else:
                if low_since is not None:
                    record['recoveries'] += 1
                    checkpoint('memory_recovered')
                low_since = None
                record['pressure_elapsed_seconds'] = 0
            if min_free_disk_bytes is not None:
                try:
                    free = disk(root)
                    if type(free) is not int or free < 0:
                        raise ValueError('invalid available-disk sample')
                except Exception as error:
                    record['diagnostic'] = f'{type(error).__name__}: {error}'[:1024]
                    return stop_owned('disk_sampling_failed')
                record['free_disk_bytes'] = free
                if free < emergency_free_disk_bytes:
                    return stop_owned('emergency_low_disk')
                if free < min_free_disk_bytes:
                    if disk_low_since is None:
                        disk_low_since = now
                        checkpoint('low_disk')
                    record['disk_pressure_elapsed_seconds'] = now - disk_low_since
                    if now - disk_low_since >= disk_pressure_seconds:
                        return stop_owned('sustained_low_disk')
                else:
                    if disk_low_since is not None:
                        record['disk_recoveries'] += 1
                        checkpoint('disk_recovered')
                    disk_low_since = None
                    record['disk_pressure_elapsed_seconds'] = 0
            # One bounded latest checkpoint, not an accumulating poll log.
            if now >= next_checkpoint:
                checkpoint()
                next_checkpoint = now + checkpoint_seconds
            sleep(interval_seconds)
    except _CheckpointFailure:
        try:
            return stop_owned('checkpoint_failed')
        except Exception as error:
            record['diagnostic'] = f'{type(error).__name__}: {error}'[:1024]
            best_effort_checkpoint('guard_error')
            raise
    except Exception as error:
        record['diagnostic'] = f'{type(error).__name__}: {error}'[:1024]
        best_effort_checkpoint('guard_error')
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--min-available-bytes', type=int, default=6 * GiB)
    parser.add_argument('--interval-seconds', type=float, default=2)
    parser.add_argument('--pressure-seconds', type=float, default=4)
    parser.add_argument('--min-free-disk-bytes', type=int, default=8 * GiB,
                        help='sustained available-disk floor on the run filesystem')
    parser.add_argument('--emergency-free-disk-bytes', type=int, default=DISK_EMERGENCY_BYTES)
    parser.add_argument('--disk-pressure-seconds', type=float, default=30)
    parser.add_argument('--checkpoint-seconds', type=float, default=30)
    parser.add_argument('--status-query-attempts', type=int, default=3)
    parser.add_argument('--status-retry-seconds', type=float, default=2)
    args = parser.parse_args()
    try:
        result = monitor(args.run_dir, min_available_bytes=args.min_available_bytes,
                         interval_seconds=args.interval_seconds,
                         pressure_seconds=args.pressure_seconds,
                         min_free_disk_bytes=args.min_free_disk_bytes,
                         emergency_free_disk_bytes=args.emergency_free_disk_bytes,
                         disk_pressure_seconds=args.disk_pressure_seconds,
                         checkpoint_seconds=args.checkpoint_seconds,
                         status_query_attempts=args.status_query_attempts,
                         status_retry_seconds=args.status_retry_seconds)
    except Exception as error:
        print(f'memory guard failed: {type(error).__name__}: {error}', file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
