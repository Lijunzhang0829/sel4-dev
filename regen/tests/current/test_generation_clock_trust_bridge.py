"""Untrusted timing pauses serial acceptance without declaring proof failure."""
import tempfile
import unittest

from regen.execution.check_tasks import CheckCancelled
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.generation_activity_bridge import GenerationActivityBridge
from regen.infrastructure.theory_activity import TheoryActivityStop


class TrustBridgeTests(unittest.TestCase):
    def test_untrusted_preserves_costs_and_green_then_blocks_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [0.]
            ledger = Ledger(directory, 'trust', clock=lambda: now[0])
            stage = TheoryActivityStop(ledger, 3600, monotonic=lambda: now[0])
            try:
                stage.register({'A': ['a1', 'a2']})
                stage.select('A')
                now[0] = 10
                stage.observe('a1', {'status': 'green'}, kind='validation', theory='A')
                now[0] = 20
                bridge = GenerationActivityBridge(stage)
                bridge.handle('generation_clock_untrusted', {
                    'reason': 'incomplete_item_crossed_callback', 'theory': 'A',
                    'generation_id': 'g', 'thread_id': 'thread', 'turn_id': 'turn',
                    'item_ids': ['i'], 'model_lifecycle_policy': 2,
                    'source_text': 'must not be copied into timing ledger'})
                self.assertTrue(stage.event.is_set())
                self.assertEqual(stage.reason, 'effective_clock_evidence_error')
                self.assertIsNone(stage.expired_theory)
                snapshot = stage.snapshot()['theories']['A']
                self.assertEqual(snapshot['green_contract_ids'], ['a1'])
                self.assertEqual(snapshot['effective_seconds'], 20)
                evidence = [r['data'] for r in ledger.rows
                            if r['kind'] == 'generation_clock_untrusted'][0]
                self.assertFalse(evidence['evidence_trusted'])
                self.assertNotIn('source_text', evidence['evidence'])
                with self.assertRaises(CheckCancelled):
                    stage.check_publication('A', {'a2'})
            finally:
                stage.close()


if __name__ == '__main__':
    unittest.main()
