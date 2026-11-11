"""Numbered input preserves goal boundaries and refuses manifest drift."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from regen.preparation.calg_shuffled_dataset import inventory, contracts_for

SOURCE=Path(__file__).resolve().parents[3]/'c-algorithms/binomial/dataset-shuffled-001/input'


@unittest.skipUnless(SOURCE.is_dir(),'optional frozen C input not bundled')
class NumberedCInputTests(unittest.TestCase):
    def test_exact_statements_contexts_and_target_only_allocation(self):
        manifest,targets,background=inventory(SOURCE)
        actual=contracts_for(targets)
        self.assertEqual(len(actual),79)
        self.assertEqual(len({c['contract_id'] for c in actual}),79)
        self.assertFalse({c['name'] for c in actual}&{c['name'] for c in background})
        self.assertEqual([c['exact_statement'] for c in actual],[t['statement'] for t in targets])
        for t,c in zip(targets,actual):
            self.assertEqual(c['contract_owned_context'],[] if t['context']=='global' else ['context '+t['context']+' begin'])
        self.assertEqual(manifest['verification_target_count'],79)

    def test_changed_statement_or_added_proof_rejected_before_preparation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'input';shutil.copytree(SOURCE,root)
            p=root/'targets.json';value=json.loads(p.read_text())
            value['targets'][0]['statement']='lemma fake: "True"'
            p.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'drift'):inventory(root)

    def test_unlisted_input_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'input';shutil.copytree(SOURCE,root)
            (root/'old-proof.thy').write_text('lemma leaked: "True" by simp')
            with self.assertRaisesRegex(ValueError,'drift'):inventory(root)
