"""Document-mode concurrency boundaries; no provider or native process is started."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import patch

from regen.execution.check_executor import CheckExecutor, NativeResources, native_operation
from regen.planning.parallel_file_planner import TheoryController
from regen.tests.current import test_proof_workspace_agent as fixtures


class DocumentConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ProofWorkspaceAgentTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.resources = NativeResources(slots=2, threads=1, resident_limit=2)
        self.executor = CheckExecutor(self.fixture.root / 'document-tasks', workers=2)
        self.addCleanup(self.resources.close)
        self.addCleanup(self.executor.close)
        self.controllers = {}
        for theory in ('Target', 'Source'):
            controller = TheoryController(self.fixture.root, theory, resources=self.resources,
                                          executor=self.executor, document_mode=True)
            self.controllers[theory] = controller
            self.addCleanup(controller.close)
        self.fixture.local_runtime(self.controllers['Target'])

    def observe(self, theory):
        return self.controllers[theory].handle(
            'proof_action', {'theory': theory, 'proposal': {'action': 'observe',
                'reads': [{'action': 'read_queries', 'query_ids': ['synthetic-query']}] }},
            {'thread_id': 'test', 'turn_id': 'test', 'call_id': 'observe-' + theory,
             'transcript_path': 'synthetic-test-transcript'})

    def test_prefix_failure_in_document_mode_is_not_candidate_feedback(self):
        from regen.execution.check_tasks import NativePreparationError
        controller = self.controllers['Target']
        metadata = {'thread_id':'test','turn_id':'test','call_id':'edit-prefix-failure',
                    'transcript_path':'synthetic-test-transcript'}
        draft = controller.handle('read_theory', {'theory':'Target'}, {})['document']
        saved = controller.handle('proof_action', {'theory':'Target','proposal':{
            'action':'edit_theory','base_sha256':draft['source_sha256'],
            'edits':[{'old':'oops','new':'by simp'}]}}, metadata)
        metadata = {**metadata,'call_id':'check-prefix-failure'}
        with patch('regen.planning.theory_document.check_document', return_value={
                'check':{'verdict':'infrastructure_error','failure_scope':'native_preparation',
                         'diagnostic':'prefix deadline'}}):
            with self.assertRaisesRegex(NativePreparationError, 'prefix deadline'):
                controller.handle('proof_action', {'theory':'Target','proposal':{
                    'action':'check_theory','source_sha256':saved['source_sha256']}}, metadata)
        self.assertEqual(controller._state('Target')[1]['proofs'], {})
        row = self.executor.get(next(iter(controller.task_ids)))
        self.assertEqual(row['error_type'], 'NativePreparationError')

    def test_same_theory_observation_blocks_document_reads_and_edits(self):
        controller = self.controllers['Target']
        draft = controller.handle('read_theory', {'theory': 'Target'}, {})['document']
        entered, release = threading.Event(), threading.Event()
        read_attempted, edit_attempted = threading.Event(), threading.Event()

        def observation(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test observation release timed out')
            return {'status': 'available', 'observations': []}

        def read():
            read_attempted.set()
            return controller.handle('read_theory', {'theory': 'Target'}, {})

        def edit():
            edit_attempted.set()
            return controller.handle('proof_action', {'theory': 'Target', 'proposal': {
                'action': 'edit_theory', 'base_sha256': draft['source_sha256'],
                'edits': [{'old': 'oops', 'new': 'by simp'}]}},
                {'thread_id': 'test', 'turn_id': 'test', 'call_id': 'edit',
                 'transcript_path': 'synthetic-test-transcript'})

        with patch.object(controller, '_observe', side_effect=observation), ThreadPoolExecutor(3) as callers:
            first = callers.submit(self.observe, 'Target')
            try:
                self.assertTrue(entered.wait(3))
                read_result = callers.submit(read)
                edit_result = callers.submit(edit)
                self.assertTrue(read_attempted.wait(3))
                self.assertTrue(edit_attempted.wait(3))
                self.assertFalse(first.done())
                self.assertFalse(read_result.done())
                self.assertFalse(edit_result.done())
                self.assertEqual(len(controller.task_ids), 1)
            finally:
                release.set()
            self.assertEqual(first.result(5)['status'], 'available')
            self.assertIn('document', read_result.result(5))
            self.assertEqual(edit_result.result(5)['status'], 'draft_saved')
        self.fixture.llm.assert_not_called()

    def test_distinct_document_theories_execute_tools_concurrently(self):
        rendezvous = threading.Barrier(2)

        def observation(*args):
            rendezvous.wait(timeout=3)
            return {'status': 'available', 'observations': []}

        with patch.object(self.controllers['Target'], '_observe', side_effect=observation), \
                patch.object(self.controllers['Source'], '_observe', side_effect=observation), \
                ThreadPoolExecutor(2) as callers:
            futures = [callers.submit(self.observe, theory) for theory in self.controllers]
            for future in futures:
                self.assertEqual(future.result(5)['status'], 'available')
        self.fixture.llm.assert_not_called()

    def test_one_native_slot_queues_other_theory_without_disabling_agents(self):
        resources = NativeResources(slots=1, threads=1, resident_limit=2)
        self.addCleanup(resources.close)
        for controller in self.controllers.values():
            controller.resources = resources
        first_entered, second_entered = threading.Event(), threading.Event()
        release, queued = threading.Event(), threading.Event()

        class SyntheticNativeCheck:
            def __init__(self, is_first):
                self.is_first = is_first

            def close_prefix(self):
                pass

            @native_operation
            def check(self):
                if self.is_first:
                    first_entered.set()
                    if not release.wait(5):
                        raise RuntimeError('test native release timed out')
                else:
                    second_entered.set()
                return {'status': 'available', 'observations': []}

        first, second = SyntheticNativeCheck(True), SyntheticNativeCheck(False)
        original_wait = resources.condition.wait

        def resource_wait(*args, **kwargs):
            queued.set()
            return original_wait(*args, **kwargs)

        with patch.object(resources.condition, 'wait', side_effect=resource_wait), \
                patch.object(self.controllers['Target'], '_observe', side_effect=lambda *args: first.check()), \
                patch.object(self.controllers['Source'], '_observe', side_effect=lambda *args: second.check()), \
                ThreadPoolExecutor(2) as callers:
            first_result = callers.submit(self.observe, 'Target')
            try:
                self.assertTrue(first_entered.wait(3))
                second_result = callers.submit(self.observe, 'Source')
                self.assertTrue(queued.wait(3))
                self.assertFalse(second_entered.is_set())
                self.assertFalse(second_result.done())
                self.assertEqual(resources.peak_active, 1)
            finally:
                release.set()
            self.assertEqual(first_result.result(5)['status'], 'available')
            self.assertEqual(second_result.result(5)['status'], 'available')
        self.assertTrue(second_entered.is_set())
        self.assertEqual(resources.peak_active, 1)
        self.fixture.llm.assert_not_called()


if __name__ == '__main__':
    unittest.main()
