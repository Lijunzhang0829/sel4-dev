import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from regen.planning.owner_blind import OwnerBlindView
from regen.planning.iterative_planner import agent_prompt
from regen.planning import repository_planner
from regen.tests.current import test_iterative_repository_planner as fixtures


class OwnerBlindPlanningTest(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.IterativeRepositoryPlannerTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.payload=json.loads(self.fixture.input.read_text())

    def test_no_owner_metadata_in_cards_inspection_or_prompt(self):
        view=OwnerBlindView(self.payload)
        text=agent_prompt(view.payload,{},None,1)
        for term in ('Refine.Demo_R','Demo_R.thy','owner_groups','theory_import_graph','same_owner_theory','same-owner'):
            self.assertNotIn(term,text)
        self.assertNotIn(self.fixture.cid,text)
        records=view.payload['contracts']
        self.assertNotIn('owner_theory',records[0])
        self.assertNotIn('owner_file_slot',records[0])
        self.assertEqual(records[0]['exact_statement'],self.payload['contracts'][0]['exact_statement'])
        with self.assertRaisesRegex(ValueError,'attribution'):
            view.assert_no_owner_disclosure(self.fixture.cid)

    def test_owner_reassignment_and_source_order_do_not_change_public_view(self):
        source=copy.deepcopy(self.payload)
        second=copy.deepcopy(source['contracts'][0]);second.update(contract_id='contract:Refine.Other_R:second:1',owner_theory='Refine.Other_R',name='second',exact_statement='lemma second: "Q x"')
        source['contracts'].append(second)
        second_card=copy.deepcopy(source['contract_cards'][0]);second_card.update(contract_id=second['contract_id'],owner_theory='Refine.Other_R',name='second')
        source['contract_cards'].append(second_card)
        changed=copy.deepcopy(source)
        for index,c in enumerate(changed['contracts']):
            old=c['contract_id'];c.update(contract_id=f'contract:Refine.Reassigned_{index}_R:x:1',owner_theory=f'Refine.Reassigned_{index}_R',owner_file_slot='Changed.thy')
            next(card for card in changed['contract_cards'] if card['contract_id']==old).update(contract_id=c['contract_id'],owner_theory=c['owner_theory'])
        changed['contracts'].reverse();changed['contract_cards'].reverse()
        changed['owner_groups']={'irrelevant':[]};changed['theory_import_graph']={'Other_R':['Demo_R']}
        self.assertEqual(OwnerBlindView(source).payload,OwnerBlindView(changed).payload)

    def test_round_trip_inspection_validation_and_submission_keep_source_contracts_exact(self):
        f=self.fixture
        f.policy['contract_visibility']='owner-blind-v1'
        view=OwnerBlindView(self.payload);alias=view.to_public[f.cid]
        proposal=copy.deepcopy(f.proposal);proposal['theories'][0]['contract_ids']=[alias]
        actions=[f.op('inspect_contracts',contract_ids=[alias]),
                 f.op('validate_plan',proposal={'schema_version':4,'theories':[]}),
                 f.op('read_turn',turn=1),f.op('submit_plan',proposal=proposal)]
        def call(text,prefix,**options):
            self.assertNotIn(f.cid,text)
            self.assertNotIn('Refine.Demo_R',text)
            self.assertNotIn('owner_file_slot',text)
            n=int(prefix.name.rsplit('-',1)[1])
            return json.dumps(actions[n-1]),{},prefix
        with patch.object(repository_planner,'_direct_call',side_effect=call):
            repository_planner.run_prepared(f.input,f.policy,f.output,allow_external_call=True)
        projection=json.loads((f.output/'files/Generated_Demo.json').read_text())
        self.assertEqual(projection['contracts'],self.payload['contracts'])
        self.assertEqual(projection['contract_ids'],[f.cid])
        self.assertEqual(json.loads((f.output/'agent-proposal.json').read_text()),proposal)
        self.assertEqual(json.loads((f.output/'private-contract-map.json').read_text())[alias],f.cid)

    def test_public_benchmark_name_is_not_a_contract_owner_mapping(self):
        source=copy.deepcopy(self.payload)
        source['contracts'][0]['owner_theory']='Refine.Refine'
        view=OwnerBlindView(source)
        view.assert_no_owner_disclosure(json.dumps(view.payload))
        self.assertNotIn('owner_theory',view.payload['contracts'][0])
        with self.assertRaises(ValueError): view.assert_no_owner_disclosure('Refine.Refine')

    def test_raw_source_id_cannot_be_used_to_query_contracts(self):
        from regen.preparation.planning_input import PlanningInputQueries
        view=OwnerBlindView(self.payload)
        with self.assertRaisesRegex(ValueError,'public contract ID'):
            view.queries(PlanningInputQueries(self.fixture.input)).inspect_contracts([self.fixture.cid])


if __name__=='__main__': unittest.main()
