"""Cleanup failures must not skip sibling cleanup or hide the original error."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from regen.planning.parallel_file_planner import TheoryController, _cleanup_all
from regen.planning.proof_workspace_agent import WorkspaceController


class ParallelCleanupBasicsTests(unittest.TestCase):
    def test_all_actions_run_and_first_cleanup_error_is_preserved(self):
        first, second = OSError('disk full'), RuntimeError('worker close failed')
        actions = [Mock(side_effect=first), Mock(side_effect=second), Mock()]
        with self.assertRaises(OSError) as caught:
            _cleanup_all(actions)
        self.assertIs(caught.exception, first)
        self.assertTrue(all(action.call_count == 1 for action in actions))
        self.assertIn('worker close failed', '\n'.join(first.__notes__))

    def test_original_exception_is_not_replaced_by_cleanup(self):
        original = KeyboardInterrupt('coordinator terminated')
        final = Mock()
        _cleanup_all([Mock(side_effect=OSError('audit full')), final], primary=original)
        final.assert_called_once()
        self.assertIn('audit full', '\n'.join(original.__notes__))

    def test_cleanup_interrupt_still_runs_remaining_actions(self):
        final = Mock()
        with self.assertRaises(KeyboardInterrupt):
            _cleanup_all([Mock(side_effect=KeyboardInterrupt()), final])
        final.assert_called_once()

    def controller(self):
        controller = object.__new__(TheoryController)
        controller.task_ids = ['first', 'second']
        controller.executor = SimpleNamespace(cancel=Mock(), get=Mock(return_value={'status':'cancelled'}), wait=Mock())
        return controller

    def test_controller_cancel_failure_does_not_skip_other_cancel_drain_or_workers(self):
        controller = self.controller()
        controller.executor.cancel.side_effect = [OSError('cancel persistence failed'), None]
        with patch.object(WorkspaceController, 'close') as native_close:
            with self.assertRaisesRegex(OSError, 'cancel persistence failed'):
                controller.close()
        self.assertEqual(controller.executor.cancel.call_count, 2)
        self.assertEqual(controller.executor.get.call_count, 2)
        native_close.assert_called_once()

    def test_controller_drain_failure_still_closes_workers(self):
        controller = self.controller()
        controller.executor.get.side_effect = RuntimeError('task state unavailable')
        with patch.object(WorkspaceController, 'close') as native_close:
            with self.assertRaisesRegex(RuntimeError, 'task state unavailable'):
                controller.close()
        self.assertEqual(controller.executor.cancel.call_count, 2)
        native_close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
