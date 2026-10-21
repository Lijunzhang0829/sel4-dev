import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.native_check_receipt import checker_binding, native_check_receipt


class FakeChecker:
    image = 'sha256:image'
    base_session = 'Semantic_Base'
    base_import = 'Semantic_Base.Semantic_Root'
    previous_theories = []
    architecture = 'ARM'
    checker_context_id = 'context-v1'

    def imports(self):
        return [self.base_import]


class NativeCheckReceiptTests(unittest.TestCase):
    def test_cold_result_binds_source_log_context_and_timeout_scope(self):
        checker = FakeChecker()
        result = {'log': '/unused', 'wall_ms': 1120, 'timeout_seconds': 1,
                  'timeout_scope': 'cold-full-theory', 'returncode': 124,
                  'verdict': 'red'}
        row = native_check_receipt(checker, result, source='theory Demo',
                                   scope='cold-full-theory', log_text='timed out',
                                   request_id='req-1', candidate_execution_ms=1120)
        self.assertEqual(row['source_sha256'], hashlib.sha256(b'theory Demo').hexdigest())
        self.assertEqual(row['log_sha256'], hashlib.sha256(b'timed out').hexdigest())
        self.assertEqual(row['checker_binding_sha256'], checker_binding(checker))
        self.assertEqual(row['timeout_scope'], 'cold-full-theory')
        self.assertEqual(row['candidate_execution_ms'], 1120)

    def test_incremental_result_keeps_prefix_and_delta_distinct(self):
        checker = FakeChecker()
        result = {'log': '/unused', 'wall_ms': 32, 'timeout_seconds': 120,
                  'timeout_scope': None, 'returncode': 0, 'verdict': 'green'}
        row = native_check_receipt(checker, result, source='prefix + suffix',
                                   scope='committed-prefix-and-incremental-batch',
                                   log_text=b'GREEN', request_id='req-2',
                                   prefix_sha256='a' * 64, delta_sha256='b' * 64,
                                   prefix_prepare_ms=50, candidate_execution_ms=32)
        self.assertEqual(row['prefix_sha256'], 'a' * 64)
        self.assertEqual(row['delta_sha256'], 'b' * 64)
        self.assertEqual(row['prefix_prepare_ms'], 50)
        self.assertEqual(row['verdict'], 'green')

    def test_session_build_receipt_binds_mounted_imported_theories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated = root / 'generated'
            generated.mkdir()
            (generated / 'Parent.thy').write_text('theory Parent imports Main begin end\n')
            (generated / 'Child.thy').write_text('theory Child imports Parent begin end\n')
            checker = IsabelleChecker(root, root / 'work', generated_dir=generated,
                heaps_dir=root / 'heaps', l4v_dir=root / 'l4v',
                semantic_workspace=root / 'semantic', isabelle_dir=root / 'isabelle')
            response = subprocess.CompletedProcess([], 0, 'build succeeded', '')
            with patch('regen.execution.isabelle_node_checker.native_run', return_value=response):
                first = checker.build_generated_session(['Child'])
                (generated / 'Parent.thy').write_text(
                    'theory Parent imports Main begin lemma changed: "True" by simp end\n')
                second = checker.build_generated_session(['Child'])
            self.assertNotEqual(first['native_receipt']['source_sha256'],
                                second['native_receipt']['source_sha256'])
            self.assertEqual(first['native_receipt']['check_scope'],
                             'generated-session-build')
            self.assertEqual(second['returncode'], 0)

    def test_cold_receipt_binds_mounted_imported_theories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);generated=root/'generated';generated.mkdir()
            parent=generated/'Parent.thy'
            parent.write_text('theory Parent imports Main begin end\n')
            checker=IsabelleChecker(root,root/'work',generated_dir=generated,
                heaps_dir=root/'heaps',l4v_dir=root/'l4v',
                semantic_workspace=root/'semantic',isabelle_dir=root/'isabelle')
            response=subprocess.CompletedProcess([],0,'check succeeded','')
            with patch('regen.execution.isabelle_node_checker.native_run',return_value=response):
                first=checker._check('theory Child imports Parent begin end\n','Child')
                parent.write_text('theory Parent imports Main begin lemma changed: True by simp end\n')
                second=checker._check('theory Child imports Parent begin end\n','Child')
            self.assertNotEqual(first['native_receipt']['source_sha256'],
                second['native_receipt']['source_sha256'])
            self.assertNotEqual(first['native_receipt']['source_files_sha256']['generated/Parent.thy'],
                second['native_receipt']['source_files_sha256']['generated/Parent.thy'])
            self.assertEqual(first['native_receipt']['source_files_sha256']['candidate/Child.thy'],
                second['native_receipt']['source_files_sha256']['candidate/Child.thy'])

    def test_session_build_rejects_import_change_during_native_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated = root / 'generated'
            generated.mkdir()
            parent = generated / 'Parent.thy'
            parent.write_text('theory Parent imports Main begin end\n')
            (generated / 'Child.thy').write_text(
                'theory Child imports Parent begin end\n')
            checker = IsabelleChecker(root, root / 'work', generated_dir=generated,
                heaps_dir=root / 'heaps', l4v_dir=root / 'l4v',
                semantic_workspace=root / 'semantic', isabelle_dir=root / 'isabelle')

            def changed_during_build(*args, **kwargs):
                parent.write_text('theory Parent imports Main begin lemma changed: "True" by simp end\n')
                return subprocess.CompletedProcess([], 0, 'build succeeded', '')

            with patch('regen.execution.isabelle_node_checker.native_run',
                       side_effect=changed_during_build):
                with self.assertRaisesRegex(ValueError, 'session build inputs changed'):
                    checker.build_generated_session(['Child'])


if __name__ == '__main__':
    unittest.main()
