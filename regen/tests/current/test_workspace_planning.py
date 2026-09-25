import copy
import json
from pathlib import Path
import shutil
import sqlite3
import unittest
from unittest.mock import Mock
from regen.tests.current import test_iterative_repository_planner as fixtures
from regen.tests.current import test_premise_database_v2 as db_fixtures
from regen.planning.workspace_harness import WorkspaceHarness, diagnostics, short_view
from regen.planning.workspace_preflight import render
from regen.planning.workspace_planner import snapshot_file, container_command, instruction
from regen.preparation.premise_database import digest


class WorkspacePlanningTest(unittest.TestCase):
    def setUp(self):
        f=fixtures.IterativeRepositoryPlannerTest();f.setUp();self.addCleanup(f.doCleanups);self.f=f
        db=db_fixtures.PremiseDatabaseV2Tests();db.setUp();self.addCleanup(db.doCleanups)
        self.input=f.input; self.root=f.root
        payload=json.loads(self.input.read_text());target=self.input.parent/payload['parent_premises']['database']
        shutil.copyfile(db.db,target)
        # A synthetic fixture equation name; production uses sealed native facts.
        with sqlite3.connect(target) as sql: sql.execute("UPDATE premises SET canonical_name='Base.map_def',declaration_name='Base.map_def' WHERE record_id='p1'")
        payload['parent_premises']['database_sha256']=digest(target)
        self.input.write_text(json.dumps(payload))
        (self.input.parent/'parent-premises-manifest.json').write_text(json.dumps({'schema_version':2,'database_sha256':digest(target)}))
        self.native=Mock(return_value={'valid':True,'proved_contracts':0})
        self.h=WorkspaceHarness(self.input,self.root/'workspace-run',self.native)
        self.proposal=copy.deepcopy(f.proposal);self.proposal['theories'][0]['contract_ids']=[self.h.view.to_public[f.cid]]

    def test_visibility_prompt_is_consistent_in_both_modes(self):
        visible=instruction({**self.h.view.payload,'contract_visibility':'source-theory-visible-v1'})
        self.assertIn('owner_theory',visible)
        self.assertNotIn('Original theory attribution is deliberately absent',visible)
        self.assertIn('choose the new grouping freely',visible)
        blind=instruction({**self.h.view.payload,'contract_visibility':'owner-blind-v1'})
        self.assertIn('Original theory attribution is deliberately absent',blind)
        self.assertNotIn('names are explicitly available as owner_theory',blind)

    def test_public_short_ids_and_owner_independence(self):
        v=self.h.view
        self.assertEqual(list(v.to_original),['C0001'])
        text=instruction(v.payload)
        v.assert_no_owner_disclosure(text)
        self.assertNotIn('contract_relation_candidates',text)
        self.assertNotIn(self.f.cid,text)
        changed=json.loads(self.input.read_text());changed['contracts'][0]['owner_theory']='Refine.Other_R'
        old=changed['contracts'][0]['contract_id'];changed['contracts'][0]['contract_id']='contract:Refine.Other_R:x'
        for c in changed['contract_cards']:
            if c['contract_id']==old:c['contract_id']='contract:Refine.Other_R:x'
        self.assertEqual(short_view(changed).payload,v.payload)

    def test_aggregate_errors_and_no_automatic_fix(self):
        proposal=copy.deepcopy(self.proposal);t=proposal['theories'][0]
        t['name']='bad-name';t['contract_ids']=['Unknown','Unknown']
        t['helper_decisions']=[{'id':'bad-id'}]
        errors=diagnostics(self.h.view.payload,proposal)
        self.assertGreaterEqual(len(errors),4)
        self.assertEqual(t['helper_decisions'][0]['id'],'bad-id')
        self.assertFalse(self.h.dispatch({'action':'submit','proposal':proposal})['valid'])
        self.native.assert_not_called();self.assertIsNone(self.h.submitted)

    def test_verified_definitions_and_inspection_receipt(self):
        r=self.h.dispatch({'action':'definitions','values':['map'],'limit':10})
        self.assertEqual(r['records'][0]['record_id'],'p1')
        self.assertEqual(r['records'][0]['exact_statement'],'map id xs = xs')
        self.assertIn('p1',self.h.state['inspected_premises'])
        self.assertEqual(self.h.dispatch({'action':'definitions','values':["' OR 1=1 --"]})['records'],[])
        self.assertFalse(self.h.dispatch({'action':'contracts','values':[self.f.cid]})['valid'])

    def test_reuse_requires_inspection_and_native_gate(self):
        self.proposal['theories'][0]['helper_decisions']=[{'id':'reuse_map','kind':'reuse_existing_fact','intent':'Reuse the inspected equation.','for_contracts':['C0001'],'premise_ids':['p1']}]
        self.assertFalse(self.h.dispatch({'action':'submit','proposal':self.proposal})['valid'])
        self.h.dispatch({'action':'inspect','values':['p1']})
        self.native.return_value={'valid':False,'diagnostic':'native failure'}
        self.assertFalse(self.h.dispatch({'action':'submit','proposal':self.proposal})['valid'])
        self.assertIsNone(self.h.submitted)
        self.native.return_value={'valid':True,'proved_contracts':0}
        result=self.h.dispatch({'action':'preflight','proposal':self.proposal})
        self.assertTrue(result['valid']);self.assertIsNone(self.h.submitted)
        count=self.native.call_count
        self.assertTrue(self.h.dispatch({'action':'submit','proposal':self.proposal})['submitted'])
        self.assertEqual(self.native.call_count,count)
        canonical,bundle=self.h.artifacts()
        self.assertEqual(canonical['theories'][0]['contract_ids'],[self.f.cid])
        self.assertEqual(bundle['files/Generated_Demo.json']['contracts'],json.loads(self.input.read_text())['contracts'])

    def test_snapshot_does_not_follow_symlink(self):
        secret=self.root/'private';secret.write_text('not model input')
        link=self.root/'plan.json';link.symlink_to(secret)
        self.assertIsNone(snapshot_file(link))

    def test_adjacent_identical_contexts_are_interpreted_once(self):
        payload=copy.deepcopy(self.h.view.payload)
        first=payload['contracts'][0]; first['contract_owned_context']=['context begin interpretation Arch .']
        second={**first,'contract_id':'C0002','name':'other','exact_statement':'lemma other: "True"'}
        payload['contracts'].append(second)
        proposal=copy.deepcopy(self.proposal);proposal['theories'][0]['contract_ids'].append('C0002')
        folder=self.root/'coalesced';render(payload,proposal,folder,'Semantic_Base.Semantic_Root')
        text=(folder/'Generated_Demo.thy').read_text()
        self.assertEqual(text.count('interpretation Arch .'),1)
        self.assertEqual(text.count('oops'),2)
        self.assertIn(first['exact_statement'],text);self.assertIn(second['exact_statement'],text)

    def test_worker_has_no_private_or_checker_mounts(self):
        cmd=container_command({'codex_bin':'/runtime/bin/codex'},self.f.policy,Path('/public'),Path('/workspace'),Path('/api'),Path('/auth'),'worker')
        mounts=[cmd[i+1] for i,v in enumerate(cmd) if v=='--mount']
        self.assertTrue(any('dst=/input,readonly' in x for x in mounts))
        self.assertFalse(any('docker.sock' in x or 'checker-assets' in x or 'planner-input' in x for x in mounts))
        self.assertIn('--read-only',cmd);self.assertIn('--cap-drop=ALL',cmd)
        self.assertIn('web_search="disabled"',cmd)

    def test_renderer_uses_frozen_contexts_and_only_oops(self):
        source=self.root/'rendered';render(self.h.view.payload,self.proposal,source,'Semantic_Base.Semantic_Root')
        text=(source/'Generated_Demo.thy').read_text()
        self.assertIn(self.h.view.payload['contracts'][0]['exact_statement'],text)
        self.assertIn('\noops\n',text);self.assertIn('unbundle l4v_word_context',text)
        self.assertNotIn('sorry',text)


if __name__=='__main__':unittest.main()
