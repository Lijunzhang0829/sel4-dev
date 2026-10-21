"""Prompt projection preserves complete canonical evidence and honest navigation."""
import ast
from copy import deepcopy
import json
import unittest

from regen.planning.planner_context import project_context


def follow(root, path):
    def walk(node):
        if isinstance(node, ast.Name) and node.id == 'root':
            return root
        if isinstance(node, ast.Attribute):
            return walk(node.value)[node.attr]
        if isinstance(node, ast.Subscript):
            return walk(node.value)[ast.literal_eval(node.slice)]
        raise AssertionError('unsupported reference: '+path)
    return walk(ast.parse('root.'+path, mode='eval').body)


def expand(root, value, visited=()):
    if isinstance(value, dict):
        if 'reference' in value:
            path = value['reference']
            if path in visited:
                raise AssertionError('cyclic reference: '+path)
            return expand(root, follow(root, path), (*visited, path))
        return {key: expand(root, item, visited) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(root, item, visited) for item in value]
    return value


class PlannerContextTests(unittest.TestCase):
    def fixture(self):
        fact = {'record_id': 'fact:one', 'exact_statement': 'P ==> Q', 'context': ['assumes P']}
        proof = {'id': 'aux:a.b', 'statement': 'lemma helper: "True"',
                 'context': ['context Demo'], 'proof_body': 'by simp'}
        candidate = {'action': 'submit_batch', 'proofs': {'contract': 'by simp'}, 'notes': 'route summary'}
        feedback = {'status': 'rejected_batch', 'check': {'diagnostic': 'remaining goal ' * 400}}
        attempt = {'request_number': 112, 'candidate': candidate, 'feedback': feedback}
        return {'graph': {'nodes': [{'id': 'contract', 'statement': 'lemma target: "True"',
                                    'context': ['context Demo']}]},
                'recalled_facts': [fact], 'requested_accepted_proofs': {'aux:a.b': proof},
                'last_proof_feedback': attempt, 'requested_attempts': [deepcopy(attempt)],
                'last_candidate': deepcopy(candidate), 'last_feedback': deepcopy(feedback),
                'target_memory': {'contract': {'request_number': 112, 'status': 'rejected_batch',
                    'diagnostic_excerpt': feedback['check']['diagnostic'][:1800],
                    'diagnostic_excerpt_incomplete': True, 'notes': 'route summary'}},
                'recent_actions': [{'request_number': 112, 'action': 'submit_batch', 'status': 'rejected_batch',
                    'proof_node_ids': ['contract'], 'notes': 'route summary' * 100,
                    'query_results': [{'unneeded_navigation_body': 'x' * 500}]}]}

    def test_references_expand_to_exact_candidates_feedback_and_proofs_without_input_mutation(self):
        payload = self.fixture(); before = deepcopy(payload)
        out = project_context(payload)
        self.assertEqual(payload, before)
        for key in ('graph', 'recalled_facts', 'requested_accepted_proofs', 'last_proof_feedback',
                    'requested_attempts', 'last_candidate', 'last_feedback'):
            self.assertEqual(expand(out, out[key]), payload[key], key)
        self.assertEqual(out['last_candidate']['reference'], 'last_proof_feedback.candidate')
        self.assertEqual(out['requested_attempts'][0]['reference'], 'last_proof_feedback')
        self.assertLess(len(json.dumps(out)), len(json.dumps(payload)))
        self.assertEqual(project_context(out), out)

    def test_target_navigation_only_replaces_preview_of_available_matching_attempt(self):
        payload = self.fixture()
        payload['target_memory']['unknown'] = deepcopy(payload['target_memory']['contract'])
        payload['target_memory']['older'] = dict(payload['target_memory']['contract'], request_number=99)
        out = project_context(payload)
        self.assertEqual(out['target_memory']['contract'],
                         {'request_number': 112, 'status': 'rejected_batch', 'reference': 'last_proof_feedback'})
        self.assertEqual(out['target_memory']['unknown'], payload['target_memory']['unknown'])
        self.assertEqual(out['target_memory']['older'], payload['target_memory']['older'])
        payload['target_memory']['contract']['diagnostic_excerpt'] = 'different unseen evidence'
        self.assertEqual(project_context(payload)['target_memory']['contract'], payload['target_memory']['contract'])

    def test_compound_result_data_has_exact_canonical_references_and_errors_are_preserved(self):
        payload = self.fixture()
        page = {'request_number': 112, 'offset': 0, 'end': 3, 'text': 'abc'}
        state = {'request_number': 112, 'diagnostic': {'goals': ['P ==> Q']}}
        symbols = [{'name': 'Demo.fact', 'statement': 'P ==> Q', 'context': ['P']}]
        payload.update(diagnostic_pages=[page], diagnostic_page=deepcopy(page),
                       requested_proof_states=[state], requested_proof_state=deepcopy(state),
                       symbol_observations=[symbols], resolved_symbols=deepcopy(symbols))
        error = {'status': 'unavailable', 'diagnostic': 'unique resolver failure'}
        payload['last_feedback'] = {'status': 'available', 'observations': [
            {'item_index': 1, 'request': {'action': 'query'}, 'result': {'status': 'available',
             'kind': 'fact_selection', 'records': deepcopy(payload['recalled_facts']),
             'queries': [{'query': 'P', 'matches': ['fact:one']}]}},
            {'item_index': 2, 'request': {'action': 'read_proofs'}, 'result': {'status': 'available',
             'proofs': list(deepcopy(payload['requested_accepted_proofs']).values())}},
            {'item_index': 3, 'request': {'action': 'read_diagnostic'}, 'result': {'status': 'available',
             'diagnostic_page': deepcopy(page)}},
            {'item_index': 4, 'request': {'action': 'read_proof_state'}, 'result': {'status': 'available',
             'proof_state': deepcopy(state)}},
            {'item_index': 5, 'request': {'action': 'resolve_symbols'}, 'result': {'status': 'available',
             'symbols': deepcopy(symbols)}},
            {'item_index': 6, 'request': {'action': 'query'}, 'result': error},
        ]}
        out = project_context(payload)
        self.assertEqual(expand(out, out['last_feedback']), payload['last_feedback'])
        items = out['last_feedback']['observations']
        self.assertEqual(items[0]['result']['records'][0]['reference'], 'recalled_facts[0]')
        self.assertEqual(items[1]['result']['proofs'][0]['reference'], 'requested_accepted_proofs["aux:a.b"]')
        self.assertEqual(items[2]['result']['diagnostic_page']['reference'], 'diagnostic_pages[0]')
        self.assertEqual(items[3]['result']['proof_state']['reference'], 'requested_proof_states[0]')
        self.assertEqual(items[4]['result']['symbols']['reference'], 'symbol_observations[0]')
        self.assertEqual(items[5]['result'], error)

    def test_controller_prompt_projection_preserves_compound_feedback_and_direct_inventory_paths(self):
        from regen.planning.batch_file_planner import prepare_prompt
        payload = self.fixture()
        feedback = {'status':'available','observations':[
            {'item_index':1, 'request':{'action':'read_attempts','request_numbers':[112]},
             'result':{'status':'available','attempts':deepcopy(payload['requested_attempts'])}},
            {'item_index':2, 'request':{'action':'query'}, 'result':{'status':'available',
             'records':deepcopy(payload['recalled_facts']), 'queries':[{'query':'P','matches':1}]}},
            {'item_index':3, 'request':{'action':'query'},
             'result':{'status':'unavailable','diagnostic':'exact original failure'}}]}
        payload['last_feedback'] = feedback
        page = {'request_number':112,'offset':0,'end':3,'total_chars':3,
                'text':'abc','incomplete':False}
        payload.update(diagnostic_pages=[page], diagnostic_page=deepcopy(page))
        view, prompt = prepare_prompt(payload)
        self.assertEqual(expand(view, view['last_feedback']), feedback)
        self.assertIn('reference',view['target_memory']['contract'])
        diagnostics = view['observation_inventory']['diagnostics']
        self.assertEqual(len(diagnostics),2)
        for item in diagnostics:
            text = follow(view,item['path'])
            self.assertIsInstance(text,str)
            self.assertEqual(len(text),item['end']-item['offset'])
        self.assertEqual(view['graph'],payload['graph'])

    def test_same_ids_with_changed_mathematical_context_or_provenance_are_not_merged(self):
        payload = self.fixture()
        different = deepcopy(payload['recalled_facts'][0]); different['context'] = ['assumes R']
        other_proof = deepcopy(payload['requested_accepted_proofs']['aux:a.b'])
        other_proof['context'] = ['context Other']
        payload['last_feedback'] = {'status': 'available', 'records': [different], 'proofs': [other_proof]}
        out = project_context(payload)
        self.assertEqual(out['last_feedback']['records'], [different])
        self.assertEqual(out['last_feedback']['proofs'], [other_proof])

    def test_navigation_keeps_result_and_retrieval_path_while_full_input_stays_unchanged(self):
        payload = self.fixture(); original = deepcopy(payload['recent_actions'])
        item = project_context(payload)['recent_actions'][0]
        self.assertEqual(item['status'], 'rejected_batch')
        self.assertEqual(item['proof_node_ids'], ['contract'])
        self.assertEqual(item['read_action'], {'action': 'read_attempts', 'request_numbers': [112]})
        self.assertNotIn('notes', item)
        self.assertNotIn('query_results', item)
        self.assertEqual(payload['recent_actions'], original)


if __name__ == '__main__':
    unittest.main()
