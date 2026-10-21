"""Concurrent theories must never share mutable checker resources."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from regen.execution.repository_execution import checker_for, setup
from regen.infrastructure.path_identity import source_identity


class CheckerIsolationTest(unittest.TestCase):
    def test_setup_builds_frozen_transitive_providers_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installation = root / 'isabelle'
            installation.mkdir()
            (root / 'heaps').mkdir()
            (root / 'generated').mkdir()
            run = {
                'execution_input_kind': 'refine-dataset', 'implementation': {},
                'checker_context_root': str(root),
                'isabelle_dir': str(installation),
                'isabelle_sha256': source_identity(installation, allow_links=True),
                'l4v': str(root), 'semantic_workspace': str(root),
                'base_session': 'Semantic_Base',
                'base_import': 'Semantic_Base.Semantic_Root',
                'base_catalog': '/semantic-base', 'image': 'test-image',
            }
            states = [{'theory': name, 'file_slot': name + '.thy',
                       'status': 'pending' if name == 'C' else 'sealed'}
                      for name in ('A', 'B', 'C', 'Unrelated')]
            (root / 'run.json').write_text(json.dumps(run))
            (root / 'execution-input.json').write_text(json.dumps({'files': states}))
            (root / 'generated-premises.json').write_text('{"records": []}')
            for name, imports in [('A', []), ('B', ['Regen_A'])]:
                directory = root / 'theories' / name
                directory.mkdir(parents=True)
                (directory / 'imports.json').write_text(json.dumps(imports))
                (root / 'generated' / ('Regen_' + name + '.thy')).write_text(name)
            (root / 'generated' / 'Regen_Unrelated.thy').write_text('unrelated')
            package = {'file_slot': 'C.thy', 'repository_projection': {
                'schema_version': 5, 'required_imports': [{'theory': 'B'}],
            }}
            with patch('regen.execution.repository_execution.progress',
                       return_value={'files': states}), \
                 patch('regen.execution.repository_execution.package_for', return_value=package), \
                 patch('regen.execution.repository_execution.journal'), \
                 patch('regen.execution.isabelle_node_checker.IsabelleChecker.build_generated_session',
                       return_value={'verdict': 'green'}) as build:
                states[1]['status'] = 'retired'
                with self.assertRaisesRegex(ValueError, 'providers are not sealed'):
                    setup(root, 'C', 10)
                states[1]['status'] = 'sealed'
                checker = setup(root, 'C', 10)[4]
                self.assertEqual(checker.imports(), ['Generated_Refine.Regen_B'])
                self.assertEqual(sorted(p.name for p in checker.generated_dir.glob('*.thy')),
                                 ['Regen_A.thy', 'Regen_B.thy'])
                build.assert_called_once_with(['Regen_B'])
                again = setup(root, 'C', 10)[4]
                self.assertEqual(again.heaps_dir, checker.heaps_dir)
                build.assert_called_once()

    def test_retired_required_provider_blocks_serial_and_parallel_scheduling(self):
        from regen.execution.repository_execution import run_session
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'run.json').write_text(json.dumps({'session_schedule':{'source':str(root/'schedule.json')}}))
            (root/'schedule.json').write_text(json.dumps({'schema_version':6}))
            states = [{'theory':'Provider','file_slot':'Provider.thy','status':'retired','order':0},
                      {'theory':'Consumer','file_slot':'Consumer.thy','status':'pending','order':1}]
            (root/'execution-input.json').write_text(json.dumps({'files':states}))
            package = {'repository_projection':{'schema_version':5,
                       'required_imports':[{'theory':'Provider','reason':'Needed'}]}}
            with patch('regen.execution.repository_execution.progress', return_value={'files':states}), \
                 patch('regen.execution.repository_execution.package_for', return_value=package), \
                 patch('regen.execution.repository_execution.run_file') as run_file:
                for workers in (1, 2):
                    result = run_session(root, max_parallel_theories=workers)
                    self.assertEqual(result['blocked_theories'], ['Consumer'])
                run_file.assert_not_called()

    def test_concurrent_builds_and_checks_use_distinct_resources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installation = root / 'isabelle'
            installation.mkdir()
            run = {
                'execution_input_kind': 'refine-dataset',
                'checker_context_root': str(root),
                'isabelle_dir': str(installation),
                'isabelle_sha256': source_identity(installation, allow_links=True),
                'l4v': str(root), 'semantic_workspace': str(root),
                'base_session': 'Semantic_Base',
                'base_import': 'Semantic_Base.Semantic_Root',
                'base_catalog': '/semantic-base', 'image': 'test-image',
            }
            checkers = [checker_for(root, run, root / name / 'isabelle', 10, [])
                        for name in ('A', 'B')]
            for checker in checkers:
                name = checker.generated_dir.parent.name
                (checker.generated_dir/(name+'.thy')).write_text('theory '+name+' imports Main begin end')
            self.assertNotEqual(checkers[0].heaps_dir, checkers[1].heaps_dir)
            self.assertNotEqual(checkers[0].generated_dir, checkers[1].generated_dir)
            barrier = threading.Barrier(2)
            names = []

            def execute(command, **kwargs):
                names.append(command[command.index('--name') + 1])
                generated = next(arg for arg in command
                                 if arg.startswith('type=bind,src=')
                                 and ',dst=/generated,' in arg)
                directory = Path(generated.split('src=', 1)[1].split(',dst=')[0])
                barrier.wait(timeout=5)
                if 'build' in command:
                    theory = directory.parent.name
                    self.assertIn('    ' + theory + '\n',
                                  (directory / 'ROOT').read_text())
                return subprocess.CompletedProcess(command, 0, 'checked', '')

            with patch('regen.execution.isabelle_node_checker.native_run',
                       side_effect=execute):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    builds = list(pool.map(
                        lambda checker: checker.build_generated_session(
                            [checker.generated_dir.parent.name]), checkers))
                    checks = list(pool.map(
                        lambda checker: checker._check('theory Test begin end', 'Test'),
                        checkers))
            self.assertEqual(len(names), len(set(names)))
            self.assertTrue(all(item['verdict'] == 'green' for item in builds + checks))
            self.assertNotEqual(builds[0]['log'], builds[1]['log'])
            self.assertFalse((root / 'generated' / 'ROOT').exists())


if __name__ == '__main__':
    unittest.main()
