"""Public advice handoff with real journals and synthetic, non-native checking."""
from copy import deepcopy
import unittest

from regen.execution.check_executor import CheckExecutor, NativeResources
from regen.execution.proof_commits import durable
from regen.planning.batch_file_planner import planner_input, prepare_prompt
from regen.planning.parallel_file_planner import TheoryController
from regen.tests.current import test_proof_workspace_agent as fixtures


class AdviceControllerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ProofWorkspaceAgentTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.premise = {'record_id': 'base-fact', 'canonical_name': 'Base.fact',
                        'exact_statement': 'True', 'source_class': 'retained_semantic_export'}
        self.direction = {
            'id': 'try-source', 'target_contract_id': 'target_contract',
            'direction': 'Try source_contract if its conclusion helps; otherwise prove directly.',
            'candidate_contract_ids': ['source_contract'],
            'candidate_premise_ids': ['base-fact'], 'open_questions': ['Does it apply?']}
        self.fixture.fresh_target(
            nodes=[], proof_direction_suggestions=[self.direction],
            proof_direction_contracts=[{
                'contract_id': 'source_contract', 'provider_theory': 'Source',
                'exact_statement': 'a stale projection must not override the fixed package'}],
            proof_direction_premises=[self.premise],
            auxiliary_intents=[{'id': 'possible-helper', 'for_contracts': ['target_contract'],
                                'intent': 'Try an elementary simplification.'}])
        self.fixture.run['base_import'] = 'Semantic_Base.Semantic_Root'
        durable(self.fixture.root/'run.json', self.fixture.run)
        self.resources = NativeResources(1, 1, 1)
        self.executor = CheckExecutor(self.fixture.root/'advice-test-tasks', workers=1)
        self.addCleanup(self.resources.close)
        self.addCleanup(self.executor.close)
        self.controller = TheoryController(self.fixture.root, 'Target', resources=self.resources,
            executor=self.executor, document_mode=True)
        self.addCleanup(self.controller.close)
        self.checkers, self.setup_calls = self.fixture.local_runtime(self.controller)

    def read(self, **kwargs):
        return self.controller.handle('read_theory', {'theory': 'Target', **kwargs}, {})

    def entry(self):
        return self.controller.handle('proof_action', {'theory': 'Target', 'proposal': {
            'action': 'read_entry', 'entry_id': 'target_contract'}}, {})

    def test_public_reads_expand_only_target_details_without_acceptance_or_external_runtime(self):
        view = self.read()
        compact = view['planning_advice']['proof_direction_suggestions'][0]
        self.assertEqual(compact['candidate_contracts'][0]['authority_status'], 'pending')
        self.assertNotIn('exact_statement', compact['candidate_contracts'][0])
        self.assertNotIn('exact_statement', compact['candidate_premises'][0]['record'])
        self.assertIn('auxiliary_intents', view['planning_advice']['intents'])
        before = deepcopy(self.controller._state('Target')[1])
        calls = self.checkers['Target'].calls
        result = self.entry()
        self.assertTrue(result['refutation_capability']['can_attempt'])
        expanded = result['planning_advice']['proof_direction_suggestions'][0]
        candidate = expanded['candidate_contracts'][0]
        fixed = self.fixture.packages[0]['contracts'][0]
        self.assertEqual(candidate['exact_statement'], fixed['exact_statement'])
        self.assertEqual(candidate['contract_owned_context'], fixed['contract_owned_context'])
        self.assertFalse(candidate['can_use_as_current_premise'])
        self.assertFalse(candidate['applicability_verified'])
        self.assertEqual(expanded['candidate_premises'][0]['record'], self.premise)
        unchanged = self.read(if_source_sha256=view['document']['source_sha256'])
        self.assertTrue(unchanged['document']['source_unchanged'])
        self.assertEqual(unchanged['planning_advice'], view['planning_advice'])
        self.assertEqual(self.setup_calls, ['Target'])
        self.assertEqual(self.checkers['Target'].calls, calls)
        self.assertEqual(self.controller._state('Target')[1], before)
        self.assertEqual(len(self.executor.records), 0)

    def test_advice_survives_prompt_projection_without_entering_facts_or_graph(self):
        self.read()
        package, state = self.controller._state('Target')
        before = deepcopy(state)
        payload = planner_input(package, state)
        self.assertEqual(payload['planning_advice']['proof_direction_suggestions'], [self.direction])
        self.assertFalse(payload['planning_advice']['availability_checked'])
        self.assertEqual(payload['recalled_facts'], [])
        self.assertNotIn('source_contract', {row['id'] for row in payload['graph']['nodes']})
        projected, prompt = prepare_prompt(payload)
        self.assertEqual(projected['planning_advice'], payload['planning_advice'])
        self.assertIn('try-source', prompt)
        self.assertIn('optional', prompt)
        self.assertEqual(state, before)


if __name__ == '__main__':
    unittest.main()
