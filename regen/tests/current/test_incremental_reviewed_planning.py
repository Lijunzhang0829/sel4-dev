"""Recovery revisions use generated evidence without bypassing review or preservation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.planning import plan_review
from regen.planning.recovery_revision import recovery_evidence, validate_revision_constraints
from regen.planning.revision_decisions import stable_issue_id
from regen.planning.workspace_harness import WorkspaceHarness
from regen.planning.workspace_harness import short_view
from regen.preparation.refine_campaign import sha256_json
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.effective_time_budget import CHECKPOINT_KIND,POLICY
from regen.tools import reviewed_planning as workflow


class IncrementalReviewedPlanningTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.plan={'schema_version':6,'theories':[
            {'name':'Provider','contract_ids':['goal:provider'],'required_imports':[],
             'purpose':'Provider C0001 remains available'},
            {'name':'Consumer','contract_ids':['goal:consumer'],'required_imports':[]}]}
        self.contracts=[{'contract_id':c,'name':c.split(':')[-1],'owner_theory':'Original.Hidden',
                         'kind':'lemma','exact_statement':'lemma '+c.split(':')[-1]+': True'} for c in ['goal:provider','goal:consumer']]
        self.source={'contracts':self.contracts,'schema_version':1,'artifact_type':'test',
            'benchmark':{'target_session':'Example'},'context_profiles':[],
            'parent_premises':{},'planner_interfaces':{},'planning_input_id':'input:test'}
        self.view=short_view(self.source)
        self.mapping=self.view.to_original
        self.to_public={v:k for k,v in self.mapping.items()}
        self.issue={'id':'I1','kind':'missing_interface','theories':['Consumer','Provider'],
            'contract_ids':['goal:consumer','goal:provider'],'evidence':'Selected external interface absent from imports',
            'scenario':'Consumer cannot use pending provider','suggested_change':'Select a route',
            'tradeoff':'Provider wait or local reconstruction','uncertainty':'Premises still need checking'}
        self.review={'theory_reviews':[
            {'theory':'Provider','assessment':'self-contained','consumed_contract_ids':[]},
            {'theory':'Consumer','assessment':'select provider or local','consumed_contract_ids':['goal:provider']}],
            'issues':[self.issue]}
        self.decision={'issue_id':'I1','decision':'revise','reason':'Use explicit provider import',
            'tradeoff':'Wait for provider seal','remaining_risk':'Consumer premises remain pending',
            'obligations':[{'theory':'Consumer','contract_ids':['goal:consumer'],'provider_contract_ids':['goal:provider'],
                'route':'import','task':'Establish exact preconditions and instantiate provider',
                'completion_condition':'Native check accepts the complete consumer proof'}]}
        self.constraints={'schema_version':1,'immutable_theories':['Provider'],
            'preserve_theory_contract_sets':['Consumer'],
            'fixed_contract_owners':[{'contract_id':'goal:consumer','theory':'Consumer'}]}
        self.seed=self.root/'old/revision'; self.seed.mkdir(parents=True)
        published={**self.plan,'proposal_sha256':sha256_json(self.plan),
            'planning_input_id':'input:test',
            'planner_input_sha256':sha256_json({**self.view.canonical_payload,'planner_evidence':{}})}
        for name,data in [('planner-proposal.json',self.plan),
                ('agent-proposal.json',workflow.replace_exact_ids(self.plan,self.to_public)),
                ('private-contract-map.json',self.mapping),('repository-plan.json',published),
                ('planner-input.json',self.source),('planner-validation-input.json',self.view.canonical_payload),
                ('planner-evidence.json',{})]:
            self.save(self.seed/name,data)
        self.review_path=self.root/'old/review.json'; self.save(self.review_path,self.review)
        self.input=self.root/'input.json'; self.save(self.input,self.source)
        self.policy=self.root/'policy.json'; self.save(self.policy,{'model':{}})
        self.constraints_path=self.root/'constraints.json'; self.save(self.constraints_path,self.constraints)
        self.native=self.root/'generated-result.json'; self.save(self.native,{'status':'RED','remaining':'slot condition'})
        self.save(self.root/'old/proof/run.json',{'run_id':'old'})
        ledger=Ledger(self.root/'old/proof/effective-generation-budget','old')
        tail=ledger.append(CHECKPOINT_KIND,state={'policy':POLICY,'include_planning':True,
            'paused':True,'seconds':500.,'effective_seconds':100.})['event_id']
        self.evidence={'schema_version':1,'authorization':'user authorized recovery',
            'recovery_source_run':str(self.root/'old/proof'),'recovery_source_tail_event_id':tail,
            'notice':'Generated evidence is fallible, not removed human proof history',
            'observations':[{'theory':'Consumer','contract_ids':['goal:consumer'],'kind':'native_proof_failure',
                'summary':'A generated composition failed with a slot condition still open.',
                'evidence':[{'path':str(self.native),'sha256':hashlib.sha256(self.native.read_bytes()).hexdigest()}]}]}
        self.evidence_path=self.root/'evidence.json'; self.save(self.evidence_path,self.evidence)

    @staticmethod
    def save(path,data):
        path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(data))

    def amended(self):
        result=deepcopy(self.plan)
        result['theories'][1]['required_imports']=[{'theory':'Provider'}]
        return result

    def argv(self,output):
        return ['reviewed_planning','--input',str(self.input),'--output',str(output),
            '--policy',str(self.policy),'--revision-seed',str(self.seed),
            '--initial-review',str(self.review_path),'--recovery-evidence',str(self.evidence_path),
            '--revision-constraints',str(self.constraints_path),'--model','gpt-6-astra',
            '--reasoning-effort','low','--max-revision-rounds','1','--allow-external-call']

    def test_imports_and_priority_can_change_without_moving_accepted_contracts(self):
        plan=self.amended(); plan['theories'].reverse()
        validate_revision_constraints(self.plan,plan,self.constraints)

    def test_protected_membership_owner_and_sealed_entries_are_enforced(self):
        mutations=[lambda p:p['theories'][0].update(purpose='reworded'),
            lambda p:p['theories'][1]['contract_ids'].append('new'),
            lambda p:p['theories'].pop(0)]
        for mutate in mutations:
            plan=deepcopy(self.plan); mutate(plan)
            with self.assertRaises(ValueError): validate_revision_constraints(self.plan,plan,self.constraints)
        plan=deepcopy(self.plan); plan['theories'][0]['contract_ids'].append('goal:consumer')
        plan['theories'][1]['contract_ids']=[]
        with self.assertRaisesRegex(ValueError,'owner changed'):
            validate_revision_constraints(self.plan,plan,{'schema_version':1,
                'fixed_contract_owners':self.constraints['fixed_contract_owners']})

    def test_evidence_is_hash_bound_and_paths_not_passed_to_models(self):
        packet=recovery_evidence(self.evidence,self.plan)
        self.assertEqual(packet['observations'][0]['summary'],self.evidence['observations'][0]['summary'])
        self.assertNotIn(str(self.native),json.dumps(packet))
        self.assertNotIn('recovery_source_run',packet)
        self.native.write_text('changed')
        with self.assertRaisesRegex(ValueError,'source changed'):
            recovery_evidence(self.evidence,self.plan)

    def test_seed_matches_exact_id_decoding_without_rewriting_prose(self):
        output=self.root/'seed-copy'; output.mkdir()
        baseline,review=workflow.prepare_revision_seed(output,self.seed,self.review_path,
            self.source,self.constraints)
        self.assertEqual(baseline,self.plan)
        self.assertEqual(review,self.review)
        self.save(self.seed/'private-contract-map.json',{k:('goal:consumer' if v=='goal:provider' else 'goal:provider') for k,v in self.mapping.items()})
        with self.assertRaisesRegex(ValueError,'binding differs'):
            workflow.prepare_revision_seed(self.root/'unused',self.seed,self.review_path,
                self.source,self.constraints)

    def exercise(self,*,violate=False,reject=False,charge=False):
        calls=[]; output=self.root/'result'
        def run(base,stage,args,*,budget=None):
            calls.append((stage,args,budget)); folder=base/stage
            self.save(base/'status.json',{'state':'running','stage':stage})
            if stage=='revision':
                policy=workflow.read(Path(args[args.index('--policy')+1]))
                context=policy['revision_context']
                self.assertEqual(context['recovery_evidence']['observations'][0]['contract_ids'],[self.to_public['goal:consumer']])
                self.assertEqual(context['revision_constraints']['fixed_contract_owners'][0]['contract_id'],self.to_public['goal:consumer'])
                plan=self.amended()
                if violate: plan['theories'][0]['purpose']='changed sealed entry'
                for name,data in [('planner-proposal.json',plan),('repository-plan.json',plan),
                    ('file-generation-list.json',{'files':[]}),('revision-decisions.json',[
                        workflow.replace_exact_ids(self.decision,self.to_public)])]:
                    self.save(folder/name,data)
            elif stage=='final-review':
                self.assertIn('--recovery-evidence',args)
                self.assertIn('--revision-constraints',args)
                review=deepcopy(self.review)
                if not reject: review['issues']=[]
                review['decision_reviews']=[{'issue_id':stable_issue_id(self.issue),
                    'status':'needs_revision' if reject else 'accepted',
                    'reason':'Allocates exact premises and provider wait'}]
                self.save(folder/'review.json',review)
            else: self.fail('Recovery must not create a fresh draft: '+stage)
        argv=self.argv(output)
        if charge: argv+=['--include-planning-time','--effective-seconds','400']
        with patch('sys.argv',argv),patch.object(workflow,'run_command',side_effect=run):
            workflow.main()
        return output,calls

    def test_recovery_goes_straight_to_revision_and_freezes_only_after_review(self):
        output,calls=self.exercise()
        self.assertEqual([c[0] for c in calls],['revision','final-review'])
        frozen=workflow.read(output/'frozen-plan.json')
        self.assertEqual(frozen['planning_mode'],'incremental-recovery-v1')
        self.assertTrue(frozen['preservation_constraints_checked'])
        self.assertIn('recovery-evidence.json',frozen['artifacts'])
        self.assertTrue(workflow.read(output/'binding.json')['historical_execution_inputs'])
        self.assertFalse(workflow.read(output/'revision/review-handoff.json')['legacy'])

    def test_violated_constraints_or_rejected_review_cannot_freeze(self):
        with self.assertRaisesRegex(ValueError,'immutable theory changed'):
            self.exercise(violate=True)
        self.assertFalse((self.root/'result/frozen-plan.json').exists())

    def test_unresolved_recovery_review_blocks_publication(self):
        with self.assertRaisesRegex(ValueError,'unhandled issues'):
            self.exercise(reject=True)
        self.assertFalse((self.root/'result/frozen-plan.json').exists())

    def test_budget_carries_source_identity_into_new_planning_clock(self):
        with patch('regen.infrastructure.reviewed_planning_budget.ReviewedPlanningBudget') as budget:
            self.exercise(charge=True)
        self.assertEqual(budget.call_args.kwargs['seconds'],400)
        binding=budget.call_args.kwargs['binding']
        self.assertEqual(binding['recovery_source_run'],self.evidence['recovery_source_run'])
        self.assertEqual(binding['recovery_source_tail_event_id'],self.evidence['recovery_source_tail_event_id'])
        budget.return_value.complete.assert_called_once()

    def test_stale_source_or_increased_allowance_rejected_before_model(self):
        self.evidence['recovery_source_tail_event_id']='a'*64
        with self.assertRaisesRegex(ValueError,'tail changed'):
            recovery_evidence(self.evidence,self.plan)
        argv=self.argv(self.root/'invalid-budget')+['--include-planning-time','--effective-seconds','401']
        # The on-disk evidence still has the valid source tail.
        with patch('sys.argv',argv),patch.object(workflow,'run_command') as command:
            with self.assertRaisesRegex(ValueError,'paused source remainder'):
                workflow.main()
        command.assert_not_called()

    def test_seed_publication_cannot_bind_a_different_proposal(self):
        published=workflow.read(self.seed/'repository-plan.json')
        published['proposal_sha256']='b'*64
        self.save(self.seed/'repository-plan.json',published)
        with self.assertRaisesRegex(ValueError,'published proposal/input'):
            workflow.prepare_revision_seed(self.root/'invalid-seed',self.seed,self.review_path,
                                           self.source,self.constraints)

    def test_reviewer_receives_audited_generated_observations_and_constraints(self):
        output=self.root/'review-output'
        plan=self.root/'amended.json'; self.save(plan,self.amended())
        argv=['plan_review','--plan',str(plan),'--input',str(self.input),'--output',str(output),
            '--recovery-evidence',str(self.evidence_path),
            '--recovery-baseline-plan',str(self.seed/'planner-proposal.json'),
            '--revision-constraints',str(self.constraints_path)]
        with patch('sys.argv',argv):plan_review.main()
        packet=workflow.read(output/'review-input.json')
        self.assertEqual(packet['revision_constraints'],self.constraints)
        self.assertEqual(packet['recovery_evidence']['observations'][0]['contract_ids'],['goal:consumer'])
        self.assertNotIn(str(self.native),json.dumps(packet))
        self.assertTrue(workflow.read(output/'binding.json')['historical_data_in_review'])

    def test_harness_rejects_protected_change_before_preflight(self):
        harness=object.__new__(WorkspaceHarness)
        harness.view=type('View',(),{'payload':{}})()
        harness.state={'inspected_contracts':{},'inspected_premises':{}}
        harness.recovery_baseline_plan=self.plan; harness.revision_constraints=self.constraints
        plan=deepcopy(self.plan); plan['theories'][0]['purpose']='illegal edit'
        with patch('regen.planning.workspace_harness.diagnostics',return_value=[]),\
                patch('regen.planning.workspace_harness.validate_draft') as native:
            result=harness.validate(plan)
        self.assertFalse(result['valid']); native.assert_not_called()


if __name__=='__main__':unittest.main()
