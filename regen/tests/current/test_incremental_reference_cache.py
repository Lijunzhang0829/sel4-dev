"""Warm reachability checks stay payload-free without hiding altered evidence."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.infrastructure import incremental_log as logs
from regen.execution.proof_commits import ProofCommits, durable


class ReferenceMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(logs.clear_cache)
        logs.clear_cache()
        self.root = Path(self.temp.name) / 'run'
        logs.enable(self.root)
        self.path = self.root / 'commits/000001.json'

    def write_history(self):
        durable(self.path, {'proof': 'p' * 40000,
                           'evidence': {'log': 'q' * 40000}}, immutable=True)
        second = self.path.with_name('000002.json')
        durable(second, {'proof': 'p' * 40000,
                         'evidence': {'log': 'q' * 40000}, 'request': 2}, immutable=True)
        return self.path, second

    def test_warm_fingerprint_does_not_decode_evicted_payload(self):
        paths = self.write_history()
        logs.clear_cache()
        with patch.object(logs, '_MAX_CACHE_BYTES', 0):
            with logs.fingerprint_batch():
                expected = [logs.artifact_fingerprint(p) for p in paths]
            self.assertEqual(logs._CACHE_BYTES, 0)
            self.assertTrue(logs._REFERENCES)
            with patch.object(logs.zlib, 'decompress', side_effect=AssertionError('payload read')):
                with logs.fingerprint_batch():
                    actual = [logs.artifact_fingerprint(p) for p in paths]
            self.assertEqual(actual, expected)

    def test_each_reachable_object_is_statted_once_per_batch_again_next_batch(self):
        paths = self.write_history()
        expected = [logs.artifact_fingerprint(p) for p in paths]
        all_keys = {key for fp in expected for key, _ in fp}
        original = logs._stamp
        seen = []

        def stamp(path):
            seen.append(path.name)
            return original(path)

        with patch.object(logs, '_stamp', side_effect=stamp):
            for _ in range(2):
                seen.clear()
                with logs.fingerprint_batch():
                    self.assertEqual([logs.artifact_fingerprint(p) for p in paths], expected)
                self.assertEqual(set(seen), {key + '.json.z' for key in all_keys})
                self.assertEqual(len(seen), len(all_keys))

    def test_same_size_corruption_with_restored_mtime_is_detected_by_ctime(self):
        self.write_history()
        refs = logs.artifact_fingerprint(self.path)
        store = logs.ObjectStore(self.root)
        root = json.loads(self.path.read_text())['root']
        child = store.path(next(key for key, _ in refs if key != root))
        info = child.stat()
        raw = child.read_bytes()
        child.write_bytes(b'X' * len(raw))
        os.utime(child, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertEqual(child.stat().st_size, info.st_size)
        self.assertEqual(child.stat().st_mtime_ns, info.st_mtime_ns)
        with self.assertRaises(ValueError):
            logs.artifact_fingerprint(self.path)

    def test_deleted_descendant_and_symlink_replacement_are_not_hidden(self):
        self.write_history()
        refs = logs.artifact_fingerprint(self.path)
        store = logs.ObjectStore(self.root)
        root = json.loads(self.path.read_text())['root']
        child = store.path(next(key for key, _ in refs if key != root))
        raw = child.read_bytes()
        child.unlink()
        with self.assertRaises(FileNotFoundError):
            logs.artifact_fingerprint(self.path)
        replacement = Path(self.temp.name) / 'same-bytes'
        replacement.write_bytes(raw)
        child.symlink_to(replacement)
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            logs.artifact_fingerprint(self.path)

    def test_metadata_is_store_scoped_even_with_same_hashes(self):
        self.write_history()
        logs.artifact_fingerprint(self.path)
        other = Path(self.temp.name) / 'other'
        logs.enable(other)
        target = other / 'commits/000001.json'
        logs.copy_logged_file(self.path, target)
        copied = logs.artifact_fingerprint(target)
        logs.ObjectStore(other).path(copied[0][0]).write_bytes(b'bad copy')
        with logs.fingerprint_batch():
            logs.artifact_fingerprint(self.path)
            with self.assertRaises(ValueError):
                logs.artifact_fingerprint(target)

    def test_metadata_eviction_and_cache_clear_do_not_change_result(self):
        self.write_history()
        expected = logs.artifact_fingerprint(self.path)
        logs.clear_cache()
        with patch.object(logs, '_MAX_REFERENCE_ENTRIES', 1), patch.object(
                logs, '_MAX_REFERENCE_BYTES', 2048):
            self.assertEqual(logs.artifact_fingerprint(self.path), expected)
            self.assertLessEqual(len(logs._REFERENCES), 1)
            self.assertLessEqual(logs._REFERENCE_BYTES, 2048)
            self.assertEqual(logs.artifact_fingerprint(self.path), expected)
        logs.clear_cache()
        self.assertEqual(logs._REFERENCES, {})
        self.assertEqual(logs._REFERENCE_BYTES, 0)
        self.assertEqual(logs.artifact_fingerprint(self.path), expected)

    def test_harmless_stamp_change_rehashes_then_can_warm_again(self):
        self.write_history()
        previous = logs.artifact_fingerprint(self.path)
        obj = logs.ObjectStore(self.root).path(previous[0][0])
        info = obj.stat()
        os.utime(obj, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000))
        with patch.object(logs.zlib, 'decompress', wraps=logs.zlib.decompress) as decode:
            current = logs.artifact_fingerprint(self.path)
            self.assertGreaterEqual(decode.call_count, 1)
            decode.reset_mock()
            self.assertEqual(logs.artifact_fingerprint(self.path), current)
            self.assertEqual(decode.call_count, 0)
        self.assertNotEqual(previous, current)

    def test_duplicate_write_cannot_bless_warm_corrupt_object(self):
        self.write_history()
        refs = logs.artifact_fingerprint(self.path)
        store = logs.ObjectStore(self.root)
        root = json.loads(self.path.read_text())['root']
        store.path(next(key for key, _ in refs if key != root)).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            durable(self.path.with_name('000003.json'),
                    {'proof': 'p' * 40000, 'evidence': {'log': 'q' * 40000}}, immutable=True)

    def test_warm_journal_append_checks_descendant_and_validates_new_event(self):
        events = []
        journal = ProofCommits(self.path.parent.parent / 'journal', {},
                              lambda *args: events.append(args[2]))
        journal.append('initial', {'proof': 'x' * 40000}, {'log': 'y' * 40000})
        first = journal.directory / 'commits/000001.json'
        journal.append('step', {'proof': 'x' * 40000, 'n': 2}, {})
        self.assertEqual(events, ['initial', 'step'])
        refs = logs.artifact_fingerprint(first)
        store = logs.ObjectStore(self.root)
        root = json.loads(first.read_text())['root']
        store.path(next(key for key, _ in refs if key != root)).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            journal.append('step', {'proof': 'x' * 40000, 'n': 3}, {})
        self.assertFalse((journal.directory / 'commits/000003.json').exists())

    def test_warm_authoritative_replay_works_without_payload_cache(self):
        journal = ProofCommits(self.root / 'journal', {}, lambda *args: None)
        state = {'proof': 'x' * 40000, 'request': 1}
        with patch.object(logs, '_MAX_CACHE_BYTES', 0):
            journal.append('initial', state, {'log': 'z' * 40000})
            self.assertEqual(journal.replay(repair=False), state)
            self.assertEqual(logs._CACHE_BYTES, 0)
            with patch.object(logs.zlib, 'decompress', side_effect=AssertionError('payload read')):
                self.assertEqual(journal.replay(repair=False), state)
                # Losing HEAD does not lose accepted authority or trigger a
                # write from read-only replay; immutable commits remain checked.
                (journal.directory / 'HEAD.json').unlink()
                self.assertEqual(journal.replay(repair=False), state)
                self.assertFalse((journal.directory / 'HEAD.json').exists())


if __name__ == '__main__':
    unittest.main()
