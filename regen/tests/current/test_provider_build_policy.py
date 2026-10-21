"""Provider cold-build limits must not enlarge proof or seal budgets."""
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources
from regen.execution.isabelle_node_checker import IsabelleChecker


class ProviderBuildPolicyTests(unittest.TestCase):
    def test_default_and_custom_memory_headroom(self):
        policy = NativeResources().policy
        self.assertEqual(policy['provider_build_timeout'], 1800)
        self.assertEqual(policy['build_ml_heap_mb'], 12288)
        self.assertEqual(policy['build_java_heap_mb'], 2048)
        self.assertLess(policy['build_ml_heap_mb'] + policy['build_java_heap_mb'], policy['memory_mb'])
        self.assertEqual(NativeResources(memory_mb=1024).policy['build_ml_heap_mb'], 768)
        self.assertEqual(NativeResources(build_ml_heap_mb=8000).policy['build_ml_heap_mb'], 8000)

    def test_reject_invalid_limits(self):
        for value in (0, -1, True, float('inf'), float('nan'), '1800'):
            with self.subTest(timeout=value), self.assertRaises(ValueError):
                NativeResources(provider_build_timeout=value)
        for value in (0, -1, True, 15000, 12288.0):
            with self.subTest(heap=value), self.assertRaises(ValueError):
                NativeResources(build_ml_heap_mb=value)

    def test_provider_only_budget_and_post_settings_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checker = IsabelleChecker(root, root/'check', heaps_dir=root,
                l4v_dir=root, semantic_workspace=root, timeout=900)
            (checker.generated_dir/'X.thy').write_text('theory X imports Main begin end')
            pool = NativeResources(provider_build_timeout=1800)
            marker = CURRENT_RESOURCES.set(pool)
            try:
                with patch('regen.execution.isabelle_node_checker.native_run',
                           return_value=CompletedProcess([], 0, '', '')) as launch:
                    result = checker.build_generated_session(['X'])
                    self.assertEqual(launch.call_args.kwargs['timeout'], 1800)
                    command = launch.call_args.args[0]
                    executable = '/workspace/verification/isabelle/bin/isabelle'
                    self.assertEqual(command.count(executable), 2)
                    self.assertEqual(command[command.index(executable)+1], 'env')
                    self.assertIn('ML_OPTIONS=-H 1024 --maxheap 12288 --stackspace 64 --gcthreads 4', command)
                    self.assertEqual(command[command.index('build')+1:command.index('build')+5], ['-j','1','-o','threads=4'])
                    self.assertEqual(result['timeout_seconds'], 1800)
                    self.assertEqual(result['resource_policy'], pool.policy)
                    checker.build_sealed_session(['X'])
                    self.assertEqual(launch.call_args.kwargs['timeout'], 900)
                    checker._check('theory X imports Main begin end', 'X', timeout=120)
                    self.assertEqual(launch.call_args.kwargs['timeout'], 120)
                    self.assertEqual(checker.timeout, 900)
            finally:
                pool.close()
                CURRENT_RESOURCES.reset(marker)

    def test_unmanaged_build_preserves_legacy_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checker = IsabelleChecker(root, root/'check', heaps_dir=root,
                l4v_dir=root, semantic_workspace=root, timeout=91)
            (checker.generated_dir/'X.thy').write_text('theory X imports Main begin end')
            with patch('regen.execution.isabelle_node_checker.native_run',
                       return_value=CompletedProcess([], 0, '', '')) as launch:
                result = checker.build_generated_session(['X'])
            self.assertEqual(launch.call_args.kwargs['timeout'], 91)
            self.assertIsNone(result['resource_policy'])

    def test_provider_timeout_receipt_uses_its_own_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checker = IsabelleChecker(root, root/'check', heaps_dir=root,
                l4v_dir=root, semantic_workspace=root, timeout=900)
            (checker.generated_dir/'X.thy').write_text('theory X imports Main begin end')
            pool = NativeResources(provider_build_timeout=1700)
            marker = CURRENT_RESOURCES.set(pool)
            try:
                with patch('regen.execution.isabelle_node_checker.native_run',
                           side_effect=TimeoutExpired([], 1700)), patch(
                           'regen.execution.isabelle_node_checker.subprocess.run') as cleanup:
                    result = checker.build_generated_session(['X'])
                self.assertEqual(result['returncode'], 124)
                self.assertEqual(result['timeout_seconds'], 1700)
                self.assertIn('after 1700 seconds', result['diagnostic'])
                self.assertEqual(cleanup.call_args.args[0][:3], ['docker','rm','-f'])
            finally:
                pool.close()
                CURRENT_RESOURCES.reset(marker)
