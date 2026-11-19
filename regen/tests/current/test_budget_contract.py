"""Baseline and ours share the same budget/accounting contract."""
import sys
import unittest
from pathlib import Path

from regen.infrastructure.normal_runtime_budget import BUDGET_CONTRACT as OURS

VERO = Path(__file__).resolve().parents[3] / "vero-isabelle" / "src"
sys.path.insert(0, str(VERO))
from vero.isabelle.budget import BUDGET_CONTRACT as BASELINE
from vero.isabelle.active_clock import CLOCK_EXCLUSION_REASONS as BASELINE_EXCLUSIONS
from regen.infrastructure.normal_runtime_budget import CLOCK_EXCLUSION_REASONS as OURS_EXCLUSIONS


class BudgetContractTest(unittest.TestCase):
    def test_shared_clock_exclusion_reasons(self):
        self.assertEqual(OURS_EXCLUSIONS, BASELINE_EXCLUSIONS)

    def test_shared_attribution_and_charging(self):
        self.assertEqual(OURS, BASELINE)
        self.assertFalse(OURS["per_contract_split"])
        self.assertFalse(OURS["per_helper_split"])
        self.assertEqual(OURS["attribution"], "shared-run-only")
        self.assertEqual(OURS["timeout_accounting"], "charged")


if __name__ == "__main__":
    unittest.main()
