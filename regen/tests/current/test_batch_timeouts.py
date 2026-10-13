"""Candidate deadlines do not repeat timed-out work or consume seal budgets."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from regen.execution.batch_checker import BatchWorker, body, prefix_startup_budget
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
        self.assertEqual(check['verdict'], 'infrastructure_error' if scope == 'prefix-startup' else 'red')
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
            self.assertEqual(evidence['check']['failure_scope'], 'native_preparation')
            self.assertTrue(all(n['status']=='not_checked' for n in evidence['node_results']))

    def test_prefix_budget_scales_but_candidate_and_command_limits_do_not(self):
        self.assertEqual(prefix_startup_budget(900, 0), 900)
        self.assertEqual(prefix_startup_budget(900, 78), 2400)
        self.assertEqual(prefix_startup_budget(900, 1000), 3600)
        self.assertEqual(prefix_startup_budget(4000, 78), 4000)
        with tempfile.TemporaryDirectory() as tmp:
            checker = Mock(work_dir=Path(tmp), timeout=900, batch_candidate_timeout=120)
            checker.previous_theories = []
            checker._command.return_value = ['docker','run','image','isabelle','-e','x']
            with patch.object(BatchWorker, '_launch'):
                worker = BatchWorker(checker, 'theory T\nbegin\n\nend\n', ['a']*78, [])
            self.assertEqual(worker.prefix_timeout, 2400)
            self.assertEqual((worker.root/'command_timeout').read_text(), '900')
            self.assertEqual((worker.root/'candidate_timeout').read_text(), '120')

    def test_ready_watchdog_uses_prefix_limit_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'worker.log').write_text('prefix replay log')
            worker = object.__new__(BatchWorker)
            worker.root = root; worker.name = 'test-worker'
            worker.checker = Mock(timeout=900)
            worker.prefix_timeout = 2400
            worker.process = Mock(); worker.process.poll.return_value = None
            worker.close = Mock()
            for name, limit, scope in [('ready',2400,'prefix-startup'),
                                       ('1.response',900,'candidate-watchdog')]:
                with patch('regen.execution.batch_checker.time.monotonic', side_effect=[0,limit+1]):
                    with self.assertRaises(TimeoutError) as caught:
                        worker.wait(root/name)
                self.assertEqual(caught.exception.timeout_seconds, limit)
                self.assertEqual(caught.exception.timeout_scope, scope)
                self.assertEqual(caught.exception.worker_log, 'prefix replay log')

    def test_explicit_cold_mode_uses_candidate_budget_without_changing_full_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new, checker = self.setup_case(Path(tmp))
            checker.batch_incremental = False
            budgets = []
            original = checker._check
            def check(source, label, *, timeout=None):
                budgets.append(checker.timeout if timeout is None else timeout)
                self.assertEqual(checker.timeout, 1800)
                return original(source, label, timeout=timeout)
            checker._check = check
            with patch('regen.execution.batch_checker.BatchWorker') as worker:
                evidence = check_batch(checker, old, new)
            worker.assert_not_called()
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
            def check(source, label, *, timeout=None):
                self.assertEqual(timeout, 120)
                self.assertEqual(checker.timeout, 1800)
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
