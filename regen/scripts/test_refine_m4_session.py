#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "regen"))

from refine_campaign import sha256_json  # noqa: E402
from refine_m4_session import (  # noqa: E402
    build_artifacts,
    compute_layers,
    normalized_support_plan,
    parse_model_json,
    planner_payload,
    planner_prompt,
    strongly_connected_components,
    validate_candidate_edges,
    validate_progress_ledger,
    verify_reconstructed_immutable,
)


def package(slot: str, contract: str, statement: str) -> dict:
    theory = f"Refine.{slot.removesuffix('.thy')}"
    return {
        "file_slot": slot,
        "theory": theory,
        "contracts": [{
            "contract_id": f"contract:{theory}:{contract}:hash",
            "name": contract,
            "exact_statement": statement,
            "owner_file_slot": slot,
        }],
    }


class M4SessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.packages = [
            package("A_R.thy", "small_corres", 'lemma small_corres: "corres (=) P P\' small smallH"'),
            package("B_R.thy", "large_corres", 'lemma large_corres: "corres (=) Q Q\' large largeH"'),
            package("C_R.thy", "other_corres", 'lemma other_corres: "corres (=) R R\' other otherH"'),
        ]
        self.policy = {
            "artifact_type": "m4-scheduling-policy",
            "model": {"model": "gpt-5.5", "max_candidate_edges": 8, "minimum_confidence": 0.55},
        }
        self.support = {
            "eligible": [{
                "theory": "Refine.Base_H", "file_slot": "Base_H.thy",
                "support_class": "initial-frozen-support", "initial_status": "published",
            }],
            "blocked": [{
                "theory": "Refine.Later_H", "file_slot": "Later_H.thy",
                "support_class": "proof-dependent-frozen-support", "initial_status": "blocked",
                "unlock_by_proof_slots": ["A_R.thy"], "also_waits_for_support": [],
            }],
        }
        self.snapshot = {
            "snapshot_id": "m3-test", "record_count": 3,
            "source_class_counts": {"frozen_parent": 2, "frozen_support": 1},
        }
        self.inputs = {
            "manifest": {"campaign_id": "campaign"},
            "policy": self.policy,
            "policy_sha256": "policy-hash",
            "m2_gate": {"verdict": "pass"},
            "m2_gate_sha256": "m2-hash",
            "m3_gate": {"verdict": "pass"},
            "snapshot": self.snapshot,
        }
        self.transcript = {
            "transcript_count": 1,
            "provider": "codex-exec-audited",
            "model": "gpt-5.5",
            "stage": "M4-session-planner",
            "theory": "Refine",
            "returncode": 0,
            "timed_out": False,
            "provider_network_call": True,
            "sealable_generation": True,
            "meta_no_tool_events": True,
            "audit_no_tool_events": True,
            "prompt_matches_reconstructed_input": True,
            "model_matches_policy": True,
            "prompt_sha256": "prompt", "raw_sha256": "raw", "final_sha256": "final",
            "stderr_sha256": "stderr", "audit_sha256": "audit",
        }

    def make_artifacts(self, response: dict | None = None) -> dict:
        payload = planner_payload(
            "campaign", self.packages, self.support, self.snapshot, self.policy,
        )
        return build_artifacts(
            self.inputs, self.packages, self.support, payload,
            response or {"schema_version": 1, "candidate_edges": []},
            self.transcript,
        )

    def test_normalized_support_plan_removes_human_imports_and_paths(self) -> None:
        workspace = {"initial_support_plan": {
            "eligible_theories": [{
                "theory": "Refine.Base_H", "file_slot": "Base_H.thy",
                "imports": ["Human.Import"], "source_path": "/forbidden/source",
            }],
            "blocked_support_theories": [{
                "theory": "Refine.Later_H", "file_slot": "Later_H.thy",
                "direct_proof_dependencies": ["Refine.A_R"],
                "unavailable_local_dependencies": ["Refine.A_R", "Refine.Base_H"],
                "imports": ["A_R"], "source_path": "/forbidden/source",
            }],
        }}
        plan = normalized_support_plan(workspace, self.packages)
        rendered = str(plan)
        self.assertNotIn("Human.Import", rendered)
        self.assertNotIn("/forbidden/source", rendered)
        self.assertEqual(plan["blocked"][0]["unlock_by_proof_slots"], ["A_R.thy"])
        self.assertEqual(plan["blocked"][0]["also_waits_for_support"], ["Refine.Base_H"])

    def test_candidate_edge_requires_identifier_tokens_and_both_contracts(self) -> None:
        good_edge = {
            "from_file": "A_R.thy", "to_file": "B_R.thy",
            "type": "contract-composition", "confidence": 0.8,
            "contract_ids": [
                self.packages[0]["contracts"][0]["contract_id"],
                self.packages[1]["contracts"][0]["contract_id"],
            ],
            "symbols": ["small", "large"], "reason": "visible composition surface",
        }
        accepted, rejected = validate_candidate_edges(
            {"schema_version": 1, "candidate_edges": [good_edge]}, self.packages, self.policy,
        )
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, [])
        for symbols in (["small arg"], ["mall"], ["secretHumanHelper"]):
            accepted, rejected = validate_candidate_edges(
                {"schema_version": 1, "candidate_edges": [{**good_edge, "symbols": symbols}]},
                self.packages, self.policy,
            )
            self.assertEqual(accepted, [])
            self.assertIn("ungrounded-symbol-evidence", rejected[0]["reasons"])

    def test_prompt_requires_one_identifier_per_symbols_item(self) -> None:
        payload = planner_payload("campaign", self.packages, self.support, self.snapshot, self.policy)
        prompt = planner_prompt(payload)
        self.assertIn("exactly one Isabelle identifier", prompt)
        self.assertIn("without arguments", prompt)

    def test_composition_forms_layers_and_operation_is_precedence(self) -> None:
        accepted = [
            {"from_file": "A_R.thy", "to_file": "B_R.thy", "type": "contract-composition"},
            {"from_file": "C_R.thy", "to_file": "A_R.thy", "type": "operation-call"},
        ]
        layers, components, cycles = compute_layers(self.packages, accepted, self.support)
        self.assertEqual(components, [["A_R.thy"], ["B_R.thy"], ["C_R.thy"]])
        self.assertEqual([item["file_slot"] for item in layers[0]["theories"]], ["C_R.thy", "A_R.thy"])
        self.assertEqual([item["file_slot"] for item in layers[1]["theories"]], ["B_R.thy"])
        self.assertEqual(cycles, [])

    def test_operation_cycle_is_collapsed_and_reported(self) -> None:
        accepted = [
            {"from_file": "A_R.thy", "to_file": "C_R.thy", "type": "operation-call"},
            {"from_file": "C_R.thy", "to_file": "A_R.thy", "type": "operation-call"},
        ]
        layers, _, cycles = compute_layers(self.packages, accepted, self.support)
        self.assertEqual(cycles, [["A_R.thy", "C_R.thy"]])
        self.assertEqual(set(item["file_slot"] for item in layers[0]["theories"]), {
            "A_R.thy", "B_R.thy", "C_R.thy",
        })

    def test_scc_is_reported_as_one_component(self) -> None:
        components = strongly_connected_components(
            ["A_R.thy", "B_R.thy", "C_R.thy"],
            [("A_R.thy", "B_R.thy"), ("B_R.thy", "A_R.thy")],
        )
        self.assertEqual(components, [["A_R.thy", "B_R.thy"], ["C_R.thy"]])

    def test_payload_uses_snapshot_identity_not_premise_records(self) -> None:
        payload = planner_payload("campaign", self.packages, self.support, self.snapshot, self.policy)
        self.assertEqual(payload["premise_snapshot_identity_only"]["snapshot_id"], "m3-test")
        self.assertNotIn("premises", payload)
        self.assertNotIn("records", payload["premise_snapshot_identity_only"])

    def test_json_fence_is_accepted(self) -> None:
        self.assertEqual(parse_model_json('```json\n{"schema_version":1,"candidate_edges":[]}\n```')["candidate_edges"], [])

    def test_audit_is_derived_from_payload_transcript_and_rebuild(self) -> None:
        artifacts = self.make_artifacts()
        checks = artifacts["audit"]["checks"]
        self.assertTrue(all(checks.values()))
        self.assertEqual(artifacts["audit"]["counts"]["model_calls"], 1)
        self.assertEqual(
            artifacts["audit"]["evidence"]["transcript"]["prompt_sha256"], "prompt",
        )
        self.assertEqual(artifacts["gate"]["verdict"], "pass")

    def test_reconstruction_rejects_rehashed_audit_tamper(self) -> None:
        artifacts = self.make_artifacts()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            filenames = {
                "dependency": "dependency-evidence.json",
                "file_list": "file-generation-list.json",
                "audit": "m4-audit.json",
                "gate": "m4-gate.json",
            }
            for key, filename in filenames.items():
                (root / filename).write_text(json.dumps(artifacts[key]), encoding="utf-8")
            verify_reconstructed_immutable(root, artifacts)
            audit = copy.deepcopy(artifacts["audit"])
            audit["checks"]["no_human_proof_import_graph_used"] = False
            audit.pop("audit_payload_sha256")
            audit["audit_payload_sha256"] = sha256_json(audit)
            (root / "m4-audit.json").write_text(json.dumps(audit), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not reconstruct"):
                verify_reconstructed_immutable(root, artifacts)

    def test_reconstruction_rejects_rehashed_file_coverage_tamper(self) -> None:
        artifacts = self.make_artifacts()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            filenames = {
                "dependency": "dependency-evidence.json",
                "file_list": "file-generation-list.json",
                "audit": "m4-audit.json",
                "gate": "m4-gate.json",
            }
            for key, filename in filenames.items():
                (root / filename).write_text(json.dumps(artifacts[key]), encoding="utf-8")
            file_list = copy.deepcopy(artifacts["file_list"])
            removed = file_list["layers"][0]["theories"].pop()
            file_list["serial_fallback"].remove(removed["file_slot"])
            file_list["proof_file_count"] -= 1
            file_list["contract_count"] -= removed["contract_count"]
            file_list.pop("list_payload_sha256")
            file_list["list_payload_sha256"] = sha256_json(file_list)
            (root / "file-generation-list.json").write_text(json.dumps(file_list), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not reconstruct"):
                verify_reconstructed_immutable(root, artifacts)

    def test_progress_ledger_rejects_unrecorded_status_and_revision(self) -> None:
        artifacts = self.make_artifacts()
        validate_progress_ledger(
            artifacts["progress"], artifacts["file_list"], self.support, artifacts["gate"],
        )
        corrupt = copy.deepcopy(artifacts["progress"])
        corrupt["proof_theories"][0]["status"] = "CORRUPTED"
        corrupt["revision"] = -999
        with self.assertRaises(ValueError):
            validate_progress_ledger(corrupt, artifacts["file_list"], self.support, artifacts["gate"])

    def test_progress_ledger_replays_legal_transition(self) -> None:
        artifacts = self.make_artifacts()
        progress = copy.deepcopy(artifacts["progress"])
        slot = progress["proof_theories"][0]["file_slot"]
        progress["revision"] = 1
        progress["events"] = [{
            "revision": 1, "kind": "proof-status", "file_slot": slot,
            "from_status": "pending", "to_status": "generating",
            "generation_id": "generation-001",
        }]
        progress["proof_theories"][0]["status"] = "generating"
        progress["proof_theories"][0]["generation_ids"] = ["generation-001"]
        validate_progress_ledger(progress, artifacts["file_list"], self.support, artifacts["gate"])

    def test_progress_ledger_rejects_unbound_generation_id(self) -> None:
        artifacts = self.make_artifacts()
        progress = copy.deepcopy(artifacts["progress"])
        slot = progress["proof_theories"][0]["file_slot"]
        progress["revision"] = 1
        progress["events"] = [{
            "revision": 1, "kind": "proof-status", "file_slot": slot,
            "from_status": "pending", "to_status": "generating",
        }]
        progress["proof_theories"][0]["status"] = "generating"
        progress["proof_theories"][0]["generation_ids"] = ["unrecorded-generation"]
        with self.assertRaisesRegex(ValueError, "generation_id"):
            validate_progress_ledger(progress, artifacts["file_list"], self.support, artifacts["gate"])


if __name__ == "__main__":
    unittest.main()
