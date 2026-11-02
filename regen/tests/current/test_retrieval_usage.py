"""Versioned usage views from receipts, never inferred proof dependencies."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.import_revisions import revised_state
from regen.execution.proof_commits import digest, durable
from regen.planning.batch_file_planner import apply_feedback, execute_query, query_binding, query_identity
from regen.planning.observation_memory import merge
from regen.planning.retrieval_memory import entry_retrieval_memory, retrieval_index_context
from regen.planning.document_recall import context_index_page
from regen.planning.retrieval_usage import (
    binding_status, candidate_identity, entry_usage_evidence, proof_mentions,
    public_query_binding, target_identity,
)
from regen.tests.current.test_proof_commits import fixture


def fact(key='f', name='Base.useful'):
    return {'record_id': key, 'canonical_name': name, 'exact_statement': 'P x ==> Q x',
            'source_class': 'frozen_parent', 'owner_theory': 'Base', 'owner_session': 'HOL'}


class RetrievalUsageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        _, self.state = fixture()
        self.state['graph']['declaration_index_version'] = 1
        self.state.update(context_revision=2, context_imports=['Provider'],
                          context_provider_bindings=[{'theory': 'Provider', 'seal_id': 's1'}])
        self.directory = self.root / 'theory/batch-requests/000001'
        self.directory.mkdir(parents=True)
        self.database = self.root / 'premises.sqlite'
        self.database.write_bytes(b'synthetic-unused-catalog')
        self.run = {'run_id': 'test', 'premise_database': str(self.database)}
        self.request = {'premise_query': 'useful', 'context_node_id': 'contract'}

    def selection(self, state=None):
        state = self.state if state is None else state
        binding = query_binding(self.run, state, self.directory, self.request)
        return {'kind': 'fact_selection', 'status': 'available', 'records': [fact()],
                'query_identity': digest(binding), 'query_binding': public_query_binding(binding, state)}

    def receipt(self, result=None, task='q', request=None, revision=2):
        result = self.selection() if result is None else result
        return {'task_id': task, 'submission_sequence': 1, 'submitted_at': 1,
                'binding': {'context_revision': revision},
                'request': {'proposal': request or {'action': 'query', 'request': self.request}},
                'result': result, 'result_sha256': digest(result)}

    def test_query_identity_payload_stays_compatible_and_receipt_exposes_binding(self):
        binding = query_binding(self.run, self.state, self.directory, self.request)
        self.assertEqual(query_identity(self.run, self.state, self.directory, self.request), digest(binding))
        with patch('regen.planning.batch_file_planner.batch_query', return_value={
                'kind': 'fact_selection', 'records': [fact()]}):
            result = execute_query(self.run, {}, self.state, None, self.directory, self.request)
        self.assertEqual(result['query_binding']['query_identity'], result['query_identity'])
        self.assertEqual(result['query_binding']['context_revision'], 2)
        self.assertEqual(result['query_binding']['entry_id'], 'contract')
        self.assertNotIn(str(self.database), json.dumps(result['query_binding']))

    def test_indexed_receipt_identity_tracks_implementation_and_effective_budget_only_when_opted_in(self):
        legacy = query_identity(self.run, self.state, self.directory, self.request)
        request = {**self.request, 'retrieval_policy': 'indexed-v1'}
        original = query_binding(self.run, self.state, self.directory, request)
        implementation = original['retrieval_implementation']
        self.assertEqual(implementation['candidate_budget'], 100)
        self.assertEqual(set(implementation['source_sha256']), {
            'indexed_retrieval.py', 'proof_state_recall.py', 'file_planner_recall.py'})
        changed = deepcopy(implementation)
        changed['source_sha256']['indexed_retrieval.py'] = 'modified-source'
        with patch('regen.planning.retrieval_usage.indexed_implementation_binding', return_value=changed):
            self.assertNotEqual(query_identity(self.run, self.state, self.directory, request), digest(original))
            self.assertEqual(query_identity(self.run, self.state, self.directory, self.request), legacy)
        with patch('regen.execution.indexed_retrieval.DEFAULT_CANDIDATE_BUDGET', 101):
            self.assertNotEqual(query_identity(self.run, self.state, self.directory, request), digest(original))
            self.assertEqual(query_identity(self.run, self.state, self.directory, self.request), legacy)
        explicit = query_binding(self.run, self.state, self.directory, {**request, 'candidate_budget': 25})
        self.assertEqual(explicit['retrieval_implementation']['candidate_budget'], 25)
        structural = query_binding(self.run, self.state, self.directory, {**request, 'structural_rerank': True})
        self.assertIn('premise_structure.py', structural['retrieval_implementation']['source_sha256'])

    def test_binding_reports_target_environment_provider_and_prefix_changes(self):
        binding = self.selection()['query_binding']
        self.assertEqual(binding_status(binding, self.state, 'contract')['differences'], [])
        mutations = [
            ('target_changed', lambda s: next(n for n in s['graph']['nodes'] if n['id'] == 'contract').update(statement='lemma target: False')),
            ('context_revision_changed', lambda s: s.update(context_revision=3)),
            ('imports_changed', lambda s: s.update(context_imports=['Other'])),
            ('provider_versions_changed', lambda s: s['context_provider_bindings'][0].update(seal_id='s2')),
            ('accepted_prefix_changed', lambda s: s.update(proofs={'helper': 'by simp'}, accepted_order=['helper'])),
        ]
        for expected, change in mutations:
            with self.subTest(expected=expected):
                state = deepcopy(self.state)
                change(state)
                self.assertIn(expected, binding_status(binding, state, 'contract')['differences'])
        draft = {'entry_id': 'contract', 'statement': 'lemma target: False', 'context': []}
        self.assertIn('target_changed', binding_status(binding, self.state, 'contract', draft)['differences'])

    def test_legacy_binding_remains_unknown_instead_of_guessing_current_revision(self):
        result = self.selection()
        result.pop('query_binding')
        state = apply_feedback(self.state, {'action': 'query', 'request': self.request}, result, 1)
        memory = entry_retrieval_memory(state, 'contract')
        self.assertIsNone(memory['queries'][0]['query_context_revision'])
        self.assertEqual(memory['queries'][0]['context_status'], 'unknown_legacy_binding')

    def test_old_receipts_survive_import_revision_only_as_history_not_current_facts(self):
        row = self.receipt()
        initial = merge(self.state, [row], self.root, history_rows=[row])
        retired = revised_state(initial, ['OtherProvider'], 3, [])
        recovered = merge(retired, [], self.root, history_rows=[row])
        memory = entry_retrieval_memory(recovered, 'contract')
        self.assertEqual(memory['facts'], [])
        self.assertEqual(memory['queries'][0]['query_context_revision'], 2)
        self.assertIn('context_revision_changed', memory['queries'][0]['context_comparison']['differences'])
        self.assertEqual(memory['totals']['unavailable_fact_records'], 1)
        self.assertEqual(memory['usage_evidence']['returned'][0]['record_ids'], ['f'])
        self.assertEqual(recovered['batch_facts'], [])

    def test_returned_and_explicitly_read_are_separate_and_read_target_not_guessed(self):
        query = self.receipt()
        selected = merge(self.state, [query], self.root)
        self.assertEqual(entry_retrieval_memory(selected, 'contract')['usage_evidence']['explicit_fact_reads'], [])
        read = self.receipt({'status': 'available', 'records': [fact()]}, task='read',
                            request={'action': 'read_facts', 'record_ids': ['f']})
        state = merge(selected, [read], self.root)
        events = entry_retrieval_memory(state, 'contract')['usage_evidence']['explicit_fact_reads']
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0]['entry_id'])
        self.assertEqual(events[0]['evidence'], 'explicit_fact_read')
        replay = json.loads(json.dumps(state))
        self.assertEqual(entry_retrieval_memory(replay, 'contract'), entry_retrieval_memory(state, 'contract'))

    def test_proof_token_mentions_ignore_comments_substrings_and_ambiguous_shortnames(self):
        candidates = [candidate_identity(fact()), candidate_identity(fact('other', 'Other.useful')),
                      candidate_identity(fact('prime', "Base.useful'"))]
        self.assertEqual(proof_mentions('by (simp add: useful useful_long) (* Base.useful *)', candidates), [])
        mentions = proof_mentions('by (simp add: Base.useful) (* nested (* Other.useful *) *)', candidates)
        self.assertEqual([x['record_id'] for x in mentions], ['f'])
        self.assertFalse(mentions[0]['native_dependency_confirmed'])
        self.assertFalse(mentions[0]['candidate_version_resolved_by_prover'])

    def test_failed_attempt_is_bound_and_never_becomes_a_negative_or_accepted_use(self):
        state = merge(self.state, [self.receipt()], self.root)
        proposal = {'action': 'submit_batch', 'proofs': {'contract': 'by (rule Base.useful)'},
                    'graph': deepcopy(state['graph']), 'context_revision': 2}
        feedback = {'status': 'rejected_batch', 'check': {'verdict': 'red'}}
        state = apply_feedback(state, proposal, feedback, 5)
        directory = self.root / 'batch-requests/000005'
        durable(directory / 'proposal.json', proposal)
        durable(directory / 'feedback.json', feedback)
        usage = entry_retrieval_memory(state, 'contract', directory=self.root)['usage_evidence']
        self.assertEqual(len(usage['attempts']), 1)
        self.assertFalse(usage['attempts'][0]['is_negative_training_label'])
        self.assertEqual(usage['attempts'][0]['mentions'][0]['record_id'], 'f')
        self.assertIsNone(usage['accepted_proof'])
        self.assertEqual(usage['attempts'][0]['context_revision'], 2)
        durable(directory / 'proposal.json', {**proposal, 'proofs': {'contract': 'by simp'}})
        invalid = entry_retrieval_memory(state, 'contract', directory=self.root)['usage_evidence']
        self.assertEqual(invalid['attempts'], [])
        self.assertEqual(len(invalid['unavailable_attempts']), 1)

    def test_current_accepted_mentions_do_not_claim_native_dependency_or_resolve_old_versions(self):
        state = merge(self.state, [self.receipt()], self.root)
        state.update(proofs={'contract': 'by (rule Base.useful)'}, accepted_order=['contract'])
        memory = entry_retrieval_memory(state, 'contract')
        accepted = memory['usage_evidence']['accepted_proof']
        self.assertEqual(accepted['context_revision'], 2)
        self.assertEqual(accepted['mentions'][0]['evidence'], 'proof_text_token_mention')
        self.assertFalse(accepted['mentions'][0]['native_dependency_confirmed'])
        retired = revised_state(state, ['OtherProvider'], 3, [])
        self.assertIsNone(entry_retrieval_memory(retired, 'contract')['usage_evidence']['accepted_proof'])

    def test_long_history_bounds_per_fact_contexts_and_proof_mentions_with_exact_navigation(self):
        state = deepcopy(self.state)
        shared = fact()
        shared['exact_statement'] = 'P x ==> ' + ' & '.join(['Q x'] * 500)
        distinct = [fact(f'fact-{i}', f'Base.fact_{i}') for i in range(120)]
        state['batch_facts'] = [shared, *distinct]
        state['observation_queries'] = [
            {'query_id': f'q-{i:03d}', 'request': deepcopy(self.request), 'requests': [],
             'observation_order': [i, i, str(i)], 'identity_bound': True,
             'returned_record_ids': ['f', item['record_id']],
             'returned_candidates': [candidate_identity(shared), candidate_identity(item)]}
            for i, item in enumerate(distinct)]
        proof = 'by (simp add: ' + ' '.join(item['canonical_name'] for item in distinct) + ')'
        proposal = {'action': 'submit_batch', 'proofs': {'contract': proof}}
        feedback = {'status': 'rejected_batch', 'check': {'verdict': 'red'}}
        state = apply_feedback(state, proposal, feedback, 10)
        directory = self.root / 'batch-requests/000010'
        durable(directory / 'proposal.json', proposal)
        durable(directory / 'feedback.json', feedback)
        state.update(proofs={'contract': proof}, accepted_order=['contract'])
        memory = entry_retrieval_memory(state, 'contract', limit=3, directory=self.root)
        remembered = next(item for item in memory['facts'] if item['record_id'] == 'f')
        self.assertEqual(remembered['exact_statement'], shared['exact_statement'])
        context = remembered['memory_context']
        self.assertEqual(len(context['query_ids']), 3)
        self.assertEqual(len(context['query_contexts']), 3)
        self.assertEqual(context['query_total'], 120)
        self.assertTrue(context['has_more_queries'])
        action = context['query_index_action']
        self.assertEqual(action, {'action': 'read_evidence', 'index': 'queries',
                                  'entry_id': 'contract', 'offset': 0, 'limit': 3})
        # Existing target query pagination still reaches the complete history.
        index = retrieval_index_context(state, 'contract')
        page = context_index_page(index, {k: v for k, v in action.items() if k != 'entry_id'})
        visited = list(page['records'])
        while page['next_action']:
            page = context_index_page(index, page['next_action'])
            visited.extend(page['records'])
        self.assertEqual(len(visited), 120)
        self.assertEqual(len({item['query_id'] for item in visited}), 120)
        usage = memory['usage_evidence']
        for evidence in [usage['attempts'][0], usage['accepted_proof']]:
            self.assertEqual(len(evidence['mentions']), 3)
            self.assertEqual(evidence['mentions_total'], 120)
            self.assertTrue(evidence['has_more_mentions'])
        self.assertEqual(usage['attempts'][0]['read_action'],
                         {'action': 'read_attempts', 'request_numbers': [10], 'view': 'full'})
        self.assertEqual(usage['accepted_proof']['read_action'],
                         {'action': 'read_evidence', 'refs': [{'kind': 'proof', 'id': 'contract'}]})


class RetrievalUsageControllerTests(unittest.TestCase):
    def setUp(self):
        from regen.tests.current.test_retrieval_recovery_controller import RetrievalRecoveryControllerTests
        self.fixture = RetrievalRecoveryControllerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_real_controller_records_query_read_and_current_accepted_text_mention(self):
        f = self.fixture
        _, facts = f.query('usage')
        memory = f.entry()['retrieval_memory']
        self.assertEqual(memory['queries'][0]['query_context_revision'], 0)
        self.assertEqual(memory['usage_evidence']['explicit_fact_reads'], [])
        f.action({'action': 'read_evidence', 'refs': [{'kind': 'fact', 'id': facts[0]['record_id']}]})
        memory = f.entry()['retrieval_memory']
        self.assertEqual(len(memory['usage_evidence']['explicit_fact_reads']), 1)
        saved = f.action({'action': 'edit_proof', 'entry_id': 'target_contract',
                          'expected_hash': f.read_theory()['document']['source_sha256'],
                          'proof_body': 'by (simp add: Base.usage_0)'})
        checked = f.action({'action': 'check_theory', 'source_sha256': saved['source_sha256'],
                           'entry_ids': ['target_contract']})
        self.assertEqual(checked['feedback']['status'], 'accepted')
        memory = f.entry()['retrieval_memory']
        mentions = memory['usage_evidence']['accepted_proof']['mentions']
        self.assertEqual([item['record_id'] for item in mentions], [facts[0]['record_id']])
        self.assertFalse(mentions[0]['native_dependency_confirmed'])
        self.assertIn('accepted_prefix_changed', memory['queries'][0]['context_comparison']['differences'])


if __name__ == '__main__':
    unittest.main()
