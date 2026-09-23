"""Candidate deadlines do not repeat timed-out work or consume seal budgets."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from regen.execution.batch_checker import BatchWorker, body
from regen.execution.proof_batches import check_batch, prepare_batch, validate_batch_commit
from regen.tests.current.test_proof_batches import Checker
from regen.tests.current.test_proof_commits import fixture


class BatchTimeoutTest(unittest.TestCase):
    def setup_case(self, root):
        package, old = fixture()
        new = prepare_batch(package, old, {'proofs':{'helper':'by simp'}})
        checker = Checker(); checker.work_dir = root
        checker.timeout = 1800; checker.batch_candidate_timeout = 120; checker.batch_incremental = True
        return old, new, checker

    def worker_factory(self, root, transaction):
        def factory(checker, source, names, imports):
            worker = object.__new__(BatchWorker)
            worker.checker = checker; worker.root = root; worker.name = 'fake-timeout-worker'
            worker.index = 1; worker.base = body(source); worker.pending = 'uncommitted-old-candidate'
            worker.imports = imports; worker.closed = False
            worker.process = Mock(); worker.process.poll.return_value = None
            worker.transaction = transaction
            worker.close = Mock(side_effect=lambda: setattr(worker, 'closed', True))
            return worker
        return factory

    def assert_timeout_evidence(self, old, new, evidence, scope):
        check = evidence['check']
        self.assertEqual(check['verdict'], 'red')
        self.assertEqual(check['returncode'], 124)
        self.assertTrue(check['timed_out'])
        self.assertEqual(check['timeout_scope'], scope)
        self.assertEqual(check['source_sha256'], hashlib.sha256(check['source_content'].encode()).hexdigest())
        self.assertEqual(check['log_sha256'], hashlib.sha256(check['log_content'].encode()).hexdigest())
        self.assertIn('prefix_sha256', check)
        self.assertIn('delta_sha256', check)
        self.assertIn('by simp', check['source_content'])
        self.assertNotIn('incremental_fallback', evidence)
        with self.assertRaises(ValueError):
            validate_batch_commit(old, new, evidence)

    def test_ml_candidate_timeout_is_red_clears_pending_and_never_cold_repeats(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); old, new, checker = self.setup_case(root)
            transaction = Mock(return_value=('TIMEOUT\nexecute-and-audit deadline', 'REGEN_COMMAND by 120000'))
            with patch('regen.execution.batch_checker.BatchWorker', self.worker_factory(root, transaction)):
                evidence = check_batch(checker, old, new)
            self.assert_timeout_evidence(old, new, evidence, 'candidate-execute-and-audit')
            self.assertEqual(checker.calls, 0)
            self.assertIsNone(checker._batch_worker.pending)
            self.assertEqual(evidence['check']['timeout_seconds'], 120)
            self.assertEqual(checker.timeout, 1800)
            with self.assertRaises(ValueError):
                checker._batch_worker.align(body(evidence['check']['source_content']))

    def test_watchdog_timeout_saves_evidence_closes_worker_and_never_cold_repeats(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); old, new, checker = self.setup_case(root)
            error = TimeoutError('response deadline'); error.worker_log = 'partial native log'
            with patch('regen.execution.batch_checker.BatchWorker', self.worker_factory(root, Mock(side_effect=error))):
                evidence = check_batch(checker, old, new)
            self.assert_timeout_evidence(old, new, evidence, 'candidate-watchdog')
            self.assertIn('partial native log', evidence['check']['log_content'])
            self.assertEqual(checker.calls, 0)
            self.assertIsNone(checker._batch_worker.pending)
            self.assertTrue(checker._batch_worker.closed)
            checker._batch_worker.close.assert_called_once()
            self.assertEqual(evidence['check']['watchdog_grace_seconds'], 10)

    def test_prefix_startup_timeout_has_distinct_scope_without_cold_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); old, new, checker = self.setup_case(root)
            error = TimeoutError('prefix deadline'); error.timeout_scope = 'prefix-startup'
            error.timeout_seconds = 1800; error.worker_log = 'prefix reconstruction'
            with patch('regen.execution.batch_checker.BatchWorker', side_effect=error):
                evidence = check_batch(checker, old, new)
            self.assert_timeout_evidence(old, new, evidence, 'prefix-startup')
            self.assertEqual(evidence['check']['timeout_seconds'], 1800)
            self.assertEqual(checker.calls, 0)
            self.assertIsNone(checker._batch_worker)

    def test_infrastructure_fallback_uses_candidate_budget_then_restores_full_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new, checker = self.setup_case(Path(tmp))
            budgets = []
            original = checker._check
            def check(source, label):
                budgets.append(checker.timeout)
                return original(source, label)
            checker._check = check
            with patch('regen.execution.batch_checker.BatchWorker', side_effect=RuntimeError('worker infrastructure failed')):
                evidence = check_batch(checker, old, new)
            self.assertEqual(budgets, [120])
            self.assertEqual(checker.timeout, 1800)
            self.assertEqual(checker.calls, 1)
            self.assertEqual(evidence['check']['timeout_seconds'], 120)
            self.assertFalse(evidence['check']['incremental'])
            validate_batch_commit(old, new, evidence)
            checker._check(evidence['check']['source_content'], 'full-seal')
            self.assertEqual(budgets, [120, 1800])

    def test_cold_exception_also_restores_full_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new, checker = self.setup_case(Path(tmp))
            checker.batch_incremental = False
            def check(source, label):
                self.assertEqual(checker.timeout, 120)
                raise RuntimeError('cold infrastructure failure')
            checker._check = check
            with self.assertRaisesRegex(RuntimeError, 'cold infrastructure failure'):
                check_batch(checker, old, new)
            self.assertEqual(checker.timeout, 1800)

    def test_transaction_watchdog_grace_does_not_change_commit_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = object.__new__(BatchWorker)
            worker.root = root; worker.index = 0
            worker.checker = Mock(timeout=1800, batch_candidate_timeout=120)
            (root/'worker.log').write_text('')
            observed = []
            def wait(path, *, timeout):
                observed.append(timeout)
                path.write_text('GREEN' if len(observed)==1 else 'COMMITTED')
            worker.wait = wait
            self.assertEqual(worker.transaction('check', 'candidate')[0], 'GREEN')
            self.assertEqual(worker.transaction('commit')[0], 'COMMITTED')
            self.assertEqual(observed, [130, 1800])


if __name__ == '__main__':
    unittest.main()
