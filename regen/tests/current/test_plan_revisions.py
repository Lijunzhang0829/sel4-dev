from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from regen.planning.plan_revisions import (
    append_revision, start_plan, verify_revisions,
)


class PlanRevisionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.root = self.output / "planning"
        self.plan = {
            "schema_version": 4,
            "theories": [
                {"name": "A", "contract_ids": ["a"], "priority_rank": 0},
                {"name": "B", "contract_ids": ["b"], "priority_rank": 1},
            ],
            "suggested_theory_edges": [],
            "priority_order": ["A", "B"],
        }

    def test_stable_identity_append_only_revisions_and_head(self):
        identity = start_plan(self.root, "sel4-refine-arm", identity_token="one")
        self.assertEqual(
            start_plan(self.root, "sel4-refine-arm", identity_token="ignored"),
            identity,
        )
        first = append_revision(self.root, self.plan)
        changed = deepcopy(self.plan)
        changed["theories"][1]["priority_rank"] = 0
        second = append_revision(
            self.root, changed, started_theories={"A": first["revision_id"]},
        )
        self.assertEqual(first["plan_id"], second["plan_id"])
        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["parent_revision_id"], first["revision_id"])
        self.assertEqual(verify_revisions(self.root)["revision_id"], second["revision_id"])

    def test_started_theory_cannot_change_or_disappear(self):
        start_plan(self.root, "sel4-refine-arm", identity_token="one")
        first = append_revision(self.root, self.plan)
        for mutation in ("change", "remove"):
            changed = deepcopy(self.plan)
            if mutation == "change":
                changed["theories"][0]["contract_ids"] = ["different"]
            else:
                changed["theories"].pop(0)
            with self.subTest(mutation=mutation), self.assertRaisesRegex(
                ValueError, "started theory"
            ):
                append_revision(
                    self.root, changed,
                    started_theories={"A": first["revision_id"]},
                )

    def test_fresh_output_gets_new_identity(self):
        first = start_plan(self.root, "sel4-refine-arm", identity_token="one")
        second = start_plan(
            self.output / "other", "sel4-refine-arm", identity_token="two",
            derived_from_plan_id=first["plan_id"],
        )
        self.assertNotEqual(first["plan_id"], second["plan_id"])
        self.assertEqual(second["derived_from_plan_id"], first["plan_id"])


if __name__ == "__main__":
    unittest.main()
