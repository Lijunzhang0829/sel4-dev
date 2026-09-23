"""Controller phase accounting covers continue, return and transport failures."""
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from regen.planning.batch_file_planner import _run_batch_file
from regen.tests.current.test_proof_commits import fixture


class BatchTimingTest(unittest.TestCase):
    def timing(self, path):
        value = json.loads(path.read_text())
        self.assertTrue(all(v >= 0 for v in value['phase_seconds'].values()))
        self.assertAlmostEqual(sum(value['phase_seconds'].values()), value['total_wall_seconds'], places=8)
        return value

    def execute(self, root, responses, *, checked=None, seal=None, max_requests=2, candidate_timeout=120):
        package, initial = fixture()
        directory = root/'theories/Demo'; directory.mkdir(parents=True, exist_ok=True)
        journal = Mock()
        journal.replay.return_value = deepcopy(initial)
        def append(event, state, evidence):
            journal.replay.return_value = deepcopy(state)
            return deepcopy(state)
        journal.append.side_effect = append
        checker = Mock(timeout=900, batch_incremental=True)
        checker.imports.return_value = ['Semantic_Base.Semantic_Root']
        setup = ({'run_id':'test-run','execution_models':{'File Planner':'test'}}, package, directory, journal, checker, [])
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('regen.execution.repository_execution.setup', return_value=setup))
        stack.enter_context(patch('regen.execution.repository_execution.progress', return_value={'test_progress':True}))
        stack.enter_context(patch('regen.execution.proof_session_runner.frozen_role_model', return_value='test'))
        call = stack.enter_context(patch('regen.planning.batch_file_planner.call_llm', side_effect=responses))
        stack.enter_context(patch('regen.planning.batch_file_planner.validate_call_artifact', return_value={'sealable_generation':True}))
        if checked is not None:
            def prepare(package, state, proposal):
                candidate = deepcopy(state)
                candidate['proofs'].update(proposal['proofs'])
                return candidate
            stack.enter_context(patch('regen.execution.proof_batches.prepare_batch', side_effect=prepare))
            stack.enter_context(patch('regen.execution.proof_batches.check_batch', return_value=checked))
        if seal is not None:
            stack.enter_context(patch('regen.planning.batch_file_planner.seal_theory', return_value=seal))
        return lambda: _run_batch_file(root, 'Demo', max_requests=max_requests, candidate_timeout=candidate_timeout), directory, journal, checker, call

    def test_accepted_continue_then_pause_and_initialization_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run, directory, journal, checker, call = self.execute(root, [
                json.dumps({'action':'submit_batch', 'proofs':{'helper':'by simp'}}),
                json.dumps({'action':'pause', 'reason':'test boundary'})], checked={'check':{'verdict':'green'}})
            self.assertEqual(run()['status'], 'paused')
            first = self.timing(directory/'batch-requests/000001/timing.json')
            second = self.timing(directory/'batch-requests/000002/timing.json')
            self.assertEqual(first['status'], 'accepted')
            self.assertEqual(second['status'], 'paused')
            self.assertEqual(first['model_attempts'], 1)
            self.assertTrue(first['calls'][0]['audited_turn_completed'])
            self.assertTrue(first['calls'][0]['provider_called'])
            self.assertEqual(set(first['phase_seconds']), {'charge','input_and_prompt','model','action','commit','progress'})
            self.assertEqual(call.call_count, 2)
            init = self.timing(directory/'batch-initialization-timing.json')
            archives = list((directory/'initialization-timings').glob('*.json'))
            self.assertEqual(len(archives), 1)
            self.assertEqual(self.timing(archives[0]), init)
            # A subsequent initialization must preserve the previous timing.
            journal.replay.return_value = {**journal.replay.return_value, 'status':'sealed'}
            run()
            self.assertEqual(len(list((directory/'initialization-timings').glob('*.json'))), 2)
            self.assertEqual(self.timing(archives[0]), init)
            self.assertEqual(checker.close_prefix.call_count, 2)

    def test_saved_input_matches_prompt_and_monitor_closes_on_return(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run, directory, journal, checker, call = self.execute(root, [
                json.dumps({'action':'pause', 'reason':'test boundary'})])
            run()
            payload = json.loads((directory/'batch-requests/000001/input.json').read_text())
            self.assertIn('observation_inventory', payload)
            self.assertEqual(json.loads(call.call_args.args[1].split('\nINPUT:\n', 1)[1]), payload)
            status = json.loads((root/'runner-status.json').read_text())
            self.assertFalse(status['running'])
            self.assertEqual(status['stop_reason'], 'controller-returned')
            events = [json.loads(line) for line in (root/'runner-events.ndjson').read_text().splitlines()]
            self.assertEqual(events[0]['event'], 'file_planner_started')
            self.assertEqual(events[-1]['event'], 'runner_monitor_stopped')

    def test_candidate_budget_is_applied_and_frozen_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, directory, journal, checker, call = self.execute(Path(tmp),
                [json.dumps({'action':'pause', 'reason':'test'})], candidate_timeout=17)
            run()
            policy = json.loads((directory/'batch-policy.json').read_text())
            self.assertEqual(policy['candidate_timeout'], 17)
            self.assertEqual(policy['check_timeout'], 900)
            self.assertEqual(checker.batch_candidate_timeout, 17)

    def test_successful_finish_return_is_timed(self):
        with tempfile.TemporaryDirectory() as tmp:
            package, initial = fixture()
            sealed = {**initial, 'status':'sealed'}
            run, directory, journal, checker, call = self.execute(Path(tmp),
                [json.dumps({'action':'finish'})], seal=(sealed, {'seal':'test'}))
            self.assertEqual(run(), {'test_progress':True})
            timing = self.timing(directory/'batch-requests/000001/timing.json')
            self.assertEqual(timing['status'], 'sealed')
            self.assertEqual(timing['outcome'], 'completed')
            self.assertEqual(timing['action'], 'finish')
            self.assertEqual(journal.append.call_args.args[0], 'seal')
            checker.close_prefix.assert_called_once()

    def test_unclassified_model_failure_preserves_charge_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, directory, journal, checker, call = self.execute(Path(tmp),
                [RuntimeError('first transport failure'), RuntimeError('second transport failure')])
            with self.assertRaisesRegex(RuntimeError, 'first transport failure'):
                run()
            timing = self.timing(directory/'batch-requests/000001/timing.json')
            self.assertEqual(timing['model_attempts'], 1)
            self.assertEqual(timing['outcome'], 'exception')
            self.assertEqual(timing['exception_type'], 'RuntimeError')
            self.assertEqual(timing['phase_seconds']['action'], 0)
            self.assertEqual(timing['phase_seconds']['commit'], 0)
            self.assertEqual(timing['phase_seconds']['progress'], 0)
            self.assertEqual(journal.append.call_args.args[0], 'failed')
            self.assertEqual(journal.append.call_args.args[1]['status'], 'infrastructure_error')
            self.assertEqual(journal.append.call_args.args[1]['batch_requests'], 1)
            self.assertEqual(call.call_count, 1)
            checker.close_prefix.assert_called_once()


if __name__ == '__main__':
    unittest.main()
