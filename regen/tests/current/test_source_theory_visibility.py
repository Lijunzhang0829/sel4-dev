"""Explicit provenance visibility changes neither contracts nor proof access."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from regen.tests.current import test_workspace_planning as fixtures
from regen.planning.workspace_harness import short_view,WorkspaceHarness
from regen.planning.workspace_planner import instruction,container_command
from regen.planning.repository_planner import validate_policy
from regen.planning.owner_blind import CONTRACT_FIELDS

class SourceTheoryVisibilityTest(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.WorkspacePlanningTest();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.source=json.loads(self.f.input.read_text())

    def test_only_declared_source_metadata_is_added_and_aliases_stay_stable(self):
        blind=short_view(self.source)
        visible=short_view(self.source,visibility='source-theory-visible-v1')
        self.assertEqual(blind.to_original,visible.to_original)
        for c in visible.payload['contracts']:
            original=next(x for x in self.source['contracts'] if x['contract_id']==visible.to_original[c['contract_id']])
            self.assertEqual(c['owner_theory'],original['owner_theory'])
            self.assertLessEqual(set(c),set(CONTRACT_FIELDS)|{'contract_id','owner_theory'})
            self.assertEqual(c['exact_statement'],original['exact_statement'])
            self.assertEqual(c['contract_owned_context'],original['contract_owned_context'])
        self.assertEqual(visible.payload['contract_cards'][0]['owner_theory'],self.source['contracts'][0]['owner_theory'])
        visible.assert_no_owner_disclosure(json.dumps(visible.payload))
        with self.assertRaises(ValueError):blind.assert_no_owner_disclosure(json.dumps(visible.payload))
        with self.assertRaises(ValueError):visible.assert_no_owner_disclosure(self.source['contracts'][0]['contract_id'])

    def test_visible_harness_inspection_and_prompt_match_frozen_mode(self):
        h=WorkspaceHarness(self.f.input,self.f.root/'visible-run',self.f.native,visibility='source-theory-visible-v1')
        result=h.dispatch({'action':'contracts','values':['C0001']})
        self.assertIn(self.source['contracts'][0]['owner_theory'],json.dumps(result))
        text=instruction(h.view.payload,6)
        self.assertIn('explicitly available as owner_theory',text)
        self.assertNotIn('Never seek removed proofs, source owners',text)
        self.assertNotIn('do not reconstruct original source ownership',text)
        with self.assertRaises(FileExistsError):WorkspaceHarness(self.f.input,self.f.root/'visible-run',self.f.native)

    def test_new_defaults_use_astra_medium_and_unknown_visibility_is_rejected(self):
        for filename in ('repository-planning-policy.json','repository-planning-policy-workspace.json'):
            policy=json.loads((Path(__file__).resolve().parents[2]/'config'/filename).read_text())
            validate_policy(policy)
            self.assertEqual(policy['model']['model'],'gpt-6-astra')
            self.assertEqual(policy['model']['reasoning_effort'],'medium')
            self.assertEqual(policy['contract_visibility'],'source-theory-visible-v1')
            policy['contract_visibility']='all-raw-sources'
            with self.assertRaises(ValueError):validate_policy(policy)
        with self.assertRaises(ValueError):short_view(self.source,visibility='all-raw-sources')
