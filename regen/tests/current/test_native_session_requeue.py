"""Cleanup-bound native session recovery; no model/native subprocess is run."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from regen.execution.check_tasks import (NativePreparationError, NativeCleanupError,
    NativeWorkerUnavailable, RecoverableNativePreparationError, ImplementationChangedError)
from regen.execution.proof_batches import _raise_clean_native_recovery, check_batch, prepare_batch
from regen.execution.batch_checker import body
from regen.infrastructure.progress_events import emit_event
from regen.planning.parallel_file_planner import _native_recovery_state
from regen.tests.current.test_incremental_batches import MemoryWorker
from regen.tests.current.test_proof_batches import Checker
from regen.tests.current.test_proof_commits import fixture
from regen.tests.current import test_parallel_provider_cooldown as coordinator_fixture


def incident(root, suffix='one', *, discarded=True):
    directory = root/suffix
    directory.mkdir(exist_ok=True)
    worker = SimpleNamespace(root=directory, name='fixture-worker', closed=discarded)
    error = NativeWorkerUnavailable('native process exited', worker=worker, waiting_for='ready')
    prefix = 'theory Prefix\nimports Main\nbegin\nlemma retained: True by simp\nend\n'
    try:
        _raise_clean_native_recovery(error, worker, prefix, directory,
            {'prefix_sha256':hashlib.sha256(body(prefix).encode()).hexdigest()})
    except RecoverableNativePreparationError as recovery:
        return recovery
    raise AssertionError('fixture incident did not produce a recovery receipt')


class NativeIncidentTests(unittest.TestCase):
    def test_launch_cleanup_failure_keeps_unassigned_owner_and_pauses(self):
        from regen.execution.persistent_checker import PrefixWorker
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = object.__new__(PrefixWorker)
            worker.root, worker.name, worker.closed = root, 'owned-launch-fixture', False
            worker.checker = SimpleNamespace(owner_lease_seconds=60)
            worker.wait = Mock(side_effect=NativeWorkerUnavailable(
                'launch died', worker=worker, waiting_for='ready'))
            worker.close = Mock(side_effect=RuntimeError('cannot confirm container removal'))
            with patch('regen.execution.persistent_checker.threading.Thread'), \
                    patch('regen.execution.persistent_checker.subprocess.Popen'), \
                    patch('regen.execution.persistent_checker.atexit.register'):
                with self.assertRaises(NativeCleanupError) as caught:
                    worker._launch(['docker','run','/workspace/verification/isabelle/bin/isabelle'])
            self.addCleanup(worker.log.close)
            self.assertIs(caught.exception.native_worker, worker)
            package, old = fixture()
            candidate = prepare_batch(package, old, {'proofs':{'helper':'by simp'}})
            checker = Checker(); checker.batch_incremental = True; checker.work_dir = root
            with patch('regen.execution.batch_checker.BatchWorker', side_effect=caught.exception):
                with self.assertRaises(NativeCleanupError):
                    check_batch(checker, old, candidate)
            self.assertIs(checker._batch_worker, worker)
            self.assertEqual(old['proofs'], {})
            worker.close.assert_called_once()

    def test_executor_roundtrip_keeps_typed_receipt_and_accepted_proofs(self):
        from regen.tests.current import test_parallel_file_planner as controller_fixture
        control = controller_fixture.ParallelControllerTests()
        control.setUp(); self.addCleanup(control.doCleanups)
        before = deepcopy(control.controller._state('Target')[1]['proofs'])
        error = incident(control.root)
        with patch('regen.execution.proof_batches.check_batch', side_effect=error):
            with self.assertRaises(RecoverableNativePreparationError) as caught:
                control.controller.handle('proof_action', {'theory':'Target',
                    'proposal':{'action':'submit_batch','proofs':{'target_contract':'by simp'}}},
                    control.metadata)
        self.assertEqual(caught.exception.record(), error.record())
        self.assertEqual(control.controller._state('Target')[1]['proofs'], before)
        row = control.executor.get(next(iter(control.controller.task_ids)))
        self.assertEqual(row['native_recovery'], error.record())
        self.assertEqual(row['error_type'], 'RecoverableNativePreparationError')

    def test_typed_launch_loss_preserves_committed_prefix_and_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package, initial = fixture()
            old = prepare_batch(package, initial, {'proofs':{'helper':'by simp'}})
            candidate = prepare_batch(package, old, {'proofs':{'contract':'by simp'}})
            before = deepcopy(old)
            checker = Checker(); checker.batch_incremental = True; checker.work_dir = root
            created = []
            def launch(checker, source, names, imports):
                worker = MemoryWorker(checker, source, names, imports)
                worker.root = root; worker.name = 'failed-prefix-worker'
                (root/'worker.log').write_text('native service exited before ready')
                worker.close()
                created.append(worker)
                raise NativeWorkerUnavailable('worker exited', worker=worker, waiting_for='ready')
            with patch('regen.execution.batch_checker.BatchWorker', side_effect=launch):
                with self.assertRaises(RecoverableNativePreparationError) as raised:
                    check_batch(checker, old, candidate)
            receipt = raised.exception.read_receipt(root)
            self.assertTrue(created[0].closed)
            self.assertEqual(old, before)
            self.assertEqual(checker.calls, 0)
            self.assertIsNone(checker._batch_worker)
            saved = Path(receipt['prefix_source_path']).read_text()
            self.assertIn('helper', saved)
            self.assertNotIn('lemma contract', saved)
            self.assertTrue(next(root.glob('incremental-failure-*/candidate.thy')).exists())

    def test_receipt_tamper_and_unconfirmed_cleanup_cannot_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(NativeCleanupError):
                incident(root, 'unclean', discarded=False)
            error = incident(root)
            Path(error.recovery['prefix_source_path']).write_text('changed prefix')
            with self.assertRaisesRegex(NativePreparationError, 'prefix changed'):
                error.read_receipt(root)

    def test_generic_preparation_or_native_proof_failure_has_no_recovery_type(self):
        self.assertNotIsInstance(NativePreparationError('missing heap'), RecoverableNativePreparationError)
        self.assertNotIsInstance(ImplementationChangedError('code drift'), RecoverableNativePreparationError)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = SimpleNamespace(root=root, name='fixture', closed=True)
            value = _raise_clean_native_recovery(RuntimeError('native ERROR'), worker,
                'theory T\nbegin\n\nend\n', root, {})
            self.assertIsNone(value)
            self.assertFalse((root/'native-recovery.json').exists())


class NativeCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = coordinator_fixture.ProviderCooldownTests()
        self.coordinator.setUp()
        self.addCleanup(self.coordinator.doCleanups)

    def test_clean_native_failure_requeues_after_healthy_peer_without_budget_reset(self):
        c = self.coordinator
        order = []
        retained = c.root/'retained-proof.json'; retained.write_text('unchanged accepted proof')
        def session(theory):
            order.append(theory)
            emit_event('theory_generation_started')
            c.now += 2
            if theory == 'A' and c.attempts[theory] == 1:
                raise incident(c.root)
            if theory == 'A':
                # A switched entry may legitimately use another certified prefix.
                emit_event('native_request_started', request_id='verified-response',
                    binding={'prefix_sha256':'a'*64, 'request_sha256':'b'*64, 'action':'check'})
                emit_event('native_request_finished', request_id='verified-response', outcome='completed')
            return c.seal(theory)
        result = c.exercise(session, ('A','B'), workers=1, retries=0)
        self.assertEqual(order, ['A','B','A'])
        self.assertEqual(result['status'], 'selected_theories_sealed')
        self.assertFalse(result['failures'])
        self.assertEqual(retained.read_text(), 'unchanged accepted proof')
        self.assertEqual(result['generation_budget']['effective_seconds'], 6)
        events = [r['kind'] for r in c.budget.ledger.rows]
        self.assertEqual(events.count('native_recovery_scheduled'), 1)
        self.assertEqual(events.count('native_recovery_started'), 1)
        self.assertEqual(events.count('native_recovery_succeeded'), 1)

    def test_second_loss_before_native_response_pauses_instead_of_looping(self):
        c = self.coordinator
        def session(theory):
            raise incident(c.root, str(c.attempts[theory]))
        with self.assertRaises(RecoverableNativePreparationError):
            c.exercise(session, workers=1, retries=0)
        self.assertEqual(c.attempts, {'A':2})
        self.assertEqual(c.budget.reason, 'native_recovery_exhausted')

    def test_query_response_does_not_grant_another_native_replacement(self):
        c = self.coordinator
        def session(theory):
            emit_event('theory_generation_started')
            if c.attempts[theory] > 1:
                emit_event('native_request_started', request_id='observation',
                    binding={'prefix_sha256':'a'*64, 'request_sha256':'b'*64, 'action':'query'})
                emit_event('native_request_finished', request_id='observation', outcome='completed')
            raise incident(c.root, str(c.attempts[theory]))
        with self.assertRaises(RecoverableNativePreparationError):
            c.exercise(session, workers=1, retries=0)
        self.assertEqual(c.attempts, {'A':2})
        self.assertEqual(c.budget.reason, 'native_recovery_exhausted')
        self.assertNotIn('native_recovery_succeeded', [r['kind'] for r in c.budget.ledger.rows])

    def test_clean_yield_keeps_pending_recovery_without_interruption_pause(self):
        c = self.coordinator; order = []
        def session(theory):
            emit_event('theory_generation_started')
            order.append((theory, c.attempts[theory]))
            if theory == 'A' and c.attempts[theory] == 1:
                raise incident(c.root)
            if theory == 'B' and c.attempts[theory] == 1:
                return {'theory':theory,'status':'yielded','switch':{'to_theory':'A'}}
            if theory == 'A' and c.attempts[theory] == 2:
                return {'theory':theory,'status':'yielded','switch':{'to_theory':'B'}}
            if theory == 'A':
                emit_event('native_request_started', request_id='after-yield',
                    binding={'prefix_sha256':'a'*64, 'request_sha256':'b'*64, 'action':'check'})
                emit_event('native_request_finished', request_id='after-yield', outcome='completed')
            return c.seal(theory)
        result = c.exercise(session, ('A','B'), workers=1, retries=0)
        self.assertEqual(order, [('A',1),('B',1),('A',2),('B',2),('A',3)])
        self.assertEqual(result['status'], 'selected_theories_sealed')
        events = [r['kind'] for r in c.budget.ledger.rows]
        self.assertEqual(events.count('native_recovery_scheduled'), 1)
        self.assertEqual(events.count('native_recovery_session_returned'), 1)
        self.assertEqual(events.count('native_recovery_succeeded'), 1)

    def test_clean_yield_does_not_reset_failure_retry_allowance(self):
        c = self.coordinator
        def session(theory):
            if c.attempts[theory] == 2:
                return {'theory':theory,'status':'yielded','switch':{'to_theory':theory}}
            raise incident(c.root, str(c.attempts[theory]))
        with self.assertRaises(RecoverableNativePreparationError):
            c.exercise(session, workers=1, retries=0)
        self.assertEqual(c.attempts, {'A':3})
        self.assertEqual(c.budget.reason, 'native_recovery_exhausted')

    def test_failed_cleanup_pauses_without_replacement(self):
        c = self.coordinator
        def session(theory):
            raise NativeCleanupError('owned worker still running')
        with self.assertRaises(NativeCleanupError):
            c.exercise(session, workers=1, retries=3)
        self.assertEqual(c.attempts, {'A':1})
        self.assertEqual(c.budget.reason, 'native_cleanup_failed')

    def test_reserved_recovery_state_survives_replay_and_needs_real_response(self):
        rows = [{'kind':'native_recovery_scheduled','data':{'theory':'A','incident_id':'i','receipt':{}}},
                {'kind':'native_recovery_started','data':{'theory':'A','incident_id':'i'}},
                {'kind':'unrelated_success','data':{'theory':'A'}}]
        state = _native_recovery_state(rows, 'A')
        self.assertTrue(state['started'])
        self.assertEqual(state['incident_id'], 'i')
        rows.append({'kind':'native_recovery_succeeded','data':{'theory':'A','incident_id':'i'}})
        state = _native_recovery_state(rows, 'A')
        self.assertIsNone(state['incident_id'])
        self.assertIn('i', state['seen'])
        rows.append({'kind':'native_recovery_stopped','data':{'theory':'A', 'incident_id':'i',
            'reason':'native_recovery_duplicate_incident'}})
        self.assertEqual(_native_recovery_state(rows, 'A')['stopped'], 'native_recovery_duplicate_incident')


if __name__ == '__main__':
    unittest.main()
