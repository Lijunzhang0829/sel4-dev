import unittest
import hashlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from copy import deepcopy
from regen.planning import plan_review
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

    def run_cli(self, root, responses, *, audited=True):
        root = Path(root)
        (root/'plan.json').write_text(json.dumps(self.plan))
        (root/'input.json').write_text(json.dumps({'contracts':self.contracts}))
        output = root/'review'
        prompts = []
        def call(prompt, prefix, **options):
            self.assertFalse((output/'review.json').exists())
            self.assertFalse((output/'review-commit.json').exists())
            self.assertNotIn('owner_file_slot', prompt)
            prompts.append(prompt)
            result = responses[len(prompts)-1]
            if isinstance(result, BaseException): raise result
            Path(str(prefix)+'.final.txt').write_text(result)
            return result
        argv = ['plan_review', '--plan', str(root/'plan.json'), '--input', str(root/'input.json'),
                '--output', str(output), '--model', 'gpt-5.6-sol', '--reasoning-effort', 'medium',
                '--allow-external-call']
        with patch('sys.argv', argv), \
                patch('regen.infrastructure.llm_transcript.call_codex', side_effect=call) as calls, \
                patch('regen.infrastructure.llm_transcript.validate_call_artifact',
                      return_value={'sealable_generation':audited}):
            try:
                plan_review.main()
            finally:
                self.last_calls = calls.call_count
                self.last_prompts = prompts
        return output

    def test_invalid_external_reference_is_repaired_before_commit(self):
        bad = deepcopy(self.review)
        bad['theory_reviews'][0]['consumed_contract_ids'] = ['a']
        good = deepcopy(self.review)
        good['theory_reviews'][1]['consumed_contract_ids'] = ['a']
        first, second = json.dumps(bad), json.dumps(good)
        with tempfile.TemporaryDirectory() as tmp:
            output = self.run_cli(tmp, [first, second])
            self.assertEqual(json.loads((output/'review.json').read_text()), good)
            self.assertEqual((output/'review.final.txt').read_text(), first)
            self.assertEqual((output/'review-repair-0001.final.txt').read_text(), second)
            self.assertIn('theory_reviews[A].consumed_contract_ids', self.last_prompts[1])
            self.assertIn('belongs to this theory', self.last_prompts[1])
            feedback = json.loads(self.last_prompts[1].split('OUTPUT REPAIR REQUEST:\n')[1])
            self.assertEqual(feedback['previous_response'], first)
            self.assertFalse(json.loads((output/'validation-0001.json').read_text())['valid'])
            commit = json.loads((output/'review-commit.json').read_text())
            self.assertEqual(commit['model'], 'gpt-5.6-sol')
            self.assertEqual(commit['accepted_call']['attempt'], 2)
            self.assertEqual(commit['accepted_call']['response_sha256'], hashlib.sha256(second.encode()).hexdigest())
            self.assertEqual(json.loads((output/'status.json').read_text())['state'], 'complete')

    def test_repeated_invalid_output_can_repair_beyond_former_round_limit(self):
        bad = deepcopy(self.review)
        bad['theory_reviews'][0]['consumed_contract_ids'] = ['a']
        with tempfile.TemporaryDirectory() as tmp:
            output = self.run_cli(tmp, [json.dumps(bad)]*5 + [json.dumps(self.review)])
            self.assertEqual(self.last_calls, 6)
            self.assertEqual(json.loads((output/'review.json').read_text()), self.review)
            self.assertEqual(len(list(output.glob('validation-*.json'))), 6)
            for index in range(1, 6):
                self.assertFalse(json.loads((output/f'validation-{index:04d}.json').read_text())['valid'])
            commit = json.loads((output/'review-commit.json').read_text())
            self.assertIsNone(commit['max_repairs'])
            self.assertEqual(commit['accepted_call']['attempt'], 6)
            self.assertEqual(commit['output_repair_policy'], plan_review.OUTPUT_REPAIR_POLICY)

    def test_operator_interrupt_stops_repair_without_publishing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(KeyboardInterrupt):
                self.run_cli(tmp, ['not JSON']*4 + [KeyboardInterrupt()])
            output = Path(tmp)/'review'
            self.assertEqual(self.last_calls, 5)
            self.assertFalse((output/'review.json').exists())
            self.assertFalse((output/'review-commit.json').exists())
            self.assertEqual(len(list(output.glob('validation-*.json'))), 4)
            self.assertEqual(json.loads((output/'status.json').read_text())['state'], 'interrupted')

    def test_malformed_json_and_missing_or_wrong_type_fields_receive_feedback(self):
        missing = deepcopy(self.review); del missing['theory_reviews'][0]['assessment']
        wrong_type = deepcopy(self.review); wrong_type['issues'] = [{'id':'I', 'kind':[]}]
        for bad in ['not JSON', json.dumps(missing), json.dumps(wrong_type)]:
            with self.subTest(response=bad), tempfile.TemporaryDirectory() as tmp:
                output = self.run_cli(tmp, [bad, json.dumps(self.review)])
                self.assertEqual(self.last_calls, 2)
                self.assertTrue((output/'review-commit.json').exists())

    def test_provider_or_audit_failure_is_not_retried_as_schema_repair(self):
        for responses, audited, exception in [
                ([RuntimeError('provider failed')], True, RuntimeError),
                ([json.dumps(self.review)], False, ValueError)]:
            with self.subTest(audited=audited), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(exception): self.run_cli(tmp, responses, audited=audited)
                self.assertEqual(self.last_calls, 1)
                self.assertFalse((Path(tmp)/'review/review-commit.json').exists())


if __name__=='__main__': unittest.main()
