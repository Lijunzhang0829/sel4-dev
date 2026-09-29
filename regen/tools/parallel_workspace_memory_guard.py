"""Stop one verified experiment on global memory pressure; never restart it.

This is monitoring, not a cgroup memory limit. MemAvailable includes pressure
from Docker workers even though their cgroups are outside the runner service.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time

from regen.execution.proof_commits import durable
from regen.tools import parallel_workspace_service as service


GiB = 1024 ** 3
EMERGENCY_BYTES = 2 * GiB


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
            monotonic=None, sleep=None):
    """Dependencies are injectable for tests without workers, services or sleep."""
    if (type(min_available_bytes) is not int or min_available_bytes < EMERGENCY_BYTES
            or any(type(v) not in (int, float) or not math.isfinite(v)
                   for v in (interval_seconds, pressure_seconds))
            or interval_seconds <= 0 or pressure_seconds < 0):
        raise ValueError('invalid memory guard policy')
    status, stop = status or service.status, stop or service.stop
    memory, monotonic = memory or available_memory, monotonic or time.monotonic
    sleep = sleep or time.sleep
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
                         'measurement': '/proc/meminfo MemAvailable',
                         'protection': 'monitor-and-stop; not a hard memory limit'},
              'samples': 0, 'recoveries': 0, 'events': []}

    def save(event):
        now = datetime.now(timezone.utc).isoformat()
        record.update(status=event, observed_at=now)
        record['events'] = (record['events'] + [{'event': event, 'at': now}])[-32:]
        durable(root / 'memory-guard.json', record)

    def owned_status():
        if hashlib.sha256(receipt_path.read_bytes()).hexdigest() != receipt_sha:
            raise RuntimeError('service receipt changed; refusing to stop')
        observed = status(root)
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
            save('service_finished')
            return record
        try:
            save('stopping')
        except OSError as error:
            # Disk exhaustion must not block a verified emergency stop.
            record['checkpoint_error'] = f'{type(error).__name__}: {error}'
            print('memory guard checkpoint failed before stop: ' + str(error), file=sys.stderr)
        result = stop(root)
        record['stop_receipt'] = result
        if result.get('running') or not result.get('identity_verified'):
            raise RuntimeError('memory guard could not confirm owned service stopped')
        try:
            save('stopped_for_memory')
        except OSError as error:
            record['checkpoint_error'] = f'{type(error).__name__}: {error}'
            print('memory guard checkpoint failed after stop: ' + str(error), file=sys.stderr)
        return record

    low_since = None
    try:
        save('started')
        while True:
            observed = owned_status()
            if not observed.get('running'):
                save('service_finished')
                return record
            try:
                available = memory()
                if type(available) is not int or available < 0:
                    raise ValueError('invalid global available-memory sample')
            except Exception as error:
                record['diagnostic'] = f'{type(error).__name__}: {error}'
                return stop_owned('memory_sampling_failed')
            now = monotonic()
            record.update(available_bytes=available, samples=record['samples'] + 1)
            if available < EMERGENCY_BYTES:
                return stop_owned('emergency_low_memory')
            if available < min_available_bytes:
                if low_since is None:
                    low_since = now
                    save('low_memory')
                record['pressure_elapsed_seconds'] = now - low_since
                if now - low_since >= pressure_seconds:
                    return stop_owned('sustained_low_memory')
            else:
                if low_since is not None:
                    record['recoveries'] += 1
                    save('memory_recovered')
                low_since = None
                record['pressure_elapsed_seconds'] = 0
            # Bounded checkpoint; do not accumulate a per-poll transcript.
            record['observed_at'] = datetime.now(timezone.utc).isoformat()
            durable(root / 'memory-guard.json', record)
            sleep(interval_seconds)
    except Exception as error:
        record['diagnostic'] = f'{type(error).__name__}: {error}'
        try:
            save('guard_error')
        except Exception:
            pass
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--min-available-bytes', type=int, default=6 * GiB)
    parser.add_argument('--interval-seconds', type=float, default=2)
    parser.add_argument('--pressure-seconds', type=float, default=4)
    args = parser.parse_args()
    try:
        result = monitor(args.run_dir, min_available_bytes=args.min_available_bytes,
                         interval_seconds=args.interval_seconds,
                         pressure_seconds=args.pressure_seconds)
    except Exception as error:
        print(f'memory guard failed: {type(error).__name__}: {error}', file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
