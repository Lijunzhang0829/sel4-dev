"""A missing native response is retryable transport feedback, never proof RED."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from regen.execution.batch_checker import BatchWorker, body
from regen.execution.proof_batches import (
    NativeEvidenceError, check_batch, classify_native_failure, prepare_batch,
    recoverable_native_watchdog, validate_batch_commit,
)
from regen.execution.proof_commits import ProofCommits
from regen.execution.repository_execution import validator
from regen.planning.batch_file_planner import _run_batch_file
from regen.planning.planner_efficiency import deterministic_red, reused_failure
from regen.tests.current.test_incremental_batches import MemoryWorker
from regen.tests.current.test_proof_batches import Checker
from regen.tests.current.test_proof_commits import check, fixture


class NativeWatchdogRecoveryTests(unittest.TestCase):
    def worker_factory(self, root, created, *, cleanup_error=None):
        def factory(checker, source, names, imports):
            worker = object.__new__(BatchWorker)
            worker.checker, worker.root, worker.name = checker, root, 'test-owned-worker'
            worker.index, worker.base, worker.pending = 0, body(source), None
            worker.imports, worker.closed = imports, False
            worker.process = Mock(); worker.process.poll.return_value = None
            error = TimeoutError('incremental batch worker timeout waiting for 1.response')
            error.worker_log = 'partial native output; no response'
            worker.transaction = Mock(side_effect=error)
            worker.close = Mock(side_effect=(cleanup_error if cleanup_error else
                lambda: setattr(worker, 'closed', True)))
            created.append((worker, source, names))
            return worker
        return factory

    def checker(self, root):
        checker = Checker()
        checker.work_dir, checker.batch_incremental = root, True
        checker.batch_candidate_timeout = 300
        return checker

    def test_watchdog_discards_worker_keeps_committed_prefix_and_never_accepts(self):
        with tempfile.TemporaryDirectory() as tmp:
            package, empty = fixture()
            old = prepare_batch(package, empty, {'proofs': {'helper': 'by simp'}})
            candidate = prepare_batch(package, old, {'proofs': {'contract': 'by simp'}})
            before = deepcopy(old); checker = self.checker(Path(tmp)); created = []
            with patch('regen.execution.batch_checker.BatchWorker', self.worker_factory(Path(tmp), created)):
                evidence = check_batch(checker, old, candidate)
            result = evidence['check']; worker = created[0][0]
            self.assertEqual(result['verdict'], 'infrastructure_error')
            self.assertTrue(recoverable_native_watchdog(result))
            self.assertFalse(result['framework_blocked'])
            self.assertEqual(result['native_receipt']['verdict'], 'infrastructure_error')
            self.assertEqual(result['prefix_sha256'], hashlib.sha256(worker.base.encode()).hexdigest())
            self.assertIn('no response', result['log_content'])
            self.assertEqual(old, before)
            self.assertIsNone(checker._batch_worker)
            self.assertTrue(worker.closed); self.assertIsNone(worker.pending)
            worker.close.assert_called_once(); worker.transaction.assert_called_once()
            self.assertEqual(checker.calls, 0)
            self.assertTrue(all(row['status'] == 'not_checked' for row in evidence['node_results']))
            self.assertFalse(deterministic_red(result))
            with self.assertRaises(ValueError):
                validate_batch_commit(old, candidate, evidence)
            self.assertTrue(recoverable_native_watchdog(classify_native_failure(deepcopy(result))))

    def test_explicit_next_check_constructs_new_worker_from_only_committed_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); package, empty = fixture()
            old = prepare_batch(package, empty, {'proofs': {'helper': 'by simp'}})
            candidate = prepare_batch(package, old, {'proofs': {'contract': 'by simp'}})
            checker = self.checker(root); created = []
            with patch('regen.execution.batch_checker.BatchWorker', self.worker_factory(root, created)):
                check_batch(checker, old, candidate)
            recovered = []
            def fresh(*args):
                recovered.append((args[1], args[2]))
                return MemoryWorker(*args)
            with patch('regen.execution.batch_checker.BatchWorker', fresh):
                evidence = check_batch(checker, old, candidate)
            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0][1], ['helper'])
            self.assertIn('lemma helper:', recovered[0][0])
            self.assertNotIn('lemma target:', recovered[0][0])
            validate_batch_commit(old, candidate, evidence)
            self.assertEqual(old['accepted_order'], ['helper'])
            self.assertEqual(old['proofs'], {'helper': 'by simp'})

    def test_cleanup_failure_remains_fatal_infrastructure(self):
        from regen.execution.check_tasks import NativePreparationError
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); package, old = fixture()
            candidate = prepare_batch(package, old, {'proofs': {'helper': 'by simp'}})
            checker = self.checker(root); created = []
            with patch('regen.execution.batch_checker.BatchWorker',
                       self.worker_factory(root, created, cleanup_error=RuntimeError('cleanup failed'))):
                with self.assertRaisesRegex(NativePreparationError, 'cleanup failed'):
                    check_batch(checker, old, candidate)
            self.assertIs(checker._batch_worker, created[0][0])
            self.assertEqual(old['proofs'], {})

    def test_prefix_binding_mismatch_cannot_authorize_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); package, old = fixture()
            candidate = prepare_batch(package, old, {'proofs': {'helper': 'by simp'}})
            checker = self.checker(root)
            worker = MemoryWorker(checker, 'theory X\nbegin\n\nend\n', [], checker.imports())
            worker.align = Mock()
            worker.check_batch = Mock(return_value={
                'verdict':'infrastructure_error', 'returncode':124, 'timed_out':True,
                'timeout_scope':'candidate-watchdog', 'prefix_sha256':'wrong'})
            with patch('regen.execution.batch_checker.BatchWorker', return_value=worker):
                with self.assertRaisesRegex(NativeEvidenceError, 'prefix mismatch'):
                    check_batch(checker, old, candidate)

    def test_legacy_watchdog_and_real_io_remain_blocked_without_verified_discard(self):
        for native in [
                {'verdict':'red', 'returncode':124, 'timed_out':True,
                 'timeout_scope':'candidate-watchdog', 'diagnostic':'no response'},
                {'verdict':'red', 'returncode':1, 'diagnostic':'exception Io {cause=SysErr("disk full",NONE)}'},
                {'verdict':'red', 'returncode':124, 'timeout_scope':'prefix-startup', 'diagnostic':'prefix timeout'}]:
            with self.subTest(native=native):
                result = classify_native_failure(native)
                self.assertEqual(result['verdict'], 'infrastructure_error')
                self.assertTrue(result['framework_blocked'])
                self.assertFalse(recoverable_native_watchdog(result))

    def test_failed_response_feedback_is_preserved_for_agent_and_not_red_memoized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); package, initial = fixture()
            directory = root/'theories/Demo'; directory.mkdir(parents=True)
            journal = ProofCommits(directory/'acceptance', {}, validator(package))
            journal.append('initial', initial, {'check':check()})
            checker = self.checker(directory); checker.close_prefix = Mock()
            run = {'run_id':'test-run','execution_models':{'File Planner':'test'}}
            setup = (run, package, directory, journal, checker, [])
            proposal = {'action':'submit_batch','proofs':{'helper':'by simp'}}
            with patch('regen.execution.repository_execution.setup', return_value=setup), \
                 patch('regen.execution.repository_execution.progress', return_value={'checked_contracts':0}), \
                 patch('regen.execution.proof_session_runner.frozen_role_model', return_value='test'), \
                 patch('regen.planning.batch_file_planner.call_llm', return_value=json.dumps(proposal)), \
                 patch('regen.planning.batch_file_planner.validate_call_artifact', return_value={'sealable_generation':True}), \
                 patch('regen.execution.batch_checker.BatchWorker', self.worker_factory(directory, [])):
                result = _run_batch_file(root, 'Demo', max_requests=1)
            self.assertEqual(result['status'], 'checkpoint')
            state = journal.replay(); feedback = state['batch_feedback']
            self.assertEqual(feedback['status'], 'infrastructure_error')
            self.assertFalse(feedback['framework_blocked'])
            self.assertTrue(feedback['retryable']); self.assertTrue(feedback['worker_discarded'])
            self.assertTrue(feedback['attempt_preserved']); self.assertEqual(feedback['new_green'], [])
            self.assertEqual(state['proofs'], {})
            self.assertEqual(state['batch_candidate']['proofs'], proposal['proofs'])
            self.assertEqual(state['batch_proof_feedback']['candidate']['proofs'], proposal['proofs'])
            request = directory/'batch-requests/000001'
            self.assertTrue((request/'check.json').exists())
            self.assertTrue((request/'feedback.json').exists())
            self.assertIsNone(reused_failure(directory, state, feedback['failure_identity']))
            self.assertEqual(checker.calls, 0)


if __name__ == '__main__':
    unittest.main()
