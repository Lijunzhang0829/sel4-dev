import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from regen.preparation.environment_gate import (
    build_environment_gate,
    build_semantic_base_seal,
    payload_sha256,
    validate_environment_gate,
    write_environment_gate,
    write_semantic_base_seal,
)
from regen.preparation.premise_catalog import build_snapshot, verify_catalog, write_database
from regen.execution.proof_session_runner import init_run
from regen.preparation.refine_campaign import write_immutable_json
from regen.preparation.semantic_base_builder import sha256_path
from regen.legacy.session_planner import (
    build_artifacts,
    generation_tasks,
    write_artifacts,
)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


class EnvironmentGateTest(unittest.TestCase):
    def fixture(self, root: Path, *, complete: bool = True):
        campaign_id = "campaign-test"
        campaign = root / "Environment-Freeze/campaign-manifest.json"
        inventory = root / "Contract-Command-Inventory/private/command-inventory.json"
        ledger = root / "Command-Necessity/command-necessity-ledger.json"
        workspace = root / "Semantic-Base/workspace"
        heaps = root / "Semantic-Base/heaps"
        l4v = root / "Runtime/l4v"
        l4v_manifest = root / "Runtime/sanitized-l4v-runtime.json"
        build = root / "Semantic-Base/build-result.json"
        package_dir = root / "Contract-Command-Inventory/public/contract-packages"

        for directory in (workspace, heaps, l4v, package_dir):
            directory.mkdir(parents=True, exist_ok=True)
        (workspace / "Demo.thy").write_text("theory Demo imports Main begin end\n")
        (workspace / "ROOT").write_text("session Semantic_Base = HOL + theories Demo\n")
        (workspace / "Semantic_Root.thy").write_text(
            "theory Semantic_Root imports Demo begin end\n"
        )
        (workspace / "build.log").write_text("Finished Semantic_Base\n")
        (heaps / "Semantic_Base").write_text("heap")
        (l4v / "ROOT").write_text("session BaseRefine = HOL +\n")
        (l4v / "proof/refine/base").mkdir(parents=True)
        (l4v / "proof/refine/base/Base.thy").write_text(
            "theory Base imports Main begin end\n"
        )
        write_json(l4v_manifest, {
            "artifact_type": "sanitized-l4v-runtime",
            "destination": str(l4v.resolve()),
            "raw_target_refine_theory_count": 0,
        })
        write_json(campaign, {"campaign_id": campaign_id})
        inventory_value = {
            "schema_version": 2,
            "artifact_type": "refine-contract-command-inventory",
            "campaign_id": campaign_id,
            "counts": {"theories": 1, "contracts": 1, "commands": 2},
            "theories": [{"theory": "Refine.Demo", "contracts": [{"contract_id": "c"}]}],
        }
        write_json(inventory, inventory_value)
        write_json(ledger, {
            "schema_version": 1,
            "artifact_type": "command-necessity-ledger",
            "campaign_id": campaign_id,
            "verdict": "pass" if complete else "partial",
            "fixed_point_complete": complete,
            "selection_complete": complete,
            "retained_command_ids": ["definition"],
            "removed_command_ids": ["lemma"],
            "unresolved_command_ids": [],
            "passes": [{"pass": 1, "removed_command_ids": [], "decisions": []}],
        })
        workspace_manifest = {
            "schema_version": 1,
            "artifact_type": "semantic-base-workspace-manifest",
            "campaign_id": campaign_id,
            "session": "Semantic_Base",
            "base_import": "Semantic_Base.Semantic_Root",
            "retained_command_count": 1,
            "contract_shell_count": 1,
            "theories": [{
                "theory": "Refine.Demo",
                "workspace_path": "Demo.thy",
                "sha256": sha256_path(workspace / "Demo.thy"),
                "retained_semantic_commands": 1,
            }],
            "contract_shell_theories": ["Contract_Shell_Demo"],
        }
        isabelle = {
            "schema_version": 1,
            "artifact_type": "semantic-base-isabelle-build",
            "verdict": "pass",
            "session": "Semantic_Base",
            "build_log": {
                "path": "build.log",
                "sha256": sha256_path(workspace / "build.log"),
                "bytes": (workspace / "build.log").stat().st_size,
            },
            "container_image": {"reference": "test", "inspect_verdict": "pass"},
            "effective_command": ["isabelle", "build", "Semantic_Base"],
        }
        write_json(build, {"workspace": workspace_manifest, "isabelle": isabelle})
        write_json(package_dir / "Demo.thy.json", {
            "artifact_type": "contract-package",
            "campaign_id": campaign_id,
            "theory": "Refine.Demo",
            "file_slot": "Demo.thy",
            "contracts": [{"contract_id": "c"}],
        })
        return campaign, inventory, ledger, build, workspace, heaps, l4v, l4v_manifest, package_dir

    def test_partial_necessity_ledger_cannot_be_sealed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self.fixture(root, complete=False)
            with self.assertRaisesRegex(ValueError, "selection has not passed"):
                build_semantic_base_seal(root, *args[:8])

    def test_seal_catalog_gate_and_downstream_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            campaign, inventory, ledger, build, workspace, heaps, l4v, l4v_manifest, packages = (
                self.fixture(root)
            )
            seal = build_semantic_base_seal(
                root, campaign, inventory, ledger, build, workspace, heaps, l4v,
                l4v_manifest,
            )
            seal_path = root / "Semantic-Base/semantic-base-seal.json"
            disclosure_path = root / "Semantic-Base/context-effect-disclosure.json"
            write_semantic_base_seal(seal_path, seal)

            records = [{
                "record_id": "parent:demo",
                "canonical_name": "Main.demo",
                "exact_statement": "True",
                "owner_theory": "Main",
                "owner_session": "HOL",
                "source_class": "frozen_parent",
                "attributes": [],
            }]
            snapshot = build_snapshot(seal, records)
            snapshot_path = root / "Premise-Catalog/premise-snapshot.json"
            database_path = root / "Premise-Catalog/premises.sqlite3"
            write_immutable_json(snapshot_path, snapshot)
            write_database(database_path, records)
            disclosure = {
                "schema_version": 2,
                "artifact_type": "context-effect-disclosure",
                "campaign_id": seal["campaign_id"],
                "semantic_base_seal_id": seal["seal_id"],
                "status": "complete",
                "gate_requirement_satisfied": True,
                "coverage": {
                    "facts": True, "simp": True, "wp": True,
                    "cong": True, "weak_cong": True,
                    "classical_intro_elim": True,
                    "named_collections": True,
                },
                "premise_records_payload_sha256": payload_sha256(records),
                "exported_fact_count": len(records),
                "retained_exported_fact_count": 0,
                "raw_export": {
                    "facts_sha256": "facts",
                    "automation_pools_sha256": "pools",
                    "context_metadata_sha256": "metadata",
                },
            }
            write_immutable_json(disclosure_path, disclosure)

            gate = build_environment_gate(
                root, seal_path, disclosure_path, packages,
                snapshot_path, database_path,
            )
            gate_path = root / "Environment-Preparation/environment-gate.json"
            write_environment_gate(gate_path, gate)
            verified = validate_environment_gate(root, gate_path)
            self.assertTrue(verified["proof_generation_allowed"])
            self.assertEqual(verified["contract_packages"]["contract_count"], 1)
            self.assertEqual(verified["premise_catalog"]["record_count"], 1)

            tasks = generation_tasks(root, verified)
            policy = {
                "model": {"model": "codex-5.6", "max_candidate_edges": 8, "minimum_confidence": 0.5},
            }
            artifacts = build_artifacts(
                verified, tasks, policy, "policy-hash", {"input": "test"},
                {"schema_version": 1, "candidate_edges": []},
                transcript_meta={
                    "sealable_generation": True, "model": "codex-5.6",
                    "stage": "Session-Planning",
                    "provider": "codex-exec-audited",
                },
                transcript_prefix=root / "Session-Planning/transcript/session-planner",
            )
            write_artifacts(root / "Session-Planning", artifacts)
            file_list_path = root / "Session-Planning/refine-file-generation-list.json"
            proof_run = root / "Proof-Runs/run-test"
            run = init_run(
                proof_run, root, run_id="run-test",
                schedule_path=file_list_path,
            )
            self.assertEqual(
                run["environment_gate"]["environment_gate_id"],
                verified["gate_id"],
            )
            self.assertEqual(
                run["environment_gate"]["semantic_base_seal_id"],
                seal["seal_id"],
            )
            self.assertEqual(Path(run["premise_database"]), database_path)
            self.assertEqual(Path(run["semantic_workspace"]), workspace)
            files = json.loads(
                Path(run["session_progress_ledger"]).read_text()
            )
            self.assertEqual(files["active_run_id"], "run-test")
            self.assertEqual(
                [(item["theory"], item["layer"]) for item in files["files"]],
                [("Refine.Demo", 0)],
            )


    def test_raw_refine_source_is_independently_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self.fixture(root)
            leak = args[6] / "proof/refine/ARM/Leak.thy"
            leak.parent.mkdir(parents=True)
            leak.write_text("theory Leak imports Main begin end\n")
            with self.assertRaisesRegex(ValueError, "target Refine theories"):
                build_semantic_base_seal(root, *args[:8])

    def test_catalog_verifier_checks_every_database_column(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self.fixture(root)
            seal = build_semantic_base_seal(root, *args[:8])
            seal_path = root / "Semantic-Base/semantic-base-seal.json"
            write_semantic_base_seal(seal_path, seal)
            records = [{
                "record_id": "parent:demo",
                "canonical_name": "Main.demo",
                "declaration_name": "demo",
                "fact_index": 0,
                "exact_statement": "True",
                "normalized_statement_sha256": "statement-hash",
                "owner_theory": "Main",
                "owner_session": "HOL",
                "source_class": "frozen_parent",
                "attributes": [],
                "definition_equations": [],
                "publication_state": "published",
                "provenance": {"source": "test"},
            }]
            snapshot = build_snapshot(seal, records)
            snapshot_path = root / "Premise-Catalog/premise-snapshot.json"
            database_path = root / "Premise-Catalog/premises.sqlite3"
            write_immutable_json(snapshot_path, snapshot)
            write_database(database_path, records)
            verify_catalog(seal_path, snapshot_path, database_path)
            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    "UPDATE premises SET provenance_json = ? WHERE record_id = ?",
                    ('{"source":"drift"}', "parent:demo"),
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(ValueError, "does not reconstruct"):
                verify_catalog(seal_path, snapshot_path, database_path)

    def test_pending_disclosure_cannot_open_environment_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            campaign, inventory, ledger, build, workspace, heaps, l4v, l4v_manifest, packages = self.fixture(root)
            seal = build_semantic_base_seal(
                root, campaign, inventory, ledger, build, workspace, heaps, l4v,
                l4v_manifest,
            )
            seal_path = root / "Semantic-Base/semantic-base-seal.json"
            write_semantic_base_seal(seal_path, seal)
            records = [{
                "record_id": "parent:demo", "canonical_name": "Main.demo",
                "exact_statement": "True", "owner_theory": "Main",
                "owner_session": "HOL", "source_class": "frozen_parent",
                "attributes": [],
            }]
            snapshot = build_snapshot(seal, records)
            snapshot_path = root / "Premise-Catalog/premise-snapshot.json"
            database_path = root / "Premise-Catalog/premises.sqlite3"
            write_immutable_json(snapshot_path, snapshot)
            write_database(database_path, records)
            pending = root / "Semantic-Base/context-effect-disclosure.json"
            write_json(pending, {
                "artifact_type": "context-effect-disclosure",
                "status": "pending", "blocking": True,
            })
            with self.assertRaisesRegex(ValueError, "not complete"):
                build_environment_gate(
                    root, seal_path, pending, packages,
                    snapshot_path, database_path,
                )

    def test_gate_identity_and_runtime_base_are_rechecked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            campaign, inventory, ledger, build, workspace, heaps, l4v, l4v_manifest, packages = self.fixture(root)
            seal = build_semantic_base_seal(
                root, campaign, inventory, ledger, build, workspace, heaps, l4v,
                l4v_manifest,
            )
            seal_path = root / "Semantic-Base/semantic-base-seal.json"
            write_semantic_base_seal(seal_path, seal)
            records = [{
                "record_id": "parent:demo", "canonical_name": "Main.demo",
                "exact_statement": "True", "owner_theory": "Main",
                "owner_session": "HOL", "source_class": "frozen_parent",
                "attributes": [],
            }]
            snapshot = build_snapshot(seal, records)
            snapshot_path = root / "Premise-Catalog/premise-snapshot.json"
            database_path = root / "Premise-Catalog/premises.sqlite3"
            write_immutable_json(snapshot_path, snapshot)
            write_database(database_path, records)
            disclosure_path = root / "Semantic-Base/context-effect-disclosure.json"
            write_json(disclosure_path, {
                "artifact_type": "context-effect-disclosure",
                "campaign_id": seal["campaign_id"],
                "semantic_base_seal_id": seal["seal_id"],
                "status": "complete", "gate_requirement_satisfied": True,
                "coverage": {
                    "facts": True, "simp": True, "wp": True, "cong": True,
                    "weak_cong": True, "classical_intro_elim": True,
                    "named_collections": True,
                },
                "premise_records_payload_sha256": payload_sha256(records),
                "exported_fact_count": len(records),
                "retained_exported_fact_count": 0,
                "raw_export": {
                    "facts_sha256": "facts",
                    "automation_pools_sha256": "pools",
                    "context_metadata_sha256": "metadata",
                },
            })
            gate = build_environment_gate(
                root, seal_path, disclosure_path, packages,
                snapshot_path, database_path,
            )
            gate["runtime"]["base_import"] = "Wrong.Import"
            core = dict(gate)
            core.pop("gate_id")
            gate["gate_id"] = f"environment:{payload_sha256(core)[:20]}"
            gate_path = root / "Environment-Preparation/tampered-gate.json"
            write_json(gate_path, gate)
            with self.assertRaisesRegex(ValueError, "runtime identity differs"):
                validate_environment_gate(root, gate_path)


if __name__ == "__main__":
    unittest.main()
