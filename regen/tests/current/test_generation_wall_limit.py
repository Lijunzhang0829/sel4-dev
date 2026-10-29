"""The elapsed cutoff must block late acceptance even while effective time is idle."""
import tempfile
from pathlib import Path
import unittest
from regen.execution.check_tasks import CheckCancelled
from regen.infrastructure.effective_time_budget import EffectiveTimeBudget
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.parallel_generation_budget import ParallelGenerationBudgetBridge


class WallLimitTests(unittest.TestCase):
    def test_idle_cutoff_blocks_green_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [0.]
            budget = EffectiveTimeBudget(Ledger(Path(directory), 'test'),
                seconds=100, monotonic=lambda: now[0])
            budget.bind_wall_limit(10, wall_clock=lambda: 100.)
            budget.register({'A': ['a']})
            bridge = ParallelGenerationBudgetBridge(budget)
            now[0] = 11.
            self.assertEqual(budget.snapshot()['effective_seconds'], 0.)
            record = {'state': {'graph': {'theory': 'A'}, 'proofs': {'a': 'proof'},
                               'status': 'active'}, 'commit_id': 'unpublished'}
            with self.assertRaises(CheckCancelled):
                with bridge.acceptance(record):
                    self.fail('late publication entered')
            self.assertEqual(budget.reason, 'generation_wall_time_budget_exhausted')
            self.assertNotIn('a', budget.green_contract_ids)
            budget.close()

    def test_resume_retains_deadline_and_rejects_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory), 'test')
            budget = EffectiveTimeBudget(ledger, seconds=100, monotonic=lambda: 0.)
            budget.bind_wall_limit(10, wall_clock=lambda: 100.)
            budget.register({'A': ['a']})
            budget.close()
            resumed = EffectiveTimeBudget(ledger, seconds=100,
                resume_authorization='user', monotonic=lambda: 0.)
            with self.assertRaises(ValueError):
                resumed.bind_wall_limit(None)
            with self.assertRaises(ValueError):
                resumed.bind_wall_limit(20, wall_clock=lambda: 105.)
            with self.assertRaises(CheckCancelled):
                resumed.bind_wall_limit(10, wall_clock=lambda: 111.)
            self.assertEqual(resumed.wall_limit['deadline_unix'], 110.)
            resumed.close()


if __name__ == '__main__':
    unittest.main()
