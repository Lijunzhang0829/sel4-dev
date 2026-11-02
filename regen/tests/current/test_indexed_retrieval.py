import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from regen.analysis.premise_lab_strategies import build_catalog
from regen.execution.file_planner_recall import recall_for_node
from regen.execution.indexed_retrieval import (
    build_generated_index, generated_candidates, generated_index_for,
    reorder_candidates, verify_generated_index, build_upstream_index, upstream_index_for,
    verify_upstream_index,
)
from regen.execution.proof_commits import digest
from regen.execution.proof_state_recall import build_vectors, recall_for_proof_state, vector
from regen.preparation.premise_database import digest as file_digest


class IndexedRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def database(self, count):
        rows = [{'record_id': f'p{i:05d}', 'canonical_name': f'Base.common_rule_{i:05d}',
                 'exact_statement': f'common item_{i} ==> conclusion item_{i}',
                 'proposition_digest': f'synthetic:{i}'} for i in range(count)]
        return build_catalog(self.root / 'catalog', rows)

    def generated(self, count=10, extras=()):
        proof = 'by simp'
        rows = [{'record_id': f'g{i:05d}', 'canonical_name': f'Generated.common_rule_{i:05d}',
                 'exact_statement': f'common item_{i} ==> conclusion item_{i}', 'context': [],
                 'source_class': 'generated_theorem', 'status': 'sealed',
                 'theory_seal_id': 'seal-A', 'proof_commit_id': 'commit-A',
                 'proof_body': proof, 'proof_sha256': digest(proof)} for i in range(count)]
        rows.extend(extras)
        path = self.root / 'sealed.json'
        path.write_text(json.dumps({'records': rows}))
        return path

    def test_upstream_ten_thousand_only_scores_bounded_candidates(self):
        database = self.database(10000)
        index = self.root / 'vectors.sqlite'
        build_vectors(database, index)
        result = recall_for_proof_state(database, 'common', vector_index=index,
                                        retrieval_policy='indexed-v1', candidate_budget=17)
        pipeline = result['candidate_pipeline']
        self.assertEqual(pipeline['vector_candidates_scored'], 17)
        self.assertEqual(pipeline['selected_candidates'], 17)
        self.assertTrue(pipeline['candidate_truncated'])
        self.assertEqual(pipeline['fts_candidates'], 500)
        self.assertEqual(len(result['records']), 5)

    def test_reranking_cannot_recover_fts_miss(self):
        database = build_catalog(self.root / 'catalog', [
            {'record_id': 'family', 'canonical_name': 'Base.family',
             'exact_statement': 'valid_ghost_guard sigma'}])
        index = self.root / 'vectors.sqlite'
        build_vectors(database, index)
        # Legacy vectors add component matches that its FTS index does not expose.
        legacy = recall_for_proof_state(database, 'ghost_guard', vector_index=index)
        bounded = recall_for_proof_state(database, 'ghost_guard', vector_index=index,
                                         retrieval_policy='indexed-v1', candidate_budget=5)
        self.assertEqual([r['record_id'] for r in legacy['records']], ['family'])
        self.assertEqual(bounded['records'], [])
        self.assertEqual(bounded['candidate_pipeline']['vector_candidates_scored'], 0)

        # Second iteration fixes candidate generation rather than hoping that
        # reranking can retrieve facts it was never given.
        normalized = build_upstream_index(database, self.root / 'normalized')
        fixed = recall_for_proof_state(database, 'ghost_guard', vector_index=index,
            retrieval_policy='indexed-v1', candidate_budget=5, lexical_index=normalized)
        self.assertEqual([r['record_id'] for r in fixed['records']], ['family'])
        self.assertEqual(fixed['candidate_pipeline']['vector_candidates_scored'], 1)

    def test_normalized_upstream_index_is_bound_to_catalog_and_bytes(self):
        database = self.database(10)
        index = upstream_index_for(database, self.root / 'indexes')
        before = file_digest(database)
        page = recall_for_proof_state(database, 'common', retrieval_policy='indexed-v1', lexical_index=index)
        self.assertTrue(page['records'])
        self.assertEqual(file_digest(database), before)
        self.assertEqual(index, upstream_index_for(database, self.root / 'indexes'))
        with sqlite3.connect(index) as db:
            db.execute("UPDATE metadata SET value='{}'")
        with self.assertRaisesRegex(ValueError, 'stale'):
            recall_for_proof_state(database, 'common', retrieval_policy='indexed-v1', lexical_index=index)

    def test_generated_indexes_publish_consistently_for_concurrent_builders(self):
        from concurrent.futures import ThreadPoolExecutor
        source = self.generated(30)
        with ThreadPoolExecutor(max_workers=3) as pool:
            paths = list(pool.map(lambda _: generated_index_for(source, self.root / 'indexes'), range(3)))
        self.assertEqual(len(set(paths)), 1)
        verify_generated_index(source, paths[0])

    def test_legacy_default_and_explicit_policy_are_identical(self):
        database = self.database(12)
        self.assertEqual(recall_for_proof_state(database, 'common'),
                         recall_for_proof_state(database, 'common', retrieval_policy='legacy-v1'))
        for kwargs in ({'retrieval_policy': 'unknown'}, {'candidate_budget': True}, {'candidate_budget': 501}):
            with self.assertRaises(ValueError):
                recall_for_proof_state(database, 'common', **kwargs)

    def test_generated_ten_thousand_only_scores_bounded_candidates(self):
        source = self.generated(10000)
        index = build_generated_index(source, self.root / 'index')
        with patch('regen.execution.file_planner_recall.vector', wraps=vector) as scoring:
            result = recall_for_node(self.root / 'unused', {'id': 'target', 'statement': 'common', 'context': []},
                {'goal_query': True, 'scope': 'sealed', 'retrieval_policy': 'indexed-v1', 'candidate_budget': 19},
                generated_records=source, generated_index=index, audit_path=self.root / 'query.json')
        self.assertEqual(scoring.call_count, 20)  # one target plus 19 candidates
        info = result['candidate_pipeline']['generated']
        self.assertEqual(info['indexed_records'], 10000)
        self.assertEqual(info['vector_candidates_scored'], 19)
        self.assertLessEqual(result['candidate_pipeline']['combined_candidates'], 19)

    def test_generated_index_excludes_pending_and_uncertified_examples(self):
        base = {'record_id': 'bad', 'canonical_name': 'Bad.common', 'exact_statement': 'common x',
                'source_class': 'generated_theorem', 'status': 'sealed', 'theory_seal_id': 'seal',
                'proof_commit_id': 'commit', 'proof_body': 'by simp', 'proof_sha256': 'wrong'}
        source = self.generated(1, [base, {**base, 'record_id': 'pending', 'status': 'pending'},
                                     {**base, 'record_id': 'human', 'source_class': 'deleted_human_proof'},
                                     {**base, 'record_id': 'unsealed', 'theory_seal_id': None}])
        index = build_generated_index(source, self.root / 'index')
        helper = generated_candidates(source, index, 'common')
        example = generated_candidates(source, index, 'common', purpose='proof_example')
        self.assertEqual({r['record_id'] for r in helper['records']}, {'g00000', 'bad'})
        self.assertNotIn('proof_body', next(r for r in helper['records'] if r['record_id'] == 'bad'))
        self.assertEqual([r['record_id'] for r in example['records']], ['g00000'])

    def test_changed_source_and_tampered_index_are_rejected(self):
        source = self.generated(2)
        index = build_generated_index(source, self.root / 'index')
        original = source.read_text()
        source.write_text(original + '\n')
        with self.assertRaisesRegex(ValueError, 'stale'):
            generated_candidates(source, index, 'common')
        source.write_text(original)
        with sqlite3.connect(index) as db:
            db.execute("UPDATE records SET record_json='{}'")
        with self.assertRaisesRegex(ValueError, 'stale'):
            verify_generated_index(source, index)

    def test_unhashed_sqlite_sidecars_are_rejected_for_both_derived_indexes(self):
        source = self.generated(2)
        database = self.database(2)
        generated = build_generated_index(source, self.root / 'generated-index')
        upstream = build_upstream_index(database, self.root / 'upstream-index')
        for index, verify, original in [(generated, verify_generated_index, source),
                                        (upstream, verify_upstream_index, database)]:
            before = file_digest(index)
            for suffix in ('-wal', '-journal'):
                with self.subTest(index=index.name, sidecar=suffix):
                    sidecar = Path(str(index) + suffix)
                    sidecar.write_bytes(b'unhashed changed SQLite pages')
                    self.assertEqual(file_digest(index), before)
                    with self.assertRaisesRegex(ValueError, 'sidecar'):
                        verify(original, index)
                    sidecar.unlink()
            verify(original, index)

    def test_generated_index_normalizes_components_and_keeps_complete_statement(self):
        text = 'valid_ghost_guard sigma ==> ' + ' & '.join(f'guard_{i} sigma' for i in range(300))
        source = self.generated(0, [{'record_id': 'g', 'canonical_name': 'Gen.rule',
            'exact_statement': text, 'source_class': 'generated_theorem', 'status': 'sealed',
            'theory_seal_id': 'seal', 'context': []}])
        index = generated_index_for(source, self.root / 'indexes')
        result = generated_candidates(source, index, 'ghost_guard')
        self.assertEqual(result['records'][0]['exact_statement'], text)
        self.assertEqual(generated_index_for(source, self.root / 'indexes'), index)

    def test_reranker_can_only_permute_bounded_candidates(self):
        records = [{'record_id': 'a', 'exact_statement': 'original A'},
                   {'record_id': 'b', 'exact_statement': 'original B'}]
        def rerank(context, values):
            values[0]['exact_statement'] = 'tampered callback argument'
            return {'ordered_record_ids': ['b', 'a'], 'evidence': {'b': {'matched': True}}}
        result, evidence = reorder_candidates(records, rerank, {'target': 'target'})
        self.assertEqual(result[1]['exact_statement'], 'original A')
        self.assertFalse(evidence['is_proof_authority'])
        for ids in (['a'], ['a', 'a'], ['a', 'injected']):
            with self.assertRaises(ValueError):
                reorder_candidates(records, lambda *_: {'ordered_record_ids': ids}, {})

    def test_file_planner_structural_hook_receives_bounded_pool(self):
        source = self.generated(30)
        seen = []
        def rerank(context, records):
            seen.append((context, records))
            return {'ordered_record_ids': [r['record_id'] for r in reversed(records)], 'evidence': {}}
        result = recall_for_node(self.root / 'unused', {'id': 'target', 'statement': 'common', 'context': []},
            {'goal_query': True, 'scope': 'sealed', 'retrieval_policy': 'indexed-v1', 'candidate_budget': 11},
            generated_records=source, audit_path=self.root / 'query.json', candidate_reranker=rerank)
        self.assertEqual(len(seen[0][1]), 11)
        self.assertEqual(len(result['records']), 5)
        self.assertEqual(result['candidate_pipeline']['structural_rerank']['examined_candidates'], 11)
        self.assertEqual(result['records'][0]['record_id'], seen[0][1][-1]['record_id'])


if __name__ == '__main__':
    unittest.main()
