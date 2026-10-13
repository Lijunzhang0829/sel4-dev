import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import durable
from regen.tools.prepared_checkpoint import SCHEMA, derive, inventory, readonly, sha, verify


class PreparedCheckpointTests(unittest.TestCase):
    def fixture(self, root):
        fixture=root/'fixture';run=fixture/'run';run.mkdir(parents=True)
        config={'run_id':'lineage','implementation':{'x':'version'},
            'contract_package_dir':str(run/'packages'),
            'session_progress_ledger':str(run/'session-progress-ledger.json')}
        durable(run/'run.json',config)
        durable(run/'theories/T/acceptance/commits/000001.json',{'bound':'lineage','proofs':{'h':'preserved'}})
        durable(run/'pending.json',{'requests':44,'draft':'failed candidate retained'})
        durable(fixture/'manifest.json',{'schema':SCHEMA,'application':{'x':'a'},
            'external_context':{'files':{},'source_trees':{}},
            'lineage_run_id':'lineage','prepared_root':str(run),'files':inventory(run),
            'run_binding':{k:v for k,v in config.items() if k not in
                ('contract_package_dir','session_progress_ledger')}})
        return fixture,sha(fixture/'manifest.json')

    def test_independent_copy_relocates_only_writable_paths(self):
        with tempfile.TemporaryDirectory() as tmp,patch('regen.tools.prepared_checkpoint.application_binding',return_value={'x':'a'}):
            root=Path(tmp);fixture,pin=self.fixture(root)
            a=derive(fixture,root/'a',expected_manifest_sha256=pin)
            b=derive(fixture,root/'b',expected_manifest_sha256=pin)
            self.assertNotEqual(a['instance_id'],b['instance_id'])
            self.assertEqual(a['lineage_run_id'],'lineage')
            self.assertTrue(a['cold_start_validation_required'])
            copied=json.loads((root/'a/run.json').read_text())
            self.assertEqual(copied['contract_package_dir'],str(root/'a/packages'))
            self.assertEqual((root/'a/pending.json').read_bytes(),(fixture/'run/pending.json').read_bytes())
            (root/'a/pending.json').write_text('arm mutation')
            self.assertNotEqual((root/'a/pending.json').read_bytes(),(root/'b/pending.json').read_bytes())
            verify(fixture,pin)

    def test_manifest_content_and_application_tampering_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp,patch('regen.tools.prepared_checkpoint.application_binding',return_value={'x':'a'}):
            root=Path(tmp);fixture,pin=self.fixture(root)
            with self.assertRaisesRegex(ValueError,'manifest mismatch'):verify(fixture,'0'*64)
            with patch('regen.tools.prepared_checkpoint.application_binding',return_value={'x':'changed'}):
                with self.assertRaisesRegex(ValueError,'application changed'):verify(fixture,pin)
            (fixture/'run/pending.json').write_text('corrupt')
            with self.assertRaisesRegex(ValueError,'content mismatch'):derive(fixture,root/'bad',expected_manifest_sha256=pin)
            self.assertFalse((root/'bad').exists())

    def test_unlisted_content_symlinks_and_destination_collision_rejected(self):
        with tempfile.TemporaryDirectory() as tmp,patch('regen.tools.prepared_checkpoint.application_binding',return_value={'x':'a'}):
            root=Path(tmp);fixture,pin=self.fixture(root)
            (root/'existing').mkdir()
            with self.assertRaises(FileExistsError):derive(fixture,root/'existing',expected_manifest_sha256=pin)
            with self.assertRaises(ValueError):derive(fixture,fixture/'arm',expected_manifest_sha256=pin)
            (fixture/'run/link').symlink_to(root/'external')
            with self.assertRaisesRegex(ValueError,'symlink'):verify(fixture,pin)


if __name__=='__main__':unittest.main()
