"""Session abort bridges to real executor cancellation; no native/model calls."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import patch

from regen.execution.check_executor import CheckExecutor, NativeResources
from regen.execution.check_tasks import CheckCancelled
from regen.planning.parallel_file_planner import TheoryController
from regen.planning.proof_workspace_agent import WorkspaceController
from regen.tests.current import test_proof_workspace_agent as fixtures


class WorkspaceProtocolCancellationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ProofWorkspaceAgentTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.resources = NativeResources(2, 1, 2)
        self.executor = CheckExecutor(self.fixture.root/'protocol-tasks', workers=3)
        self.addCleanup(self.resources.close)
        self.addCleanup(self.executor.close)
        self.controller = TheoryController(self.fixture.root, 'Target',
            resources=self.resources, executor=self.executor)
        self.addCleanup(self.controller.close)
        self.fixture.local_runtime(self.controller)
        self.arguments = {'theory':'Target', 'kind':'proof_batch',
            'proposal':{'action':'submit_batch', 'proofs':{'target_contract':'by simp'}}}
        self.metadata = {'thread_id':'thread', 'turn_id':'turn', 'call_id':'one',
                         'transcript_path':'synthetic-protocol-fixture'}

    def event(self):
        event = threading.Event()
        self.addCleanup(event.set)
        return event

    def call(self):
        return self.controller.handle('submit_check', self.arguments, self.metadata)

    def test_abort_blocks_new_dispatch_but_receipts_remain_readable(self):
        self.controller.abort_pending()
        self.controller.abort_pending()
        with self.assertRaisesRegex(CheckCancelled, 'session aborted'):
            self.call()
        self.assertEqual(self.executor.records, {})

    def test_abort_before_executor_registration_prevents_native_dispatch(self):
        entering, release = self.event(), self.event()
        submit = self.executor.submit

        def delayed_submit(**arguments):
            entering.set()
            self.assertTrue(release.wait(5))
            return submit(**arguments)

        with patch.object(self.executor, 'submit', side_effect=delayed_submit), patch(
                'regen.execution.proof_batches.check_batch') as native, ThreadPoolExecutor(1) as callers:
            future = callers.submit(self.call)
            try:
                self.assertTrue(entering.wait(5))
                self.assertEqual(self.executor.records, {})
                self.controller.abort_pending()
            finally:
                release.set()
            row = future.result(timeout=5)
        self.assertEqual(row['status'], 'cancelled', row)
        native.assert_not_called()
        self.assertEqual(self.controller._state('Target')[1]['proofs'], {})

    def test_abort_after_executor_registration_before_controller_id_cancels_late_green(self):
        registered, native_entered = self.event(), self.event()
        release_submit, release_native = self.event(), self.event()
        submit = self.executor.submit
        from regen.execution.proof_batches import check_batch

        def delayed_submit(**arguments):
            result = submit(**arguments)
            registered.set()
            self.assertTrue(release_submit.wait(5))
            return result

        def delayed_native(*arguments, **keywords):
            native_entered.set()
            self.assertTrue(release_native.wait(5))
            return check_batch(*arguments, **keywords)

        with patch.object(self.executor, 'submit', side_effect=delayed_submit), patch(
                'regen.execution.proof_batches.check_batch', side_effect=delayed_native), ThreadPoolExecutor(1) as callers:
            future = callers.submit(self.call)
            try:
                self.assertTrue(registered.wait(5))
                self.assertTrue(native_entered.wait(5))
                self.assertEqual(self.controller.task_ids, set())
                # handle() still holds _tool_lock. abort must not wait for it.
                self.controller.abort_pending()
                task = next(iter(self.executor.records))
                self.assertTrue(self.executor.tokens[task].event.is_set())
            finally:
                release_submit.set()
                release_native.set()
            row = future.result(timeout=5)
        self.assertEqual(row['status'], 'cancelled', row)
        state = self.controller._state('Target')[1]
        self.assertEqual(state['proofs'], {})
        self.assertEqual(state['batch_requests'], 1, 'charged request must survive cancellation')
        receipt = self.controller.handle('get_check_result', {'task_id':row['task_id']}, {})
        self.assertEqual(receipt['status'], 'cancelled')

    def test_abort_does_not_cancel_peer_theory_or_foreign_run(self):
        release = self.event()
        entries = []
        for theory, run_id in [('Source', self.controller.run['run_id']), ('Target', 'other-run')]:
            entered = self.event()

            def work(directory, entered=entered):
                entered.set()
                self.assertTrue(release.wait(5))
                return {'completed':True}

            row = self.executor.submit(kind='symbol', theory=theory,
                binding={'run_id':run_id}, request={}, execute=work)
            self.assertTrue(entered.wait(5))
            entries.append(row['task_id'])
        try:
            self.controller.abort_pending()
            for task in entries:
                self.assertFalse(self.executor.tokens[task].event.is_set())
        finally:
            release.set()
        for task in entries:
            self.assertEqual(self.executor.wait([task], timeout=5)[0]['status'], 'completed')

    def test_abort_preserves_commit_accepted_before_response_and_task_receipt(self):
        accepted, release = self.event(), self.event()
        action = WorkspaceController.proof_action

        def after_acceptance(controller, *arguments, **keywords):
            result = action(controller, *arguments, **keywords)
            accepted.set()
            self.assertTrue(release.wait(5))
            return result

        with patch.object(WorkspaceController, 'proof_action', new=after_acceptance), ThreadPoolExecutor(1) as callers:
            future = callers.submit(self.call)
            try:
                self.assertTrue(accepted.wait(5))
                task = next(iter(self.executor.records))
                self.assertTrue(self.executor.tokens[task].published)
                self.controller.abort_pending()
                self.assertFalse(self.executor.tokens[task].event.is_set())
            finally:
                release.set()
            row = future.result(timeout=5)
        self.assertEqual(row['status'], 'completed', row)
        state = self.controller._state('Target')[1]
        self.assertEqual(state['proofs'], {'target_contract':'by simp'})
        self.assertEqual(state['batch_requests'], 1)
        receipt = self.controller.handle('get_check_result', {'task_id':row['task_id']}, {})
        self.assertEqual(receipt['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
