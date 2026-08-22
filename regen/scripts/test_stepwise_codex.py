#!/usr/bin/env python3
import json
import unittest

from regen.stepwise_codex import (
    build_step_prompt, decode_state_request, encode_state_request,
    parse_steps, ranked_steps,
)


class StepwiseCodexTest(unittest.TestCase):
    def test_parses_ranked_json_candidates(self):
        text = json.dumps({"steps": ["by simp", {"step": "apply rule"}, "by simp"]})
        self.assertEqual(parse_steps(text, 8), ["by simp", "apply rule"])
        self.assertEqual(ranked_steps(["a", "b"]), [["a", 1.0], ["b", 0.5]])

    def test_rejects_proof_bypasses(self):
        text = json.dumps({"steps": ["sorry", "by simp", "apply (rule x); sorry"]})
        self.assertEqual(parse_steps(text, 8), ["by simp"])

    def test_extracts_fenced_json_and_bounds_width(self):
        text = '```json\n{"steps":["simp","rule foo","wp"]}\n```'
        self.assertEqual(
            parse_steps(text, 2), ["apply (simp)", "apply (rule foo)"],
        )

    def test_repairs_literal_isabelle_symbol_escapes_in_json(self):
        text = '{"steps":["apply (rule_tac x=\\"(\\<lambda>_. None)\\" in exI)","by simp"]}'
        self.assertEqual(parse_steps(text, 4), [
            'apply (rule_tac x="(\\<lambda>_. None)" in exI)',
            "by simp",
        ])

    def test_preserves_outer_commands_and_wraps_bare_methods(self):
        text = json.dumps({"steps": ["by simp", "apply wp", "fastforce"]})
        self.assertEqual(
            parse_steps(text, 8),
            ["by simp", "apply wp", "apply (fastforce)"],
        )

    def test_prompt_contains_state_and_schema(self):
        prompt = build_step_prompt(
            "proof (prove)\\ngoal", 4,
            proof_prefix=["apply rule"], forbidden_facts={"ko_at_def"},
        )
        self.assertIn('"steps"', prompt)
        self.assertIn("proof (prove)", prompt)
        self.assertIn("at most 4", prompt)
        self.assertIn("not bare `simp", prompt)
        self.assertIn("apply rule", prompt)
        self.assertIn("ko_at_def", prompt)
        self.assertIn("legal\n  retrieved premise hints", prompt)
        self.assertIn("Preserve the checked prefix", prompt)

    def test_state_envelope_preserves_checked_prefix(self):
        encoded = encode_state_request("goal", ["apply rule"])
        self.assertEqual(
            decode_state_request(encoded), ("goal", ["apply rule"]),
        )

    def test_broad_automation_is_penalized(self):
        ranked = ranked_steps(["apply auto", "apply (rule x)"])
        self.assertLess(ranked[0][1], 1.0)
        self.assertEqual(ranked[1][1], 0.5)


if __name__ == "__main__":
    unittest.main()
