"""Exercise agent iteration, explicit submission and interrupted replay."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from regen.planning import repository_planner
from regen.preparation.planning_input import prepare
from regen.tests.current import test_planning_input as fixtures


class IterativeRepositoryPlannerTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PlanningInputTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.output = self.root / 'iterative-output'
        contracts, premises = self.root/'contracts.json', self.root/'premises.json'
        contracts.write_text(json.dumps({'contracts':[self.fixture.contract]}))
        premises.write_text(json.dumps({'records':[self.fixture.premise]}))
        dataset = self.root/'dataset'
        prepare(self.fixture.l4v, contracts, premises, dataset)
        self.input = dataset/'repository-planning-input.json'
        self.policy = json.loads(Path('regen/fixtures/repository-planning-policy-iterative.json').read_text())
        self.policy['model']['model'] = 'test-model'
        self.policy.pop('contract_visibility', None)
        self.cid = self.fixture.contract['contract_id']
        self.proposal = {
            'schema_version':4, 'ordering_rationale':'One independent group is ready.',
            'contract_relations':[], 'unresolved_retrieval_questions':[],
            'theories':[{'name':'Generated_Demo','purpose':'Prove the fixed contract.',
                'grouping_reason':'One common context.', 'contract_ids':[self.cid],
                'context_profile_ids':['context:Refine:parents'],
                'helper_decisions':[], 'suggested_predecessors':[]}],
        }

    def run_script(self, actions, *, crash_at=None, seen=None):
        def call(text, prefix, **options):
            turn = int(prefix.name.rsplit('-',1)[1])
            if seen is not None: seen.append((turn, text))
            if turn == crash_at: raise RuntimeError('transport interrupted')
            self.assertFalse((self.output/'repository-plan.json').exists())
            if turn > len(actions): self.fail('unexpected extra model turn')
            action = actions[turn-1]
            return (action if isinstance(action,str) else json.dumps(action)), {}, prefix
        with patch.object(repository_planner,'_direct_call',side_effect=call):
            return repository_planner.run_prepared(self.input,self.policy,self.output,allow_external_call=True)

    @staticmethod
    def op(action, **arguments): return {'action':action,'arguments':arguments}

    def test_more_than_three_corrections_and_validation_does_not_publish(self):
        invalid=copy.deepcopy(self.proposal)
        invalid['theories'][0]['contract_ids']=[]
        actions=[self.op('submit_plan',proposal=invalid) for _ in range(5)]
        actions += [self.op('search_premises',query='Base base_rule',context_profile_ids=['context:Refine:parents']),
                    self.op('inspect_premises',record_ids=['premise:base']),
                    self.op('validate_plan',proposal=self.proposal),
                    self.op('read_turn',turn=6),
                    self.op('inspect_contracts',contract_ids=[self.cid]), self.op('submit_plan')]
        seen=[]
        result=self.run_script(actions,seen=seen)
        self.assertEqual(result['contracts'],1)
        self.assertEqual(len(seen),11)
        rejected=json.loads((self.output/'turns/000005.json').read_text())
        self.assertEqual(rejected['result']['contract_coverage']['missing'],[self.cid])
        validated=json.loads((self.output/'turns/000008.json').read_text())
        self.assertTrue(validated['result']['valid'])
        self.assertFalse(validated['result']['published'])
        self.assertIn('CURRENT DRAFT',seen[8][1])
        evidence=json.loads((self.output/'planner-evidence.json').read_text())
        self.assertEqual(evidence['inspected_parent_premises'][0]['record_id'],'premise:base')

    def test_invalid_json_and_unknown_operations_are_returned_for_correction(self):
        actions=['{broken', self.op('proof_state_query',query='P'),
                 self.op('inspect_contracts',contract_ids=['unknown']),
                 self.op('submit_plan',proposal=self.proposal)]
        seen=[]
        self.run_script(actions,seen=seen)
        self.assertIn('request_error',seen[1][1])
        self.assertIn('unknown planner operation',seen[2][1])
        self.assertIn('unknown contract ID',seen[3][1])

    def test_validation_after_inspection_and_illegal_premise_rejection(self):
        proposed=copy.deepcopy(self.proposal)
        proposed['theories'][0]['helper_decisions']=[{
            'id':'reuse_base','kind':'reuse_existing_fact','intent':'Use a legal base result.',
            'for_contracts':[self.cid],'premise_ids':['premise:base']}]
        actions=[self.op('validate_plan',proposal=proposed),
                 self.op('inspect_premises',record_ids=['premise:base']),
                 self.op('submit_plan')]
        self.run_script(actions)
        first=json.loads((self.output/'turns/000001.json').read_text())
        self.assertFalse(first['result']['valid'])
        self.assertTrue((self.output/'repository-plan.json').exists())

    def test_recovery_replays_same_prompts_and_keeps_prior_turns(self):
        actions=[self.op('inspect_contracts',contract_ids=[self.cid]),
                 self.op('validate_plan',proposal=self.proposal),self.op('submit_plan')]
        before=[]
        with self.assertRaisesRegex(RuntimeError,'transport interrupted'):
            self.run_script(actions,crash_at=3,seen=before)
        saved={p.name:p.read_bytes() for p in (self.output/'turns').glob('*.json')}
        after=[]
        self.run_script(actions,seen=after)
        self.assertEqual(before,after)
        for name,data in saved.items(): self.assertEqual((self.output/'turns'/name).read_bytes(),data)

    def test_cumulative_evidence_and_notes_survive_other_operations(self):
        actions=[self.op('inspect_contracts',contract_ids=[self.cid]),
                 self.op('inspect_premises',record_ids=['premise:base']),
                 self.op('save_notes',notes='Preserve this planning observation.'),
                 self.op('search_premises',query='Base',context_profile_ids=['context:Refine:parents']),
                 self.op('stop',reason='Local memory test complete.')]
        seen=[]
        self.run_script(actions,seen=seen)
        text=seen[-1][1]
        compact,_=json.JSONDecoder().raw_decode(text.split('COMPACT LEGAL INPUT:\n')[1])
        evidence=compact['planner_evidence']
        self.assertEqual(evidence['inspected_contracts'][0]['contract_id'],self.cid)
        self.assertEqual(evidence['inspected_parent_premises'][0]['record_id'],'premise:base')
        self.assertIn('Preserve this planning observation.',text)

    def test_infrastructure_prompt_fix_preserves_completed_turn_prompts(self):
        from regen.planning import iterative_planner
        from regen.infrastructure.llm_transcript import artifact_paths
        actions=[self.op('inspect_contracts',contract_ids=[self.cid]),
                 self.op('validate_plan',proposal=self.proposal),self.op('submit_plan')]
        before=[]
        with self.assertRaises(RuntimeError): self.run_script(actions,crash_at=3,seen=before)
        for turn,text in before[:2]:
            prefix=self.output/'transcript'/f'repository-agent-{turn:06d}'
            prefix.parent.mkdir(parents=True,exist_ok=True)
            artifact_paths(prefix)['prompt'].write_text(text)
        original=iterative_planner.agent_prompt
        after=[]
        with patch.object(iterative_planner,'agent_prompt',side_effect=lambda *args: original(*args)+'\nInfrastructure correction'):
            self.run_script(actions,seen=after)
        self.assertEqual(before[:2],after[:2])
        self.assertTrue(after[2][1].endswith('Infrastructure correction'))

    def test_agent_can_stop_without_publishing(self):
        result=self.run_script([self.op('stop',reason='Need a missing contract clarification.')])
        self.assertEqual(result['verdict'],'stopped')
        self.assertFalse((self.output/'repository-plan.json').exists())
        self.assertTrue((self.output/'planner-stop.json').exists())

    def test_iterative_policy_rejects_attempt_cap(self):
        policy=copy.deepcopy(self.policy);policy['model']['max_attempts']=3
        with self.assertRaisesRegex(ValueError,'no proposal-attempt limit'):
            repository_planner.validate_policy(policy)


if __name__=='__main__': unittest.main()
