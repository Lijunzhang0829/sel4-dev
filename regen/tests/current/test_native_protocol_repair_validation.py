"""Pure acceptance controls; real cancellation requires the native report."""
import unittest
from unittest.mock import patch

from regen.tools.native_protocol_repair_validation import (
    native_source, ownership_bridge, pass_conditions, run,
)
from regen.planning.parallel_file_planner import TheoryController


def negative_values():
    return dict(error_type='SessionProtocolError', diagnostic='non-allowlisted built-in item: commandExecution',
                native_started=True, native_finished=False, task_status='cancelled',
                acceptance_written=False, result_written=False, completion_written=False,
                late_guard_blocked=True, response_count=0, child_exited=True, abort_count=1,
                abort_latency=.2)


class NativeProtocolRepairValidationTests(unittest.TestCase):
    def test_accepts_only_bound_genuine_cancel_observations(self):
        values = negative_values()
        self.assertTrue(pass_conditions('unsafe', **values))
        for key, value in [('native_started',False), ('native_finished',True), ('result_written',True),
                           ('acceptance_written',True), ('task_status','completed'), ('abort_count',0),
                           ('late_guard_blocked',False), ('response_count',1), ('abort_latency',6),
                           ('error_type','TimeoutError')]:
            with self.subTest(key=key):
                self.assertFalse(pass_conditions('unsafe', **{**values, key:value}))

    def test_foreign_requires_matching_protocol_failure(self):
        values = negative_values()
        self.assertFalse(pass_conditions('foreign', **values))
        values['diagnostic'] = 'tool callback belongs to another thread/turn'
        self.assertTrue(pass_conditions('foreign', **values))

    def test_legal_requires_native_completion_and_response_receipt(self):
        values = negative_values()
        values.update(error_type=None, diagnostic='', native_finished=True, task_status='completed',
                      acceptance_written=True, result_written=True, completion_written=True,
                      response_count=1, abort_count=0)
        self.assertTrue(pass_conditions('legal', **values))
        self.assertFalse(pass_conditions('legal', **{**values, 'response_count':0}))

    def test_fixed_source_cannot_select_unbounded_delay_or_external_path(self):
        text = native_source('Semantic_Base.Semantic_Root', 'unsafe', 45)
        self.assertIn('OS.Process.sleep (Time.fromSeconds 45)', text)
        self.assertIn('/acceptance/unsafe/native-started.txt', text)
        self.assertIn('lemma native_generic_true: "True" by simp', text)
        with self.assertRaises(ValueError):
            native_source('Main', '../../escape', 45)
        with self.assertRaises(ValueError):
            native_source('Main', 'unsafe', 9999)

    def test_ownership_bridge_uses_real_abort_filter_without_repo_initialization(self):
        import threading
        from types import SimpleNamespace
        cancelled = []
        records = {
            'mine': {'theory':'Protocol_Native_Fixture','binding':{'run_id':'fixture'},'status':'running'},
            'other_run': {'theory':'Protocol_Native_Fixture','binding':{'run_id':'other'},'status':'running'},
            'complete': {'theory':'Protocol_Native_Fixture','binding':{'run_id':'fixture'},'status':'completed'},
        }
        executor = SimpleNamespace(lock=threading.Lock(), records=records, cancel=cancelled.append)
        bridge = ownership_bridge(executor, 'fixture')
        TheoryController.abort_pending(bridge)
        self.assertEqual(cancelled, ['mine'])
        self.assertTrue(bridge._abort_requested.is_set())

    def test_invalid_budget_precedes_context_loading(self):
        with patch('regen.tools.native_protocol_repair_validation.load_context') as load:
            with self.assertRaises(ValueError):
                run('/unused', '/unused', 9999)
            load.assert_not_called()


if __name__ == '__main__':
    unittest.main()
