"""Bounded-copy serialization preserves existing immutable journal identities."""
import hashlib
import json
from pathlib import Path
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest, durable


class CommitSerializationTests(unittest.TestCase):
    def test_canonical_digest_matches_existing_encoding(self):
        values = [None, True, 17, -0.0, 1e-12, float('inf'), float('nan'),
                  {'源': ['quote"\\\n', '𝄞', '证明', 2.5], 'a': {'x': []}},
                  {'origin': [{'evidence': {'source_content': 'lemma x: True'}}] * 8}]
        for value in values:
            expected = hashlib.sha256(json.dumps(value, sort_keys=True,
                ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
            with self.subTest(value=value), patch('json.dumps', side_effect=AssertionError('whole encoding')):
                self.assertEqual(digest(value), expected)

    def test_durable_bytes_and_immutable_collision_are_unchanged(self):
        value = {'证据': ['a\n', 2, {'nested': True}], 'zero': -0.0}
        expected = (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + '\n').encode()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'commits' / '000001.json'
            with patch('json.dumps', side_effect=AssertionError('whole encoding')):
                durable(path, value, immutable=True)
                durable(path, value, immutable=True)
                with self.assertRaisesRegex(ValueError, 'immutable artifact collision'):
                    durable(path, {'changed': True}, immutable=True)
            self.assertEqual(path.read_bytes(), expected)
            self.assertEqual(list(path.parent.glob('.pending-*')), [])

    def test_large_collection_does_not_allocate_a_whole_encoded_copy(self):
        value = {'evidence': ['x' * 4096] * 4096}
        with tempfile.TemporaryDirectory() as temporary:
            tracemalloc.start()
            try:
                digest(value)
                durable(Path(temporary) / 'record.json', value)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
        self.assertLess(peak, 2 * 1024 * 1024)

    def test_failed_serialization_leaves_no_published_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'record.json'
            with self.assertRaises(TypeError):
                durable(path, {'bad': object()}, immutable=True)
            self.assertFalse(path.exists())
            self.assertEqual(list(path.parent.glob('.pending-*')), [])


if __name__ == '__main__':
    unittest.main()
