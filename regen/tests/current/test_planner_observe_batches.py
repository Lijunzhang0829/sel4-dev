"""Independent observations share a decision without losing evidence boundaries."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from regen.execution.proof_commits import digest, durable
from regen.planning.planner_workspace import observe
from regen.tests.current.test_proof_commits import fixture


class ObserveBatchTests(unittest.TestCase):
    def test_two_diagnostic_pages_remain_exact_and_tampered_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _, state = fixture()
            original = 'first-page second-page'
            feedback = {'status': 'rejected_batch', 'check': {'diagnostic': original}}
            path = directory/'batch-requests/000007/feedback.json'
            durable(path, feedback)
            state['batch_history'] = [{'request_number': 7, 'feedback_sha256': digest(feedback)}]
            proposal = {'action': 'observe', 'reads': [
                {'action': 'read_diagnostic', 'request_number': 7, 'offset': 0, 'limit': 11},
                {'action': 'read_diagnostic', 'request_number': 7, 'offset': 11, 'limit': 11},
            ]}
            before = deepcopy(state)
            result = observe(directory, state, proposal, Mock())
            items = result['observations']
            self.assertEqual([x['item_index'] for x in items], [1, 2])
            self.assertEqual(''.join(x['result']['diagnostic_page']['text'] for x in items), original)
            self.assertTrue(all(x['result']['diagnostic_page']['sha256'] == digest(original) for x in items))
            self.assertEqual(state, before)
            durable(path, {'check': {'diagnostic': 'tampered'}})
            rejected = observe(directory, state, proposal, Mock())
            self.assertEqual([x['result']['status'] for x in rejected['observations']], ['unavailable']*2)

    def test_queries_get_distinct_indices_and_do_not_mutate_proposal_or_state(self):
        _, state = fixture()
        before = deepcopy(state)
        proposal = {'action': 'observe', 'reads': [
            {'action': 'query', 'request': {'premise_query': 'first'}},
            {'action': 'query', 'request': {'premise_query': 'second'}},
        ]}
        original = deepcopy(proposal)
        calls = []
        returned = []
        def query_handler(query, index):
            calls.append((deepcopy(query), index))
            query['premise_query'] = 'executor-local change'
            result = {'status': 'available', 'kind': 'fact_selection', 'records': [
                {'record_id': 'fact:'+str(index), 'exact_statement': 'True'}]}
            returned.append(result)
            return result
        result = observe(Path('/unused'), state, proposal, Mock(), query_handler=query_handler)
        self.assertEqual(calls, [({'premise_query': 'first'}, 1), ({'premise_query': 'second'}, 2)])
        self.assertEqual(proposal, original)
        self.assertEqual(state, before)
        returned[0]['records'].clear()
        self.assertEqual(result['observations'][0]['result']['records'][0]['record_id'], 'fact:1')

    def test_query_runtime_failure_does_not_discard_other_queries_or_reads(self):
        _, state = fixture()
        state['batch_facts'] = [{'record_id': 'existing', 'exact_statement': 'True'}]
        proposal = {'action': 'observe', 'reads': [
            {'action': 'query', 'request': {'premise_query': 'broken'}},
            {'action': 'read_facts', 'record_ids': ['existing']},
            {'action': 'query', 'request': {'premise_query': 'working'}},
        ]}
        def query_handler(query, index):
            if index == 1:
                raise RuntimeError('symbol resolver unavailable')
            return {'status': 'available', 'kind': 'fact_selection', 'records': []}
        items = observe(Path('/unused'), state, proposal, Mock(), query_handler=query_handler)['observations']
        self.assertEqual([x['result']['status'] for x in items], ['unavailable', 'available', 'available'])
        self.assertIn('symbol resolver unavailable', items[0]['result']['diagnostic'])
        self.assertEqual(items[1]['result']['records'][0]['record_id'], 'existing')

    def test_mutations_nested_observe_and_unbound_query_are_unavailable(self):
        _, state = fixture()
        before = deepcopy(state)
        proposal = {'action': 'observe', 'reads': [
            {'action': 'submit_batch', 'proofs': {'contract': 'by simp'}},
            {'action': 'observe', 'reads': []},
            {'action': 'query', 'request': {}},
            {'action': 'finish'},
        ]}
        items = observe(Path('/unused'), state, proposal, Mock())['observations']
        self.assertEqual([x['result']['status'] for x in items], ['unavailable']*4)
        self.assertEqual(state, before)

    def test_malformed_query_is_isolated_before_executor(self):
        _, state = fixture()
        state['batch_facts'] = [{'record_id': 'existing', 'exact_statement': 'True'}]
        executor = Mock()
        proposal = {'action': 'observe', 'reads': [
            {'action': 'query', 'request': 'invalid'},
            {'action': 'read_facts', 'record_ids': ['existing']},
        ]}
        items = observe(Path('/unused'), state, proposal, Mock(), query_handler=executor)['observations']
        executor.assert_not_called()
        self.assertEqual([x['result']['status'] for x in items], ['unavailable', 'available'])

    def test_same_kind_fact_reads_and_structured_error_details_survive(self):
        _, state = fixture()
        state['batch_facts'] = [{'record_id': 'fact:one', 'exact_statement': 'True'},
                                {'record_id': 'fact:two', 'exact_statement': 'False'}]
        proposal = {'action': 'observe', 'reads': [
            {'action': 'read_facts', 'record_ids': ['fact:one']},
            {'action': 'read_facts', 'record_ids': ['missing']},
            {'action': 'read_facts', 'record_ids': ['fact:two']},
        ]}
        items = observe(Path('/unused'), state, proposal, Mock())['observations']
        self.assertEqual([x['result']['status'] for x in items], ['available', 'unavailable', 'available'])
        self.assertEqual(items[0]['result']['records'][0]['record_id'], 'fact:one')
        self.assertEqual(items[2]['result']['records'][0]['record_id'], 'fact:two')
        self.assertIn('invalid_ids', items[1]['result'])


if __name__ == '__main__':
    unittest.main()
