"""Clock progress is atomic with accepted journal publication, not HEAD return."""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.check_tasks import Cancellation, CheckCancelled, CURRENT_TASK
from regen.execution.fact_context import manifest
from regen.execution.proof_commits import ProofCommits, durable
from regen.execution.repository_execution import context_graph, validator
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.generation_activity_bridge import GenerationActivityBridge
from regen.infrastructure.progress_events import observe_events
from regen.infrastructure.theory_activity import TheoryActivityStop
from regen.tests.current.test_proof_commits import fixture, check


class ActivityPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = [0.]
        self.ledger = Ledger(self.root / 'ledger', 'pub', clock=lambda: self.now[0])
        self.stage = TheoryActivityStop(self.ledger, 100, monotonic=lambda: self.now[0])
        self.addCleanup(self.stage.close)
        self.stage.register({'Generated_Demo': ['contract']})
        self.stage.select('Generated_Demo')
        self.bridge = GenerationActivityBridge(self.stage)
        self.package, self.initial = fixture()
        self.journal = ProofCommits(self.root / 'acceptance', {'run': 'r'}, validator(self.package))
        self.journal.append('initial', self.initial, {'check': check()})

    def green(self, uid='contract'):
        state = deepcopy(self.initial)
        state['proofs'] = {uid: 'by simp'}
        state['accepted_order'] = [uid]
        state['graph'] = context_graph(self.initial['graph'], [uid])
        evidence = {'node_id': uid, 'check': check(), 'sealable_generation': True,
                    'visibility': manifest(self.initial['graph'], {}, uid)}
        return state, evidence

    def append(self, uid='contract'):
        state, evidence = self.green(uid)
        with observe_events(self.bridge.handle, publication_guard=self.bridge.acceptance):
            return self.journal.append('green', state, evidence)

    def clock(self):
        return self.stage.snapshot()['theories']['Generated_Demo']

    def test_green_before_threshold_survives_slow_head_projection(self):
        def delayed(path, value, **kwargs):
            if path.name == 'HEAD.json':
                self.assertEqual(self.clock()['green_contract_ids'], ['contract'])
                self.now[0] = 110  # The old timer would falsely expire at return.
                self.stage.check()
            return durable(path, value, **kwargs)
        self.now[0] = 90
        with patch('regen.execution.proof_commits.durable', side_effect=delayed):
            self.append()
        self.assertFalse(self.stage.event.is_set())
        self.assertEqual(self.clock()['idle_seconds'], 20)
        self.assertEqual(self.clock()['effective_seconds'], 110)

    def test_head_failure_preserves_authoritative_progress_and_task_publication(self):
        token = Cancellation()
        marker = CURRENT_TASK.set(token)
        self.addCleanup(CURRENT_TASK.reset, marker)
        def fail_head(path, value, **kwargs):
            if path.name == 'HEAD.json':
                raise OSError('projection failed after acceptance')
            return durable(path, value, **kwargs)
        self.now[0] = 90
        with patch('regen.execution.proof_commits.durable', side_effect=fail_head):
            with self.assertRaisesRegex(OSError, 'projection failed'):
                self.append()
        self.assertEqual(self.clock()['green_contract_ids'], ['contract'])
        self.assertTrue(token.published)
        self.assertEqual(self.journal.replay()['proofs'], {'contract': 'by simp'})

    def test_failed_authoritative_write_does_not_refresh(self):
        token = Cancellation(); marker = CURRENT_TASK.set(token)
        self.addCleanup(CURRENT_TASK.reset, marker)
        def fail_commit(path, value, **kwargs):
            if path.parent.name == 'commits':
                raise OSError('no accepted record written')
            return durable(path, value, **kwargs)
        self.now[0] = 90
        with patch('regen.execution.proof_commits.durable', side_effect=fail_commit):
            with self.assertRaisesRegex(OSError, 'no accepted record'):
                self.append()
        self.assertEqual(self.clock()['green_contract_ids'], [])
        self.assertEqual(self.clock()['idle_seconds'], 90)
        self.assertFalse(token.published)
        self.assertFalse((self.root / 'acceptance/commits/000002.json').exists())

    def test_visible_commit_io_failure_keeps_existing_cancellation_semantics(self):
        token = Cancellation(); marker = CURRENT_TASK.set(token)
        self.addCleanup(CURRENT_TASK.reset, marker)
        def fail_after_link(path, value, **kwargs):
            result = durable(path, value, **kwargs)
            if path.parent.name == 'commits':
                raise OSError('directory fsync uncertain after publish')
            return result
        self.now[0] = 90
        with patch('regen.execution.proof_commits.durable', side_effect=fail_after_link):
            with self.assertRaisesRegex(OSError, 'fsync uncertain'):
                self.append()
        self.assertTrue(token.published)
        self.assertFalse(token.cancel())
        self.assertEqual(self.clock()['green_contract_ids'], [])
        self.assertEqual(self.clock()['idle_seconds'], 90)
        self.assertEqual(self.journal.replay()['proofs'], {'contract': 'by simp'})

    def test_expired_clock_refuses_new_authoritative_commit(self):
        self.now[0] = 100.01
        with self.assertRaises(CheckCancelled):
            self.append()
        self.assertFalse((self.root / 'acceptance/commits/000002.json').exists())
        self.assertEqual(self.clock()['green_contract_ids'], [])

    def test_exact_boundary_first_contract_acceptance_wins(self):
        self.now[0] = 100
        self.append()
        self.assertFalse(self.stage.event.is_set())
        self.assertEqual(self.clock()['idle_seconds'], 0)
        self.assertEqual(self.clock()['green_contract_ids'], ['contract'])

    def test_helper_publication_cannot_refresh_contract_clock(self):
        self.now[0] = 90
        self.append('helper')
        self.assertEqual(self.clock()['green_contract_ids'], [])
        self.assertEqual(self.clock()['idle_seconds'], 90)
        self.now[0] = 100
        with self.assertRaises(CheckCancelled):
            self.stage.check()

    def test_initial_and_attempt_records_do_not_invoke_publication_guard(self):
        observed = []
        @contextmanager
        def guard(record):
            observed.append(record['event'])
            yield
        other = ProofCommits(self.root / 'other', {'run': 'r'}, validator(self.package))
        with observe_events(None, publication_guard=guard):
            other.append('initial', self.initial, {'check': check()})
            state = deepcopy(self.initial); state['rounds']['helper'] = 1
            other.append('attempt', state, {})
        self.assertEqual(observed, [])

    def test_nested_event_observers_restore_guard_binding(self):
        observed = []
        @contextmanager
        def guard(record):
            observed.append(record['event'])
            yield
        state, evidence = self.green()
        with observe_events(None, publication_guard=guard):
            with observe_events(None):
                other = ProofCommits(self.root / 'nested', {'run': 'r'}, validator(self.package))
                other.append('initial', self.initial, {'check': check()})
                other.append('green', state, evidence)
            self.journal.append('green', state, evidence)
        self.assertEqual(observed, ['green'])


if __name__ == '__main__':
    unittest.main()
