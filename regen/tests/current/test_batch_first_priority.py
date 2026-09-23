"""Regressions for oversized feedback, bound diagnostic reads and terminal state."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest, durable
from regen.planning.batch_file_planner import (
    apply_feedback, planner_input, prepare_prompt, read_diagnostic, PROMPT_CHARS)
from regen.tests.current.test_proof_commits import fixture
from regen.tests.current import test_batch_timings as timings


class ObservationBudgetTests(unittest.TestCase):
    def test_huge_feedback_deduplicates_without_mutating_evidence(self):
        package, state = fixture()
        diagnostic = 'Failed goal\n' + 'x = y\n' * 95000
        feedback = {'status':'rejected_batch', 'check':{'diagnostic':diagnostic}}
        original = deepcopy(feedback)
        state = apply_feedback(state, {'action':'submit_batch','proofs':{'target':'by simp'}}, feedback, 1)
        # Include an old continuation with full duplicated feedback, not only new reducer output.
        state['batch_proof_feedback']['feedback'] = deepcopy(feedback)
        view, prompt = prepare_prompt(planner_input(package, state))
        self.assertLess(len(prompt), 60000)
        self.assertEqual(view['last_feedback']['reference'], 'last_proof_feedback.feedback')
        excerpt = view['last_proof_feedback']['feedback']['check']
        self.assertTrue(excerpt['diagnostic_excerpt']['incomplete'])
        self.assertEqual(excerpt['diagnostic_excerpt']['total_chars'],len(diagnostic))
        self.assertEqual(feedback, original)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            artifact = directory/'batch-requests/000001/feedback.json'
            durable(artifact, feedback)
            page = read_diagnostic(directory, state, {'request_number':1,'offset':12000,'limit':8000})
            self.assertEqual(page['diagnostic_page']['text'],diagnostic[12000:20000])
            self.assertEqual(page['diagnostic_page']['next_offset'],20000)
            durable(artifact, {'check':{'diagnostic':'tampered'}})
            with self.assertRaises(ValueError):
                read_diagnostic(directory, state, {'request_number':1})

    def test_oversized_mandatory_graph_is_not_silently_truncated(self):
        payload = {'graph':{'statement':'a' * PROMPT_CHARS}}
        before = deepcopy(payload)
        with self.assertRaisesRegex(ValueError, 'input_budget_exceeded'):
            prepare_prompt(payload)
        self.assertEqual(payload,before)


class LifecycleTests(timings.BatchTimingTest):
    def test_preflight_rejection_never_calls_backend_and_preserves_charge(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, directory, journal, checker, call = self.execute(Path(tmp), [])
            with patch('regen.planning.batch_file_planner.planner_input',
                       return_value={'graph':{'statement':'a' * PROMPT_CHARS}}):
                with self.assertRaisesRegex(ValueError,'input_budget_exceeded'):
                    run()
            call.assert_not_called()
            self.assertEqual(journal.append.call_args.args[0], 'failed')
            terminal = journal.append.call_args.args[1]
            self.assertEqual(terminal['status'],'infrastructure_error')
            self.assertEqual(terminal['batch_requests'],1)
            self.assertEqual(self.timing(directory/'batch-requests/000001/timing.json')['model_attempts'],0)
            self.assertEqual(json.loads((directory/'execution-stop.json').read_text())['request_number'],1)
            checker.close_prefix.assert_called_once()

    def test_failure_after_green_commit_preserves_latest_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, directory, journal, checker, call = self.execute(Path(tmp), [
                json.dumps({'action':'submit_batch','proofs':{'helper':'by simp'}}),
                ValueError('local input rejection')], checked={'check':{'verdict':'green'}})
            with self.assertRaisesRegex(ValueError,'local input rejection'):
                run()
            terminal = journal.append.call_args.args[1]
            self.assertEqual(terminal['proofs']['helper'],'by simp')
            self.assertEqual(terminal['batch_requests'],2)

    def test_startup_failure_is_reported_without_charging_or_calling_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run, directory, journal, checker, call = self.execute(root, [])
            with patch('regen.execution.repository_execution.setup', side_effect=ValueError('frozen context drift')):
                with self.assertRaisesRegex(ValueError,'frozen context drift'):
                    run()
            call.assert_not_called()
            journal.append.assert_not_called()
            self.assertEqual(json.loads((root/'startup-failure.json').read_text())['request_number'],0)

    def test_initial_scaffold_failure_with_no_state_retains_original_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, directory, journal, checker, call = self.execute(Path(tmp), [])
            journal.replay.return_value = None
            with patch('regen.planning.batch_file_planner.build_graph', side_effect=ValueError('invalid scaffold')):
                with self.assertRaisesRegex(ValueError,'invalid scaffold'):
                    run()
            self.assertEqual(json.loads((directory/'execution-stop.json').read_text())['reason'],'invalid scaffold')
            checker.close_prefix.assert_called_once()

    def test_cleanup_error_does_not_mask_model_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, directory, journal, checker, call = self.execute(Path(tmp), [ValueError('original model failure')])
            checker.close_prefix.side_effect = RuntimeError('cleanup failed')
            with self.assertLogs('regen.planning.batch_file_planner', level='ERROR'):
                with self.assertRaisesRegex(ValueError,'original model failure'):
                    run()
            self.assertEqual(json.loads((directory/'execution-stop.json').read_text())['reason'],'original model failure')
