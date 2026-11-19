"""Canonical native-attempt classification and charging are shared."""
import unittest

from regen.execution.check_protocol import charged_attempt, classify_status


class ErrorClassificationTest(unittest.TestCase):
    def test_success(self):
        for status in ("ok", "green", "proved", "disproved", "refuted", "context_refuted"):
            self.assertEqual(classify_status(status), "success")
            self.assertFalse(charged_attempt(status))

    def test_timeout_is_charged(self):
        self.assertEqual(classify_status("timeout"), "timeout")
        self.assertTrue(charged_attempt("timeout"))

    def test_proof_failure_is_charged(self):
        for status in ("build_error", "rejected", "missing_theorem"):
            self.assertEqual(classify_status(status), "proof_failure")
            self.assertTrue(charged_attempt(status))

    def test_infrastructure_and_blocked_are_not_charged(self):
        for status in (
            "infrastructure_error",
            "stack_exhausted",
            "resource_exhausted",
            "native_interrupt",
            "blocked_dependency",
        ):
            self.assertIn(classify_status(status), {"infrastructure", "blocked"})
            self.assertFalse(charged_attempt(status))


if __name__ == "__main__":
    unittest.main()
