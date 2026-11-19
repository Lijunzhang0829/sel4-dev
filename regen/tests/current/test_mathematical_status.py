"""Canonical three-way result mapping is shared across internal status names."""
import unittest

from regen.execution.result_scope import mathematical_status


class MathematicalStatusTest(unittest.TestCase):
    def test_proved(self):
        self.assertEqual(mathematical_status("proved"), "proved")

    def test_refuted_scopes_collapse(self):
        self.assertEqual(mathematical_status("refuted"), "refuted")
        self.assertEqual(mathematical_status("context_refuted"), "refuted")
        self.assertEqual(mathematical_status("disproved"), "refuted")

    def test_non_mathematical_status_is_unknown(self):
        for status in (
            "unknown",
            "unfilled",
            "blocked_dependency",
            "infrastructure_error",
            "rejected",
            "overfilled",
            "extraction_error",
        ):
            self.assertEqual(mathematical_status(status), "unknown")


if __name__ == "__main__":
    unittest.main()
