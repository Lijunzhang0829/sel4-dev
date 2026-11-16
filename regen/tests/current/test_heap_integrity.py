import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from regen.infrastructure import heap_integrity as integrity
from regen.infrastructure import resource_budget as budget
from regen.execution.check_tasks import NativePreparationError


class HeapIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/'source'
        self.destination = self.root/'copy'
        (self.source/'platform').mkdir(parents=True)
        (self.source/'platform/ASpec').write_bytes(b'parent-image')
        self.expected = integrity.manifest(self.source)

    def clone(self):
        return budget.clone_heaps(self.source, self.destination, reserve_bytes=0)

    def verify(self):
        return integrity.verify_parents(self.source, self.destination, self.expected,
                                        receipt_dir=self.root/'evidence')

    def test_same_size_corruption_during_copy_is_not_published(self):
        copy = budget.shutil.copytree
        def corrupt(source, target, *args, **kwargs):
            copy(source, target, *args, **kwargs)
            if Path(source) == self.source:
                (Path(target)/'platform/ASpec').write_bytes(b'Parent-image')
        with patch.object(budget.shutil, 'copytree', side_effect=corrupt):
            with self.assertRaises(NativePreparationError):
                self.clone()
        self.assertFalse(self.destination.exists())

    def test_bound_parent_restored_and_child_untouched(self):
        receipt = self.clone()
        self.assertEqual(receipt['inherited_heap_sha256'], self.expected)
        target = self.destination/'platform/ASpec'
        target.write_bytes(b'Parent-image')
        child = self.destination/'platform/Generated_Refine'
        child.write_bytes(b'child-image')
        repairs = self.verify()
        self.assertEqual(target.read_bytes(), b'parent-image')
        self.assertEqual(Path(repairs[0]['quarantine']).read_bytes(), b'Parent-image')
        self.assertEqual(child.read_bytes(), b'child-image')
        self.assertEqual(self.verify(), [])

    def test_source_corruption_never_repaired_into_destination(self):
        self.clone()
        (self.source/'platform/ASpec').write_bytes(b'Parent-image')
        with self.assertRaisesRegex(NativePreparationError, 'frozen parent'):
            self.verify()
        self.assertEqual((self.destination/'platform/ASpec').read_bytes(), b'parent-image')

    def test_corrupt_repair_copy_does_not_replace_image(self):
        self.clone()
        target = self.destination/'platform/ASpec'
        target.write_bytes(b'Parent-image')
        def corrupt(source, dest):
            Path(dest).write_bytes(b'broken')
        with patch.object(integrity.shutil, 'copyfile', side_effect=corrupt):
            with self.assertRaisesRegex(NativePreparationError, 'repair copy'):
                self.verify()
        self.assertEqual(target.read_bytes(), b'Parent-image')

    def test_symlink_rejected(self):
        self.clone()
        target = self.destination/'platform/ASpec'
        target.unlink()
        target.symlink_to(self.source/'platform/ASpec')
        with self.assertRaisesRegex(NativePreparationError, 'symlink'):
            self.verify()

    def test_log_database_is_not_an_immutable_parent(self):
        (self.source/'platform/log').mkdir()
        (self.source/'platform/log/ASpec.db').write_bytes(b'metadata')
        self.assertEqual(integrity.manifest(self.source), self.expected)

    def test_import_loader_crash_is_infrastructure_not_semantic_rejection(self):
        from regen.execution.import_semantics import _observe
        from types import SimpleNamespace
        check = {'returncode': 134, 'verdict': 'red', 'diagnostic':
                 "poly: savestate.cpp:1022: LoadRelocate::RelocateAddress: Assertion `0' failed."}
        checker = SimpleNamespace(imports=lambda: ['Base'], _check=lambda *args: check)
        with patch('regen.execution.import_semantics.freeze_evidence', side_effect=lambda x: x):
            with self.assertRaises(NativePreparationError):
                _observe(checker, {'contracts': []})
        integrity.require_loadable({'returncode': 1, 'diagnostic': 'Failed to finish proof'})


if __name__ == '__main__':
    unittest.main()
