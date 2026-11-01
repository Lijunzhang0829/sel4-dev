"""Guard the expanded fixed-contract boundary, without solving any targets."""
import importlib.util
import json
from pathlib import Path
import unittest

from regen.planning.plan_review import review_input


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare_intervaltree", ROOT / "tools/prepare_benchmark.py")
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class SpecInputTests(unittest.TestCase):
    def test_parameterized_definition_is_not_silently_closed(self):
        with self.assertRaises(ValueError):
            prepare.spec_body("spec_test", 'definition spec_test :: "nat ⇒ bool" where "spec_test x ⟷ x = 0"')

    def test_wrong_equation_owner_is_rejected(self):
        with self.assertRaises(ValueError):
            prepare.spec_body("spec_test", r'definition spec_test :: bool where "other \<longleftrightarrow> True"')

    def test_review_receives_actual_delete_preconditions(self):
        records = [json.loads(line) for line in (ROOT / "sanitized/contracts.jsonl").read_text().splitlines()]
        self.assertEqual(len(records), 91)
        packet = review_input({}, records, {})
        target = next(c for c in packet["contracts"] if c["name"] == "tree_remove_preserves_wf")
        statement = target["exact_statement"]
        for fragment in (r"\<forall>fuel e t u", "tree_wf t", r"e \<in> tree_entries t",
                         "tree_remove fuel e t = Some u", "tree_wf u"):
            self.assertIn(fragment, statement)
        self.assertNotIn('"Interval_Refinement.tree_remove_preserves_wf"', statement)
        self.assertNotIn("proof", target)


if __name__ == "__main__":
    unittest.main()
