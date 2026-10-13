"""Bounded, model-free native backend acceptance on generic frozen seL4 inputs.

This deliberately calls the internal BatchWorker, not the document acceptance
tool. It changes no proof run. Parent heaps are read-only; an independent raw
Isabelle process/build checks the exact saved candidate without the production
renderer, candidate cache, or checker adapter.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from types import SimpleNamespace
import uuid
import xml.etree.ElementTree as ET

from regen.execution.batch_audit import validate_native_audit
from regen.execution.batch_checker import BatchWorker, body
from regen.execution.proof_commits import durable
from regen.preparation.checker_context import load_context
from regen.preparation.semantic_base_builder import SESSION_CATALOGS


LABEL = "Native_Backend_Acceptance"
GiB = 1024 ** 3


def source(import_name, content):
    return f'theory {LABEL}\nimports "{import_name}"\nbegin\n{content}\nend\n'


def fixture(side):
    """Same theory/fact names, but distinct local assumptions and exported facts."""
    if side not in (1, 2):
        raise ValueError("fixture side must be 1 or 2")
    statement = f'"(x::nat) = {side} \\<Longrightarrow> x + 1 = {side + 1}"'
    other = 3 - side
    shared = (f'\ncontext fixes x :: nat assumes side_assumption: "x = {side}" begin\n'
              f'lemma shared_fact: "x + 1 = {side + 1}" using side_assumption by simp\nend\n')
    return {
        "shared": shared,
        "consumer": f'\nlemma accepted_consumer: {statement} by (rule shared_fact)\n',
        "premature": f'\nlemma premature: {statement} by (rule shared_fact)\n',
        "failed": '\nlemma leaked_helper: "True" by simp\nlemma doomed: "False" by simp\n',
        "theft": '\nlemma stolen: "True" by (rule leaked_helper)\n',
        "cross": (f'\nlemma cross_context: "(x::nat) = {other} \\<Longrightarrow> '
                  f'x + 1 = {other + 1}" by (rule shared_fact)\n'),
        "recovery": f'\nlemma recovered: {statement} by (rule shared_fact)\n',
    }


def probe(side):
    # A controlled two-second native command establishes process overlap, not
    # proof throughput. No model-supplied ML or benchmark proof enters here.
    return (f'\nML \\<open>writeln ("NATIVE_ACCEPT_START {side} " ^ Time.toString (Time.now ()));'
            '\nOS.Process.sleep (Time.fromSeconds 2);'
            f'\nwriteln ("NATIVE_ACCEPT_END {side} " ^ Time.toString (Time.now ()))\\<close>\n')


def native_window(log, side):
    values = {}
    for kind, stamp in re.findall(
            rf"^NATIVE_ACCEPT_(START|END) {side} ([0-9.]+)$", log, re.M):
        values[kind] = float(stamp)
    if set(values) != {"START", "END"} or values["END"] <= values["START"]:
        raise ValueError("missing native process overlap markers")
    return {"start": values["START"], "end": values["END"]}


def proof_rejected(returncode, log):
    """Do not count broken imports, syntax, timeouts, or CLI failures as a negative."""
    expected = ('Failed to apply initial proof method', 'Failed to finish proof')
    infrastructure = ('Bad theory import', 'Unknown session', 'Bad session',
                      'Outer syntax error', 'Malformed', 'syntax error',
                      'No such file', 'Undefined fact', 'Failed to load')
    return (returncode not in (0, 124, 125, 126, 127)
            and any(marker in log for marker in expected)
            and not any(marker in log for marker in infrastructure))


def binding_mutations(check, *, prefix, delta, imports, names):
    expected = dict(prefix=prefix, delta=delta, names=names, imports=imports, label=LABEL)
    validate_native_audit(check, **expected)
    results = []
    for field in ("prefix", "delta", "imports", "facts", "worker", "request", "theory"):
        root = ET.fromstring(check["audit_content"])
        if field in ("worker", "request", "theory"):
            root.set(field, "999999" if field == "request" else "wrong-binding")
        elif field in ("imports", "facts"):
            root.find(field)[0].text = "wrong-binding"
        else:
            root.find(field).text = (root.find(field).text or "") + " changed"
        content = ET.tostring(root, encoding="unicode")
        changed = {**check, "audit_content": content,
                   "audit_sha256": hashlib.sha256(content.encode()).hexdigest()}
        try:
            validate_native_audit(changed, **expected)
        except ValueError as error:
            results.append({"field": field, "rejected": True, "diagnostic": str(error)})
        else:
            raise AssertionError("modified native audit accepted: " + field)
    return results


class Limits:
    def __init__(self, output, total_seconds, max_output_bytes):
        self.output = output
        self.deadline = time.monotonic() + total_seconds
        self.max_output_bytes = max_output_bytes
        self.names = []
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.failure = None
        self.monitor = threading.Thread(target=self._watch, daemon=True)

    def register(self, name):
        self.check()
        with self.lock:
            self.names.append(name)

    def check(self):
        if self.failure:
            raise RuntimeError(self.failure)
        if time.monotonic() >= self.deadline:
            raise TimeoutError("native acceptance total time budget exhausted")
        if shutil.disk_usage(self.output).free < 2 * GiB:
            raise RuntimeError("native acceptance stopped: less than 2 GiB free disk")
        available = next(int(line.split()[1]) * 1024 for line in
                         Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:'))
        if available < 6 * GiB:
            raise RuntimeError("native acceptance stopped: less than 6 GiB available memory")
        used = sum(path.stat().st_size for path in self.output.rglob('*') if path.is_file())
        if used > self.max_output_bytes:
            raise RuntimeError("native acceptance output size budget exhausted")

    def cleanup(self):
        with self.lock:
            names = list(self.names)
        rows = []
        for name in names:
            removed = subprocess.run(['docker', 'rm', '-f', name], capture_output=True,
                                     text=True, timeout=15)
            inspected = subprocess.run(['docker', 'inspect', name], capture_output=True,
                                       text=True, timeout=10)
            gone = inspected.returncode != 0 and 'no such' in inspected.stderr.lower()
            rows.append({'container': name, 'removed_returncode': removed.returncode,
                         'confirmed_absent': gone, 'inspect_stderr': inspected.stderr.strip()})
        return rows

    def _watch(self):
        while not self.stop.wait(2):
            try:
                self.check()
            except FileNotFoundError:
                continue  # A worker may atomically rename a diagnostic file.
            except Exception as error:
                self.failure = f'{type(error).__name__}: {error}'
                self.cleanup()
                return


def command(ctx, output, *, extra_mounts=(), user_heaps=None):
    mounts = [(ctx['l4v'], '/sel4-project/verification/l4v', True),
              (ctx['isabelle'], '/workspace/verification/isabelle', True),
              (ctx['semantic_workspace'], '/semantic-base', True),
              (ctx['heaps'], '/parent-heaps', True),
              (output, '/acceptance', False)]
    mounts += [(path, target, False) for path, target in extra_mounts]
    if user_heaps:
        mounts.append((user_heaps, '/oracle-heaps', False))
    args = ['docker', 'run', '--rm', '--network', 'none', '--cpus', '2',
            '--memory', '8g', '--memory-swap', '8g', '--pids-limit', '512']
    for path, target, readonly in mounts:
        args += ['--mount', f'type=bind,src={Path(path).resolve()},dst={target}'
                 + (',readonly' if readonly else '')]
    isabelle = '/workspace/verification/isabelle/bin/isabelle'
    # Load the image's settings normally, then override only the heap store.
    # A nested bind at $ISABELLE_HOME/heaps fails when that directory does not
    # exist in the read-only Isabelle tree. No host configuration is changed.
    return args + ['-e', 'L4V_ARCH=' + ctx['architecture'], ctx['image'], isabelle,
                   'env', 'ISABELLE_HEAPS_SYSTEM=/parent-heaps',
                   'ISABELLE_HEAPS=' + ('/oracle-heaps' if user_heaps else '/tmp/acceptance-heaps'),
                   isabelle]


def catalogs(ctx):
    args = []
    for catalog in SESSION_CATALOGS:
        args += ['-d', '/sel4-project/verification/l4v/' + catalog]
    return args + ['-d', ctx['base_catalog']]


class ObservedBatchWorker(BatchWorker):
    def _launch(self, args):
        self.checker.limits.register(self.name)
        return super()._launch(args)


def exercise_worker(ctx, output, side, timeout, limits, ready):
    root = output / f'worker-{side}'
    root.mkdir()
    imports = [ctx['base_import']]
    pieces = fixture(side)
    checker = SimpleNamespace(work_dir=root, timeout=timeout, batch_candidate_timeout=20,
                              owner_lease_seconds=15, previous_theories=[],
                              base_session=ctx['base_session'], limits=limits)

    def worker_command(path, *, session, writable_mounts):
        return (command(ctx, output, extra_mounts=writable_mounts)
                + ['process', '-l', session, '-o', 'threads=2'] + catalogs(ctx)
                + ['-T', '/unused'])

    checker._command = worker_command
    initial = source(ctx['base_import'], '\nunbundle l4v_word_context\n')
    worker = None
    checks = []
    try:
        worker = ObservedBatchWorker(checker, initial, [], imports)
        ready.wait(timeout=timeout)

        def check(name, suffix, facts, verdict):
            limits.check()
            before = worker.base
            candidate = source(ctx['base_import'], before + suffix)
            result = worker.check_batch(candidate, facts)
            durable(root / (name + '.json'), result, immutable=True)
            if result['verdict'] != verdict:
                raise AssertionError(f'{name}: expected {verdict}: {result["diagnostic"]}')
            if verdict == 'red':
                diagnostic = result['diagnostic']
                if result.get('timed_out') or result['returncode'] != 1:
                    raise AssertionError(f'{name}: timeout/infrastructure failure is not a negative proof test')
                missing = {'pending-fact-invisible': 'shared_fact',
                           'failed-helper-invisible': 'leaked_helper'}.get(name)
                if missing:
                    if 'Undefined fact' not in diagnostic or missing not in diagnostic:
                        raise AssertionError(f'{name}: not the expected missing-fact rejection: {diagnostic}')
                elif not proof_rejected(result['returncode'], diagnostic):
                    raise AssertionError(f'{name}: not an expected proof-method rejection: {diagnostic}')
            if worker.base != before:
                raise AssertionError('check changed base without commit')
            checks.append({'name': name, 'verdict': result['verdict'],
                           'returncode': result['returncode'], 'worker': worker.name})
            return result, candidate

        first, _ = check('pending-not-committed', probe(side) + pieces['shared'],
                         ['shared_fact'], 'green')
        window = native_window(Path(first['log']).read_text(), side)
        pending_before = worker.pending
        if pending_before is None or worker.pending == worker.base:
            raise AssertionError('GREEN did not create pending state')
        check('pending-fact-invisible', pieces['premature'], ['premature'], 'red')
        if worker.pending is not None:
            raise AssertionError('failed attempt retained pending state')
        success, _ = check('commit-shared-fact', pieces['shared'], ['shared_fact'], 'green')
        mutations = binding_mutations(success, prefix=worker.base, delta=pieces['shared'],
                                      imports=imports, names=['shared_fact'])
        worker.align(worker.pending)
        if worker.pending is not None:
            raise AssertionError('commit did not clear pending')
        failure, _ = check('helper-then-failed-consumer', pieces['failed'], ['leaked_helper', 'doomed'], 'red')
        snapshot = failure.get('native_proof_state_full') or {}
        if (snapshot.get('origin') != 'before_failed_command' or not snapshot.get('command')
                or not snapshot.get('goals')):
            raise AssertionError('failed proof did not produce an actual command/goal snapshot')
        check('failed-helper-invisible', pieces['theft'], ['stolen'], 'red')
        check('valid-consumer', pieces['consumer'], ['accepted_consumer'], 'green')
        worker.align(worker.pending)
        cross, bad_source = check('cross-context-rejected', pieces['cross'], ['cross_context'], 'red')
        check('valid-after-failure', pieces['recovery'], ['recovered'], 'green')
        worker.align(worker.pending)
        exact = source(ctx['base_import'], worker.base)
        (root / (LABEL + '.thy')).write_text(exact)
        negative = root / 'negative'
        negative.mkdir()
        (negative / (LABEL + '.thy')).write_text(bad_source)
        return {'pass': True, 'side': side, 'checks': checks, 'window': window,
                'mutations': mutations, 'source': str(root / (LABEL + '.thy')),
                'source_sha256': hashlib.sha256(exact.encode()).hexdigest(),
                'negative_source': str(negative / (LABEL + '.thy')),
                'negative_source_sha256': hashlib.sha256(bad_source.encode()).hexdigest(),
                'context': f'x = {side}', 'proof_state_captured': True,
                'failed_command': snapshot['command'], 'failed_goal_count': len(snapshot['goals'])}
    except BaseException:
        ready.abort()
        raise
    finally:
        if worker is not None:
            worker.close()


def raw_native(ctx, output, relative, timeout, limits, *, build=False, expected=0):
    """Independent oracle: raw CLI only, no checker renderer/caches/adapter."""
    limits.check()
    directory = output / relative
    source_path = directory / (LABEL + '.thy')
    before = hashlib.sha256(source_path.read_bytes()).hexdigest()
    session = 'Native_Backend_Oracle_' + uuid.uuid4().hex[:12]
    name = 'regen-native-acceptance-' + uuid.uuid4().hex[:16]
    heaps = directory / 'oracle-heaps' if build else None
    if heaps:
        heaps.mkdir()
        (directory / 'ROOT').write_text(
            f'session {session} = {ctx["base_session"]} +\n'
            '  sessions Lib CorresK AInvs\n  theories ' + LABEL + '\n')
    args = command(ctx, output, user_heaps=heaps)
    args[2:2] = ['--name', name]
    if build:
        args += ['build', '-b', '-j', '1', '-o', 'threads=2'] + catalogs(ctx)
        args += ['-d', '/acceptance/' + str(relative), session]
    else:
        args += ['process', '-l', ctx['base_session'], '-o', 'threads=2'] + catalogs(ctx)
        args += ['-T', '/acceptance/' + str(relative / LABEL)]
    limits.register(name)
    started = time.monotonic()
    duration = min(timeout, limits.deadline - started)
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=duration)
        log = result.stdout + result.stderr
        returncode = result.returncode
    except subprocess.TimeoutExpired as error:
        def text(value):
            return value.decode(errors='replace') if isinstance(value, bytes) else value or ''
        log = text(error.stdout) + text(error.stderr) + '\nIndependent oracle timed out.\n'
        returncode = 124
    finally:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=15)
    stem = 'independent-build' if build else 'independent-process'
    (directory / (stem + '.log')).write_text(log)
    unchanged = before == hashlib.sha256(source_path.read_bytes()).hexdigest()
    passed = returncode == 0 if expected == 0 else proof_rejected(returncode, log)
    receipt = {'pass': passed and unchanged, 'returncode': returncode,
               'expected': 'green' if expected == 0 else 'proof-rejected',
               'source_sha256': before, 'exact_source_unchanged': unchanged,
               'wall_seconds': time.monotonic() - started, 'command': args,
               'log': str(directory / (stem + '.log')), 'parent_heaps_read_only': True,
               'independent_of_candidate_cache_renderer_and_checker': True}
    durable(directory / (stem + '.json'), receipt, immutable=True)
    if not receipt['pass']:
        raise AssertionError(f'{stem} {relative} failed: {returncode}; see {receipt["log"]}')
    return receipt


def run(dataset, output, timeout=180, total_seconds=1200, max_output_bytes=2 * GiB):
    if not 30 <= timeout <= 300 or not timeout <= total_seconds <= 1800:
        raise ValueError('require 30 <= timeout <= 300 and timeout <= total <= 1800')
    if not 16 * 1024 ** 2 <= max_output_bytes <= 3 * GiB:
        raise ValueError('output budget must be between 16 MiB and 3 GiB')
    ctx = load_context(Path(dataset))
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    limits = Limits(output, total_seconds, max_output_bytes)
    report = {'pass': False, 'model_calls': 0, 'benchmark_contracts_proved': 0,
              'scope': 'generic-fixtures-in-frozen-seL4-context',
              'checker_context_id': ctx['checker_context_id'], 'image': ctx['image'],
              'isabelle_identity': ctx['isabelle_sha256'], 'parent_heaps': str(ctx['heaps']),
              'native_workers': 2, 'threads_per_worker': 2, 'memory_limit_per_container': '8g',
              'timeout_seconds': timeout, 'total_seconds_limit': total_seconds,
              'output_bytes_limit': max_output_bytes,
              'not_covered': ['model tool transport', 'document acceptance and journal',
                              '122-node Scheduler performance or seal',
                              'task cancellation/crash recovery', 'general session/query/snapshot API']}
    repo = Path(__file__).resolve().parents[2]
    implementations = ['regen/tools/native_backend_acceptance.py',
                       'regen/execution/batch_checker.py', 'regen/execution/batch_checker.ML',
                       'regen/execution/persistent_checker.py', 'regen/execution/batch_audit.py',
                       'regen/execution/worker_owner.sh']
    report['implementation_sha256'] = {
        name: hashlib.sha256((repo / name).read_bytes()).hexdigest() for name in implementations}
    (output / 'driver-snapshot.py').write_bytes(Path(__file__).read_bytes())
    durable(output / 'manifest.json', report, immutable=True)
    try:
        limits.check()
        limits.monitor.start()
        ready = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(exercise_worker, ctx, output, side, timeout, limits, ready)
                       for side in (1, 2)]
            workers = [future.result() for future in futures]
        overlap = min(row['window']['end'] for row in workers) - max(row['window']['start'] for row in workers)
        if overlap <= 0:
            raise AssertionError('native command windows did not overlap')
        report.update(workers=workers, native_command_overlap_seconds=overlap,
                      overlap_notice='Controlled 2-second ML windows; not proof throughput evidence.')
        oracles = []
        for side in (1, 2):
            relative = Path(f'worker-{side}')
            oracles.append(raw_native(ctx, output, relative, timeout, limits))
            oracles.append(raw_native(ctx, output, relative / 'negative', timeout, limits, expected=1))
            oracles.append(raw_native(ctx, output, relative, timeout, limits, build=True))
        report.update(oracles=oracles, **{'pass': True})
    except Exception as error:
        report['failure'] = f'{type(error).__name__}: {error}'
    finally:
        limits.stop.set()
        if limits.monitor.ident is not None:
            limits.monitor.join(timeout=20)
        report['cleanup'] = limits.cleanup()
        if limits.failure or not all(row['confirmed_absent'] for row in report['cleanup']):
            report.update({'pass': False, 'resource_guard_failure': limits.failure})
        report['output_bytes'] = sum(p.stat().st_size for p in output.rglob('*') if p.is_file())
        durable(output / 'report.json', report, immutable=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--max-total-seconds', type=int, default=1200)
    parser.add_argument('--max-output-mib', type=int, default=2048)
    args = parser.parse_args()
    result = run(args.dataset_root, args.output, args.timeout,
                 args.max_total_seconds, args.max_output_mib * 1024 ** 2)
    print(json.dumps({key: result.get(key) for key in
                      ('pass', 'failure', 'native_command_overlap_seconds', 'output_bytes')}, indent=2))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
