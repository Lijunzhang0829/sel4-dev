import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from regen.execution.check_executor import CheckExecutor
from regen.execution.check_tasks import CheckCancelled


class WaitUntilResultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.executor = CheckExecutor(Path(self.tmp.name), workers=1)
        self.addCleanup(self.executor.close)

    def test_returns_immediately_when_a_task_is_terminal(self):
        self.executor.records['task-a'] = {'task_id': 'task-a', 'status': 'running'}
        self.executor.records['task-b'] = {'task_id': 'task-b', 'status': 'completed'}
        self.executor.get = lambda key: self.executor.records[key]
        with patch.object(self.executor.condition, 'wait') as wait:
            result = self.executor.wait_until_result(['task-a', 'task-b'], check_active=lambda: None)
        self.assertEqual([row['task_id'] for row in result], ['task-a', 'task-b'])
        wait.assert_not_called()

    def test_check_active_can_cancel_unchanged_wait(self):
        self.executor.records['task-a'] = {'task_id': 'task-a', 'status': 'running'}
        self.executor.get = lambda key: self.executor.records[key]
        def abort():
            raise CheckCancelled('session aborted')
        with self.assertRaises(CheckCancelled):
            self.executor.wait_until_result(['task-a'], check_active=abort)

    def test_rejects_duplicate_task_ids(self):
        with self.assertRaises(ValueError):
            self.executor.wait_until_result(['task-a', 'task-a'], check_active=lambda: None)


if __name__ == '__main__':
    unittest.main()
