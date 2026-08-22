#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from regen.local_proof_outline import (
    build_outline_request,
    deterministic_outline,
    outline_prompt_block,
    plan_initial_outline,
    validate_outline,
)


class LocalProofOutlineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.node = {
            "id": "contract:demo", "role": "contract",
            "statement": 'lemma demo_corres: "corres R P P\' f g"',
            "strategy": "enter corres and compose generated dependencies",
        }
        self.dependencies = [{
            "id": "node:result", "name": "regen_result_relation",
            "statement": 'lemma regen_result_relation: "R x y"',
        }]
        self.premises = [{
            "record_id": "premise:intro",
            "canonical_name": "Corres_UL.corres_underlyingI",
            "exact_statement": "corres_underlying R nf nf' P P' f g",
            "owner_theory": "Corres_UL", "attributes": [],
        }]

    def test_fallback_puts_composition_before_residual_automation(self) -> None:
        request = build_outline_request(
            self.node, self.dependencies, self.premises,
        )
        outline = deterministic_outline(request)
        phases = [item["phase"] for item in outline["local_steps"]]
        self.assertEqual(phases, ["introduce", "compose", "glue"])
        self.assertEqual(
            outline["local_steps"][1]["generated_dependencies"],
            ["regen_result_relation"],
        )
        self.assertEqual(
            outline["local_steps"][-1]["automation_policy"], "residual-only",
        )

    def test_validator_allows_natural_language_apply(self) -> None:
        request = build_outline_request(
            self.node, self.dependencies, self.premises,
        )
        outline = deterministic_outline(request)
        outline["local_steps"][1]["objective"] = (
            "Apply the generated relation bridge after preserving the state."
        )
        self.assertEqual(validate_outline(outline, request), outline)

    def test_validator_rejects_unavailable_dependency(self) -> None:
        request = build_outline_request(
            self.node, self.dependencies, self.premises,
        )
        outline = deterministic_outline(request)
        outline["local_steps"][1]["generated_dependencies"] = ["human_fact"]
        with self.assertRaisesRegex(ValueError, "unavailable generated"):
            validate_outline(outline, request)

    def test_planner_records_request_and_validated_outline(self) -> None:
        response = {
            "diagnosis": "compose the generated result relation first",
            "local_steps": [{
                "id": "outline-1", "phase": "compose",
                "objective": "Establish the generated result relation.",
                "depends_on": [],
                "generated_dependencies": ["regen_result_relation"],
                "premise_names": [], "automation_policy": "none",
            }],
            "avoid": ["avoid full semantic unfolding"],
        }
        with tempfile.TemporaryDirectory() as temp, patch(
            "regen.local_proof_outline.call_llm",
            return_value=json.dumps(response),
        ):
            prefix = Path(temp) / "outline" / "initial"
            outline = plan_initial_outline(
                self.node, self.dependencies, self.premises,
                output_prefix=prefix, provider="codex", model="test-model",
                timeout=10, theory="Refine.Demo",
            )
            self.assertEqual(outline["source"], "llm")
            self.assertTrue(Path(str(prefix) + ".request.json").is_file())
            self.assertTrue(Path(str(prefix) + ".outline.json").is_file())
            self.assertIn("Local Proof Outline", outline_prompt_block(outline))


if __name__ == "__main__":
    unittest.main()
