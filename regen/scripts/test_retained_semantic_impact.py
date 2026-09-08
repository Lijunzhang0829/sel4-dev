#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from regen.retained_semantic_impact import (
    aggregate_session, audit_theory, build_theory_report, summarize_node,
)


def record(
    record_id: str, name: str, *, attributes: list[str] | None = None,
) -> dict:
    return {
        "record_id": record_id,
        "canonical_name": name,
        "declaration_name": name,
        "fact_index": 0,
        "owner_theory": "Semantic_Base.Demo",
        "source_class": "retained_semantic_export",
        "attributes": attributes or [],
        "provenance": {
            "validation_name": name,
            "name_hint": name,
            "derivation_name": name,
        },
    }


class FakeChecker:
    def __init__(self, root: Path, dependency_name: str) -> None:
        self.root = root
        self.dependency_name = dependency_name
        self.call_count = 0

    def audit_complete_theory(self, graph, proofs):
        self.call_count += 1
        automation = {
            "schema_version": 1,
            "node_id": f"theory:{graph['theory']}",
            "automation_context_id": "automation-test",
            "records": [{
                "kind": "simp_rule",
                "name_hint": self.dependency_name,
                "derivation_name": self.dependency_name,
            }],
        }
        dependencies = {
            node["id"]: {
                "schema_version": 1,
                "node_id": node["id"],
                "dependency_set_id": f"dependencies-{node['id']}",
                "has_skip_proof": False,
                "dependencies": [{
                    "canonical_name": self.dependency_name, "fact_index": 0,
                }],
            }
            for node in graph["nodes"]
        }
        return {
            "verdict": "green",
            "complete_theory_audit": {
                "automation_context": automation,
                "proof_dependencies": dependencies,
            },
        }


class RetainedSemanticImpactTest(unittest.TestCase):
    def setUp(self) -> None:
        self.records = [
            record("retained:foo", "Semantic_Base.Demo.foo", attributes=["simp"]),
            record("retained:bar", "Semantic_Base.Demo.bar"),
            record(
                "retained:corres",
                "IpcCancel_R.delete_one.delete_one_corres",
            ),
        ]
        self.node = {"id": "node:demo", "name": "demo"}

    def audit(self, *, skip: bool = False, dependencies=None) -> dict:
        return {
            "automation_context": {
                "automation_context_id": "automation-test",
                "records": [{
                    "kind": "simp_rule",
                    "name_hint": "Semantic_Base.Demo.foo",
                    "derivation_name": "Semantic_Base.Demo.foo",
                }],
            },
            "proof_dependencies": {
                "dependency_set_id": "dependencies-test",
                "has_skip_proof": skip,
                "dependencies": dependencies or [],
            },
        }

    def test_simp_dependency_is_measured_not_merely_available(self) -> None:
        node = summarize_node(
            self.node,
            "by (simp add: Semantic_Base.Demo.foo)",
            self.audit(dependencies=[{
                "canonical_name": "Semantic_Base.Demo.foo", "fact_index": 0,
            }]),
            self.records,
        )
        self.assertEqual(
            ["retained:foo"],
            [item["record_id"] for item in node["retained_certificate_dependencies"]],
        )
        self.assertEqual(
            ["retained:foo"],
            [
                item["record_id"]
                for item in node["retained_automation_associated_dependencies"]
            ],
        )

    def test_available_pool_member_without_dependency_is_not_used(self) -> None:
        node = summarize_node(
            self.node, "by simp", self.audit(), self.records,
        )
        self.assertEqual([], node["retained_certificate_dependencies"])
        self.assertEqual([], node["retained_automation_associated_dependencies"])

    def test_skip_proof_is_a_hard_failure(self) -> None:
        with self.assertRaisesRegex(ValueError, "skip proof"):
            summarize_node(
                self.node, "by simp", self.audit(skip=True), self.records,
            )

    def test_theory_gate_and_session_aggregation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = root / "premises.json"
            snapshot.write_text(json.dumps({
                "campaign_id": "campaign:test",
                "semantic_base_seal_id": "semantic-base:test",
                "records": self.records,
            }), encoding="utf-8")
            graph = {
                "theory": "Refine.Demo_R", "revision": 3,
                "nodes": [self.node],
            }
            theory_report = root / "theory-impact.json"
            checker = FakeChecker(
                root / "checker", "Semantic_Base.Demo.foo",
            )
            result = audit_theory(
                checker=checker,
                graph=graph,
                proofs={"node:demo": "by (simp add: Semantic_Base.Demo.foo)"},
                premise_snapshot_path=snapshot,
                output_path=theory_report,
            )
            self.assertEqual("pass", result["verdict"])
            self.assertEqual(1, checker.call_count)
            self.assertEqual(1, result["actual_impact"]["used_retained_fact_count"])
            session_report = root / "session-impact.json"
            session = aggregate_session([theory_report], session_report)
            self.assertEqual(1, session["affected_theorem_count"])
            self.assertEqual(1, session["used_retained_fact_count"])

    def test_refinement_surface_is_disclosed_even_when_unused(self) -> None:
        report = build_theory_report(
            campaign_id="campaign:test",
            semantic_base_seal_id="semantic-base:test",
            theory="Refine.Demo_R", graph_revision=1,
            records=self.records,
            node_reports=[summarize_node(
                self.node, "by simp", self.audit(), self.records,
            )],
        )
        self.assertEqual(1, report["retained_surface"]["refinement_shaped_fact_count"])
        self.assertEqual(0, report["actual_impact"]["used_retained_fact_count"])


if __name__ == "__main__":
    unittest.main()
