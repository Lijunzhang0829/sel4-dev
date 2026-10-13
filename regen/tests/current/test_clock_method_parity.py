"""Common serial trace through A's evidence evaluator and B's live clock."""
import tempfile
import unittest
from pathlib import Path

from regen.execution.check_tasks import CheckCancelled
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.generation_activity_bridge import GenerationActivityBridge
from regen.infrastructure.theory_activity import TheoryActivityStop
from regen.tools import theory_effective_clock as a_clock


class ClockMethodParityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = [0.]
        ledger = Ledger(Path(self.tmp.name), 'parity', clock=lambda: self.now[0])
        self.b = TheoryActivityStop(ledger, 3600, monotonic=lambda: self.now[0])
        self.addCleanup(self.b.close)
        self.b.register({'A': ['contract:a1', 'contract:a2'], 'B': ['contract:b1']})
        self.bridge = GenerationActivityBridge(self.b)
        self.a = {}
        a_clock.clock_state(self.a)

    def event(self, at, name, theory='A', **fields):
        self.now[0] = at
        if name.startswith('model_work_'):
            fields.setdefault('model_lifecycle_policy', 2)
        data = {'theory': theory, **fields}
        self.a['effective_clock']['events'].append(
            {'at': at, 'run_dir': 'service', 'event': name, **data})
        self.bridge.handle(name, data)

    def start(self, at, theory, identity):
        self.now[0] = at
        self.b.select(theory)
        self.event(at, 'theory_generation_started', theory,
                   generation_id=identity, clock_schema=a_clock.MODE,
                   model_lifecycle_policy=2)

    def stop(self, at, theory, identity):
        self.event(at, 'theory_generation_stopped', theory, generation_id=identity)

    def progress(self, at, theory, uid):
        self.now[0] = at
        self.a['effective_clock']['progress'].append(
            {'at': at, 'theory': theory, 'proof_ids': [uid]})
        self.b.observe(uid, {'status': 'green'}, kind='validation', theory=theory)

    def exclude(self, at, identity, category, *, theory='A', finish=False):
        self.event(at, 'generation_exclusion_finished' if finish else 'generation_exclusion_started',
                   theory, phase_id=identity, category=category)

    def compare(self, at):
        self.now[0] = at
        a = a_clock.evaluate(self.a, {'service': {'running': True}}, at, 3600)
        b = self.b.snapshot()
        for theory, row in a['theories'].items():
            self.assertEqual(row['effective_seconds'], b['theories'][theory]['effective_seconds'])
            self.assertEqual(row['idle_effective_seconds'], b['theories'][theory]['idle_seconds'])
        return a, b

    def test_serial_shared_trace_normal_nested_prep_overlap_retry_green_switch_seal(self):
        self.start(0, 'A', 'g1')
        self.exclude(10, 'prefix', 'prefix_preparation')
        self.exclude(20, 'heap', 'heap_preparation')
        self.event(30, 'model_work_observed')
        self.exclude(40, 'setup', 'runtime_setup')
        self.exclude(50, 'setup', 'runtime_setup', finish=True)
        self.event(60, 'model_work_observation_finished')
        self.exclude(70, 'heap', 'heap_preparation', finish=True)
        self.exclude(80, 'prefix', 'prefix_preparation', finish=True)
        self.progress(90, 'A', 'contract:a1')
        self.stop(100, 'A', 'g1')
        self.start(140, 'B', 'g2')
        self.exclude(150, 'statement', 'statement_preflight', theory='B')
        self.exclude(170, 'statement', 'statement_preflight', theory='B', finish=True)
        self.progress(180, 'B', 'contract:b1')
        self.exclude(190, 'seal', 'seal', theory='B')
        self.exclude(200, 'seal', 'seal', theory='B', finish=True)
        self.b.mark_sealed('B')
        self.stop(200, 'B', 'g2')
        self.start(240, 'A', 'g3')
        self.exclude(260, 'retry', 'provider_stream_retry')
        self.exclude(280, 'retry', 'provider_stream_retry', finish=True)
        a, b = self.compare(300)
        self.assertEqual(a['theories']['A']['effective_seconds'], 100)
        self.assertEqual(a['theories']['A']['idle_effective_seconds'], 50)
        self.assertEqual(a['theories']['B']['effective_seconds'], 30)
        self.assertFalse(a['expired_theories'])
        self.bridge.close()

    def test_first_green_boundary_tie_and_subsequent_expiry_match(self):
        self.start(0, 'A', 'g')
        self.progress(3600, 'A', 'contract:a1')
        a, b = self.compare(3600)
        self.assertFalse(a['expired_theories'])
        self.assertFalse(b['paused'])
        self.now[0] = 7200
        with self.assertRaises(CheckCancelled):
            self.b.check()
        a = a_clock.evaluate(self.a, {'service': {'running': True}}, 7200, 3600)
        self.assertEqual(a['expired_theories'], ['A'])
        self.assertEqual(a['first_crossing_at'], 7200)
        self.assertEqual(self.b.expired_theory, 'A')

    def test_missing_generation_stop_is_explicitly_unreconciled_in_a(self):
        self.start(0, 'A', 'g')
        self.now[0] = 10
        self.b.check()
        with self.assertRaisesRegex(a_clock.ClockEvidenceError, 'without generation-stop'):
            a_clock.evaluate(self.a, {'service': {'running': False}}, 10, 3600)
        # B checkpoints distinguish unobserved crash tail; no invented downtime
        # duration is compared as if it had been measured generation.
        restored = TheoryActivityStop(self.b.ledger, 3600, monotonic=lambda: self.now[0])
        self.addCleanup(restored.close)
        receipt = [r for r in self.b.ledger.rows if r['kind'] == 'theory_activity_started'][-1]
        self.assertTrue(receipt['data']['unobserved_shutdown_tail'])
        self.assertIsNone(restored.active_theory)


if __name__ == '__main__':
    unittest.main()
