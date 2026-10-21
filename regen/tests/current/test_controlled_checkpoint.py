"""Checkpoint selection is predeclared and excludes future source records."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from regen.execution.proof_commits import durable
from regen.infrastructure.incremental_log import read_artifact
from regen.tools.controlled_checkpoint import prepare


class CheckpointTests(unittest.TestCase):
    def fixture(self, root, *, proved=False):
        source = root/'source'
        durable(source/'execution-input.json', {'files': [{'theory': 'T', 'file_slot': 'T.thy'}]})
        directory = source/'theories/T'
        text = 'theory T imports Main begin\nlemma goal: "True" oops\nend\n'
        h = hashlib.sha256(text.encode()).hexdigest()
        durable(directory/'imports.json', [])
        durable(directory/'document/versions'/h/'source.json', {'source': text})
        durable(directory/'acceptance/commits/000001.json', {
            'commit_id': 'fixed', 'sequence': 1, 'event': 'document', 'state': {
                'graph': {'nodes': [{'id': 'contract:goal'}]},
                'proofs': {'contract:goal': 'by simp'} if proved else {},
                'document_source_sha256': h, 'batch_requests': 12}})
        durable(directory/'acceptance/commits/000002.json', {'future': 'must not be read'})
        return source

    def test_exact_historical_selection_not_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.fixture(root)
            prepare(source, root/'out', theory='T', sequence=1,
                    commit_id='fixed', target='contract:goal')
            checkpoint = read_artifact(root/'out/checkpoint.json')
            self.assertEqual(checkpoint['theories']['T']['commit_id'], 'fixed')
            self.assertEqual(checkpoint['theories']['T']['artifacts'], {})
            self.assertEqual(read_artifact(root/'out/provenance.json')['inherited_requests'], 12)

    def test_wrong_commit_and_already_green_rejected_before_output(self):
        for proved, expected, message in ((False, 'wrong', 'predeclared'), (True, 'fixed', 'already GREEN')):
            with self.subTest(proved=proved), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = self.fixture(root, proved=proved)
                with self.assertRaisesRegex(ValueError, message):
                    prepare(source, root/'out', theory='T', sequence=1,
                            commit_id=expected, target='contract:goal')
                self.assertFalse((root/'out').exists())


if __name__ == '__main__':
    unittest.main()
