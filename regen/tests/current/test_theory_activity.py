import tempfile
import threading
import unittest

from regen.execution.check_tasks import CheckCancelled
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.theory_activity import TheoryActivityStop


class TheoryActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = [0.]
        self.wall = [1000.]
        self.ledger = Ledger(self.temp.name, 'clock-test', clock=lambda: self.wall[0])
        self.clock = TheoryActivityStop(self.ledger, 100, monotonic=lambda: self.now[0])
        self.clock.register({'A': ['a1', 'a2'], 'B': ['b1']})
        self.clock.select('A')
        self.addCleanup(self.clock.close)

    def advance(self, seconds):
        self.now[0] += seconds
        self.wall[0] += seconds

    def state(self, theory='A'):
        return self.clock.snapshot()['theories'][theory]

    def green(self, uid='a1', theory='A', **kw):
        return self.clock.observe(uid, {'status': 'green'}, kind='validation', theory=theory, **kw)

    def test_late_green_cannot_reopen_budget(self):
        self.advance(100.01)
        with self.assertRaises(CheckCancelled):
            self.green()
        self.assertTrue(self.clock.event.is_set())
        self.assertEqual(self.clock.expired_theory, 'A')
        self.assertEqual(self.state()['green_contract_ids'], [])
        self.assertEqual(self.ledger.rows[-2]['data']['disposition'], 'pause')

    def test_first_green_exactly_at_deadline_wins_before_pause(self):
        self.advance(100)
        self.green()
        self.assertFalse(self.clock.event.is_set())
        self.assertEqual(self.state()['idle_seconds'], 0)
        self.advance(100)
        with self.assertRaises(CheckCancelled):
            self.clock.check()
        with self.assertRaises(CheckCancelled):
            self.green('a2')

    def test_only_first_fixed_contract_green_refreshes(self):
        self.advance(30)
        self.clock.observe('a1', 'by simp', kind='draft', theory='A')
        self.clock.observe('helper:A:h', {'status': 'green'}, kind='validation', theory='A')
        self.assertEqual(self.state()['idle_seconds'], 30)
        self.green()
        self.assertEqual(self.state()['idle_seconds'], 0)
        self.advance(20)
        self.green()
        self.clock.observe('a1', {'status': 'sealed'}, kind='validation', theory='A')
        self.assertEqual(self.state()['idle_seconds'], 20)
        self.assertEqual(self.state()['effective_seconds'], 50)
        self.clock.observe('a2', {'status': 'open'}, kind='validation', theory='A')
        self.assertEqual(self.state()['idle_seconds'], 20)

    def test_switch_freezes_does_not_reset_and_other_theory_green_does_not_refresh(self):
        self.advance(70)
        self.clock.select('B')
        self.advance(20)
        self.green('b1', 'B')
        self.clock.mark_sealed('B')
        self.advance(200)
        self.assertEqual(self.state('B')['effective_seconds'], 20)
        self.clock.select('A')
        self.assertEqual(self.state()['idle_seconds'], 70)
        self.advance(30)
        with self.assertRaises(CheckCancelled):
            self.clock.check()

    def test_overlapping_exclusions_are_union_not_double_subtraction(self):
        self.advance(10)
        a = self.clock.begin_exclusion('theory_initialization')
        self.advance(80)
        b = self.clock.begin_exclusion('heap_preparation')
        self.advance(60)
        self.clock.end_exclusion(a)
        self.advance(90)
        self.clock.end_exclusion(b)
        self.advance(20)
        self.assertEqual(self.state()['idle_seconds'], 30)
        with self.assertRaises(ValueError):
            self.clock.end_exclusion(b)
        with self.assertRaises(ValueError):
            self.clock.begin_exclusion('model_says_idle')

    def test_concurrent_model_work_counts_while_native_wait_does_not(self):
        with self.clock.excluded('heap_preparation'):
            self.advance(20)
            self.clock.set_model_overlap(True)
            self.advance(30)
            with self.clock.excluded('provider_stream_retry'):
                self.advance(1000)
            self.clock.set_model_overlap(False)
            self.advance(30)
        self.assertEqual(self.state()['idle_seconds'], 30)

    def test_context_manager_closes_on_error(self):
        with self.assertRaisesRegex(RuntimeError, 'native error'):
            with self.clock.excluded('seal'):
                self.advance(1000)
                raise RuntimeError('native error')
        self.advance(5)
        self.assertEqual(self.state()['idle_seconds'], 5)
        self.assertFalse(self.clock.snapshot()['exclusions'])

    def test_baseline_is_not_progress_and_sealed_theory_does_not_expire(self):
        self.advance(40)
        self.green(baseline=True)
        self.assertEqual(self.state()['idle_seconds'], 40)
        self.clock.register({'A': ['a1', 'a2'], 'B': ['b1']}, baseline_green=['a1', 'a2'])
        self.clock.mark_sealed('A')
        self.advance(1000)
        self.clock.check()
        self.assertTrue(self.state()['completed_contracts'])
        self.assertEqual(self.state()['idle_seconds'], 40)
        self.assertIsNone(self.clock.deadline)
        self.assertFalse(any(r['kind'] == 'theory_contract_first_green' for r in self.ledger.rows))

    def test_helper_work_after_last_contract_can_still_expire_before_seal(self):
        self.green()
        self.green('a2')
        self.assertTrue(self.state()['completed_contracts'])
        self.advance(100)
        with self.assertRaises(CheckCancelled):
            self.clock.check()

    def test_seal_requires_known_and_proven_theory(self):
        with self.assertRaisesRegex(ValueError, 'unregistered'):
            self.clock.mark_sealed('unknown')
        with self.assertRaisesRegex(ValueError, 'unproved'):
            self.clock.mark_sealed('A')

    def test_resume_preserves_budgets_and_green_without_downtime_charge(self):
        self.advance(25)
        self.green()
        self.advance(40)
        self.clock.close()
        self.advance(50000)
        resumed = TheoryActivityStop(self.ledger, 100, monotonic=lambda: self.now[0])
        self.addCleanup(resumed.close)
        self.assertIsNone(resumed.snapshot()['active_theory'])
        resumed.register({'A': ['a1', 'a2'], 'B': ['b1']})
        resumed.select('A')
        self.assertEqual(resumed.snapshot()['theories']['A']['idle_seconds'], 40)
        resumed.observe('a1', {'status': 'sealed'}, kind='validation', theory='A')
        self.advance(60)
        with self.assertRaises(CheckCancelled):
            resumed.check()
        with self.assertRaisesRegex(ValueError, 'policy changed'):
            TheoryActivityStop(self.ledger, 200, monotonic=lambda: self.now[0])

    def test_sealed_state_persists_and_unclean_recovery_marks_unknown_tail(self):
        self.green()
        self.green('a2')
        self.clock.mark_sealed('A')
        self.advance(20)
        self.clock.check()
        # No close boundary: mimic a lost process after the latest checkpoint.
        resumed = TheoryActivityStop(self.ledger, 100, monotonic=lambda: self.now[0])
        self.addCleanup(resumed.close)
        self.assertTrue(resumed.snapshot()['theories']['A']['sealed'])
        started = [r for r in self.ledger.rows if r['kind'] == 'theory_activity_started'][-1]
        self.assertTrue(started['data']['unobserved_shutdown_tail'])
        resumed.select('A')
        self.advance(1000)
        resumed.check()

    def test_expired_resume_does_not_grant_fresh_budget(self):
        self.advance(100)
        with self.assertRaises(CheckCancelled):
            self.clock.check()
        self.clock.close()
        resumed = TheoryActivityStop(self.ledger, 100, monotonic=lambda: self.now[0])
        self.addCleanup(resumed.close)
        with self.assertRaises(CheckCancelled):
            resumed.check()

    def test_plan_frozen_and_unregistered_green_cannot_reset(self):
        with self.assertRaisesRegex(ValueError, 'frozen'):
            self.clock.register({'A': ['a1'], 'B': ['a2', 'b1']})
        with self.assertRaisesRegex(ValueError, 'unregistered'):
            self.clock.select('C')
        self.advance(20)
        self.green('foreign', 'A')
        self.green('a2', 'B')
        self.assertEqual(self.state()['idle_seconds'], 20)

    def test_wall_clock_adjustment_does_not_change_elapsed(self):
        self.advance(10)
        self.wall[0] -= 10000
        self.clock.check()
        self.assertEqual(self.state()['idle_seconds'], 10)
        self.wall[0] += 30000
        self.clock.check()
        self.assertEqual(self.state()['idle_seconds'], 10)

    def test_watchdog_pauses_during_generation_but_not_exclusion(self):
        self.clock.poll_seconds = .01
        abort = threading.Event()
        with self.clock.excluded('runtime_setup'):
            self.clock.start(abort=abort.set)
            self.advance(1000)
            self.assertFalse(abort.wait(.03))
        self.advance(100)
        self.assertTrue(abort.wait(2))
        self.assertTrue(self.clock.event.is_set())

    def test_concurrent_green_notifications_are_single_progress(self):
        threads = [threading.Thread(target=self.green) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(r['kind'] == 'theory_contract_first_green' for r in self.ledger.rows), 1)
        self.assertEqual(self.state()['green_contract_ids'], ['a1'])

    def test_no_selected_theory_during_initialization_has_no_generation_time(self):
        self.clock.select(None)
        self.advance(1000)
        self.clock.check()
        self.assertEqual(self.state()['effective_seconds'], 0)
        self.clock.select('A')
        self.advance(5)
        self.assertEqual(self.state()['effective_seconds'], 5)


if __name__ == '__main__':
    unittest.main()
