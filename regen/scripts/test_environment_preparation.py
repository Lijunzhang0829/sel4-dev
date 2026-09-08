import tempfile
import unittest
from pathlib import Path

from regen.command_necessity import accept_green_baseline, necessity_fixed_point
from regen.contract_command_inventory import command_occurrences
from regen.environment_preparation import materialize_sanitized_l4v
from regen.scripts.regen_extract import slice_decls
from regen.semantic_base_builder import initial_retained_command_ids, materialize_workspace


class EnvironmentPreparationTest(unittest.TestCase):
    def test_sanitized_runtime_keeps_parent_and_removes_target_refine_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            (source / "proof/refine/base").mkdir(parents=True)
            (source / "proof/refine/ARM").mkdir(parents=True)
            (source / "lib").mkdir()
            (source / "spec/design").mkdir(parents=True)
            (source / "spec/cspec/c/build/generated").mkdir(parents=True)
            overlay = root / "overlay"
            (overlay / "spec/design").mkdir(parents=True)
            (source / "proof/refine/base/Include.thy").write_text("parent")
            (source / "proof/refine/ARM/KHeap_R.thy").write_text("human proof")
            (source / "proof/refine/Move_R.thy").write_text("human proof")
            (source / "lib/Base.thy").write_text("library")
            (source / "spec/design/Tracked.thy").write_text("tracked")
            (source / "spec/cspec/c/build/generated/root-owned").write_text(
                "irrelevant generated C artifact"
            )
            (overlay / "spec/design/Tracked.thy").write_text("must not overwrite")
            (overlay / "spec/design/API_H.thy").write_text("generated endpoint")
            destination = root / "runtime"
            manifest_path = root / "sanitized-l4v-runtime.json"
            manifest = materialize_sanitized_l4v(
                source, destination, manifest_path, overlay,
            )
            self.assertEqual(manifest["raw_target_refine_theory_count"], 0)
            self.assertTrue((destination / "proof/refine/base/Include.thy").is_file())
            self.assertTrue((destination / "lib/Base.thy").is_file())
            self.assertEqual(
                (destination / "spec/design/Tracked.thy").read_text(), "tracked",
            )
            self.assertEqual(
                (destination / "spec/design/API_H.thy").read_text(),
                "generated endpoint",
            )
            self.assertEqual(len(manifest["generated_overlay_files"]), 1)
            self.assertFalse((destination / "proof/refine/ARM").exists())
            self.assertFalse((destination / "proof/refine/Move_R.thy").exists())
            self.assertFalse((destination / "spec/cspec/c/build").exists())

    def test_semantic_core_seed_excludes_mixed_certificate_commands(self):
        inventory = {
            "theories": [{
                "commands": [
                    {"command_id": "definition", "kind": "definition", "role": "semantic_candidate"},
                    {"command_id": "interpretation", "kind": "interpretation", "role": "semantic_candidate"},
                    {"command_id": "ordinary", "kind": "lemma", "role": "ordinary_proof"},
                ],
            }],
        }
        self.assertEqual(initial_retained_command_ids(inventory), {"definition"})

    def test_semantic_core_seed_rejects_swallowed_proof_body(self):
        inventory = {
            "theories": [{
                "commands": [{
                    "command_id": "bad-definition",
                    "kind": "definition",
                    "role": "semantic_candidate",
                    "internal_proof_syntax_present": True,
                }],
            }],
        }
        with self.assertRaisesRegex(ValueError, "contain proof syntax"):
            initial_retained_command_ids(inventory)

    def test_inventory_splits_indented_outer_lemma_from_semantic_command(self):
        source = """theory Demo
imports Main
begin

abbreviation "x == True"
   lemma local_fact: "x"
     by simp
end
"""
        _header, commands = command_occurrences(
            source, "Refine.Demo", contract_starts={}, slice_decls=slice_decls,
        )
        self.assertEqual([item["kind"] for item in commands], [
            "abbreviation", "lemma", "end",
        ])
        self.assertEqual(commands[0]["source_end_line"], 5)
        self.assertEqual(commands[1]["role"], "ordinary_proof")

    def test_context_notes_begin_scope_is_paired(self):
        source = """theory Demo
imports Main
begin

context
notes option.case_cong_weak[cong]
begin
lemma local_fact: "True" by simp
end
end
"""
        _header, commands = command_occurrences(
            source, "Refine.Demo", contract_starts={}, slice_decls=slice_decls,
        )
        context, _notes, begin, _lemma, scope_end, _theory_end = commands
        self.assertEqual(begin["scope_prelude_command_id"], context["command_id"])
        self.assertEqual(begin["matching_end_command_id"], scope_end["command_id"])
        self.assertEqual(scope_end["matching_start_command_id"], begin["command_id"])

    def test_inventory_ignores_commented_declaration_and_ids_occurrences(self):
        source = '''theory Demo
imports Main
begin

(* lemma fake: "False" by simp *)
definition x where "x = True"
definition x2 where "x2 = True"
lemma target_corres: "corres dc P P' f g"
  by simp
end
'''
        contracts = {
            8: {
                "name": "target_corres",
                "contract_id": "contract:Demo:target_corres",
            }
        }
        _header, commands = command_occurrences(
            source, "Refine.Demo", contract_starts=contracts,
            slice_decls=slice_decls,
        )
        self.assertEqual([item["kind"] for item in commands], [
            "definition", "definition", "lemma", "end",
        ])
        self.assertEqual(len({item["command_id"] for item in commands}), 4)
        self.assertEqual(commands[2]["role"], "fixed_contract")

    def test_green_baseline_accepts_semantic_core_without_minimality_claim(self):
        inventory = {
            "campaign_id": "test",
            "counts": {"contracts": 1},
            "theories": [{"commands": [
                {"command_id": "core", "kind": "definition", "role": "semantic_candidate"},
                {"command_id": "mixed", "kind": "interpretation", "role": "semantic_candidate"},
            ]}],
        }
        with tempfile.TemporaryDirectory() as directory:
            result_path = Path(directory) / "baseline.json"
            result_path.write_text(__import__("json").dumps({
                "workspace": {
                    "campaign_id": "test",
                    "retained_command_count": 1,
                    "contract_shell_count": 1,
                },
                "isabelle": {"verdict": "pass", "wall_ms": 10},
            }))
            ledger = accept_green_baseline(inventory, result_path)
        self.assertEqual(ledger["verdict"], "pass")
        self.assertTrue(ledger["selection_complete"])
        self.assertFalse(ledger["command_minimality_claimed"])
        self.assertEqual(ledger["retained_command_ids"], ["core"])
        self.assertEqual(ledger["removed_command_ids"], ["mixed"])

    def test_necessity_uses_green_counterfactual_and_keeps_red(self):
        inventory = {
            "campaign_id": "test",
            "theories": [{
                "commands": [
                    {"command_id": "a", "kind": "definition", "role": "semantic_candidate"},
                    {"command_id": "b", "kind": "definition", "role": "semantic_candidate"},
                ],
            }],
        }

        def probe(retained, _command):
            return {"verdict": "pass" if "a" in retained else "fail"}

        result = necessity_fixed_point(inventory, probe)
        self.assertTrue(result["fixed_point_complete"])
        self.assertEqual(result["retained_command_ids"], ["a"])
        self.assertEqual(result["removed_command_ids"], ["b"])
        self.assertEqual(result["baseline_exclusion"]["decision"], "REMOVE_AS_GROUP")

    def test_baseline_only_records_group_removal_without_claiming_fixed_point(self):
        inventory = {
            "campaign_id": "test",
            "theories": [{
                "commands": [
                    {"command_id": "core", "kind": "definition", "role": "semantic_candidate"},
                    {"command_id": "mixed", "kind": "interpretation", "role": "semantic_candidate"},
                ],
            }],
        }
        result = necessity_fixed_point(
            inventory, lambda retained, _command: {"verdict": "pass"},
            baseline_only=True,
        )
        self.assertEqual(result["verdict"], "partial")
        self.assertFalse(result["fixed_point_complete"])
        self.assertEqual(result["removed_command_ids"], ["mixed"])
        self.assertEqual(result["passes"], [])

    def test_timeout_is_unresolved_not_keep(self):
        inventory = {
            "campaign_id": "test",
            "theories": [{
                "commands": [{"command_id": "a", "kind": "definition", "role": "semantic_candidate"}],
            }],
        }
        calls = 0

        def probe(_retained, command):
            nonlocal calls
            calls += 1
            return {"verdict": "pass" if command is None else "timeout"}

        result = necessity_fixed_point(inventory, probe)
        self.assertFalse(result["fixed_point_complete"])
        self.assertEqual(result["unresolved_command_ids"], ["a"])

    def test_materializer_removes_empty_scope_and_keeps_required_scope(self):
        inventory = {
            "campaign_id": "test",
            "counts": {"contracts": 0},
            "theories": [{
                "theory": "Refine.Demo",
                "workspace_path": "ARM/Demo.thy",
                "header": {
                    "raw_text": "theory Demo\nimports Main\nbegin\n",
                    "source_end_line": 3,
                },
                "contracts": [],
                "commands": [
                    {
                        "command_id": "scope-empty", "role": "structure",
                        "kind": "context", "source_start_line": 4,
                        "source_end_line": 5, "raw_text": "context begin\n",
                        "matching_end_command_id": "end-empty",
                    },
                    {
                        "command_id": "end-empty", "role": "structure",
                        "kind": "end", "source_start_line": 5,
                        "source_end_line": 6, "raw_text": "end\n",
                        "matching_start_command_id": "scope-empty",
                    },
                    {
                        "command_id": "scope-used", "role": "structure",
                        "kind": "context", "source_start_line": 6,
                        "source_end_line": 7, "raw_text": "context begin\n",
                        "matching_end_command_id": "end-used",
                    },
                    {
                        "command_id": "definition-x", "role": "semantic_candidate",
                        "kind": "definition", "source_start_line": 7,
                        "source_end_line": 8, "raw_text": 'definition x where "x = True"\n',
                        "enclosing_scope_command_ids": ["scope-used"],
                    },
                    {
                        "command_id": "end-used", "role": "structure",
                        "kind": "end", "source_start_line": 8,
                        "source_end_line": 9, "raw_text": "end\n",
                        "matching_start_command_id": "scope-used",
                    },
                    {
                        "command_id": "theory-end", "role": "structure",
                        "kind": "end", "source_start_line": 9,
                        "source_end_line": 9, "raw_text": "end\n",
                        "closes_theory": True,
                    },
                ],
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            materialize_workspace(inventory, Path(directory), {"definition-x"})
            source = (Path(directory) / "ARM" / "Demo.thy").read_text()
        self.assertEqual(source.count("context begin"), 1)
        self.assertIn("definition x", source)


if __name__ == "__main__":
    unittest.main()
