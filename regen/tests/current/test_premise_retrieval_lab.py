"""Independent checks for the offline retrieval laboratory and its measurements."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from regen.analysis.premise_lab_strategies import (
    VARIANTS, build_catalog, prepare_variant, retrieve,
)
from regen.execution.premise_recall import recall_premises
from regen.execution.proof_state_recall import recall_for_proof_state
from regen.preparation.premise_database import digest


def record(identifier, name, statement, *, group=None, **extra):
    return {
        'record_id': identifier, 'canonical_name': name,
        'exact_statement': statement,
        'proposition_digest': group or 'synthetic:' + hashlib.sha256(statement.encode()).hexdigest(),
        'owner_theory': name.split('.')[0], 'owner_session': 'Synthetic_Lab',
        'source_class': 'frozen_parent', 'attributes': [], **extra,
    }


def query(identifier='q', *, mode='search', text='alpha', relevant=(), forbidden=(), empty=False):
    return {'id': identifier, 'category': 'independent-regression', 'mode': mode,
            'query': text, 'relevant_ids': list(relevant), 'forbidden_ids': list(forbidden),
            'expected_empty': empty, 'judgment': 'synthetic_authored',
            'rationale': 'Authored retrieval behavior, not a native applicability judgment.'}


class PremiseRetrievalLabTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.records = [
            record('a', 'Base.alpha_signal', 'alpha_signal item = item'),
            record('b', 'Other.alpha_signal', 'alpha_signal (f item) = f item'),
            record('c', 'Base.beta_rule', 'beta_signal item = item'),
        ]

    def catalog(self, records=None, suffix='catalog'):
        return build_catalog(self.root / suffix, self.records if records is None else records)

    @staticmethod
    def identity(page):
        return [(r['record_id'], r['exact_statement']) for r in page['records']]

    def test_baseline_first_page_matches_production_exact_search_and_goal(self):
        database = self.catalog()
        for mode, text in [('exact', 'alpha_signal'), ('search', 'alpha'),
                           ('goal', 'alpha_signal item')]:
            with self.subTest(mode=mode):
                native_reader = (recall_for_proof_state(database, text, limit=5)
                                 if mode == 'goal' else
                                 recall_premises(database, text, mode=mode, scope='upstream'))
                lab = retrieve(database, {'mode': mode, 'query': text}, candidate_budget=15)
                self.assertEqual(self.identity(lab), self.identity(native_reader))
                self.assertFalse(lab['is_proof_authority'])
        manifest = json.loads((database.parent / 'parent-premises-manifest.json').read_text())
        self.assertTrue(manifest['synthetic'])
        self.assertFalse(manifest['native_validated'])

    def test_labels_are_not_catalog_or_index_fields_and_cannot_be_query_input(self):
        secret = 'LABEL_ONLY_MUST_NOT_REACH_RANKER'
        records = [{**self.records[0], 'gold_label': secret, 'relevant_ids': ['a']}]
        database = self.catalog(records)
        with sqlite3.connect(database) as connection:
            columns = {r[1] for r in connection.execute('PRAGMA table_info(premises)')}
            self.assertNotIn('gold_label', columns)
            self.assertNotIn('relevant_ids', columns)
            values = repr(connection.execute('SELECT * FROM premises').fetchall())
            self.assertNotIn(secret, values)
        prepared = prepare_variant(database, 'normalized_fts', self.root / 'index')
        with sqlite3.connect(prepared['index_path']) as connection:
            values = repr(connection.execute('SELECT * FROM normalized_search').fetchall())
            self.assertNotIn(secret, values)
            self.assertNotIn(secret.lower().encode().hex(), values)
        with self.assertRaisesRegex(ValueError, 'labels are separate'):
            retrieve(database, {'mode': 'search', 'query': 'alpha', 'relevant_ids': ['a']})

    def test_exact_missing_prime_and_spelling_controls_survive_all_variants(self):
        database = self.catalog([
            record('prime', "Base.obj_at'_def", "obj_at' object = True"),
            record('plain', 'Base.obj_at_def', 'obj_at object = False'),
            record('snake', 'Base.get_cell_def', 'get_cell object = object'),
            record('camel', 'Base.getCell_def', 'getCell object = object'),
        ])
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                for text, expected in [("obj_at'_def", ['prime']), ('get_cell_def', ['snake']),
                                       ('getCell_def', ['camel']), ('missing_def', [])]:
                    page = retrieve(database, {'mode': 'exact', 'query': text}, variant=variant)
                    self.assertEqual([r['record_id'] for r in page['records']], expected)
                page = retrieve(database, {'mode': 'search', 'query': "obj_at'"}, variant=variant)
                self.assertEqual([r['record_id'] for r in page['records']], ['prime'])

    def test_body_candidate_ablation_keeps_existing_order_and_adds_body_only_match(self):
        database = self.catalog([
            record('named', 'Lab.signal_rule', 'unrelated object = object'),
            record('body', 'Lab.bridge', 'signal object = object'),
        ])
        request = {'mode': 'search', 'query': 'signal'}
        baseline = retrieve(database, request)
        ablation = retrieve(database, request, variant='body_candidates')
        self.assertEqual([r['record_id'] for r in baseline['records']], ['named'])
        self.assertEqual([r['record_id'] for r in ablation['records']], ['named', 'body'])
        self.assertEqual(baseline['records'][0]['score'], ablation['records'][0]['score'])
        # This ablation does not relax the all-query-terms requirement.
        missing = retrieve(database, {'mode': 'search', 'query': 'signal absent'},
                           variant='body_candidates')
        self.assertEqual(missing['records'], [])

    def test_normalized_fts_indexes_the_same_identifier_components_as_query(self):
        database = self.catalog([
            record('compound', 'Lab.rare_component', 'rare_component object = object'),
            record('other', 'Lab.unrelated', 'unrelated object = object'),
        ])
        request = {'mode': 'goal', 'query': 'rare component'}
        self.assertEqual(retrieve(database, request)['records'], [])
        prepared = prepare_variant(database, 'normalized_fts', self.root / 'normalized')
        page = retrieve(database, request, variant='normalized_fts', prepared=prepared)
        self.assertEqual([r['record_id'] for r in page['records']], ['compound'])
        exact = retrieve(database, {'mode': 'exact', 'query': 'rare_component'},
                         variant='normalized_fts', prepared=prepared)
        self.assertEqual([r['record_id'] for r in exact['records']], ['compound'])

    def test_candidate_budget_is_applied_before_alias_grouping(self):
        records = [record(f'a{i}', f'Lab.token_{i:02d}', 'token object = object', group='alias')
                   for i in range(5)]
        records.append(record('z', 'Lab.token_99', 'token (f object) = f object', group='distinct'))
        database = self.catalog(records)
        request = {'mode': 'search', 'query': 'token'}
        narrow = retrieve(database, request, variant='digest_groups', candidate_budget=5)
        self.assertEqual(narrow['raw_candidates_considered'], 5)
        self.assertEqual(len(narrow['records']), 1)
        self.assertEqual(set(narrow['records'][0]['alias_ids']), {f'a{i}' for i in range(5)})
        self.assertTrue(narrow['truncated'])
        self.assertNotIn('z', [r['record_id'] for r in narrow['records']])
        wider = retrieve(database, request, variant='digest_groups', candidate_budget=6)
        self.assertEqual([r['record_id'] for r in wider['records']], ['a0', 'z'])
        self.assertFalse(wider['truncated'])

    def test_grouping_preserves_complete_statement_and_alias_origins(self):
        statement = 'long_token (' + 'function ' * 1500 + 'object) = object'
        database = self.catalog([
            record('a', 'Alpha.long_token', statement, group='same'),
            record('b', 'Beta.long_token', statement, group='same'),
        ])
        page = retrieve(database, {'mode': 'search', 'query': 'long_token'}, variant='digest_groups')
        self.assertEqual(len(page['records']), 1)
        self.assertEqual(page['records'][0]['exact_statement'], statement)
        aliases = page['records'][0]['aliases']
        self.assertEqual({r['owner_theory'] for r in aliases}, {'Alpha', 'Beta'})
        self.assertEqual({r['record_id'] for r in aliases}, {'a', 'b'})
        exact = retrieve(database, {'mode': 'exact', 'query': 'long_token'}, variant='digest_groups')
        self.assertEqual(len(exact['records']), 2)

    def test_pagination_terminates_without_repetition_at_a_fixed_budget(self):
        database = self.catalog([
            record(f'r{i}', f'Lab.page_token_{i:02d}', f'page_token object = value_{i}')
            for i in range(12)
        ])
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                request = {'mode': 'search', 'query': 'page_token'}
                offset, seen, page_count = 0, [], 0
                while offset is not None:
                    page = retrieve(database, request, variant=variant,
                                    limit=2, offset=offset, candidate_budget=9)
                    self.assertTrue(page['truncated'])
                    seen.extend(r['record_id'] for r in page['records'])
                    offset = page['next_offset']
                    page_count += 1
                    self.assertLessEqual(page_count, 5)
                self.assertEqual(len(seen), 9)
                self.assertEqual(len(set(seen)), 9)
                exhausted = retrieve(database, request, variant=variant,
                                     limit=2, offset=9, candidate_budget=9)
                self.assertEqual(exhausted['records'], [])
                self.assertIsNone(exhausted['next_offset'])

    def test_serialized_descriptor_replays_and_does_not_modify_source_catalog(self):
        database = self.catalog()
        before = database.read_bytes()
        prepared = prepare_variant(database, 'normalized_fts', self.root / 'normalized')
        request = {'mode': 'goal', 'query': 'alpha signal'}
        first = retrieve(database, request, variant='normalized_fts', prepared=prepared)
        self.assertTrue(first['records'])
        replay = retrieve(database, request, variant='normalized_fts',
                          prepared=json.loads(json.dumps(prepared)))
        self.assertEqual(first, replay)
        self.assertEqual(database.read_bytes(), before)
        with self.assertRaises(FileExistsError):
            build_catalog(database.parent, self.records)

    def test_stale_catalog_variant_and_tampered_index_are_rejected(self):
        database = self.catalog()
        prepared = prepare_variant(database, 'normalized_fts', self.root / 'normalized')
        request = {'mode': 'goal', 'query': 'alpha signal'}
        for changed in [{**prepared, 'database_sha256': 'wrong'}, {**prepared, 'variant': 'combined'}]:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                retrieve(database, request, variant='normalized_fts', prepared=changed)
        other = self.catalog([self.records[2]], suffix='other')
        with self.assertRaises(ValueError):
            retrieve(other, request, variant='normalized_fts', prepared=prepared)
        with sqlite3.connect(prepared['index_path']) as connection:
            connection.execute('UPDATE metadata SET value=?',
                               (json.dumps({'database_sha256': 'wrong', 'normalizer': prepared['normalizer']}),))
        with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
            retrieve(database, request, variant='normalized_fts', prepared=prepared)
        changed = {**prepared, 'index_sha256': digest(prepared['index_path'])}
        with self.assertRaisesRegex(ValueError, 'another catalog or encoder'):
            retrieve(database, request, variant='normalized_fts', prepared=changed)
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE premises SET exact_statement='changed' WHERE record_id='a'")
        with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
            retrieve(database, request, variant='baseline')

    def test_metrics_count_proposition_groups_and_exclude_unlabelled_positives(self):
        from regen.tools.premise_retrieval_lab import score_query
        records = [record('a', 'A.alias', 'same', group='same'),
                   record('b', 'B.alias', 'same', group='same'),
                   record('c', 'C.other', 'different', group='other')]
        q = query(relevant=['a', 'b', 'c'])
        measured = score_query(records, q, records[:2], 5)
        self.assertEqual(measured['relevant_group_count'], 2)
        self.assertEqual(measured['relevant_groups_found'], 1)
        self.assertEqual(measured['group_recall_at_k'], 0.5)
        self.assertEqual(measured['duplicate_proposition_slots'], 1)
        # A different alias still counts as a hit for the same encoded proposition.
        alias = score_query(records, query(relevant=['a']), [records[1]], 5)
        self.assertEqual(alias['group_recall_at_k'], 1.0)
        negative = score_query(records, query(forbidden=['a'], empty=True), [records[1]], 5)
        self.assertIsNone(negative['group_recall_at_k'])
        self.assertIsNone(negative['reciprocal_rank_at_k'])
        self.assertEqual(negative['forbidden_group_hits'], 1)
        self.assertTrue(negative['expected_empty_violation'])

    def test_fixture_validation_rejects_contradictory_alias_labels(self):
        from regen.tools.premise_retrieval_lab import validate_fixture
        records = [record('a', 'A.alias', 'same', group='same'),
                   record('b', 'B.alias', 'same', group='same')]
        fixture = {'kind': 'synthetic', 'records': records,
                   'queries': [query(relevant=['a'], forbidden=['b'])]}
        with self.assertRaisesRegex(ValueError, 'same proposition group'):
            validate_fixture(fixture)

    def test_evaluator_sends_only_query_and_mode_to_the_ranker(self):
        from unittest.mock import patch
        from regen.tools import premise_retrieval_lab as lab
        database = self.catalog()
        prepared = prepare_variant(database, 'baseline', self.root / 'unused')
        q = query(relevant=['a'], forbidden=['c'])
        with patch.object(lab, 'retrieve', wraps=retrieve) as reader:
            result = lab.evaluate_query(database, self.records, q, 'baseline', prepared, 5, 10)
        self.assertTrue(result['serializable_descriptor_replay_stable'])
        self.assertGreaterEqual(reader.call_count, 2)
        for call in reader.call_args_list:
            self.assertEqual(call.args[1], {'query': 'alpha', 'mode': 'search'})

    def test_cli_custom_fixture_writes_reviewable_results_and_refuses_overwrite(self):
        fixture = {'schema': 'premise-retrieval-lab-fixture-v1', 'kind': 'synthetic',
                   'records': self.records, 'queries': [query(relevant=['a', 'b'])], 'notes': []}
        path = self.root / 'fixture.json'
        path.write_text(json.dumps(fixture))
        output = self.root / 'run'
        command = [sys.executable, '-m', 'regen.tools.premise_retrieval_lab',
                   '--output', str(output), '--fixture', str(path), '--noise-records', '0',
                   '--seeds', '0', '--variants', 'baseline', 'combined', '--candidate-budget', '10']
        first = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(first.returncode, 0, first.stderr)
        summary_path = output / 'summary.json'
        saved = summary_path.read_bytes()
        summary = json.loads(saved)
        self.assertEqual(summary['kind'], 'synthetic')
        self.assertEqual(summary['scenarios'][0]['queries'], 1)
        self.assertEqual(set(summary['scenarios'][0]['by_variant']), {'baseline', 'combined'})
        detail = output / 'noise-0-seed-0' / 'queries.jsonl'
        rows = [json.loads(line) for line in detail.read_text().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertIn('first_response', rows[0])
        self.assertIn('exact_statement', rows[0]['first_response']['records'][0])
        self.assertTrue((output / 'REPORT.md').is_file())
        repeated = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(repeated.returncode, 0)
        self.assertEqual(summary_path.read_bytes(), saved)


if __name__ == '__main__':
    unittest.main()
