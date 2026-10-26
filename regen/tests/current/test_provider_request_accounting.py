"""Successful retry corrections preserve raw consumption and concurrent work."""
from copy import deepcopy
import itertools
import random
import unittest

from regen.execution.check_tasks import CheckCancelled
from regen.infrastructure.effective_time_budget import EffectiveTimeBudget, budget_journal_summary
from regen.infrastructure.provider_request_accounting import ProviderRequestAccounting
from regen.tests.current.test_effective_time_budget import MemoryLedger


class RequestAccountingTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.
        self.ledger = MemoryLedger()
        self.stage = self.make()
        self.stage.register({'A': ['a'], 'B': ['b']})

    def make(self, **options):
        return EffectiveTimeBudget(self.ledger, 1000, monotonic=lambda: self.now, **options)

    def begin(self, worker, uid):
        self.stage.select(worker, worker_id=worker)
        self.stage.begin_provider_request(worker, uid)

    def finish(self, worker, uid, status='success'):
        self.stage.finish_provider_request(worker, uid, outcome=status)

    def test_only_last_failed_request_excluded_after_completed_retry(self):
        self.begin('A', 'completed'); self.now += 3; self.finish('A', 'completed')
        self.begin('A', 'failed'); self.now += 7; self.finish('A', 'failed', 'failed')
        self.assertEqual(self.stage.snapshot()['effective_seconds'], 10)
        self.begin('A', 'retry'); self.now += 5; self.finish('A', 'retry')
        receipt = self.stage.credit_provider_request('failed', 'retry')
        snap = self.stage.snapshot()
        self.assertEqual((snap['raw_effective_seconds'], snap['effective_seconds']), (15, 8))
        self.assertEqual((snap['raw_worker_seconds'], snap['worker_seconds']), (15, 8))
        self.assertEqual(receipt['effective_seconds'], 7)
        self.assertEqual(snap['provider_request_accounting']['segments'], [])
        self.assertEqual(self.ledger.rows[-1]['data']['correction'], receipt)
        self.assertEqual(budget_journal_summary(self.ledger)['effective_seconds'], 8)

    def test_healthy_peer_keeps_union_while_failed_worker_cost_is_excluded(self):
        self.begin('A', 'failed'); self.begin('B', 'healthy')
        self.now += 10
        self.finish('A', 'failed', 'failed'); self.finish('B', 'healthy')
        self.stage.deactivate('B')
        self.begin('A', 'retry'); self.now += 2; self.finish('A', 'retry')
        receipt = self.stage.credit_provider_request('failed', 'retry')
        self.assertEqual(receipt['effective_seconds'], 0)
        self.assertEqual(receipt['worker_seconds'], 10)
        self.assertEqual(self.stage.snapshot()['effective_seconds'], 12)
        self.assertEqual(self.stage.snapshot()['raw_worker_seconds'], 22)
        self.assertEqual(self.stage.snapshot()['worker_seconds'], 12)

    def test_two_failed_lanes_refund_union_only_after_both_recover_in_either_order(self):
        for order in (('A', 'B'), ('B', 'A')):
            with self.subTest(order=order):
                self.setUp()
                self.begin('A', 'a-failed'); self.begin('B', 'b-failed')
                self.now += 10
                for lane in order:
                    self.finish(lane, lane.lower()+'-failed', 'failed'); self.stage.deactivate(lane)
                first, second = order
                self.begin(first, 'retry-first'); self.now += 2; self.finish(first, 'retry-first')
                one = self.stage.credit_provider_request(first.lower()+'-failed', 'retry-first')
                self.stage.deactivate(first)
                self.begin(second, 'retry-second'); self.now += 3; self.finish(second, 'retry-second')
                two = self.stage.credit_provider_request(second.lower()+'-failed', 'retry-second')
                self.assertEqual((one['effective_seconds'], two['effective_seconds']), (0, 10))
                self.assertEqual(self.stage.snapshot()['effective_seconds'], 5)
                self.assertEqual(self.stage.snapshot()['raw_effective_seconds'], 15)
                self.assertEqual(self.stage.snapshot()['worker_seconds'], 5)
                self.assertEqual(self.stage.snapshot()['raw_worker_seconds'], 25)

    def test_native_exclusion_is_never_refunded_twice(self):
        self.begin('A', 'failed'); self.now += 5
        with self.stage.exclude('prefix_preparation', worker_id='A'):
            self.now += 50
        self.now += 5; self.finish('A', 'failed', 'failed')
        self.begin('A', 'retry'); self.now += 3; self.finish('A', 'retry')
        receipt = self.stage.credit_provider_request('failed', 'retry')
        self.assertEqual(receipt['effective_seconds'], 10)
        self.assertEqual(self.stage.snapshot()['raw_effective_seconds'], 13)
        self.assertEqual(self.stage.snapshot()['effective_seconds'], 3)

    def test_planning_and_unlabelled_completed_work_remain_charged(self):
        self.ledger = MemoryLedger(); self.stage = self.make(include_planning=True)
        self.stage.begin_planning(); self.now += 20; self.stage.end_planning()
        self.stage.register({'A': ['a']})
        self.stage.select('A', worker_id='A'); self.now += 4
        self.begin('A', 'failed'); self.now += 10; self.finish('A', 'failed', 'failed')
        self.begin('A', 'retry'); self.now += 5; self.finish('A', 'retry')
        self.stage.credit_provider_request('failed', 'retry')
        snap = self.stage.snapshot()
        self.assertEqual((snap['raw_effective_seconds'], snap['effective_seconds'], snap['planning_seconds']), (39, 29, 20))

    def test_planning_contributor_protects_union_even_inside_request_segment(self):
        accounting = ProviderRequestAccounting()
        accounting.begin('A', 'A', 'failed'); accounting.record(10, {'A': 'A'}, planning=True)
        accounting.finish('A', 'failed', 'failed')
        accounting.begin('A', 'A', 'retry'); accounting.finish('A', 'retry', 'success')
        receipt = accounting.credit('failed', 'retry')
        self.assertEqual((receipt['effective_seconds'], receipt['worker_seconds']), (0, 10))

    def test_pending_failed_request_survives_resume_without_automatic_credit(self):
        self.begin('A', 'failed'); self.now += 10; self.finish('A', 'failed', 'failed')
        self.stage.deactivate('A'); self.stage.cancel('network'); self.stage.close()
        self.now += 100000
        self.stage = self.make(resume_authorization='same authorized experiment')
        self.assertEqual(self.stage.snapshot()['effective_seconds'], 10)
        self.begin('A', 'retry'); self.now += 4; self.finish('A', 'retry')
        self.stage.credit_provider_request('failed', 'retry')
        self.assertEqual(self.stage.snapshot()['effective_seconds'], 4)
        self.stage.deactivate('A'); self.stage.close()
        self.stage = self.make(resume_authorization='continue')
        before = len(self.ledger.rows)
        self.stage.credit_provider_request('failed', 'retry')
        self.assertEqual(len(self.ledger.rows), before)
        self.assertEqual(self.stage.snapshot()['raw_effective_seconds'], 14)

    def test_recovery_cannot_turn_an_unfinished_request_into_a_refundable_failure(self):
        self.begin('A', 'unfinished'); self.now += 10; self.stage.cancel('process_stopped'); self.stage.close()
        self.stage = self.make(resume_authorization='recover')
        self.assertEqual(self.stage.snapshot()['provider_request_accounting']['requests']['unfinished']['status'], 'unresolved')
        self.begin('A', 'retry'); self.now += 1; self.finish('A', 'retry')
        with self.assertRaises(ValueError):
            self.stage.credit_provider_request('unfinished', 'retry')
        self.assertEqual(self.stage.snapshot()['effective_seconds'], 11)

    def test_zero_cost_complete_response_is_valid_success_evidence(self):
        self.begin('A', 'failed'); self.now += 10; self.finish('A', 'failed', 'failed')
        self.begin('A', 'retry'); self.finish('A', 'retry')
        self.stage.credit_provider_request('failed', 'retry')
        self.assertEqual(self.stage.snapshot()['effective_seconds'], 0)
        self.assertEqual(self.stage.snapshot()['raw_effective_seconds'], 10)

    def test_retry_failure_or_retained_outcome_never_credits(self):
        for outcome in ('failed', 'retained'):
            with self.subTest(outcome=outcome):
                self.setUp()
                self.begin('A', 'failed'); self.now += 10; self.finish('A', 'failed', 'failed')
                self.begin('A', 'retry'); self.now += 5; self.finish('A', 'retry', outcome)
                with self.assertRaises(ValueError):
                    self.stage.credit_provider_request('failed', 'retry')
                self.assertEqual(self.stage.snapshot()['effective_seconds'], 15)

    def test_unknown_duplicate_and_cross_worker_boundaries_rejected(self):
        with self.assertRaises(ValueError):
            self.stage.begin_provider_request('missing', 'x')
        self.begin('A', 'failed')
        with self.assertRaises(ValueError):
            self.stage.begin_provider_request('A', 'second')
        with self.assertRaises(ValueError):
            self.stage.finish_provider_request('B', 'failed')
        self.finish('A', 'failed', 'failed')
        self.begin('B', 'other'); self.finish('B', 'other')
        with self.assertRaises(ValueError):
            self.stage.credit_provider_request('failed', 'other')
        with self.assertRaises(ValueError):
            self.stage.credit_provider_request('unknown', 'other')

    def test_old_success_cannot_be_used_as_retry_and_credit_pair_cannot_change(self):
        self.begin('A', 'old'); self.finish('A', 'old')
        self.begin('A', 'failed'); self.now += 5; self.finish('A', 'failed', 'failed')
        with self.assertRaises(ValueError):
            self.stage.credit_provider_request('failed', 'old')
        self.begin('A', 'retry'); self.finish('A', 'retry')
        self.stage.credit_provider_request('failed', 'retry')
        with self.assertRaises(ValueError):
            self.stage.credit_provider_request('failed', 'old')

    def test_exhausted_budget_cannot_borrow_an_unconfirmed_future_refund(self):
        self.begin('A', 'failed'); self.now += 600; self.finish('A', 'failed', 'failed')
        self.begin('A', 'retry'); self.now += 400; self.finish('A', 'retry')
        with self.assertRaises(CheckCancelled):
            self.stage.credit_provider_request('failed', 'retry')
        self.assertEqual(self.stage.snapshot()['excluded_failed_request_seconds'], 0)

    def test_credit_receipt_and_state_are_one_atomic_checkpoint(self):
        self.begin('A', 'failed'); self.now += 10; self.finish('A', 'failed', 'failed')
        self.begin('A', 'retry'); self.finish('A', 'retry')
        before = deepcopy(self.ledger.rows)
        original = self.ledger.append
        def append(kind, **data):
            if data.get('boundary') == 'provider_request_retry_credit':
                raise OSError('fixture interrupted publication')
            return original(kind, **data)
        self.ledger.append = append
        with self.assertRaises(OSError):
            self.stage.credit_provider_request('failed', 'retry')
        self.assertEqual(self.ledger.rows, before)
        self.ledger.append = original
        self.stage = self.make(resume_authorization='recover after failed publication')
        self.assertEqual(self.stage.snapshot()['effective_seconds'], 10)
        self.stage.credit_provider_request('failed', 'retry')
        self.assertEqual(self.stage.snapshot()['effective_seconds'], 0)

    def test_imported_old_intervals_are_pending_and_preserve_original_consumption(self):
        accounting = ProviderRequestAccounting(20, 30, {'A': 20, 'B': 10})
        accounting.import_failed_requests([
            dict(request_id='old-A', worker_id='A', theory='A', failure={'transcript_sha256': 'a'}),
            dict(request_id='old-B', worker_id='B', theory='B')], [
            dict(seconds=10, planning=False, contributors=[dict(worker_id='A', theory='A', request_id='old-A')]),
            dict(seconds=10, planning=False, contributors=[dict(worker_id='A', theory='A', request_id='old-A'),
                                                          dict(worker_id='B', theory='B', request_id='old-B')])])
        state = accounting.state()
        restored = ProviderRequestAccounting.restore(state, effective_seconds=20, worker_seconds=30,
                                                     theory_seconds={'A': 20, 'B': 10})
        self.assertEqual(restored.summary()['excluded_failed_request_seconds'], 0)
        for lane in ('A', 'B'):
            restored.begin(lane, lane, 'new-'+lane); restored.finish(lane, 'new-'+lane, 'success')
        one = restored.credit('old-A', 'new-A'); two = restored.credit('old-B', 'new-B')
        self.assertEqual((one['effective_seconds'], two['effective_seconds']), (10, 10))
        self.assertEqual(restored.raw_effective_seconds, 20)
        with self.assertRaises(ValueError):
            accounting.import_failed_requests([], [])

    def test_tampered_pending_accounting_cannot_resume(self):
        self.begin('A', 'failed'); self.now += 5; self.finish('A', 'failed', 'failed')
        self.stage.deactivate('A'); self.stage.close()
        self.ledger.rows[-1]['data']['state']['provider_request_accounting']['raw_effective_seconds'] = 1
        with self.assertRaisesRegex(ValueError, 'raw and net'):
            self.make(resume_authorization='must fail')

    def test_multiple_lanes_and_planning_match_independent_union_oracle(self):
        rng = random.Random(781)
        for order in itertools.permutations(('A', 'B', 'C')):
            accounting = ProviderRequestAccounting()
            for lane in ('A', 'B', 'C'):
                accounting.begin(lane, lane, 'failed-'+lane)
            segments = []
            for _ in range(30):
                lanes = {lane:lane for lane in ('A', 'B', 'C') if rng.getrandbits(1)}
                if rng.getrandbits(1):
                    lanes['healthy'] = 'healthy'
                planning = bool(rng.getrandbits(1)); elapsed = rng.randint(1, 9)
                segments.append((elapsed, set(lanes), planning))
                accounting.record(elapsed, lanes, planning)
            for lane in ('A', 'B', 'C'):
                accounting.finish(lane, 'failed-'+lane, 'failed')
                accounting.begin(lane, lane, 'retry-'+lane)
                accounting.finish(lane, 'retry-'+lane, 'success')
            corrected = set()
            for lane in order:
                accounting.credit('failed-'+lane, 'retry-'+lane); corrected.add(lane)
                union = sum(n for n, lanes, planning in segments if planning or lanes-corrected)
                workers = sum(n*len(lanes-corrected) for n, lanes, _ in segments)
                self.assertEqual(accounting.raw_effective_seconds-accounting.excluded_effective_seconds, union)
                self.assertEqual(accounting.raw_worker_seconds-accounting.excluded_worker_seconds, workers)

    def test_worker_refund_does_not_erase_same_theory_healthy_worker(self):
        accounting = ProviderRequestAccounting()
        accounting.begin('bad', 'A', 'failed')
        accounting.record(10, {'bad':'A', 'healthy':'A'})
        accounting.finish('bad', 'failed', 'failed')
        accounting.begin('bad', 'A', 'retry'); accounting.finish('bad', 'retry', 'success')
        receipt = accounting.credit('failed', 'retry')
        self.assertEqual(receipt['effective_seconds'], 0)
        self.assertEqual(receipt['worker_seconds'], 10)
        self.assertEqual(receipt['theory_seconds'], {})

    def test_existing_audited_infrastructure_adjustment_keeps_separate_raw_cost(self):
        self.begin('A', 'normal'); self.now += 20; self.finish('A', 'normal')
        self.stage.deactivate('A'); self.stage.cancel('runtime_failure'); self.stage.close()
        original = deepcopy(self.ledger.rows[-1]['data']['state'])
        corrected = deepcopy(original)
        corrected.update(effective_seconds=15., worker_seconds=15., theory_seconds={'A':15.})
        self.ledger.append('time_budget_checkpoint', boundary='audited_runtime_infrastructure_correction',
            state=corrected, correction={'adjustment':dict(effective_seconds=5., worker_seconds=5., theory_seconds={'A':5.})})
        self.stage = self.make(resume_authorization='continue audited correction')
        snap = self.stage.snapshot()
        self.assertEqual((snap['raw_effective_seconds'], snap['effective_seconds']), (20, 15))
        self.assertEqual(snap['excluded_failed_request_seconds'], 0)
        self.assertEqual(snap['excluded_infrastructure_seconds'], 5)
        self.stage.close(); self.stage = self.make(resume_authorization='continue again')
        self.assertEqual(self.stage.snapshot()['effective_seconds'], 15)
        self.assertEqual(self.stage.snapshot()['excluded_infrastructure_seconds'], 5)

    def test_many_successful_requests_keep_checkpoint_metadata_bounded(self):
        import json
        self.begin('A', 'oldest'); self.now += 1; self.finish('A', 'oldest')
        sizes = []
        for i in range(1000):
            uid = 'normal-%04d' % i
            self.begin('A', uid); self.now += .1; self.finish('A', uid)
            sizes.append(len(json.dumps(self.ledger.rows[-1]['data']['state'])))
        self.assertLess(max(sizes), 3000)
        self.assertEqual(len(self.stage.snapshot()['provider_request_accounting']['requests']), 1)
        with self.assertRaisesRegex(ValueError, 'already used'):
            self.stage.begin_provider_request('A', 'oldest')
        self.stage.deactivate('A'); self.stage.close()
        self.stage = self.make(resume_authorization='resume long experiment')
        self.stage.select('A', worker_id='A')
        with self.assertRaisesRegex(ValueError, 'already used'):
            self.stage.begin_provider_request('A', 'oldest')

    def test_compaction_keeps_credit_evidence_and_duplicate_credit_is_idempotent(self):
        self.begin('A', 'failed'); self.now += 5; self.finish('A', 'failed', 'failed')
        self.begin('A', 'retry'); self.finish('A', 'retry')
        receipt = self.stage.credit_provider_request('failed', 'retry')
        for i in range(5):
            self.begin('A', str(i)); self.finish('A', str(i))
        self.assertEqual(self.stage.credit_provider_request('failed', 'retry'), receipt)
        self.assertEqual(set(self.stage.provider_requests.requests), {'failed', 'retry', '4'})
        self.stage.deactivate('A'); self.stage.close()
        self.stage = self.make(resume_authorization='resume compact accounting')
        self.assertEqual(self.stage.credit_provider_request('failed', 'retry'), receipt)


if __name__ == '__main__':
    unittest.main()
