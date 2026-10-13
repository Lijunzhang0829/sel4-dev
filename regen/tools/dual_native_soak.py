"""Bounded, model-free dual native lifecycle soak on a frozen Semantic Base.

This exercises production BatchWorker, NativeResources and CheckExecutor, not a
second proof runner. Synthetic facts are never published to benchmark journals.
The bound is elapsed monotonic wall time INCLUDING startup/recovery, not the
experiment effective-time budget. Full document/seal and model transport need
their own acceptance receipts. No sleeping command is used as native load.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid

from regen.execution.batch_checker import BatchWorker
from regen.execution.check_executor import CURRENT_RESOURCES, CheckExecutor, NativeResources
from regen.execution.check_tasks import TERMINAL, check_cancelled
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import durable
from regen.preparation.checker_context import load_context
from regen.tools.native_backend_acceptance import fixture, proof_rejected

GiB = 1024 ** 3
IMPLEMENTATIONS = (
    'regen/tools/dual_native_soak.py', 'regen/tools/native_backend_acceptance.py',
    'regen/execution/batch_checker.py', 'regen/execution/batch_checker.ML',
    'regen/execution/batch_audit.py', 'regen/execution/persistent_checker.py',
    'regen/execution/check_executor.py', 'regen/execution/check_tasks.py',
    'regen/execution/isabelle_node_checker.py', 'regen/execution/native_container.py',
    'regen/execution/worker_owner.sh',
)


def utc(stamp=None):
    return datetime.fromtimestamp(time.time() if stamp is None else stamp,
                                  tz=timezone.utc).isoformat()


def implementation_hashes():
    root = Path(__file__).resolve().parents[2]
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in IMPLEMENTATIONS}


def validate_options(duration_seconds, memory_mb, threads, cpus, lemmas):
    if not 30 <= duration_seconds <= 7200:
        raise ValueError('duration must be between 30 and 7200 monotonic wall seconds')
    if type(memory_mb) is not int or not 4096 <= memory_mb <= 10240:
        raise ValueError('native memory must be 4096..10240 MiB per worker')
    if type(threads) is not int or not 1 <= threads <= 4 or not 0 < cpus <= 4:
        raise ValueError('use at most four threads/CPUs per native worker')
    if type(lemmas) is not int or not 1 <= lemmas <= 256:
        raise ValueError('candidate lemmas must be 1..256')


def source(import_name, content):
    return f'theory Dual_Native_Soak\nimports "{import_name}"\nbegin\n{content}\nend\n'


def workload(side, sequence, lemmas):
    """Real induction/simplification commands bounded independently of run age."""
    tag = f'{side}_{sequence}'
    start = f'\nML \\<open>writeln ("DUAL_SOAK_START {tag} " ^ Time.toString (Time.now ()))\\<close>\n'
    end = f'\nML \\<open>writeln ("DUAL_SOAK_END {tag} " ^ Time.toString (Time.now ()))\\<close>\n'
    names = [f'soak_candidate_{index}' for index in range(lemmas)]
    proofs = ''.join(f'lemma {name}: "length (rev (xs::nat list)) = length xs" '
                     'by (induct xs) simp_all\n' for name in names)
    return start + proofs + end, names, tag


def native_window(log, tag):
    marks = re.findall(rf'^DUAL_SOAK_(START|END) {re.escape(tag)} ([0-9.]+)$', log, re.M)
    values = {kind: float(stamp) for kind, stamp in marks}
    if len(marks) != 2 or set(values) != {'START', 'END'} or values['END'] <= values['START']:
        raise ValueError('missing/ambiguous native proof execution markers')
    return {'start': values['START'], 'end': values['END']}


def overlap_seconds(windows):
    """Union of intersections between actual native proof-command intervals."""
    left, right = sorted(windows[1]), sorted(windows[2])
    index = other = 0
    overlaps = []
    while index < len(left) and other < len(right):
        start, end = max(left[index][0], right[other][0]), min(left[index][1], right[other][1])
        if end > start:
            overlaps.append((start, end))
        if left[index][1] <= right[other][1]:
            index += 1
        else:
            other += 1
    return sum(end - start for start, end in overlaps)


def prune_transactions(worker, first, last):
    """Only completed private request artifacts, never a live request or prefix."""
    if not 0 < first <= last <= worker.index:
        raise ValueError('pruning requires completed owned transaction indices')
    removed = 0
    for index in range(first, last + 1):
        for path in worker.root.glob(str(index) + '.*'):
            if path.is_file() and not path.is_symlink():
                removed += path.stat().st_size
                path.unlink()
    return removed


def host_resources(output):
    memory = {parts[0].rstrip(':'): int(parts[1]) * 1024
              for line in Path('/proc/meminfo').read_text().splitlines()
              if len(parts := line.split()) >= 3 and parts[2] == 'kB'}
    return {'available_memory_bytes': memory['MemAvailable'],
            'swap_used_bytes': memory['SwapTotal'] - memory['SwapFree'],
            'free_disk_bytes': shutil.disk_usage(output).free}


def output_bytes(output):
    total = 0
    for path in output.rglob('*'):
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            pass  # A completed private transaction may be pruned concurrently.
    return total


def unexpected_task_errors(statuses, stop_reason):
    return [row for row in statuses if row['status'] == 'error' and not (
        stop_reason == 'duration_complete'
        and row.get('error_type') in {'InterruptedError', 'BrokenBarrierError'})]


class ResourceGuard:
    def __init__(self, grace_seconds=None):
        self.low_since = {}
        self.grace = {'available_memory_bytes':4, 'free_disk_bytes':30}
        if grace_seconds is not None:
            self.grace = {key:grace_seconds for key in self.grace}

    def check(self, sample, now):
        for key, low in (('available_memory_bytes', 6 * GiB), ('free_disk_bytes', 8 * GiB)):
            value = sample[key]
            if value < 2 * GiB:
                raise RuntimeError(f'emergency resource floor: {key}={value}')
            if value < low:
                began = self.low_since.setdefault(key, now)
                if now - began >= self.grace[key]:
                    raise RuntimeError(f'sustained resource floor: {key}={value}')
            else:
                self.low_since.pop(key, None)


class Recorder:
    def __init__(self, output, duration, hashes):
        self.output, self.hashes = output, hashes
        self.started, self.started_wall = time.monotonic(), time.time()
        self.deadline = self.started + duration
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.recovered = threading.Event()
        self.recovery_started = threading.Event()
        self.barrier = threading.Barrier(2)
        self.names = set()
        self.current_names = {}
        self.rows = {side: {'phase': 'starting', 'green_checks': 0, 'red_checks': 0,
                            'steady_checks': 0, 'warm_reused': False,
                            'completed_cases': [], 'pruned_bytes': 0} for side in (1, 2)}
        self.windows = {1: [], 2: []}
        self.reason = None
        self.recovery = {}

    def append(self, kind, **fields):
        with self.lock:
            event = {'kind':kind, 'timestamp':utc(),
                     'elapsed_seconds':time.monotonic()-self.started, **fields}
            with (self.output/'events.jsonl').open('a') as stream:
                stream.write(json.dumps(event, sort_keys=True) + '\n')

    def update(self, side, **values):
        with self.lock:
            self.rows[side].update(values)

    def register(self, side, name):
        if not re.fullmatch(r'regen-batch-[0-9a-f]{16}', name):
            raise ValueError('not a private production batch worker name')
        with self.lock:
            self.names.add(name)
            self.current_names[side] = name
        self.append('worker_registered', side=side, container=name)

    def kill_owned(self, name):
        with self.lock:
            if name not in self.names:
                raise ValueError('refusing to kill an unregistered container')
        result = subprocess.run(['docker', 'kill', '--signal', 'KILL', name],
                                capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise RuntimeError('owned failure injection failed: ' + result.stderr.strip())
        self.append('failure_injected', container=name, signal='KILL')

    def checkpoint(self, resources, sample, *, docker_stats=False):
        stats = []
        if docker_stats:
            with self.lock:
                names = list(self.current_names.values())
            if names:
                result = subprocess.run(['docker', 'stats', '--no-stream', '--format', '{{json .}}', *names],
                                        capture_output=True, text=True, timeout=10)
                stats = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
        with self.lock:
            state = dict(status='running' if not self.stop.is_set() else 'stopping',
                started_at=utc(self.started_wall), expected_workload_end_at=utc(self.started_wall+self.deadline-self.started),
                elapsed_seconds=time.monotonic()-self.started,
                remaining_seconds=max(0, self.deadline-time.monotonic()), stop_reason=self.reason,
                workers=self.rows, host=sample, containers=stats,
                native_peak_active=resources.peak_active,
                native_proof_overlap_seconds=overlap_seconds(self.windows), recovery=self.recovery)
            durable(self.output/'status.json', state)
        self.append('heartbeat', **state)

    def check(self):
        check_cancelled()
        if self.stop.is_set() or time.monotonic() >= self.deadline:
            raise InterruptedError('soak wall deadline or stop request')

    def sync(self):
        self.check()
        self.barrier.wait(timeout=max(.1, min(180, self.deadline-time.monotonic())))


class SoakChecker(IsabelleChecker):
    """Use the production command with one stricter fixture-only mount policy."""
    def _command(self, *args, **kwargs):
        command = super()._command(*args, **kwargs)
        mount = f'type=bind,src={self.heaps_dir},dst=/root/.isabelle/heaps'
        command[command.index(mount)] = mount + ',readonly'
        return command


class ObservedWorker(BatchWorker):
    def _launch(self, command):
        self.checker.recorder.register(self.checker.side, self.name)
        return super()._launch(command)


class Side:
    def __init__(self, side, ctx, output, resources, recorder, lemmas):
        self.side, self.ctx, self.resources, self.recorder, self.lemmas = side, ctx, resources, recorder, lemmas
        self.root = output/f'worker-{side}'
        self.checker = SoakChecker(output, self.root, generated_dir=self.root/'generated',
            heaps_dir=ctx['heaps'], l4v_dir=ctx['l4v'], semantic_workspace=ctx['semantic_workspace'],
            isabelle_dir=ctx['isabelle'], image=ctx['image'], architecture=ctx['architecture'],
            base_session=ctx['base_session'], base_import=ctx['base_import'],
            base_catalog=ctx['base_catalog'], timeout=180)
        self.checker.batch_candidate_timeout = 60
        self.checker.owner_lease_seconds = 15
        self.checker.recorder, self.checker.side = recorder, side
        self.worker = None
        self.sequence = 0

    def open(self, body='\nunbundle l4v_word_context\n', names=()):
        self.recorder.check()
        if implementation_hashes() != self.recorder.hashes:
            raise ValueError('implementation changed during frozen soak')
        started = time.monotonic()
        with self.resources.lease(self.checker):
            self.worker = ObservedWorker(self.checker, source(self.ctx['base_import'], body),
                                         list(names), [self.ctx['base_import']])
            self.checker._batch_worker = self.worker
        self.recorder.append('worker_ready', side=self.side, worker=self.worker.name,
                             elapsed_prepare_seconds=time.monotonic()-started)

    def check(self, case, *, bad=False, commit=False, suffix=None, names=None, retain=False):
        self.recorder.check()
        self.recorder.update(self.side, phase=case)
        self.sequence += 1
        tag = None
        if suffix is None:
            if bad:
                suffix, names = '\nlemma doomed: "False" by simp\n', ['doomed']
            else:
                suffix, names, tag = workload(self.side, self.sequence, self.lemmas)
        before = self.worker.base
        first = self.worker.index + 1
        started = time.monotonic()
        with self.resources.lease(self.checker):
            result = self.worker.check_batch(source(self.ctx['base_import'], before+suffix), names)
            if self.worker.base != before:
                raise AssertionError('candidate mutated committed prefix')
            if bad:
                if not proof_rejected(result['returncode'], result['diagnostic']) or self.worker.pending is not None:
                    raise AssertionError('negative case was not a clean proof rejection')
            elif result['verdict'] != 'green':
                durable(self.root/f'failure-{self.sequence}.json', result, immutable=True)
                raise AssertionError('synthetic proof failed: '+result['diagnostic'])
            if commit:
                self.worker.align(self.worker.pending)
        window = native_window(Path(result['log']).read_text(), tag) if tag else None
        with self.recorder.lock:
            row = self.recorder.rows[self.side]
            row['red_checks' if bad else 'green_checks'] += 1
            if case == 'steady':
                row['steady_checks'] += 1
            else:
                row['completed_cases'].append(case)
            if window:
                self.recorder.windows[self.side].append((window['start'], window['end']))
        self.recorder.append('candidate', side=self.side, case=case, sequence=self.sequence,
            worker=self.worker.name, request_id=result.get('request_id', self.worker.index),
            verdict=result['verdict'], wall_seconds=time.monotonic()-started, native_window=window,
            candidate_sha256=hashlib.sha256(suffix.encode()).hexdigest(),
            prefix_sha256=hashlib.sha256(before.encode()).hexdigest(), audit_sha256=result.get('audit_sha256'))
        if retain or self.sequence % 120 == 0:
            durable(self.root/f'evidence-{self.sequence}.json', result, immutable=True)
        else:
            removed = prune_transactions(self.worker, first, self.worker.index)
            with self.recorder.lock:
                self.recorder.rows[self.side]['pruned_bytes'] += removed

    def run(self, task_dir):
        try:
            self.open()
            self.recorder.sync()
            self.check('seed', suffix=fixture(self.side)['shared'], names=['shared_fact'], commit=True, retain=True)
            seed_worker = self.worker.name
            self.recorder.sync()
            self.check('warm-1', retain=True)
            self.check('warm-2', retain=True)
            if self.worker.name != seed_worker:
                raise AssertionError('warm checks replaced the resident worker')
            self.recorder.update(self.side, warm_reused=True)
            self.recorder.sync()
            self.check('red-green-isolation', bad=self.side == 1, retain=True)
            self.check('valid-after-red', retain=True)
            self.recorder.sync()
            if self.side == 1:
                self.recover()
            else:
                count = 0
                while not self.recorder.recovery_started.wait(.1):
                    self.recorder.check()
                    if self.recorder.recovered.is_set():
                        raise RuntimeError('peer failed before recovery injection')
                while not self.recorder.recovered.is_set():
                    self.check('peer-during-recovery', retain=count == 0)
                    count += 1
                with self.recorder.lock:
                    self.recorder.recovery['peer_green_checks_during_recovery'] = count
            self.recorder.sync()
            while time.monotonic() < self.recorder.deadline and not self.recorder.stop.is_set():
                self.check('steady', bad=self.sequence % 20 == 0)
            return {'side': self.side, 'status': 'deadline_reached'}
        except BaseException:
            self.recorder.barrier.abort()
            self.recorder.recovered.set()
            raise

    def recover(self):
        old = self.worker
        accepted = old.base
        started = time.monotonic()
        self.recorder.recovery_started.set()
        self.recorder.update(self.side, phase='worker-recovery')
        self.recorder.kill_owned(old.name)
        old.process.wait(timeout=15)
        with self.resources.lease(self.checker):
            try:
                old.check_batch(source(self.ctx['base_import'], accepted+'\nlemma unavailable: "True" by simp\n'),
                                ['unavailable'])
            except RuntimeError:
                observed = True
            else:
                raise AssertionError('killed worker did not report infrastructure failure')
            self.checker.close_prefix()
        self.open(accepted, ['shared_fact'])
        if old.name == self.worker.name or self.worker.base != accepted:
            raise AssertionError('worker rebuild did not preserve exact committed prefix')
        self.check('recovered-consumer', suffix=fixture(self.side)['recovery'], names=['recovered'], retain=True)
        with self.recorder.lock:
            self.recorder.recovery.update(old_worker=old.name, new_worker=self.worker.name,
                infrastructure_failure_observed=observed, prefix_preserved=True,
                wall_seconds=time.monotonic()-started)
        self.recorder.recovered.set()


def cleanup_registered(recorder):
    """Last-resort cleanup stays inside the exact names this invocation created."""
    rows = []
    for name in sorted(recorder.names):
        try:
            removed = subprocess.run(['docker', 'rm', '-f', name], capture_output=True, text=True, timeout=15)
            inspected = subprocess.run(['docker', 'inspect', '--format', '{{.State.Status}}', name],
                                       capture_output=True, text=True, timeout=10)
            gone = inspected.returncode != 0 and 'no such' in inspected.stderr.lower()
            rows.append(dict(container=name, confirmed_absent=gone, remove_returncode=removed.returncode))
        except Exception as error:
            rows.append(dict(container=name, confirmed_absent=False, error=str(error)))
    return rows


def run(dataset, output, *, duration_seconds=7200, memory_mb=10240, threads=4, cpus=4, lemmas=64):
    validate_options(duration_seconds, memory_mb, threads, cpus, lemmas)
    ctx = load_context(Path(dataset))
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    hashes = implementation_hashes()
    recorder = Recorder(output, duration_seconds, hashes)
    resources = NativeResources(2, threads, 2, memory_mb=memory_mb, cpus=cpus)
    manifest = dict(run_id='dual-native-soak-'+uuid.uuid4().hex, scope='synthetic-dual-native-lifecycle-only',
        model_calls=0, benchmark_contracts_proved=0, parent_heaps_read_only=True,
        dataset_root=str(Path(dataset).resolve()), checker_context_id=ctx['checker_context_id'],
        image=ctx['image'], isabelle_identity=ctx['isabelle_sha256'], resource_policy=resources.policy,
        duration_seconds=duration_seconds, clock='monotonic wall including startup/recovery; cleanup reported separately',
        implementation_sha256=hashes, lemmas_per_candidate=lemmas,
        retention='all compact candidate receipts; initial/recovery and every 120th detailed candidate',
        not_covered=['model requests/retries', 'effective-generation clock', 'heavy historical theory prefixes',
                     'production document commits/seal/integration', '48-hour unattended supervisor recovery'])
    durable(output/'manifest.json', manifest, immutable=True)
    report = dict(manifest, passed=False)
    executor = None
    previous_handlers = {}
    guard = ResourceGuard()
    token = CURRENT_RESOURCES.set(resources)
    tasks = []
    def request_stop(signum, _frame):
        recorder.reason = 'signal:' + signal.Signals(signum).name
        recorder.stop.set()
    try:
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous_handlers[signum] = signal.signal(signum, request_stop)
        sample = host_resources(output)
        guard.check(sample, time.monotonic())
        # The operational minimum is headroom, not a promise that two quotas fit.
        if sample['available_memory_bytes'] < 2 * memory_mb * 1024 ** 2 + 6 * GiB:
            raise RuntimeError('startup needs both native quotas plus 6 GiB host headroom')
        if sample['free_disk_bytes'] < 8 * GiB:
            raise RuntimeError('startup needs at least 8 GiB free disk')
        executor = CheckExecutor(output/'tasks', workers=2)
        for side in (1, 2):
            actor = Side(side, ctx, output, resources, recorder, lemmas)
            task = executor.submit(kind='action', theory=f'Synthetic_{side}', binding={'run_id':manifest['run_id']},
                request={'kind':'bounded-native-lifecycle-soak'}, execute=actor.run, owner=f'side-{side}')
            tasks.append(task['task_id'])
        heartbeat = 0.
        while not recorder.stop.is_set():
            now = time.monotonic()
            if now >= recorder.deadline:
                recorder.reason = 'duration_complete'
                break
            sample = host_resources(output)
            guard.check(sample, now)
            statuses = [executor.get(task) for task in tasks]
            failed = [row for row in statuses if row['status'] in {'error', 'cancelled'}]
            if failed:
                raise RuntimeError('native worker task failed: '+json.dumps(failed))
            if all(row['status'] in TERMINAL for row in statuses):
                recorder.reason = 'duration_complete' if time.monotonic() >= recorder.deadline else 'workers_ended_early'
                break
            if now >= heartbeat:
                recorder.checkpoint(resources, sample, docker_stats=True)
                used = output_bytes(output)
                if used > 512 * 1024 ** 2:
                    raise RuntimeError('soak evidence exceeded its 512 MiB disk budget')
                heartbeat = now + 30
            recorder.stop.wait(min(2, max(0, recorder.deadline-time.monotonic())))
    except BaseException as error:
        recorder.reason = recorder.reason or 'failure'
        report['error'] = {'type':type(error).__name__, 'message':str(error)}
    finally:
        recorder.stop.set()
        recorder.barrier.abort()
        recorder.recovered.set()
        workload_end = time.monotonic()
        report['workload_wall_seconds'] = workload_end-recorder.started
        errors = []
        if executor is not None:
            try:
                executor.close()
                report['task_statuses'] = [executor.get(task) for task in tasks]
            except BaseException as error:
                errors.append(str(error))
        try:
            resources.close()
        except BaseException as error:
            errors.append(str(error))
        finally:
            CURRENT_RESOURCES.reset(token)
        report['cleanup'] = cleanup_registered(recorder)
        report['cleanup_seconds'] = time.monotonic()-workload_end
        report['cleanup_errors'] = errors
        report['unexpected_task_errors'] = unexpected_task_errors(
            report.get('task_statuses', []), recorder.reason)
        report['workers'] = recorder.rows
        report['recovery'] = recorder.recovery
        report['native_peak_active'] = resources.peak_active
        report['native_proof_overlap_seconds'] = overlap_seconds(recorder.windows)
        report['native_queue_seconds_sum'] = sum(row['queue_seconds'] for row in resources.metrics)
        report['native_lease_seconds_sum'] = sum(row['execution_seconds'] for row in resources.metrics)
        report['stop_reason'] = recorder.reason
        report['passed'] = (recorder.reason == 'duration_complete' and 'error' not in report and not errors
            and not report['unexpected_task_errors']
            and all(row['confirmed_absent'] for row in report['cleanup'])
            and resources.peak_active == 2 and report['native_proof_overlap_seconds'] > 0
            and all(row['warm_reused'] and row['steady_checks'] > 0 for row in recorder.rows.values())
            and recorder.rows[1]['red_checks'] > 0 and recorder.recovery.get('prefix_preserved', False)
            and recorder.recovery.get('peer_green_checks_during_recovery', 0) > 0)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        durable(output/'report.json', report, immutable=True)
        durable(output/'status.json', dict(status='completed' if report['passed'] else 'failed',
            passed=report['passed'], stop_reason=recorder.reason, report=str(output/'report.json'),
            elapsed_seconds=time.monotonic()-recorder.started, workers=recorder.rows,
            recovery=recorder.recovery, error=report.get('error')))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--duration-seconds', type=float, default=7200)
    parser.add_argument('--native-memory-mb', type=int, default=10240)
    parser.add_argument('--native-threads', type=int, default=4)
    parser.add_argument('--native-cpus', type=float, default=4)
    parser.add_argument('--lemmas-per-candidate', type=int, default=64)
    args = parser.parse_args()
    report = run(args.dataset, args.output, duration_seconds=args.duration_seconds,
                 memory_mb=args.native_memory_mb, threads=args.native_threads,
                 cpus=args.native_cpus, lemmas=args.lemmas_per_candidate)
    print(json.dumps({key:report[key] for key in ('passed', 'stop_reason', 'native_proof_overlap_seconds',
                                               'workload_wall_seconds', 'cleanup_seconds')}, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
