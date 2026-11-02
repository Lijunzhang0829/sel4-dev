"""Public evidence recovery preserves existing readers and authority checks."""
from copy import deepcopy
import unittest

from jsonschema import Draft202012Validator

from regen.planning.evidence_reads import evidence_reads, evidence_schema


class EvidenceReadsTest(unittest.TestCase):
    def setUp(self):
        self.schema = Draft202012Validator(evidence_schema())

    def invalid(self, proposal):
        self.assertTrue(list(self.schema.iter_errors(proposal)), proposal)
        with self.assertRaises(ValueError):
            evidence_reads(proposal)

    def test_mixed_refs_group_without_rewriting_ids_or_mutating_input(self):
        proposal = {'action': 'read_evidence', 'refs': [
            {'kind': 'query', 'id': 'query:first'}, {'kind': 'fact', 'id': 'premise:Case_sensitive'},
            {'kind': 'proof', 'id': 'entry-1'}, {'kind': 'fact', 'id': 'premise:second'}]}
        before = deepcopy(proposal)
        self.schema.validate(proposal)
        result = evidence_reads(proposal)
        self.assertEqual(result, [
            {'action': 'read_queries', 'query_ids': ['query:first']},
            {'action': 'read_facts', 'record_ids': ['premise:Case_sensitive', 'premise:second']},
            {'action': 'read_proofs', 'node_ids': ['entry-1']}])
        result[1]['record_ids'].append('changed-output')
        self.assertEqual(proposal, before)

    def test_distinct_namespaces_may_share_an_exact_id(self):
        proposal = {'action': 'read_evidence', 'refs': [
            {'kind': kind, 'id': 'same'} for kind in ('fact', 'query', 'proof')]}
        self.schema.validate(proposal)
        self.assertEqual(len(evidence_reads(proposal)), 3)

    def test_index_optional_fields_pass_through_without_eager_defaults(self):
        for proposal in [
                {'action': 'read_evidence', 'index': 'facts'},
                {'action': 'read_evidence', 'index': 'queries', 'offset': 32,
                 'limit': 100, 'snapshot_sha256': 'a' * 64, 'entry_id': 'target'}]:
            with self.subTest(proposal=proposal):
                self.schema.validate(proposal)
                before = deepcopy(proposal)
                self.assertEqual(evidence_reads(proposal), [{**proposal, 'action': 'read_context_index'}])
                self.assertEqual(proposal, before)

    def test_exact_limits(self):
        for kinds in [['fact'] * 24, ['fact'] * 16 + ['query'] * 4 + ['proof'] * 4]:
            proposal = {'action': 'read_evidence', 'refs': [
                {'kind': kind, 'id': str(index)} for index, kind in enumerate(kinds)]}
            self.schema.validate(proposal)
            self.assertEqual(sum(len(next(v for k, v in row.items() if k != 'action'))
                                 for row in evidence_reads(proposal)), 24)
        for kinds in [['fact'] * 25, ['query'] * 5, ['proof'] * 5]:
            self.invalid({'action': 'read_evidence', 'refs': [
                {'kind': kind, 'id': str(index)} for index, kind in enumerate(kinds)]})

    def test_invalid_ref_shapes_and_duplicates(self):
        for refs in [None, {}, [], [None], ['premise:x'], [{'kind': 'fact'}],
                [{'kind': 'fact', 'id': 'x', 'extra': True}],
                [{'kind': 'unknown', 'id': 'x'}], [{'kind': [], 'id': 'x'}],
                [{'kind': 'fact', 'id': value} for value in ['x', 'x']]]:
            with self.subTest(refs=refs):
                self.invalid({'action': 'read_evidence', 'refs': refs})
        for identity in [None, 7, True, [], {}, '', ' \n\t']:
            with self.subTest(identity=identity):
                self.invalid({'action': 'read_evidence', 'refs': [{'kind': 'fact', 'id': identity}]})

    def test_invalid_request_modes_and_unknown_fields(self):
        for proposal in [None, [], {}, {'action': 'read_facts', 'refs': []},
                {'action': 'read_evidence'},
                {'action': 'read_evidence', 'refs': [{'kind': 'fact', 'id': 'x'}], 'index': 'facts'},
                {'action': 'read_evidence', 'refs': [{'kind': 'fact', 'id': 'x'}], 'offset': 0},
                {'action': 'read_evidence', 'index': 'facts', 'query': 'something'},
                {'action': 'read_evidence', 'index': 'proofs'}]:
            with self.subTest(proposal=proposal):
                self.invalid(proposal)

    def test_invalid_pagination(self):
        for key, values in {'offset': [-1, True, 1.5, '1', None],
                            'limit': [0, 101, False, 2.5, '2', None],
                            'entry_id': ['', ' \n', 5, None],
                            'snapshot_sha256': ['', ' \n', 5, None]}.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.invalid({'action': 'read_evidence', 'index': 'facts', key: value})

    def test_schema_instances_are_independent(self):
        first = evidence_schema()
        first['oneOf'][0]['properties']['refs']['items']['properties']['kind']['enum'].append('invented')
        self.assertNotIn('invented', evidence_schema()['oneOf'][0]['properties']['refs']['items']['properties']['kind']['enum'])


if __name__ == '__main__':
    unittest.main()
