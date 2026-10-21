#!/usr/bin/env python3
"""Regression tests for architecture isolation in the theory DAG."""
from __future__ import annotations

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import parse_theory_imports as dag


class TheoryIndexTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parsed = dag.parse_roots.parse_all_roots(dag.L4V.resolve(), arch=dag.ARCH)
        cls.index, cls.paths = dag.build_theory_index(parsed)

    def test_only_selected_architecture_is_indexed(self):
        wrong_arches = dag.ARCH_DIRS - {dag.ARCH}
        for path in self.paths:
            relative_parts = path.relative_to(dag.L4V).parts
            self.assertTrue(wrong_arches.isdisjoint(relative_parts), path)

    def test_canonical_paths_are_unique(self):
        selected = {Path(info["path"]) for info in self.index.values()}
        self.assertEqual(selected, set(self.paths))
        self.assertEqual(len(selected), len(self.paths))

    def test_arm_refine_roots_have_correct_owners(self):
        self.assertIn("/refine/ARM/LevityCatch.thy", self.index["Refine.LevityCatch"]["path"])
        self.assertEqual(
            self.index["RefineOrphanage.Orphanage"]["session"], "RefineOrphanage"
        )


if __name__ == "__main__":
    unittest.main()
