"""Synthetic resource/transport tests; no mathematical evidence is produced."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources, native_operation
from regen.execution.check_tasks import CheckCancelled, NativePreparationError
from regen.execution.repository_execution import ProviderPreparationError
from regen.planning.parallel_file_planner import TheoryController


def controller(resources, theory='Target'):
    value = object.__new__(TheoryController)
    value.root = Path('/synthetic')
    value.theory = theory
    value.options = {'check_timeout':900}
    value.resources = resources
    value.runtime_cache = {}
    value._runtime_lock = threading.RLock()
    value._provider_preparation_error = None
    value.document_mode = True
    value.generation_strategy = {'name': 'normal'}
    return value


class BootstrapResourceTests(unittest.TestCase):
    def test_sync_runtime_binds_policy_and_restores_callers_context(self):
        resources = NativeResources()
        value = controller(resources)
        checker = SimpleNamespace()
        result = (None, None, None, None, checker, None)
        sentinel = object()
        marker = CURRENT_RESOURCES.set(sentinel)
        try:
            def setup(*args):
                self.assertIs(CURRENT_RESOURCES.get(), resources)
                self.assertEqual(CURRENT_RESOURCES.get().policy['threads'], 4)
                return result
            with patch('regen.execution.repository_execution.setup', side_effect=setup) as call:
                self.assertIs(value._runtime(), result)
                self.assertIs(value._runtime(), result)
                call.assert_called_once()
            self.assertIs(CURRENT_RESOURCES.get(), sentinel)
            self.assertTrue(checker.document_mode)
        finally:
            CURRENT_RESOURCES.reset(marker)

    def test_failed_provider_is_latched_without_rebuild(self):
        value = controller(NativeResources())
        failure = ProviderPreparationError('provider timeout')
        self.assertIsInstance(failure, NativePreparationError)
        with patch('regen.execution.repository_execution.setup', side_effect=failure) as call:
            for _ in range(2):
                with self.assertRaises(ProviderPreparationError) as caught:
                    value._runtime()
                self.assertIs(caught.exception, failure)
            call.assert_called_once()
        self.assertIsNone(CURRENT_RESOURCES.get())
        self.assertEqual(value.runtime_cache, {})

    def test_cancellation_and_unrelated_errors_are_not_latched(self):
        for error in (CheckCancelled('cancel'), ValueError('invalid input')):
            value = controller(NativeResources())
            with patch('regen.execution.repository_execution.setup', side_effect=error) as call:
                for _ in range(2):
                    with self.assertRaises(type(error)):
                        value._runtime()
                self.assertEqual(call.call_count, 2)
            self.assertIsNone(value._provider_preparation_error)
            self.assertIsNone(CURRENT_RESOURCES.get())

    def test_two_sync_initializations_share_one_native_slot(self):
        resources = NativeResources(slots=1, threads=4, resident_limit=2)
        self.addCleanup(resources.close)
        first_entered, release, second_started, second_entered = [threading.Event() for _ in range(4)]
        class Checker:
            def close_prefix(self):
                pass
            @native_operation
            def build(self, first):
                if first:
                    first_entered.set()
                    if not release.wait(5):
                        raise AssertionError('test release timed out')
                else:
                    second_entered.set()
        def setup(root, theory, timeout):
            checker = Checker()
            if theory == 'second':
                second_started.set()
            checker.build(theory == 'first')
            return (None, None, None, None, checker, None)
        with patch('regen.execution.repository_execution.setup', side_effect=setup), ThreadPoolExecutor(2) as pool:
            first = pool.submit(controller(resources, 'first')._runtime)
            try:
                self.assertTrue(first_entered.wait(3))
                second = pool.submit(controller(resources, 'second')._runtime)
                self.assertTrue(second_started.wait(3))
                self.assertFalse(second_entered.wait(.1))
            finally:
                release.set()
            first.result(timeout=3)
            second.result(timeout=3)
        self.assertTrue(second_entered.is_set())
        self.assertEqual(resources.peak_active, 1)
        self.assertEqual(len(resources.metrics), 2)

    def test_provider_error_survives_task_serialization(self):
        value = controller(NativeResources())
        value._tool_lock = threading.RLock()
        value._submit_task = lambda *args: {'status':'error', 'error_type':'ProviderPreparationError',
                                          'diagnostic':'provider killed: 137'}
        with self.assertRaisesRegex(ProviderPreparationError, '137'):
            value.submit({}, {})


if __name__ == '__main__':
    unittest.main()
