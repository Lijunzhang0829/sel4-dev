"""Continuation carries charged usage/activity and excludes explicitly stopped gaps."""
import tempfile
import unittest

from regen.infrastructure.experiment_activity import ActivityStop, UsageSnapshots, experiment_summary
from regen.infrastructure.experiment_ledger import Ledger


class ContinuationAccountingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 100.
        self.ledger = Ledger(self.tmp.name, 'original', clock=lambda: self.now)
        self.ledger.append('experiment_started')

    def event(self, when, kind, **data):
        self.now = when
        return self.ledger.append(kind, **data)

    def test_fresh_live_and_finished_time_unchanged(self):
        self.now = 120.
        summary = experiment_summary(self.ledger)
        self.assertEqual(summary['elapsed_seconds'], 20)
        self.assertEqual(summary['wall_elapsed_seconds'], 20)
        self.event(140, 'experiment_finished')
        self.now = 1000
        summary = experiment_summary(self.ledger)
        self.assertEqual(summary['elapsed_seconds'], 40)
        self.assertEqual(summary['wall_elapsed_seconds'], 40)

    def test_multiple_finished_segments_and_curve_exclude_downtime(self):
        self.event(130, 'proof_commit', theory='T', nodes=[
            {'uid': 'c', 'kind': 'contract', 'status': 'green'}])
        self.event(150, 'experiment_finished')
        self.event(500, 'experiment_resumed')
        self.event(520, 'proof_commit', theory='T', nodes=[
            {'uid': 'c', 'kind': 'contract', 'status': 'green'},
            {'uid': 'h', 'kind': 'helper', 'status': 'green'}])
        self.event(550, 'experiment_finished')
        self.event(1000, 'experiment_resumed')
        self.event(1010, 'termination', reason='test')
        self.event(1020, 'experiment_finished')
        self.now = 2000
        summary = experiment_summary(self.ledger)
        self.assertEqual(summary['elapsed_seconds'], 120)
        self.assertEqual(summary['wall_elapsed_seconds'], 920)
        self.assertEqual([r['elapsed_seconds'] for r in summary['curve']], [30, 70, 110])
        self.assertEqual([r['wall_elapsed_seconds'] for r in summary['curve']], [30, 420, 910])
        self.assertEqual(summary['curve'][-1]['contracts_green'], 1)
        self.assertEqual(summary['curve'][-1]['helpers_green'], 1)

    def test_live_continuation_uses_clock_not_previous_finish(self):
        self.event(150, 'experiment_finished')
        self.event(500, 'experiment_resumed')
        self.now = 540
        summary = experiment_summary(self.ledger)
        self.assertEqual(summary['elapsed_seconds'], 90)
        self.assertEqual(summary['wall_elapsed_seconds'], 440)

    def test_usage_threads_and_cost_intervals_survive_without_duplicate_charges(self):
        usage = UsageSnapshots(self.ledger)
        scope = {'target_ids': ['c', 'helper:T:h']}
        usage.record('old', {'totalTokens': 100}, scope=scope, source='test')
        self.event(150, 'experiment_finished')
        # Reload the exact persisted ledger, as the continuation does.
        self.ledger = Ledger(self.tmp.name, 'original', clock=lambda: self.now)
        self.event(500, 'experiment_resumed')
        usage = UsageSnapshots(self.ledger)
        usage.record('old', {'totalTokens': 100}, scope=scope, source='test')
        usage.record('new', {'totalTokens': 20}, scope={}, source='test')
        usage.record('new', {'totalTokens': 50}, scope={}, source='test')
        summary = experiment_summary(self.ledger)
        self.assertEqual(summary['usage_accounting']['total_tokens'], 150)
        self.assertEqual(summary['curve'][-1]['total_tokens'], 150)
        self.assertEqual([r['usage']['totalTokens'] for r in summary['cost_intervals']], [100, 20, 30])
        self.assertEqual(summary['cost_intervals'][0]['allocation'], 'shared')
        self.assertEqual(len(summary['usage_by_thread']), 2)

    def test_recovered_activity_ignores_old_content_and_validation_versions(self):
        stop = ActivityStop(self.ledger, 7200)
        self.assertTrue(stop.observe('c', 'by simp', kind='draft'))
        self.assertTrue(stop.observe('c', {'status': 'green'}, kind='validation'))
        self.event(200, 'experiment_finished')
        self.event(10000, 'experiment_resumed')
        restored = ActivityStop(Ledger(self.tmp.name, 'original', clock=lambda: self.now), 7200)
        self.assertEqual(restored.deadline, 17200)
        self.now = 10100
        self.assertFalse(restored.observe('c', 'by simp', kind='draft'))
        self.assertFalse(restored.observe('c', {'status': 'green'}, kind='validation'))
        self.assertEqual(restored.deadline, 17200)
        self.assertTrue(restored.observe('c', 'by auto', kind='draft'))
        self.assertEqual(restored.deadline, 17300)
        self.assertEqual(sum(r['kind'] == 'target_updated' for r in restored.ledger.rows), 3)


if __name__ == '__main__':
    unittest.main()
