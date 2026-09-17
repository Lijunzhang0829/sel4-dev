import copy
import json
import tempfile
import unittest
from pathlib import Path

from regen.preparation.refine_campaign import sha256_json
from regen.execution.proof_session_runner import run_session, save_progress, update_file
from regen.legacy.session_planner import (
    build_artifacts,
    build_layers,
    select_completed_transcript,
    validate_candidate_edges,
    validate_file_list,
    validate_progress_ledger,
)


class SessionPlannerRulesTest(unittest.TestCase):
    def setUp(self):
        self.tasks = [
            {
                "theory": "Refine.A_R", "file_slot": "A_R.thy",
                "contract_ids": ["contract-a"], "root_ids": ["contract-a"],
                "contracts": [{
                    "contract_id": "contract-a", "name": "foo_corres",
                    "exact_statement": "lemma foo_corres: corres dc P P' foo foo_H",
                }],
            },
            {
                "theory": "Refine.B_R", "file_slot": "B_R.thy",
                "contract_ids": ["contract-b"], "root_ids": ["contract-b"],
                "contracts": [{
                    "contract_id": "contract-b", "name": "bar_corres",
                    "exact_statement": "lemma bar_corres: corres dc Q Q' (bar foo) bar_H",
                }],
            },
            {
                "theory": "Refine.C_R", "file_slot": "C_R.thy",
                "contract_ids": ["contract-c"], "root_ids": ["contract-c"],
                "contracts": [{
                    "contract_id": "contract-c", "name": "baz_corres",
                    "exact_statement": "lemma baz_corres: corres dc R R' (baz foo) baz_H",
                }],
            },
        ]
        self.policy = {
            "model": {"model": "gpt-5.6-sol", "max_candidate_edges": 8, "minimum_confidence": 0.55},
        }
        self.gate = {
            "campaign_id": "campaign", "gate_id": "gate:one",
            "proof_generation_allowed": True,
            "contract_packages": {
                "payload_sha256": "packages", "package_count": 3,
                "contract_count": 3,
            },
        }

    def edge(self, provider, consumer, edge_type, contract_ids, symbols):
        return {
            "provider": provider, "consumer": consumer, "type": edge_type,
            "confidence": 0.8, "contract_ids": contract_ids,
            "symbols": symbols, "reason": "grounded test evidence",
        }

    def test_composition_layers_and_operation_same_layer_order(self):
        response = {
            "schema_version": 1,
            "candidate_edges": [
                self.edge("Refine.A_R", "Refine.B_R", "contract-composition",
                          ["contract-a", "contract-b"], ["foo"]),
                self.edge("Refine.C_R", "Refine.A_R", "operation-call",
                          ["contract-c", "contract-a"], ["foo"]),
            ],
        }
        accepted, rejected = validate_candidate_edges(
            response, self.tasks, self.policy,
        )
        self.assertEqual(rejected, [])
        layers, structure = build_layers(self.tasks, accepted)
        self.assertEqual(
            [[item["theory"] for item in layer["theories"]] for layer in layers],
            [["Refine.C_R", "Refine.A_R"], ["Refine.B_R"]],
        )
        self.assertEqual(len(structure["strong_edges"]), 1)
        self.assertEqual(len(structure["weak_edges"]), 1)

    def test_invalid_citations_and_symbols_are_rejected(self):
        response = {
            "schema_version": 1,
            "candidate_edges": [
                self.edge("Refine.A_R", "Refine.B_R", "contract-composition",
                          ["contract-a"], ["foo"]),
                self.edge("Refine.A_R", "Refine.C_R", "operation-call",
                          ["contract-a", "contract-c"], ["not_in_contracts"]),
            ],
        }
        accepted, rejected = validate_candidate_edges(
            response, self.tasks, self.policy,
        )
        self.assertEqual(accepted, [])
        reasons = [set(item["reasons"]) for item in rejected]
        self.assertIn("citations-do-not-cover-both-endpoints", reasons[0])
        self.assertIn("ungrounded-symbol-evidence", reasons[1])

    def test_composition_cycle_is_collapsed_and_disclosed(self):
        response = {
            "schema_version": 1,
            "candidate_edges": [
                self.edge("Refine.A_R", "Refine.B_R", "contract-composition",
                          ["contract-a", "contract-b"], ["foo"]),
                self.edge("Refine.B_R", "Refine.A_R", "contract-composition",
                          ["contract-b", "contract-a"], ["foo"]),
            ],
        }
        accepted, _ = validate_candidate_edges(response, self.tasks, self.policy)
        layers, structure = build_layers(self.tasks, accepted)
        self.assertEqual(len(layers), 1)
        self.assertEqual(structure["composition_sccs"], [["Refine.A_R", "Refine.B_R"]])

    def test_file_list_is_bound_to_exact_gate_and_packages(self):
        artifacts = build_artifacts(
            self.gate, self.tasks, self.policy, "policy", {"input": "test"},
            {"schema_version": 1, "candidate_edges": []},
            transcript_meta={
                    "sealable_generation": True, "model": "gpt-5.6-sol",
                    "stage": "Session-Planning",
                    "provider": "codex-exec-audited",
                },
            transcript_prefix=Path("transcript/session-planner"),
        )
        validate_file_list(artifacts["file_list"], self.gate)
        wrong_gate = dict(self.gate, gate_id="gate:two")
        with self.assertRaisesRegex(ValueError, "another environment gate"):
            validate_file_list(artifacts["file_list"], wrong_gate)
        changed = copy.deepcopy(artifacts["file_list"])
        changed["contract_package_payload_sha256"] = "other-packages"
        changed.pop("list_payload_sha256")
        changed["list_payload_sha256"] = sha256_json(changed)
        with self.assertRaisesRegex(ValueError, "another contract package set"):
            validate_file_list(changed, self.gate)


    def test_failed_physical_call_does_not_block_matching_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            preferred = Path(temporary) / "session-planner"
            retry = Path(str(preferred) + "--call-001")
            prompt = "planner input"
            for prefix, state, returncode, final in [
                (preferred, "failed", 1, ""),
                (retry, "complete", 0, "{}"),
            ]:
                for suffix, content in [
                    (".prompt.txt", prompt),
                    (".ndjson", "{}\n"),
                    (".final.txt", final),
                    (".stderr.txt", ""),
                    (".audit.json", "{}"),
                ]:
                    Path(str(prefix) + suffix).write_text(content, encoding="utf-8")
                Path(str(prefix) + ".meta.json").write_text(json.dumps({
                    "state": state, "returncode": returncode,
                    "timed_out": False,
                }), encoding="utf-8")
            from unittest.mock import patch
            with patch(
                "regen.legacy.session_planner.validate_call_artifact",
                return_value={"model": "gpt-5.6-sol"},
            ):
                selected = select_completed_transcript(
                    preferred, prompt=prompt, model="gpt-5.6-sol",
                )
            self.assertIsNotNone(selected)
            self.assertEqual(selected[0], retry)


    def test_progress_ledger_requires_real_theory_seals(self):
        artifacts = build_artifacts(
            self.gate, self.tasks, self.policy, "policy", {"input": "test"},
            {"schema_version": 1, "candidate_edges": []},
            transcript_meta={
                "sealable_generation": True, "model": "gpt-5.6-sol",
                "stage": "Session-Planning",
                "provider": "codex-exec-audited",
            },
            transcript_prefix=Path("transcript/session-planner"),
        )
        ledger = artifacts["ledger"]
        validate_progress_ledger(ledger, artifacts["file_list"], self.gate)
        ledger["files"][0]["status"] = "sealed"
        with self.assertRaisesRegex(ValueError, "lacks a theory seal"):
            validate_progress_ledger(ledger, artifacts["file_list"], self.gate)

    def test_session_traversal_continues_after_theory_failure(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = build_artifacts(
                self.gate, self.tasks, self.policy, "policy", {"input": "test"},
                {"schema_version": 1, "candidate_edges": []},
                transcript_meta={
                    "sealable_generation": True, "model": "gpt-5.6-sol",
                    "stage": "Session-Planning",
                    "provider": "codex-exec-audited",
                },
                transcript_prefix=Path("transcript/session-planner"),
            )
            planning = root / "Session-Planning"
            planning.mkdir()
            schedule = planning / "refine-file-generation-list.json"
            ledger_path = planning / "session-progress-ledger.json"
            schedule.write_text(json.dumps(artifacts["file_list"]))
            ledger = artifacts["ledger"]
            ledger["active_run_id"] = "run-test"
            ledger["session_status"] = "running"
            ledger_path.write_text(json.dumps(ledger))
            run_dir = root / "Proof-Run"
            run_dir.mkdir()
            run = {
                "run_id": "run-test",
                "session_schedule": {
                    "source": str(schedule),
                    "campaign_id": self.gate["campaign_id"],
                    "environment_gate_id": self.gate["gate_id"],
                },
                "session_progress_ledger": str(ledger_path),
            }
            (run_dir / "run.json").write_text(json.dumps(run))

            calls = []
            def fake_run_file(run_dir_arg, theory, **options):
                calls.append(theory)
                if theory == "Refine.A_R":
                    raise RuntimeError("synthetic failure")
                current = json.loads(ledger_path.read_text())
                update_file(
                    current, theory, "sealed",
                    generation_id=f"run-test--{theory}",
                    seal_id=f"theory-seal:{theory}",
                )
                save_progress(run_dir_arg, current)
                return next(
                    item for item in current["files"] if item["theory"] == theory
                )

            with patch("regen.execution.proof_session_runner.run_file", side_effect=fake_run_file):
                result = run_session(
                    run_dir, provider="test", planner_model="test",
                    revision_model="test", prover_model="test",
                    lemma_backend="whole-proof", timeout=1, max_attempts=1,
                    max_node_rounds=1, max_node_revisions=1,
                )
            self.assertEqual(calls, artifacts["file_list"]["serial_fallback"])
            self.assertEqual(result["session_status"], "partial")
            final = json.loads(ledger_path.read_text())
            statuses = {item["theory"]: item["status"] for item in final["files"]}
            self.assertEqual(statuses["Refine.A_R"], "failed")
            self.assertEqual(statuses["Refine.B_R"], "sealed")
            self.assertEqual(statuses["Refine.C_R"], "sealed")


if __name__ == "__main__":
    unittest.main()
