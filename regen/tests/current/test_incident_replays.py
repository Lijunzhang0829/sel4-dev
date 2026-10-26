"""Small fixtures test replay-package integrity and truthful result reporting."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from regen.tools import replay_incidents as replay


class IncidentReplayTests(unittest.TestCase):
    def fixture(self, root):
        root.mkdir(parents=True, exist_ok=True)
        (root / 'cases').mkdir()
        (root / 'evidence').mkdir()
        evidence = root / 'evidence' / 'small.json'
        evidence.write_text('{"observed": "failure"}\n')
        case = {'schema_version': 1, 'id': 'tiny-incident', 'title': 'tiny fixture',
            'component': 'test', 'category': 'integrity', 'status': 'fixed',
            'source_steps': [{'step': 7}],
            'evidence': [{'path': 'evidence/small.json', 'sha256': replay.sha256(evidence),
                'source_path': '/historical/step.json', 'selection': 'public result'}],
            'replays': [{'id': 'tiny-replay', 'handler': 'test.tiny',
                'kind': 'synthetic_regression', 'parameters': {'sentinel': 1},
                'oracle': {'state': 'safe'}}], 'existing_tests': [],
            'limitations': ['Generated integrity fixture; no prover or model.']}
        (root / 'cases' / 'tiny.json').write_text(json.dumps(case))
        (root / 'inventory.json').write_text('{"schema_version":1,"issues":[]}\n')
        (root / 'coverage.json').write_text('{"schema_version":1,"coverage":[]}\n')
        self.reseal(root, [case['id']])
        return case

    @staticmethod
    def reseal(root, case_ids):
        paths = [p for folder in ('cases', 'evidence')
                 for p in (root / folder).rglob('*') if p.is_file()]
        paths += [root / 'inventory.json', root / 'coverage.json']
        value = {'schema_version': 1, 'case_ids': sorted(case_ids),
                 'files': {str(p.relative_to(root)): replay.sha256(p) for p in sorted(paths)}}
        (root / 'manifest.json').write_text(json.dumps(value))

    @staticmethod
    def identity():
        return {'sha256': 'a' * 64, 'python_files': {}}

    def run_small(self, root, cases, registry):
        with patch.object(replay, 'handlers', return_value=registry), \
                patch.object(replay, 'implementation_identity', side_effect=self.identity):
            return replay.run_cases(root, cases)

    def test_fixture_path_rejects_parent_and_absolute_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'fixture'
            self.fixture(root)
            self.assertEqual(replay.fixture_path(root, 'evidence/small.json'),
                             root / 'evidence/small.json')
            for path in ('../outside.json', 'evidence/../../outside.json', '/tmp/outside.json'):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    replay.fixture_path(root, path)

    def test_fixture_path_rejects_file_and_directory_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'fixture'
            self.fixture(root)
            (root / 'evidence' / 'alias.json').symlink_to(root / 'evidence' / 'small.json')
            (root / 'alias-dir').symlink_to(root / 'evidence', target_is_directory=True)
            for path in ('evidence/alias.json', 'alias-dir/small.json'):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    replay.fixture_path(root, path)

    def test_evidence_hash_tampering_fails_without_consulting_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'fixture'
            self.fixture(root)
            (root / 'evidence' / 'small.json').write_text('{"observed":"changed"}\n')
            with self.assertRaisesRegex(ValueError, 'evidence changed'):
                replay.load_cases(root)

    def test_case_evidence_cannot_escape_package_even_with_correct_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'fixture'
            case = self.fixture(root)
            outside = Path(tmp) / 'outside.json'
            outside.write_text('outside')
            case['evidence'][0].update(path='../outside.json', sha256=replay.sha256(outside))
            (root / 'cases' / 'tiny.json').write_text(json.dumps(case))
            with self.assertRaises(ValueError):
                replay.load_cases(root)

    def test_valid_manifest_covers_inventory_and_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'fixture'
            self.fixture(root)
            self.assertEqual([case['id'] for case in replay.verify(root)], ['tiny-incident'])
            manifest = json.loads((root / 'manifest.json').read_text())
            self.assertIn('inventory.json', manifest['files'])
            self.assertIn('coverage.json', manifest['files'])

    def test_manifest_rejects_added_or_missing_package_files(self):
        for mutation in ('extra-case', 'extra-evidence', 'missing-case', 'missing-evidence',
                         'missing-inventory', 'missing-coverage', 'nonexistent-manifest-entry'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / 'fixture'
                self.fixture(root)
                if mutation.startswith('extra-'):
                    folder = 'cases' if mutation == 'extra-case' else 'evidence'
                    (root / folder / 'unlisted.json').write_text('{}')
                elif mutation.startswith('missing-'):
                    name = {'missing-case': 'cases/tiny.json',
                            'missing-evidence': 'evidence/small.json',
                            'missing-inventory': 'inventory.json',
                            'missing-coverage': 'coverage.json'}[mutation]
                    (root / name).unlink()
                else:
                    manifest = json.loads((root / 'manifest.json').read_text())
                    manifest['files']['evidence/ghost.json'] = 'f' * 64
                    (root / 'manifest.json').write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    replay.verify(root)

    def test_manifest_rejects_inventory_or_coverage_tampering(self):
        for name in ('inventory.json', 'coverage.json'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / 'fixture'
                self.fixture(root)
                (root / name).write_text('{"tampered":true}')
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    replay.verify(root)

    def test_manifest_itself_must_not_be_a_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'fixture'
            self.fixture(root)
            outside = Path(tmp) / 'real-manifest.json'
            (root / 'manifest.json').rename(outside)
            (root / 'manifest.json').symlink_to(outside)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                replay.verify(root)

    def test_manifest_case_ids_must_match_and_case_ids_are_unique(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'fixture'
            self.fixture(root)
            self.reseal(root, ['different-id'])
            with self.assertRaisesRegex(ValueError, 'case ids differ'):
                replay.verify(root)
            shutil.copy2(root / 'cases' / 'tiny.json', root / 'cases' / 'duplicate.json')
            with self.assertRaisesRegex(ValueError, 'duplicate incident id'):
                replay.load_cases(root)

    def test_unknown_handler_is_recorded_as_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'fixture'
            case = self.fixture(root)
            result = self.run_small(root, [case], {})
            self.assertEqual((result['passed'], result['failed']), (0, 1))
            self.assertEqual(result['results'][0]['status'], 'failed')
            self.assertIn('test.tiny', result['results'][0]['error'])

    def test_handler_failure_is_recorded_and_remaining_replays_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'fixture'
            case = self.fixture(root)
            second = {**case['replays'][0], 'id': 'second', 'handler': 'test.pass'}
            case['replays'].append(second)
            def fail(*_args):
                raise AssertionError('independent oracle failed')
            report = self.run_small(root, [case], {'test.tiny': fail,
                'test.pass': lambda *_args: {'value': 2}})
            self.assertEqual((report['passed'], report['failed']), (1, 1))
            self.assertEqual(report['results'][0]['error_type'], 'AssertionError')
            self.assertEqual(report['results'][0]['error'], 'independent oracle failed')
            self.assertEqual(report['results'][1]['measurements'], {'value': 2})
            self.assertIn('AssertionError', report['results'][0]['traceback'])
            json.dumps(report)

    def test_handler_must_return_measurement_object(self):
        for value in (None, [], 'not-measurements'):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / 'fixture'
                case = self.fixture(root)
                report = self.run_small(root, [case], {'test.tiny': lambda *_args: value})
                self.assertEqual((report['passed'], report['failed']), (0, 1))

    def test_non_json_measurements_fail_without_poisoning_the_result_report(self):
        for value in ({'path': Path('/not-serializable')}, {'set': {1}}, {'nan': float('nan')}):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / 'fixture'
                case = self.fixture(root)
                report = self.run_small(root, [case], {'test.tiny': lambda *_args: value})
                self.assertEqual((report['passed'], report['failed']), (0, 1))
                json.dumps(report, allow_nan=False)

    def test_optimized_python_cannot_silently_disable_replay_oracles(self):
        command = ('from pathlib import Path; '
                   'from regen.tools.replay_incidents import run_cases; '
                   'run_cases(Path("."), [])')
        result = subprocess.run([sys.executable, '-O', '-c', command], capture_output=True,
            text=True, cwd=Path(replay.__file__).resolve().parents[2], timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('without -O or PYTHONOPTIMIZE', result.stderr)

    def test_case_rejects_duplicate_replay_ids_and_empty_oracle(self):
        for mutation in ('duplicate', 'empty-oracle'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / 'fixture'
                case = self.fixture(root)
                if mutation == 'duplicate':
                    case['replays'].append(deepcopy(case['replays'][0]))
                else:
                    case['replays'][0]['oracle'] = {}
                (root / 'cases' / 'tiny.json').write_text(json.dumps(case))
                with self.assertRaises(ValueError):
                    replay.load_cases(root)

    def test_handlers_receive_independent_case_copies_and_disposable_scratch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'fixture'
            case = self.fixture(root)
            before = deepcopy(case)
            scratch_paths = []
            def measure(payload, fixture_root, scratch):
                self.assertEqual(fixture_root, root)
                self.assertEqual(payload['_replay']['parameters']['sentinel'], 1)
                payload['replays'][0]['parameters']['sentinel'] = 99
                payload['_replay']['parameters']['sentinel'] = 88
                (scratch / 'temporary-result').write_text('test')
                scratch_paths.append(scratch)
                return {'scratch_writable': True}
            report = self.run_small(root, [case], {'test.tiny': measure})
            self.assertEqual(report['failed'], 0)
            self.assertEqual(case, before)
            self.assertFalse(scratch_paths[0].exists())

    def test_compare_reports_regression_missing_and_added_results(self):
        before = {'fixture_manifest_sha256': 'fixture-a', 'implementation': {'sha256': 'old'},
            'results': [{'case_id': 'c', 'replay_id': 'stable', 'status': 'passed',
                         'measurements': {'count': 1}},
                        {'case_id': 'c', 'replay_id': 'removed', 'status': 'passed'}]}
        after = {'fixture_manifest_sha256': 'fixture-b', 'implementation': {'sha256': 'new'},
            'results': [{'case_id': 'c', 'replay_id': 'stable', 'status': 'failed'},
                        {'case_id': 'c', 'replay_id': 'added', 'status': 'passed',
                         'measurements': {'count': 2}}]}
        result = replay.compare(before, after)
        self.assertFalse(result['same_fixture_manifest'])
        self.assertEqual((result['before_implementation'], result['after_implementation']),
                         ('old', 'new'))
        indexed = {r['replay_id']: r for r in result['results']}
        self.assertEqual((indexed['stable']['before_status'], indexed['stable']['after_status']),
                         ('passed', 'failed'))
        self.assertEqual(indexed['removed']['after_status'], 'missing')
        self.assertEqual(indexed['added']['before_status'], 'missing')
        self.assertEqual(indexed['stable']['before_measurements'], {'count': 1})
        self.assertEqual(result['regressions'], [['c', 'removed'], ['c', 'stable']])

    def test_compare_rejects_duplicate_result_identity(self):
        row = {'case_id': 'c', 'replay_id': 'r', 'status': 'passed'}
        report = {'fixture_manifest_sha256': 'same', 'implementation': {'sha256': 'impl'},
                  'results': [row, deepcopy(row)]}
        with self.assertRaisesRegex(ValueError, 'duplicate result identity'):
            replay.compare(report, report)

    def test_compare_cli_exit_codes_distinguish_regression_and_changed_fixture(self):
        for mode, expected_code in (('unchanged', 0), ('regressed', 1), ('different-fixture', 2)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                before = {'fixture_manifest_sha256': 'same', 'implementation': {'sha256': 'old'},
                    'results': [{'case_id': 'c', 'replay_id': 'r', 'status': 'passed'}]}
                after = deepcopy(before)
                after['implementation']['sha256'] = 'new'
                if mode != 'unchanged':
                    after['results'][0]['status'] = 'failed'
                if mode == 'different-fixture':
                    after['fixture_manifest_sha256'] = 'changed'
                old_path, new_path = Path(tmp) / 'before.json', Path(tmp) / 'after.json'
                old_path.write_text(json.dumps(before))
                new_path.write_text(json.dumps(after))
                output = io.StringIO()
                with patch('sys.argv', ['replay_incidents', 'compare', str(old_path), str(new_path)]), \
                        redirect_stdout(output):
                    code = replay.main()
                self.assertEqual(code, expected_code)
                result = json.loads(output.getvalue())
                self.assertEqual(result['same_fixture_manifest'], mode != 'different-fixture')

    def test_nested_manifest_is_bound_by_root_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'fixture'
            self.fixture(root)
            nested = root / 'evidence' / 'manifest.json'
            nested.write_text('{"historical_manifest":true}')
            self.reseal(root, ['tiny-incident'])
            replay.verify(root)
            nested.write_text('{"historical_manifest":false}')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                replay.verify(root)

    def test_cli_failure_exit_code_and_persisted_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'fixture'
            self.fixture(root)
            destination = Path(tmp) / 'report.json'
            argv = ['replay_incidents', '--fixtures', str(root), 'run', '--output', str(destination)]
            with patch('sys.argv', argv), patch.object(replay, 'handlers', return_value={}), \
                    patch.object(replay, 'implementation_identity', side_effect=self.identity), \
                    redirect_stdout(io.StringIO()):
                code = replay.main()
            report = json.loads(destination.read_text())
            self.assertEqual(code, 1)
            self.assertEqual((report['passed'], report['failed']), (0, 1))

    def test_export_contains_independent_evidence_after_original_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'fixture'
            case = self.fixture(root)
            destination = Path(tmp) / 'export'
            argv = ['replay_incidents', '--fixtures', str(root), 'export', case['id'],
                    '--output', str(destination)]
            with patch('sys.argv', argv), redirect_stdout(io.StringIO()):
                self.assertEqual(replay.main(), 0)
            shutil.rmtree(root)
            exported = json.loads((destination / 'cases' / 'case.json').read_text())
            self.assertEqual(exported, case)
            for item in exported['evidence']:
                p = destination / item['path']
                self.assertFalse(p.is_symlink())
                self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(), item['sha256'])
                self.assertEqual(json.loads(p.read_text()), {'observed': 'failure'})
            verified = replay.verify(destination)
            self.assertEqual([c['id'] for c in verified], [case['id']])
            def measure(_case, fixture_root, _scratch):
                observed = json.loads((fixture_root / 'evidence' / 'small.json').read_text())
                self.assertEqual(observed, {'observed': 'failure'})
                return {'independent_evidence': True}
            report = self.run_small(destination, verified, {'test.tiny': measure})
            self.assertEqual((report['passed'], report['failed']), (1, 0))


if __name__ == '__main__':
    unittest.main()
