"""Semantic-risk review gates; native truth remains an independent obligation."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from regen.tests.current import test_sketch_workflow as workflow_fixtures
from regen.planning.sketch_workflow import review_sketch, probe_helpers
from regen.planning.sketch_check import authorize, strategy_options
from regen.planning.sketch_semantics import WorkflowBlocked, require_critical_predecessors

class SemanticReviewTests(unittest.TestCase):
    def setUp(self):
        self.base=workflow_fixtures.WorkflowTests();self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.store=self.base.store
        self.store.require_semantic_review=True;self.store.allow_formal_leaf=True

    def row(self):
        return {'helper_id':'helper','use_indices':[0],'domain_scope':'local','premises':'local',
                'conclusion':'local','generalization':'none','burden':'isolated',
                'reduction':'A standalone propositional subgoal, preserving the exact fixed target.',
                'helper_role':'local','proof_mode':'formal','proof_plan':'Prove the existing conjunction with simp.',
                'coverage':{'definition_refs':['HOL.conjI'],
                            'branches':[{'case':'propositional conjunction','status':'handled',
                                         'evidence':'The existing helper statement has no object representation split.'}],
                            'premise_sources':[],'unknowns':[]}}

    def review(self,row):
        return review_sketch(self.store,self.store.read()['source_sha256'],'contract',
                             self.base.closed['groups'][0]['receipt_id'],[row])

    def test_v2_mode_is_explicit_and_v1_does_not_change_policy(self):
        self.assertTrue(strategy_options('sketch-reviewed-v2')['require_semantic_review'])
        self.assertTrue(strategy_options('sketch-reviewed-v2')['allow_formal_leaf'])
        self.assertNotIn('require_semantic_review',strategy_options('sketch-reviewed-v1'))

    def test_missing_coverage_cannot_authorize_formal_helper(self):
        row=self.row();row.pop('coverage')
        with self.assertRaisesRegex(WorkflowBlocked,'coverage evidence'):self.review(row)
        with self.assertRaisesRegex(ValueError,'decomposition review'):
            authorize(self.store,self.store.read()['source'],['helper'])

    def test_explicit_formal_plan_does_not_require_successful_short_probe(self):
        reviewed=self.review(self.row())
        self.assertEqual(reviewed['helper_feasibility'],{'helper':'unestablished'})
        self.assertFalse(reviewed['semantic_claims_verified'])
        self.assertFalse(reviewed['difficulty_certified'])
        authorize(self.store,self.store.read()['source'],['helper'])
        self.assertEqual(self.store.state['proofs'],{})

    def test_timeout_probe_remains_unestablished(self):
        checker=self.base.checker;original=checker.check_sketch_source
        def timeout(*args,**kwargs):
            x=original(*args,**kwargs);x.update(verdict='red',returncode=124,log_content='TIMEOUT')
            import hashlib
            x['log_sha256']=hashlib.sha256(b'TIMEOUT').hexdigest()
            return x
        with patch.object(checker,'check_sketch_source',side_effect=timeout):
            probe=probe_helpers(checker,self.store,self.store.read()['source_sha256'])['results'][0]
        row=self.row();row.update(proof_mode='diagnostic',probe_id=probe['probe_id'])
        reviewed=self.review(row)
        self.assertEqual(probe['status'],'candidate_timeout')
        self.assertEqual(reviewed['helper_feasibility']['helper'],'unestablished')
        self.assertEqual(self.store.state['proofs'],{})

    def test_unknown_branch_requires_honest_unresolved_list(self):
        row=self.row();row['coverage']['branches'][0]['status']='unknown'
        with self.assertRaisesRegex(WorkflowBlocked,'unresolved coverage'):self.review(row)
        row['coverage']['unknowns']=['This branch has not been established.']
        reviewed=self.review(row)
        self.assertEqual(reviewed['helper_feasibility']['helper'],'unestablished')

    def test_formal_label_without_plan_does_not_bypass_diagnostic_gate(self):
        row=self.row();row['proof_plan']=' '
        with self.assertRaisesRegex(WorkflowBlocked,'proof_plan'):self.review(row)

    def priority(self,needed,groups=None):
        group={'target_entry_id':'parent','helper_ids':['critical','local']}
        review={'reviews':[{'helper_id':'critical','helper_role':'classification'},
                           {'helper_id':'local','helper_role':'local'}]}
        with patch('regen.planning.sketch_semantics.load_groups',return_value=groups or [group]):
            require_critical_predecessors(self.store,{}, {},group,review,needed)

    def test_unproved_classifier_blocks_sibling_with_exact_check_action(self):
        with self.assertRaises(WorkflowBlocked) as blocked:self.priority({'local'})
        self.assertEqual(blocked.exception.code,'critical_helper_unproved')
        self.assertEqual(blocked.exception.details['critical_entry_ids'],['critical'])
        action=blocked.exception.next_actions[0]
        self.assertEqual(action['requires_agent_fields'],[])
        self.assertEqual(action['request']['entry_ids'],['critical'])
        self.assertEqual(action['request']['source_sha256'],self.store.read()['source_sha256'])

    def test_critical_itself_and_unrelated_contract_are_allowed(self):
        self.priority({'critical'})
        self.priority({'unrelated-contract'})

    def test_newly_green_classifier_unlocks_sibling_without_new_review(self):
        self.store.state['proofs']['critical']='by simp'
        self.priority({'local'})

    def test_helpers_serving_a_critical_child_sketch_remain_allowed(self):
        self.priority({'local'},groups=[{'target_entry_id':'critical','helper_ids':['local']}])

    def test_v2_prompt_does_not_require_probing_all_leaves(self):
        from regen.planning.parallel_file_planner import GENERATION_STRATEGIES
        prompt=GENERATION_STRATEGIES['sketch-reviewed-v2']
        self.assertNotIn('Briefly probe all leaves',prompt)
        self.assertNotIn('Every\npending leaf needs a native probe',prompt)
        self.assertIn('concrete proof_plan',prompt)
