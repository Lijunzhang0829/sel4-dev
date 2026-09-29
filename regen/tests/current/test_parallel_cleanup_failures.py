"""Shutdown faults must not leave live writers unowned or skip peer cleanup."""
from contextlib import ExitStack, nullcontext
import errno
import hashlib
import os
from pathlib import Path
import signal
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from regen.planning import parallel_file_planner as planner


class ParallelCleanupFailuresTests(unittest.TestCase):
    def test_cleanup_preserves_primary_error_and_attempts_all_actions(self):
        primary = KeyboardInterrupt('stop requested')
        disk_error = OSError(errno.ENOSPC, 'disk full')
        first = Mock(side_effect=disk_error)
        later = Mock(side_effect=RuntimeError('resident close failed'))
        last = Mock()
        planner._cleanup_all([first, later, last], primary=primary)
        last.assert_called_once_with()
        self.assertEqual(len(primary.__notes__), 2)
        self.assertIn('disk full', primary.__notes__[0])
        self.assertIn('resident close failed', primary.__notes__[1])

    def test_cleanup_without_primary_raises_first_error_after_remaining_actions(self):
        first_error = OSError(errno.ENOSPC, 'disk full')
        last = Mock()
        with self.assertRaises(OSError) as caught:
            planner._cleanup_all([Mock(side_effect=first_error), last])
        self.assertIs(caught.exception, first_error)
        last.assert_called_once_with()

    def test_controller_close_attempts_all_cancellations_and_closes_runtime(self):
        controller = planner.TheoryController.__new__(planner.TheoryController)
        controller.task_ids = {'one', 'two'}
        def cancel(task):
            if task == 'one':
                raise OSError(errno.ENOSPC, 'cannot persist cancellation')
        controller.executor = SimpleNamespace(cancel=Mock(side_effect=cancel),
            get=Mock(return_value={'status': 'cancelled'}), wait=Mock())
        with patch.object(planner.WorkspaceController, 'close') as close_runtime:
            with self.assertRaises(OSError):
                controller.close()
        self.assertEqual(controller.executor.cancel.call_count, 2)
        close_runtime.assert_called_once_with()

    def _run_interrupted(self, *, signum=signal.SIGTERM, failed_join=False):
        order = []
        originals = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP)}
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary)
            binary = root/'codex'
            binary.write_text('synthetic executable identity only')
            started = threading.Event()
            worker_exited = threading.Event()
            class Overview:
                selected = ['Target']
                run = {'run_id': 'cleanup-test', 'execution_input_kind': 'refine-dataset',
                       'checker_context_root': str(root), 'checker_context_id': 'fixture'}
                options = {'model': 'fixture', 'call_timeout': 1,
                           'check_timeout': 1, 'candidate_timeout': 1}
                def __init__(self, *args, **kwargs):
                    pass
                def list_workspace(self):
                    return {'theories': [{'theory': 'Target', 'status': 'pending',
                        'selected': True, 'contracts': ['target'], 'ready_for_proof_action': True}]}
            class Executor:
                lock = threading.RLock()
                records = {'one': {}, 'two': {}}
                closed = False
                shutdown_complete = False
                def cancel(self, key):
                    order.append('cancel:'+key)
                    if key == 'one':
                        raise OSError(errno.ENOSPC, 'cancellation receipt disk full')
                def close(self):
                    order.append('executor.close')
                    self.shutdown_complete = not failed_join
                    raise OSError(errno.ENOSPC, 'join failed' if failed_join else 'final receipt disk full')
            executor = Executor()
            def close_resources():
                order.append('resources.close')
            resources = SimpleNamespace(policy={}, close=close_resources)
            def session(*args, cancel_event, **kwargs):
                started.set()
                try:
                    if not cancel_event.wait(5):
                        raise AssertionError('coordinator never cancelled the agent')
                    return {'status': 'partial', 'theory': 'Target'}
                finally:
                    worker_exited.set()
            def interrupt(*args, **kwargs):
                self.assertTrue(started.wait(3))
                os.kill(os.getpid(), signum)
                self.fail('termination signal was not handled')
            def emit(*args, **kwargs):
                order.append('diagnostic.event')
                raise OSError(errno.ENOSPC, 'event disk full')
            def save(path, *args, **kwargs):
                if path.name == 'interruption.json':
                    order.append('diagnostic.receipt')
                    raise OSError(errno.ENOSPC, 'interruption receipt disk full')
            for name, replacement in {
                'WorkspaceController': Overview, 'NativeResources': Mock(return_value=resources),
                'CheckExecutor': Mock(return_value=executor), 'run_theory_session': session,
                'wait': interrupt, 'emit_event': emit, 'durable': save,
                'event_scope': lambda *args, **kwargs: nullcontext(),
            }.items():
                stack.enter_context(patch.object(planner, name, replacement))
            stack.enter_context(patch('regen.infrastructure.audited_llm.model_settings', return_value={
                'codex_bin': str(binary), 'codex_sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}))
            stack.enter_context(patch('regen.preparation.checker_context.load_context',
                                     return_value={'checker_context_id': 'fixture'}))
            stack.enter_context(patch('regen.execution.proof_session_runner.acquire_run_mutation_lock',
                                     return_value={'test_token': True}))
            release = stack.enter_context(patch('regen.execution.proof_session_runner.release_run_mutation_lock'))
            with self.assertRaises(KeyboardInterrupt) as caught:
                planner.run_parallel_workspace(root, allow_external_call=True, integrate=False)
            self.assertTrue(worker_exited.is_set())
            self.assertTrue(executor.closed)
            self.assertLess(order.index('cancel:two'), order.index('diagnostic.event'))
            self.assertLess(order.index('diagnostic.event'), order.index('diagnostic.receipt'))
            self.assertLess(order.index('executor.close'), order.index('resources.close'))
            self.assertIn('event disk full', '\n'.join(caught.exception.__notes__))
            if failed_join:
                release.assert_not_called()
            else:
                release.assert_called_once_with({'test_token': True})
        for sig, previous in originals.items():
            self.assertIs(signal.getsignal(sig), previous)

    def test_sigterm_cancels_before_failed_diagnostics_and_closes_resources(self):
        self._run_interrupted(signum=signal.SIGTERM)

    def test_sighup_also_cancels_and_restores_prior_handler(self):
        self._run_interrupted(signum=signal.SIGHUP)

    def test_failed_join_preserves_run_ownership_while_attempting_resource_close(self):
        self._run_interrupted(failed_join=True)


if __name__ == '__main__':
    unittest.main()
