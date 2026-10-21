"""Verified prefixes may be reused, but never outlive their evidence identity."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from regen.execution.proof_commits import ProofCommits, digest, durable


class ProofCommitCacheTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.validate = Mock()
        self.journal = ProofCommits(self.root, {'run': 'demo'}, self.validate)
        self.journal.append('initial', {'n': 1, 'nested': {'value': 'safe'}}, {})
        self.journal.append('step', {'n': 2, 'nested': {'value': 'safe'}}, {})
        self.validate.reset_mock()

    def test_unchanged_prefix_does_not_repeat_validator_and_return_is_isolated(self):
        state = self.journal.replay()
        state['nested']['value'] = 'polluted'
        self.assertEqual(self.journal.replay()['nested']['value'], 'safe')
        self.validate.assert_not_called()

    def test_new_commit_only_validates_suffix(self):
        writer = ProofCommits(self.root, {'run': 'demo'}, Mock())
        writer.append('step', {'n': 3}, {})
        self.assertEqual(self.journal.replay(), {'n': 3})
        self.validate.assert_called_once()
        self.assertEqual(self.validate.call_args.args[0]['n'], 2)
        self.assertEqual(self.validate.call_args.args[1]['n'], 3)

    def test_append_does_not_repeat_prefix_or_alias_its_argument(self):
        state = {'n': 3, 'nested': {'value': 'original'}}
        self.journal.append('step', state, {})
        state['nested']['value'] = 'modified'
        self.assertEqual(self.journal.replay()['nested']['value'], 'original')
        self.validate.assert_called_once()

    def test_in_place_same_size_tamper_with_restored_mtime_is_rejected(self):
        path = self.root/'commits/000001.json'
        original = path.stat()
        text = path.read_text()
        self.assertIn('"n": 1', text)
        path.write_text(text.replace('"n": 1', '"n": 9'))
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.assertEqual(path.stat().st_size, original.st_size)
        with self.assertRaises(ValueError):
            self.journal.replay()

    def test_atomic_identical_replacement_revalidates_complete_chain(self):
        path = self.root/'commits/000001.json'
        replacement = self.root/'replacement'
        replacement.write_bytes(path.read_bytes())
        os.replace(replacement, path)
        self.assertEqual(self.journal.replay()['n'], 2)
        self.assertEqual(self.validate.call_count, 2)

    def test_missing_first_or_tail_commit_is_not_hidden(self):
        for filename in ('000001.json', '000002.json'):
            with self.subTest(filename=filename):
                path = self.root/'commits'/filename
                contents = path.read_bytes()
                path.unlink()
                with self.assertRaises(ValueError):
                    self.journal.replay()
                path.write_bytes(contents)
                self.journal.replay()

    def test_truncated_history_remains_missing_even_if_head_is_rewound(self):
        (self.root/'commits/000002.json').unlink()
        first = json.loads((self.root/'commits/000001.json').read_text())
        durable(self.root/'HEAD.json', {'sequence': 1, 'commit_id': first['commit_id']})
        with self.assertRaisesRegex(ValueError, 'committed history is missing'):
            self.journal.replay()

    def test_binding_and_validator_changes_invalidate_cache(self):
        self.journal.binding['run'] = 'different'
        with self.assertRaises(ValueError):
            self.journal.replay()
        self.journal.binding['run'] = 'demo'
        changed = Mock(side_effect=ValueError('new validation rejects history'))
        self.journal.validate = changed
        with self.assertRaisesRegex(ValueError, 'new validation'):
            self.journal.replay()
        changed.assert_called_once()

    def test_unscoped_new_instance_and_new_process_validate_history(self):
        validate = Mock()
        ProofCommits(self.root, {'run': 'demo'}, validate).replay()
        self.assertEqual(validate.call_count, 2)
        code = ('from pathlib import Path; from regen.execution.proof_commits import ProofCommits; '
                'calls=[]; ProofCommits(Path(__import__("sys").argv[1]), {"run":"demo"}, '
                'lambda *args: calls.append(1)).replay(); print(len(calls))')
        result = subprocess.run([sys.executable, '-c', code, str(self.root)],
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), '2')

    def test_shared_cache_requires_matching_explicit_validation_namespace(self):
        first = Mock()
        ProofCommits(self.root, {'run': 'demo'}, first, cache_namespace='validator:v1').replay()
        self.assertEqual(first.call_count, 2)
        second = Mock()
        result = ProofCommits(self.root, {'run': 'demo'}, second, cache_namespace='validator:v1').replay()
        second.assert_not_called()
        result['nested']['value'] = 'polluted'
        self.assertEqual(ProofCommits(self.root, {'run': 'demo'}, second,
                         cache_namespace='validator:v1').replay()['nested']['value'], 'safe')
        third = Mock()
        ProofCommits(self.root, {'run': 'demo'}, third, cache_namespace='validator:v2').replay()
        self.assertEqual(third.call_count, 2)

    def test_rehashed_evidence_change_still_runs_validator(self):
        path = self.root/'commits/000002.json'
        record = json.loads(path.read_text())
        record['evidence'] = {'invalid': True}
        record['commit_id'] = digest({k: v for k, v in record.items() if k != 'commit_id'})
        durable(path, record)
        # A correct hash does not exempt changed evidence from semantic validation.
        self.validate.side_effect = lambda old, state, event, evidence: (
            (_ for _ in ()).throw(ValueError('invalid evidence')) if evidence.get('invalid') else None)
        with self.assertRaisesRegex(ValueError, 'invalid evidence'):
            self.journal.replay()
        self.assertEqual(self.validate.call_count, 2)

    def test_head_projection_is_recovered_without_revalidating_unchanged_commits(self):
        (self.root/'HEAD.json').write_text('not json')
        self.assertEqual(self.journal.replay()['n'], 2)
        self.assertEqual(json.loads((self.root/'HEAD.json').read_text())['sequence'], 2)
        self.validate.assert_not_called()

    def test_empty_repository_theories_do_not_evict_active_shared_cache(self):
        active = ProofCommits(self.root, {'run':'demo'}, self.validate, cache_namespace='repository')
        active.replay()
        self.validate.reset_mock()
        for n in range(15):
            ProofCommits(self.root/f'empty-{n}', {}, Mock(), cache_namespace='repository').replay()
        # progress creates fresh instances, so test shared reuse as well.
        ProofCommits(self.root, {'run':'demo'}, self.validate, cache_namespace='repository').replay()
        self.validate.assert_not_called()

    def test_lru_eviction_keeps_instance_prefix_and_tamper_detection(self):
        active = ProofCommits(self.root, {'run':'demo'}, self.validate, cache_namespace='repository')
        active.replay()
        self.validate.reset_mock()
        with patch('regen.execution.proof_commits._MAX_VERIFIED_PREFIXES', 8):
            for n in range(15):
                other = ProofCommits(self.root/f'active-{n}', {}, Mock(), cache_namespace='repository')
                other.append('initial', {'n':n}, {})
        active.replay()
        self.validate.assert_not_called()
        path = self.root/'commits/000001.json'
        path.write_text(path.read_text().replace('"n": 1', '"n": 9'))
        with self.assertRaises(ValueError):
            active.replay()



if __name__ == '__main__':
    unittest.main()
