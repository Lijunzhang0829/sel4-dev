"""Verified inherited index identities remain readable in full-repository runs."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from regen.execution import repository_execution as execution
from regen.execution.proof_commits import durable
from regen.execution.proof_batches import prepare_batch,check_batch
from regen.execution.shared_proof_workspace import SharedProofWorkspace
from regen.tests.current.test_shared_proof_workspace import setup_run
from regen.tests.current.test_proof_batches import Checker

class WorkspaceSealedReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source'
        self.run,self.packages,self.initial,self.state=setup_run(self.source)
        self.destination=self.root/'destination';self.destination.mkdir()
        shutil.copytree(self.source/'packages',self.destination/'packages')
        shutil.copyfile(self.source/'execution-input.json',self.destination/'execution-input.json')
        self.new_run={**self.run,'run_id':'new-run','contract_package_dir':str(self.destination/'packages')}
        durable(self.destination/'run.json',self.new_run)

    def test_verified_seal_reuse_preserves_source_identity_and_readable_proofs(self):
        package=self.packages[0];log=execution.journal(self.source,self.run,package)
        current=prepare_batch(package,self.state,{'proofs':{'source_contract':'by simp'}})
        evidence=check_batch(Checker(),self.state,current);evidence['sealable_generation']=True
        log.append('batch',current,evidence)
        sealed=deepcopy(current);sealed.update(status='sealed',seal_id='test-seal')
        log.append('seal',sealed,{'impact':{'verdict':'pass'},'dependencies':{'verdict':'green'},
            'complete':Checker().check_graph(sealed['graph']),'build':{'verdict':'green'}})
        origin={'run':self.run,'imports':[], 'commits':[json.loads(p.read_text()) for p in sorted((self.source/'theories/Source/acceptance/commits').glob('*.json'))]}
        state=execution.continuation_state(package,origin,sealed_reuse=True)
        execution._reuse_sealed_theories(self.destination,self.new_run,self.source,[(package,origin,state)])
        workspace=SharedProofWorkspace(self.destination)
        snapshot=workspace._snapshot('Source')
        self.assertEqual(snapshot['state']['graph']['run_id'],'test-run--Source')
        self.assertEqual(snapshot['state']['status'],'sealed')
        records=workspace.index(theory='Source')['records']
        self.assertEqual(len(records),3)
        read=workspace.read([records[0]['source_ref']])
        self.assertTrue(read['records'][0]['proof_body'])

    def test_foreign_identity_without_verified_origin_still_rejected(self):
        package=self.packages[0]
        execution.journal(self.destination,self.new_run,package).append('initial',self.initial['Source'],
            {'check':Checker().check_graph(self.initial['Source']['graph'])})
        with self.assertRaisesRegex(ValueError,'different run/theory'):
            SharedProofWorkspace(self.destination)._snapshot('Source')
