#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from regen.planning.automation_advisor import (
    advice_prompt,
    build_automation_advice,
    build_probe_advice,
    parse_automation_context,
)
from regen.execution.stepwise_lemma_backend import AuditedCodexStepGenerator


def enc(value: str) -> str:
    return value.encode("utf-8").hex()


class AutomationAdvisorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.premises = [
            {
                "record_id": "premise:def",
                "canonical_name": "StateRelation.cte_relation_def",
                "owner_theory": "StateRelation",
                "exact_statement": "cte_relation y = (\\<lambda>x. True)",
                "attributes": [],
            },
            {
                "record_id": "premise:wp",
                "canonical_name": "KHeap_H.getObject_wp",
                "owner_theory": "KHeap_H",
                "exact_statement": "\\<lbrace>P\\<rbrace> getObject p \\<lbrace>Q\\<rbrace>",
                "attributes": ["wp"],
            },
        ]

    def test_parses_and_resolves_active_context(self) -> None:
        raw = "\n".join([
            "\t".join(["COUNTS", enc("100"), enc("20"), enc("1"), enc("1")]),
            "\t".join(["SIMP_RULE", enc("StateRelation"),
                       enc("cte_relation_def"),
                       enc("StateRelation.cte_relation_def"),
                       enc("cte_relation y = P")]),
            "\t".join(["WP_RULE", enc("KHeap_H"), enc("getObject_wp"),
                       enc("KHeap_H.getObject_wp"), enc("valid P getObject Q")]),
        ])
        snapshot = parse_automation_context(raw)
        advice = build_automation_advice(
            snapshot, self.premises, "goal: corres dc P P' cte_relation getObject",
        )
        self.assertEqual(advice["counts"]["active_simp_rules"], 100)
        self.assertEqual(
            advice["relevant_rules"][0]["legal_premise_id"], "premise:def",
        )
        self.assertEqual(advice["candidate_actions"][0]["command"], "apply wp")

    def test_probe_advice_uses_only_retrieved_named_definitions(self) -> None:
        advice = build_probe_advice(
            self.premises, "goal: corres dc P P' cte_relation getObject",
        )
        commands = [item["command"] for item in advice["candidate_actions"]]
        self.assertNotIn("apply wp", commands)
        self.assertIn(
            "apply (simp add: StateRelation.cte_relation_def)", commands,
        )
        self.assertNotIn("KHeap_H.getObject_wp", " ".join(commands))
        self.assertIn("Automation Advisor", advice_prompt(advice))

    def test_wp_requires_an_actual_hoare_goal(self) -> None:
        advice = build_probe_advice(
            self.premises,
            r"goal: \<lbrace>P\<rbrace> getObject ptr \<lbrace>Q\<rbrace>",
        )
        self.assertIn(
            "apply wp",
            [item["command"] for item in advice["candidate_actions"]],
        )

    def test_structural_split_is_only_a_residual_probe(self) -> None:
        advice = build_probe_advice(
            self.premises, "goal: case value of None => P | Some x => Q x",
        )
        self.assertIn(
            "apply (simp split: option.splits)",
            [item["command"] for item in advice["candidate_actions"]],
        )

    def test_step_generator_prepends_advisor_actions_and_logs_them(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            audit = Path(temp)
            generator = AuditedCodexStepGenerator(
                audit, model="test-model", width=8, timeout=10,
                premise_hints=self.premises, theory="KHeap_R",
                local_proof_outline={"diagnosis": "compose first", "local_steps": []},
            )
            with patch(
                "regen.execution.stepwise_lemma_backend.call_llm",
                return_value=json.dumps({"steps": ["apply rule"]}),
            ):
                ranked = generator.generate(
                    "goal (1 subgoal): corres dc P P' f getObject",
                )
            commands = [item[0] for item in ranked]
            self.assertEqual(commands[0], "apply rule")
            self.assertIn("apply simp", commands)
            log = json.loads(
                (audit / "0001-proof-step.premises.json").read_text()
            )
            self.assertEqual(
                log["automation_advice"]["mode"],
                "premise-guided-isabelle-probes",
            )
            self.assertEqual(
                log["local_proof_outline"]["diagnosis"], "compose first",
            )


if __name__ == "__main__":
    unittest.main()
