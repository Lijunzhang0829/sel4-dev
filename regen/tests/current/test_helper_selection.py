from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.planning.contract_and_or import SCHEMA
from regen.planning.helper_selection import recommend, install, current_selection, STRATEGY, route_key, record_attempt
from regen.planning.theory_document import DocumentStore
from regen.tests.current.test_proof_commits import fixture


def problem(root='contract', helper='helper'):
    return {'schema_version':1,'method':SCHEMA,'root_goal':root,'goals':[
        {'id':root,'role':'contract','routes':[
            {'id':'direct','requires':[],'estimate_seconds':100},
            {'id':'via','requires':[helper],'estimate_seconds':1}]},
        {'id':helper,'role':'helper','routes':[{'id':'help','requires':[],'estimate_seconds':1}]}]}


class SelectionTests(unittest.TestCase):
    def test_failed_cost_survives_restore_and_changes_frontier_without_renaming_reset(self):
        p = problem()
        goal = p['goals'][1]
        key = route_key(self.package, self.entries, goal, goal['routes'][0])
        self.state['helper_route_attempt'] = {'key':key, 'goal_id':'helper', 'request_number':9}
        feedback = {'status':'rejected_batch', 'node_results':[{'node_id':'helper','status':'failed'}],
                    'check':{'verdict':'red','candidate_execution_ms':150000}}
        record_attempt(self.state, feedback, 9)
        record_attempt(self.state, feedback, 9)
        self.assertEqual(self.state['helper_route_costs'][key]['failures'], 1)
        self.assertEqual(self.state['helper_route_costs'][key]['failed_seconds'], 150)
        restored = json.loads(json.dumps(self.state))
        p['goals'][1]['routes'][0]['id'] = 'renamed'
        p['goals'][1]['routes'][0]['estimate_seconds'] = 0
        result = recommend(p,self.package,self.entries,{},restored)
        self.assertEqual(result['decision']['goal_id'], 'contract')
        self.assertEqual(result['route_attempts']['renamed'], 1)

    def test_unexecuted_cached_and_infrastructure_failures_do_not_penalize(self):
        for status, scope, cached in [('not_reached',None,False), ('failed','native_preparation',False),
                                       ('failed',None,True), ('unknown',None,False)]:
            state=deepcopy(self.state)
            state['helper_route_attempt']={'key':'k','goal_id':'helper','request_number':1}
            feedback={'status':'rejected_batch','cached_failure':cached,
                      'check':{'candidate_execution_ms':900000,'failure_scope':scope},
                      'node_results':[{'node_id':'helper','status':status}]}
            record_attempt(state,feedback,1)
            self.assertEqual(state['helper_route_costs']['k']['failures'],0)

    def test_changed_statement_does_not_inherit_unrelated_failure(self):
        p=problem();goal=p['goals'][1];route=goal['routes'][0]
        key=route_key(self.package,self.entries,goal,route)
        changed=deepcopy(self.entries)
        next(n for n in changed if n['id']=='helper')['statement']='lemma helper: "False"'
        self.assertNotEqual(key,route_key(self.package,changed,goal,route))

    def setUp(self):
        self.package,self.state=fixture()
        self.entries=self.state['graph']['nodes']

    def advice(self, p=None, proofs=None):
        return recommend(p or problem(),self.package,self.entries,
                         self.state['proofs'] if proofs is None else proofs,self.state)

    def test_select_helper_then_parent_then_solved_without_mutating_authority(self):
        original=deepcopy(self.state)
        self.assertEqual(self.advice()['decision']['goal_id'],'helper')
        self.assertEqual(self.state,original)
        self.state['proofs']['helper']='by simp';self.state['accepted_order']=['helper']
        self.assertEqual(self.advice()['decision']['goal_id'],'contract')
        self.state['proofs']['contract']='by simp';self.state['accepted_order'].append('contract')
        self.entries.sort(key=lambda n:self.state['accepted_order'].index(n['id']))
        self.assertEqual(self.advice()['decision']['action'],'SOLVED')

    def test_unchecked_draft_body_is_not_certified(self):
        result=self.advice(proofs={'helper':'by simp'})
        self.assertEqual(result['certified_ids'],[])
        self.assertEqual(result['decision']['goal_id'],'helper')

    def test_frontier_limit_requests_revision_without_stopping_proof_generation(self):
        p=problem();p['max_frontier_plans']=1
        advice=self.advice(p)
        self.assertEqual(advice['status'],'frontier_limit_exceeded')
        self.assertEqual(advice['decision']['action'],'REVISE_SELECTION')

    def test_changed_accepted_proof_is_not_a_legal_certified_prefix(self):
        self.state['proofs']['helper']='by simp';self.state['accepted_order']=['helper']
        result=self.advice(proofs={'helper':'by blast'})
        self.assertFalse(result['accepted_prefix_unchanged'])
        self.assertEqual(result['certified_ids'],[])

    def test_invalid_goals_cycles_nonfinite_costs_and_fake_authority_rejected(self):
        mutations=[lambda p:p.update(certified=['contract']),
            lambda p:p.update(root_goal='helper'),
            lambda p:p['goals'][1].update(id='foreign'),
            lambda p:p['goals'][1]['routes'][0].update(requires=['contract']),
            lambda p:p['goals'][0]['routes'][0].update(estimate_seconds=float('nan')),
            lambda p:p.update(max_frontier_plans=9999)]
        for mutate in mutations:
            p=problem();mutate(p)
            with self.subTest(problem=p),self.assertRaises(ValueError):self.advice(p)

    def test_restore_binding_stale_declarations_and_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=DocumentStore(tmp,self.package,self.state,['Main'])
            proposal={'action':'select_helpers','source_sha256':store.read()['source_sha256'],'problem':problem()}
            install(store,self.state,proposal,{'test':True})
            restored=DocumentStore(tmp,self.package,self.state,['Main'])
            self.assertEqual(current_selection(restored,self.state)['decision']['goal_id'],'helper')
            doc=store.read()
            store.edit({'action':'edit_theory','base_sha256':doc['source_sha256'],
                'source':doc['source'].replace('True & True','True | True')},{})
            self.assertEqual(current_selection(store,self.state)['status'],'stale_declarations')
            path=Path(tmp)/'helper-selection.json';value=json.loads(path.read_text())
            value['problem']['root_goal']='helper';path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'binding'):current_selection(store,self.state)
            with self.assertRaisesRegex(ValueError,'stale'):install(store,self.state,proposal,{})


class ControllerSelectionTests(unittest.TestCase):
    """Offline historical cost/search tests above remain independently usable."""
    def test_retired_strategy_requires_the_original_frozen_application(self):
        from regen.planning.parallel_file_planner import generation_strategy
        with self.assertRaisesRegex(ValueError,'removed.*frozen'):
            generation_strategy(STRATEGY)

    def setUp(self):
        from regen.tests.current.test_theory_document import DocumentControllerTests
        self.base=DocumentControllerTests();self.base.setUp();self.addCleanup(self.base.doCleanups)

    def test_baseline_cannot_enable_selection_by_tool_call(self):
        with self.assertRaisesRegex(ValueError,'removed'):
            self.base.call({'action':'select_helpers'})

    def test_baseline_checks_without_a_selection_or_hidden_recomputation(self):
        draft=self.base.read()['document']
        saved=self.base.call({'action':'edit_theory','base_sha256':draft['source_sha256'],
                             'edits':[{'old':'oops','new':'by simp'}]})
        with patch('regen.planning.helper_selection.current_selection',side_effect=AssertionError('retired selection')):
            result=self.base.call({'action':'check_theory','source_sha256':saved['source_sha256']})
        self.assertEqual(result['feedback']['status'],'accepted')
        self.assertNotIn('helper_selection',result)
        self.assertNotIn('helper_selection',self.base.read())


if __name__=='__main__':unittest.main()
