"""The compact dataset retains proofs privately and stages proof-free inputs."""
import ast
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import sys

import compact_dataset as compact


class CompactDatasetTests(unittest.TestCase):
    def test_retained_tool_imports_have_local_sources(self):
        folder = Path(__file__).resolve().parent
        external = {'vero', 'regen'}
        for file in folder.glob('*.py'):
            for node in ast.walk(ast.parse(file.read_text())):
                modules = ([n.name for n in node.names] if isinstance(node, ast.Import)
                           else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                for module in modules:
                    name = module.split('.')[0]
                    if name not in sys.stdlib_module_names and name not in external:
                        self.assertTrue((folder / (name + '.py')).is_file(),
                                        str(file) + ' imports missing local source ' + name)

    def test_archived_receipts_still_decide_all_targets(self):
        result = compact.verify()
        self.assertEqual((result['positive'], result['context_refuted']), (83, 8))
        self.assertFalse(result['new_native_run'])
        self.assertFalse(result['runtime_ready'])

    def test_changed_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(compact.DATASET / 'input', root / 'input')
            p = root / 'input/targets.json'
            p.write_text(p.read_text() + ' ')
            with self.assertRaisesRegex(ValueError, 'binding changed'):
                compact.verify_input(root)

    def test_changed_archive_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'dataset'; root.mkdir()
            shutil.copytree(compact.DATASET / 'input', root / 'input')
            shutil.copytree(compact.DATASET / 'acceptance', root / 'acceptance')
            p = root / 'acceptance/checked-evidence.tar.gz'
            with p.open('ab') as stream: stream.write(b'changed')
            with self.assertRaisesRegex(ValueError, 'archive hash changed'):
                compact.extract_checked(root, Path(tmp) / 'extract')

    def test_default_staging_cannot_supply_accepted_proofs(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'sources'
            result = compact.materialize(output)
            self.assertFalse(result['target_proofs_supplied'])
            self.assertFalse((output / 'private-acceptance').exists())
            self.assertFalse(list(output.rglob('Current_Preparation_*.thy')))
            self.assertEqual(len((output / 'contracts.jsonl').read_text().splitlines()), 91)
            self.assertNotIn('spec_0054', [json.loads(x)['name'] for x in (output / 'contracts.jsonl').read_text().splitlines()])
            self.assertEqual((output / 'parent/ROOT').read_bytes(), (compact.DATASET / 'input/parent/ROOT').read_bytes())

    def test_acceptance_source_replay_is_explicit_and_self_contained(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'trusted'
            result = compact.materialize(output, with_acceptance=True)
            self.assertTrue(result['target_proofs_supplied'])
            source = next((output / 'private-acceptance/dataset/validation').glob('Current_Target_Receipts-*/source'))
            self.assertTrue((source / 'Current_Target_Receipts.thy').is_file())
            self.assertTrue((source / 'ROOT').is_file())

    def test_refuses_to_put_build_products_in_the_dataset_tree(self):
        with self.assertRaisesRegex(ValueError, 'outside c-algorithms'):
            compact.materialize(compact.BASE / 'unexpected-build')


if __name__ == '__main__':
    unittest.main()
