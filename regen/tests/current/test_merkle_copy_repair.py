"""Recovery copies preserve authority and reject corrupt independent stores."""
from pathlib import Path
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

from regen.infrastructure import incremental_log as logs


class MerkleCopyRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.source=Path(self.tmp.name)/'source';self.target=Path(self.tmp.name)/'target'
        logs.enable(self.source);logs.enable(self.target)
        self.original=self.source/'batch-requests/0001/check.json'
        self.destination=self.target/'batch-requests/0001/check.json'
        self.value={'proof':'proof body '*2000,'feedback':{'source':'source '*2000}}
        logs.write_artifact(self.original,self.value,immutable=True)
        self.key=json.loads(self.original.read_text())['root']
        self.store=logs.ObjectStore(self.source)

    def test_copy_preserves_merkle_identity_without_expansion_or_reencoding(self):
        with patch.object(logs.ObjectStore,'decode',side_effect=AssertionError('expanded history')), \
                patch.object(logs.ObjectStore,'encode',side_effect=AssertionError('reencoded history')):
            logs.copy_logged_file(self.original,self.destination)
        self.assertEqual(self.original.read_bytes(),self.destination.read_bytes())
        shutil.rmtree(self.source)
        logs.clear_cache()
        self.assertEqual(logs.read_artifact(self.destination),self.value)

    def test_corrupt_reachable_source_never_publishes_destination(self):
        child=next(k for k,_ in self.store.fingerprint(self.key) if k!=self.key)
        self.store.path(child).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):logs.copy_logged_file(self.original,self.destination)
        self.assertFalse(self.destination.exists())

    def test_existing_corrupt_destination_cannot_borrow_source_authority(self):
        logs.copy_logged_file(self.original,self.destination)
        target=logs.ObjectStore(self.target)
        child=next(k for k,_ in target.fingerprint(self.key) if k!=self.key)
        target.path(child).write_bytes(b'corrupt')
        other=self.target/'batch-requests/0002/check.json'
        with self.assertRaises(ValueError):logs.copy_logged_file(self.original,other)
        self.assertFalse(other.exists())

    def test_existing_destination_manifest_collision_is_rejected(self):
        logs.write_artifact(self.destination,{'different':True},immutable=True)
        with self.assertRaisesRegex(ValueError,'immutable log collision'):
            logs.copy_logged_file(self.original,self.destination)

    def test_copied_store_rejects_a_symlink_instead_of_an_object(self):
        path=logs.ObjectStore(self.target).path(self.key)
        path.symlink_to(self.store.path(self.key))
        with self.assertRaisesRegex(ValueError,'symlinks'):
            logs.copy_logged_file(self.original,self.destination)
        self.assertFalse(self.destination.exists())

    def test_shared_objects_are_checked_once_per_batch_and_again_on_next_copy(self):
        original=logs.ObjectStore.references;counts={}
        def references(store,key):
            identity=(store.root,key);counts[identity]=counts.get(identity,0)+1
            return original(store,key)
        with patch.object(logs.ObjectStore,'references',references),logs.logged_copy_batch():
            logs.copy_logged_file(self.original,self.destination)
            logs.copy_logged_file(self.original,self.target/'batch-requests/0002/check.json')
        self.assertTrue(all(n==1 for n in counts.values()),counts)
        child=next(k for k,_ in self.store.fingerprint(self.key) if k!=self.key)
        self.store.path(child).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            logs.copy_logged_file(self.original,self.target/'batch-requests/0003/check.json')

    def test_source_mutation_during_batch_cannot_be_reported_as_success(self):
        child=next(k for k,_ in self.store.fingerprint(self.key) if k!=self.key)
        with self.assertRaisesRegex(ValueError,'changed during recovery copy'):
            with logs.logged_copy_batch():
                logs.copy_logged_file(self.original,self.destination)
                self.store.path(child).write_bytes(b'corrupt')

    def test_destination_mutation_during_batch_cannot_be_reported_as_success(self):
        with self.assertRaisesRegex(ValueError,'changed during recovery copy'):
            with logs.logged_copy_batch():
                logs.copy_logged_file(self.original,self.destination)
                logs.ObjectStore(self.target).path(self.key).write_bytes(b'corrupt')

    def test_recovery_must_complete_filesystem_sync_before_reporting_success(self):
        import subprocess
        with patch('subprocess.run',side_effect=subprocess.CalledProcessError(1,['sync','-f'])):
            with self.assertRaises(subprocess.CalledProcessError):
                logs.copy_logged_file(self.original,self.destination)

    def test_deferred_copy_durability_is_not_available_outside_recovery(self):
        with self.assertRaisesRegex(ValueError,'active recovery copy batch'):
            logs.atomic_bytes(self.target/'unsafe',b'bytes',_recovery_copy=True)

    def test_normal_journal_write_keeps_fsync_even_inside_copy_batch(self):
        import os
        with patch.object(os,'fsync',wraps=os.fsync) as fsync,logs.logged_copy_batch():
            logs.atomic_bytes(self.target/'live-journal.json',b'{}')
        self.assertEqual(fsync.call_count,2)


if __name__=='__main__':
    unittest.main()
