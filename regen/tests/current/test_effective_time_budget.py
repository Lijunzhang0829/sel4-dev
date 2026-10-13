"""Time-only gate, compact journal and real abrupt-exit recovery contracts."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from regen.execution.check_tasks import CheckCancelled
from regen.infrastructure.effective_time_budget import (
    CHECKPOINT_KIND, EffectiveTimeBudget, LIMIT_REASON, POLICY, budget_journal_summary,
)
from regen.infrastructure.experiment_ledger import Ledger


class MemoryLedger:
    def __init__(self):
        self.rows = []
        self.lock = threading.RLock()
        self.clock = lambda: 1000.

    def append(self, kind, **data):
        row = dict(sequence=len(self.rows)+1, event_id=str(len(self.rows)+1),
                   kind=kind, data=deepcopy(data))
        self.rows.append(row)
        return row


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.
        self.ledger = MemoryLedger()
        self.stage = self.make()
        self.stage.register({'A': ['a1', 'a2'], 'B': ['b1']})

    def make(self, **kwargs):
        return EffectiveTimeBudget(self.ledger, 100, monotonic=lambda: self.now, **kwargs)

    def advance(self, seconds):
        self.now += seconds

    def test_union_and_separate_worker_cost(self):
        s = self.stage
        s.select('A', worker_id='a'); self.advance(10)
        s.select('B', worker_id='b'); self.advance(20)
        with s.exclude('prefix_preparation', worker_id='a'):
            self.advance(30)
        s.deactivate('b'); self.advance(10)
        snap = s.snapshot()
        self.assertEqual(snap['effective_seconds'], 70)
        self.assertEqual(snap['worker_seconds'], 90)
        self.assertEqual(snap['theories']['A']['effective_seconds'], 40)
        self.assertEqual(snap['theories']['B']['effective_seconds'], 50)

    def test_nested_exclusions_and_model_overlap(self):
        s = self.stage; s.select('A')
        one = s.begin_exclusion('prefix_preparation')
        two = s.begin_exclusion('heap_preparation')
        self.advance(1000); s.end_exclusion(one)
        self.advance(1000); s.set_model_overlap(True)
        self.advance(20)
        recovery = s.begin_exclusion('provider_recovery')
        self.advance(1000); s.end_exclusion(recovery)
        self.advance(10); s.set_model_overlap(False)
        self.advance(1000); s.end_exclusion(two)
        self.advance(10)
        self.assertEqual(s.snapshot()['effective_seconds'], 40)

    def test_no_reset_from_green_helpers_or_tokens(self):
        s = self.stage; s.select('A'); self.advance(80)
        s.observe('a1', {'status': 'green'}, theory='A')
        s.observe('helper:A:h', {'status': 'green'}, theory='A')
        self.ledger.append('usage_snapshot', total={'totalTokens': 10**15})
        self.assertFalse(s.usage_updated())
        s.select('B'); self.advance(20)
        with self.assertRaises(CheckCancelled): s.check_publication('B', ['b1'])
        self.assertEqual(s.reason, LIMIT_REASON)
        self.assertNotIn('b1', s.green_contract_ids)
        self.assertFalse(s.finished)
        self.assertEqual([r['data']['disposition'] for r in self.ledger.rows
                          if r['kind'] == 'termination'], ['pause'])

    def test_pause_cleanup_and_resume_excludes_offline(self):
        s = self.stage; s.select('A'); s.set_model_overlap(True)
        self.advance(25); s.cancel('transport_failure')
        s.set_model_overlap(False); s.deactivate(); s.close()
        self.advance(100000)
        with self.assertRaisesRegex(ValueError, 'authorization'): self.make()
        resumed = self.make(resume_authorization='resume-1')
        self.assertEqual(resumed.snapshot()['gate_state'], 'CLOSED')
        self.assertEqual(resumed.snapshot()['effective_seconds'], 25)
        self.assertEqual(resumed.snapshot()['green_contract_ids'], [])
        resumed.select('B'); self.advance(10)
        self.assertEqual(resumed.snapshot()['effective_seconds'], 35)

    def test_extension_preserves_base_and_cutoff(self):
        s = self.stage; s.select('A'); self.advance(100)
        with self.assertRaises(CheckCancelled): s.check()
        s.close()
        with self.assertRaisesRegex(ValueError, 'exhausted'):
            self.make(resume_authorization='resume')
        resumed = self.make(resume_authorization='extension-approved', extension_seconds=50)
        self.assertEqual(resumed.base_seconds, 100)
        self.assertEqual(resumed.seconds, 150)
        self.assertEqual(resumed.effective_seconds, 100)
        self.assertEqual(sum(r['kind'] == 'termination' for r in self.ledger.rows), 1)

    def test_durable_cutoff_before_checkpoint_is_not_forgiven(self):
        s = self.stage; s.select('A'); self.advance(40); s.check()
        self.ledger.append('termination', activity_policy=POLICY,
            effective_seconds=100, worker_seconds=150, theory_seconds={'A': 100})
        with self.assertRaisesRegex(ValueError, 'exhausted'):
            self.make(resume_authorization='resume')
        resumed = self.make(resume_authorization='extension', extension_seconds=10)
        self.assertEqual(resumed.effective_seconds, 100)
        self.assertEqual(resumed.worker_seconds, 150)

    def test_durable_extension_before_checkpoint_replayed_once(self):
        self.ledger.append('time_budget_extended', previous_seconds=100, seconds=150)
        resumed = self.make(resume_authorization='resume')
        self.assertEqual(resumed.seconds, 150)
        resumed.close()
        again = self.make(resume_authorization='next-resume')
        self.assertEqual(again.seconds, 150)

    def test_summary_replays_cutoff_without_extrapolating_offline_time(self):
        self.stage.select('A'); self.advance(40); self.stage.check()
        self.ledger.append('termination', activity_policy=POLICY, disposition='pause',
            reason=LIMIT_REASON, effective_seconds=100, worker_seconds=100)
        self.advance(100000)
        value = budget_journal_summary(self.ledger)
        self.assertEqual(value['effective_seconds'], 100)
        self.assertEqual(value['remaining_seconds'], 0)
        self.assertTrue(value['paused'])
        self.assertFalse(value['gate_open'])
        self.assertEqual(value['token_limit'], None)

    def test_invalid_persisted_counters_reject_restore(self):
        state = deepcopy(self.ledger.rows[-1]['data']['state'])
        state['effective_seconds'] = float('nan')
        self.ledger.append(CHECKPOINT_KIND, state=state)
        with self.assertRaisesRegex(ValueError, 'nonnegative finite'):
            self.make(resume_authorization='resume')

    def test_unknown_crash_tail_not_erased_by_next_clean_resume(self):
        s = self.stage; s.select('A'); self.advance(40); s.check()
        self.advance(10000)
        recovered = self.make(resume_authorization='recover')
        self.assertEqual(recovered.effective_seconds, 40)
        self.assertIsNone(recovered.snapshot()['unobserved_tail_seconds'])
        recovered.close()
        again = self.make(resume_authorization='again')
        self.assertFalse(again.snapshot()['accounting_complete'])
        self.assertEqual(again.snapshot()['unclean_recoveries'], 1)

    def test_legacy_finished_and_changed_binding_rejected(self):
        with self.assertRaisesRegex(ValueError, 'binding changed'):
            EffectiveTimeBudget(self.ledger, 101, resume_authorization='resume')
        legacy = MemoryLedger(); legacy.append('proof_commit')
        with self.assertRaisesRegex(ValueError, 'migration'):
            EffectiveTimeBudget(legacy)
        self.stage.finish()
        self.assertFalse(self.stage.snapshot()['paused'])
        self.assertTrue(self.stage.snapshot()['finished'])
        with self.assertRaisesRegex(ValueError, 'completed'):
            self.make(resume_authorization='resume')

    def test_empty_contract_theories_survive_seal_and_restore(self):
        for allocation in ({'Helpers': [], 'A': ['a']}, {'Helpers': []}, {}):
            with self.subTest(allocation=allocation):
                ledger = MemoryLedger()
                stage = EffectiveTimeBudget(ledger, 100)
                stage.register(allocation)
                if 'Helpers' in allocation: stage.mark_sealed('Helpers')
                stage.close()
                resumed = EffectiveTimeBudget(ledger, 100, resume_authorization='resume')
                self.assertEqual(set(resumed.theories), set(allocation))
                resumed.register(allocation)
                with self.assertRaisesRegex(ValueError, 'frozen'):
                    resumed.register({**allocation, 'Unexpected': []})
                self.assertEqual(sum(r['kind'] == 'time_budget_registered' for r in ledger.rows), 1)

    def test_registration_frozen_and_invalid_exclusion_rejected(self):
        with self.assertRaisesRegex(ValueError, 'frozen'):
            self.stage.register({'A': ['a1'], 'B': ['a2', 'b1']})
        with self.assertRaisesRegex(ValueError, 'unknown trusted'):
            self.stage.begin_exclusion('model_is_slow')
        with self.assertRaisesRegex(ValueError, 'unmatched'):
            self.stage.end_exclusion('unknown')

    def test_context_exception_closes_gate_exclusion(self):
        s = self.stage; s.select('A')
        with self.assertRaises(RuntimeError):
            with s.excluded('seal'):
                self.advance(5000)
                raise RuntimeError('native failed')
        self.advance(10)
        self.assertEqual(s.snapshot()['effective_seconds'], 10)

    def test_48h_logging_scales_with_checkpoints_not_ticks_or_proof_size(self):
        ledger = MemoryLedger()
        s = EffectiveTimeBudget(ledger, 172800, monotonic=lambda: self.now)
        s.register({'A': ['a']}); s.select('A')
        initial = len(ledger.rows)
        for tick in range(1, 172800):
            self.now = float(tick); s.check()
        additional = ledger.rows[initial:]
        self.assertEqual(len(additional), 5759)
        self.assertTrue(all(r['kind'] == CHECKPOINT_KIND for r in additional))
        self.assertLess(len(json.dumps(additional)), 4_000_000)
        self.assertTrue(all('contract_owners' not in r['data']['state'] for r in additional))

    def test_watchdog_aborts_even_if_journal_persistence_fails(self):
        aborted = threading.Event()
        s = self.stage; s.select('A')
        self.now = 100
        def broken(*args, **kwargs): raise OSError('disk full')
        self.ledger.append = broken
        s.poll_seconds = .001
        s.start(abort=aborted.set)
        self.assertTrue(aborted.wait(2))
        s.thread.join(2)
        self.assertTrue(s.event.is_set())
        self.assertEqual(s.reason, 'effective_time_budget_persistence_error')


class CrashTests(unittest.TestCase):
    def test_process_killed_without_close_reopens_hash_checked_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = '''
import sys, threading
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.effective_time_budget import EffectiveTimeBudget
now = [0.]
s = EffectiveTimeBudget(Ledger(sys.argv[1], 'crash'), 100, monotonic=lambda: now[0])
s.register({'A': ['a']}); s.select('A')
now[0] = 35.; s.check()
print('checkpoint-ready', flush=True)
threading.Event().wait()
'''
            proc = subprocess.Popen([sys.executable, '-c', script, tmp],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(proc.stdout.readline().strip(), 'checkpoint-ready')
                proc.kill(); proc.communicate(timeout=5)
            finally:
                if proc.poll() is None: proc.kill(); proc.communicate(timeout=5)
            ledger = Ledger(tmp, 'crash')
            stage = EffectiveTimeBudget(ledger, 100, resume_authorization='post-crash',
                                        monotonic=lambda: 10**9)
            self.assertEqual(stage.effective_seconds, 35)
            self.assertEqual(stage.snapshot()['gate_state'], 'CLOSED')
            self.assertFalse(stage.snapshot()['accounting_complete'])
            stage.close()
            first = Path(tmp, 'events', '000000001.json')
            value = json.loads(first.read_text()); value['data']['seconds'] = 1
            first.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, 'chain mismatch'): Ledger(tmp, 'crash')


if __name__ == '__main__': unittest.main()
