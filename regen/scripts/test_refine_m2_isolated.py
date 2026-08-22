#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from regen.refine_campaign import sha256_json
from regen.refine_m2_isolated import (
    PLATFORM,
    access_policy,
    artifact_binding,
    immutable_copy,
    l4v_relative_path,
    mount_args,
    reuse_contract_shell_cache,
    reuse_support_cache,
    support_heap_identity,
    tree_payload,
    verify_m2_artifacts,
    verify_payload_hash,
)


class RefineM2IsolatedTest(unittest.TestCase):
    def test_tree_payload_depends_on_file_content(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = Path(temp)
            theory = root / "A.thy"
            theory.write_text("theory A\n", encoding="utf-8")
            first = tree_payload(root)
            self.assertEqual(first["file_count"], 1)
            theory.write_text("theory B\n", encoding="utf-8")
            self.assertNotEqual(first["payload_sha256"], tree_payload(root)["payload_sha256"])

    def test_immutable_copy_is_idempotent_for_identical_content(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = Path(temp)
            source = root / "source"
            destination = root / "destination"
            source.write_bytes(b"stable heap bytes")
            self.assertEqual(immutable_copy(source, destination), "created")
            self.assertEqual(immutable_copy(source, destination), "unchanged")
            self.assertEqual(destination.read_bytes(), b"stable heap bytes")

    def test_l4v_relative_path_rejects_outside_source(self):
        self.assertEqual(
            l4v_relative_path("verification/l4v/proof/refine/ARM/Bits_R.thy"),
            "proof/refine/ARM/Bits_R.thy",
        )
        with self.assertRaisesRegex(ValueError, "outside l4v"):
            l4v_relative_path("other/Bits_R.thy")

    def test_mounts_do_not_expose_full_workspace(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = Path(temp)
            repo = root / "repo"
            l4v = root / "l4v"
            shells = root / "shells"
            heaps = root / "heaps"
            args = mount_args(repo, l4v, shells, heaps)
            rendered = " ".join(args)
            self.assertIn("dst=/sel4-project/verification/l4v,readonly", rendered)
            self.assertIn("dst=/m2,readonly", rendered)
            self.assertNotIn(f"src={repo},dst=/workspace", rendered)
            self.assertIn("--network none", rendered)

    def test_policy_records_no_workspace_or_docker_socket_mount(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = Path(temp)
            campaign = root / "campaign"
            inventory = campaign / "M1/private/proof-source-forbidden-inventory.json"
            inventory.parent.mkdir(parents=True)
            inventory.write_text(
                '{"exact_proof_sources":[{"source_path":'
                '"verification/l4v/proof/refine/ARM/Bits_R.thy"}]}'
            )
            policy = access_policy(
                root, campaign, image="image", image_id="sha256:test",
                sanitized_l4v=root / "l4v", workspace=root / "workspace",
                run_heaps=root / "heaps",
            )
            self.assertFalse(policy["full_host_workspace_mounted"])
            self.assertFalse(policy["docker_socket_mounted"])
            self.assertIn(
                f"/root/.isabelle/heaps/{PLATFORM}/Refine",
                policy["forbidden_read_paths"],
            )

    def make_bound_m2(self, root: Path) -> tuple[Path, Path]:
        campaign = root / "campaign"
        stage = campaign / "M2"
        preflight = campaign / "M2-preflight-0001"
        (campaign / "M0").mkdir(parents=True)
        (campaign / "M1").mkdir()
        stage.mkdir()
        preflight.mkdir()
        workspace_dir = preflight / "sanitized-workspace"
        sanitized_dir = stage / "isolated-environment/l4v"
        parent_platform = stage / "parent-cache/heaps" / PLATFORM
        run_platform = stage / "isolated-environment/run-heaps" / PLATFORM
        for path in (workspace_dir, sanitized_dir, parent_platform, run_platform):
            path.mkdir(parents=True)
        campaign_id = "test-campaign"

        def write(path: Path, value: dict) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")

        write(campaign / "M0/campaign-manifest.json", {"campaign_id": campaign_id})
        write(campaign / "M1/artifact-seal.json", {"artifact_type": "m1-artifact-seal"})
        write(
            campaign / "M1/private/proof-source-forbidden-inventory.json",
            {"exact_proof_sources": []},
        )
        workspace = {
            "artifact_type": "m2-sanitized-workspace-manifest",
            "campaign_id": campaign_id,
            "proof_package_count": 21,
            "contract_count": 287,
            "workspace_path": str(workspace_dir),
            "workspace_tree_identity": {
                "file_count": 0, "files_payload_sha256": sha256_json([]),
            },
        }
        workspace["manifest_payload_sha256"] = sha256_json(workspace)
        write(preflight / "sanitized-workspace-manifest.json", workspace)
        write(preflight / "workspace-content-audit.json", {"verdict": "pass"})
        sanitized = {
            "path": str(sanitized_dir),
            "file_count": 0,
            "tree_payload_sha256": sha256_json([]),
        }
        write(stage / "sanitized-l4v-manifest.json", sanitized)
        parent = {
            "artifact_type": "m2-parent-cache-manifest",
            "parent_cache_content_sha256": "parent-content",
            "platform": PLATFORM,
            "heap_records": [],
        }
        parent["parent_cache_payload_sha256"] = sha256_json(parent)
        write(stage / "parent-cache-manifest.json", parent)
        policy = {"artifact_type": "m2-isolated-access-policy", "network": "none"}
        policy["access_policy_sha256"] = sha256_json(policy)
        isolation = {
            "campaign_id": campaign_id,
            "verdict": "pass",
            "access_policy_identity": policy,
        }
        write(stage / "source-access-isolation-results.json", isolation)
        elaboration = {
            "campaign_id": campaign_id,
            "verdict": "pass",
            "returncode": 0,
        }
        write(stage / "contract-elaboration-results.json", elaboration)
        paths = {
            "m1_artifact_seal": campaign / "M1/artifact-seal.json",
            "preflight_workspace_manifest": preflight / "sanitized-workspace-manifest.json",
            "preflight_workspace_content_audit": preflight / "workspace-content-audit.json",
            "sanitized_l4v_manifest": stage / "sanitized-l4v-manifest.json",
            "parent_cache_manifest": stage / "parent-cache-manifest.json",
            "source_access_isolation_results": stage / "source-access-isolation-results.json",
            "contract_elaboration_results": stage / "contract-elaboration-results.json",
        }
        gate = {
            "artifact_type": "m2-gate",
            "campaign_id": campaign_id,
            "verdict": "pass",
            "workspace_content_audit": {"verdict": "pass"},
            "source_access_isolation": "pass",
            "contract_elaboration": "pass",
            "parent_cache_identity": "parent-content",
            "sanitized_l4v_identity": sha256_json([]),
            "support_cache_reuse_identity": None,
            "contract_shell_cache_reuse_identity": None,
            "support_heap_identity": None,
            "proof_package_count": 21,
            "contract_count": 287,
            "artifact_bindings": {
                name: artifact_binding(campaign, path) for name, path in paths.items()
            },
        }
        gate["gate_payload_sha256"] = sha256_json(gate)
        write(stage / "m2-gate.json", gate)
        return campaign, stage

    def test_m2_gate_binds_all_evidence(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            campaign, stage = self.make_bound_m2(Path(temp))
            self.assertEqual(verify_m2_artifacts(campaign, stage)["replay"], "unchanged")

    def test_m2_gate_rejects_bound_evidence_drift(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            campaign, stage = self.make_bound_m2(Path(temp))
            (stage / "contract-elaboration-results.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "size drifted|hash drifted"):
                verify_m2_artifacts(campaign, stage)

    def test_m2_gate_rejects_physical_workspace_drift(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            campaign, stage = self.make_bound_m2(Path(temp))
            workspace = campaign / "M2-preflight-0001/sanitized-workspace"
            (workspace / "injected.thy").write_text("theory Injected", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "physical workspace identity drifted"):
                verify_m2_artifacts(campaign, stage)

    def test_cache_reuse_records_source_gate_and_payload_identity(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = Path(temp)
            source_stage = root / "source-campaign/M2"
            source_preflight = root / "source-campaign/M2-preflight"
            current_preflight = root / "current-campaign/M2-preflight"
            run_heaps = root / "current-campaign/M2/isolated-environment/run-heaps"
            source_platform = source_stage / "isolated-environment/run-heaps" / PLATFORM
            (source_platform / "log").mkdir(parents=True)
            (run_heaps / PLATFORM / "log").mkdir(parents=True)
            for preflight in (source_preflight, current_preflight):
                support = preflight / "sanitized-workspace/frozen-support"
                shells = preflight / "sanitized-workspace/contract-shells"
                support.mkdir(parents=True)
                shells.mkdir(parents=True)
                (support / "Support.thy").write_text("theory Support", encoding="utf-8")
                (shells / "Shell.thy").write_text("theory Shell", encoding="utf-8")
            (source_platform / "M2_Frozen_Support").write_bytes(b"support heap")
            (source_platform / "log/M2_Frozen_Support.db").write_bytes(b"support log")
            (source_platform / "log/M2_Contract_Shells.db").write_bytes(b"shell log")
            source_gate = {
                "campaign_id": "source-campaign",
                "verdict": "pass",
                "contract_elaboration": "pass",
                "support_heap_identity": support_heap_identity(
                    source_stage / "isolated-environment/run-heaps"
                ),
            }
            (source_stage / "m2-gate.json").write_text(
                json.dumps(source_gate, sort_keys=True), encoding="utf-8"
            )

            support_reuse = reuse_support_cache(
                run_heaps,
                source_stage=source_stage,
                source_preflight=source_preflight,
                current_preflight=current_preflight,
            )
            contract_reuse = reuse_contract_shell_cache(
                run_heaps,
                source_stage=source_stage,
                source_preflight=source_preflight,
                current_preflight=current_preflight,
            )
            self.assertEqual(support_reuse["source_campaign_id"], "source-campaign")
            self.assertEqual(contract_reuse["source_campaign_id"], "source-campaign")
            self.assertEqual(
                support_reuse["source_gate_sha256"],
                contract_reuse["source_gate_sha256"],
            )
            verify_payload_hash(support_reuse, "reuse_payload_sha256")
            verify_payload_hash(contract_reuse, "reuse_payload_sha256")


if __name__ == "__main__":
    unittest.main()
