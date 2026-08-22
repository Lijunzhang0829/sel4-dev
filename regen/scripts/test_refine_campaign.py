#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from regen.refine_campaign import (
    build_manifest,
    verify_frozen_inputs,
    verify_manifest,
    write_immutable_json,
)


class RefineCampaignTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        (self.repo / "policy.py").write_text("POLICY = 1\n", encoding="utf-8")
        self.config = self.repo / "campaign.json"
        self.config.write_text(json.dumps({
            "schema_version": 1,
            "campaign_name": "test-refine",
            "architecture": "ARM",
            "setting": "B",
            "target_stages": [
                {"stage": "Refine", "target_session": "Refine"},
                {"stage": "CRefine", "target_session": "CRefine"},
            ],
            "experimental_claim": "test claim",
            "trust_boundary": {"forbidden_source_classes": ["human Proof body"]},
            "contract_selection_policy": {"id": "test-v1"},
            "model_backends": [{"model": "gpt-test"}],
            "budgets": {"attempts": 2},
            "completion_criteria": {"refine": "all sealed"},
            "frozen_input_files": ["policy.py"],
            "trusted_source_repositories": [],
            "forbidden_path_policy": {
                "id": "test-deny-v1",
                "static_forbidden_path_patterns": ["proof/private/*.thy"],
            },
        }, sort_keys=True), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def build(self):
        return build_manifest(
            self.repo, self.config, revision="a" * 40, worktree_clean=False,
        )

    def test_manifest_and_campaign_id_are_stable(self):
        first = self.build()
        second = self.build()
        self.assertEqual(first, second)
        self.assertEqual(first["model_calls_performed"], 0)
        self.assertNotIn("created_at", first)
        verify_manifest(first)

    def test_semantic_base_campaign_has_refine_only(self):
        config = json.loads(self.config.read_text(encoding="utf-8"))
        config["setting"] = "Semantic-Base"
        config["target_stages"] = [
            {"stage": "Refine", "target_session": "Refine"},
        ]
        self.config.write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
        manifest = self.build()
        self.assertEqual(manifest["setting"], "Semantic-Base")
        self.assertEqual(
            manifest["staging_rule"],
            "This campaign generates and seals Refine only",
        )
        verify_manifest(manifest)

    def test_frozen_input_change_changes_campaign(self):
        first = self.build()
        (self.repo / "policy.py").write_text("POLICY = 2\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "frozen input drift"):
            verify_frozen_inputs(self.repo, first, revision="a" * 40)
        second = self.build()
        self.assertNotEqual(first["campaign_id"], second["campaign_id"])
        self.assertNotEqual(first["manifest_payload_sha256"], second["manifest_payload_sha256"])

    def test_verifier_rejects_manifest_tampering(self):
        manifest = self.build()
        manifest["architecture"] = "RISCV64"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verify_manifest(manifest)

    def test_immutable_writer_accepts_replay_and_rejects_conflict(self):
        path = self.repo / "out" / "manifest.json"
        self.assertEqual(write_immutable_json(path, self.build()), "created")
        self.assertEqual(write_immutable_json(path, self.build()), "unchanged")
        changed = self.build()
        changed["architecture"] = "RISCV64"
        with self.assertRaises(FileExistsError):
            write_immutable_json(path, changed)

    def test_nested_trusted_repository_revision_and_scoped_cleanliness(self):
        source_repo = self.repo / "sources"
        (source_repo / "proof" / "refine").mkdir(parents=True)
        (source_repo / "proof" / "ROOT").write_text("session Refine\n", encoding="utf-8")
        (source_repo / "proof" / "refine" / "Bits_R.thy").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=source_repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source_repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=source_repo, check=True)
        subprocess.run(["git", "add", "proof"], cwd=source_repo, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=source_repo, check=True)

        config = json.loads(self.config.read_text(encoding="utf-8"))
        config["trusted_source_repositories"] = [{
            "id": "sources",
            "path": "sources",
            "require_clean": True,
            "required_clean_paths": ["proof/ROOT", "proof/refine"],
        }]
        self.config.write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
        manifest = self.build()
        nested = manifest["trusted_source_repositories"][0]
        self.assertEqual(nested["revision"], subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source_repo, text=True,
        ).strip())
        self.assertTrue(nested["paths_clean"])

        (source_repo / "proof" / "refine" / "Bits_R.thy").write_text("drift\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "trusted repository path drift"):
            verify_frozen_inputs(self.repo, manifest, revision="a" * 40)


if __name__ == "__main__":
    unittest.main()
