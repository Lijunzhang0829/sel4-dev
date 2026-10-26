"""Diagnostic transport has no proof authority and does not duplicate large logs."""
import copy
import gzip
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from regen.tools.vero_serial_application import Service, frozen_plan_binding

VERO_SOURCE = Path(__file__).resolve().parents[3] / 'vero-isabelle' / 'src'
sys.path.insert(0, str(VERO_SOURCE))
from vero.isabelle.benchmark import prepare, render, extract
from vero.isabelle.planning import compile_plan


class VeroDiagnosticServiceTests(unittest.TestCase):
    request_id = 'a' * 32

    def fixture(self, root, *, completed=True, compressed=False):
        service = object.__new__(Service)
        service.output = root / 'requests'
        service.modules = {'Vero_A'}
        forbidden = Mock(side_effect=AssertionError('diagnostic read mutated proof state'))
        observer = SimpleNamespace(stop=SimpleNamespace(check=Mock()),
            reconcile_contracts=forbidden, select_theory=forbidden, ledger=forbidden)
        service.checker = SimpleNamespace(observer=observer, check=forbidden)
        service.verify_plan = forbidden
        service.extract = forbidden
        attempt = service.output / self.request_id
        (attempt / 'evaluation').mkdir(parents=True)
        (attempt / 'evaluation' / 'Vero_A.log').write_text('start 中🦊\nend\n', encoding='utf-8')
        if completed:
            if compressed:
                with gzip.open(attempt / 'response.json.gz', 'wt', encoding='utf-8') as stream:
                    json.dump({'modules': {'Vero_A': {'status':'build_error'}}}, stream)
            else:
                (attempt / 'response.json').write_text('{}\n')
        request = {'cmd':'diagnostic', 'request_id':self.request_id, 'module':'Vero_A'}
        return service, attempt, request, forbidden

    def test_paging_is_read_only_and_retains_exact_original_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, attempt, request, forbidden = self.fixture(Path(tmp))
            before = {str(p.relative_to(attempt)):p.read_bytes()
                      for p in attempt.rglob('*') if p.is_file()}
            offset, chunks = 0, []
            with patch('regen.tools.vero_serial_application.snapshot', forbidden):
                while True:
                    page = service.dispatch({**request, 'offset':offset, 'limit':2})
                    self.assertEqual(page['request_id'], self.request_id)
                    self.assertEqual(page['module'], 'Vero_A')
                    chunks.append(page['text'])
                    if page['eof']:
                        break
                    offset = page['next_offset']
            self.assertEqual(''.join(chunks), 'start 中🦊\nend\n')
            after = {str(p.relative_to(attempt)):p.read_bytes()
                     for p in attempt.rglob('*') if p.is_file()}
            self.assertEqual(before, after)
            forbidden.assert_not_called()

    def test_unknown_module_and_request_path_traversal_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, _, request, forbidden = self.fixture(Path(tmp))
            invalid = [
                {**request, 'module':'Unknown'}, {**request, 'module':'../Vero_A'},
                {**request, 'request_id':'../' + self.request_id},
                {**request, 'request_id':'/' + self.request_id},
                {**request, 'request_id':None}, {**request, 'module':['Vero_A']},
            ]
            for item in invalid:
                with self.subTest(request=item), self.assertRaisesRegex(ValueError, 'invalid diagnostic reference'):
                    service.dispatch(item)
            forbidden.assert_not_called()

    def test_request_without_completion_marker_is_not_exposed(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, _, request, forbidden = self.fixture(Path(tmp), completed=False)
            with self.assertRaisesRegex(ValueError, 'not complete'):
                service.dispatch(request)
            forbidden.assert_not_called()

    def test_archived_response_marker_retains_read_access_to_full_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, _, request, forbidden = self.fixture(Path(tmp), compressed=True)
            page = service.dispatch(request)
            self.assertEqual(page['text'], 'start 中🦊\nend\n')
            self.assertTrue(page['eof'])
            forbidden.assert_not_called()

    def test_symlink_at_every_reference_component_is_rejected(self):
        for component in ('request', 'evaluation', 'response.json', 'Vero_A.log'):
            with self.subTest(component=component), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                service, attempt, request, forbidden = self.fixture(root)
                source = (attempt if component == 'request' else
                          attempt / 'evaluation' if component == 'evaluation' else
                          attempt / 'evaluation' / component if component.endswith('.log') else
                          attempt / component)
                target = root / 'other'
                source.rename(target)
                source.symlink_to(target, target_is_directory=target.is_dir())
                with self.assertRaises((OSError, ValueError)):
                    service.dispatch(request)
                forbidden.assert_not_called()

    def test_check_preserves_verdicts_and_large_native_log_but_bounds_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inventory = root / 'contracts.jsonl'
            inventory.write_text(json.dumps({'contract_id':'fixture-c0001',
                'name':'tiny', 'owner_theory':'A', 'exact_statement':'lemma tiny: "True"'}) + '\n')
            source_bench = prepare(inventory, root / 'source-benchmark', base_import='Main')
            plan = {'version':1, 'source_benchmark_id':source_bench['benchmark_id'],
                'modules':[{'name':'Vero_A', 'contracts':['c0001'], 'imports':[], 'rationale':'fixture'}],
                'execution_order':['Vero_A']}
            bench = compile_plan(source_bench, plan)
            workspace = root / 'workspace'
            workspace.mkdir()
            render(bench, workspace, {'c0001:prove':'by simp'})
            source_before = (workspace / 'Vero_A.thy').read_bytes()
            service = object.__new__(Service)
            service.bench = bench
            service.workspace = workspace
            service.dataset = root
            service.output = root / 'requests'
            service.modules = {'Vero_A'}
            service.frozen_plan = frozen_plan_binding(bench, plan)
            service.checker = SimpleNamespace(observer=None)
            service.extract = extract
            verdict = {'contracts':{'c0001':{'status':'build_error', 'polarity':'prove'}},
                'modules':{'Vero_A':{'status':'build_error', 'returncode':1, 'audited':[]}},
                'complete':False, 'proved_specs':0, 'integration':None}
            original = copy.deepcopy(verdict)
            evidence = {}
            def evaluate(_bench, _artifact, output, _checker):
                output.mkdir()
                log = output / 'Vero_A.log'
                with log.open('wb') as stream:
                    stream.write(b'BEGIN real native feedback\n')
                    stream.seek(800 * 1024 * 1024)
                    stream.write(b'\nEND failed proof\n')
                evidence['path'] = log
                evidence['stat'] = log.stat()
                return copy.deepcopy(verdict)
            with patch('vero.isabelle.checker.evaluate', side_effect=evaluate) as mocked:
                result = service.dispatch({'cmd':'check', 'module':'Vero_A'})
            mocked.assert_called_once()
            for key in original:
                self.assertEqual(result[key], original[key])
            wire = json.dumps(result).encode('utf-8')
            self.assertLess(len(wire), 400000)
            metadata = result['diagnostics_metadata']['Vero_A']
            self.assertTrue(metadata['truncated'])
            self.assertEqual(metadata['module'], 'Vero_A')
            self.assertIn(metadata['request_id'], metadata['read_command'])
            self.assertEqual((workspace / 'Vero_A.thy').read_bytes(), source_before)
            self.assertEqual(evidence['path'].stat().st_size, evidence['stat'].st_size)
            self.assertEqual(evidence['path'].stat().st_mtime_ns, evidence['stat'].st_mtime_ns)
            persisted = evidence['path'].parent.parent / 'response.json'
            self.assertLess(persisted.stat().st_size, 400000)
            page = service.dispatch({'cmd':'diagnostic', 'request_id':metadata['request_id'],
                'module':'Vero_A', 'offset':metadata['total_bytes'] - 18})
            self.assertTrue(page['eof'])
            self.assertTrue(page['text'].endswith('END failed proof\n'))


if __name__ == '__main__':
    unittest.main()
