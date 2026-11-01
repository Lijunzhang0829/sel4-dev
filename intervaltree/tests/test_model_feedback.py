"""Source-side regression for the frozen deletion witness, without Isabelle."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest


PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("model_feedback", PROJECT / "tools/check_model_feedback.py")
feedback = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(feedback)


class ModelFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(feedback.FIXTURE.read_text())

    def test_pinned_python_reproduces_failed_balance_and_preserves_observations(self):
        observed = feedback.python_replay(self.fixture)
        self.assertTrue(observed["pre_wf"])
        self.assertFalse(observed["post_wf"])
        self.assertEqual(observed["final_tree"]["left"]["balance"], -2)
        self.assertEqual(len(observed["contents"]), 11)
        self.assertTrue(observed["content_matches_set_model"])
        self.assertTrue(all(row["set_model_matches"] for row in observed["point_queries"]))

    def test_incorrect_witness_cache_is_not_silently_rebuilt(self):
        fixture = copy.deepcopy(self.fixture)
        fixture["initial_tree"]["height"] += 1
        with self.assertRaisesRegex(ValueError, "construction changed"):
            feedback.python_replay(fixture)

    def test_unpinned_source_is_rejected_before_execution(self):
        fixture = copy.deepcopy(self.fixture)
        fixture["python_source_sha256"][feedback.PYTHON_FILES[0]] = "0" * 64
        with self.assertRaisesRegex(ValueError, "source hash mismatch"):
            feedback.python_replay(fixture)


if __name__ == "__main__":
    unittest.main()
