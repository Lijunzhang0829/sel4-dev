"""Preparation -> repository plan -> file projections, with external calls stubbed."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

from regen.tests.current import test_environment_pipeline as fixtures
from regen.preparation import environment_pipeline
from regen.preparation.environment_gate import read_json, sha256_path
from regen.preparation.refine_campaign import sha256_json
from regen.planning import repository_planner as planner


class RepositoryPlannerTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.EnvironmentPipelineTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo, self.root = self.fixture.repo, self.fixture.root
        shutil.rmtree(self.root)
        self.policy = read_json(getattr(self, 'policy_file', Path(__file__).parents[2] / 'fixtures/repository-planning-policy-v2.json'))
        policy_path = self.repo / 'regen/config/repository-planning-policy.json'
        policy_path.parent.mkdir(parents=True)
        policy_path.write_text(json.dumps(self.policy))
        self.policy_hash = sha256_path(policy_path)
        config = read_json(self.fixture.config)
        config['frozen_input_files'].append('regen/config/repository-planning-policy.json')
        config['budgets'] = {'repository_planner_context_chars': 20000, 'model_call_timeout_seconds': 5}
        self.fixture.config.write_text(json.dumps(config))
        theory = self.fixture.l4v / 'proof/refine/ARM/Demo_R.thy'
        theory.write_text(theory.read_text().replace('\nend\n', '''
lemma write_corres:
  "corres (=) P Q h k"
  by simp

end
'''))
        environment_pipeline.initialize(self.root, self.repo, self.fixture.config)
        self.fixture.execute()
        self.gate = self.root / environment_pipeline.GATE
        self.index = self.root / environment_pipeline.INDEX
        self.output = self.root / 'Repository-Planning'
        self.payload = planner.make_input(self.root, self.gate, self.index, self.policy, self.policy_hash, 20000)
        first, second = [item['contract_id'] for item in self.payload['contracts']]
        premise = self.payload['legal_premises'][0]['record_id']
        self.proposal = {'schema_version': 2, 'theories': [
            {'name': 'Generated_Bridges', 'purpose': 'Shared auxiliary facts', 'nodes': [
                {'id': 'small_bridge', 'intent': 'Establish an intermediate fact', 'for_contracts': [first, second],
                 'suggested_dependencies': [], 'premise_ids': [premise]},
                {'id': 'large_bridge', 'intent': 'Compose the intermediate fact', 'for_contracts': [first],
                 'suggested_dependencies': ['small_bridge'], 'premise_ids': []}]},
            {'name': 'Generated_Read', 'purpose': 'First contract', 'nodes': [
                {'id': 'read_goal', 'contract_id': first, 'suggested_dependencies': ['large_bridge'], 'premise_ids': []}]},
            {'name': 'Generated_Write', 'purpose': 'Second contract', 'nodes': [
                {'id': 'write_goal', 'contract_id': second, 'suggested_dependencies': ['small_bridge'], 'premise_ids': []}]},
        ]}

    def call(self, responses=None, *, verify_only=False, allow=False):
        responses = list(responses or [])
        def provider(argv, **kwargs):
            if not responses:
                raise AssertionError('unexpected external provider call')
            final = json.dumps(responses.pop(0))
            Path(argv[argv.index('-o') + 1]).write_text(final)
            kwargs['raw_path'].write_text(json.dumps({
                'type': 'item.completed', 'item': {'type': 'agent_message', 'text': final}}) + '\n')
            kwargs['stderr_path'].write_text('')
            return {'returncode':0}
        # Only substitute the process invocation; keep actual transcript creation,
        # hash checks, no-tool audit, selection and reconstruction code.
        with patch.dict('os.environ', {'REGEN_CODEX_BIN': sys.executable}), \
             patch('regen.infrastructure.audited_llm._run_provider', side_effect=provider) as external:
            result = planner.run(self.root, self.gate, self.index, self.policy, self.policy_hash,
                                 self.output, allow_external_call=allow, verify_only=verify_only)
            return result, external.call_count

    def test_upstream_handoff_free_grouping_and_nonleaf_auxiliaries(self):
        self.assertEqual(self.payload['environment_dataflow_id'], read_json(self.index)['dataflow_id'])
        self.assertEqual(len(self.payload['contracts']), 2)
        self.assertEqual(len(self.payload['original_owner_groups']), 1)
        self.assertTrue(self.payload['legal_premises'])
        self.assertNotIn('human_secret_helper', json.dumps(self.payload))
        result, calls = self.call([self.proposal], allow=True)
        self.assertEqual((result['files'], result['contracts'], result['auxiliary_nodes']), (3, 2, 2))
        self.assertEqual(calls, 1)
        self.assertFalse(result['execution_ready'])
        plan = read_json(self.output / 'repository-plan.json')
        self.assertFalse(plan['semantics']['node_statements_certified'])
        bridge = read_json(self.output / 'files/Generated_Bridges.json')
        self.assertEqual(bridge['contracts'], [])
        self.assertEqual(bridge['nodes'][1]['suggested_dependencies'], ['small_bridge'])
        fixed = read_json(self.output / 'files/Generated_Read.json')['contracts'][0]
        self.assertEqual(fixed, self.payload['contracts'][0])
        self.assertEqual(self.call(verify_only=True)[1], 0)
        with self.assertRaisesRegex(ValueError, 'file-projection consumer'):
            from regen.legacy.session_planner import validate_file_list
            validate_file_list(read_json(self.output / 'file-generation-list.json'), read_json(self.gate))

    def test_downstream_loader_verifies_both_preparation_and_plan(self):
        self.call([self.proposal], allow=True)
        projection = planner.load_file_projection(self.root, self.output, 'Generated_Read')
        self.assertEqual(projection['contracts'], [self.payload['contracts'][0]])
        with self.assertRaisesRegex(ValueError, 'unknown generated theory'):
            planner.load_file_projection(self.root, self.output, '../unknown')
        heap = self.root / 'Semantic-Base/heaps/Semantic_Base'
        heap.write_text('changed upstream heap')
        with self.assertRaisesRegex(ValueError, 'runtime directory changed'):
            planner.load_file_projection(self.root, self.output, 'Generated_Read')

    def test_premise_budget_omissions_are_explicit_and_deterministic(self):
        from regen.planning.repository_planner import read_stream
        actual = read_stream
        def large_stream(root, reference):
            records = actual(root, reference)
            if reference['path'].endswith('premises.jsonl'):
                return [{**records[0], 'record_id': f'premise-{i:04d}',
                         'exact_statement': 'demo ' * 1000} for i in range(20)]
            return records
        with patch.object(planner, 'read_stream', side_effect=large_stream):
            payload = planner.make_input(self.root, self.gate, self.index, self.policy, self.policy_hash, 20000)
            again = planner.make_input(self.root, self.gate, self.index, self.policy, self.policy_hash, 20000)
        self.assertEqual(payload, again)
        self.assertEqual(payload['contracts'], self.payload['contracts'])
        self.assertGreater(payload['premise_selection']['omitted_count'], 0)
        self.assertFalse(payload['premise_selection']['complete'])
        self.assertLessEqual(len(planner.prompt(payload)), 20000)

    def test_recover_missing_projections_from_audited_transcript(self):
        self.call([self.proposal], allow=True)
        (self.output / 'files/Generated_Read.json').unlink()
        (self.output / 'planner-input.json').unlink()
        with self.assertRaisesRegex(ValueError, 'missing planning artifact'):
            self.call(verify_only=True)
        result, calls = self.call()
        self.assertEqual(calls, 0)
        self.assertEqual(result['contracts'], 2)
        self.assertTrue((self.output / 'files/Generated_Read.json').exists())

    def test_invalid_proposal_feedback_and_partial_publication_recovery(self):
        bad = copy.deepcopy(self.proposal)
        bad['theories'].pop()
        result, calls = self.call([bad, self.proposal], allow=True)
        self.assertEqual(calls, 2)
        self.assertEqual(result['contracts'], 2)
        self.assertIn('exactly once', read_json(self.output / 'attempt-001-rejection.json')['diagnostic'])
        (self.output / 'repository-planning-audit.json').unlink()
        (self.output / 'files/Generated_Write.json').unlink()
        self.assertEqual(self.call()[1], 0)

    def test_changed_contract_or_duplicate_target_is_rejected(self):
        bad = copy.deepcopy(self.proposal)
        bad['theories'][1]['nodes'][0]['statement'] = 'lemma weakened: True'
        with self.assertRaisesRegex(ValueError, 'statement overrides'):
            planner.build_bundle(self.payload, bad)
        bad = copy.deepcopy(self.proposal)
        bad['theories'][2]['nodes'][0]['contract_id'] = bad['theories'][1]['nodes'][0]['contract_id']
        with self.assertRaisesRegex(ValueError, 'exactly once'):
            planner.build_bundle(self.payload, bad)

    def test_future_or_hidden_premise_is_rejected(self):
        bad = copy.deepcopy(self.proposal)
        bad['theories'][1]['nodes'][0]['premise_ids'] = ['small_bridge']
        with self.assertRaisesRegex(ValueError, 'absent from the legal planner input'):
            planner.build_bundle(self.payload, bad)

    def test_cycles_and_dangling_suggestions_are_rejected(self):
        bad = copy.deepcopy(self.proposal)
        bad['theories'][0]['nodes'][0]['suggested_dependencies'] = ['large_bridge']
        with self.assertRaisesRegex(ValueError, 'cyclic'):
            planner.build_bundle(self.payload, bad)
        bad['theories'][0]['nodes'][0]['suggested_dependencies'] = ['missing']
        with self.assertRaisesRegex(ValueError, 'dangling'):
            planner.build_bundle(self.payload, bad)
        # Acyclic node graph but impossible whole-theory build order.
        bad = copy.deepcopy(self.proposal)
        bad['theories'][0]['nodes'][1]['suggested_dependencies'] = ['write_goal']
        with self.assertRaisesRegex(ValueError, 'cyclic'):
            planner.build_bundle(self.payload, bad)

    def test_tampered_projection_is_not_overwritten(self):
        self.call([self.proposal], allow=True)
        path = self.output / 'files/Generated_Read.json'
        value = read_json(path)
        value['contracts'][0]['exact_statement'] = 'lemma changed: True'
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'does not reconstruct'):
            self.call()
        self.assertEqual(read_json(path), value)

    def test_tampered_dataflow_stops_before_model_call(self):
        path = self.root / 'Environment-Dataflow/contracts.jsonl'
        path.write_text(path.read_text().replace('unproved', 'green'))
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.call([self.proposal], allow=True)
        self.assertFalse(self.output.exists())

    def test_context_budget_never_truncates_fixed_contracts(self):
        with self.assertRaisesRegex(ValueError, 'targets were not truncated'):
            planner.make_input(self.root, self.gate, self.index, self.policy, self.policy_hash, 500)
        self.assertEqual(self.payload, planner.make_input(self.root, self.gate, self.index,
                                                        self.policy, self.policy_hash, 20000))

    def test_policy_substitution_and_path_injection_are_rejected(self):
        bad_policy = copy.deepcopy(self.policy)
        bad_policy['model']['model'] = 'different-model'
        with self.assertRaisesRegex(ValueError, 'frozen input snapshot'):
            planner.run(self.root, self.gate, self.index, bad_policy, self.policy_hash, self.output)
        bad = copy.deepcopy(self.proposal)
        bad['theories'][0]['name'] = '../escape'
        with self.assertRaisesRegex(ValueError, 'invalid or duplicate'):
            planner.build_bundle(self.payload, bad)

    def test_fresh_planning_requires_external_flag(self):
        with self.assertRaisesRegex(ValueError, 'allow-external-call'):
            self.call()

    def test_audit_identity_is_checked_before_recovery(self):
        self.call([self.proposal], allow=True)
        audit = self.output / 'repository-planning-audit.json'
        value = read_json(audit)
        value['plan_id'] = 'forged'
        audit.write_text(json.dumps(value))
        (self.output / 'files/Generated_Read.json').unlink()
        with self.assertRaisesRegex(ValueError, 'audit identity'):
            self.call()
        self.assertFalse((self.output / 'files/Generated_Read.json').exists())
