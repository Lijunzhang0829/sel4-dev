from pathlib import Path
import tempfile
import unittest
from regen.tools.application_release import export, verify


class ReleaseTests(unittest.TestCase):
    def test_untracked_code_is_included_but_reports_are_not(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'regen').mkdir()
            (root/'regen/new.py').write_text('value = 1\n')
            (root/'reports').mkdir()
            (root/'reports/proof.json').write_text('{}')
            result=export(root,root/'release')
            self.assertEqual(result['files'],1)
            self.assertEqual(verify(root,root/'release/manifest.json')['verified_files'],1)
            (root/'regen/new.py').write_text('value = 2\n')
            with self.assertRaisesRegex(ValueError,'mismatch'):
                verify(root,root/'release/manifest.json')

    def test_symlink_cannot_export_external_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'regen').mkdir()
            (root/'regen/outside.py').symlink_to('/etc/passwd')
            with self.assertRaisesRegex(ValueError,'symlink'):
                export(root,root/'release')
