"""The fixed HOL package preserves goals and cannot require the C frontend."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from regen.preparation.calg_shuffled_dataset import comparison_groups, inventory

PROJECT = Path(__file__).resolve().parents[3]
PACKAGE = PROJECT/'c-algorithms/binomial/dataset-hol-001'
SOURCE = PACKAGE/'input'
ORIGINAL = PROJECT/'c-algorithms/binomial/dataset/input'
spec = importlib.util.spec_from_file_location('calg_hol_bootstrap',
    PROJECT/'c-algorithms/binomial/hol-export/bootstrap_parent.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def reseal(root):
    p = root/'manifest.json'
    manifest = json.loads(p.read_text())
    files = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in root.rglob('*') if p.is_file() and p.name != 'manifest.json'}
    manifest['input_files_sha256'] = files
    manifest['dataset_binding'] = hashlib.sha256(json.dumps(
        files, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    p.write_text(json.dumps(manifest))


@unittest.skipUnless(SOURCE.is_dir(), 'optional HOL package not bundled')
class FixedHOLInputTests(unittest.TestCase):
    def test_default_generation_entry_uses_the_fixed_hol_package(self):
        pointer = json.loads((PROJECT/'c-algorithms/binomial/current-dataset.json').read_text())
        self.assertEqual((PROJECT/'c-algorithms/binomial'/pointer['input_manifest']).resolve(),
                         (SOURCE/'manifest.json').resolve())
        self.assertEqual(pointer['dataset_binding'], inventory(SOURCE)[0]['dataset_binding'])

    def test_no_c_input_and_same_goal_partition(self):
        manifest = bootstrap.validate_fixed_input(SOURCE)
        _, targets, background = inventory(SOURCE)
        groups = comparison_groups(manifest, targets, PACKAGE/'comparison-groups.json')
        self.assertEqual(groups['counts'], {'main': 78, 'fixed_helper': 13})
        self.assertEqual(len(background), 9)
        self.assertEqual(manifest['disabled_target_ids'], ['0054'])
        for name in ('specs.json', 'targets.json', 'background.json',
                     'contract-selection.json', 'isabelle/Contracts.thy',
                     'judgement-policy.json', 'environment-scopes.json'):
            self.assertEqual((SOURCE/name).read_bytes(), (ORIGINAL/name).read_bytes(), name)

    def test_resealed_c_file_or_translation_command_is_rejected(self):
        for name, content in (
                ('unused.c', 'int unused;\n'),
                ('parent/Frontend.thy', 'theory Frontend imports Main begin\n'
                 'install_C_file "unused.c"\nend\n')):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)/'input'
                shutil.copytree(SOURCE, root)
                (root/name).write_text(content)
                reseal(root)
                with self.assertRaisesRegex(ValueError, 'C input or translation'):
                    bootstrap.validate_fixed_input(root)

    def test_resealed_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'input'
            shutil.copytree(SOURCE, root)
            (root/'parent/Hidden_Model.thy').symlink_to(root/'parent/model/Project_Input.thy')
            reseal(root)
            with self.assertRaisesRegex(ValueError, 'links'):
                bootstrap.validate_fixed_input(root)

    def test_c_translation_flag_cannot_be_reenabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'input'
            shutil.copytree(SOURCE, root)
            p = root/'manifest.json'
            value = json.loads(p.read_text())
            value['c_translation_required_at_generation'] = True
            p.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, 'C-free HOL-source'):
                bootstrap.validate_fixed_input(root)
