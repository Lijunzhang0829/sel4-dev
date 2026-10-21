import json
from pathlib import Path
import tempfile
import unittest
from regen.tools.archive_reference_results import final_state, export, verify
from regen.tests.current.test_shared_proof_workspace import setup_run


class ReferenceArchiveTests(unittest.TestCase):
    def test_export_keeps_every_accepted_body_without_costs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);campaign=root/'campaign';run=campaign/'run'
            config,packages,initial,current=setup_run(run)
            path=run/'theories/Source/acceptance/commits/000002.json'
            state,identity=final_state(path)
            self.assertEqual(state['proofs'],current['proofs'])
            self.assertEqual(identity['sequence'],2)
            out=root/'reference';export(campaign,out)
            self.assertTrue(verify(out))
            saved=json.loads((out/'runs/run/theories/Source.json').read_text())
            self.assertEqual({n['id']:n['proof_body'] for n in saved['helpers']},current['proofs'])
            self.assertNotIn('rounds',saved)
            self.assertNotIn('batch_history',saved)
            self.assertTrue(path.exists())

    def test_damaged_archive_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);setup_run(root/'campaign/run');out=root/'reference'
            export(root/'campaign',out)
            (out/'runs/run/theories/Source.json').write_text('{}')
            with self.assertRaises(ValueError):verify(out)


if __name__=='__main__':unittest.main()
