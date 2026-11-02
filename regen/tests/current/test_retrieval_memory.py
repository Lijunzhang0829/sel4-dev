"""Entry recall restores complete observations, never current premise authority."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.import_revisions import revised_state
from regen.execution.proof_commits import digest
from regen.planning.batch_file_planner import apply_feedback
from regen.planning.document_recall import context_index_page
from regen.planning.observation_memory import merge
from regen.planning.retrieval_memory import entry_retrieval_memory, retrieval_index_context
from regen.tests.current.test_proof_commits import fixture


def fact(key, statement=None):
    return {'record_id': key, 'canonical_name': 'Base.' + key,
            'exact_statement': statement or f'lemma {key}: "True"',
            'context': ['context begin'], 'source_class': 'frozen_parent',
            'availability': 'catalog_candidate_requires_check'}


def query(state, number, request, records):
    return apply_feedback(state, {'action': 'query', 'request': request},
        {'kind': 'fact_selection', 'status': 'available', 'query_identity': f'q{number}',
         'records': records}, number)


def request(entry='contract', **extra):
    return {'premise_queries': [{'query': 'candidate', 'mode': 'exact'}],
            **({'context_node_id': entry} if entry is not None else {}), **extra}


class RetrievalMemoryTests(unittest.TestCase):
    def setUp(self):
        _, self.state = fixture()
        self.state['graph']['declaration_index_version'] = 1
        self.state.update(context_revision=3, context_imports=['Provider'])

    def test_only_explicit_target_queries_associate_goal_and_residual_candidates(self):
        state = query(self.state, 1, request(), [fact('direct')])
        state = query(state, 2, request(None), [fact('global')])
        state = query(state, 3, request('helper'), [fact('other')])
        state = query(state, 4, {'goal_query': True, 'context_entry_id': 'contract'}, [fact('goal')])
        state = query(state, 5, {'proof_state_query': {'request_number': 7, 'goal_index': 1},
                               'context_node_id': 'contract'}, [fact('residual')])
        memory = entry_retrieval_memory(state, 'contract')
        self.assertEqual([q['query_id'] for q in memory['queries']], ['q5', 'q4', 'q1'])
        self.assertEqual([f['record_id'] for f in memory['facts']], ['residual', 'goal', 'direct'])
        self.assertEqual(memory['global_queries']['total'], 1)
        self.assertEqual(memory['totals'], {'queries': 3, 'facts': 3,
                                         'referenced_fact_ids': 3, 'unavailable_fact_records': 0})
        self.assertEqual(memory['context_revision'], 3)
        for candidate in memory['facts']:
            self.assertFalse(candidate['is_proof_authority'])
            self.assertEqual(candidate['availability'], 'requires_current_context_check')
            self.assertIsNone(candidate['memory_context']['query_context_revision'])

    def test_unattributed_and_ambiguous_requests_are_never_guessed_from_results(self):
        state = query(self.state, 1, request(None), [fact('contract')])
        state = query(state, 2, request('contract', context_entry_id='helper'), [fact('conflict')])
        state = query(state, 3, {'proof_state_query': {'request_number': 10, 'goal_index': 1}},
                      [fact('unbound_residual')])
        memory = entry_retrieval_memory(state, 'contract')
        self.assertEqual(memory['queries'], [])
        self.assertEqual(memory['facts'], [])
        self.assertEqual(memory['global_queries']['total'], 3)
        self.assertNotIn('entry_id', memory['global_queries']['index_action'])

    def test_limit_counts_complete_records_and_bounded_query_metadata(self):
        statement = 'lemma long_fact: "' + 'P x \\<and> ' * 8000 + 'True"'
        state = self.state
        for number in range(1, 11):
            state = query(state, number, request(), [fact(f'f{number}', statement)])
        memory = entry_retrieval_memory(state, 'contract')
        self.assertEqual(len(memory['queries']), 8)
        self.assertEqual(len(memory['facts']), 8)
        self.assertEqual(memory['totals']['queries'], 10)
        self.assertEqual(memory['totals']['facts'], 10)
        self.assertEqual(memory['facts'][0]['exact_statement'], statement)
        self.assertEqual(memory['has_more'], {'queries': True, 'facts': True})
        self.assertEqual(memory['queries'][0]['read_action'],
                         {'action': 'read_evidence', 'refs': [{'kind': 'query', 'id': 'q10'}]})
        self.assertEqual(memory['continuation']['facts'],
                         {'action': 'read_evidence', 'index': 'facts', 'entry_id': 'contract',
                          'offset': 0, 'limit': 8})

    def test_target_index_paginates_all_records_and_rejects_changed_snapshot(self):
        state = query(self.state, 1, request(), [fact(f'f{i}') for i in range(5)])
        state = query(state, 2, request('helper'), [fact('unrelated')])
        context = retrieval_index_context(state, 'contract')
        self.assertEqual(len(context['recalled_fact_index']), 5)
        self.assertEqual(context['query_index'], [{'query_id': 'q1', 'context_node_id': 'contract'}])
        page = context_index_page(context, {'action': 'read_context_index', 'index': 'facts', 'limit': 2})
        continuation = page['next_action']
        second = context_index_page(context, continuation)
        self.assertEqual([r['record_id'] for r in second['records']], ['f2', 'f3'])
        other = query(state, 3, request('helper'), [fact('another_unrelated')])
        self.assertEqual(context_index_page(retrieval_index_context(other, 'contract'), continuation), second)
        changed = query(state, 4, request(), [fact('new_target_fact')])
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            context_index_page(retrieval_index_context(changed, 'contract'), continuation)

    def test_serial_receipt_order_and_legacy_query_history_both_restore(self):
        state = query(self.state, 1, request(), [fact('legacy')])
        def row(number):
            result = {'status': 'available', 'kind': 'fact_selection',
                      'query_identity': f'serial{number}', 'records': [fact(f's{number}') ]}
            return {'task_id': str(number), 'submission_sequence': number, 'submitted_at': 100 - number,
                    'request': {'proposal': {'action': 'query', 'request': request()}},
                    'result': result, 'result_sha256': digest(result)}
        with tempfile.TemporaryDirectory() as temporary:
            state = merge(state, [row(3)], Path(temporary))
            state = merge(state, [row(2)], Path(temporary))
        memory = entry_retrieval_memory(state, 'contract')
        self.assertEqual([q['query_id'] for q in memory['queries']], ['serial3', 'serial2', 'q1'])
        self.assertEqual([f['record_id'] for f in memory['facts']], ['s3', 's2', 'legacy'])
        self.assertNotIn('"cache"', json.dumps(memory))
        self.assertNotIn('result_path', json.dumps(memory))

    def test_import_revision_never_reintroduces_retired_facts(self):
        state = query(self.state, 1, request(), [fact('old_import_fact')])
        state['observation_queries'] = [{'query_id': 'serial', 'request': request(),
            'requests': [], 'task_ids': ['old-task'], 'returned_record_ids': ['old_import_fact']}]
        retired = revised_state(state, ['DifferentProvider'], 4, [])
        memory = entry_retrieval_memory(retired, 'contract')
        self.assertEqual(memory['context_revision'], 4)
        self.assertEqual(memory['facts'], [])
        self.assertEqual([q['query_id'] for q in memory['queries']], ['q1'])
        self.assertEqual(memory['totals']['unavailable_fact_records'], 1)
        self.assertEqual(retrieval_index_context(retired, 'contract')['recalled_fact_index'], [])
        self.assertIn('retired records are not restored', memory['notice'])
        self.assertIn('batch_facts', state)

    def test_restart_returns_full_facts_again_without_io_or_state_mutation(self):
        state = query(self.state, 1, request(), [fact('remembered')])
        before = deepcopy(state)
        memory = entry_retrieval_memory(state, 'contract')
        restored = json.loads(json.dumps(state))
        with patch.object(Path, 'read_text', side_effect=AssertionError('no receipt file read')):
            self.assertEqual(entry_retrieval_memory(restored, 'contract'), memory)
        self.assertEqual(state, before)
        memory['facts'][0]['context'].append('changed output')
        memory['queries'][0]['request']['premise_queries'][0]['query'] = 'changed output'
        self.assertEqual(state, before)

    def test_shared_candidate_is_deduplicated_in_most_recent_query_order(self):
        state = query(self.state, 1, request(), [fact('shared'), fact('older')])
        state = query(state, 2, request(), [fact('newer'), fact('shared')])
        memory = entry_retrieval_memory(state, 'contract', limit=2)
        self.assertEqual([f['record_id'] for f in memory['facts']], ['newer', 'shared'])
        self.assertEqual(memory['facts'][1]['memory_context']['query_ids'], ['q2', 'q1'])
        self.assertEqual(memory['totals']['facts'], 3)

    def test_invalid_limits_and_empty_entry_rejected(self):
        for value in (True, 0, 25, 1.5, '8'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                entry_retrieval_memory(self.state, 'contract', limit=value)
        for entry in ('', ' ', None, 12):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                retrieval_index_context(self.state, entry)


if __name__ == '__main__':
    unittest.main()
