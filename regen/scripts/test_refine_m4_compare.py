#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "regen"))

from refine_m4_compare import project_proof_paths  # noqa: E402


class M4ComparisonTests(unittest.TestCase):
    def test_projection_collapses_support_and_stops_at_next_proof(self) -> None:
        successors = {
            "Proof.A": {"Support.X", "Proof.C"},
            "Support.X": {"Support.Y"},
            "Support.Y": {"Support.X", "Proof.B"},
            "Proof.B": {"Support.Z"},
            "Support.Z": {"Proof.D"},
            "Proof.C": set(),
            "Proof.D": set(),
        }
        paths = project_proof_paths(
            successors, {"Proof.A", "Proof.B", "Proof.C", "Proof.D"},
        )
        self.assertEqual(paths[("Proof.A", "Proof.B")], [
            "Proof.A", "Support.X", "Support.Y", "Proof.B",
        ])
        self.assertEqual(paths[("Proof.A", "Proof.C")], ["Proof.A", "Proof.C"])
        self.assertEqual(paths[("Proof.B", "Proof.D")], [
            "Proof.B", "Support.Z", "Proof.D",
        ])
        self.assertNotIn(("Proof.A", "Proof.D"), paths)

    def test_projection_keeps_shortest_deterministic_path(self) -> None:
        successors = {
            "Proof.A": {"Support.X", "Proof.B"},
            "Support.X": {"Proof.B"},
            "Proof.B": set(),
        }
        paths = project_proof_paths(successors, {"Proof.A", "Proof.B"})
        self.assertEqual(paths[("Proof.A", "Proof.B")], ["Proof.A", "Proof.B"])


if __name__ == "__main__":
    unittest.main()
