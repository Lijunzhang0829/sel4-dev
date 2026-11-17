from copy import deepcopy
import unittest
from unittest.mock import patch
from regen.tests.current.test_document_proof_edits import ProofEditTests
from regen.planning.theory_document import DocumentEditError, parse


class BatchEditTests(unittest.TestCase):
    def setUp(self):
        self.f=ProofEditTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.s=self.f.store

    def proposal(self):
        ids=[n['id'] for n in self.f.state['graph']['nodes']]
        return {'action':'edit_proofs','expected_hash':self.s.read()['source_sha256'],
                'proofs':[{'entry_id':key,'proof_body':'by simp'} for key in ids]}

    def test_two_bodies_single_save_without_acceptance(self):
        p=self.proposal()
        with patch.object(self.s,'_write',wraps=self.s._write) as write:
            result=self.s.edit_proofs(p,{})
        self.assertEqual(write.call_count,1);self.assertTrue(result['atomic'])
        self.assertFalse(result['checked']);self.assertEqual(self.f.state['proofs'],{})
        _,proofs=parse(self.s.path.read_text(),self.f.package,self.s.imports)
        self.assertEqual(set(proofs),set(result['entry_ids']))

    def test_invalid_second_duplicate_and_stale_leave_all_files_unchanged(self):
        for variant in ('missing','duplicate','marker','stale','extra'):
            p=self.proposal();before=self.f.files()
            if variant=='missing':p['proofs'][-1]['entry_id']='unknown'
            if variant=='duplicate':p['proofs'].append(deepcopy(p['proofs'][0]))
            if variant=='marker':p['proofs'][-1]['proof_body']='(* REGEN-PROOF *)'
            if variant=='stale':p['expected_hash']='stale'
            if variant=='extra':p['imports']=['Evil']
            with self.subTest(variant=variant),self.assertRaises((DocumentEditError,ValueError)):
                self.s.edit_proofs(p,{})
            self.assertEqual(self.f.files(),before)

    def test_controller_dispatch_and_pending_protection(self):
        from regen.tests.current.test_async_document_checks import AsyncChecksTests
        f=AsyncChecksTests();f.setUp();self.addCleanup(f.doCleanups)
        saved=f.prepare();before=f.f.read()['document']['source']
        f.c._async_snapshots={'pending':{'protected':{'target_contract'}}}
        with patch.object(f.c,'_pending',return_value=[{'task_id':'pending'}]):
            with self.assertRaisesRegex(ValueError,'reserved'):
                f.f.call({'action':'edit_proofs','expected_hash':saved['source_sha256'],
                    'proofs':[{'entry_id':'extra','proof_body':'by simp'},
                              {'entry_id':'target_contract','proof_body':'by blast'}]})
            self.assertEqual(f.f.read()['document']['source'],before)
            result=f.f.call({'action':'edit_proofs','expected_hash':saved['source_sha256'],
                            'proofs':[{'entry_id':'extra','proof_body':'by simp'}]})
            self.assertTrue(result['atomic'])

    def test_schema_and_disposition_guard_cover_every_batch_entry(self):
        from regen.planning.document_interface import tool_contracts
        from regen.planning.parallel_file_planner import TheoryController
        from jsonschema import Draft202012Validator
        schema=tool_contracts()['source_action_schemas']['edit_proofs']
        p=self.proposal();Draft202012Validator(schema).validate(p)
        controller=object.__new__(TheoryController)
        controller.run={}
        with patch('regen.planning.parallel_file_planner.contract_dispositions_enabled',return_value=True), \
             patch('regen.planning.parallel_file_planner.current_dispositions',return_value={'contract':{}}):
            with self.assertRaisesRegex(ValueError,'disposed'):
                controller._guard_disposed_write(p)


if __name__ == '__main__':unittest.main()
