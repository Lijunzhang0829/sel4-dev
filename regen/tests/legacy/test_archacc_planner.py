import ast
import copy
import inspect
import json
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

import regen.legacy.refine_arm_session.archacc_milestone as archacc


class ArchAccPlannerTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "fixed_contracts": [
                {"id": "contract:c1", "name": "c1", "statement": 'lemma c1: "True"'},
                {"id": "contract:c2", "name": "c2", "statement": 'lemma c2: "True"'},
            ],
            "fixed_framework_declarations": [
                {"id": "framework:f", "name": "f", "kind": "const", "statement": "consts f :: bool"},
            ],
            "retrieved_legal_premises": [],
            "retrieved_legal_generated_premises": [],
            "isabelle_residual_history": [],
        }
        self.analysis = {
            "schema_version": 2,
            "theory": archacc.THEORY,
            "families": [{
                "id": "family:f",
                "contracts": ["contract:c1", "contract:c2"],
                "obligation_dimensions": ["result relation"],
                "available_interfaces": ["framework:f"],
                "helper_requirements": [{
                    "dimension": "result relation",
                    "consumers": ["contract:c1"],
                    "reason": "shared projection",
                }],
            }],
            "cross_family_dependencies": [],
        }
        self.plan = {
            "schema_version": 2,
            "theory": archacc.THEORY,
            "families": [{
                "id": "family:f",
                "contracts": ["contract:c1", "contract:c2"],
                "obligation_dimensions": ["result relation"],
                "rationale": "test",
            }],
            "contract_order": ["contract:c1", "contract:c2"],
            "support_order": [
                f"support:{archacc.THEORY}:regen_s1",
                f"support:{archacc.THEORY}:regen_s2",
            ],
            "support_nodes": [
                {
                    "id": f"support:{archacc.THEORY}:regen_s1",
                    "name": "regen_s1",
                    "statement": 'lemma regen_s1: "f"',
                    "ownership": archacc.THEORY,
                    "family": "family:f",
                    "obligation_kind": "projection",
                    "dependencies": ["framework:f"],
                    "consumers": ["contract:c1"],
                    "rationale": "test",
                },
                {
                    "id": f"support:{archacc.THEORY}:regen_s2",
                    "name": "regen_s2",
                    "statement": 'lemma regen_s2: "f \\<and> f"',
                    "ownership": archacc.THEORY,
                    "family": "family:f",
                    "obligation_kind": "relation",
                    "dependencies": [f"support:{archacc.THEORY}:regen_s1"],
                    "consumers": ["contract:c2"],
                    "rationale": "test",
                },
            ],
            "contract_nodes": [
                {
                    "id": "contract:c1", "family": "family:f",
                    "dependencies": [f"support:{archacc.THEORY}:regen_s1"],
                    "strategy": "use projection",
                },
                {
                    "id": "contract:c2", "family": "family:f",
                    "dependencies": [f"support:{archacc.THEORY}:regen_s2"],
                    "strategy": "use relation",
                },
            ],
            "runtime_leaf_policy": {
                "max_new_leaves_per_contract": 1,
                "recursive_expansion": False,
                "statement_mutation": False,
            },
        }

    def test_valid_family_and_plan(self):
        archacc.validate_family_analysis(self.analysis, self.payload)
        archacc.validate_plan(self.plan, self.payload)

    def test_family_must_cover_every_contract_once(self):
        analysis = copy.deepcopy(self.analysis)
        analysis["families"][0]["contracts"] = ["contract:c1"]
        with self.assertRaisesRegex(ValueError, "cover every contract"):
            archacc.validate_family_analysis(analysis, self.payload)

    def test_support_cannot_restate_a_fixed_contract(self):
        plan = copy.deepcopy(self.plan)
        plan["support_nodes"][0]["statement"] = 'lemma regen_s1: "True"'
        with self.assertRaisesRegex(ValueError, "duplicate fixed contracts"):
            archacc.validate_plan(plan, self.payload)

    def test_support_order_rejects_forward_dependency(self):
        plan = copy.deepcopy(self.plan)
        plan["support_nodes"][0]["dependencies"] = [f"support:{archacc.THEORY}:regen_s2"]
        with self.assertRaisesRegex(ValueError, "forward support dependency"):
            archacc.validate_plan(plan, self.payload)

    def test_support_consumer_edge_must_be_explicit(self):
        plan = copy.deepcopy(self.plan)
        plan["contract_nodes"][0]["dependencies"] = []
        with self.assertRaisesRegex(ValueError, "consumer edge"):
            archacc.validate_plan(plan, self.payload)

    def test_duplicate_declaration_projection_prefers_framework_id(self):
        payload = copy.deepcopy(self.payload)
        payload["retrieved_legal_generated_premises"] = [{
            "id": "generated:f", "name": "f", "candidate_names": ["f"],
            "kind": "const", "statement": "consts f :: bool",
        }]
        self.assertEqual(archacc.fact_name_ids(payload, self.plan)["f"], {"framework:f"})

    def test_proof_cannot_hide_a_helper_edge(self):
        archacc.validate_plan(self.plan, self.payload)
        with self.assertRaisesRegex(ValueError, "undeclared DAG citations"):
            archacc.validate_proof_citations(
                self.payload, self.plan, "contract:c1", "by (rule regen_s2)",
            )

    def test_unique_legal_citation_completes_runtime_dependency(self):
        dependency_id = f"support:{archacc.THEORY}:regen_s2"
        completed, additions = archacc.complete_unique_citation_dependencies(
            self.payload, self.plan, "by (rule regen_s2)", [],
            {dependency_id},
        )
        self.assertEqual(completed, [dependency_id])
        self.assertEqual(additions, [{
            "fact_name": "regen_s2", "dependency_id": dependency_id,
        }])

    def test_ambiguous_or_illegal_citation_is_not_auto_completed(self):
        completed, additions = archacc.complete_unique_citation_dependencies(
            self.payload, self.plan, "by (rule regen_s2)",
            ["malformed:id"], set(),
        )
        self.assertEqual(completed, ["malformed:id"])
        self.assertEqual(additions, [])

    def test_structural_sibling_projects_only_machine_green_peer(self):
        pde_id = "contract:Refine.ArchAcc_R:getObject_PDE_corres:aaa"
        pte_id = "contract:Refine.ArchAcc_R:getObject_PTE_corres:bbb"
        plan = copy.deepcopy(self.plan)
        plan["contract_nodes"] = [
            {"id": pde_id, "dependencies": [], "family": "family:pde"},
            {"id": pte_id, "dependencies": [], "family": "family:pte"},
        ]
        templates = archacc.structural_sibling_proof_templates(
            plan, {pde_id: "by simp", "contract:other": "by simp"}, [pte_id],
        )
        self.assertEqual([item["node_id"] for item in templates], [pde_id])
        self.assertEqual(templates[0]["use"], "verified_structural_strategy_only")

    def test_isabelle_timeout_has_actionable_performance_diagnostic(self):
        diagnostic = archacc.isabelle_failure_diagnostic("", 124, 180)
        self.assertIn("proof-search performance failure", diagnostic)
        self.assertIn("Do not repeat", diagnostic)

    def test_isabelle_error_preserves_reported_residual(self):
        diagnostic = archacc.isabelle_failure_diagnostic("semantic residual", 1, 180)
        self.assertEqual(diagnostic, "semantic residual")

    def test_repair_diagnostic_is_bounded_to_latest_evidence(self):
        diagnostic = "old evidence\n" + ("x" * 40000) + "\nlatest residual"
        bounded = archacc.bounded_repair_diagnostic(diagnostic)
        self.assertLessEqual(
            len(bounded), archacc.REPAIR_DIAGNOSTIC_LIMIT + 100,
        )
        self.assertIn("older repair history omitted", bounded)
        self.assertTrue(bounded.endswith("latest residual"))

    def test_object_witness_residual_gets_ordering_hint(self):
        hint = archacc.residual_strategy_hint(
            "ko_wp_at' ... ksPSpace ... projectKO"
        )
        self.assertIn("Before `exI`", hint)
        self.assertIn("regen_ps_clear_lookupAround2", hint)
        self.assertIn("ARM_H.pde", hint)
        self.assertIn("Do not use `fastforce`", hint)
        self.assertEqual(archacc.residual_strategy_hint("other goal"), "")

    def test_residual_strategy_hint_closes_constant_pde_size_without_split(self):
        hint = archacc.residual_strategy_hint(
            "archObjSize (KOPDE x3) = archObjSize (KOPDE pde')"
        )
        self.assertIn("CONSTANT ARCH OBJECT-SIZE HINT", hint)
        self.assertIn("apply (simp add: archObjSize_def)", hint)
        self.assertIn("Do not split `x3`, `pde'`", hint)
        prefixed = archacc.residual_strategy_hint(
            "archObjSize (KOPDE x3) =\n*** archObjSize (KOPDE pde')"
        )
        self.assertIn("CONSTANT ARCH OBJECT-SIZE HINT", prefixed)

    def test_direct_state_update_hint_forbids_lookup_rule_regression(self):
        hint = archacc.residual_strategy_hint(
            "state_relation setObject pspace_relation "
            r"put (s\<lparr>kheap := updated\<rparr>)"
        )
        self.assertIn("DIRECT STATE-UPDATE PHASE", hint)
        self.assertIn("Do not cite\n`regen_ps_clear_lookupAround2`", hint)
        self.assertIn("updateObject_default_def", hint)
        self.assertIn("regen_pspace_dom_update", hint)
        self.assertIn("(1 :: word32) < 2 ^ pdeBits", hint)
        self.assertIn("exact `pdeBits_def`", hint)
        self.assertIn("get_object_def", hint)
        self.assertIn("unique state witness", hint)

    def test_direct_state_update_hint_survives_bounded_diagnostic(self):
        diagnostic = (
            "magnitudeCheck p x2 pdeBits b "
            "kheap := updated cdt_relation arch_state_relation"
        )
        self.assertTrue(archacc.direct_state_update_residual(diagnostic))
        self.assertIn(
            "DIRECT STATE-UPDATE PHASE",
            archacc.residual_strategy_hint(diagnostic),
        )

    def test_direct_state_phase_rejects_fastforce_and_object_discovery(self):
        diagnostic = (
            "magnitudeCheck p x2 pdeBits b "
            "kheap := updated cdt_relation arch_state_relation"
        )
        monad_closure = (
            "get_object_def bind_def gets_def get_def return_def "
            "assert_def fail_def"
        )
        self.assertIn(
            "broad fastforce",
            archacc.runtime_phase_strategy_error("by fastforce", diagnostic),
        )
        self.assertIn(
            "phase-inappropriate",
            archacc.runtime_phase_strategy_error(
                "by (rule regen_ps_clear_lookupAround2)", diagnostic,
            ),
        )
        self.assertIn(
            "mandatory abstract-monad",
            archacc.runtime_phase_strategy_error("by simp", diagnostic),
        )
        broad_relation = (
            "apply (simp add: " + monad_closure + ")\n"
            "apply (clarsimp simp: state_relation_def "
            "pspace_relation_def)\ndone"
        )
        self.assertIn(
            "broadly unfolds",
            archacc.runtime_phase_strategy_error(broad_relation, diagnostic),
        )
        same_command_relation = (
            "apply (clarsimp simp: " + monad_closure + " "
            "state_relation_def pspace_relation_def)\ndone"
        )
        self.assertIn(
            "broadly unfolds",
            archacc.runtime_phase_strategy_error(
                same_command_relation, diagnostic,
            ),
        )
        expanded_diagnostic = (
            diagnostic + " pspace_dom cdt_list_relation "
            "revokable_relation ksDomSchedule"
        )
        redundant_state_relation = (
            "apply (simp add: " + monad_closure + ")\n"
            "apply (clarsimp simp: state_relation_def)\ndone"
        )
        self.assertIn(
            "already contains its expanded conjunctions",
            archacc.runtime_phase_strategy_error(
                redundant_state_relation, expanded_diagnostic,
            ),
        )
        self.assertEqual(
            archacc.runtime_phase_strategy_error(
                "by (simp add: regen_lookupAround2_known1 "
                + monad_closure + ")",
                diagnostic,
            ),
            "",
        )

    def test_expanded_state_phase_protects_mandatory_principal_rule(self):
        diagnostic = (
            "magnitudeCheck p x2 pdeBits b kheap := updated "
            "cdt_relation arch_state_relation pspace_dom "
            "cdt_list_relation revokable_relation ksDomSchedule"
        )
        self.assertEqual(
            archacc.phase_mandatory_principal_rules(diagnostic),
            {
                "conjI", "dom_in", "dom_insert_absorb",
                "regen_pspace_dom_update",
            },
        )
        self.assertEqual(archacc.CONTRACT_REPAIR_LIMIT, 8)
        self.assertEqual(
            archacc.phase_mandatory_principal_rules("unrelated residual"),
            set(),
        )


    def test_expanded_state_phase_rejects_bulk_intro(self):
        diagnostic = (
            "magnitudeCheck p x2 pdeBits b kheap := updated "
            "cdt_relation arch_state_relation pspace_dom "
            "cdt_list_relation revokable_relation ksDomSchedule"
        )
        monad_closure = (
            "get_object_def bind_def gets_def get_def return_def "
            "assert_def fail_def"
        )
        bulk_proof = (
            "apply (simp add: " + monad_closure + ")\n"
            "apply (intro conjI allI ballI impI)\ndone"
        )
        self.assertIn(
            "at once",
            archacc.runtime_phase_strategy_error(bulk_proof, diagnostic),
        )
        missing_dom_update = (
            "apply (simp add: " + monad_closure + ")\n"
            "apply (rule conjI)\n"
            "apply (simp add: dom_fun_upd2)\ndone"
        )
        self.assertIn(
            "regen_pspace_dom_update",
            archacc.runtime_phase_strategy_error(
                missing_dom_update, diagnostic,
            ),
        )
        conditional_simp_only = (
            "apply (simp add: " + monad_closure + ")\n"
            "apply (rule conjI)\n"
            "apply (simp add: regen_pspace_dom_update dom_fun_upd2)\ndone"
        )
        self.assertIn(
            "explicit subst",
            archacc.runtime_phase_strategy_error(
                conditional_simp_only, diagnostic,
            ),
        )
        missing_first_split = (
            "apply (simp add: " + monad_closure + ")\n"
            "apply (subst regen_pspace_dom_update)\ndone"
        )
        self.assertIn(
            "mandatory first rule conjI",
            archacc.runtime_phase_strategy_error(
                missing_first_split, diagnostic,
            ),
        )
        schematic_old_object = (
            "apply (simp add: " + monad_closure + ")\n"
            "apply (rule conjI)\n"
            "apply (subst regen_pspace_dom_update)\ndone"
        )
        self.assertIn(
            "old object schematic",
            archacc.runtime_phase_strategy_error(
                schematic_old_object, diagnostic,
            ),
        )
        missing_dom_membership = (
            "apply (simp add: " + monad_closure + ")\n"
            "apply (rule conjI)\n"
            "apply (subst regen_pspace_dom_update[where "
            "x=\"ArchObj (PageDirectory pd)\"])\n"
            "apply (subst dom_insert_absorb)\n"
            "apply assumption\ndone"
        )
        self.assertIn(
            "membership via iffD1[OF dom_in]",
            archacc.runtime_phase_strategy_error(
                missing_dom_membership, diagnostic,
            ),
        )
        erule_membership = (
            "apply (simp add: " + monad_closure + ")\n"
            "apply (rule conjI)\n"
            "apply (subst regen_pspace_dom_update[where "
            "x=\"ArchObj (PageDirectory pd)\"])\n"
            "apply (subst dom_insert_absorb)\n"
            "apply (erule dom_in)\n"
            "apply assumption\ndone"
        )
        erule_error = archacc.runtime_phase_strategy_error(
            erule_membership, diagnostic,
        )
        self.assertIn("opposite simp orientation", erule_error)
        self.assertIn("apply (rule iffD1[OF dom_in])", erule_error)
        rule_membership = erule_membership.replace(
            "apply (erule dom_in)", "apply (rule dom_in)",
        )
        rule_error = archacc.runtime_phase_strategy_error(
            rule_membership, diagnostic,
        )
        self.assertIn("apply (rule dom_in)", rule_error)
        self.assertIn("apply (rule iffD1[OF dom_in])", rule_error)
        simp_membership = erule_membership.replace(
            "apply (erule dom_in)", "apply (simp add: dom_in)",
        )
        self.assertIn(
            "opposite simp orientation",
            archacc.runtime_phase_strategy_error(
                simp_membership, diagnostic,
            ),
        )
        premature_sym = (
            "apply (simp add: " + monad_closure + ")\n"
            "apply (rule conjI)\n"
            "apply (subst regen_pspace_dom_update[where "
            "x=\"ArchObj (PageDirectory pd)\"])\n"
            "apply (rule sym)\n"
            "apply (subst dom_insert_absorb)\n"
            "apply (rule iffD1[OF dom_in])\n"
            "apply (rule exI)\n"
            "apply assumption\n"
            "apply assumption\ndone"
        )
        self.assertIn(
            "premature orientation",
            archacc.runtime_phase_strategy_error(
                premature_sym, diagnostic,
            ),
        )
        bounded_first_conjunct = (
            "apply (simp add: " + monad_closure + ")\n"
            "apply (rule conjI)\n"
            "apply (subst regen_pspace_dom_update[where "
            "x=\"ArchObj (PageDirectory pd)\"])\n"
            "apply (subst dom_insert_absorb)\n"
            "apply (rule iffD1[OF dom_in])\n"
            "apply (rule exI)\n"
            "apply assumption\n"
            "apply assumption\ndone"
        )
        self.assertEqual(
            archacc.runtime_phase_strategy_error(
                bounded_first_conjunct, diagnostic,
            ),
            "",
        )

    def test_phase_gate_prefers_resumed_helper_diagnostic(self):
        helper_diagnostic = (
            "magnitudeCheck p x2 pdeBits b "
            "kheap := updated cdt_relation arch_state_relation"
        )
        self.assertIn(
            "broad fastforce",
            archacc.runtime_phase_strategy_error(
                "by fastforce", "shallow contract residual",
                helper_diagnostic,
            ),
        )

    def test_runtime_phase_rejection_diagnostic_preserves_latest_candidate(self):
        diagnostic = archacc.runtime_phase_rejection_diagnostic({
            "residual": "replace the invalid membership tactic",
            "repair": {
                "action": "local_helper",
                "proof": "apply (rule dom_in)\ndone",
            },
        })
        self.assertIn("RESUMED RUNTIME-PHASE REJECTION", diagnostic)
        self.assertIn("replace the invalid membership tactic", diagnostic)
        self.assertIn("apply (rule dom_in)", diagnostic)

    def test_runtime_phase_rejection_upgrade_corrects_disproved_simp_protocol(self):
        upgraded = archacc.upgrade_runtime_phase_rejection({
            "residual": "discharge membership with dom_in",
            "repair": {"proof": "apply (simp add: dom_in)"},
        })
        self.assertEqual(
            upgraded["protocol_version"],
            archacc.RUNTIME_PHASE_PROTOCOL_VERSION,
        )
        self.assertIn("iffD1[OF dom_in]", upgraded["residual"])
        self.assertIn("second `apply assumption`", upgraded["residual"])


    def test_rule_target_parser_handles_erule_consumption_count(self):
        self.assertEqual(
            archacc.proof_rule_targets(
                "apply (erule(1) regen_ps_clear_lookupAround2)\n"
                "apply (rule corres_no_failI)\ndone"
            ),
            {"regen_ps_clear_lookupAround2", "corres_no_failI"},
        )

    def test_payload_premise_ids_support_incremental_prompt_records(self):
        payload = {
            "retrieved_legal_premises": [{"id": "premise:a"}],
            "retrieved_legal_generated_premises": [
                {"id": "generated-premise:b"},
            ],
        }
        visible = archacc.payload_legal_premise_ids(payload)
        runtime_records = [
            {"id": "premise:a"}, {"id": "premise:c"},
            {"id": "generated-premise:b"},
        ]
        self.assertEqual(
            [item["id"] for item in runtime_records
             if item["id"] not in visible],
            ["premise:c"],
        )


    def test_runtime_exact_retrieval_keeps_scoped_out_inventory_record(self):
        with tempfile.TemporaryDirectory() as temp:
            campaign = Path(temp)
            record = {
                "id": "premise:set-object", "theory": "ExecSpec.PSpaceFuns_H",
                "session": "ExecSpec", "name": "setObject_def",
                "candidate_names": ["setObject_def"], "kind": "defs",
                "statement": "defs setObject_def: setObject p v = f p v",
            }
            record["statement_hash"] = archacc.sha256_text(
                " ".join(record["statement"].split())
            )
            records = [record]
            archacc.write_json(campaign / "premise_catalog.json", {
                "records": records,
                "records_sha256": archacc.sha256_text(
                    json.dumps(records, sort_keys=True)
                ),
            })
            archacc.write_json(campaign / "visibility.json", {
                archacc.THEORY: {
                    "frozen_external_ancestors": ["ExecSpec.PSpaceFuns_H"],
                },
            })
            payload = {"retrieved_legal_premises": [copy.deepcopy(record)]}
            retrieved = archacc.runtime_residual_premises(
                campaign, payload, "state update", k=0,
            )
        self.assertEqual([item["id"] for item in retrieved],
                         ["premise:set-object"])

    def test_runtime_lemmas_alias_exposes_split_def(self):
        with tempfile.TemporaryDirectory() as temp:
            campaign = Path(temp)
            record = {
                "id": "premise:crunch-simps", "theory": "BaseRefine.Crunch",
                "session": "BaseRefine", "name": "crunch_simps",
                "candidate_names": ["crunch_simps"], "kind": "lemmas",
                "statement": "lemmas crunch_simps = split_def whenE_def",
            }
            record["statement_hash"] = archacc.sha256_text(
                " ".join(record["statement"].split())
            )
            records = [record]
            archacc.write_json(campaign / "premise_catalog.json", {
                "records": records,
                "records_sha256": archacc.sha256_text(
                    json.dumps(records, sort_keys=True)
                ),
            })
            archacc.write_json(campaign / "visibility.json", {
                archacc.THEORY: {
                    "frozen_external_ancestors": ["BaseRefine.Crunch"],
                },
            })
            retrieved = archacc.runtime_residual_premises(
                campaign, {"retrieved_legal_premises": []},
                "state update", k=0,
            )
        self.assertEqual(retrieved[0]["id"], "premise:crunch-simps")
        self.assertIn("split_def", retrieved[0]["candidate_names"])

    def test_runtime_leaf_response_allows_intervening_harness_event(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            llm_dir = run_dir / "llm"
            llm_dir.mkdir()
            older = llm_dir / "0100-repair-13-3.final.txt"
            nearest = llm_dir / "0126-repair-13-3.final.txt"
            older.write_text("{}", encoding="utf-8")
            nearest.write_text("{}", encoding="utf-8")
            attempt = {
                "source": str(run_dir / "isabelle" /
                              "0128-ArchAcc_R_runtime_leaf_13_3" /
                              "ArchAcc_R_runtime_leaf_13_3.thy")
            }
            self.assertEqual(
                archacc.runtime_leaf_response_file(attempt, run_dir), nearest,
            )

    def test_resume_failure_record_preserves_bounded_semantic_residual(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "failed.log"
            log.write_text("prefix\n*** semantic residual", encoding="utf-8")
            semantic = archacc.resume_failure_record(
                {"returncode": 1, "wall_ms": 321, "log": str(log)},
                "apply simp")
        self.assertEqual(semantic["failure_kind"], "isabelle_semantic_failure")
        self.assertIn("semantic residual", semantic["diagnostic_excerpt"])
        self.assertEqual(semantic["previous_failed_proof"], "apply simp")
        timed_out = archacc.resume_failure_record(
            {"returncode": 124, "wall_ms": 180000}, "apply auto")
        self.assertEqual(timed_out["failure_kind"],
                         "isabelle_proof_search_timeout")

    def test_container_timeout_cleans_exact_isabelle_target(self):
        timed_out = archacc.subprocess.CompletedProcess([], 124, "", "")
        cleaned = archacc.subprocess.CompletedProcess([], 0, "", "")
        with (
            patch.object(archacc.subprocess, "run", side_effect=[timed_out, cleaned, cleaned]) as run,
            patch.object(archacc.time, "sleep"),
        ):
            result = archacc.container_run(
                ["isabelle", "process", "-T", "/workspace/audit/ArchAcc_R_prefix_11"],
                timeout=1,
            )
        self.assertEqual(result.returncode, 124)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(
            commands[1][-4:],
            ["pkill", "-TERM", "-f", "/workspace/audit/ArchAcc_R_prefix_11"],
        )
        self.assertEqual(commands[2][-4], "pkill")
        self.assertEqual(commands[2][-3], "-KILL")
    def test_prefix_probe_evidence_is_scoped_to_target(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            probe_dir = run_dir / "prefix-probes"
            probe_dir.mkdir()
            archacc.write_json(probe_dir / "evidence.json", {
                "schema_version": 1,
                "theory": archacc.THEORY,
                "node_id": "contract:hot",
                "localized_hotspot": "first inner simp",
            })
            self.assertEqual(len(archacc.load_prefix_probe_evidence(
                run_dir, ["contract:hot"])), 1)
            self.assertEqual(archacc.load_prefix_probe_evidence(
                run_dir, ["contract:other"]), [])
    def test_probe_strategy_rejects_broad_bundle_but_allows_simp_only(self):
        evidence = [{
            "prohibited_method": (
                "simp add: vs_ptr_align_def pte_relation_aligned_def "
                "pte_align'_def return_def pt_bits_def pageBits_def"
            ),
        }]
        with self.assertRaisesRegex(ValueError, "localized broad simp"):
            archacc.validate_probe_strategy(
                "by (clarsimp simp: vs_ptr_align_def pte_relation_aligned_def "
                "pte_align'_def return_def pt_bits_def pageBits_def)", evidence)
        with self.assertRaisesRegex(ValueError, "localized broad simp"):
            archacc.validate_probe_strategy(
                "by (fastforce simp: vs_ptr_align_def pte_relation_aligned_def "
                "pte_align'_def return_def pt_bits_def pageBits_def)", evidence)
        archacc.validate_probe_strategy(
            "apply (simp only: vs_ptr_align_def pte_relation_aligned_def "
            "pte_align'_def return_def pt_bits_def pageBits_def)\ndone",
            evidence)
        archacc.validate_probe_strategy(
            "apply (simp add: return_def)\n"
            "apply (case_tac aa; simp only: vs_ptr_align_def "
            "pte_relation_aligned_def pte_align'_def return_def "
            "pt_bits_def pageBits_def)\ndone",
            evidence)

    def test_probe_strategy_rejection_skips_isabelle_candidate(self):
        source = textwrap.dedent(inspect.getsource(archacc.plan_and_prove))
        tree = ast.parse(source)
        matching = [
            candidate
            for candidate in ast.walk(tree)
            if isinstance(candidate, ast.Try)
            and any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "validate_probe_strategy"
                for statement in candidate.body
                for call in ast.walk(statement)
            )
        ]
        self.assertEqual(len(matching), 1)
        self.assertTrue(any(
            isinstance(node, ast.Continue)
            for handler in matching[0].handlers
            for node in ast.walk(handler)
        ))

    def test_proof_cannot_name_unknown_generated_fact(self):
        with self.assertRaisesRegex(ValueError, "unknown generated facts"):
            archacc.validate_proof_citations(
                self.payload, self.plan, "contract:c1", "by (rule regen_missing)",
            )

    def test_proof_cannot_name_unknown_definition_fact(self):
        with self.assertRaisesRegex(ValueError, "unknown definition facts"):
            archacc.validate_proof_citations(
                self.payload, self.plan, "contract:c1",
                "by (simp add: obj_at'_def)",
            )

    def test_session_visible_definition_fact_is_not_unknown(self):
        payload = copy.deepcopy(self.payload)
        payload["_session_visible_fact_names"] = ["known_def"]
        archacc.validate_proof_citations(
            payload, self.plan, "contract:c1", "by (simp add: known_def)",
        )

    def test_machine_verified_proof_skips_fact_existence_recheck(self):
        archacc.validate_proof_citations(
            self.payload, self.plan, "contract:c1",
            "by (simp add: imported_baseline_def)",
            check_fact_existence=False,
        )

    def test_machine_verified_proof_prefix_stops_before_failure(self):
        frontier = {
            "failure_progress": 3,
            "proof": "apply first\napply second\napply failing\ndone",
        }
        self.assertEqual(
            archacc.machine_verified_proof_prefix(frontier),
            "apply first\napply second",
        )


    def test_failure_progress_rolls_multiline_apply_to_command_start(self):
        statement = 'lemma c1: "True"'
        proof = (
            "apply first\n"
            "apply (clarsimp simp: foo\n"
            "                      forbidden_rule)\n"
            "done"
        )
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "Check.thy"
            source_text = (
                "theory Check\nbegin\n" + statement + "\n" + proof
                + "\nend\n"
            )
            source.write_text(source_text, encoding="utf-8")
            failed_line = (
                source_text[:source_text.index("forbidden_rule")].count("\n")
                + 1
            )
            attempt = {
                "source": str(source),
                "diagnostic": (
                    "*** Failed to apply proof method (line "
                    + str(failed_line) + f' of "{source}")'
                ),
            }
            progress = archacc.proof_failure_progress(
                attempt, proof, statement,
            )
        self.assertEqual(progress, 2)
        self.assertEqual(
            archacc.machine_verified_proof_prefix({
                "failure_progress": progress, "proof": proof,
            }),
            "apply first",
        )


    def test_recorded_candidate_closure_adds_only_topological_predecessors(self):
        plan = copy.deepcopy(self.plan)
        plan["support_nodes"][1]["dependencies"] = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            llm_dir = root / "llm"
            llm_dir.mkdir()
            (llm_dir / "0001-proof.final.txt").write_text(json.dumps({
                "schema_version": 1,
                "theory": archacc.THEORY,
                "proofs": [{
                    "node_id": f"support:{archacc.THEORY}:regen_s2",
                    "proof": "by (simp add: regen_s1 f)",
                }],
            }))
            (llm_dir / "0002-forward.final.txt").write_text(json.dumps({
                "schema_version": 1,
                "theory": archacc.THEORY,
                "proofs": [{
                    "node_id": f"support:{archacc.THEORY}:regen_s1",
                    "proof": "by (rule regen_s2)",
                }],
            }))
            with patch.object(archacc, "REPO", root):
                changes = archacc.complete_recorded_candidate_premise_edges(
                    plan, self.payload, llm_dir,
                )

        dependencies = set(plan["support_nodes"][1]["dependencies"])
        self.assertEqual(dependencies, {
            "framework:f",
            f"support:{archacc.THEORY}:regen_s1",
        })
        self.assertEqual(
            {change["dependency_id"] for change in changes}, dependencies,
        )
        self.assertEqual(
            plan["support_nodes"][0]["dependencies"], ["framework:f"],
        )


    def test_historical_support_index_cannot_target_runtime_leaf(self):
        plan = copy.deepcopy(self.plan)
        helper = {
            "id": f"support:{archacc.THEORY}:regen_runtime",
            "name": "regen_runtime",
            "statement": 'lemma regen_runtime: "True"',
            "ownership": archacc.THEORY, "family": "family:f",
            "obligation_kind": "isabelle_residual_leaf",
            "dependencies": [], "consumers": ["contract:c1"],
            "rationale": "residual",
            "origin": "isabelle_residual_runtime_extension",
        }
        plan["support_nodes"].append(helper)
        plan["support_order"].append(helper["id"])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            llm_dir = root / "llm"
            llm_dir.mkdir()
            (llm_dir / "0001-repair-support-3-1.final.txt").write_text(
                json.dumps({
                    "action": "proof", "proof": "by (simp add: f)",
                }), encoding="utf-8",
            )
            with patch.object(archacc, "REPO", root):
                changes = archacc.complete_recorded_candidate_premise_edges(
                    plan, self.payload, llm_dir,
                )
        self.assertEqual(changes, [])
        self.assertEqual(helper["dependencies"], [])

    def test_runtime_leaf_ledger_restores_last_green_state(self):
        plan = copy.deepcopy(self.plan)
        helper = {
            "id": f"support:{archacc.THEORY}:regen_runtime",
            "name": "regen_runtime",
            "statement": 'lemma regen_runtime: "True"',
            "ownership": archacc.THEORY, "family": "family:f",
            "obligation_kind": "isabelle_residual_leaf",
            "dependencies": ["framework:f"], "consumers": ["contract:c1"],
            "rationale": "green",
            "origin": "isabelle_residual_runtime_extension",
        }
        polluted = copy.deepcopy(helper)
        polluted["dependencies"] = ["framework:f", "bogus:edge"]
        plan["support_nodes"].append(polluted)
        plan["support_order"].append(polluted["id"])
        with tempfile.TemporaryDirectory() as temp:
            ledger = Path(temp) / "runtime-leaf-extensions.jsonl"
            ledger.write_text(json.dumps({
                "status": "helper_revision_green", "helper": helper,
            }) + "\n", encoding="utf-8")
            changes = archacc.reconcile_runtime_leaves_from_ledger(plan, ledger)
        self.assertEqual(len(changes), 1)
        restored = next(
            node for node in plan["support_nodes"] if node["id"] == helper["id"]
        )
        self.assertEqual(restored, helper)

    def test_unverified_runtime_leaf_is_rolled_back(self):
        plan = copy.deepcopy(self.plan)
        helper = {
            "id": f"support:{archacc.THEORY}:regen_runtime",
            "name": "regen_runtime",
            "statement": 'lemma regen_runtime: "f"',
            "ownership": archacc.THEORY, "family": "family:f",
            "obligation_kind": "isabelle_residual_leaf",
            "dependencies": ["framework:f"], "consumers": ["contract:c1"],
            "rationale": "residual",
            "origin": "isabelle_residual_runtime_extension",
        }
        plan["support_nodes"].append(helper)
        plan["support_order"].append(helper["id"])
        plan["contract_nodes"][0]["dependencies"].append(helper["id"])
        changes = archacc.rollback_unverified_runtime_leaves(
            plan, [{"node_id": helper["id"], "verdict": "fail"}],
        )
        self.assertEqual([item["helper_id"] for item in changes], [helper["id"]])
        self.assertNotIn(helper["id"], plan["support_order"])
        self.assertNotIn(helper["id"], plan["contract_nodes"][0]["dependencies"])

    def test_green_runtime_leaf_is_retained(self):
        plan = copy.deepcopy(self.plan)
        helper = copy.deepcopy(plan["support_nodes"][0])
        helper["id"] = f"support:{archacc.THEORY}:regen_runtime"
        helper["name"] = "regen_runtime"
        helper["origin"] = "isabelle_residual_runtime_extension"
        plan["support_nodes"].append(helper)
        plan["support_order"].append(helper["id"])
        self.assertEqual(archacc.rollback_unverified_runtime_leaves(
            plan, [{"node_id": helper["id"], "verdict": "pass"}],
        ), [])
        self.assertIn(helper["id"], plan["support_order"])

    def test_subgoal_count_and_frontier_rank_are_monotone(self):
        self.assertEqual(
            archacc.diagnostic_subgoal_count("*** goal (3 subgoals):"), 3,
        )
        semantic_three = {
            "verdict": "fail", "returncode": 1, "subgoal_count": 3,
            "wall_ms": 73000,
        }
        semantic_eight = semantic_three | {"subgoal_count": 8}
        timed_out = semantic_three | {"returncode": 124, "subgoal_count": None}
        self.assertLess(
            archacc.failure_frontier_rank(semantic_three)[:2],
            archacc.failure_frontier_rank(semantic_eight)[:2],
        )
        self.assertLess(
            archacc.failure_frontier_rank(semantic_eight)[:2],
            archacc.failure_frontier_rank(timed_out)[:2],
        )

    def test_consumer_attempt_epoch_starts_after_latest_green_helper(self):
        attempts = [
            {"node_id": "contract:c1", "verdict": "fail"},
            {"node_id": "support:h", "contract_id": "contract:c1",
             "role": "runtime_leaf", "verdict": "pass"},
            {"node_id": "contract:c1", "verdict": "fail", "tag": "old"},
            {"node_id": "support:h", "contract_id": "contract:c1",
             "role": "runtime_leaf_revision", "verdict": "pass"},
            {"node_id": "contract:c1", "verdict": "fail", "tag": "current"},
        ]
        epoch = archacc.attempts_after_latest_runtime_leaf(
            attempts, "contract:c1",
        )
        self.assertEqual([item.get("tag") for item in epoch], ["current"])

    def test_best_frontier_uses_full_log_and_latest_equal_candidate(self):
        statement = 'lemma c1: "True"'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            attempts = []
            for sequence, proof in ((1, "by simp"), (2, "by blast")):
                source = root / f"{sequence:04d}-check.thy"
                log = root / f"{sequence:04d}-check.log"
                source.write_text(
                    "theory Check\nbegin\n" + statement + "\n" + proof
                    + "\nlemma next: \"True\"\n  by simp\nend\n",
                    encoding="utf-8",
                )
                log.write_text(
                    "*** goal (3 subgoals):\n" + ("residual " * 4000),
                    encoding="utf-8",
                )
                attempts.append({
                    "node_id": "contract:c1", "verdict": "fail",
                    "returncode": 1, "wall_ms": 100,
                    "source": str(source), "log": str(log),
                })
            with patch.object(archacc, "REPO", root):
                frontier = archacc.best_failed_frontier(
                    attempts, "contract:c1", statement,
                )
        self.assertEqual(frontier["subgoal_count"], 3)
        self.assertEqual(frontier["proof"], "by blast")
        self.assertTrue(frontier["source"].endswith("0002-check.thy"))

    def test_best_frontier_prefers_deeper_failure_at_equal_subgoals(self):
        statement = 'lemma c1: "True"'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            attempts = []
            candidates = [
                ("deep", "apply (rule TrueI)\napply simp\ndone", "apply simp"),
                ("shallow", "apply blast\ndone", "apply blast"),
            ]
            for sequence, (label, proof, failed_command) in enumerate(
                    candidates, start=1):
                source = root / f"{sequence:04d}-{label}.thy"
                log = root / f"{sequence:04d}-{label}.log"
                source_text = (
                    "theory Check\nbegin\n" + statement + "\n" + proof
                    + "\nlemma next: \"True\"\n  by simp\nend\n"
                )
                source.write_text(source_text, encoding="utf-8")
                failed_line = (
                    source_text[:source_text.index(failed_command)].count("\n") + 1
                )
                log.write_text(
                    "*** Failed to apply proof method (line "
                    + str(failed_line) + f' of "{source}")\n'
                    + "*** goal (1 subgoal):\nresidual",
                    encoding="utf-8",
                )
                attempts.append({
                    "node_id": "contract:c1", "verdict": "fail",
                    "returncode": 1, "wall_ms": 100,
                    "source": str(source), "log": str(log),
                })
            with patch.object(archacc, "REPO", root):
                frontier = archacc.best_failed_frontier(
                    attempts, "contract:c1", statement,
                )
        self.assertEqual(frontier["proof"], candidates[0][1])
        self.assertEqual(frontier["failure_progress"], 2)
        self.assertEqual(attempts[0]["failure_progress"], 2)
        self.assertEqual(attempts[1]["failure_progress"], 1)

    def test_runtime_leaf_revision_must_change_proposition(self):
        current = 'lemma helper: "True"'
        self.assertFalse(archacc.runtime_leaf_revision_changes_statement(
            current, 'lemma  helper:   "True"',
        ))
        self.assertTrue(archacc.runtime_leaf_revision_changes_statement(
            current, 'lemma helper: "P"',
        ))

    def test_outer_proof_prefix_rejects_replacement_tail(self):
        frontier = "apply (rule corres_no_failI)\napply simp\ndone"
        complete = "apply (rule corres_no_failI)\napply blast\ndone"
        tail = "apply (rule regen_runtime_boundary)\ndone"
        self.assertTrue(archacc.preserves_outer_proof_prefix(complete, frontier))
        self.assertFalse(archacc.preserves_outer_proof_prefix(tail, frontier))

    def test_trailing_contract_plateau_counts_equal_failures(self):
        attempts = [
            {"node_id": "contract:c1", "verdict": "fail",
             "subgoal_count": 4},
            {"node_id": "support:s1", "verdict": "pass",
             "subgoal_count": None},
            {"node_id": "contract:c1", "verdict": "fail",
             "subgoal_count": 3},
            {"node_id": "contract:c1", "verdict": "fail",
             "subgoal_count": 3},
            {"node_id": "contract:c1", "verdict": "fail",
             "subgoal_count": 3},
        ]
        self.assertEqual(archacc.trailing_contract_subgoal_plateau(
            attempts, "contract:c1", 3,
        ), 3)
        self.assertEqual(archacc.trailing_contract_subgoal_plateau(
            attempts, "contract:c1", 2,
        ), 0)

    def test_green_runtime_leaf_revision_resets_contract_plateau(self):
        attempts = [
            {"node_id": "contract:c1", "verdict": "fail",
             "subgoal_count": 1},
            {"node_id": "contract:c1", "verdict": "fail",
             "subgoal_count": 1},
            {"node_id": "support:helper", "contract_id": "contract:c1",
             "role": "runtime_leaf_revision", "verdict": "pass"},
            {"node_id": "contract:c1", "verdict": "fail",
             "subgoal_count": 1},
        ]
        self.assertEqual(archacc.trailing_contract_subgoal_plateau(
            attempts, "contract:c1", 1,
        ), 1)

    def test_contract_plateau_ignores_shallower_equal_subgoal_failures(self):
        attempts = [
            {"node_id": "contract:c1", "verdict": "fail",
             "subgoal_count": 1, "failure_progress": 65},
            {"node_id": "contract:c1", "verdict": "fail",
             "subgoal_count": 1, "failure_progress": 63},
            {"node_id": "contract:c1", "verdict": "fail",
             "subgoal_count": 1, "failure_progress": 65},
        ]
        self.assertEqual(archacc.trailing_contract_subgoal_plateau(
            attempts, "contract:c1", 1, 65,
        ), 2)

    def test_frontier_rank_regressions_count_since_green_helper(self):
        attempts = [
            {"node_id": "contract:c1", "verdict": "fail",
             "subgoal_count": 1, "failure_progress": 20},
            {"node_id": "support:helper", "contract_id": "contract:c1",
             "role": "runtime_leaf_revision", "verdict": "pass"},
            {"node_id": "contract:c1", "verdict": "fail",
             "subgoal_count": 1, "failure_progress": 65},
            {"node_id": "contract:c1", "verdict": "fail",
             "subgoal_count": 1, "failure_progress": 63},
            {"node_id": "support:other", "verdict": "fail",
             "subgoal_count": 1, "failure_progress": 1},
            {"node_id": "contract:c1", "verdict": "fail",
             "subgoal_count": 1, "failure_progress": 62},
            {"node_id": "contract:c1", "verdict": "fail",
             "subgoal_count": 2, "failure_progress": 10},
        ]
        self.assertEqual(archacc.trailing_contract_frontier_regressions(
            attempts, "contract:c1", 1, 65,
        ), 3)

    def test_runtime_dependency_rejection_names_invalid_and_exact_contract_ids(self):
        diagnostic = archacc.runtime_dependency_rejection(
            ["premise:good", "premise:guessed"],
            {"premise:good", "premise:exact", "support:green"},
            ["premise:exact", "premise:good", "contract:not-green"],
        )
        self.assertIn('"premise:guessed"', diagnostic)
        self.assertIn('["premise:exact","premise:good"]', diagnostic)
        self.assertNotIn('"support:green"', diagnostic)

    def test_runtime_generated_retrieval_includes_exact_obj_at_export(self):
        name = "regen_obj_at" + chr(39) + "_def"
        records = [{
            "id": "generated-premise:obj-at",
            "theory": "Refine.Invariants_H", "session": "GeneratedRefine",
            "name": name, "candidate_names": [name], "kind": "lemma",
            "statement": "lemma exact_obj_at: True",
        }, {
            "id": "generated-premise:other",
            "theory": "Refine.Invariants_H", "session": "GeneratedRefine",
            "name": "other_fact", "candidate_names": ["other_fact"],
            "kind": "lemma", "statement": "lemma other_fact: True",
        }]
        with tempfile.TemporaryDirectory() as temp:
            campaign = Path(temp)
            archacc.write_json(campaign / "visibility.json", {
                archacc.THEORY: {
                    "generated_target_ancestors": ["Refine.Invariants_H"],
                },
            })
            with patch.object(archacc, "generated_premises", return_value=records):
                retrieved = archacc.runtime_residual_generated_premises(
                    campaign, {"retrieved_legal_generated_premises": []},
                    "unrelated query", k=1,
                )
        self.assertIn(
            "generated-premise:obj-at",
            {item["id"] for item in retrieved},
        )

    def test_proof_rule_targets_find_principal_rule(self):
        proof = "apply (rule corres_guard_imp)\napply (rule setObject_other_corres)\ndone"
        self.assertEqual(
            archacc.proof_rule_targets(proof),
            {"corres_guard_imp", "setObject_other_corres"},
        )

    def test_verified_prefix_rules_are_not_marked_exhausted(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            (run_dir / "llm").mkdir()
            attempts = []
            for sequence in (10, 12, 14):
                source = run_dir / f"{sequence:04d}-helper" / "helper.thy"
                source.parent.mkdir()
                response = {
                    "action": "local_helper",
                    "proof": (
                        "apply (rule corres_no_failI)\n"
                        "apply (rule setObject_other_corres)\ndone"
                    ),
                }
                (
                    run_dir / "llm"
                    / f"{sequence - 1:04d}-repair-1.final.txt"
                ).write_text(json.dumps(response), encoding="utf-8")
                attempts.append({
                    "contract_id": "contract:c1", "role": "runtime_leaf",
                    "verdict": "fail", "source": str(source),
                })
            exhausted = archacc.repeated_runtime_leaf_principal_rules(
                attempts, "contract:c1", run_dir,
                excluded_rules={"corres_no_failI"},
            )
        self.assertEqual(exhausted, ["setObject_other_corres"])

    def test_runtime_leaf_frontier_prefers_fewer_subgoals(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = root / "run"
            (run_dir / "llm").mkdir(parents=True)
            attempts = []
            for sequence, subgoals in ((10, 2), (12, 3)):
                source_dir = root / f"{sequence:04d}-helper"
                source_dir.mkdir()
                source = source_dir / "helper.thy"
                log = source_dir / "helper.log"
                statement = 'lemma regen_helper: "True"'
                proof = "apply simp\ndone"
                source.write_text(
                    statement + "\n" + proof + "\n", encoding="utf-8",
                )
                log.write_text(
                    f'*** Failed to apply proof method (line 2 of "{source}")\n'
                    f"*** goal ({subgoals} subgoals):\nresidual",
                    encoding="utf-8",
                )
                response = {
                    "action": "local_helper", "name": "regen_helper",
                    "statement": statement, "dependencies": [],
                    "proof": proof, "contract_proof": "by simp",
                }
                (run_dir / "llm" / f"{sequence - 1:04d}-repair-1.final.txt").write_text(
                    json.dumps(response), encoding="utf-8",
                )
                attempts.append({
                    "contract_id": "contract:c1", "role": "runtime_leaf",
                    "node_id": "support:helper", "verdict": "fail",
                    "returncode": 1, "subgoal_count": subgoals,
                    "source": str(source), "log": str(log),
                })
            with patch.object(archacc, "REPO", root):
                frontier = archacc.best_failed_runtime_leaf_frontier(
                    attempts, "contract:c1", run_dir,
                )
        self.assertEqual(frontier["subgoal_count"], 2)

    def test_contract_plateau_ignores_rejected_regressions(self):
        attempts = [
            {"node_id": "contract:c1", "verdict": "fail",
             "subgoal_count": 1},
            {"node_id": "contract:c1", "verdict": "fail",
             "subgoal_count": 5},
            {"node_id": "support:s1", "verdict": "pass",
             "subgoal_count": None},
            {"node_id": "contract:c1", "verdict": "fail",
             "subgoal_count": 1},
            {"node_id": "contract:c1", "verdict": "fail",
             "subgoal_count": 3},
            {"node_id": "contract:c1", "verdict": "fail",
             "subgoal_count": 1},
        ]
        self.assertEqual(archacc.trailing_contract_subgoal_plateau(
            attempts, "contract:c1", 1,
        ), 3)

    def test_failed_apply_detects_runtime_helper_interface(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "failed.thy"
            source.write_text(textwrap.dedent("""\
                theory Failed
                begin
                lemma target: "True"
                apply (rule_tac x1=True
                    and x2=True
                    and x3=True
                    and x4=True
                    and x5=True
                    and x6=True
                    and x7=True
                    and x8=True
                    and x9=True
                    and x10=True
                    and x11=True
                    and x12=True
                    in regen_runtime_boundary)
                done
                end
            """), encoding="utf-8")
            frontier = {
                "source": "failed.thy",
                "diagnostic": (
                    "*** Failed to apply proof method "
                    "(line 4 of \"/workspace/failed.thy\")"
                ),
            }
            with patch.object(archacc, "REPO", root):
                self.assertTrue(archacc.failed_apply_targets_helper(
                    frontier, "regen_runtime_boundary",
                ))
                self.assertFalse(archacc.failed_apply_targets_helper(
                    frontier, "regen_other",
                ))

    def test_probe_rejections_persist_across_frontier_rechecks(self):
        events = [
            {"kind": "harness", "stage": "probe-strategy-rejection",
             "details": {"node_id": "contract:old"}},
            {"kind": "isabelle", "stage": "same-frontier-recheck"},
            {"kind": "llm", "stage": "repair-1"},
            {"kind": "harness", "stage": "probe-strategy-rejection",
             "details": {"node_id": "contract:c1"}},
            {"kind": "llm", "stage": "repair-2"},
            {"kind": "harness", "stage": "probe-strategy-rejection",
             "details": {"node_id": "contract:c1"}},
            {"kind": "harness", "stage": "generation-failed"},
        ]
        self.assertEqual(
            archacc.consecutive_probe_strategy_rejections(events, "contract:c1"),
            2,
        )
        events.append({
            "kind": "harness", "stage": "best-failed-frontier-promoted",
            "details": {"node_id": "contract:c1"},
        })
        self.assertEqual(
            archacc.consecutive_probe_strategy_rejections(events, "contract:c1"),
            0,
        )

    def test_probe_strategy_rejects_multiline_reduced_hotspot(self):
        evidence = [{
            "prohibited_method": (
                "simp add: vs_ptr_align_def pte_relation_aligned_def "
                "pte_align'_def return_def pt_bits_def pageBits_def"
            ),
        }]
        candidate = """
by (wpsimp simp: pte_relation_aligned_def pte_align'_def;
    fastforce intro: conjI
      simp: vs_ptr_align_def pt_bits_def)
"""
        with self.assertRaisesRegex(ValueError, "broad wpsimp/fastforce"):
            archacc.validate_probe_strategy(candidate, evidence)

    def h6_v2_response(self):
        names = [
            "regen_setObject_PD_h6_domain",
            "regen_setObject_PD_h6_owner",
            "regen_setObject_PD_h6_foreign",
            "regen_setObject_PD_h6_compose",
        ]
        helper_ids = [
            f"support:{archacc.THEORY}:{name}" for name in names
        ]
        return {
            "schema_version": 2,
            "theory": archacc.THEORY,
            "contract_id": "contract:h6",
            "helpers": [
                {
                    "name": names[0],
                    "statement": (
                        f"lemma {names[0]}: "
                        "\"pde_at' p s' \\<Longrightarrow> "
                        "p \\<in> dom (ksPSpace s')\""
                    ),
                    "frame_obligations": ["domain_preservation"],
                    "dependencies": [],
                    "premise_provenance": [{
                        "assumption": "pde_at' p s'",
                        "origin": "parent_concrete_guard",
                        "used_for": ["domain_preservation"],
                    }],
                },
                {
                    "name": names[1],
                    "statement": (
                        f"lemma {names[1]}: "
                        "\"pde_at' p s' \\<Longrightarrow> "
                        "pde_relation_aligned idx pde pde' "
                        "\\<Longrightarrow> True\""
                    ),
                    "frame_obligations": [
                        "changed_point_relation",
                        "same_owner_unchanged_region",
                    ],
                    "dependencies": [],
                    "premise_provenance": [
                        {
                            "assumption": (
                                "idx = ucast (p && mask pd_bits >> 2)"
                            ),
                            "origin": "operation_semantics",
                            "used_for": [
                                "changed_point_relation",
                                "same_owner_unchanged_region",
                            ],
                        },
                        {
                            "assumption": "pde_at' p s'",
                            "origin": "parent_concrete_guard",
                            "used_for": ["changed_point_relation"],
                        },
                    ],
                },
                {
                    "name": names[2],
                    "statement": (
                        f"lemma {names[2]}: "
                        "\"pspace_aligned s \\<Longrightarrow> True\""
                    ),
                    "frame_obligations": [
                        "foreign_owner_non_interference",
                    ],
                    "dependencies": [],
                    "premise_provenance": [{
                        "assumption": "pspace_aligned s",
                        "origin": "parent_abstract_guard",
                        "used_for": [
                            "foreign_owner_non_interference",
                        ],
                    }],
                },
                {
                    "name": names[3],
                    "statement": (
                        f"lemma {names[3]}: "
                        "\"\\<lbrakk>pspace_aligned s; "
                        "pspace_relation (kheap s) (ksPSpace s'); "
                        "pde_at' p s'; "
                        "pde_relation_aligned idx pde pde'\\<rbrakk> "
                        "\\<Longrightarrow> pspace_relation "
                        "(kheap s) (ksPSpace s')\""
                    ),
                    "frame_obligations": [
                        "pspace_relation_composition",
                    ],
                    "dependencies": helper_ids[:3],
                    "premise_provenance": [{
                        "assumption": (
                            "pspace_relation (kheap s) (ksPSpace s')"
                        ),
                        "origin": "state_relation_projection",
                        "used_for": ["pspace_relation_composition"],
                    }],
                },
            ],
            "composition_strategy": "compose the three frame leaves",
        }

    def test_h6_v2_applicability_accepts_complete_guard_coverage(self):
        response = self.h6_v2_response()
        contract = {
            "id": "contract:h6",
            "statement": (
                "lemma setObject_PD_corres: "
                "\"corres dc (ko_at x p and pspace_aligned) "
                "(pde_at' p) f g\""
            ),
        }
        report = archacc.helper_bundle_applicability_report(
            response, contract,
        )
        self.assertEqual(report["verdict"], "pass")
        self.assertTrue(report["foreign_owner_guard_preserved"])

    def test_h6_v2_rejects_dropped_parent_alignment_guard(self):
        response = self.h6_v2_response()
        response["helpers"][2]["statement"] = (
            "lemma regen_setObject_PD_h6_foreign: \"True\""
        )
        contract = {
            "id": "contract:h6",
            "statement": (
                "lemma setObject_PD_corres: "
                "\"corres dc (ko_at x p and pspace_aligned) "
                "(pde_at' p) f g\""
            ),
        }
        with self.assertRaisesRegex(ValueError, "must retain pspace_aligned"):
            archacc.helper_bundle_applicability_report(response, contract)

    def test_h6_v2_rejects_wrong_guard_provenance(self):
        response = self.h6_v2_response()
        response["helpers"][2]["premise_provenance"][0]["origin"] = (
            "accessible_premise"
        )
        contract = {
            "id": "contract:h6",
            "statement": (
                "lemma setObject_PD_corres: "
                "\"corres dc (ko_at x p and pspace_aligned) "
                "(pde_at' p) f g\""
            ),
        }
        with self.assertRaisesRegex(ValueError, "parent-guard provenance"):
            archacc.helper_bundle_applicability_report(response, contract)



    def test_h6_v2_normalizes_unambiguous_earlier_helper_names(self):
        response = self.h6_v2_response()
        names = [item["name"] for item in response["helpers"]]
        response["helpers"][3]["dependencies"] = names[:3]
        changes = archacc.normalize_helper_bundle_dependency_ids(response)
        self.assertEqual(len(changes), 3)
        self.assertEqual(
            response["helpers"][3]["dependencies"],
            [f"support:{archacc.THEORY}:{name}" for name in names[:3]],
        )



    def test_h6_v2_rejects_owner_helper_without_alignment_guard(self):
        response = self.h6_v2_response()
        response["helpers"][1]["statement"] = (
            "lemma regen_setObject_PD_h6_owner: "
            "\"pde_relation_aligned idx pde pde' \\<Longrightarrow> True\""
        )
        contract = {
            "id": "contract:h6",
            "statement": (
                "lemma setObject_PD_corres: "
                "\"corres dc (ko_at x p and pspace_aligned) "
                "(pde_at' p) f g\""
            ),
        }
        with self.assertRaisesRegex(ValueError, "must retain pde_at"):
            archacc.helper_bundle_applicability_report(response, contract)




if __name__ == "__main__":
    unittest.main()
