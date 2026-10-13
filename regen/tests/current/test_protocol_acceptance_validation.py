"""Controls for the acceptance reporter, not claims that production is repaired."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.tools import protocol_acceptance_validation as validation


class ProtocolAcceptanceValidationTests(unittest.TestCase):
    def test_protocol_and_mock_clock_controls_pass(self):
        self.assertTrue(all(row['pass'] for row in validation.controls()))

    def test_delayed_events_fail_even_if_all_arrive(self):
        result = validation.latency_assessment([2, 1.5, 1, .5], .5,
            received_while_callback=0, required_count=4)
        self.assertFalse(result['pass'])
        self.assertEqual(result['max_receive_delay_seconds'], 2)

    def test_events_must_arrive_while_callback_is_active(self):
        result = validation.latency_assessment([.01]*4, .5,
            received_while_callback=0, required_count=4)
        self.assertFalse(result['pass'])

    def test_missing_events_fail(self):
        result = validation.latency_assessment([], .5,
            received_while_callback=0, required_count=4)
        self.assertFalse(result['pass'])

    def test_prompt_complete_event_ingestion_passes(self):
        result = validation.latency_assessment([.01]*4, .5,
            received_while_callback=4, required_count=4)
        self.assertTrue(result['pass'])

    def test_partial_event_capture_cannot_pass(self):
        result = validation.latency_assessment([.01]*3, .5,
            received_while_callback=3, required_count=4)
        self.assertFalse(result['pass'])

    def test_one_large_lag_cannot_hide_in_average(self):
        result = validation.latency_assessment([.01]*99+[2], .5,
            received_while_callback=100, required_count=100)
        self.assertFalse(result['pass'])

    def test_replay_rejection_is_not_acceptance_success(self):
        events = [{'source_line': 1, 'event': {'time_unix': 1, 'message': {'parsed': {
            'method': 'item/started', 'params': {'threadId': 't', 'turnId': 'u',
                'item': {'type': 'sleep', 'id': 's', 'durationMs': 40000}}}}}}]
        with patch.object(validation, 'validate_sleep_event', side_effect=ValueError('fixture rejection')):
            result = validation.replay_sleep(events)
        self.assertFalse(result['acceptance_pass'])
        self.assertEqual(result['status'], 'reproduced')

    def test_policy_comparison_is_not_whole_failed_session_acceptance(self):
        events = []
        for number in range(4):
            for method in ('item/started', 'item/completed'):
                events.append({'source_line': len(events)+1, 'event': {
                    'time_unix': 1, 'message': {'parsed': {'method': method,
                    'params': {'threadId': 't', 'turnId': 'u', 'item': {
                    'type': 'sleep', 'id': str(number), 'durationMs': 40000}}}}}})
        with patch.object(validation, 'audit_workspace_session', side_effect=ValueError('failed marker')):
            result = validation.historical_comparison(events, Path('/unused'))
        self.assertTrue(result['historical_legacy_reproduced'])
        self.assertTrue(result['current_policy_event_prefix_accepted'])
        self.assertTrue(result['historical_failed_whole_session_rejected']['pass'])
        self.assertEqual(result['legacy']['rejected_on_sleep_start_number'], 4)
        self.assertEqual(result['current_policy']['pending_sleep_count'], 0)
        with patch.object(validation, 'audit_workspace_session'):
            result = validation.historical_comparison(events, Path('/unused'))
        self.assertFalse(result['historical_failed_whole_session_rejected']['pass'])

    def test_pending_prefix_is_explicitly_not_sealable_session(self):
        events = [{'source_line': 1, 'event': {'time_unix': 1, 'message': {'parsed': {
            'method': 'item/started', 'params': {'threadId': 't', 'turnId': 'u',
                'item': {'type': 'sleep', 'id': 's', 'durationMs': 40000}}}}}}]
        result = validation.replay_sleep(events, policy_version=2)
        self.assertTrue(result['acceptance_pass'])
        self.assertEqual(result['pending_sleep_count'], 1)
        self.assertIn('not whole-session sealability', result['limitation'])

    def test_existing_report_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(FileExistsError):
                validation.run(Path(temporary), Path(temporary)/'unused')

    def test_source_experiment_cannot_be_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)/'run/parallel-sessions/session/T/session-transcript.jsonl'
            with self.assertRaisesRegex(ValueError, 'historical source'):
                validation.run(Path(temporary)/'run/report', source)

    def test_cli_nonacceptance_is_nonzero(self):
        with patch.object(validation, 'run', return_value={
                'controls_pass': True, 'acceptance_pass': False, 'exit_code': 1}):
            self.assertEqual(validation.main(['--output', '/tmp/unused-protocol-validation']), 1)


if __name__ == '__main__':
    unittest.main()
