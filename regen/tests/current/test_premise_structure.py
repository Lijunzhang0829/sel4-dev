"""Native matching protocol tests with a synthetic observer, never Isabelle.

Actual typed matching has a separate native smoke. These tests exercise the
source binding, observation protocol, bounds and production query integration.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.indexed_retrieval import reorder_candidates
from regen.execution.premise_structure import MAX_CANDIDATES, rerank, source
from regen.execution.proof_commits import digest
from regen.planning.batch_file_planner import batch_query


class Observer:
    timeout = 180

    def __init__(self, root, rows, verdict='green'):
        self.work_dir, self.rows, self.verdict = root, rows, verdict
        self.calls = []
        self.write_output = True

    def imports(self):
        return ['Semantic_Base.Semantic_Root']

    def _check_observation(self, rendered, label, *, writable_mounts, timeout):
        self.calls.append({'source': rendered, 'label': label, 'mounts': writable_mounts, 'timeout': timeout})
        output = writable_mounts[0][0] / 'structure.tsv'
        if self.write_output:
            output.write_text(''.join('MATCH\t' + '\t'.join(str(v).encode().hex() for v in row) + '\n'
                                      for row in self.rows))
        return {'verdict': self.verdict, 'returncode': 0 if self.verdict == 'green' else 1,
                'source_content': rendered, 'source_sha256': hashlib.sha256(rendered.encode()).hexdigest(),
                'log_content': 'synthetic observer',
                'log_sha256': hashlib.sha256(b'synthetic observer').hexdigest()}


class PremiseStructureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.node = {'id': 'target', 'statement': 'lemma target: "Q (x::nat)"',
                     'context': ['context Synthetic begin']}
        self.context = {'node': self.node, 'observation': None}
        self.records = [{'record_id': key, 'canonical_name': 'Base.' + key,
                         'exact_statement': 'original complete ' + key,
                         'proposition_digest': 'isabelle-term-yxml-sha1:' + str(i) * 40}
                        for i, key in enumerate(('unrelated', 'direct', 'rewrite'), 1)]
        self.rows = [
            ['unrelated', 'no_direct_match', '1' * 40, 'R x', '', ''],
            ['direct', 'conclusion_match', '2' * 40, 'Q x',
             ','.join(value.encode().hex() for value in ('P x', 'S x')), ''],
            ['rewrite', 'rewrite_match', '3' * 40, 'f x = x', '', ''],
        ]

    def test_native_observation_only_reorders_and_reports_unproved_premises(self):
        observer = Observer(self.root, self.rows)
        original = deepcopy(self.records)
        result = rerank(observer, self.context, self.records)
        self.assertEqual(result['ordered_record_ids'], ['direct', 'rewrite', 'unrelated'])
        self.assertEqual(result['evidence']['direct']['required_premises'], ['P x', 'S x'])
        self.assertTrue(all(not item['is_proof_authority'] and not item['applicability_proved']
                            for item in result['evidence'].values()))
        self.assertTrue(all(item['catalog_digest_verified'] for item in result['evidence'].values()))
        self.assertEqual(self.records, original)
        self.assertEqual(observer.calls[0]['timeout'], 120)

    def test_source_and_output_bytes_are_bound_in_observation_receipt(self):
        observer = Observer(self.root, self.rows)
        result = rerank(observer, self.context, self.records)
        receipt_path = next(self.root.rglob('observation.json'))
        receipt = json.loads(receipt_path.read_text())
        self.assertEqual(receipt['binding']['node'], self.node)
        self.assertEqual(receipt['binding']['imports'], observer.imports())
        self.assertEqual(receipt['source_sha256'], hashlib.sha256(observer.calls[0]['source'].encode()).hexdigest())
        self.assertEqual(receipt['output_sha256'], hashlib.sha256(
            (receipt_path.parent / 'structure.tsv').read_bytes()).hexdigest())
        self.assertEqual(result['evidence']['direct']['evidence_id'], digest(receipt))
        rendered = observer.calls[0]['source']
        self.assertIn(self.node['statement'], rendered)
        self.assertIn(self.node['context'][0], rendered)
        self.assertIn('oops', rendered)

    def test_multiline_premise_remains_one_complete_premise(self):
        rows = deepcopy(self.rows)
        premise = 'P x ==>\n  Q x & R x'
        rows[1][4] = premise.encode().hex()
        result = rerank(Observer(self.root, rows), self.context, self.records)
        self.assertEqual(result['evidence']['direct']['required_premises'], [premise])

    def test_repeat_observations_cannot_reuse_an_old_output_file(self):
        observer = Observer(self.root, self.rows)
        rerank(observer, self.context, self.records)
        observer.write_output = False
        with self.assertRaises(FileNotFoundError):
            rerank(observer, self.context, self.records)
        self.assertNotEqual(observer.calls[0]['mounts'][0][0], observer.calls[1]['mounts'][0][0])

    def test_failed_observation_preserves_all_lexical_candidates(self):
        observer = Observer(self.root, [], verdict='red')
        result = rerank(observer, self.context, self.records)
        self.assertEqual(result['ordered_record_ids'], [r['record_id'] for r in self.records])
        self.assertTrue(all(row['status'] == 'native_observation_failed'
                            for row in result['evidence'].values()))

    def test_printed_residual_is_not_reparsed_as_an_original_declaration(self):
        observer = Observer(self.root, self.rows)
        result = rerank(observer, {'node': {'statement': 'untrusted printed goal'},
                                  'observation': {'request_number': 1}}, self.records)
        self.assertEqual(observer.calls, [])
        self.assertTrue(all(row['status'] == 'unsupported_historical_residual'
                            for row in result['evidence'].values()))

    def test_invalid_candidates_and_non_declaration_are_rejected_before_native(self):
        observer = Observer(self.root, self.rows)
        for records in ([self.records[0], self.records[0]],
                        [{'record_id': str(n)} for n in range(MAX_CANDIDATES + 1)],
                        [{**self.records[0], 'fact_index': True}],
                        [{**self.records[0], 'proposition_digest': 'isabelle-term-yxml-sha1:bad'}]):
            with self.subTest(records=records[:1]), self.assertRaises(ValueError):
                rerank(observer, self.context, records)
        with self.assertRaises(ValueError):
            rerank(observer, {'node': {'statement': 'Q x'}}, self.records)
        self.assertEqual(observer.calls, [])

    def test_protocol_rejects_unknown_duplicate_missing_and_invalid_status_rows(self):
        invalid = [self.rows[:-1], [*self.rows, self.rows[0]],
                   [['unknown', *self.rows[0][1:]], *self.rows[1:]],
                   [['unrelated', 'green', *self.rows[0][2:]], *self.rows[1:]]]
        for rows in invalid:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                rerank(Observer(self.root, rows), self.context, self.records)

    def test_success_status_cannot_contradict_catalog_digest(self):
        for status, native_digest in (('conclusion_match', 'a' * 40),
                                      ('catalog_mismatch', '2' * 40),
                                      ('conclusion_match', 'malformed')):
            rows = deepcopy(self.rows)
            rows[1][1:3] = [status, native_digest]
            with self.subTest(status=status, native_digest=native_digest), self.assertRaises(ValueError):
                rerank(Observer(self.root, rows), self.context, self.records)

    def test_catalog_mismatch_remains_a_candidate_without_match_priority(self):
        rows = deepcopy(self.rows)
        rows[1][1:3] = ['catalog_mismatch', 'a' * 40]
        result = rerank(Observer(self.root, rows), self.context, self.records)
        self.assertEqual(result['ordered_record_ids'], ['rewrite', 'unrelated', 'direct'])
        self.assertFalse(result['evidence']['direct']['catalog_digest_verified'])
        self.assertFalse(result['evidence']['direct']['applicability_proved'])

    def test_successful_checker_response_must_bind_the_actual_submitted_source(self):
        class WrongSource(Observer):
            def _check_observation(self, *args, **kwargs):
                result = super()._check_observation(*args, **kwargs)
                result['source_content'] += '\n(* another source *)\n'
                result['source_sha256'] = hashlib.sha256(result['source_content'].encode()).hexdigest()
                return result
        with self.assertRaisesRegex(ValueError, 'different source'):
            rerank(WrongSource(self.root, self.rows), self.context, self.records)

    def test_multifact_index_and_names_are_encoded_in_exact_source(self):
        rendered = source(self.node, ['Base.Root'], [
            {'record_id': 'multi', 'canonical_name': 'Base.rules(2)'},
            {'record_id': 'explicit', 'canonical_name': 'display_name',
             'declaration_name': 'Base.actual', 'fact_index': 3}], 'Audit')
        self.assertIn('("multi","Base.rules",1,"")', rendered)
        self.assertIn('("explicit","Base.actual",3,"")', rendered)

    def test_callback_cannot_change_original_context_records_or_candidate_set(self):
        context, records = deepcopy(self.context), deepcopy(self.records)
        def callback(ctx, rows):
            ctx['node']['statement'] = 'lemma injected: False'
            rows[0]['exact_statement'] = 'modified'
            return {'ordered_record_ids': [r['record_id'] for r in reversed(rows)], 'evidence': {}}
        result, evidence = reorder_candidates(records, callback, context)
        self.assertEqual(context, self.context)
        self.assertEqual(records, self.records)
        self.assertEqual(result[-1]['exact_statement'], self.records[0]['exact_statement'])
        self.assertFalse(evidence['is_proof_authority'])
        with self.assertRaises(ValueError):
            reorder_candidates(records, lambda *_: {'ordered_record_ids': [r['record_id'] for r in records],
                                                    'evidence': {'unknown': {'status': 'match'}}}, context)

    def test_batch_query_uses_graph_node_and_strips_structural_switch_before_recall(self):
        directory = self.root / 'theory/batch-requests/000001'
        state = {'graph': {'nodes': [self.node]}, 'proofs': {}, 'accepted_order': []}
        request = {'goal_query': True, 'context_node_id': 'target',
                   'retrieval_policy': 'indexed-v1', 'structural_rerank': True}
        observer = Observer(self.root, self.rows)
        with patch('regen.execution.file_planner_recall.recall_for_node', return_value={}) as recall:
            batch_query({'premise_database': 'catalog'}, {}, state, observer, directory, request)
        args, kwargs = recall.call_args
        self.assertEqual(args[1], self.node)
        self.assertNotIn('structural_rerank', args[2])
        result = kwargs['candidate_reranker'](self.context, self.records)
        self.assertEqual(result['ordered_record_ids'][0], 'direct')
        self.assertEqual(state['proofs'], {})

    def test_batch_query_rejects_caller_goal_text_and_unbound_structural_requests(self):
        state = {'graph': {'nodes': [self.node]}, 'proofs': {}, 'accepted_order': []}
        observer = Observer(self.root, self.rows)
        base = {'goal_query': True, 'context_node_id': 'target',
                'retrieval_policy': 'indexed-v1', 'structural_rerank': True}
        for request in ({**base, 'statement': 'lemma injected: False'},
                        {**base, 'context_node_id': 'missing'},
                        {**base, 'retrieval_policy': 'legacy-v1'},
                        {**base, 'structural_rerank': 'yes'},
                        {'premise_query': 'fact', 'retrieval_policy': 'indexed-v1', 'structural_rerank': True}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                batch_query({'premise_database': 'unused'}, {}, state, observer,
                            self.root / 'theory/batch-requests/000001', request)
        self.assertEqual(observer.calls, [])

    def test_batch_historical_residual_preserves_selector_and_skips_native_reparse(self):
        state = {'graph': {'nodes': [self.node]}, 'proofs': {}, 'accepted_order': []}
        observer = Observer(self.root, self.rows)
        node = {**self.node, 'statement': 'Q x'}
        observation = {'request_number': 3, 'goal_index': 1, 'goal_sha256': 'bound-goal'}
        request = {'proof_state_query': {'request_number': 3, 'goal_index': 1},
                   'context_node_id': 'target', 'retrieval_policy': 'indexed-v1', 'structural_rerank': True}
        with patch('regen.planning.planner_workspace.intermediate_target', return_value=(node, observation)), \
             patch('regen.execution.file_planner_recall.recall_for_node', return_value={}) as recall:
            batch_query({'premise_database': 'unused'}, {}, state, observer,
                        self.root / 'theory/batch-requests/000001', request)
        args, kwargs = recall.call_args
        self.assertEqual(args[1], node)
        self.assertEqual(kwargs['observation'], observation)
        self.assertTrue(args[2]['goal_query'])
        result = kwargs['candidate_reranker']({'node': node, 'observation': observation}, self.records)
        self.assertEqual(result['ordered_record_ids'], [row['record_id'] for row in self.records])
        self.assertEqual(observer.calls, [])


if __name__ == '__main__':
    unittest.main()
