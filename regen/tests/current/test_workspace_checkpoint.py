"""Checkpoint artifact admission cannot inherit future work or heap markers."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from regen.execution.repository_execution import _checkpoint_artifacts


class CheckpointArtifactsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = {'batch_requests': 3}

    def select(self, name, payload=b'checkpoint'):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return {'artifacts': {name: hashlib.sha256(payload).hexdigest()}}

    def test_only_explicit_files_are_returned(self):
        selection = self.select('batch-requests/000003/proposal.json')
        self.select('batch-requests/000004/feedback.json')
        self.select('provider-session.json')
        admitted = _checkpoint_artifacts(self.root, selection, self.state)
        self.assertEqual([str(p) for p,_ in admitted], ['batch-requests/000003/proposal.json'])

    def test_future_request_and_runtime_state_rejected(self):
        for name in ('batch-requests/000004/feedback.json', 'provider-session.json',
                     'generated/Future.thy', 'document/last-edit.json'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                _checkpoint_artifacts(self.root, self.select(name), self.state)

    def test_changed_artifact_rejected(self):
        selection = self.select('document/draft.thy')
        (self.root/'document/draft.thy').write_text('later edit')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            _checkpoint_artifacts(self.root, selection, self.state)

    def test_historical_draft_is_rebuilt_without_reading_latest_draft(self):
        source = 'historical draft'
        version = hashlib.sha256(source.encode()).hexdigest()
        self.select('document/versions/'+version+'/source.json', json.dumps({'source':source}).encode())
        self.select('document/draft.thy', b'future draft')
        admitted = _checkpoint_artifacts(self.root, {'draft_version':version}, self.state)
        self.assertEqual(admitted, [(Path('document/draft.thy'),source.encode())])

    def test_traversal_and_absolute_paths_rejected(self):
        for name in ('../outside', '/tmp/outside'):
            with self.assertRaisesRegex(ValueError, 'unsafe'):
                _checkpoint_artifacts(self.root, {'artifacts': {name: 'unused'}}, self.state)

    def test_symlink_rejected(self):
        selection = self.select('document/draft.thy')
        (self.root/'document/alias.thy').symlink_to('draft.thy')
        (self.root/'document/draft.thy').rename(self.root/'original')
        (self.root/'document/draft.thy').symlink_to('../original')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            _checkpoint_artifacts(self.root, selection, self.state)


if __name__ == '__main__':
    unittest.main()
