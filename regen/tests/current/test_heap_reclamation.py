"""Sealed-cache reclamation preserves all durable proof evidence."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest, durable
from regen.infrastructure.resource_budget import reclaim_theory_heaps


class HeapReclamationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.theory = self.root / 'theories' / 'Target'
        self.heaps = self.theory / 'heaps'
        self.heaps.mkdir(parents=True)
        (self.heaps / 'Generated_Refine').write_bytes(b'rebuildable heap')
        (self.root / 'heaps').mkdir()
        (self.root / 'heaps' / 'Semantic_Base').write_text('parent heap')
        for name in ('source.thy', 'check.log'):
            (self.theory / name).write_text('proof evidence')
        self.seal()

    def seal(self, *, status='sealed', event='seal'):
        body = {'sequence': 1, 'parent': None, 'binding': {'run_id': 'test'},
                'event': event, 'state': {'status': status}, 'evidence': {}}
        self.commit = {**body, 'commit_id': digest(body)}
        durable(self.theory / 'acceptance' / 'commits' / '000001.json', self.commit)

    def test_reclaims_only_owned_heaps_and_persists_commit_bound_result(self):
        originals = {path: path.read_bytes() for path in self.root.rglob('*')
                     if path.is_file() and not path.is_relative_to(self.heaps)}
        result = reclaim_theory_heaps(self.root, 'Target')
        self.assertEqual(result['status'], 'reclaimed')
        self.assertTrue(result['heap_removed'])
        self.assertFalse(self.heaps.exists())
        self.assertEqual(result['commit_id'], self.commit['commit_id'])
        self.assertEqual(json.loads(Path(result['audit_path']).read_text()), result)
        for path, data in originals.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertEqual(reclaim_theory_heaps(self.root, 'Target')['status'], 'absent')

    def test_unsealed_or_bad_commit_does_not_delete_cache(self):
        self.seal(status='generating', event='document')
        self.assertEqual(reclaim_theory_heaps(self.root, 'Target')['status'], 'warning')
        self.assertTrue(self.heaps.exists())
        self.seal()
        self.commit['commit_id'] = 'forged'
        durable(self.theory / 'acceptance' / 'commits' / '000001.json', self.commit)
        self.assertEqual(reclaim_theory_heaps(self.root, 'Target')['status'], 'warning')
        self.assertTrue(self.heaps.exists())

    def test_traversal_and_symlink_theory_are_rejected(self):
        for name in ('..', '../Target', '/Target', 'a\\b', ''):
            self.assertEqual(reclaim_theory_heaps(self.root, name)['status'], 'warning')
        (self.root / 'theories' / 'Alias').symlink_to(self.theory, target_is_directory=True)
        self.assertEqual(reclaim_theory_heaps(self.root, 'Alias')['status'], 'warning')
        self.assertTrue(self.heaps.exists())

    def test_symlink_heap_or_entry_is_rejected_without_touching_target(self):
        renamed = self.theory / 'original-heaps'
        self.heaps.rename(renamed)
        self.heaps.symlink_to(renamed, target_is_directory=True)
        self.assertEqual(reclaim_theory_heaps(self.root, 'Target')['status'], 'warning')
        self.assertTrue((renamed / 'Generated_Refine').exists())
        self.heaps.unlink()
        renamed.rename(self.heaps)
        (self.heaps / 'external').symlink_to(self.root / 'heaps', target_is_directory=True)
        self.assertEqual(reclaim_theory_heaps(self.root, 'Target')['status'], 'warning')
        self.assertEqual((self.root / 'heaps' / 'Semantic_Base').read_text(), 'parent heap')

    def test_failed_audit_prevents_any_deletion(self):
        with patch('regen.execution.proof_commits.durable', side_effect=OSError('audit disk full')):
            result = reclaim_theory_heaps(self.root, 'Target')
        self.assertEqual(result['status'], 'warning')
        self.assertFalse(result['heap_removed'])
        self.assertTrue(self.heaps.exists())

    def test_failed_removal_is_audited_as_warning_and_retry_uses_unique_path(self):
        with patch('regen.infrastructure.resource_budget.shutil.rmtree', side_effect=OSError('busy cache')):
            first = reclaim_theory_heaps(self.root, 'Target')
        self.assertEqual(first['status'], 'warning')
        self.assertFalse(first['heap_removed'])
        self.assertTrue(self.heaps.exists())
        self.assertEqual(json.loads(Path(first['audit_path']).read_text())['status'], 'warning')
        second = reclaim_theory_heaps(self.root, 'Target')
        self.assertEqual(second['status'], 'reclaimed')
        self.assertNotEqual(first['audit_path'], second['audit_path'])
        self.assertEqual(json.loads(Path(first['audit_path']).read_text())['status'], 'warning')

    def test_result_audit_failure_does_not_claim_success(self):
        original = durable

        def fail_result(path, record, **kwargs):
            if record.get('status') != 'planned':
                raise OSError('result audit full')
            return original(path, record, **kwargs)

        with patch('regen.execution.proof_commits.durable', side_effect=fail_result):
            result = reclaim_theory_heaps(self.root, 'Target')
        self.assertEqual(result['status'], 'warning')
        self.assertTrue(result['heap_removed'])
        self.assertFalse(self.heaps.exists())
        self.assertEqual(json.loads(Path(result['audit_path']).read_text())['status'], 'planned')


if __name__ == '__main__':
    unittest.main()
