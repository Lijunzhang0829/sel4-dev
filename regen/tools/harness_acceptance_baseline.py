"""Record bounded regression controls without treating them as native acceptance.

Uses only isolated existing test fixtures. Optional Docker fault tests kill only
containers created by those tests. No model requests or proof-run mutations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import unittest


MODULES = (
    'test_document_interface', 'test_theory_document',
    'test_document_declaration_checks', 'test_document_concurrency',
    'test_document_recovery_cancellation', 'test_check_executor',
    'test_check_executor_failures', 'test_batch_audit_evidence',
    'test_shared_proof_workspace', 'test_parallel_file_planner',
    'test_batch_timeouts', 'test_codex_workspace_session',
    'test_worker_lifecycle', 'test_symbol_resolution',
)


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    return {'command': list(args), 'returncode': result.returncode,
            'stdout': result.stdout, 'stderr': result.stderr}


def protected_snapshot(root):
    """Metadata inventory, plus hashes of small authority pointers; not full hash.

    Never follow asset symlinks or decode/replay giant experiment commits.
    """
    root = root.resolve()
    inventory = hashlib.sha256()
    count = size = 0
    controls = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        for name in sorted(files + [d for d in dirs if (Path(directory)/d).is_symlink()]):
            path = Path(directory)/name
            stat = path.lstat()
            relative = str(path.relative_to(root))
            row = [relative, stat.st_size, stat.st_mtime_ns,
                   os.readlink(path) if path.is_symlink() else None]
            inventory.update((json.dumps(row, separators=(',', ':')) + '\n').encode())
            count += 1
            size += stat.st_size
            if not path.is_symlink() and name in {'run.json', 'HEAD.json'}:
                controls[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {'root': str(root), 'entry_count': count, 'logical_bytes': size,
            'metadata_inventory_sha256': inventory.hexdigest(),
            'authority_pointer_sha256': controls,
            'limitation': 'File size/mtime/link inventory, not a hash of all proof contents.'}


def production_hashes(repo):
    result = command('git', '-C', str(repo), 'ls-files', 'regen/execution',
                     'regen/infrastructure', 'regen/planning', 'regen/preparation')
    if result['returncode']:
        raise RuntimeError(result)
    return {name: hashlib.sha256((repo/name).read_bytes()).hexdigest()
            for name in result['stdout'].splitlines() if (repo/name).is_file()}


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = []
        self.started = {}

    def startTest(self, test):
        self.started[test.id()] = time.monotonic()
        super().startTest(test)

    def record(self, test, status, detail=None):
        self.rows.append({'test': test.id(), 'status': status,
                          'seconds': time.monotonic()-self.started.get(test.id(), time.monotonic()),
                          'detail': detail})

    def addSuccess(self, test):
        self.record(test, 'pass')
        super().addSuccess(test)

    def addFailure(self, test, error):
        self.record(test, 'fail', self._exc_info_to_string(error, test))
        super().addFailure(test, error)

    def addError(self, test, error):
        self.record(test, 'error', self._exc_info_to_string(error, test))
        super().addError(test, error)

    def addSkip(self, test, reason):
        self.record(test, 'skip', reason)
        super().addSkip(test, reason)

    def addSubTest(self, test, subtest, error):
        if error is not None:
            self.record(test, 'subtest_fail', self._exc_info_to_string(error, test))
        super().addSubTest(test, subtest, error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--protected-run', type=Path, required=True)
    parser.add_argument('--docker-faults', action='store_true')
    parser.add_argument('--all-current', action='store_true',
                        help='Run every test_*.py in regen/tests/current, still without provider calls')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    if not (args.protected_run/'run.json').is_file():
        parser.error('protected run.json is missing')
    if args.output.resolve().is_relative_to(args.protected_run.resolve()):
        parser.error('validation output cannot be inside the protected experiment')
    if shutil.disk_usage(repo).free < 2*1024**3:
        parser.error('less than 2 GiB free; do not start validation')
    if os.environ.get('REGEN_WORKER_ISABELLE_TESTS') == '1':
        parser.error('native smoke must run separately, not hidden in regression controls')
    if args.docker_faults:
        for image in ['ubuntu:20.04']:
            if command('docker', 'image', 'inspect', image)['returncode']:
                parser.error('required local Docker image is missing; no implicit pull')
        os.environ['REGEN_WORKER_DOCKER_TESTS'] = '1'
    else:
        os.environ.pop('REGEN_WORKER_DOCKER_TESTS', None)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output/'driver-snapshot.py').write_bytes(Path(__file__).read_bytes())
    modules = (tuple(path.stem for path in sorted((repo/'regen/tests/current').glob('test_*.py')))
               if args.all_current else MODULES)
    before = protected_snapshot(args.protected_run)
    implementation = production_hashes(repo)
    manifest = {'scope': 'regression controls, NOT full native or model acceptance',
                'model_calls': 0, 'modules': list(modules),
                'docker_faults': args.docker_faults,
                'git_head': command('git', 'rev-parse', 'HEAD'),
                'git_status': command('git', 'status', '--short'),
                'production_sha256': implementation,
                'tool_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'disk_free_bytes': shutil.disk_usage(repo).free,
                'docker_before': command('docker', 'ps', '--format', '{{.Names}} {{.Status}}'),
                'protected_before': before}
    save(output/'manifest.json', manifest)
    suite = unittest.defaultTestLoader.loadTestsFromNames(
        ['regen.tests.current.'+name for name in modules])
    started = time.monotonic()
    with (output/'unittest.log').open('w') as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2,
                                        resultclass=RecordedResult).run(suite)
    after = protected_snapshot(args.protected_run)
    unchanged = implementation == production_hashes(repo)
    report = {'scope': manifest['scope'], 'model_calls': 0,
              'tests_run': result.testsRun, 'failures': len(result.failures),
              'errors': len(result.errors), 'skipped': len(result.skipped),
              'seconds': time.monotonic()-started, 'results': result.rows,
              'controls_pass': result.wasSuccessful(),
              'acceptance_complete': False,
              'protected_after': after, 'protected_unchanged': before == after,
              'production_unchanged': unchanged,
              'docker_after': command('docker', 'ps', '--format', '{{.Names}} {{.Status}}'),
              'disk_free_bytes_after': shutil.disk_usage(repo).free}
    save(output/'report.json', report)
    print(json.dumps({key: report[key] for key in (
        'tests_run', 'failures', 'errors', 'skipped', 'seconds', 'controls_pass',
        'acceptance_complete', 'protected_unchanged', 'production_unchanged')}, indent=2))
    return 0 if result.wasSuccessful() and before == after and unchanged else 1


if __name__ == '__main__':
    raise SystemExit(main())
