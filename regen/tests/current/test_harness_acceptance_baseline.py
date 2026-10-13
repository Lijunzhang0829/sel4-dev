"""The validation observer must not traverse external heaps or mutate a run."""
import os
from pathlib import Path
import tempfile
import unittest

from regen.tools.harness_acceptance_baseline import protected_snapshot


class AcceptanceObserverTests(unittest.TestCase):
    def test_observer_is_read_only_and_does_not_follow_asset_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'run'
            root.mkdir()
            assets = Path(temporary)/'large-external-assets'
            assets.mkdir()
            (assets/'heap').write_text('initial')
            (root/'heaps').symlink_to(assets, target_is_directory=True)
            (root/'run.json').write_text('{"id":1}')
            before = protected_snapshot(root)
            self.assertEqual(before, protected_snapshot(root))
            (assets/'heap').write_text('changed external heap')
            self.assertEqual(before, protected_snapshot(root))
            self.assertEqual(before['entry_count'], 2)

    def test_pointer_digest_detects_equal_size_equal_mtime_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pointer = root/'HEAD.json'
            pointer.write_text('{"id":1}')
            old_stat = pointer.stat()
            before = protected_snapshot(root)
            pointer.write_text('{"id":2}')
            os.utime(pointer, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
            after = protected_snapshot(root)
            self.assertEqual(before['metadata_inventory_sha256'], after['metadata_inventory_sha256'])
            self.assertNotEqual(before['authority_pointer_sha256'], after['authority_pointer_sha256'])


if __name__ == '__main__':
    unittest.main()
