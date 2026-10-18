import unittest
from copy import deepcopy
from regen.planning.plan_review import analyze, review_input, validate_review, history_comparison


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.contracts = [{'contract_id': c, 'name': c, 'exact_statement': c, 'owner_file_slot': 'secret'} for c in ['a','b','c']]
        self.plan = {'schema_version':6, 'theories':[
            {'name':'A','contract_ids':['a'],'required_imports':[]},
            {'name':'B','contract_ids':['b'],'required_imports':[{'theory':'A'}]},
            {'name':'C','contract_ids':['c'],'required_imports':[{'theory':'B'}]}]}
        self.review = {'theory_reviews':[{'theory': t, 'assessment':'reviewed', 'consumed_contract_ids':[]} for t in ['A','B','C']], 'issues':[]}

    def test_transitive_visibility_and_wait_scope(self):
        rows = analyze(self.plan, self.contracts)['theories']
        self.assertEqual(rows[0]['blocked_contracts_if_unsealed'], 2)
        self.assertEqual(rows[2]['transitive_providers'], ['A','B'])

    def test_invalid_graph_and_coverage(self):
        for kind in ['duplicate','cycle','unknown','missing']:
            p=deepcopy(self.plan)
            if kind=='duplicate': p['theories'][1]['contract_ids']=['a']
            if kind=='cycle': p['theories'][0]['required_imports']=[{'theory':'C'}]
            if kind=='unknown': p['theories'][0]['required_imports']=[{'theory':'Z'}]
            if kind=='missing': p['theories'].pop()
            with self.assertRaises(ValueError): analyze(p,self.contracts)

    def test_no_original_paths_in_prompt(self):
        x=review_input(self.plan,self.contracts,analyze(self.plan,self.contracts))
        self.assertNotIn('owner_file_slot',x['contracts'][0])

    def test_review_requires_all_theories_and_external_ids(self):
        validate_review(self.review,self.plan,self.contracts)
        x=deepcopy(self.review);x['theory_reviews'].pop()
        with self.assertRaises(ValueError): validate_review(x,self.plan,self.contracts)
        for cid in ['z','a']:
            x=deepcopy(self.review); x['theory_reviews'][0]['consumed_contract_ids']=[cid]
            with self.assertRaises(ValueError): validate_review(x,self.plan,self.contracts)

    def test_native_helper_paths_and_noncausal_status(self):
        self.review['issues']=[{'id':'I1','contract_ids':['a','c']}]
        history={'native_edges':[['c','helper:x'],['helper:x','a'],['a','c']]}
        r=history_comparison(self.plan,analyze(self.plan,self.contracts),self.review,history)
        self.assertEqual(len(r['uncovered_paths']),1)
        self.assertEqual(r['uncovered_paths'][0]['consumer'],'a')
        c=next(p for p in r['cross_theory_contract_paths'] if p['consumer']=='c')
        self.assertTrue(c['covered_by_plan'])
        self.assertEqual(c['native_path'],['c','helper:x','a'])
        self.assertFalse(r['issues'][0]['actual_planning_failure_confirmed'])

    def test_unknown_issue_is_rejected(self):
        self.review['issues']=[{'id':'I','kind':'grouping','theories':['A'],'contract_ids':['unknown']}]
        with self.assertRaises(ValueError): validate_review(self.review,self.plan,self.contracts)


if __name__=='__main__': unittest.main()
