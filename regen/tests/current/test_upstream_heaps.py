from pathlib import Path
import tempfile
import unittest
from regen.preparation.upstream_heaps import PLATFORM, inventory, copy_cache

class UpstreamHeapsTest(unittest.TestCase):
    def test_copy_and_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'cache'; platform=root/PLATFORM
            (platform/'log').mkdir(parents=True)
            (platform/'HOL').write_bytes(b'heap')
            (platform/'log/HOL.db').write_bytes(b'database')
            records=inventory(root)
            copy_cache(root,Path(directory)/'copy',records)
            self.assertEqual(inventory(Path(directory)/'copy'),records)
            (platform/'HOL').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'drifted'):
                copy_cache(root,Path(directory)/'second',records)

    def test_target_descendant_unknown_and_symlink_rejected(self):
        for name in ['Refine','CRefine','CBaseRefine','RegenSeal_1','unknown']:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); (root/PLATFORM).mkdir()
                (root/PLATFORM/name).write_bytes(b'forbidden')
                with self.assertRaisesRegex(ValueError,'allowlisted'):
                    inventory(root)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/PLATFORM).mkdir()
            (root/PLATFORM/'HOL').symlink_to('/tmp')
            with self.assertRaisesRegex(ValueError,'symlink'):
                inventory(root)

    def test_heap_requires_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/PLATFORM).mkdir()
            (root/PLATFORM/'HOL').write_bytes(b'heap')
            with self.assertRaisesRegex(ValueError,'no build database'):
                inventory(root)
