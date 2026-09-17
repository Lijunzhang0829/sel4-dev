#!/usr/bin/env python3
"""Unit tests for per-theory LLM and Isabelle audit sealing."""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from regen.infrastructure.audited_llm import sha256_text
from regen.infrastructure.llm_transcript import artifact_paths, record_replay
from regen.legacy.refine_arm_session.theory_trace import TheoryAuditTrail
from regen.legacy.refine_arm_session.theory_export_planner import critique_prompt, validate as validate_export_plan
from regen.legacy.refine_arm_session.theory_preflight import shortest_consumer_path
from regen.legacy.refine_arm_session.archacc_milestone import (validate_plan as validate_archacc_plan, validate_proofs as validate_archacc_proofs)


class TheoryAuditTrailTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.campaign = self.repo / "reports" / "regen" / "campaign"
        self.campaign.mkdir(parents=True)
        self.theory = "Refine.Test_R"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def real_call(self, prefix: Path, stage: str, *, success: bool = True) -> None:
        paths = artifact_paths(prefix)
        prefix.parent.mkdir(parents=True, exist_ok=True)
        values = {
            "prompt": "prove the supplied statement",
            "raw": json.dumps({
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "by simp"},
            }) + "\n",
            "final": "by simp\n",
            "stderr": "",
            "audit": json.dumps({
                "no_tool_events": True,
                "forbidden_item_types": [],
            }, sort_keys=True) + "\n",
        }
        if not success:
            values["raw"] = ""
            values["final"] = ""
            values["stderr"] = "provider timeout\n"
        for name, value in values.items():
            paths[name].write_text(value, encoding="utf-8")
        meta = {
            "schema_version": 2,
            "provider": "codex-exec-audited",
            "model": "test-model",
            "theory": self.theory,
            "stage": stage,
            "provider_network_call": True,
            "sealable_generation": True,
            "no_tool_events": True,
            "returncode": 0 if success else 124,
            "timed_out": not success,
            **{
                f"{name}_sha256": sha256_text(values[name])
                for name in ("prompt", "raw", "final", "stderr", "audit")
            },
        }
        paths["meta"].write_text(
            json.dumps(meta, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def check(self, trail: TheoryAuditTrail, label: str, verdict: str) -> Path:
        source = trail.run_dir / f"{label}.thy"
        log = trail.run_dir / f"{label}.log"
        source.write_text("theory Test_R imports Main begin end\n", encoding="utf-8")
        log.write_text("OK\n" if verdict == "pass" else "*** failed\n", encoding="utf-8")
        trail.record_isabelle(label, {
            "verdict": verdict,
            "returncode": 0 if verdict == "pass" else 1,
            "wall_ms": 10,
            "source": str(source.relative_to(self.repo)),
            "source_sha256": sha256_text(source.read_text(encoding="utf-8")),
            "log": str(log.relative_to(self.repo)),
        })
        return source

    def test_real_call_and_green_check_seal(self) -> None:
        trail = TheoryAuditTrail(
            self.campaign, self.theory, generation_id="real-green",
        )
        prefix = self.campaign / "planner" / "Test_R" / "01-plan"
        self.real_call(prefix, "plan")
        trail.record_llm_artifact(prefix, stage="plan")
        source = self.check(trail, "final", "pass")

        manifest = trail.seal(source, required_llm_stages={"plan"})
        self.assertEqual(manifest["status"], "production_audit_seal")
        self.assertEqual(manifest["replay_or_file_agent_calls"], 0)

    def test_failed_provider_call_is_preserved_before_retry(self) -> None:
        trail = TheoryAuditTrail(
            self.campaign, self.theory, generation_id="provider-retry",
        )
        failed = trail.run_dir / "llm" / "0001-plan"
        self.real_call(failed, "plan", success=False)
        trail.record_llm_artifact(failed, stage="plan")
        retry = trail.run_dir / "llm" / "0002-plan"
        self.real_call(retry, "plan")
        trail.record_llm_artifact(retry, stage="plan")
        source = self.check(trail, "final-retry", "pass")
        manifest = trail.seal(source, required_llm_stages={"plan"})
        self.assertEqual(manifest["llm_call_count"], 2)


    def test_replay_cannot_production_seal(self) -> None:
        trail = TheoryAuditTrail(
            self.campaign, self.theory, generation_id="replay",
        )
        record_replay(
            "prompt", "by simp",
            self.campaign / "planner" / "Test_R" / "01-plan-replay",
            theory=self.theory, stage="plan", transcript_sink=trail,
        )
        source = self.check(trail, "final-replay", "pass")
        with self.assertRaisesRegex(ValueError, "missing successful real LLM"):
            trail.seal(source, required_llm_stages={"plan"})

    def test_tampered_prompt_is_rejected(self) -> None:
        trail = TheoryAuditTrail(
            self.campaign, self.theory, generation_id="tamper",
        )
        prefix = self.campaign / "planner" / "Test_R" / "01-plan-tamper"
        self.real_call(prefix, "plan")
        trail.record_llm_artifact(prefix, stage="plan")
        artifact_paths(prefix)["prompt"].write_text("changed", encoding="utf-8")
        source = self.check(trail, "final-tamper", "pass")
        with self.assertRaisesRegex(ValueError, "artifact hash mismatch"):
            trail.seal(source, required_llm_stages={"plan"})

    def test_red_check_requires_repair_stage(self) -> None:
        trail = TheoryAuditTrail(
            self.campaign, self.theory, generation_id="repair-order",
        )
        plan = self.campaign / "planner" / "Test_R" / "01-plan-order"
        self.real_call(plan, "plan")
        trail.record_llm_artifact(plan, stage="plan")
        self.check(trail, "candidate-red", "fail")
        proof = self.campaign / "planner" / "Test_R" / "02-proof-order"
        self.real_call(proof, "proof")
        trail.record_llm_artifact(proof, stage="proof")
        source = self.check(trail, "final-order", "pass")
        with self.assertRaisesRegex(ValueError, "followed by a repair"):
            trail.seal(source, required_llm_stages={"plan", "proof"})

    def test_red_check_cannot_be_hidden_by_direct_green_retry(self) -> None:
        trail = TheoryAuditTrail(
            self.campaign, self.theory, generation_id="red-then-green",
        )
        plan = self.campaign / "planner" / "Test_R" / "01-plan-direct"
        self.real_call(plan, "plan")
        trail.record_llm_artifact(plan, stage="plan")
        self.check(trail, "candidate-red-direct", "fail")
        source = self.check(trail, "candidate-green-direct", "pass")
        with self.assertRaisesRegex(ValueError, "followed by a repair"):
            trail.seal(source, required_llm_stages={"plan"})

    def test_preflight_failure_is_recorded_in_generation(self) -> None:
        preflight = Path(__file__).resolve().parents[2] / "legacy/refine_arm_session/theory_preflight.py"
        result = subprocess.run(
            ["python3", str(preflight), "--campaign-dir", str(self.campaign),
             "--theory", self.theory, "--generation-id", "preflight-fail"],
            text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        events_path = (self.campaign / "audit" / "Test_R" /
                       "preflight-fail" / "events.jsonl")
        stages = [json.loads(line)["stage"]
                  for line in events_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(stages, ["preflight-start", "preflight-failed"])


    def test_support_empty_plan_requires_complete_contract_coverage(self) -> None:
        payload = {
            "target_theory": self.theory,
            "reachable_fixed_contract_summaries": [{"id": "contract:one"}],
        }
        incomplete = {
            "schema_version": 2, "theory": self.theory,
            "decision": "empty", "requirements": [],
            "contract_clusters": [], "empty_justification": "none needed",
        }
        with self.assertRaisesRegex(ValueError, "cover every reachable"):
            validate_export_plan(incomplete, payload)
        complete = dict(incomplete)
        complete["contract_clusters"] = [{
            "id": "cluster:one", "contract_ids": ["contract:one"],
            "coverage": "unresolved", "shared_symbols": ["corres"],
            "rationale": "consumer evidence is not yet decisive",
        }]
        validate_export_plan(complete, payload)

    def test_support_contract_clusters_cannot_overlap(self) -> None:
        payload = {
            "target_theory": self.theory,
            "reachable_fixed_contract_summaries": [{"id": "contract:one"}],
        }
        plan = {
            "schema_version": 2, "theory": self.theory,
            "decision": "empty", "requirements": [],
            "empty_justification": "both clusters claim the same contract",
            "contract_clusters": [
                {"contract_ids": ["contract:one"], "coverage": "unresolved"},
                {"contract_ids": ["contract:one"], "coverage": "unresolved"},
            ],
        }
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            validate_export_plan(plan, payload)

    def test_empty_plan_cannot_discard_export_candidate(self) -> None:
        payload = {
            "target_theory": self.theory,
            "reachable_fixed_contract_summaries": [{"id": "contract:one"}],
        }
        plan = {
            "schema_version": 2, "theory": self.theory,
            "decision": "empty", "requirements": [],
            "empty_justification": "candidate was left unimplemented",
            "contract_clusters": [{
                "contract_ids": ["contract:one"],
                "coverage": "target-export-candidate",
            }],
        }
        with self.assertRaisesRegex(ValueError, "cannot discard"):
            validate_export_plan(plan, payload)

    def test_shortest_consumer_path_follows_target_import_edges(self) -> None:
        graph = [
            {"name": "Refine.Support", "target_imports": []},
            {"name": "Refine.Middle", "target_imports": ["Refine.Support"]},
            {"name": "Refine.Consumer", "target_imports": ["Refine.Middle"]},
        ]
        self.assertEqual(
            shortest_consumer_path(graph, "Refine.Support", "Refine.Consumer"),
            ["Refine.Support", "Refine.Middle", "Refine.Consumer"],
        )

    def test_exports_cover_exactly_target_candidate_contracts(self) -> None:
        payload = {
            "target_theory": self.theory,
            "reachable_fixed_contract_summaries": [
                {"id": "contract:candidate"}, {"id": "contract:covered"},
            ],
        }
        plan = {
            "schema_version": 2, "theory": self.theory,
            "decision": "exports", "empty_justification": "",
            "contract_clusters": [
                {"contract_ids": ["contract:candidate"],
                 "coverage": "target-export-candidate"},
                {"contract_ids": ["contract:covered"],
                 "coverage": "existing-premises"},
            ],
            "requirements": [{
                "id": "req:one", "suggested_name": "regen_candidate",
                "statement": "lemma regen_candidate: True",
                "downstream_contract_ids": ["contract:candidate"],
                "risk": "low",
            }],
        }
        validate_export_plan(plan, payload)
        plan["requirements"][0]["downstream_contract_ids"] = ["contract:covered"]
        with self.assertRaisesRegex(ValueError, "cover exactly"):
            validate_export_plan(plan, payload)

    def test_support_critique_is_adversarial_to_false_empty(self) -> None:
        prompt = critique_prompt(
            {"consumer_import_paths": [], "reachable_fixed_contract_summaries": []},
            {"decision": "empty"},
        )
        self.assertIn("false-empty", prompt)
        self.assertIn("need not name its imported support theory", prompt)

    def test_shell_dry_run_uses_one_id_without_persisting(self) -> None:
        launcher = Path(__file__).resolve().parents[2] / "legacy/refine_arm_session/run_theory_generation.sh"
        preview = Path(self.temp.name) / "preview-campaign"
        result = subprocess.run(
            [str(launcher), "--theory", "Refine.InvariantUpdates_H",
             "--campaign-dir", str(preview), "--generation-id", "shell-test",
             "--dry-run"],
            text=True, capture_output=True, check=True,
        )
        self.assertEqual(result.stdout.count("--generation-id shell-test"), 3)
        self.assertIn("generation_id=shell-test (preview only; not persisted)",
                      result.stdout)
        self.assertFalse(preview.exists())

    def test_shell_allocates_one_timestamp_random_id(self) -> None:
        launcher = Path(__file__).resolve().parents[2] / "legacy/refine_arm_session/run_theory_generation.sh"
        preview = Path(self.temp.name) / "automatic-id-preview"
        result = subprocess.run(
            [str(launcher), "--theory", "Refine.Bits_R",
             "--campaign-dir", str(preview), "--dry-run"],
            text=True, capture_output=True, check=True,
        )
        ids = re.findall(r"generation_id=([0-9]{8}T[0-9]{6}Z-[0-9a-f]{8})",
                         result.stdout)
        self.assertEqual(len(ids), 1)
        self.assertIn(f"--generation-id {ids[0]}", result.stdout)
        self.assertFalse(preview.exists())

    def test_archacc_plan_rejects_duplicate_or_forward_contract_edges(self) -> None:
        contract_id = "contract:Refine.ArchAcc_R:test:123"
        payload = {
            "fixed_contracts": [{"id": contract_id}],
            "fixed_framework_declarations": [],
            "retrieved_legal_premises": [],
            "retrieved_legal_generated_premises": [],
        }
        plan = {
            "schema_version": 2, "theory": "Refine.ArchAcc_R",
            "families": [{"id": "family:test", "contracts": [contract_id],
                          "obligation_dimensions": ["direct"]}],
            "contract_order": [contract_id],
            "support_order": [], "support_nodes": [],
            "contract_nodes": [{"id": contract_id, "family": "family:test",
                                "dependencies": [], "strategy": "direct"}],
            "runtime_leaf_policy": {
                "max_new_leaves_per_contract": 1,
                "recursive_expansion": False,
                "statement_mutation": False,
            },
        }
        validate_archacc_plan(plan, payload)
        plan["contract_order"] = [contract_id, contract_id]
        with self.assertRaisesRegex(ValueError, "exactly once"):
            validate_archacc_plan(plan, payload)
        plan["contract_order"] = [contract_id]
        plan["contract_nodes"][0]["dependencies"] = ["contract:future"]
        with self.assertRaisesRegex(ValueError, "forward contract dependency"):
            validate_archacc_plan(plan, payload)

    def test_archacc_proof_validator_accepts_complete_structured_isar(self) -> None:
        node_id = "support:Refine.ArchAcc_R:regen_test"
        response = {
            "schema_version": 1, "theory": "Refine.ArchAcc_R",
            "proofs": [{"node_id": node_id,
                        "proof": "proof -\n  show ?thesis by simp\nqed"}],
        }
        self.assertEqual(
            validate_archacc_proofs(response, {node_id})[node_id],
            "proof -\n  show ?thesis by simp\nqed",
        )

    def test_archacc_proof_validator_rejects_incomplete_apply_or_proof(self) -> None:
        node_id = "support:Refine.ArchAcc_R:regen_test"
        for proof in ("apply simp", "proof -\n  show ?thesis"):
            response = {
                "schema_version": 1, "theory": "Refine.ArchAcc_R",
                "proofs": [{"node_id": node_id, "proof": proof}],
            }
            with self.assertRaisesRegex(ValueError, "illegal or incomplete"):
                validate_archacc_proofs(response, {node_id})

    def test_shell_registers_archacc_contract_pipeline(self) -> None:
        launcher = Path(__file__).resolve().parents[2] / "legacy/refine_arm_session/run_theory_generation.sh"
        result = subprocess.run(
            [str(launcher), "--theory", "Refine.ArchAcc_R",
             "--generation-id", "archacc-shell", "--dry-run"],
            text=True, capture_output=True, check=True,
        )
        self.assertIn("pipeline=archacc-contract", result.stdout)
        self.assertIn("regen/legacy/refine_arm_session/archacc_milestone.py", result.stdout)

    def test_shell_rejects_unregistered_theory(self) -> None:
        launcher = Path(__file__).resolve().parents[2] / "legacy/refine_arm_session/run_theory_generation.sh"
        result = subprocess.run(
            [str(launcher), "--theory", "Refine.Unknown_R", "--dry-run"],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("no production pipeline registered", result.stderr)



if __name__ == "__main__":
    unittest.main()
