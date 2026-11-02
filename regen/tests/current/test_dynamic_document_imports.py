"""Document-tool orchestration; synthetic checker results are not native proof evidence."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from regen.execution.check_executor import CheckExecutor, NativeResources
from regen.execution.proof_commits import durable
from regen.planning.parallel_file_planner import TheoryController
from regen.tests.current import test_proof_workspace_agent as workspace_fixtures


BASE = 'Semantic_Base.Semantic_Root'
SOURCE = 'Generated_Refine.Regen_Source'


class DynamicDocumentImportControllerTests(unittest.TestCase):
    def setUp(self):
        fixture = workspace_fixtures.ProofWorkspaceAgentTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.fresh_target()
        fixture.run['base_import'] = BASE
        durable(fixture.root/'run.json', fixture.run)
        self.fixture = fixture
        self.resources = NativeResources(1, 1, 1)
        self.executor = CheckExecutor(fixture.root/'dynamic-test-tasks', workers=1)
        self.addCleanup(self.resources.close)
        self.addCleanup(self.executor.close)
        self.controller = TheoryController(fixture.root, 'Target', resources=self.resources,
            executor=self.executor, document_mode=True)
        self.addCleanup(self.controller.close)
        self.checkers, self.setup_calls = fixture.local_runtime(self.controller)
        self.serial = 0

    def read(self):
        return self.controller.handle('read_theory', {'theory': 'Target'}, {})

    def action(self, proposal):
        self.serial += 1
        return self.controller.handle('proof_action', {'theory': 'Target', 'proposal': proposal},
            {'thread_id': 'synthetic-thread', 'turn_id': 'synthetic-turn',
             'call_id': str(self.serial), 'transcript_path': 'synthetic-test-transcript',
             'transcript_offset': 1})

    def edit_imports(self, imports):
        draft = self.read()['document']
        return self.action({'action': 'edit_theory', 'base_sha256': draft['source_sha256'],
                            'imports': imports})

    def prove_draft(self, proof='by simp'):
        draft = self.read()['document']
        return self.action({'action': 'edit_theory', 'base_sha256': draft['source_sha256'],
                            'edits': [{'old': '\noops\n', 'new': '\n' + proof + '\n'}]})

    def test_repeated_edits_change_only_draft_until_a_native_check(self):
        original = self.read()['document']
        _, before_state = self.controller._state('Target')
        before_state = deepcopy(before_state)
        checker = self.controller._runtime()[4]
        calls = checker.calls
        tasks = len(self.executor.records)
        with patch('regen.execution.import_revisions.prepare_import_change') as prepare:
            self.edit_imports([SOURCE])
            self.edit_imports([SOURCE, 'Generated_Refine.Regen_Another'])
            self.edit_imports([SOURCE])
            view = self.read()['document']
            self.assertEqual(view['draft_imports'], [SOURCE])
            self.assertEqual(view['checked_imports'], [BASE])
            self.assertTrue(view['imports_pending'])
            self.assertNotEqual(view['source_sha256'], original['source_sha256'])
            prepare.assert_not_called()
        self.assertEqual(checker.calls, calls)
        self.assertEqual(len(self.executor.records), tasks)
        self.assertEqual(checker.imports(), [BASE])
        self.assertEqual(self.controller._state('Target')[1], before_state)

    def test_import_discovery_does_not_treat_unsealed_green_as_available(self):
        # Reading a local draft must not scan all other theory journals.
        original = self.controller._state
        with patch.object(self.controller, '_state', side_effect=lambda name:
                original(name) if name == 'Target' else self.fail('unexpected repository scan')):
            options = self.read()['import_options']
        self.assertEqual(options['base_import'], BASE)
        self.assertEqual(options['provider_directory']['tool'], 'list_workspace')
        overview = self.controller.handle('list_workspace', {}, {})
        source = next(row for row in overview['theories'] if row['theory'] == 'Source')
        self.assertGreater(source['green_count'], 0)
        self.assertEqual(source['import_option']['import_name'], SOURCE)
        self.assertFalse(source['import_option']['exports_available'])
        reference = self.controller.handle('read_theory', {'theory':'Source'}, {})
        self.assertEqual(reference['import_option'], source['import_option'])
        self.assertTrue(reference['reference_only'])

    def test_add_remove_cancellation_does_not_prepare_an_import_revision(self):
        original = self.read()['document']
        with patch('regen.execution.import_revisions.prepare_import_change') as prepare:
            self.edit_imports([SOURCE])
            self.edit_imports([BASE])
            draft = self.read()['document']
            self.assertFalse(draft['imports_pending'])
            self.assertEqual(draft['source'], original['source'])
            saved = self.prove_draft()
            result = self.action({'action': 'check_theory', 'source_sha256': saved['source_sha256']})
            prepare.assert_not_called()
        self.assertEqual(result['feedback']['status'], 'accepted')
        self.assertEqual(self.controller._state('Target')[1]['accepted_order'], ['target_contract'])
        self.assertEqual(self.setup_calls, ['Target'])

    def test_stale_source_cannot_apply_a_newer_drafts_imports(self):
        earlier = self.prove_draft()
        self.edit_imports([SOURCE])
        with patch('regen.execution.import_revisions.prepare_import_change') as prepare:
            result = self.action({'action':'check_theory','source_sha256':earlier['source_sha256']})
            prepare.assert_not_called()
        self.assertEqual(result['status'], 'error')
        self.assertTrue(self.read()['document']['imports_pending'])
        self.assertIsNone(self.controller._runtime()[3].replay(repair=False))

    def test_rejected_import_preparation_preserves_old_authority_and_draft(self):
        saved = self.prove_draft()
        self.action({'action': 'check_theory', 'source_sha256': saved['source_sha256']})
        before = deepcopy(self.controller._state('Target')[1])
        self.edit_imports([SOURCE])
        desired = self.read()['document']
        with patch('regen.execution.import_revisions.prepare_import_change',
                   side_effect=ValueError('import provider has no certified exports: Source')) as prepare:
            try:
                result = self.action({'action': 'check_theory', 'source_sha256': desired['source_sha256']})
            except ValueError as error:
                diagnostic = str(error)
            else:
                diagnostic = json.dumps(result)
            self.assertIn('no certified exports', diagnostic)
            prepare.assert_called_once()
        current = self.controller._state('Target')[1]
        self.assertEqual(current['proofs'], before['proofs'])
        self.assertEqual(current.get('context_revision'), before.get('context_revision'))
        self.assertEqual(self.read()['document']['source'], desired['source'])
        self.assertTrue(self.read()['document']['imports_pending'])

    def test_structural_goal_query_applies_pending_imports_before_observing(self):
        self.edit_imports([SOURCE])
        request = {'goal_query': True, 'context_node_id': 'target_contract',
                   'retrieval_policy': 'indexed-v1', 'structural_rerank': True}
        with patch('regen.execution.import_revisions.prepare_import_change',
                   side_effect=ValueError('synthetic unsealed provider')) as prepare, \
                patch.object(self.controller, '_observe') as observe:
            result = self.action({'action': 'observe',
                                  'reads': [{'action': 'query', 'request': request}]})
            prepare.assert_called_once()
            observe.assert_not_called()
        self.assertEqual(result['status'], 'imports_rejected')
        self.assertIn('synthetic unsealed provider', result['diagnostic'])
        self.assertTrue(self.read()['document']['imports_pending'])
        self.assertEqual(self.controller._state('Target')[1].get('context_revision', 0), 0)

    def test_lexical_goal_query_retains_checked_context_with_pending_imports(self):
        self.edit_imports([SOURCE])
        request = {'goal_query': True, 'context_node_id': 'target_contract',
                   'retrieval_policy': 'indexed-v1'}
        with patch('regen.execution.import_revisions.prepare_import_change') as prepare, \
                patch.object(self.controller, '_observe', return_value={'status': 'available'}) as observe:
            result = self.action({'action': 'query', 'request': request})
            prepare.assert_not_called()
            observe.assert_called_once()
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['context_revision'], 0)
        self.assertTrue(self.read()['document']['imports_pending'])

    def assert_first_import_action_initializes_scaffold(self, action):
        saved = self.prove_draft()
        self.edit_imports([SOURCE])
        draft = self.read()['document']
        runtime = self.controller._runtime()
        self.assertIsNone(runtime[3].replay())
        result = self.action({'action': action, 'source_sha256': draft['source_sha256']})
        # The initial fixed declarations must be checked in the original
        # environment before provider validation, even without an earlier proof
        # check. Source deliberately has no certified exports in this fixture.
        self.assertIn('import provider has no certified exports: Source', json.dumps(result))
        initial = runtime[3].replay()
        self.assertEqual(initial['graph']['declaration_index_version'], 1)
        self.assertEqual(initial['proofs'], {})
        self.assertEqual(initial.get('batch_requests', 0), 0)
        self.assertEqual(initial.get('context_revision', 0), 0)
        self.assertEqual(initial['status'], 'planned')
        self.assertEqual(self.read()['document']['source_sha256'], draft['source_sha256'])
        self.assertTrue(self.read()['document']['imports_pending'])
        self.assertNotEqual(saved['source_sha256'], draft['source_sha256'])
        self.fixture.llm.assert_not_called()

    def test_first_check_with_draft_imports_initializes_old_scaffold(self):
        self.assert_first_import_action_initializes_scaffold('check_theory')

    def test_first_finish_with_draft_imports_initializes_old_scaffold(self):
        with patch('regen.planning.batch_file_planner.seal_theory') as seal:
            self.assert_first_import_action_initializes_scaffold('finish')
            seal.assert_not_called()

    def test_finish_checks_complete_draft_then_reports_seal_failure_to_planner(self):
        saved = self.prove_draft()
        self.assertFalse(saved['checked'])
        self.assertEqual(self.controller._state('Target')[1]['proofs'], {})
        with patch('regen.planning.batch_file_planner.seal_theory', return_value=(None, {
                'stage': 'complete', 'check': {'verdict': 'red'},
                'diagnostic': 'synthetic complete-check failure'})) as seal:
            result = self.action({'action': 'finish', 'source_sha256': saved['source_sha256']})
            seal.assert_called_once()
        self.assertEqual(result['feedback']['status'], 'seal_failed')
        self.assertIn('synthetic complete-check failure', result['feedback']['diagnostic'])
        self.assertEqual(self.controller._state('Target')[1]['accepted_order'], ['target_contract'])
        self.assertFalse(result['all_selected_sealed'])
        self.assertEqual(self.read()['document']['source_sha256'], saved['source_sha256'])
        self.fixture.llm.assert_not_called()

    def test_finish_failed_candidate_check_never_attempts_seal(self):
        saved = self.prove_draft('by fail')
        checker = self.controller._runtime()[4]
        native = checker._check
        def proof_only_failure(source, label, **kwargs):
            checker.verdict = 'red' if 'by fail' in source else 'green'
            return native(source, label, **kwargs)
        checker._check = proof_only_failure
        with patch('regen.planning.batch_file_planner.seal_theory') as seal:
            result = self.action({'action': 'finish', 'source_sha256': saved['source_sha256']})
            seal.assert_not_called()
        self.assertEqual(result['feedback']['status'], 'rejected_batch')
        self.assertFalse(self.controller._state('Target')[1]['proofs'])
        self.assertFalse(result['all_selected_sealed'])
        self.assertIn('by fail', self.read()['document']['source'])


if __name__ == '__main__':
    unittest.main()
