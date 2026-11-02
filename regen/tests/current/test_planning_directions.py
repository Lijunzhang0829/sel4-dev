"""Tentative contract directions survive planning without granting proof authority."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator

from regen.planning.import_contract import build_bundle_v6, normalize_v6, proposal_v5
from regen.planning.repository_plan_contract import proposal_schema, schema_help
from regen.planning.workspace_harness import diagnostics, short_view
from regen.planning.workspace_preflight import render
from regen.preparation.refine_campaign import sha256_json
from regen.tests.current.test_collaboration_plan import fixture


def direction(key='TryAccess', target='C2', candidates=None, premises=None):
    return {
        'id': key, 'target_contract_id': target,
        'direction': 'Investigate the related operation; independently prove the target if reuse fails.',
        'candidate_contract_ids': ['C1'] if candidates is None else candidates,
        'candidate_premise_ids': [] if premises is None else premises,
        'open_questions': ['Are the candidate assumptions available at this proof point?'],
    }


class PlanningDirectionsTest(unittest.TestCase):
    def setUp(self):
        self.payload, self.proposal = fixture()
        self.premise = {
            'record_id': 'parent:operation_identity',
            'name': 'operation_identity',
            'exact_statement': 'lemma operation_identity: "P x = P x"',
            'source': 'frozen_parent', 'owner_theory': 'Semantic.Root',
        }
        self.payload['planner_evidence'] = {'inspected_parent_premises': [self.premise]}

    def test_absent_optional_field_preserves_pre_extension_artifact_identity(self):
        # Golden digest captured before adding directions. It binds every field,
        # plan ID and projection hash, including the absence of new empty keys.
        payload, proposal = fixture()
        artifacts = build_bundle_v6(payload, proposal)
        self.assertEqual(sha256_json(artifacts),
            '2fcf7b7482158dc408c930e52e686653666b4cebab098f4cc830e9748d6c0838')
        for value in artifacts.values():
            self.assertNotIn('proof_direction_suggestions', value)
            self.assertNotIn('proof_direction_contracts', value)
            self.assertNotIn('proof_direction_premises', value)

    def test_schema_and_hard_core_accept_optional_advice_only_in_v6(self):
        validator = Draft202012Validator(proposal_schema(6))
        validator.validate(self.proposal)
        self.proposal['proof_direction_suggestions'] = [direction()]
        validator.validate(self.proposal)
        hard = proposal_v5(self.proposal)
        self.assertNotIn('proof_direction_suggestions', hard)
        self.assertEqual(hard['theories'], self.proposal['theories'])
        hard['proof_direction_suggestions'] = [direction()]
        self.assertTrue(list(Draft202012Validator(proposal_schema(5)).iter_errors(hard)))
        self.assertNotIn('proof_direction_suggestions', proposal_schema(4)['properties'])
        self.assertIn('candidate availability', schema_help())

    def test_pending_reciprocal_and_alternative_directions_create_no_authority(self):
        self.proposal['proof_direction_suggestions'] = [
            direction(premises=[self.premise['record_id']]),
            direction('TryReverse', 'C1', ['C2']),
            direction('TryIndependent', 'C2', [], []),
        ]
        artifacts = build_bundle_v6(self.payload, self.proposal)
        plan = artifacts['repository-plan.json']
        self.assertEqual(len(plan['proof_direction_suggestions']), 3)
        self.assertEqual(plan['required_import_edges'], [])
        self.assertEqual(plan['theory_order'], ['Access', 'Update'])
        self.assertFalse(plan['semantics']['proof_direction_suggestions_grant_premise_visibility'])
        self.assertFalse(plan['semantics']['proof_direction_suggestions_create_readiness_dependencies'])
        self.assertFalse(plan['semantics']['proof_direction_suggestions_create_imports'])
        for entry in artifacts['file-generation-list.json']['files']:
            projection = artifacts[entry['projection']]
            self.assertEqual(projection['required_imports'], [])
            self.assertEqual(projection['required_providers'], [])
            self.assertEqual(projection['legal_premises'], [])
            self.assertEqual(projection['reused_parent_premise_ids'], [])
            self.assertEqual(entry['provider_theories'], [])

    def test_projection_filters_by_target_and_copies_fixed_reference_content(self):
        self.payload['contracts'][0]['owner_theory'] = 'Original.Secret'
        self.proposal['proof_direction_suggestions'] = [
            direction(premises=[self.premise['record_id']]),
            direction('TryReverse', 'C1', ['C2']),
        ]
        before = deepcopy((self.payload, self.proposal))
        artifacts = build_bundle_v6(self.payload, self.proposal)
        access, update = artifacts['files/Access.json'], artifacts['files/Update.json']
        self.assertEqual([d['id'] for d in access['proof_direction_suggestions']], ['TryReverse'])
        self.assertEqual([d['id'] for d in update['proof_direction_suggestions']], ['TryAccess'])
        reference = update['proof_direction_contracts'][0]
        expected = {k: v for k, v in self.payload['contracts'][0].items() if k != 'owner_theory'}
        self.assertEqual(reference, {**expected, 'provider_theory': 'Access'})
        self.assertNotIn('owner_theory', reference)
        self.assertEqual(update['proof_direction_premises'], [self.premise])
        self.assertEqual(access['proof_direction_premises'], [])
        self.assertEqual(update['contracts'], self.payload['contracts'][1:])
        self.assertEqual(artifacts['file-generation-list.json']['proof_direction_suggestions'],
                         artifacts['repository-plan.json']['proof_direction_suggestions'])
        reference['contract_owned_context'].append('mutated output')
        update['proof_direction_premises'][0]['exact_statement'] = 'mutated output'
        self.assertEqual((self.payload, self.proposal), before)

    def test_directions_bind_plan_identity_and_all_projection_hashes(self):
        self.proposal['proof_direction_suggestions'] = [direction()]
        first = build_bundle_v6(self.payload, self.proposal)
        self.proposal['proof_direction_suggestions'][0]['open_questions'].append('Try another instantiation?')
        second = build_bundle_v6(self.payload, self.proposal)
        self.assertNotEqual(first['repository-plan.json']['plan_id'], second['repository-plan.json']['plan_id'])
        for entry in second['file-generation-list.json']['files']:
            projection = second[entry['projection']]
            self.assertEqual(entry['projection_sha256'], sha256_json(projection))
            self.assertEqual(projection['plan_id'], second['repository-plan.json']['plan_id'])

    def test_workspace_validation_uses_actual_inspection_receipts(self):
        self.proposal['proof_direction_suggestions'] = [direction(premises=[self.premise['record_id']])]
        self.assertEqual(diagnostics(self.payload, self.proposal, self.payload['planner_evidence']), [])
        errors = diagnostics(self.payload, self.proposal, {})
        self.assertTrue(any('inspected parent premise' in item['message'] for item in errors))

    def test_advice_does_not_change_native_preflight_declarations(self):
        advised = deepcopy(self.proposal)
        advised['proof_direction_suggestions'] = [direction()]
        with tempfile.TemporaryDirectory() as tmp:
            before, after = Path(tmp) / 'before', Path(tmp) / 'after'
            self.assertEqual(render(self.payload, self.proposal, before, 'Semantic.Root'),
                             render(self.payload, advised, after, 'Semantic.Root'))
            self.assertEqual({p.name: p.read_bytes() for p in before.glob('*.thy')},
                             {p.name: p.read_bytes() for p in after.glob('*.thy')})

    def test_rejects_unknown_uninspected_self_duplicate_or_authority_fields(self):
        invalid = [
            {'target_contract_id': 'unknown'}, {'target_contract_id': []},
            {'candidate_contract_ids': ['unknown']}, {'candidate_contract_ids': ['C2']},
            {'candidate_contract_ids': ['C1', 'C1']}, {'candidate_contract_ids': 'C1'},
            {'candidate_premise_ids': ['not-inspected']},
            {'candidate_premise_ids': [self.premise['record_id']] * 2},
            {'id': ''}, {'id': 'has spaces'}, {'direction': '  '},
            {'open_questions': 'question'}, {'open_questions': ['']},
            {'exact_statement': 'lemma changed: "True"'}, {'proof': 'by simp'},
            {'certified': True}, {'provider_theory': 'Original.Secret'},
        ]
        for changes in invalid:
            proposal = deepcopy(self.proposal)
            proposal['proof_direction_suggestions'] = [{**direction(), **changes}]
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                normalize_v6(self.payload, proposal)
        for suggestions in ([direction(), direction()], 'not-a-list', [{}]):
            proposal = {**self.proposal, 'proof_direction_suggestions': suggestions}
            with self.subTest(suggestions=suggestions), self.assertRaises(ValueError):
                normalize_v6(self.payload, proposal)

    def test_owner_blind_ids_decode_in_target_and_candidate_lists(self):
        source, proposal = fixture()
        mapping = {}
        for contract, card in zip(source['contracts'], source['contract_cards']):
            original = contract['contract_id']
            canonical = 'contract:Original.Secret:' + original
            mapping[original] = canonical
            contract.update(contract_id=canonical, owner_theory='Original.Secret')
            card.update(contract_id=canonical, owner_theory='Original.Secret')
        view = short_view(source)
        public = {old: view.to_public[canonical] for old, canonical in mapping.items()}
        for theory in proposal['theories']:
            theory['contract_ids'] = [public[cid] for cid in theory['contract_ids']]
        proposal['collaboration_groups'][0]['contract_ids'] = [public['C1'], public['C2']]
        proposal['proof_direction_suggestions'] = [direction(target=public['C2'], candidates=[public['C1']])]
        view.assert_no_owner_disclosure(str(proposal))
        decoded = view.decode(proposal)
        suggestion = decoded['proof_direction_suggestions'][0]
        self.assertEqual(suggestion['target_contract_id'], mapping['C2'])
        self.assertEqual(suggestion['candidate_contract_ids'], [mapping['C1']])
        artifacts = build_bundle_v6(view.canonical_payload, decoded)
        reference = artifacts['files/Update.json']['proof_direction_contracts'][0]
        self.assertEqual(reference['exact_statement'], source['contracts'][0]['exact_statement'])
        self.assertEqual(reference['provider_theory'], 'Access')
        self.assertNotIn('owner_theory', reference)


if __name__ == '__main__':
    unittest.main()
