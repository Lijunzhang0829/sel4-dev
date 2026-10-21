"""Purpose notes cannot change proof authority, and survive exact checkpoint forks."""
import json
from pathlib import Path
import tempfile
import unittest

from regen.planning.helper_purposes import merge, view
from regen.planning.theory_document import DocumentStore
from regen.tests.current.test_proof_commits import fixture
from regen.tests.current.test_proof_batches import Checker


class HelperPurposeTests(unittest.TestCase):
    def setUp(self):
        self.package,self.state=fixture()
        self.imports=Checker().imports()
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=DocumentStore(Path(self.temp.name)/'document',self.package,self.state,self.imports)
        self.note={'contract_id':'contract','gap':'establish the premise',
                   'expected_use':'first proof step','remaining_gap':'apply the helper'}

    def save(self, **changes):
        current=self.store.read()
        return self.store.edit({'action':'edit_theory','base_sha256':current['source_sha256'],
            'source':current['source'],'helper_purposes':{'helper':self.note},**changes},{})

    def test_notes_only_roundtrip_does_not_accept_anything(self):
        before=self.store.read()['source']
        result=self.save()
        self.assertFalse(result['checked'])
        self.assertEqual(self.store.read()['source'],before)
        self.assertEqual(self.store.read()['helper_purposes']['records']['helper']['status'],'pending')
        restored=DocumentStore(self.store.root,self.package,self.state,self.imports)
        self.assertEqual(restored.read(),self.store.read())
        self.assertEqual(self.state['proofs'],{})

    def test_missing_record_is_nonblocking_and_bad_claim_is_rejected(self):
        self.assertIn('helper',self.store.read()['helper_purposes']['missing_helper_ids'])
        for update in ({**self.note,'status':'accepted'}, {**self.note,'contract_id':'helper'},
                       {**self.note,'used_in_target':'first step'}):
            with self.assertRaises(ValueError): self.save(helper_purposes={'helper':update})
        self.assertEqual(self.store.read()['helper_purposes']['records'],{})

    def test_acceptance_and_reported_use_are_separate(self):
        entries=self.state['graph']['nodes']
        records=merge({}, {'helper':{**self.note,'used_in_target':'using helper'}},
                      entries,{'contract':'using helper by simp'},'draft')
        self.assertEqual(view(records,entries,{},self.state)['records']['helper']['status'],'pending')
        self.state['proofs']['helper']='by simp'
        row=view(records,entries,{'contract':'using helper by simp'},self.state)['records']['helper']
        self.assertEqual(row['status'],'used_in_target')
        self.assertFalse(row['target_green'])
        row=view(records,entries,{'contract':'by blast'},self.state)['records']['helper']
        self.assertEqual(row['status'],'accepted')

    def test_changed_statement_invalidates_note(self):
        entries=[dict(e) for e in self.state['graph']['nodes']]
        records=merge({}, {'helper':self.note},entries,{},'a')
        next(e for e in entries if e['id']=='helper')['statement']='lemma changed: "True"'
        self.assertEqual(merge(records,{},entries,{},'b'),{})

    def test_deleted_helper_does_not_leave_active_record(self):
        entries=self.state['graph']['nodes']
        records=merge({}, {'helper':self.note},entries,{},'a')
        self.assertEqual(merge(records,{},[e for e in entries if e['id']!='helper'],{},'b'),{})

    def test_edit_validation_is_atomic_with_notes(self):
        before=self.store.read()
        with self.assertRaises(ValueError):
            self.save(source=before['source'].replace('oops','by simp'),
                      helper_purposes={'helper':{**self.note,'contract_id':'unknown'}})
        self.assertEqual(self.store.read(),before)

    def test_notes_schema_accepts_only_advisory_fields(self):
        from jsonschema import Draft202012Validator
        from regen.planning.document_interface import tool_contracts
        schema=tool_contracts()['source_action_schemas']['edit_theory']
        proposal={'action':'edit_theory','source':self.store.read()['source'],
                  'base_sha256':'hash','helper_purposes':{'helper':self.note}}
        validator=Draft202012Validator(schema)
        self.assertTrue(validator.is_valid(proposal))
        proposal['helper_purposes']['helper']={**self.note,'status':'accepted'}
        self.assertFalse(validator.is_valid(proposal))

    def test_controller_exposes_current_accepted_status(self):
        from regen.tests.current.test_theory_document import DocumentControllerTests
        from regen.planning.theory_document import block
        base=DocumentControllerTests();base.setUp();self.addCleanup(base.doCleanups)
        draft=base.read()['document']
        helper=block({'id':'new_helper','role':'helper','context':[],
                      'statement':'lemma new_helper: "True"'},'by simp')
        source=draft['source'].replace('(* REGEN-BEGIN ',helper+'(* REGEN-BEGIN ',1)
        saved=base.call({'action':'edit_theory','base_sha256':draft['source_sha256'],
            'source':source,'helper_purposes':{'new_helper':{**self.note,'contract_id':'target_contract'}}})
        self.assertEqual(saved['helper_purposes']['records']['new_helper']['status'],'pending')
        result=base.call({'action':'check_theory','source_sha256':saved['source_sha256']})
        self.assertEqual(result['feedback']['status'],'accepted')
        notes=base.read()['document']['helper_purposes']
        self.assertEqual(notes['records']['new_helper']['status'],'accepted')
        self.assertFalse(notes['records']['new_helper']['target_green'])

    def test_checkpoint_explicit_snapshot_restores_notes_without_future_pointer(self):
        from regen.execution.repository_execution import _checkpoint_artifacts
        self.save()
        pointer=json.loads((self.store.root/'last-edit.json').read_text())
        selection={'draft_version':pointer['source_sha256'],
                   'helper_purpose_snapshot':pointer['helper_purpose_snapshot']}
        self.note['gap']='later note must not leak'
        self.save()
        copied=Path(self.temp.name)/'copy'
        for relative,payload in _checkpoint_artifacts(self.store.root.parent,selection,self.state):
            target=copied/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(payload)
        restored=DocumentStore(copied/'document',self.package,self.state,self.imports)
        self.assertEqual(restored.read()['helper_purposes']['records']['helper']['gap'],'establish the premise')
        selection['draft_version']='a'*64
        with self.assertRaises((ValueError,FileNotFoundError)):
            _checkpoint_artifacts(self.store.root.parent,selection,self.state)


if __name__=='__main__': unittest.main()
