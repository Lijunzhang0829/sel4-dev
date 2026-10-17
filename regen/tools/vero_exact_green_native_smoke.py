"""Model-free, real-Isabelle shadow acceptance for VERO exact GREEN reuse.

Run with a frozen checker dataset and the unmodified VERO support theory. The
fixture is synthetic and never writes into a proof experiment or its workspace.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import threading
import time
from unittest.mock import patch

from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import durable
from regen.execution.vero_checker_adapter import VeroCheckerAdapter
from regen.infrastructure.experiment_ledger import Ledger
from regen.preparation.checker_context import load_context


THEORY = 'Vero_Exact_Green_Smoke'
CONTRACT = 'c0001'
GIB = 1024 ** 3


class Stop:
    def __init__(self):
        self.event = threading.Event()
        self.sealed = []

    def check(self):
        if self.event.is_set():
            raise RuntimeError('smoke test cancelled')

    def begin_exclusion(self, reason):
        return reason

    def end_exclusion(self, handle):
        return None

    def mark_sealed(self, theory):
        self.sealed.append(theory)


class Observer:
    def __init__(self, output):
        self.bench = {'contracts':[{'id':CONTRACT,'module':THEORY}]}
        self.stop = Stop()
        self.ledger = Ledger(output / 'ledger', 'vero-exact-green-native-smoke')
        self.published = []

    def checked(self, theory, result, source):
        self.published.append({'theory':theory,
            'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'audited':list(result['audited']),
            'reused_exact_check':result['reused_exact_check'],
            'native_elapsed_seconds':result['native_elapsed_seconds']})


def source(proof):
    return (f'theory {THEORY}\nimports Vero_Support\nbegin\n'
        f'vero_prove "{CONTRACT}" {CONTRACT}_prove: "True"\n{proof}\n'
        f'vero_disprove "{CONTRACT}" {CONTRACT}_disprove: "False"\noops\n'
        'end\n')


def verify_receipt(stage, result, rendered, expected_reuse):
    expected_source = (rendered / (THEORY + '.thy')).read_bytes()
    manifest = json.loads(Path(result['source_manifest']).read_text())
    if (result['contract_status'] != 'ok' or result['seal_status'] != 'sealed'
            or result['audited'] != [CONTRACT + ':prove']
            or result['reused_exact_check'] is not expected_reuse
            or manifest[THEORY + '.thy'] != hashlib.sha256(expected_source).hexdigest()):
        raise AssertionError(f'{stage}: unexpected native result: {result}')
    native = result['native_evidence']
    original_source = Path(native['source']).read_bytes()
    original_log = Path(native['log']).read_bytes()
    if (native['returncode'] != 0 or original_source != expected_source
            or b'VERO_RESULT c0001 prove clean' not in original_log):
        raise AssertionError(f'{stage}: original native receipt does not match source')
    native_receipt = native.get('native_receipt')
    source_files=(native_receipt.get('source_files_sha256')
        if isinstance(native_receipt,dict) else None)
    source_files_digest=(hashlib.sha256(json.dumps(source_files,sort_keys=True,
        separators=(',',':')).encode()).hexdigest()
        if isinstance(source_files,dict) else None)
    if (not isinstance(native_receipt, dict)
            or native_receipt.get('check_scope') != 'cold-full-theory'
            or native_receipt.get('returncode') != 0
            or not isinstance(source_files,dict)
            or native_receipt.get('source_sha256') != source_files_digest
            or source_files.get('candidate/'+THEORY+'.thy') != hashlib.sha256(expected_source).hexdigest()
            or any(source_files.get('generated/'+path.name)
                != hashlib.sha256(path.read_bytes()).hexdigest()
                for path in Path(native['source']).parent.glob('*.thy'))
            or native_receipt.get('log_sha256') != hashlib.sha256(original_log).hexdigest()
            or not native_receipt.get('checker_binding_sha256')):
        raise AssertionError(f'{stage}: common cold-check receipt mismatch')
    build = result['build_evidence']
    build_receipt = build.get('native_receipt') if isinstance(build, dict) else None
    if (not isinstance(build_receipt, dict)
            or build_receipt.get('check_scope') != 'generated-session-build'
            or build_receipt.get('returncode') != 0
            or build_receipt.get('log_sha256') != hashlib.sha256(
                Path(build['log']).read_bytes()).hexdigest()):
        raise AssertionError(f'{stage}: common seal-build receipt mismatch')
    if expected_reuse:
        receipt = result['reuse_receipt']
        if (result['native_elapsed_seconds'] != 0
                or receipt['original_source_sha256'] != hashlib.sha256(original_source).hexdigest()
                or receipt['original_log_sha256'] != hashlib.sha256(original_log).hexdigest()
                or receipt['original_manifest_sha256'] != hashlib.sha256(
                    Path(receipt['original_source_manifest']).read_bytes()).hexdigest()):
            raise AssertionError(f'{stage}: reuse evidence or zero native cost mismatch')
    return {'stage':stage, 'status':result['status'],
        'audited':result['audited'], 'source_identity':result['source_identity'],
        'source_sha256':hashlib.sha256(expected_source).hexdigest(),
        'original_log_sha256':hashlib.sha256(original_log).hexdigest(),
        'reused_exact_check':result['reused_exact_check'],
        'native_elapsed_seconds':result['native_elapsed_seconds'],
        'seal_elapsed_seconds':result['seal_elapsed_seconds'],
        'seal_status':result['seal_status'],
        'native_request_id':native_receipt['request_id'],
        'seal_request_id':build_receipt['request_id'],
        'source_manifest':result['source_manifest']}


def run(dataset, support, output, *, timeout=900,
        shared_host_lock_dir=None, shared_host_slots=1):
    dataset = Path(dataset).resolve()
    support = Path(support).resolve()
    output = Path(output).resolve()
    context = load_context(dataset)
    if shutil.disk_usage(output.parent).free < 3 * GIB:
        raise RuntimeError('less than 3 GiB free; native smoke will not clone heaps')
    output.mkdir(parents=True, exist_ok=False)
    rendered = output / 'rendered'
    rendered.mkdir()
    raw_support = support.read_text()
    if raw_support.count('IMPORT_BASE') != 1 or 'VERO_RESULT' not in raw_support:
        raise ValueError('expected unmodified VERO support template')
    native_support = raw_support.replace('IMPORT_BASE', context['base_import'])
    (rendered / 'Vero_Support.thy').write_text(native_support)
    (rendered / (THEORY + '.thy')).write_text(source('by simp'))
    observer = Observer(output)
    report = {'schema_version':1, 'model_calls':0, 'formal_experiment':False,
        'dataset':str(dataset), 'checker_context_id':context['checker_context_id'],
        'support_sha256':hashlib.sha256(raw_support.encode()).hexdigest(),
        'output':str(output), 'stages':[], 'passed':False}
    native_calls = []
    seal_calls = []
    original_check = IsabelleChecker._check
    original_build = IsabelleChecker.build_generated_session

    def native(checker, *args, **kwargs):
        native_calls.append(time.monotonic())
        return original_check(checker, *args, **kwargs)

    def seal(checker, *args, **kwargs):
        seal_calls.append(time.monotonic())
        return original_build(checker, *args, **kwargs)

    try:
        with patch.object(IsabelleChecker, '_check', native), \
                patch.object(IsabelleChecker, 'build_generated_session', seal), \
                VeroCheckerAdapter(dataset, output / 'native',
                    timeout=timeout, observer=observer,
                    shared_host_lock_dir=shared_host_lock_dir,
                    shared_host_slots=shared_host_slots) as checker:
            checker.set_frozen_plan({'imports':{THEORY:[]},
                'plan_sha256':hashlib.sha256(b'smoke-plan').hexdigest(),
                'allocation_sha256':hashlib.sha256(b'smoke-allocation').hexdigest(),
                'imports_sha256':hashlib.sha256(b'smoke-imports').hexdigest()})
            report['resource_policy'] = dict(checker.resources.policy)

            for stage, proof, support_source, expected_reuse in (
                    ('cold', 'by simp', native_support, False),
                    ('identical', 'by simp', native_support, True),
                    ('proof_changed', 'by blast', native_support, False),
                    ('import_changed', 'by blast', native_support.replace(
                        '\nend\n', '\nlemma vero_aux_smoke: "True" by simp\nend\n'), False)):
                (rendered / (THEORY + '.thy')).write_text(source(proof))
                (rendered / 'Vero_Support.thy').write_text(support_source)
                before_native, before_seal = len(native_calls), len(seal_calls)
                print(stage + ': start', flush=True)
                result = checker.check(rendered, THEORY)
                row = verify_receipt(stage, result, rendered, expected_reuse)
                row['new_native_calls'] = len(native_calls) - before_native
                row['new_seal_build_calls'] = len(seal_calls) - before_seal
                if (row['new_native_calls'] != (0 if expected_reuse else 1)
                        or row['new_seal_build_calls'] != 1):
                    raise AssertionError(f'{stage}: check/seal invocation count mismatch: {row}')
                report['stages'].append(row)
                print(json.dumps(row, sort_keys=True), flush=True)
        report['native_calls'] = len(native_calls)
        report['seal_build_calls'] = len(seal_calls)
        report['published_checks'] = observer.published
        report['passed'] = True
    finally:
        durable(output / 'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--support', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=900)
    parser.add_argument('--shared-host-lock-dir', type=Path)
    parser.add_argument('--shared-host-slots', type=int, default=1)
    args = parser.parse_args()
    result = run(args.dataset, args.support, args.output, timeout=args.timeout,
                 shared_host_lock_dir=args.shared_host_lock_dir,
                 shared_host_slots=args.shared_host_slots)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
