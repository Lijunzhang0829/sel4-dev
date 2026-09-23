"""Repository planning separates physical ownership, advice and hard imports."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from regen.planning.import_contract import (
    build_bundle_v5, build_bundle_v6, import_edges, normalize_v6,
    prompt_v6, required_imports, safe_repartition,
)
from regen.planning.theory_plan import build_bundle
from regen.planning.workspace_harness import diagnostics, short_view
from regen.planning.workspace_planner import instruction
from regen.planning.workspace_preflight import render
from regen.preparation.refine_campaign import sha256_json


def fixture():
    contracts = [{
        'contract_id': f'C{i}', 'name': f'operation_{i}', 'kind': 'lemma',
        'exact_statement': f'lemma operation_{i}: "P {i}"',
        'contract_owned_context': ['context begin'],
        'contract_owned_attributes': ['simp'] if i == 1 else [],
    } for i in range(1, 4)]
    payload = {
        'schema_version': 1, 'artifact_type': 'repository-planning-input',
        'benchmark': {'id': 'synthetic', 'target_session': 'Refine', 'cross_experiment_inputs': []},
        'planning_input_id': 'synthetic-input', 'contract_visibility': 'owner-blind-v1',
        'contracts': contracts,
        'contract_cards': [{**{k: c[k] for k in ('contract_id', 'name')},
                            'principal_symbols': ['P'], 'logical_shape': 'atomic'} for c in contracts],
        'contract_relation_candidates': [],
        'context_profiles': [{'context_profile_id': 'Context', 'generated_import': 'Semantic.Root'}],
        'parent_premises': {'database_sha256': 'frozen-parent'},
        'planner_interfaces': {},
    }
    def theory(name, ids):
        return {'name': name, 'purpose': 'Related semantic operations.',
            'grouping_reason': 'Joint exploration may expose a common invariant.',
            'contract_ids': ids, 'context_profile_ids': ['Context'],
            'helper_decisions': [], 'required_imports': []}
    proposal = {'schema_version': 6, 'ordering_rationale': 'Independent operations first.',
        'contract_relations': [], 'unresolved_retrieval_questions': [],
        'collaboration_groups': [{'id': 'OperationFamily', 'contract_ids': ['C1', 'C2'],
            'reason': 'Explore shared state access semantics.'}],
        'reuse_suggestions': [{'provider': 'Access', 'consumer': 'Update', 'reason': 'Potential common invariant.'},
                             {'provider': 'Update', 'consumer': 'Access', 'reason': 'Alternative result reuse.'}],
        'theories': [theory('Access', ['C1']), theory('Update', ['C2', 'C3'])]}
    return payload, proposal


class CollaborationPlanTest(unittest.TestCase):
    def setUp(self):
        self.payload, self.proposal = fixture()

    def test_reciprocal_advice_creates_no_imports_or_readiness_edges(self):
        artifacts = build_bundle(self.payload, self.proposal)
        plan, listing = artifacts['repository-plan.json'], artifacts['file-generation-list.json']
        self.assertEqual((plan['schema_version'], listing['schema_version']), (6, 7))
        self.assertEqual(import_edges(plan), [])
        self.assertEqual(plan['theory_order'], ['Access', 'Update'])
        self.assertEqual(plan['collaboration_groups'][0]['member_theories'], ['Access', 'Update'])
        self.assertFalse(plan['semantics']['collaboration_grants_premise_visibility'])
        for entry in listing['files']:
            p = artifacts[entry['projection']]
            self.assertEqual(required_imports(p), [])
            self.assertEqual(p['required_providers'], [])
            self.assertEqual(entry['provider_theories'], [])
            self.assertEqual(len(p['reuse_suggestions']), 2)
            self.assertEqual(p['schema_version'], 6)
            self.assertEqual(entry['projection_sha256'], sha256_json(p))
            self.assertEqual(p['plan_id'], plan['plan_id'])

    def test_hard_edges_override_priority_and_native_render_ignores_advice(self):
        self.proposal['theories'][0]['required_imports'] = [{'theory': 'Update', 'reason': 'Required context.'}]
        artifacts = build_bundle_v6(self.payload, self.proposal)
        self.assertEqual(artifacts['repository-plan.json']['theory_order'], ['Update', 'Access'])
        self.assertEqual(artifacts['repository-plan.json']['priority_order'], ['Access', 'Update'])
        with tempfile.TemporaryDirectory() as tmp:
            render(self.payload, self.proposal, tmp, 'Semantic.Root')
            self.assertIn('imports "Semantic.Root" "Update"', (Path(tmp)/'Access.thy').read_text())
            self.assertIn('imports "Semantic.Root"\nbegin', (Path(tmp)/'Update.thy').read_text())

    def test_hard_cycle_rejected_despite_valid_advisory_cycle(self):
        for theory, provider in zip(self.proposal['theories'], ['Update', 'Access']):
            theory['required_imports'] = [{'theory': provider, 'reason': 'Required.'}]
        with self.assertRaisesRegex(ValueError, 'cyclic'):
            build_bundle_v6(self.payload, self.proposal)
        self.assertTrue(diagnostics(self.payload, self.proposal))

    def test_exact_once_and_name_collisions_remain_mandatory(self):
        for ids in (['C1', 'C2', 'C3'], ['C2'], ['C2', 'unknown']):
            p = deepcopy(self.proposal)
            p['theories'][1]['contract_ids'] = ids
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                build_bundle_v6(self.payload, p)
        self.payload['contracts'][2]['name'] = self.payload['contracts'][1]['name']
        with self.assertRaisesRegex(ValueError, 'name collision'):
            build_bundle_v6(self.payload, self.proposal)

    def test_contract_context_attributes_preserved_without_mutating_inputs(self):
        before = deepcopy((self.payload, self.proposal))
        artifacts = build_bundle_v6(self.payload, self.proposal)
        contracts = [c for t in artifacts['repository-plan.json']['theories'] for c in t['contracts']]
        self.assertEqual(contracts, self.payload['contracts'])
        self.assertEqual(artifacts['files/Access.json']['context_profiles'], self.payload['context_profiles'])
        artifacts['files/Access.json']['contracts'][0]['contract_owned_context'].append('changed output')
        self.assertEqual((self.payload, self.proposal), before)
        self.proposal['theories'][0]['context_profile_ids'] = ['unknown']
        with self.assertRaisesRegex(ValueError, 'context profile'):
            build_bundle_v6(self.payload, self.proposal)

    def test_advisory_endpoints_are_validated_without_disallowing_overlap(self):
        self.proposal['collaboration_groups'].append({'id': 'OtherFamily', 'contract_ids': ['C2', 'C3'], 'reason': 'Another shared operation.'})
        self.assertEqual(len(normalize_v6(self.payload, self.proposal)['collaboration_groups']), 2)
        cases = [
            ('collaboration_groups', [{'id': 'Bad', 'contract_ids': ['C1', 'unknown'], 'reason': 'x'}]),
            ('collaboration_groups', [{'id': 'Bad', 'contract_ids': ['C1', 'C1'], 'reason': 'x'}]),
            ('reuse_suggestions', [{'provider': 'Missing', 'consumer': 'Access', 'reason': 'x'}]),
            ('reuse_suggestions', [{'provider': 'Access', 'consumer': 'Access', 'reason': 'x'}]),
            ('reuse_suggestions', self.proposal['reuse_suggestions'] * 2),
        ]
        for field, value in cases:
            p = deepcopy(self.proposal); p[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                build_bundle_v6(self.payload, p)
            self.assertTrue(diagnostics(self.payload, p))

    def test_schema_rejects_hidden_proof_routes_and_mixed_import_names(self):
        for target, field, value in [
            ('group', 'exact_statement', 'lemma forced: "True"'),
            ('theory', 'proof_route', 'prescribed tactic'),
            ('theory', 'suggested_predecessors', []),
            ('top', 'proofs', {}),
        ]:
            p = deepcopy(self.proposal)
            obj = p['collaboration_groups'][0] if target == 'group' else p['theories'][0] if target == 'theory' else p
            obj[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                build_bundle_v6(self.payload, p)

    def test_legacy_v4_and_v5_keep_existing_import_semantics(self):
        p = deepcopy(self.proposal)
        p.pop('collaboration_groups'); p.pop('reuse_suggestions'); p['schema_version'] = 5
        p['theories'][0]['required_imports'] = [{'theory': 'Update', 'reason': 'Required.'}]
        self.assertEqual(build_bundle(self.payload, p), build_bundle_v5(self.payload, p))
        self.assertEqual(build_bundle(self.payload, p)['file-generation-list.json']['schema_version'], 6)
        p['schema_version'] = 4
        for t in p['theories']: t['suggested_predecessors'] = t.pop('required_imports')
        legacy = build_bundle(self.payload, p)
        self.assertEqual(legacy['repository-plan.json']['schema_version'], 4)
        self.assertEqual(legacy['repository-plan.json']['theory_order'], ['Update', 'Access'])
        self.assertNotIn('collaboration_groups', legacy['repository-plan.json'])

    def test_valid_v6_diagnostics_and_current_import_paths(self):
        self.assertEqual(diagnostics(self.payload, self.proposal), [])
        self.proposal['theories'][0]['required_imports'] = [{'theory': 'Missing', 'reason': 'Required.'}]
        self.assertIn('/theories/0/required_imports/0/theory',
                      [e['path'] for e in diagnostics(self.payload, self.proposal)])

    def test_repartition_is_fresh_pure_and_can_remove_old_hard_edges(self):
        original = deepcopy(self.proposal)
        original['theories'][0]['required_imports'] = [{'theory': 'Update', 'reason': 'Old commitment.'}]
        old = build_bundle_v6(self.payload, original)['repository-plan.json']
        old.update(revision=9, run_id='old-run', green={'C1': 'old-proof'})
        before = deepcopy((self.payload, old, self.proposal))
        result = safe_repartition(self.payload, old, self.proposal)
        new = result['repository-plan.json']
        self.assertNotEqual(new['plan_id'], old['plan_id'])
        self.assertEqual(new['required_import_edges'], [])
        self.assertTrue(new['repartition_source']['requires_fresh_run'])
        self.assertFalse(new['repartition_source']['proof_state_migrated'])
        for key in ('revision', 'run_id', 'green'):
            self.assertNotIn(key, new)
        self.assertEqual((self.payload, old, self.proposal), before)
        self.assertEqual(result, safe_repartition(self.payload, old, self.proposal))

    def test_repartition_rejects_changed_fixed_context_or_semantic_input(self):
        old = build_bundle_v6(self.payload, self.proposal)['repository-plan.json']
        for location in ('context', 'statement', 'identity', 'benchmark', 'parent', 'profile'):
            p = deepcopy(self.payload)
            if location == 'context': p['contracts'][0]['contract_owned_context'] = []
            elif location == 'statement': p['contracts'][0]['exact_statement'] = 'lemma wrong: "True"'
            elif location == 'identity': p['planning_input_id'] = 'other'
            elif location == 'benchmark': p['benchmark']['id'] = 'other'
            elif location == 'parent': p['parent_premises']['database_sha256'] = 'other'
            else: p['context_profiles'][0]['generated_import'] = 'Other.Root'
            with self.subTest(location=location), self.assertRaises(ValueError):
                safe_repartition(p, old, self.proposal)

    def test_policy_explicitly_selects_new_schema_without_changing_legacy_default(self):
        from regen.planning.repository_planner import validate_policy
        policy = json.loads(Path('regen/config/repository-planning-policy-workspace.json').read_text())
        self.assertEqual(policy['proposal_schema_version'], 6)
        validate_policy(policy)
        for invalid in (True, 4, 7, '6'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_policy({**policy, 'proposal_schema_version': invalid})
        policy.pop('proposal_schema_version')
        validate_policy(policy)
        self.assertIn('"schema_version": 5', instruction(self.payload, 5))
        self.assertIn('"schema_version": 6', instruction(self.payload, 6))

    def test_owner_blind_prompt_and_semantic_grouping(self):
        source = deepcopy(self.payload)
        for c, card in zip(source['contracts'], source['contract_cards']):
            c['owner_theory'] = 'Refine.Secret_R'
            old_id = c['contract_id']
            c['contract_id'] = f'contract:Refine.Secret_R:{old_id}'
            card['contract_id'] = c['contract_id']; card['owner_theory'] = c['owner_theory']
        view = short_view(source)
        for text in (prompt_v6(view.payload, []), instruction(view.payload)):
            view.assert_no_owner_disclosure(text)
            self.assertIn('shared semantic operations', text)
            self.assertIn('collaboration_groups', text)
            self.assertIn('reuse_suggestions', text)
            self.assertIn('Only actual required_imports must be acyclic', text)
            self.assertNotIn('setObject_other_corres', text)
            self.assertNotIn('Refine_HeapObjectAccess', text)


if __name__ == '__main__':
    unittest.main()
