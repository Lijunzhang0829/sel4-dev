import sqlite3
import tempfile
import unittest
from pathlib import Path

from regen.isabelle_node_checker import IsabelleChecker
from regen.semantic_base_m2 import build_m2
from regen.semantic_base_m3 import write_database
from regen.semantic_base_pipeline import (
    build_checker_manifest,
    build_environment_manifest,
    build_file_generation_list,
    build_initial_premise_snapshot,
    build_layered_file_generation_list,
    build_progress_ledger,
    generation_tasks,
    publish_generated_records,
    reject_legacy_classification,
    update_file_status,
)


class SemanticBasePipelineTest(unittest.TestCase):
    def records(self):
        return [
            {
                "theory": "Semantic_A",
                "file_slot": "Semantic_A.thy",
                "fixed_declarations": ["Semantic_A.object"],
                "contract_ids": [],
                "framework_obligation_ids": [],
            },
            {
                "theory": "KHeap_R",
                "file_slot": "KHeap_R.thy",
                "fixed_declarations": ["KHeap_R.object_map"],
                "contract_ids": ["KHeap_R.getObject_corres"],
                "framework_obligation_ids": ["KHeap_R.instance_obligation"],
            },
            {
                "theory": "Endpoint_R",
                "file_slot": "Endpoint_R.thy",
                "fixed_declarations": [],
                "contract_ids": ["Endpoint_R.setEndpoint_corres"],
                "framework_obligation_ids": [],
            },
        ]

    def premise(self, record_id, source_class, **extra):
        return {
            "record_id": record_id,
            "canonical_name": record_id.replace(":", "."),
            "exact_statement": 'lemma x: "True"',
            "owner_theory": "Base",
            "owner_session": "BaseRefine",
            "source_class": source_class,
            "attributes": [],
            **extra,
        }

    def test_environment_has_no_file_category(self):
        manifest = build_environment_manifest("campaign-1", self.records())
        reject_legacy_classification(manifest)
        self.assertEqual(manifest["counts"]["theories"], 3)
        self.assertEqual(manifest["counts"]["contracts"], 2)

    def test_only_theories_with_roots_become_generation_tasks(self):
        manifest = build_environment_manifest("campaign-1", self.records())
        tasks = generation_tasks(manifest)
        self.assertEqual(
            [item["file_slot"] for item in tasks],
            ["Endpoint_R.thy", "KHeap_R.thy"],
        )
        self.assertEqual(
            tasks[1]["root_ids"],
            ["KHeap_R.getObject_corres", "KHeap_R.instance_obligation"],
        )

    def test_checker_manifest_binds_single_semantic_base(self):
        manifest = build_environment_manifest("campaign-1", self.records())
        checker = build_checker_manifest(
            "campaign-1", manifest, parent_sessions=["BaseRefine", "AInvs"],
        )
        self.assertEqual(checker["base_session"], "Semantic_Base")
        self.assertEqual(len(checker["contract_shell_ids"]), 2)
        reject_legacy_classification(checker)

    def test_initial_premises_reject_human_refine_theorems(self):
        initial = build_initial_premise_snapshot(
            "campaign-1",
            [
                self.premise("parent:x", "frozen_parent"),
                self.premise("semantic:y", "fixed_declaration_generated"),
            ],
        )
        self.assertEqual(len(initial["records"]), 2)
        with self.assertRaisesRegex(ValueError, "illegal premise source class"):
            build_initial_premise_snapshot(
                "campaign-1",
                [self.premise("refine:old", "human_refine_theorem")],
            )
        with self.assertRaisesRegex(ValueError, "human Refine theorem"):
            build_initial_premise_snapshot(
                "campaign-1",
                [
                    self.premise(
                        "refine:masked",
                        "fixed_semantic",
                        human_refine_source=True,
                    )
                ],
            )

    def test_dynamic_publication_requires_theory_seal(self):
        initial = build_initial_premise_snapshot(
            "campaign-1", [self.premise("parent:x", "frozen_parent")],
        )
        generated = self.premise(
            "generated:y",
            "generated_theorem",
            owner_session="Generated_Refine",
            theory_seal_id="seal-y",
        )
        updated = publish_generated_records(initial, [generated])
        self.assertEqual(len(updated["records"]), 2)
        generated.pop("theory_seal_id")
        with self.assertRaisesRegex(ValueError, "no theory seal"):
            publish_generated_records(initial, [generated])

    def test_layered_list_and_ledger_have_only_file_state(self):
        manifest = build_environment_manifest("campaign-1", self.records())
        file_list = build_layered_file_generation_list(
            "campaign-1",
            manifest,
            [("KHeap_R", "Endpoint_R")],
        )
        self.assertEqual(
            [item["theory"] for item in file_list["layers"][0]["theories"]],
            ["KHeap_R"],
        )
        ledger = build_progress_ledger(file_list)
        update_file_status(ledger, "KHeap_R.thy", "generating", "generation-1")
        update_file_status(ledger, "KHeap_R.thy", "sealed", "generation-1")
        self.assertEqual(ledger["files"][0]["status"], "sealed")
        reject_legacy_classification(file_list)
        reject_legacy_classification(ledger)

    def test_flat_file_list_remains_available_for_manual_schedule(self):
        manifest = build_environment_manifest("campaign-1", self.records())
        file_list = build_file_generation_list(
            "campaign-1",
            manifest,
            ["KHeap_R.thy", "Endpoint_R.thy"],
        )
        self.assertEqual(file_list["files"][0]["contract_ids"], [
            "KHeap_R.getObject_corres",
        ])

    def test_m2_blocks_unresolved_framework_command(self):
        manifest = build_environment_manifest("campaign-1", self.records())
        packages = [{
            "theory": "KHeap_R",
            "framework_review_items": [{
                "review_id": "review:function:x",
                "kind": "function",
                "resolution": "pending",
            }],
        }]
        gate = build_m2(
            manifest, packages, parent_sessions=["BaseRefine"],
        )
        self.assertEqual(gate["verdict"], "blocked")
        self.assertEqual(len(gate["pending_framework_reviews"]), 1)

    def test_m2_ready_still_requires_isabelle(self):
        manifest = build_environment_manifest("campaign-1", self.records())
        gate = build_m2(
            manifest,
            [{"theory": "KHeap_R", "framework_review_items": []}],
            parent_sessions=["BaseRefine"],
        )
        self.assertEqual(gate["verdict"], "ready-for-isabelle")
        self.assertIn("contract shells elaborate", gate["required_isabelle_checks"])

    def test_m3_database_matches_runtime_schema(self):
        record = self.premise("parent:x", "frozen_parent")
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "premises.sqlite3"
            write_database(database, [record])
            connection = sqlite3.connect(database)
            row = connection.execute(
                "SELECT canonical_name, source_class FROM premises"
            ).fetchone()
            connection.close()
        self.assertEqual(row, ("parent.x", "frozen_parent"))

    def test_checker_accepts_semantic_base_context(self):
        with tempfile.TemporaryDirectory() as directory:
            checker = IsabelleChecker(
                Path(directory),
                Path(directory) / "checks",
                base_session="Semantic_Base",
                base_import="Semantic_Base.Semantic_Root",
                base_catalog="/m2/semantic-base",
            )
            self.assertEqual(checker.imports(), ["Semantic_Base.Semantic_Root"])
            self.assertEqual(checker.base_session, "Semantic_Base")
            self.assertEqual(checker.base_catalog, "/m2/semantic-base")

    def test_legacy_fields_are_rejected(self):
        for field in ("classification", "proof_theories", "support_theories"):
            with self.assertRaisesRegex(ValueError, "legacy field"):
                reject_legacy_classification({field: []})


if __name__ == "__main__":
    unittest.main()
