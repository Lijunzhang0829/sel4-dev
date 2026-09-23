import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.auxiliary_reuse import auxiliary_page
from regen.execution.gra_input import gra_view
from regen.tests.current.test_proof_commits import fixture
from regen.planning.file_planner import plan_file


class PlannerRecallAndReuseTest(unittest.TestCase):
    def test_queries_accumulate_evidence_and_bad_context_is_correctable(self):
        package, _ = fixture()
        fact = {'record_id': 'known', 'canonical_name': 'Base.known',
                'exact_statement': 'True', 'source_class': 'frozen_parent'}
        responses = [
            {'observation_request': {'premise_query': 'known', 'context_contract_id': 'foreign'}},
            {'observation_request': {'premise_query': 'known', 'mode': 'exact', 'context_contract_id': 'contract'}},
            {'observation_request': {'premise_query': 'next', 'mode': 'definition'}},
            {'generated_nodes': [], 'contract_dependencies': {'contract': []},
             'contract_strategies': {'contract': 'Apply Base.known'},
             'contract_routes': {'contract': {'proof_shape': 'direct', 'semantic_steps': ['Apply the recalled theorem'],
                'direct_evidence': {'kind': 'legal_premise', 'premise_ids': ['known'], 'detail': 'Base.known'}}}}]
        prompts = []
        def call(provider, prompt, prefix, **kwargs):
            prompts.append(prompt)
            return json.dumps(responses[len(prompts)-1])
        def recall(database, request, **kwargs):
            if kwargs['symbol_resolver']:
                kwargs['symbol_resolver'](['next'])
            return {'kind': 'fact_selection', 'status': 'frozen', 'records': [fact], 'queries': []}
        contexts = []
        with tempfile.TemporaryDirectory() as tmp, \
             patch('regen.planning.file_planner.call_llm', side_effect=call), \
             patch('regen.planning.file_planner.validate_call_artifact', return_value={'prompt_sha256':'p','final_sha256':'f'}), \
             patch('regen.execution.round_recall.select_round_facts', side_effect=recall) as query:
            graph = plan_file(package, run_id='r', output_dir=Path(tmp), environment={},
                premise_database=Path(tmp)/'db', persist_graph=False,
                symbol_resolver=lambda names, ctx: contexts.append(ctx))
            self.assertEqual(query.call_count, 2)
            self.assertEqual(len(list((Path(tmp)/'planner-recall').glob('*.response.json'))), 3)
        self.assertEqual(graph['planner_context']['retrieval_queries'], 3)
        self.assertIn('unknown context_contract_id', prompts[1])
        self.assertIn('Base.known', prompts[-1])
        self.assertNotIn('This is a closed-payload planning call', prompts[0])
        self.assertEqual(contexts, [[], []])

    def test_reuse_excludes_unproved_and_later_prefix_even_when_proof_exists(self):
        _, state = fixture()
        graph = state['graph']
        graph['accepted_order'] = ['helper', 'contract']
        proofs = {'helper': 'by simp', 'contract': 'by simp'}
        page = auxiliary_page(graph, proofs, 'contract', {'node_ids': ['helper']})
        self.assertEqual(page['nodes'][0]['proof_body'], 'by simp')
        self.assertTrue(page['nodes'][0]['proof_sha256'])
        for target, ids, available in [('helper', ['contract'], proofs),
                                       ('contract', ['contract'], proofs),
                                       ('contract', ['helper'], {})]:
            with self.subTest(target=target, ids=ids), self.assertRaises(ValueError):
                auxiliary_page(graph, available, target, {'node_ids': ids})

    def test_gra_sees_structural_evidence_and_unrelated_green_index(self):
        _, state = fixture()
        graph = state['graph']
        graph['nodes'].append({'id':'sibling','name':'sibling','role':'helper',
            'statement':'lemma sibling: True','context':[],'dependencies':[]})
        view = gra_view({'current_graph':graph, 'failed_node':{'node_id':'helper'},
            'green_node_ids':['sibling'], 'decision_context':{
                'consecutive_retry_proof_decisions':3,
                'historical_isabelle_semantic_residuals':[{'semantic_goal':'remaining obligation'}]}})
        self.assertEqual(view['constraints']['consecutive_retry_proof_decisions'], 3)
        self.assertEqual(view['accepted_auxiliary_index'][0]['id'], 'sibling')
        self.assertEqual(view['structural_evidence']['historical_isabelle_semantic_residuals'][0]['semantic_goal'], 'remaining obligation')


if __name__ == '__main__':
    unittest.main()
