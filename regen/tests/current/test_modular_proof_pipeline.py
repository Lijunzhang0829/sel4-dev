#!/usr/bin/env python3
from __future__ import annotations

import json
import inspect
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from regen.infrastructure.audited_llm import audit_events, call_codex
from regen.execution.assembly_stabilizer import (
    extract_last_node_marker, prepare_assembly_stabilization,
)
from regen.execution.isabelle_node_checker import (
    IsabelleChecker, automation_audit_ml, failed_statement_node,
    parse_automation_snapshot, parse_proof_dependencies,
)
from regen.planning.file_planner import (
    planner_prompt, planning_environment, validate_plan_quality,
)
from regen.execution.lemma_prover import (
    attempt_summary, normalize_reserved_local_fact_names, proof_progress_line,
    proof_prompt, prove_node, replace_redundant_by_with_fact,
    trim_redundant_local_apply,
    trim_redundant_proof_terminator,
)
from regen.infrastructure.llm_transcript import artifact_paths, call_codex_relay
from regen.execution.graph_revision_agent import (
    augment_decision_context, graph_diff, revision_prompt,
    consumer_assumption_provenance,
    merge_generated_metadata,
    merge_contract_metadata,
    normalize_generated_roles, preserve_orphaned_green_dependencies,
    prune_unconsumed_replacement_nodes,
    next_graph_revision, replacement_scope, revision_scope,
    assert_nodes_outside_scope_unchanged,
    retry_directive_sha256, revise_graph, validate_decision,
)
from regen.planning.premise_retrieval import (
    _query_terms, retrieve_premises, retrieve_proof_premises,
    semantic_alias_tokens, semantic_bridge_guidance, semantic_bridge_tokens,
    tokens,
)
from regen.execution.proof_outline_salvage import (
    deterministic_fallback, plan_local_outline, salvage_prompt,
    validate_salvage_plan,
)
from regen.infrastructure.progress_events import (
    close_event_monitor, configure_event_monitor, emit_event,
)
from regen.planning.proof_graph import (
    assert_green_nodes_unchanged, assert_no_definitional_wrappers,
    build_graph, graph_payload_sha256, parse_json_response,
    statement_interface_diagnostics, executable_nodes,
    verify_graph_payload_sha256, write_immutable_graph,
)
from regen.execution.stepwise_lemma_backend import (
    AuditedCodexStepGenerator, _target_source, extract_forbidden_facts,
    classify_stepwise_failure, merge_premises,
    stepwise_search_budget,
)
from regen.execution.stepwise_codex import encode_state_request
from regen.execution.proof_session_runner import (
    _run_file_unlocked, acquire_run_mutation_lock, active_graph_path,
    check_statement_delta, consume_gra_feedback,
    credit_structural_evidence_round, init_run,
    latest_graph_path, next_graph_decision_index, next_node_round_id, node_log_directory,
    compact_round_feedback, create_theory_seal,
    drop_stale_proof_evidence, hydrate_feedback_candidates,
    latest_incomplete_graph_request, prior_certified_graph,
    gra_feedback_consumption_path, pending_gra_feedback,
    recover_gra_feedback, recover_latest_attempt_feedback,
    recover_round_feedback, release_run_mutation_lock,
    select_resume_feedback, statement_delta_graph,
    generated_checkpoint_theory, publish_generated_records, run_file,
    sealed_checkpoint_theories, update_file,
)


def package() -> dict:
    return {
        "theory": "Refine.Demo_R",
        "file_slot": "Demo_R.thy",
        "contracts": [{
            "contract_id": "contract:demo",
            "name": "demo_corres",
            "exact_statement": 'lemma demo_corres [corres]: "P"',
            "contract_owned_context": [],
        }],
        "visibility_assertions": {
            "human_body_included": False,
            "human_helpers_included": False,
            "human_imports_included": False,
            "human_source_locations_included": False,
        },
    }


def proposal() -> dict:
    return {
        "generated_nodes": [
            {
                "id": "node:helper", "name": "regen_helper", "role": "helper",
                "statement": 'lemma regen_helper: "A"', "dependencies": [],
                "strategy": "simp", "context": [],
            },
            {
                "id": "node:theorem", "name": "regen_theorem", "role": "theorem",
                "statement": 'lemma regen_theorem: "B"',
                "dependencies": ["node:helper"], "strategy": "rule", "context": [],
            },
        ],
        "contract_dependencies": {"contract:demo": ["node:theorem"]},
        "contract_strategies": {"contract:demo": "compose"},
    }


def test_checker(run_dir: Path, work_dir: Path) -> IsabelleChecker:
    return IsabelleChecker(
        run_dir, work_dir,
        heaps_dir=run_dir / "heaps",
        l4v_dir=run_dir / "l4v",
        semantic_workspace=run_dir / "semantic-base",
    )


class GraphTests(unittest.TestCase):
    def test_statement_interface_rejects_kheap_near_name_implicit_variable(self):
        node = {
            "id": "node:kheap-bad", "role": "helper",
            "statement": (
                "lemma regen_setObject_other_updateObject_default_checks_no_fail:\n"
                "  fixes ob' :: \"'a :: pspace_storable\"\n"
                "  assumes pko: \"projectKO_opt ko = Some obj'\"\n"
                "  shows \"updateObject_default ob' ko s = x\""
            ),
        }
        diagnostics = statement_interface_diagnostics(node)
        self.assertEqual(len(diagnostics), 1)
        self.assertIn("ambiguous implicit variable obj'", diagnostics[0])
        self.assertIn("explicitly fixed ob'", diagnostics[0])

    def test_statement_interface_accepts_kheap_explicit_distinct_variables(self):
        node = {
            "id": "node:kheap-good", "role": "helper",
            "statement": (
                "lemma regen_setObject_other_updateObject_default_no_fail:\n"
                "  fixes ob' :: \"'a :: pspace_storable\"\n"
                "  fixes obj' :: \"'a :: pspace_storable\"\n"
                "  assumes pko: \"projectKO_opt ko = Some obj'\"\n"
                "  shows \"objBits ob' = objBits obj'\""
            ),
        }
        self.assertEqual(statement_interface_diagnostics(node), [])

    def test_stepwise_port_conflict_is_infrastructure_failure(self):
        classified = classify_stepwise_failure(
            {"status": "RED", "error": "java.net.BindException: Address already in use"},
            returncode=1,
        )
        self.assertEqual(classified["failure_class"], "STEPWISE_PORT_CONFLICT")
        self.assertFalse(classified["consumes_proof_budget"])
        self.assertFalse(classified["graph_revision_eligible"])

    def test_stepwise_model_connection_failure_is_infrastructure(self):
        classified = classify_stepwise_failure({
            "status": "RED",
            "error": (
                "Failed to get results from prover: /generate_batch "
                "Failed to establish a new connection: Connection refused"
            ),
        }, returncode=1)
        self.assertEqual(
            classified["failure_class"], "STEPWISE_MODEL_PROVIDER_ERROR",
        )
        self.assertFalse(classified["consumes_proof_budget"])

    def test_stepwise_search_timeout_is_theorem_evidence(self):
        classified = classify_stepwise_failure(
            {"status": "RED", "error": "Stepwise wall-time budget exhausted"},
            returncode=124,
        )
        self.assertEqual(classified["failure_class"], "STEPWISE_SEARCH_TIMEOUT")
        self.assertTrue(classified["consumes_proof_budget"])
        self.assertTrue(classified["graph_revision_eligible"])

    def test_replace_redundant_by_with_fact_uses_checker_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "Node.thy"
            source.write_text(
                "proof -\n"
                "  from a b show False\n"
                "    by blast\n"
                "qed\n"
            )
            proof = (
                "proof -\n"
                "  from a b show False\n"
                "    by blast\n"
                "qed"
            )
            diagnostic = (
                'Failed to refine any pending goal\nAt command "by" '
                f'(line 3 of "{source}")'
            )
            self.assertEqual(
                replace_redundant_by_with_fact(
                    proof, diagnostic, str(source),
                ),
                (
                    "proof -\n"
                    "  from a b show False\n"
                    "    .\n"
                    "qed",
                    "    by blast",
                ),
            )

    def test_trim_redundant_local_apply_uses_checker_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "Node.thy"
            source.write_text(
                "theory Node\nbegin\n"
                "lemma demo: True\n"
                "proof -\n"
                "  have local: True\n"
                "    apply simp\n"
                "    apply blast\n"
                "    done\n"
                "  show True by simp\n"
                "qed\nend\n"
            )
            proof = (
                "proof -\n"
                "  have local: True\n"
                "    apply simp\n"
                "    apply blast\n"
                "    done\n"
                "  show True by simp\n"
                "qed"
            )
            diagnostic = (
                'No subgoals!\nAt command "apply" '
                f'(line 7 of "{source}")'
            )
            repaired = trim_redundant_local_apply(
                proof, diagnostic, str(source),
            )
            self.assertEqual(
                repaired,
                (
                    "proof -\n"
                    "  have local: True\n"
                    "    apply simp\n"
                    "  show True by simp\n"
                    "qed",
                    "    apply blast\n    done",
                ),
            )

    def test_trim_redundant_local_apply_requires_unique_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "Node.thy"
            source.write_text("apply simp\n")
            self.assertIsNone(trim_redundant_local_apply(
                "apply simp\napply simp",
                'No subgoals! At command "apply" (line 1 of "Node.thy")',
                str(source),
            ))

    def test_trim_redundant_proof_terminator_after_closed_goal(self):
        proof = "proof -\n  from bad show False by contradiction\nqed"
        repaired = trim_redundant_proof_terminator(
            proof, 'Illegal application of proof command in "prove" mode',
        )
        self.assertEqual(
            repaired,
            ("proof -\nqed", "  from bad show False by contradiction"),
        )

    def test_trim_multiline_show_command_and_keep_qed(self):
        proof = (
            "proof clarsimp\n"
            "  have not_oth: \"not P z\" by simp\n"
            "  from othr z_eq not_oth show False\n"
            "    by simp\n"
            "qed"
        )
        repaired = trim_redundant_proof_terminator(
            proof, 'Illegal application of proof command in "prove" mode',
        )
        self.assertEqual(
            repaired,
            (
                "proof clarsimp\n"
                "  have not_oth: \"not P z\" by simp\n"
                "qed",
                "  from othr z_eq not_oth show False\n    by simp",
            ),
        )

    def test_trim_no_pending_show_command_and_keep_qed(self):
        proof = (
            "proof clarsimp\n"
            '  have not_oth: "not P z" by simp\n'
            "  from othr not_oth show False\n"
            "    by simp\n"
            "qed"
        )
        repaired = trim_redundant_proof_terminator(
            proof, "Failed to refine any pending goal",
        )
        self.assertEqual(
            repaired,
            (
                "proof clarsimp\n"
                '  have not_oth: "not P z" by simp\n'
                "  from othr not_oth show False\n"
                "    by simp",
                "qed",
            ),
        )

    def test_trim_no_pending_with_show_by_and_keep_qed(self):
        proof = (
            "proof clarsimp\n"
            '  have not_oth: "not P z" by simp\n'
            "  with othr show False\n"
            "    by simp\n"
            "qed"
        )
        repaired = trim_redundant_proof_terminator(
            proof, "Failed to refine any pending goal",
        )
        self.assertEqual(
            repaired,
            (
                "proof clarsimp\n"
                '  have not_oth: "not P z" by simp\n'
                "  with othr show False\n"
                "    by simp",
                "qed",
            ),
        )

    def test_trim_no_pending_apply_done_command_and_keep_qed(self):
        proof = (
            "proof clarsimp\n"
            "  from othr show False\n"
            "    apply simp\n"
            "    apply auto\n"
            "    done\n"
            "qed"
        )
        repaired = trim_redundant_proof_terminator(
            proof, "Failed to refine any pending goal",
        )
        self.assertEqual(
            repaired,
            (
                "proof clarsimp\n"
                "  from othr show False\n"
                "    apply simp\n"
                "    apply auto\n"
                "    done",
                "qed",
            ),
        )

    def test_trim_redundant_by_after_no_pending_goal(self):
        proof = "apply simp\nby blast"
        repaired = trim_redundant_proof_terminator(
            proof, "Failed to refine any pending goal",
        )
        self.assertEqual(repaired, ("apply simp", "by blast"))

    def test_trim_redundant_from_show_by_after_closed_goal(self):
        proof = "apply simp\nfrom a b show False by simp"
        repaired = trim_redundant_proof_terminator(
            proof, "Failed to refine any pending goal",
        )
        self.assertEqual(
            repaired, ("apply simp", "from a b show False by simp"),
        )

    def test_trim_multiline_from_show_by_after_closed_goal(self):
        proof = "apply simp\nfrom a b show False\n  by simp"
        repaired = trim_redundant_proof_terminator(
            proof, "Failed to refine any pending goal",
        )
        self.assertEqual(
            repaired, ("apply simp", "from a b show False\n  by simp"),
        )

    def test_trim_illegal_bare_qed_after_closed_show(self):
        repaired = trim_redundant_proof_terminator(
            "proof -\n  from a show False\nqed",
            'Illegal application of proof command in "prove" mode',
        )
        self.assertEqual(
            repaired, ("proof -\n  from a show False", "qed"),
        )

    def test_trim_redundant_nested_qed_by_reported_source_line(self):
        proof = (
            "proof -\n"
            "  proof -\n"
            "    show True by simp\n"
            "  qed\n"
            "  qed\n"
            "  show True by simp\n"
            "qed"
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "Node.thy"
            source.write_text("lemma demo: True\n" + proof + "\nend\n")
            repaired = trim_redundant_proof_terminator(
                proof,
                f'Failed to refine any pending goal\nAt command "qed" '
                f'(line 6 of "{source}")',
                str(source),
            )
        self.assertEqual(
            repaired,
            (
                "proof -\n"
                "  proof -\n"
                "    show True by simp\n"
                "  qed\n"
                "  show True by simp\n"
                "qed",
                "  qed",
            ),
        )

    def test_trim_redundant_proof_terminator_is_narrow(self):
        self.assertIsNone(trim_redundant_proof_terminator(
            "apply simp\\ndone", "Failed to finish proof: goal (1 subgoal)",
        ))
        self.assertIsNone(trim_redundant_proof_terminator(
            "qed", 'Illegal application of proof command in "prove" mode',
        ))

    def test_consumer_assumption_provenance_tracks_transitive_guards(self):
        graph = {"nodes": [
            {
                "id": "node:leaf", "role": "helper", "statement": 'lemma leaf: "A"',
                "consumers": ["node:parent"],
            },
            {
                "id": "node:parent", "role": "theorem",
                "statement": (
                    'lemma parent:\n'
                    'assumes t: "T x"\n'
                    'assumes rel: "R x y"\n'
                    'shows "\\<lbrakk> P x; Q y \\<rbrakk> \\<Longrightarrow> B"'
                ),
                "consumers": ["contract:demo"],
            },
            {
                "id": "contract:demo", "role": "contract",
                "statement": (
                    'lemma demo:\nassumes fixed: "F x"\n'
                    'shows "R x y \\<Longrightarrow> C"'
                ),
                "consumers": [],
            },
        ]}
        provenance = consumer_assumption_provenance(graph, "node:leaf")
        self.assertEqual(
            [item["provider_node_id"] for item in provenance],
            ["node:parent", "contract:demo"],
        )
        self.assertEqual(
            provenance[0]["available_assumptions"],
            ['assumes t: "T x"', 'assumes rel: "R x y"'],
        )
        self.assertEqual(
            provenance[0]["available_local_antecedents"], ['P x', 'Q y'],
        )
        self.assertEqual(provenance[1]["distance_from_failed_node"], 2)
        self.assertEqual(
            provenance[1]["available_local_antecedents"], ['R x y'],
        )

    def test_json_response_normalizes_isabelle_backslash_escapes(self):
        response = '{"reason":"x \\<in> y","operation":"RETRY"}'
        value = parse_json_response(response)
        self.assertEqual(value["reason"], "x \\<in> y")

    def test_json_response_preserves_pairs_and_repairs_lone_backslash(self):
        response = '{"reason":"x \\\\<in> S and fst \\' + chr(96) + ' cuts"}'
        value = parse_json_response(response)
        self.assertEqual(value["reason"], "x \\<in> S and fst \\" + chr(96) + " cuts")

    def test_definitional_corres_wrapper_is_rejected(self):
        graph = {
            "nodes": [
                {
                    "id": "node:bridge", "role": "theorem",
                    "statement": 'lemma bridge: "corres R P Q f getObject"',
                    "consumers": ["contract:demo"],
                },
                {
                    "id": "contract:demo", "role": "contract",
                    "statement": 'lemma demo: "corres R P Q f getEndpoint"',
                    "consumers": [],
                },
            ],
        }
        with self.assertRaisesRegex(ValueError, "definitional/cosmetic wrapper"):
            assert_no_definitional_wrappers(graph)

    def test_repeated_semantic_residual_is_evidence_but_allows_five_rounds(self):
        red = {
            "node_id": "node:bridge",
            "attempts": [{
                "check": {"diagnostic": (
                    '*** Failed to finish proof:\n*** goal (1 subgoal): 1. P'
                    '\n*** At command "by"'
                )},
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audit = root / "graph-revisions/decision-001"
            audit.mkdir(parents=True)
            (audit / "request.json").write_text(json.dumps({
                "failed_node": red,
                "decision_context": {"phase": "node-proof", "proof_round": 1},
            }))
            (audit / "decision.json").write_text(json.dumps({
                "decision": "RETRY_PROOF",
                "failure_class": "PROOF_BODY_ERROR",
                "reason": "retry",
            }))
            context = augment_decision_context(
                root, {
                    "nodes": [{
                        "id": "node:bridge", "consumers": [],
                        "role": "theorem", "statement": 'lemma bridge: "P"',
                    }],
                }, red, {"phase": "node-proof", "proof_round": 2},
            )
            self.assertEqual(context["repeated_semantic_residual_rounds"], 2)
            self.assertTrue(context["retry_proof_allowed"])
            request = {"decision_policy": {"retry_proof_allowed": True}}
            decision = {
                "decision": "RETRY_PROOF",
                "failure_class": "PROOF_BODY_ERROR",
                "reason": "still retry",
                "residual_evidence": ["same goal"],
                "alternatives_considered": [],
                "confidence": 0.5,
                "repair_guidance": "use the generated dependency explicitly",
            }
            validate_decision(decision, request)

    def test_five_full_proof_rounds_forbid_retry(self):
        red = {
            "node_id": "node:bridge",
            "attempts": [{
                "check": {"diagnostic": (
                    '*** Failed to finish proof:\n*** goal (1 subgoal): 1. Q'
                    '\n*** At command "by"'
                )},
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            context = augment_decision_context(
                Path(directory), {
                    "nodes": [{
                        "id": "node:bridge", "consumers": [],
                        "role": "theorem", "statement": 'lemma bridge: "P"',
                    }],
                }, red, {
                    "phase": "node-proof",
                    "proof_round": 5,
                    "proof_rounds_used": 5,
                    "proof_retry_limit": 5,
                },
            )
            self.assertTrue(context["full_round_retry_cap_reached"])
            self.assertFalse(context["retry_proof_allowed"])

    def test_retry_requires_and_changes_material_directive(self):
        decision = {
            "decision": "RETRY_PROOF",
            "failure_class": "PROOF_BODY_ERROR",
            "reason": "change the proof search",
            "residual_evidence": ["the same bridge goal remains"],
            "alternatives_considered": ["the graph statement is unchanged"],
            "confidence": 0.8,
        }
        with self.assertRaisesRegex(ValueError, "material retrieval"):
            validate_decision(decision, {
                "decision_context": {},
                "decision_policy": {"retry_proof_allowed": True},
            })
        decision["repair_guidance"] = "apply bridge before simplification"
        directive_hash = retry_directive_sha256(decision)
        with self.assertRaisesRegex(ValueError, "previously consumed"):
            validate_decision(decision, {
                "decision_context": {
                    "current_retry_directive_sha256": directive_hash,
                },
                "decision_policy": {"retry_proof_allowed": True},
            })
        decision["retrieval_query"] = "bridge elimination rule"
        validate_decision(decision, {
            "decision_context": {
                "current_retry_directive_sha256": directive_hash,
            },
            "decision_policy": {"retry_proof_allowed": True},
        })

    def test_deterministic_false_statement_evidence_forbids_retry(self):
        red = {"node_id": "node:bridge", "attempts": []}
        with tempfile.TemporaryDirectory() as directory:
            context = augment_decision_context(
                Path(directory), {
                    "nodes": [{
                        "id": "node:bridge", "consumers": [],
                        "role": "helper", "statement": 'lemma bridge: "P"',
                    }],
                }, red, {
                    "phase": "node-proof",
                    "retry_proof_allowed": False,
                    "trusted_framework_semantic_evidence": [{
                        "equation": "f _ = True",
                        "counterexample": "P does not follow",
                    }],
                },
            )
            self.assertFalse(context["retry_proof_allowed"])

    def test_reserved_local_fact_name_is_normalized(self):
        proof, repairs = normalize_reserved_local_fact_names(
            "proof -\n  have typ: \"P\" by simp\n  from typ show P .\nqed"
        )
        self.assertNotIn("have typ:", proof)
        self.assertIn("have typ_fact:", proof)
        self.assertIn("from typ_fact", proof)
        self.assertEqual(repairs[0]["kind"], "rename-reserved-local-fact")

    def test_single_lemma_defaults_to_five_attempts(self):
        parameter = inspect.signature(prove_node).parameters["max_attempts"]
        self.assertEqual(parameter.default, 5)

    def test_runner_caps_one_isabelle_candidate_at_five_minutes(self):
        parameter = inspect.signature(run_file).parameters["node_timeout"]
        self.assertEqual(parameter.default, 300)

    def test_previous_round_feedback_is_visible_to_lemma_prover(self):
        feedback = {
            "round_id": 1,
            "last_proof_body": "by simp",
            "residual": "Failed to finish proof: P",
        }
        prompt = proof_prompt(
            {
                "id": "node:helper", "role": "helper",
                "statement": 'lemma helper: "P"', "strategy": "simp",
            }, [], [], None, previous_round_feedback=feedback,
        )
        self.assertIn("recent_attempts", prompt)
        self.assertIn("Failed to finish proof: P", prompt)
        self.assertIn("by simp", prompt)

    def test_proof_progress_uses_deepest_isabelle_source_line(self):
        shallow = {"check": {"diagnostic": "At command by (line 574 of candidate.thy)"}}
        deep = {"check": {"diagnostic": "At command by (line 587 of candidate.thy)"}}
        timeout = {"check": {"diagnostic": "timeout after 600 seconds"}}
        self.assertEqual(proof_progress_line(shallow), 574)
        self.assertGreater(proof_progress_line(deep), proof_progress_line(shallow))
        self.assertEqual(proof_progress_line(timeout), 0)

    def test_proof_prompt_separates_best_baseline_from_latest_residual(self):
        best = {"attempt": 2, "proof_body": "proof -\n  have mature: P by simp\nqed",
                "check": {"diagnostic": "failed at line 587"}}
        latest = {"attempt": 3, "proof_body": "by auto",
                  "check": {"diagnostic": "failed at line 574"}}
        prompt = proof_prompt(
            {"id": "node:helper", "role": "helper",
             "statement": "lemma helper: \"P\"", "strategy": "structured"},
            [], [], best, latest_attempt=latest,
            attempt_history=[attempt_summary(best), attempt_summary(latest)],
        )
        view = json.loads(prompt.rsplit("INPUT:\n",1)[1])
        self.assertEqual([x['proof_body'] for x in view['recent_attempts']], [latest['proof_body']])
        self.assertTrue(view['attempt_summaries'])
        self.assertNotIn('proof_body',view['attempt_summaries'][0])
        self.assertEqual(view['recent_attempts'][-1]['feedback'],"failed at line 574")

    def test_deterministic_salvage_is_bound_to_current_node(self):
        fixture = (
            Path(__file__).parents[2]
            / "fixtures/KHeap_R_lookupAround_salvage_request.json"
        )
        request = json.loads(fixture.read_text())
        attempts = request["failed_attempts"]
        self.assertEqual(len(attempts), 4)
        self.assertEqual(attempts[0]["failure_class"], "initial_method_failure")
        self.assertGreater(attempts[0]["structured_commands"]["obtain"], 0)
        self.assertIn("KOArch", attempts[2]["kernel_object_branches"])
        self.assertIn("Invariants_H.ps_clear_lookupAround2",
                      request["eligible_premise_names"])

        plan = deterministic_fallback(request)
        validate_salvage_plan(plan, request)
        self.assertEqual(
            [step["kind"] for step in plan["local_steps"]],
            ["assume", "cases", "have", "show"],
        )
        serialized = json.dumps(plan)
        self.assertIn(request["node"]["id"], plan["diagnosis"])
        self.assertNotIn("ps_clear_lookupAround2", serialized)
        self.assertNotIn("endpoint-at guard", serialized)
        self.assertIn("older theorem-specific outline", " ".join(plan["avoid"]))

    def test_deterministic_salvage_truncates_after_closed_goal(self):
        request = {
            "node": {"id": "node:demo", "role": "contract", "statement": "P", "strategy": ""},
            "failed_attempts": [{
                "attempt": 1, "proof_body": "proof -\n  show P .\nqed",
                "diagnostic": "Failed to refine any pending goal\nAt command \".\"",
                "failure_class": "isabelle_rejection",
                "structured_commands": {}, "kernel_object_branches": [],
                "retrieved_premise_names": [],
            }],
            "eligible_premise_names": [],
        }
        plan = deterministic_fallback(request)
        validate_salvage_plan(plan, request)
        self.assertIn("closed proof state", plan["diagnosis"])
        self.assertEqual([step["kind"] for step in plan["local_steps"]], ["automation"])
        self.assertIn("Shorten", " ".join(plan["repair_directives"]))

    def test_deterministic_salvage_preserves_prior_type_error(self):
        request = {
            "node": {"id": "node:typed", "role": "contract", "statement": "P", "strategy": ""},
            "failed_attempts": [
                {"attempt": 1, "proof_body": "proof -",
                 "diagnostic": "Introduced fixed type variable; incompatible operand type",
                 "failure_class": "isabelle_rejection", "structured_commands": {},
                 "kernel_object_branches": [], "retrieved_premise_names": []},
                {"attempt": 2, "proof_body": "by simp",
                 "diagnostic": "Failed to finish proof",
                 "failure_class": "unfinished_goal", "structured_commands": {},
                 "kernel_object_branches": [], "retrieved_premise_names": []},
            ],
            "eligible_premise_names": [],
        }
        plan = deterministic_fallback(request)
        validate_salvage_plan(plan, request)
        self.assertIn("underconstrained", plan["diagnosis"])
        self.assertIn("type annotation", plan["local_steps"][0]["method_hint"])

    def test_salvage_rejects_unretrieved_premise(self):
        fixture = (
            Path(__file__).parents[2]
            / "fixtures/KHeap_R_lookupAround_salvage_request.json"
        )
        request = json.loads(fixture.read_text())
        plan = deterministic_fallback(request)
        plan["local_steps"][0]["premise_names"] = ["Human_Proof.hidden"]
        with self.assertRaisesRegex(ValueError, "ineligible premise"):
            validate_salvage_plan(plan, request)

    def test_salvage_rejects_hidden_premise_in_method_hint(self):
        fixture = (
            Path(__file__).parents[2]
            / "fixtures/KHeap_R_lookupAround_salvage_request.json"
        )
        request = json.loads(fixture.read_text())
        plan = deterministic_fallback(request)
        plan["local_steps"][0]["method_hint"] = "Apply Human_Proof.hidden"
        with self.assertRaisesRegex(ValueError, "ineligible qualified premise"):
            validate_salvage_plan(plan, request)

    def test_salvage_outline_is_visible_to_next_proof_attempt(self):
        outline = {
            "diagnosis": "eliminate before normalization",
            "local_steps": [{
                "id": "local-1", "kind": "obtain",
                "local_statement": "extract endpoint object",
                "depends_on": [],
                "premise_names": ["Invariants_H.obj_atE'"],
                "method_hint": "keep the guard",
            }],
        }
        prompt = proof_prompt(
            {
                "id": "node:helper", "role": "helper",
                "statement": 'lemma helper: "P"', "strategy": "structured",
            }, [], [], None, local_proof_outline=outline,
        )
        self.assertNotIn("local_proof_outline", prompt)
        self.assertNotIn("eliminate before normalization", prompt)
        self.assertNotIn("extract endpoint object", prompt)

    def test_salvage_module_writes_checked_outline_artifacts(self):
        fixture = (
            Path(__file__).parents[2]
            / "fixtures/KHeap_R_lookupAround_salvage_request.json"
        )
        request = json.loads(fixture.read_text())
        node = request["node"]
        attempts = [{
            "attempt": item["attempt"],
            "proof_body": item["proof_body"],
            "retrieved_premise_names": item["retrieved_premise_names"],
            "check": {"diagnostic": item["diagnostic"]},
        } for item in request["failed_attempts"]]
        proposed = deterministic_fallback(request)
        proposed.pop("source")
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "after-attempt-004"
            def fake_salvage_call(_provider, _prompt, artifact_prefix, **kwargs):
                self.assertTrue(kwargs["clean_runtime"])
                artifact_prefix.with_suffix(".meta.json").write_text(
                    json.dumps({
                        "provider": "codex-exec-audited",
                        "sealable_generation": True,
                    })
                )
                return json.dumps(proposed)

            with patch(
                "regen.execution.proof_outline_salvage.call_llm",
                side_effect=fake_salvage_call,
            ), patch("regen.execution.proof_outline_salvage.write_llm_module_log"):
                plan = plan_local_outline(
                    node, attempts, output_prefix=prefix,
                    provider="codex", model="gpt-5.5", timeout=1,
                    theory="Refine.KHeap_R",
                )
            self.assertEqual(plan["source"], "llm")
            self.assertTrue(Path(str(prefix) + ".request.json").is_file())
            self.assertTrue(Path(str(prefix) + ".outline.json").is_file())

    def test_deterministic_salvage_skips_llm_and_is_sealable(self):
        fixture = (
            Path(__file__).parents[2]
            / "fixtures/KHeap_R_lookupAround_salvage_request.json"
        )
        request = json.loads(fixture.read_text())
        node = request["node"]
        attempts = [{
            "attempt": item["attempt"],
            "proof_body": item["proof_body"],
            "retrieved_premise_names": item["retrieved_premise_names"],
            "check": {"diagnostic": item["diagnostic"]},
        } for item in request["failed_attempts"]]
        with tempfile.TemporaryDirectory() as directory, patch(
            "regen.execution.proof_outline_salvage.call_llm",
        ) as model_call:
            plan = plan_local_outline(
                node, attempts,
                output_prefix=Path(directory) / "deterministic",
                provider="deterministic", model="unused", timeout=1,
                theory="Refine.KHeap_R",
            )
        model_call.assert_not_called()
        self.assertEqual(plan["provider"], "deterministic")
        self.assertTrue(plan["sealable_generation"])
        self.assertEqual(len(plan["local_steps"]), 4)

    def test_salvage_provider_failure_uses_sealable_deterministic_fallback(self):
        fixture = (
            Path(__file__).parents[2]
            / "fixtures/KHeap_R_lookupAround_salvage_request.json"
        )
        request = json.loads(fixture.read_text())
        node = request["node"]
        attempts = [{
            "attempt": item["attempt"],
            "proof_body": item["proof_body"],
            "retrieved_premise_names": item["retrieved_premise_names"],
            "check": {"diagnostic": item["diagnostic"]},
        } for item in request["failed_attempts"]]
        with tempfile.TemporaryDirectory() as directory, patch(
            "regen.execution.proof_outline_salvage.call_llm",
            side_effect=TimeoutError("salvage provider timeout"),
        ):
            plan = plan_local_outline(
                node, attempts,
                output_prefix=Path(directory) / "provider-timeout",
                provider="codex", model="gpt-5.5", timeout=1,
                theory="Refine.KHeap_R",
            )
        self.assertEqual(plan["source"], "deterministic-fallback")
        self.assertTrue(plan["sealable_generation"])
        self.assertFalse(plan["provider_attempt_sealable"])
        self.assertIn("TimeoutError", plan["provider_failure"])

    def test_salvage_prompt_forbids_graph_mutation(self):
        fixture = (
            Path(__file__).parents[2]
            / "fixtures/KHeap_R_lookupAround_salvage_request.json"
        )
        prompt = salvage_prompt(json.loads(fixture.read_text()))
        self.assertIn("not theorem-graph planning", prompt)
        self.assertIn("Do not introduce a lemma", prompt)

    def test_codex_relay_is_recorded_but_not_sealable(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return json.dumps({"answer": "{\"local_steps\": []}"}).encode()

        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "relay"
            with patch("regen.infrastructure.llm_transcript._relay_token", return_value="token"), patch(
                "regen.infrastructure.llm_transcript.urllib.request.urlopen",
                return_value=Response(),
            ):
                answer = call_codex_relay(
                    "prompt", prefix, theory="Refine.KHeap_R",
                    stage="local-proof-outline:test",
                )
            self.assertIn("local_steps", answer)
            paths = artifact_paths(prefix)
            meta = json.loads(paths["meta"].read_text())
            audit = json.loads(paths["audit"].read_text())
            self.assertEqual(meta["provider"], "codex-relay")
            self.assertFalse(meta["sealable_generation"])
            self.assertIsNone(meta["no_tool_events"])
            self.assertEqual(
                audit["tool_event_visibility"], "unavailable-from-relay-api",
            )

    def test_dependency_change_resets_only_unproved_stale_evidence(self):
        graph = {"nodes": [
            {"id": "node:red", "statement": "P", "dependencies": ["node:new"]},
            {"id": "node:green", "statement": "Q", "dependencies": []},
            {"id": "node:new", "statement": "R", "dependencies": []},
        ]}
        statuses = {"node:red": "planned", "node:green": "green", "node:new": "green"}
        rounds = {"node:red": 7, "node:green": 3}
        hints = {"node:red": "stale graph rejection", "node:green": "keep"}
        feedback = {
            "node:red": {"statement": "P", "dependencies": []},
            "node:green": {"statement": "Q", "dependencies": []},
        }
        reset = drop_stale_proof_evidence(graph, statuses, rounds, hints, feedback)
        self.assertEqual(reset, ["node:red"])
        self.assertEqual(rounds["node:red"], 0)
        self.assertNotIn("node:red", hints)
        self.assertNotIn("node:red", feedback)
        self.assertEqual(rounds["node:green"], 3)
        self.assertIn("node:green", feedback)

    def test_compact_round_feedback_keeps_proof_and_residual(self):
        feedback = compact_round_feedback({
            "statement": 'lemma helper: "P"',
        }, {
            "backend": "whole-proof",
            "attempts": [{
                "proof_body": "by simp",
                "retrieved_premise_names": ["Demo.fact"],
                "check": {"diagnostic": "goal P"},
            }],
            "residual": "goal P",
            "salvage_plans": [{
                "outline": {"local_steps": [{"id": "local-1"}]},
            }],
        }, 2)
        self.assertEqual(feedback["round_id"], 2)
        self.assertEqual(feedback["last_proof_body"], "by simp")
        self.assertEqual(feedback["residual"], "goal P")
        self.assertEqual(
            feedback["local_proof_outline"]["local_steps"][0]["id"],
            "local-1",
        )


    def test_recover_round_feedback_from_decision_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            decision = root / "graph-revisions/decision-003"
            decision.mkdir(parents=True)
            failed = {
                "node_id": "node:helper",
                "attempts": [{
                    "proof_body": "by auto",
                    "check": {"diagnostic": "remaining goal P"},
                }],
                "residual": "remaining goal P",
            }
            (decision / "request.json").write_text(json.dumps({
                "failed_node": failed,
                "current_graph": {"nodes": [{
                    "id": "node:helper",
                    "statement": 'lemma helper: "P"',
                }]},
                "decision_context": {
                    "phase": "node-proof", "proof_round": 2,
                },
            }))
            (decision / "decision.json").write_text(json.dumps({
                "decision": "RETRY_PROOF",
                "failure_class": "PROOF_BODY_ERROR",
                "reason": "retry",
            }))
            feedback = recover_round_feedback(root, {
                "nodes": [{
                    "id": "node:helper",
                    "statement": 'lemma helper: "P"',
                }],
            }, "node:helper")
            self.assertEqual(feedback["round_id"], 2)
            self.assertEqual(feedback["last_proof_body"], "by auto")
            self.assertEqual(feedback["residual"], "remaining goal P")

    def test_delete_only_cannot_restore_failed_direct_consumer(self):
        request = {"decision_policy": {"delete_only_forbidden": True}}
        decision = {
            "decision": "REVISE_GRAPH",
            "failure_class": "DUPLICATE_GENERATED_NODE",
            "operation": "DELETE",
            "reason": "delete wrapper",
            "residual_evidence": ["wrapper"],
            "alternatives_considered": [],
            "confidence": 0.8,
        }
        with self.assertRaisesRegex(ValueError, "DELETE-only"):
            validate_decision(decision, request)

    def test_statement_diagnostic_maps_to_exact_graph_node(self):
        graph = build_graph(package(), proposal(), run_id="r1", revision=1)
        checker = test_checker(Path("/tmp"), Path("/tmp/checker-map-test"))
        source = checker._source(
            graph, {}, theory_name="Map", statements_only=True,
        )
        target = 'lemma regen_helper: "A"'
        line = source[:source.index(target)].count("\n") + 1
        diagnostic = f'At command "lemma" (line {line} of "/candidate/Map.thy")'
        self.assertEqual(
            failed_statement_node(graph, source, diagnostic),
            "node:helper",
        )

    def test_statement_revision_cannot_change_unrelated_branch(self):
        value = proposal()
        value["generated_nodes"].append({
            "id": "node:sibling",
            "name": "regen_sibling",
            "role": "helper",
            "statement": 'lemma regen_sibling: "C"',
            "context": [],
            "dependencies": [],
            "strategy": "parallel support",
        })
        value["contract_dependencies"]["contract:demo"].append("node:sibling")
        graph = build_graph(package(), value, run_id="r1", revision=1)
        changed = json.loads(json.dumps(graph))
        by_id = {node["id"]: node for node in changed["nodes"]}
        by_id["node:sibling"]["strategy"] = "unrelated mutation"
        scope = revision_scope(graph, "node:helper")
        self.assertIn("contract:demo", scope)
        self.assertNotIn("node:sibling", scope)
        with self.assertRaisesRegex(ValueError, "outside failure scope"):
            assert_nodes_outside_scope_unchanged(graph, changed, scope)

    def test_replacement_scope_includes_helper_used_only_by_downstream(self):
        value = proposal()
        value["generated_nodes"].append({
            "id": "node:support", "name": "regen_support",
            "role": "helper", "statement": 'lemma regen_support: "S"',
            "context": [], "dependencies": [], "strategy": "simp",
        })
        value["generated_nodes"][1]["dependencies"].append("node:support")
        graph = build_graph(package(), value, run_id="r1", revision=1)
        scope = replacement_scope(graph, "node:helper")
        self.assertIn("node:support", scope)

    def test_replacement_scope_excludes_direct_contract_sibling(self):
        value = proposal()
        value["generated_nodes"].append({
            "id": "node:sibling", "name": "regen_sibling",
            "role": "helper", "statement": 'lemma regen_sibling: "S"',
            "context": [], "dependencies": [], "strategy": "simp",
        })
        value["contract_dependencies"]["contract:demo"].append("node:sibling")
        graph = build_graph(package(), value, run_id="r1", revision=1)
        scope = replacement_scope(graph, "node:helper")
        self.assertNotIn("node:sibling", scope)

    def test_planner_feedback_is_visible_in_next_closed_payload(self):
        prompt = planner_prompt(
            package(), {"legal_declarations": []},
            ["duplicate proposition/cosmetic wrapper"],
        )
        self.assertIn("previous_plan_rejections", prompt)
        self.assertIn("duplicate proposition/cosmetic wrapper", prompt)

    def test_immutable_graph_rejects_different_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph-v1.json"
            write_immutable_graph(path, {"revision": 1})
            write_immutable_graph(path, {"revision": 1})
            with self.assertRaises(FileExistsError):
                write_immutable_graph(path, {"revision": 2})

    def test_graph_payload_identity_detects_mutation(self):
        graph = build_graph(package(), proposal(), run_id="r1", revision=1)
        graph["upstream_bindings"] = {"environment_gate_id": "gate:1"}
        graph["graph_payload_sha256"] = graph_payload_sha256(graph)
        verify_graph_payload_sha256(graph)
        graph["upstream_bindings"]["environment_gate_id"] = "gate:2"
        with self.assertRaisesRegex(ValueError, "payload hash mismatch"):
            verify_graph_payload_sha256(graph)

    def test_next_graph_revision_skips_existing_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "graph-v2.json").touch()
            (root / "graph-v6.json").touch()
            self.assertEqual(next_graph_revision(root, 2), 7)

    def test_vague_direct_plan_is_rejected(self):
        value = {
            "generated_nodes": [],
            "contract_dependencies": {"contract:demo": []},
            "contract_strategies": {
                "contract:demo": "Use the existing correspondence rule",
            },
            "contract_routes": {
                "contract:demo": {
                    "proof_shape": "direct",
                    "semantic_steps": ["close the contract"],
                    "direct_evidence": {
                        "kind": "definitional",
                        "detail": "unfold demo and normalize both sides",
                    },
                },
            },
        }
        with self.assertRaisesRegex(ValueError, "unnamed external support"):
            validate_plan_quality(package(), value, {
                "legal_declarations": [],
            })

    def test_direct_plan_cannot_select_external_premise(self):
        value = {
            "generated_nodes": [],
            "contract_dependencies": {"contract:demo": []},
            "contract_strategies": {
                "contract:demo": "Apply Demo.hidden_corres",
            },
            "contract_routes": {
                "contract:demo": {
                    "proof_shape": "direct",
                    "semantic_steps": ["specialize Demo.hidden_corres"],
                    "direct_evidence": {
                        "kind": "legal_declaration", "name": "Demo.hidden_corres",
                    },
                },
            },
        }
        with self.assertRaisesRegex(ValueError, "invalid evidence kind"):
            validate_plan_quality(package(), value, {
                "legal_declarations": [],
            })

    def test_generated_route_requires_generated_contract_dependency(self):
        value = {
            "generated_nodes": [],
            "contract_dependencies": {"contract:demo": []},
            "contract_strategies": {"contract:demo": "compose a bridge"},
            "contract_routes": {
                "contract:demo": {
                    "proof_shape": "generated",
                    "semantic_steps": ["establish the missing bridge"],
                },
            },
        }
        with self.assertRaisesRegex(ValueError, "justify direct proof"):
            validate_plan_quality(package(), value, {
                "legal_declarations": [],
            })

    def test_precise_semantic_direct_plan_is_accepted(self):
        value = {
            "generated_nodes": [],
            "contract_dependencies": {"contract:demo": []},
            "contract_strategies": {
                "contract:demo": "unfold demo and reduce the relation pointwise",
            },
            "contract_routes": {
                "contract:demo": {
                    "proof_shape": "direct",
                    "semantic_steps": ["reduce the relation pointwise"],
                    "direct_evidence": {
                        "kind": "semantic_reduction",
                        "detail": "both programs reduce to the same return",
                    },
                },
            },
        }
        validate_plan_quality(package(), value, {
            "legal_declarations": [],
        })

    def test_structural_revision_round_is_not_double_charged(self):
        counts = {"node:x": 8}
        credit_structural_evidence_round(counts, "node:x")
        self.assertEqual(counts["node:x"], 7)

    def test_valid_graph_and_execution(self):
        graph = build_graph(package(), proposal(), run_id="r1", revision=1)
        self.assertEqual(
            graph["execution_order"],
            ["node:helper", "node:theorem", "contract:demo"],
        )
        self.assertEqual(executable_nodes(graph, {}), ["node:helper"])
        self.assertEqual(
            executable_nodes(graph, {"node:helper": "green"}), ["node:theorem"]
        )

    def test_external_premise_cannot_be_graph_node(self):
        value = proposal()
        value["generated_nodes"][0]["dependencies"] = ["premise:foo"]
        with self.assertRaisesRegex(ValueError, "unknown generated dependencies"):
            build_graph(package(), value, run_id="r1", revision=1)

    def test_generated_node_cannot_rename_contract_proposition(self):
        value = {
            "generated_nodes": [{
                "id": "node:wrapper",
                "name": "demo_wrapper",
                "role": "theorem",
                "statement": 'lemma demo_wrapper: "P"',
                "context": [],
                "dependencies": [],
                "strategy": "forward the same proposition",
            }],
            "contract_dependencies": {
                "contract:demo": ["node:wrapper"],
            },
            "contract_strategies": {
                "contract:demo": "apply wrapper",
            },
        }
        with self.assertRaisesRegex(ValueError, "cosmetic wrapper"):
            build_graph(package(), value, run_id="r1", revision=1)

    def test_wrapper_cannot_hide_behind_term_type_annotation(self):
        typed = package()
        typed["contracts"][0]["exact_statement"] = (
            'lemma demo_corres [corres]: "R (x :: word32)"'
        )
        value = {
            "generated_nodes": [{
                "id": "node:wrapper",
                "name": "demo_wrapper",
                "role": "theorem",
                "statement": 'lemma demo_wrapper: "R x"',
                "context": [],
                "dependencies": [],
                "strategy": "drop redundant type syntax",
            }],
            "contract_dependencies": {
                "contract:demo": ["node:wrapper"],
            },
            "contract_strategies": {
                "contract:demo": "apply wrapper",
            },
        }
        with self.assertRaisesRegex(ValueError, "cosmetic wrapper"):
            build_graph(typed, value, run_id="r1", revision=1)

    def test_wrapper_cannot_move_implication_into_assumes(self):
        implied = package()
        implied["contracts"][0]["exact_statement"] = (
            'lemma demo_corres:\nshows "A \\<Longrightarrow> B"'
        )
        value = {
            "generated_nodes": [{
                "id": "node:wrapper",
                "name": "demo_wrapper",
                "role": "theorem",
                "statement": 'lemma demo_wrapper:\nassumes a: "A"\nshows "B"',
                "context": [],
                "dependencies": [],
                "strategy": "move implication into context",
            }],
            "contract_dependencies": {
                "contract:demo": ["node:wrapper"],
            },
            "contract_strategies": {
                "contract:demo": "apply wrapper",
            },
        }
        with self.assertRaisesRegex(ValueError, "logically equivalent"):
            build_graph(implied, value, run_id="r1", revision=1)

    def test_helper_cannot_spawn_generated_child(self):
        value = proposal()
        value["generated_nodes"][0]["dependencies"] = ["node:theorem"]
        with self.assertRaisesRegex(ValueError, "helper must be a generated leaf"):
            build_graph(package(), value, run_id="r1", revision=1)

    def test_revision_promotes_helper_that_gains_generated_dependency(self):
        value = proposal()
        value["generated_nodes"][1]["role"] = "helper"
        normalized, changes = normalize_generated_roles(value)
        self.assertEqual(
            normalized["generated_nodes"][1]["role"], "theorem"
        )
        self.assertEqual(changes[0]["node_id"], "node:theorem")
        build_graph(package(), normalized, run_id="r1", revision=2)

    def test_green_node_is_immutable(self):
        old = build_graph(package(), proposal(), run_id="r1", revision=1)
        changed = proposal()
        changed["generated_nodes"][0]["statement"] = 'lemma regen_helper: "C"'
        new = build_graph(package(), changed, run_id="r1", revision=2)
        with self.assertRaisesRegex(ValueError, "changed green node"):
            assert_green_nodes_unchanged(old, new, {"node:helper"})

    def test_green_theorem_can_gain_a_consumer(self):
        old = build_graph(package(), proposal(), run_id="r1", revision=1)
        changed = proposal()
        changed["generated_nodes"].append({
            "id": "node:second", "name": "regen_second", "role": "theorem",
            "statement": 'lemma regen_second: "D"',
            "dependencies": ["node:helper"], "strategy": "rule", "context": [],
        })
        changed["contract_dependencies"]["contract:demo"] = [
            "node:theorem", "node:second",
        ]
        new = build_graph(package(), changed, run_id="r1", revision=2)
        assert_green_nodes_unchanged(old, new, {"node:helper"})

    def test_partial_revision_preserves_other_contract_metadata(self):
        graph = build_graph(package(), {
            "generated_nodes": [],
            "contract_dependencies": {"contract:demo": []},
            "contract_strategies": {"contract:demo": "original strategy"},
        }, run_id="r1", revision=1)
        merged = merge_contract_metadata(graph, {
            "contract_dependencies": {},
            "contract_strategies": {},
        })
        self.assertEqual(
            merged["contract_strategies"]["contract:demo"],
            "original strategy",
        )
        self.assertEqual(merged["contract_dependencies"]["contract:demo"], [])

    def test_partial_add_preserves_unmentioned_generated_nodes(self):
        graph = build_graph(package(), proposal(), run_id="r1", revision=1)
        partial = {
            "operation": "ADD",
            "generated_nodes": [{
                "id": "node:second", "name": "regen_second",
                "role": "helper", "statement": 'lemma regen_second: "D"',
                "dependencies": [], "strategy": "simp", "context": [],
            }],
        }
        merged = merge_generated_metadata(graph, partial)
        self.assertEqual(
            {item["id"] for item in merged["generated_nodes"]},
            {"node:helper", "node:theorem", "node:second"},
        )

    def test_split_replaces_failed_node_and_preserves_unrelated_nodes(self):
        graph = build_graph(package(), proposal(), run_id="r1", revision=1)
        partial = {
            "operation": "SPLIT",
            "generated_nodes": [{
                "id": "node:replacement", "name": "regen_replacement",
                "role": "helper", "statement": 'lemma regen_replacement: "D"',
                "dependencies": [], "strategy": "simp", "context": [],
            }],
        }
        merged = merge_generated_metadata(
            graph, partial, replaced_node_id="node:theorem",
        )
        self.assertEqual(
            {item["id"] for item in merged["generated_nodes"]},
            {"node:helper", "node:replacement"},
        )

    def test_partial_delete_removes_target_and_preserves_unrelated_nodes(self):
        graph = build_graph(package(), proposal(), run_id="r1", revision=1)
        partial = {
            "operation": "DELETE",
            "generated_nodes": [{
                "id": "node:theorem", "name": "regen_theorem",
                "role": "theorem", "statement": 'lemma regen_theorem: "B"',
                "dependencies": [], "strategy": "rewired", "context": [],
            }],
        }
        merged = merge_generated_metadata(
            graph, partial, replaced_node_id="node:helper",
        )
        self.assertEqual(
            {item["id"] for item in merged["generated_nodes"]},
            {"node:theorem"},
        )

    def test_local_replacement_prunes_stale_unconsumed_chain(self):
        graph = build_graph(package(), proposal(), run_id="r1", revision=1)
        replacement = merge_contract_metadata(graph, {
            "operation": "REPLACE_STATEMENT",
            "generated_nodes": [{
                "id": "node:replacement", "name": "regen_replacement",
                "role": "helper", "statement": 'lemma replacement: "D"',
                "dependencies": [], "strategy": "simp", "context": [],
            }],
            "contract_dependencies": {
                "contract:demo": ["node:replacement"],
            },
        })
        replacement = merge_generated_metadata(
            graph, replacement, replaced_node_id="node:helper",
        )
        replacement, removed = prune_unconsumed_replacement_nodes(
            replacement, {"node:helper", "node:theorem"},
        )
        self.assertEqual(set(removed), {"node:helper", "node:theorem"})
        self.assertEqual(
            {item["id"] for item in replacement["generated_nodes"]},
            {"node:replacement"},
        )

    def test_replacement_preserves_orphaned_green_dependency(self):
        graph = build_graph(package(), proposal(), run_id="r1", revision=1)
        replacement = merge_contract_metadata(graph, {
            "operation": "SPLIT",
            "generated_nodes": [{
                "id": "node:theorem", "name": "regen_theorem",
                "role": "theorem", "statement": 'lemma regen_theorem: "B"',
                "dependencies": [], "strategy": "new split", "context": [],
            }],
        })
        replacement = merge_generated_metadata(
            graph, replacement, replaced_node_id="node:theorem",
        )
        replacement, changes = preserve_orphaned_green_dependencies(
            graph, replacement, "node:theorem", {"node:helper"},
        )
        theorem = next(
            node for node in replacement["generated_nodes"]
            if node["id"] == "node:theorem"
        )
        self.assertEqual(theorem["dependencies"], ["node:helper"])
        self.assertEqual(changes[0]["to"], "node:helper")

    def test_retry_class_cannot_authorize_graph_revision(self):
        decision = {
            "decision": "REVISE_GRAPH",
            "failure_class": "SEARCH_EXHAUSTED",
            "reason": "search did not close the goal",
            "residual_evidence": ["Stepwise exhausted its frontier"],
            "alternatives_considered": ["retry"],
            "confidence": 0.6,
        }
        with self.assertRaisesRegex(ValueError, "cannot authorize"):
            validate_decision(decision)

    def test_revision_requires_public_residual_evidence(self):
        decision = {
            "decision": "RETRY_PROOF",
            "failure_class": "PROOF_BODY_ERROR",
            "reason": "repair the tactic",
            "residual_evidence": [],
            "alternatives_considered": [],
            "confidence": 0.8,
        }
        with self.assertRaisesRegex(ValueError, "residual evidence"):
            validate_decision(decision)

    def test_graph_diff_records_structural_change(self):
        old = build_graph(package(), proposal(), run_id="r1", revision=1)
        changed = proposal()
        changed["generated_nodes"].append({
            "id": "node:second", "name": "regen_second", "role": "helper",
            "statement": 'lemma regen_second: "D"', "dependencies": [],
            "strategy": "simp", "context": [],
        })
        changed["contract_dependencies"]["contract:demo"].append("node:second")
        new = build_graph(package(), changed, run_id="r1", revision=2)
        diff = graph_diff(old, new)
        self.assertEqual(diff["added_nodes"], ["node:second"])
        self.assertTrue(diff["execution_order_changed"])

    def test_revision_prompt_forbids_recreating_legal_premise(self):
        graph = build_graph(package(), proposal(), run_id="r1", revision=1)
        prompt = revision_prompt(
            package(), graph,
            {"status": "red", "retrieved_legal_premises": []}, set(),
        )
        self.assertIn("Never add a generated", prompt)
        self.assertIn("wraps an existing", prompt)


class RetrievalTests(unittest.TestCase):
    def test_revision_hint_survives_long_residual_focus(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "premises.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE premises (record_id TEXT, canonical_name TEXT, "
                "exact_statement TEXT, owner_theory TEXT, owner_session TEXT, "
                "source_class TEXT, attributes_json TEXT)"
            )
            rows = [(
                f"noise-{index}", f"Map.map_to_ctes_case_{index}",
                "map_to_ctes KOCTE kernel_object option split dom range " * 5,
                "Map", "Parent", "frozen_parent", "[]",
            ) for index in range(60)]
            rows.append((
                "bridge", "Invariants_H.ps_clear_def2",
                "p <= p + 1 implies ps_clear p n s equals "
                "({p + 1..p + 2 ^ n - 1} intersect dom (ksPSpace s) = {})",
                "Invariants_H", "Semantic_Base", "retained_semantic_export", "[]",
            ))
            connection.executemany(
                "INSERT INTO premises VALUES (?, ?, ?, ?, ?, ?, ?)", rows,
            )
            connection.commit()
            connection.close()
            result = retrieve_proof_premises(
                database,
                "map_to_ctes KOCTE " + " ".join(
                    f"constructor_residual_{index}" for index in range(80)
                ),
                retrieval_hint="ps_clear_def ps_clear range dom disjoint",
                residual="map_to_ctes KOCTE kernel_object option split dom range",
                limit=8,
            )
            self.assertIn("bridge", [item["record_id"] for item in result])

    def test_set_endpoint_abbreviation_retrieves_real_definition(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "premises.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE premises (record_id TEXT, canonical_name TEXT, exact_statement TEXT, owner_theory TEXT, owner_session TEXT, source_class TEXT, attributes_json TEXT)")
            connection.executemany(
                "INSERT INTO premises VALUES (?, ?, ?, ?, ?, ?, ?)", [
                    ("setter", "KHeap_A.set_simple_ko_def", "set_simple_ko f ptr ep equals get_object then set_object", "KHeap_A", "Semantic_Base", "retained_semantic_export", "[]"),
                    ("wrong-case", "Endpoint_H.setEndpoint_def", "setEndpoint ptr ep equals updateObject_default ep", "Endpoint_H", "Semantic_Base", "retained_semantic_export", "[]"),
                ],
            )
            connection.commit()
            connection.close()
            result = retrieve_premises(database, "fst (set_endpoint ptr e s)", limit=2)
            ids = [item["record_id"] for item in result]
            self.assertIn("setter", ids)
            self.assertLess(ids.index("setter"), ids.index("wrong-case"))
            guidance = " ".join(semantic_bridge_guidance("set_endpoint p e s"))
            self.assertIn("abbreviation", guidance)
            self.assertIn("there is no set_endpoint_def", guidance)

    def test_file_planning_environment_exposes_no_premise_records(self):
        environment = planning_environment(package())
        self.assertEqual(environment["policy"], "fixed-contracts-only-v1")
        self.assertEqual(environment["contract_count"], 1)
        serialized = json.dumps(environment)
        self.assertNotIn("canonical_name", serialized)
        self.assertNotIn("exact_statement", serialized)
        self.assertNotIn("legal_declarations", serialized)


    def test_whole_proof_prompt_blacklists_undefined_facts(self):
        prompt = proof_prompt(
            proposal()["generated_nodes"][0], [], [], None,
            forbidden_facts={"ko_at_def"},
        )
        self.assertNotIn("ko_at_def", prompt)
        self.assertIn("Never invent a fact", prompt)

    def test_repair_guidance_blacklists_rejected_fact(self):
        self.assertEqual(
            extract_forbidden_facts("Avoid nonexistent ko_at_def."),
            {"ko_at_def"},
        )

    def test_plain_avoid_guidance_blacklists_backticked_facts(self):
        self.assertEqual(
            extract_forbidden_facts(
                "Avoid `ko_at_def` and `ko_atE`; use obj_at_def instead."
            ),
            {"ko_at_def", "ko_atE"},
        )

    def test_state_premises_precede_deduplicated_initial_records(self):
        old = [{"record_id": "p1"}, {"record_id": "p2"}]
        dynamic = [{"record_id": "p2"}, {"record_id": "p3"}]
        self.assertEqual(
            [item["record_id"] for item in merge_premises(dynamic, old)],
            ["p2", "p3", "p1"],
        )

    def test_isabelle_syntax_noise_is_not_a_retrieval_term(self):
        self.assertEqual(
            tokens("lemma x: \\<lbrakk>P; Q\\<rbrakk> \\<Longrightarrow> R"),
            [],
        )

    def test_query_terms_prioritize_isabelle_identifiers(self):
        terms = _query_terms([
            "endpoint", "object", "relation", "pspace_relation_absd",
            "obj_relation_cutse",
        ], limit=2)
        self.assertEqual(
            terms, ["pspace_relation_absd", "obj_relation_cutse"],
        )

    def test_balanced_focus_terms_keep_constructor_names(self):
        terms = _query_terms([
            "pspace_relation_absd", "obj_relation_cutse", "koendpoint",
            "projectko", "endpoint",
        ], limit=4, balanced=True)
        self.assertEqual(
            terms,
            ["pspace_relation_absd", "obj_relation_cutse", "koendpoint", "projectko"],
        )

    def test_runtime_retrieval(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "premises.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE premises (record_id TEXT, canonical_name TEXT, "
                "exact_statement TEXT, owner_theory TEXT, owner_session TEXT, "
                "source_class TEXT, attributes_json TEXT)"
            )
            connection.executemany(
                "INSERT INTO premises VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    ("p1", "corres_gets", "corres r P P' (gets f) g",
                     "Corres", "Semantic_Base", "retained_semantic_export", "[]"),
                    ("p2", "unrelated", "x = x", "HOL", "HOL",
                     "frozen_parent", "[]"),
                    ("p3", "private_corres", "??.Hidden.corres P",
                     "Hidden", "Semantic_Base", "retained_semantic_export", "[]"),
                    ("p4", "Corres.gets_def__internal__", "corres gets internal",
                     "Corres", "Semantic_Base", "retained_semantic_export", "[]"),
                ],
            )
            connection.commit()
            connection.close()
            result = retrieve_premises(database, "prove corres gets")
            self.assertEqual(result[0]["record_id"], "p1")
            self.assertNotIn("p3", [item["record_id"] for item in result])
            self.assertNotIn("p4", [item["record_id"] for item in result])

    def test_ep_at_residual_retrieves_obj_at_eliminator(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "premises.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE premises (record_id TEXT, canonical_name TEXT, "
                "exact_statement TEXT, owner_theory TEXT, owner_session TEXT, "
                "source_class TEXT, attributes_json TEXT)"
            )
            connection.executemany(
                "INSERT INTO premises VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    ("elim", "Invariants_H.obj_atE'",
                     "obj_at' P ptr s implies ksPSpace s ptr = Some ko "
                     "and ps_clear ptr (objBitsKO ko) s",
                     "Invariants_H", "Semantic_Base",
                     "retained_semantic_export", "[]"),
                    ("definition", "Invariants_H.obj_at'_def",
                     "obj_at' P ptr s equals existence of ko and obj",
                     "Invariants_H", "Semantic_Base",
                     "retained_semantic_export", "[]"),
                    ("noise", "Invariants_H.ps_clear_lookupAround2",
                     "ps_clear p n s implies lookupAround2 p map = result",
                     "Invariants_H", "Semantic_Base",
                     "retained_semantic_export", "[]"),
                ],
            )
            connection.commit()
            connection.close()
            result = retrieve_premises(
                database,
                "prove endpoint lookup",
                focus_query=(
                    "alpha_one beta_two gamma_three delta_four epsilon_five "
                    "zeta_six eta_seven theta_eight iota_nine kappa_ten "
                    "lambda_eleven mu_twelve nu_thirteen xi_fourteen "
                    "omicron_fifteen ep_at' ptr s implies ksPSpace s ptr = Some ko"
                ),
            )
            ids = [item["record_id"] for item in result]
            self.assertIn("elim", ids)
            self.assertIn("definition", ids)
            self.assertLess(ids.index("elim"), ids.index("noise"))

    def test_ep_at_alias_survives_long_query_without_explicit_focus(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "premises.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE premises (record_id TEXT, canonical_name TEXT, "
                "exact_statement TEXT, owner_theory TEXT, owner_session TEXT, "
                "source_class TEXT, attributes_json TEXT)"
            )
            rows = [(
                f"noise-{index}", f"Noise.generated_type_fact_{index}",
                "endpoint lookup object " * 20,
                "Noise", "Parent", "frozen_parent", "[]",
            ) for index in range(60)]
            rows.append((
                "elim", "Invariants_H.obj_atE'",
                "obj_at' P ptr s implies ksPSpace s ptr = Some ko and ps_clear ptr n s",
                "Invariants_H", "Semantic_Base", "retained_semantic_export", "[]",
            ))
            connection.executemany(
                "INSERT INTO premises VALUES (?, ?, ?, ?, ?, ?, ?)", rows,
            )
            connection.commit()
            connection.close()
            long_history = " ".join(
                f"historical_identifier_{index}" for index in range(80)
            )
            result = retrieve_premises(
                database,
                f"endpoint lookup {long_history} ep_at' ptr s",
                limit=8,
            )
            self.assertIn("elim", [item["record_id"] for item in result])

    def test_residual_focus_promotes_bridge_premises(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "premises.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE premises (record_id TEXT, canonical_name TEXT, "
                "exact_statement TEXT, owner_theory TEXT, owner_session TEXT, "
                "source_class TEXT, attributes_json TEXT)"
            )
            connection.executemany(
                "INSERT INTO premises VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    ("bridge", "Invariants_H.projectKO_eq",
                     "fst (projectKO ko s) = {(obj, s)} = "
                     "(projectKO_opt ko = Some obj)",
                     "Invariants_H", "Semantic_Base",
                     "retained_semantic_export", "[]"),
                    ("constructor", "ObjectInstances_H.projectKO_opt_ep",
                     "projectKO_opt (KOEndpoint ep) = Some ep",
                     "ObjectInstances_H", "Semantic_Base",
                     "retained_semantic_export", "[]"),
                    ("broad", "pspace_relation_absD",
                     "pspace_relation a c implies object relation",
                     "StateRelation", "Semantic_Base",
                     "retained_semantic_export", "[]"),
                ],
            )
            connection.commit()
            connection.close()
            result = retrieve_premises(
                database,
                "pspace_relation endpoint object relation",
                focus_query="fst (projectKO (KOEndpoint ep) t) = {(ep', t)}",
            )
            ids = [item["record_id"] for item in result]
            self.assertIn("bridge", ids)
            self.assertIn("constructor", ids)
            self.assertLess(ids.index("bridge"), ids.index("broad"))

    def test_aligned_mask_residual_retrieves_no_overflow_bridge(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "premises.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE premises (record_id TEXT, canonical_name TEXT, "
                "exact_statement TEXT, owner_theory TEXT, owner_session TEXT, "
                "source_class TEXT, attributes_json TEXT)"
            )
            connection.executemany(
                "INSERT INTO premises VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    ("overflow", "Aligned.is_aligned_no_overflow",
                     "is_aligned ptr sz implies ptr <= ptr + 2 ^ sz - 1",
                     "Aligned", "Word_Lib", "frozen_parent", "[]"),
                    ("mask", "Aligned.is_aligned_no_overflow_mask",
                     "is_aligned ptr sz implies ptr <= ptr + mask sz",
                     "Aligned", "Word_Lib", "frozen_parent", "[]"),
                    ("aligned-mask", "Aligned.is_aligned_mask",
                     "is_aligned ptr sz = (ptr && mask sz = 0)",
                     "Aligned", "Word_Lib", "frozen_parent", "[]"),
                    ("mask-def", "Word_Lib_Sumo.mask_def",
                     "mask n = (1 << n) - 1", "Word_Lib_Sumo", "Word_Lib",
                     "frozen_parent", "[]"),
                    ("noise", "Word.noise", "ptr <= ptr", "Word", "Word_Lib",
                     "frozen_parent", "[]"),
                ],
            )
            connection.commit()
            connection.close()
            residual = "ptr && 0xF = 0 \\<Longrightarrow> ptr \\<le> 0xF + ptr"
            self.assertIn("is_aligned_no_overflow", semantic_bridge_tokens(residual))
            result = retrieve_premises(
                database, "endpoint lookup", focus_query=residual, limit=4,
            )
            ids = [item["record_id"] for item in result]
            self.assertIn("overflow", ids)
            self.assertIn("mask", ids)
            self.assertIn("aligned-mask", ids)
            self.assertIn("mask-def", ids)
            self.assertIn(ids[0], {"overflow", "mask"})

    def test_corres_goal_requests_framework_intro_rule(self):
        state = (
            "corres ep_relation (ep_at ptr) (ep_at' ptr) "
            "(get_endpoint ptr) (getEndpoint ptr)"
        )
        bridge = semantic_bridge_tokens(state)
        self.assertIn("corres_underlyingi", bridge)
        self.assertIn("corres_underlying_def", bridge)
        guidance = " ".join(semantic_bridge_guidance(state))
        self.assertIn("concrete result", guidance)
        self.assertIn("does not fail", guidance)
        aliases = semantic_alias_tokens(state)
        self.assertIn("get_simple_ko_def", aliases)
        self.assertIn("get_object_def", aliases)
        self.assertIn("ep_at_def2", aliases)
        self.assertIn("obj_at_def", aliases)

    def test_monad_membership_state_requests_elimination_rules(self):
        state = (
            "proof (prove) goal (1 subgoal): "
            "(ep, s) \\<in> fst (getObject ptr s)"
        )
        bridge = semantic_bridge_tokens(state)
        self.assertIn("in_bind", bridge)
        self.assertIn("in_assert", bridge)
        self.assertIn("in_assert_opt", bridge)
        guidance = " ".join(semantic_bridge_guidance(state))
        self.assertIn("monad-result membership", guidance)
        self.assertIn("in_assert_opt", guidance)

        projection = (
            "(ep, s') \\<in> fst (projectKO ko s)"
        )
        self.assertIn("projectko_eq2", semantic_bridge_tokens(projection))
        self.assertIn(
            "recover the constructor",
            " ".join(semantic_bridge_guidance(projection)),
        )

    def test_endpoint_constructor_residual_requests_exact_witness(self):
        guidance = " ".join(semantic_bridge_guidance(
            "kheap s ptr = Some (Endpoint y) and (ep, s) in fst (get_endpoint ptr s)"
        ))
        self.assertIn("payload", guidance)
        self.assertIn("witness", guidance)
        self.assertIn("do not ask clarsimp", guidance)

    def test_isar_state_mode_requests_structural_transition(self):
        guidance = " ".join(semantic_bridge_guidance(
            "proof (state) this: before = Some x goal (1 subgoal): P"
        ))
        self.assertIn("structural transition next", guidance)
        self.assertIn("before any apply/by tactic", guidance)

    def test_lookup_around_exact_slot_requests_characterization(self):
        state = (
            "lookupAround2 ptr (ksPSpace s) = "
            "(Some (ptr, ko), after)"
        )
        self.assertIn("lookuparound2_char1", semantic_bridge_tokens(state))
        guidance = " ".join(semantic_bridge_guidance(state))
        self.assertIn("fst equality", guidance)
        self.assertIn("do not unfold lookupAround2", guidance)

    def test_stepwise_budget_is_not_whole_proof_retry_count(self):
        budget = stepwise_search_budget(5)
        self.assertEqual(budget["max_attempts"], 10)
        self.assertEqual(budget["selected_states"], 2)
        self.assertEqual(budget["max_depth"], 48)

    def test_stepwise_state_envelope_contains_outline_and_bridge_premise(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "premises.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE premises (record_id TEXT, canonical_name TEXT, "
                "exact_statement TEXT, owner_theory TEXT, owner_session TEXT, "
                "source_class TEXT, attributes_json TEXT)"
            )
            connection.executemany(
                "INSERT INTO premises VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    ("overflow", "Aligned.is_aligned_no_overflow",
                     "is_aligned ptr sz implies ptr <= ptr + 2 ^ sz - 1",
                     "Aligned", "Word_Lib", "frozen_parent", "[]"),
                    ("aligned-mask", "Aligned.is_aligned_mask",
                     "is_aligned ptr sz = (ptr && mask sz = 0)",
                     "Aligned", "Word_Lib", "frozen_parent", "[]"),
                    ("mask-def", "Word_Lib_Sumo.mask_def",
                     "mask n = (1 << n) - 1", "Word_Lib_Sumo", "Word_Lib",
                     "frozen_parent", "[]"),
                ],
            )
            connection.commit()
            connection.close()
            outline = json.dumps({
                "local_proof_outline": {
                    "local_steps": [{
                        "id": "local-6",
                        "local_statement": "prove the aligned upper bound first",
                    }],
                },
            })
            generator = AuditedCodexStepGenerator(
                root / "audit", model="gpt-5.5", width=4, timeout=10,
                premise_hints=[], theory="Refine.KHeap_R",
                repair_guidance=outline, premise_database=database,
                retrieval_query="endpoint lookup",
            )
            state = "ptr && 0xF = 0 \\<Longrightarrow> ptr \\<le> 0xF + ptr"
            with patch(
                "regen.execution.stepwise_lemma_backend.call_llm",
                return_value=json.dumps({
                    "steps": ["apply (rule Aligned.is_aligned_no_overflow)"],
                }),
            ) as mocked:
                ranked = generator.generate(encode_state_request(
                    state, ["apply (erule Invariants_H.obj_atE')"],
                ))
            self.assertEqual(mocked.call_args.kwargs["timeout"], 10)
            self.assertTrue(mocked.call_args.kwargs["clean_runtime"])
            self.assertEqual(
                ranked[0][0], "apply (rule Aligned.is_aligned_no_overflow)",
            )
            payload = json.loads(
                (root / "audit/0001-proof-step.premises.json").read_text()
            )
            self.assertEqual(payload["proof_state"], state)
            self.assertEqual(
                payload["proof_prefix"],
                ["apply (erule Invariants_H.obj_atE')"],
            )
            self.assertIn(
                "is_aligned_no_overflow", payload["state_bridge_terms"],
            )
            self.assertIn(
                "Aligned.is_aligned_no_overflow",
                [item["canonical_name"] for item in payload["records"]],
            )
            self.assertIn(
                "Aligned.is_aligned_mask",
                [item["canonical_name"] for item in payload["records"]],
            )
            self.assertTrue(payload["state_phase_guidance"])
            prompt = mocked.call_args.args[1]
            self.assertIn("prove the aligned upper bound first", prompt)
            self.assertIn("Aligned.is_aligned_no_overflow", prompt)
            self.assertIn("Do not erule is_aligned_no_overflow", prompt)

    def test_retrieval_diversifies_generated_datatype_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "premises.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE premises (record_id TEXT, canonical_name TEXT, "
                "exact_statement TEXT, owner_theory TEXT, owner_session TEXT, "
                "source_class TEXT, attributes_json TEXT)"
            )
            rows = [
                (f"noise-{index}", f"Structures_A.endpoint.case({index})",
                 "endpoint endpoint endpoint projectKO", "Structures_A",
                 "Semantic_Base", "retained_semantic_export", "[]")
                for index in range(1, 41)
            ]
            rows.append((
                "bridge", "Invariants_H.projectKO_eq",
                "fst (projectKO ko s) = {(obj, s)}", "Invariants_H",
                "Semantic_Base", "retained_semantic_export", "[]",
            ))
            connection.executemany(
                "INSERT INTO premises VALUES (?, ?, ?, ?, ?, ?, ?)", rows,
            )
            connection.commit()
            connection.close()
            result = retrieve_premises(
                database, "endpoint", limit=8,
                focus_query="fst (projectKO ko s) = {(obj, s)}",
            )
            self.assertIn("bridge", [item["record_id"] for item in result])
            self.assertLessEqual(
                sum(item["record_id"].startswith("noise-") for item in result),
                2,
            )


class CheckerAndRunnerTests(unittest.TestCase):
    def test_statement_certification_rejects_retry_even_if_policy_drifted(self):
        request = {
            "decision_context": {"phase": "statement-certification"},
            "decision_policy": {"retry_proof_allowed": True},
        }
        decision = {
            "decision": "RETRY_PROOF",
            "failure_class": "PROOF_BODY_ERROR",
            "reason": "retry a proof that does not exist",
            "residual_evidence": ["statement shell has a lexical error"],
            "alternatives_considered": [],
            "confidence": 0.9,
        }
        with self.assertRaisesRegex(ValueError, "current graph-decision phase"):
            validate_decision(decision, request)

    def test_statement_certification_forbids_retry_and_counts_every_decision(self):
        source = inspect.getsource(_run_file_unlocked)
        start = source.index('"phase": "statement-certification"')
        end = source.index("while not all(", start)
        certification = source[start:end]
        self.assertIn('"retry_proof_allowed": False', certification)
        count = certification.index(
            'node_revision_counts["__graph__"] = graph_revisions + 1'
        )
        retry = certification.index(
            'if decision["decision"] == "RETRY_PROOF"'
        )
        self.assertLess(count, retry)

    def test_run_file_configures_monitor_before_first_event(self):
        source = inspect.getsource(_run_file_unlocked)
        self.assertLess(
            source.index("configure_event_monitor("),
            source.index("emit_event("),
        )

    def test_runner_monitor_persists_events_and_terminal_status(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            configure_event_monitor(
                run_dir, run_id="run:test", theory="Refine.Demo_R",
                heartbeat_seconds=0,
            )
            emit_event(
                "node_started", theory="Refine.Demo_R",
                node="demo_corres", proof_round=2,
            )
            close_event_monitor("interrupted")
            events = [json.loads(line) for line in (
                run_dir / "runner-events.ndjson"
            ).read_text().splitlines()]
            status = json.loads((run_dir / "runner-status.json").read_text())
        self.assertEqual(events[0]["event"], "node_started")
        self.assertEqual(events[-1]["event"], "runner_monitor_stopped")
        self.assertEqual(
            status["active_event"]["event"], "runner_monitor_stopped",
        )
        self.assertFalse(status["running"])
        self.assertEqual(status["stop_reason"], "interrupted")

    def test_run_mutation_lock_serializes_different_theories(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            first = acquire_run_mutation_lock(
                run_dir, operation="run-file", theory="Refine.ArchAcc_R",
            )
            try:
                owner = json.loads((
                    run_dir / ".runner-locks/session-mutation.lock"
                ).read_text())
                self.assertEqual(owner["theory"], "Refine.ArchAcc_R")
                with self.assertRaisesRegex(
                    RuntimeError, "another runner owns mutable run state",
                ):
                    acquire_run_mutation_lock(
                        run_dir, operation="run-file",
                        theory="Refine.CSpace1_R",
                    )
            finally:
                release_run_mutation_lock(first)

    def test_interrupted_attempt_recovery_requires_current_checker_source(self):
        graph = build_graph(package(), proposal(), run_id="r1", revision=1)
        graph["graph_payload_sha256"] = graph_payload_sha256(graph)
        node = next(item for item in graph["nodes"] if item["id"] == "contract:demo")
        with tempfile.TemporaryDirectory() as directory:
            theory_dir = Path(directory)
            round_dir = node_log_directory(
                theory_dir, node["id"],
            ) / "round-03"
            round_dir.mkdir(parents=True)
            source = theory_dir / "Node_Check.thy"
            proof = "by simp"
            source.write_text("\n".join(
                item["statement"] for item in graph["nodes"]
            ) + "\n" + proof)
            (round_dir / "attempts.json").write_text(json.dumps([{
                "attempt": 1,
                "proof_body": proof,
                "retrieved_premise_names": ["Demo.bridge"],
                "check": {
                    "verdict": "red", "returncode": 1,
                    "diagnostic": "remaining goal P", "source": str(source),
                },
            }]))
            feedback = recover_latest_attempt_feedback(
                theory_dir, graph, node["id"],
            )
            self.assertEqual(feedback["round_id"], 3)
            self.assertTrue(feedback["recovered_after_interruption"])
            self.assertEqual(feedback["graph_payload_sha256"], graph[
                "graph_payload_sha256"
            ])
            source.write_text("\n".join([
                next(
                    item for item in graph["nodes"]
                    if item["id"] == "node:theorem"
                )["statement"],
                node["statement"], proof,
            ]))
            self.assertIsNone(recover_latest_attempt_feedback(
                theory_dir, graph, node["id"],
            ))
            source.write_text("\n".join(
                item["statement"] for item in graph["nodes"]
            ) + "\n" + proof)
            changed = dict(graph)
            changed["nodes"] = [dict(item) for item in graph["nodes"]]
            next(
                item for item in changed["nodes"] if item["id"] == node["id"]
            )["statement"] = 'lemma regen_helper: "B"'
            self.assertIsNone(recover_latest_attempt_feedback(
                theory_dir, changed, node["id"],
            ))

    def test_gra_feedback_is_recovered_and_consumed_exactly_once(self):
        graph = build_graph(package(), proposal(), run_id="r1", revision=1)
        graph["graph_payload_sha256"] = graph_payload_sha256(graph)
        node = next(item for item in graph["nodes"] if item["id"] == "node:helper")
        decision = {
            "decision": "RETRY_PROOF",
            "failure_class": "PROOF_BODY_ERROR",
            "reason": "use the bridge explicitly",
            "residual_evidence": ["simp leaves the bridge goal"],
            "alternatives_considered": ["the statement shell is green"],
            "confidence": 0.9,
            "repair_guidance": "apply Demo.bridge before simp",
        }
        with tempfile.TemporaryDirectory() as directory:
            theory_dir = Path(directory)
            audit = theory_dir / "graph-revisions/decision-007"
            audit.mkdir(parents=True)
            (audit / "request.json").write_text(json.dumps({
                "current_graph": graph,
                "failed_node": {"node_id": node["id"], "attempts": []},
                "decision_context": {"phase": "node-proof", "proof_round": 2},
                "decision_policy": {"retry_proof_allowed": True},
            }))
            (audit / "decision.json").write_text(json.dumps(decision))
            feedback = recover_gra_feedback(theory_dir, graph, node["id"])
            self.assertIsNotNone(feedback)
            consumed = consume_gra_feedback(
                theory_dir, graph, node, feedback, proof_round=3,
            )
            self.assertEqual(
                consumed,
                gra_feedback_consumption_path(theory_dir, "decision-007"),
            )
            self.assertIsNone(recover_gra_feedback(
                theory_dir, graph, node["id"],
            ))
            self.assertEqual(
                consume_gra_feedback(
                    theory_dir, graph, node, feedback, proof_round=3,
                ),
                consumed,
            )
            with self.assertRaisesRegex(ValueError, "another round"):
                consume_gra_feedback(
                    theory_dir, graph, node, feedback, proof_round=4,
                )

    def test_resume_feedback_keeps_richer_same_round_state(self):
        current = {
            "round_id": 3,
            "local_proof_outline": {"steps": ["have P"]},
        }
        recovered = {
            "round_id": 3,
            "proof_candidates": [{"attempt": 4}],
        }
        merged = select_resume_feedback(current, recovered)
        self.assertEqual(merged["local_proof_outline"], {"steps": ["have P"]})
        self.assertEqual(merged["proof_candidates"], [{"attempt": 4}])

    def test_generated_premise_publication_uses_isabelle_name_and_contract_attributes(self):
        contract_package = package()
        contract_package["contracts"][0]["contract_owned_attributes"] = ["corres"]
        graph = build_graph(contract_package, {
            "generated_nodes": [],
            "contract_dependencies": {"contract:demo": []},
        }, run_id="generation:demo", revision=1)
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            (run_dir / "generated-premises.json").write_text(json.dumps({
                "records": [{
                    "record_id": "generated:Refine.Demo_R:demo_corres",
                    "canonical_name": "Refine.Demo_R.demo_corres",
                    "exact_statement": "stale",
                    "owner_theory": "Refine.Demo_R",
                    "owner_session": "Generated_Refine",
                    "source_class": "generated_theorem",
                    "attributes": [],
                    "theory_seal_id": "old-seal",
                }],
            }))
            publish_generated_records(
                run_dir, graph, "theory-seal:demo", contract_package,
            )
            first = json.loads(
                (run_dir / "generated-premises.json").read_text()
            )
            publish_generated_records(
                run_dir, graph, "theory-seal:demo", contract_package,
            )
            second = json.loads(
                (run_dir / "generated-premises.json").read_text()
            )
        self.assertEqual(first, second)
        self.assertEqual(len(first["records"]), 1)
        record = first["records"][0]
        self.assertEqual(record["canonical_name"], "Regen_Demo_R.demo_corres")
        self.assertEqual(record["attributes"], ["corres"])
        self.assertEqual(record["theory_seal_id"], "theory-seal:demo")

    def test_generated_checkpoint_names_do_not_collide_with_semantic_theories(self):
        self.assertEqual(
            generated_checkpoint_theory("Bits_R.thy"), "Regen_Bits_R",
        )
        files = {"files": [{
            "file_slot": "Bits_R.thy", "status": "sealed",
            "checkpoint_theory": "Regen_Bits_R",
        }, {
            "file_slot": "CNodeInv_R.thy", "status": "pending",
        }]}
        self.assertEqual(
            sealed_checkpoint_theories(files), ["Regen_Bits_R"],
        )

    def test_codex_audit_allows_transport_errors_but_rejects_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory) / "events.ndjson"
            raw.write_text("\n".join([
                json.dumps({"type": "error", "message": "reconnecting"}),
                json.dumps({
                    "type": "item.completed",
                    "item": {"id": "item_0", "type": "error"},
                }),
                json.dumps({
                    "type": "item.completed",
                    "item": {"id": "item_1", "type": "agent_message"},
                }),
            ]) + "\n")
            audit = audit_events(raw)
            self.assertTrue(audit["no_tool_events"])
            self.assertEqual(audit["forbidden_item_types"], [])
            self.assertEqual(audit["transport_diagnostic_count"], 2)

            raw.write_text(json.dumps({
                "type": "item.completed",
                "item": {"id": "item_2", "type": "command_execution"},
            }) + "\n")
            audit = audit_events(raw)
            self.assertFalse(audit["no_tool_events"])
            self.assertEqual(
                audit["forbidden_item_types"], ["command_execution"],
            )

    def test_lemma_prover_records_provider_failure_instead_of_crashing(self):
        graph = build_graph(package(), {
            "generated_nodes": [],
            "contract_dependencies": {"contract:demo": []},
        }, run_id="r1", revision=1)

        class NeverCalledChecker:
            def check_node(self, *args, **kwargs):
                raise AssertionError("provider failure must not reach Isabelle")

        with tempfile.TemporaryDirectory() as directory, patch(
            "regen.execution.lemma_prover.retrieve_proof_premises", return_value=[],
        ), patch(
            "regen.execution.lemma_prover.call_llm",
            side_effect=RuntimeError("temporary transport failure"),
        ):
            root = Path(directory)
            result = prove_node(
                graph, "contract:demo", {}, checker=NeverCalledChecker(),
                premise_database=root / "premises.sqlite3",
                output_dir=root / "theories" / "Demo_R",
                max_attempts=2, backend="whole-proof",
            )
        self.assertEqual(result["status"], "red")
        self.assertEqual(result["failure_scope"], "infrastructure")
        self.assertEqual(result["attempts"], [])
        self.assertEqual(len(result["provider_failures"]), 2)
        self.assertIn("temporary transport failure", result["residual"])

    def test_invalid_model_proof_is_handed_to_gra_without_checker(self):
        graph = build_graph(package(), {
            "generated_nodes": [], "contract_dependencies": {"contract:demo": []},
        }, run_id="r1", revision=1)

        class NeverCalledChecker:
            def check_node(self, *args, **kwargs):
                raise AssertionError("invalid model output must not reach Isabelle")

        for response in ('{"proof_body": null, "error": "missing inputs"}',
                         '{"proof_body": 17}', '{"proof_body": "  "}',
                         '{}', 'not json'):
            def fake_call(provider, prompt, prefix, **kwargs):
                prefix.parent.mkdir(parents=True, exist_ok=True)
                prefix.with_suffix(".meta.json").write_text(json.dumps({"sealable_generation": True}))
                return response

            with self.subTest(response=response), tempfile.TemporaryDirectory() as directory, patch(
                "regen.execution.lemma_prover.retrieve_proof_premises", return_value=[],
            ), patch("regen.execution.lemma_prover.call_llm", side_effect=fake_call), patch(
                "regen.execution.lemma_prover.write_llm_module_log",
            ):
                root = Path(directory)
                result = prove_node(
                    graph, "contract:demo", {}, checker=NeverCalledChecker(),
                    premise_database=root / "premises.sqlite3",
                    output_dir=root / "theories" / "Demo_R",
                    max_attempts=2, backend="whole-proof",
                )
                self.assertEqual(result["status"], "red")
                self.assertTrue(result["sealable_generation"])
                self.assertEqual(len(result["attempts"]), 1)
                self.assertFalse(result["attempts"][0]["check"]["isabelle_called"])
                self.assertEqual(result["attempts"][0]["check"]["failure_scope"], "model_output")
                self.assertIn("no valid proof candidate", result["early_handoff"])
                self.assertTrue(list(root.glob("theories/Demo_R/nodes/*/round-01/result.json")))

    def test_checker_timeout_normalizes_mixed_output_types(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checker = test_checker(root, root / "checks")
            timeout = subprocess.TimeoutExpired(
                ["isabelle"], 1, output=(
                    b"partial stdout\n"
                    b"REGEN_NODE node:timeout-target\n"
                ),
                stderr="partial stderr\n",
            )
            with patch.object(
                    checker, "_command",
                    return_value=["docker", "run", "--rm", "image"],
                ), patch(
                        "regen.execution.isabelle_node_checker.subprocess.run",
                        side_effect=timeout,
                    ) as run:
                result = checker._check(
                    "theory Demo imports Main begin end", "Timeout",
                )
            self.assertEqual(result["verdict"], "red")
            self.assertEqual(result["returncode"], 124)
            self.assertIn("partial stdout", result["diagnostic"])
            self.assertIn("partial stderr", result["diagnostic"])
            self.assertIn("timed out after", result["diagnostic"])
            self.assertEqual(
                result["last_diagnostic_marker"], "node:timeout-target",
            )
            self.assertEqual(result["diagnostic_marker_count"], 1)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args_list[1].args[0][:3], [
                "docker", "rm", "-f",
            ])

    def test_codex_timeout_normalizes_partial_bytes_before_logging(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "call"
            timeout = subprocess.TimeoutExpired(
                ["codex"], 1,
                output=b'{"type":"thread.started"}\n',
                stderr=b"partial stderr\n",
            )
            def partial_timeout(argv, **kwargs):
                kwargs['raw_path'].write_bytes(timeout.stdout)
                kwargs['stderr_path'].write_bytes(timeout.stderr)
                raise timeout
            with patch(
                "regen.infrastructure.audited_llm._run_provider", side_effect=partial_timeout,
            ):
                with self.assertRaisesRegex(RuntimeError, "exit code 124"):
                    call_codex(
                        "prompt", prefix, timeout=1,
                        codex_bin=Path("/bin/true"),
                        clean_runtime=True,
                    )
            self.assertIn(
                "thread.started", prefix.with_suffix(".ndjson").read_text(),
            )
            self.assertIn(
                "partial stderr", prefix.with_suffix(".stderr.txt").read_text(),
            )
            meta = json.loads(prefix.with_suffix(".meta.json").read_text())
            self.assertTrue(meta["clean_runtime"])
            for option in (
                "--ignore-user-config", "--ignore-rules", "--ephemeral",
                "model_reasoning_effort=\"medium\"",
                "apps", "plugins", "remote_plugin", "hooks",
            ):
                self.assertIn(option, meta["argv"])

    def test_stepwise_warm_start_binds_exact_prior_statement(self):
        from regen.execution.stepwise_lemma_backend import build_warm_start_request

        node = {"statement": 'lemma demo: "P"'}
        request = build_warm_start_request(node, {
            "statement": node["statement"],
            "last_proof_body": "proof -\n  show P by simp\nqed",
            "round_id": 4,
            "backend": "whole-proof",
            "residual": "Failed to finish proof",
        })
        self.assertEqual(request["mode"], "verified-prefix-replay")
        self.assertEqual(request["source_round"], 4)
        self.assertIn("show P", request["proof_body"])

    def test_stepwise_warm_start_keeps_all_prior_candidates(self):
        from regen.execution.stepwise_lemma_backend import build_warm_start_request

        node = {"statement": 'lemma demo: "P"'}
        result = {
            "attempts": [
                {
                    "attempt": 1, "proof_body": "proof -\n  show P",
                    "check": {"diagnostic": "failed late"},
                },
                {
                    "attempt": 2, "proof_body": "by simp",
                    "check": {"diagnostic": "failed early"},
                },
            ],
            "residual": "failed early",
            "backend": "whole-proof",
        }
        feedback = compact_round_feedback(node, result, 3)
        request = build_warm_start_request(node, feedback)
        self.assertEqual(
            [item["attempt"] for item in request["proof_candidates"]],
            [1, 2],
        )
        self.assertEqual(
            request["proof_candidates"][0]["diagnostic"], "failed late",
        )
        self.assertTrue(
            request["policy"]["select_deepest_verified_candidate"],
        )

    def test_old_ledger_hydrates_candidate_frontier_from_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node_id = "node:Refine.Demo:helper"
            attempts = root / "nodes/node_Refine.Demo_helper/round-02/attempts.json"
            attempts.parent.mkdir(parents=True)
            attempts.write_text(json.dumps([
                {
                    "attempt": 1, "proof_body": "proof -\n  show P",
                    "check": {"diagnostic": "late"},
                },
                {
                    "attempt": 2, "proof_body": "by simp",
                    "check": {"diagnostic": "early"},
                },
            ]))
            feedback = hydrate_feedback_candidates(
                root, node_id, {"round_id": 2, "last_proof_body": "by simp"},
            )
        self.assertEqual(len(feedback["proof_candidates"]), 2)
        self.assertEqual(feedback["proof_candidates"][0]["diagnostic"], "late")
        self.assertTrue(feedback["candidate_frontier_source"].endswith(
            "round-02/attempts.json"
        ))

    def test_stepwise_warm_start_rejects_stale_or_empty_candidate(self):
        from regen.execution.stepwise_lemma_backend import build_warm_start_request

        node = {"statement": 'lemma demo: "P"'}
        self.assertIsNone(build_warm_start_request(node, None))
        self.assertIsNone(build_warm_start_request(node, {
            "statement": 'lemma demo: "Q"',
            "last_proof_body": "by simp",
        }))
        self.assertIsNone(build_warm_start_request(node, {
            "statement": node["statement"], "last_proof_body": "",
        }))

    def test_decision_index_counts_partial_request_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "graph-revisions" / "decision-002").mkdir(parents=True)
            (root / "llm").mkdir()
            (root / "llm" / "graph-decision-005-from-v2.meta.json").touch()
            self.assertEqual(next_graph_decision_index(root), 6)

    def test_latest_incomplete_graph_request_matches_active_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, revision, complete in (
                (2, 17, False), (4, 18, True), (5, 18, False),
            ):
                decision = root / "graph-revisions" / f"decision-{index:03d}"
                decision.mkdir(parents=True)
                (decision / "request.json").write_text(json.dumps({
                    "current_graph": {"revision": revision},
                    "failed_node": {"node_id": f"node:{index}"},
                }))
                if complete:
                    (decision / "decision.json").write_text("{}")
            request = latest_incomplete_graph_request(root, 18)
        self.assertIsNotNone(request)
        self.assertEqual(request["interrupted_decision_id"], "decision-005")
        self.assertEqual(request["failed_node"]["node_id"], "node:5")

    def test_graph_revision_retries_transport_failure_with_same_decision(self):
        graph = build_graph(package(), proposal(), run_id="r1", revision=1)
        calls = []

        def fake_call(_provider, _prompt, prefix, **_kwargs):
            calls.append(prefix)
            paths = artifact_paths(prefix)
            for name, path in paths.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                if name == "meta":
                    path.write_text(json.dumps({"sealable_generation": True}))
                elif name == "audit":
                    path.write_text("{}")
                else:
                    path.write_text("test")
            if len(calls) == 1:
                raise RuntimeError("provider timeout")
            return json.dumps({
                "decision": "RETRY_PROOF",
                "failure_class": "TOOL_OR_TIMEOUT",
                "reason": "retry after transport recovery",
                "residual_evidence": ["first provider call timed out"],
                "alternatives_considered": ["graph structure is unchanged"],
                "confidence": 0.9,
                "repair_guidance": "continue the existing proof round plan",
            })

        with tempfile.TemporaryDirectory() as directory, patch(
            "regen.execution.graph_revision_agent.call_llm", side_effect=fake_call,
        ):
            root = Path(directory) / "theories" / "Demo_R"
            revised, decision = revise_graph(
                package(), graph, {
                    "status": "red", "node_id": "node:helper",
                    "residual": "provider interrupted", "attempts": [],
                }, set(), output_dir=root, decision_index=7,
                decision_context={"phase": "node-proof"},
            )
            failures = json.loads((
                root / "graph-revisions/decision-007/provider-failures.json"
            ).read_text())
            module_log = json.loads((
                Path(directory) / "module-logs/graph-revision-agent"
                / "Demo_R-decision-007.json"
            ).read_text())
        self.assertEqual(revised, graph)
        self.assertEqual(decision["decision"], "RETRY_PROOF")
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(failures["failures"]), 1)
        self.assertEqual(len(module_log["attempts"]), 2)

    def test_node_round_id_uses_highest_existing_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "round-01").mkdir()
            (root / "round-12").mkdir()
            self.assertEqual(next_node_round_id(root, recorded_round=9), 13)
        self.assertEqual(
            node_log_directory(Path("run"), "node:Refine.KHeap_R:x"),
            Path("run/nodes/node_Refine.KHeap_R_x"),
        )

    def test_active_graph_uses_ledger_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "graph-v2.json"
            active.touch()
            (root / "graph-v9.json").touch()
            self.assertEqual(
                active_graph_path({"graph_source": str(active)}, root),
                active,
            )

    def test_resume_selects_latest_graph_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "graph-v1.json").touch()
            (root / "graph-v4.json").touch()
            (root / "graph-v2-notes.json").touch()
            self.assertEqual(latest_graph_path(root), root / "graph-v4.json")

    def test_prior_certified_graph_uses_latest_green_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for revision, verdict in ((1, "green"), (2, "red"), (3, "green")):
                (root / f"graph-v{revision}.json").write_text(
                    json.dumps({"revision": revision, "nodes": []})
                )
                (root / f"graph-check-v{revision}.json").write_text(
                    json.dumps({"verdict": verdict})
                )
            revision, graph = prior_certified_graph(root, 4)
            self.assertEqual(revision, 3)
            self.assertEqual(graph["revision"], 3)

    def test_statement_delta_graph_keeps_only_changed_declarations(self):
        base = {
            "revision": 1,
            "nodes": [
                {"id": "same", "statement": "lemma same: P", "context": []},
                {"id": "changed", "statement": "lemma changed: P", "context": []},
            ],
            "execution_order": ["same", "changed"],
        }
        graph = {
            "revision": 2,
            "nodes": [
                {"id": "same", "statement": "lemma same: P", "context": []},
                {"id": "changed", "statement": "lemma changed: Q", "context": []},
                {"id": "new", "statement": "lemma new: R", "context": []},
            ],
            "execution_order": ["same", "changed", "new"],
        }
        delta = statement_delta_graph(graph, base)
        self.assertEqual(delta["execution_order"], ["changed", "new"])
        self.assertEqual([node["id"] for node in delta["nodes"]], ["changed", "new"])

    def test_statement_delta_check_uses_prior_green_graph(self):
        class Checker:
            def check_graph(self, graph):
                self.graph = graph
                return {
                    "verdict": "green", "returncode": 0,
                    "diagnostic": "ok", "failed_node_id": None,
                    "wall_ms": 1,
                }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = {
                "revision": 1,
                "nodes": [{"id": "same", "statement": "lemma same: P"}],
                "execution_order": ["same"],
            }
            graph = {
                "revision": 2,
                "nodes": [*base["nodes"], {"id": "new", "statement": "lemma new: Q"}],
                "execution_order": ["same", "new"],
            }
            (root / "graph-v1.json").write_text(json.dumps(base))
            (root / "graph-check-v1.json").write_text(json.dumps({"verdict": "green"}))
            checker = Checker()
            result = check_statement_delta(checker, root, graph)
            self.assertEqual(result["certification_mode"], "delta")
            self.assertEqual(result["checked_node_ids"], ["new"])
            self.assertEqual(checker.graph["execution_order"], ["new"])

    def test_automation_audit_ml_uses_actual_context_and_proof_term(self):
        source = automation_audit_ml("regen_helper")
        self.assertIn("Raw_Simplifier.dest_ss (simpset_of ctxt)", source)
        self.assertIn("WeakestPre.debug_get ctxt", source)
        self.assertIn("Thm_Deps.thm_deps thy targets", source)
        self.assertIn("Thm_Deps.has_skip_proof targets", source)
        self.assertIn("/audit-output/automation-context.tsv", source)

    def test_automation_audit_parsers_are_stable_and_structured(self):
        def enc(value):
            return value.encode("utf-8").hex()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = root / "context.tsv"
            dependencies = root / "dependencies.tsv"
            context.write_text("\t".join([
                "SIMP_RULE", enc("demo"), enc("Lib"), enc("Lib.demo"),
                enc("Lib.demo"), enc("P = Q"),
            ]) + "\n", encoding="utf-8")
            dependencies.write_text("\n".join([
                "\t".join(["THM_DEP", enc("Lib.demo"), enc("0")]),
                "\t".join(["HAS_SKIP_PROOF", enc("false")]),
            ]) + "\n", encoding="utf-8")
            first = parse_automation_snapshot(context, node_id="node:demo")
            second = parse_automation_snapshot(context, node_id="node:other")
            proof = parse_proof_dependencies(dependencies, node_id="node:demo")
        self.assertEqual(first["automation_context_id"], second["automation_context_id"])
        self.assertEqual(first["records"][0]["kind"], "simp_rule")
        self.assertEqual(first["records"][0]["statement"], "P = Q")
        self.assertFalse(proof["has_skip_proof"])
        self.assertEqual(proof["dependencies"], [{
            "canonical_name": "Lib.demo", "fact_index": 0,
        }])

    def test_checker_renders_only_supplied_proofs(self):
        graph = build_graph(package(), {
            "generated_nodes": [],
            "contract_dependencies": {"contract:demo": []},
        }, run_id="r1", revision=1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checker = test_checker(root, root / "checks")
            source = checker.complete_source(graph, {"contract:demo": "by simp"})
            self.assertIn('lemma demo_corres [corres]: "P"\nby simp', source)
            self.assertNotIn("sorry", source)

    def test_node_checker_uses_only_transitive_dependency_proofs(self):
        graph = build_graph(package(), proposal(), run_id="r1", revision=1)
        graph["nodes"].append({
            "id": "node:unrelated", "name": "regen_unrelated",
            "role": "helper", "statement": 'lemma regen_unrelated: "C"',
            "dependencies": [], "strategy": "simp", "context": [],
        })
        graph["execution_order"].insert(0, "node:unrelated")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checker = test_checker(root, root / "checks")
            source = checker._source(
                graph, {
                    "node:helper": "by simp", "node:theorem": "by simp",
                    "node:unrelated": "by simp",
                }, theory_name="Node_Check", target="node:theorem",
            )
        self.assertIn("regen_helper", source)
        self.assertIn("regen_theorem", source)
        self.assertNotIn("regen_unrelated", source)

    def test_statement_shell_uses_oops_not_sorry(self):
        graph = build_graph(package(), {
            "generated_nodes": [],
            "contract_dependencies": {"contract:demo": []},
        }, run_id="r1", revision=1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checker = test_checker(root, root / "checks")
            source = checker._source(
                graph, {}, theory_name="Statement_Shell", statements_only=True,
            )
            self.assertIn("oops", source)
            self.assertNotIn("sorry", source)

    def test_stepwise_target_contains_green_prefix_and_exact_target(self):
        graph = build_graph(package(), proposal(), run_id="r1", revision=1)
        graph["nodes"].append({
            "id": "node:unrelated", "name": "regen_unrelated",
            "role": "helper", "statement": 'lemma regen_unrelated: "C"',
            "dependencies": [], "strategy": "simp", "context": [],
        })
        graph["execution_order"].insert(0, "node:unrelated")
        source = _target_source(
            graph, "node:theorem", {
                "node:helper": "by simp", "node:unrelated": "by simp",
            },
            ["Semantic_Base.Corres"], "Stepwise_Test",
        )
        self.assertIn('lemma regen_helper: "A"\nby simp', source)
        self.assertIn('lemma regen_theorem: "B"\n  oops', source)
        self.assertNotIn("regen_unrelated", source)
        self.assertNotIn("demo_corres", source)

    def test_stepwise_target_preserves_arch_interpretation(self):
        value = package()
        value["contracts"][0]["contract_owned_context"] = [
            "context begin interpretation Arch ."
        ]
        graph = build_graph(value, {
            "generated_nodes": [],
            "contract_dependencies": {"contract:demo": []},
        }, run_id="r1", revision=1)
        source = _target_source(
            graph, "contract:demo", {},
            ["Semantic_Base.Corres"], "Stepwise_Test",
        )
        self.assertIn('lemma demo_corres [corres]: "P"\n  oops', source)
        self.assertIn("context begin interpretation Arch .", source)
        self.assertIn("\nend\n\nend\n", source)

    def test_theory_seal_binds_green_build_source_and_impact(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            theory_dir = run_dir / "theories/Demo_R"
            theory_dir.mkdir(parents=True)
            graph = build_graph(package(), proposal(), run_id="generation:demo", revision=1)
            (theory_dir / "graph-v1.json").write_text(json.dumps(graph))
            for name, value in (
                ("complete-check.json", {"verdict": "green"}),
                ("session-build.json", {"verdict": "green"}),
                ("retained-semantic-impact.json", {"verdict": "pass"}),
            ):
                (theory_dir / name).write_text(json.dumps(value))
            generated = run_dir / "generated/Demo_R.thy"
            generated.parent.mkdir()
            generated.write_text("theory Demo_R imports Main begin end\n")
            run = {
                "run_id": "run-test",
                "session_schedule": {
                    "campaign_id": "campaign",
                    "environment_gate_id": "environment:one",
                },
            }
            entry = {"file_slot": "Demo_R.thy"}
            seal = create_theory_seal(
                run_dir, run, entry, "generation:demo", graph, generated,
                theory_dir,
            )
            self.assertEqual(seal["verdict"], "pass")
            self.assertEqual(seal["generation_id"], "generation:demo")
            self.assertEqual(
                create_theory_seal(
                    run_dir, run, entry, "generation:demo", graph, generated,
                    theory_dir,
                ),
                seal,
            )
            generated.write_text("changed")
            with self.assertRaisesRegex(ValueError, "differs"):
                create_theory_seal(
                    run_dir, run, entry, "generation:demo", graph, generated,
                    theory_dir,
                )

    def test_update_file_clears_stale_failure_metadata_when_sealed(self):
        files = {
            "revision": 0,
            "session_status": "running",
            "files": [{
                "theory": "Refine.Demo_R", "status": "failed",
                "generation_ids": ["generation:demo"], "seal_id": None,
                "failed_node": "node:old", "reason": "old failure",
            }],
            "events": [],
        }
        update_file(
            files, "Refine.Demo_R", "sealed",
            generation_id="generation:demo", seal_id="theory-seal:demo",
            generated_source="Demo_R.thy",
        )
        entry = files["files"][0]
        self.assertEqual(entry["status"], "sealed")
        self.assertEqual(entry["seal_id"], "theory-seal:demo")
        self.assertNotIn("failed_node", entry)
        self.assertNotIn("reason", entry)

    def test_extracts_last_complete_theory_marker_for_stabilization(self):
        output = (
            "startup\n"
            "REGEN_NODE node:helper\n"
            "progress\n"
            "REGEN_NODE contract:demo\n"
            "Isabelle checker timed out after 1800 seconds.\n"
        )
        self.assertEqual(
            extract_last_node_marker(output), ("contract:demo", 2),
        )

    def test_assembly_stabilization_targets_only_the_marked_proof_body(self):
        graph = build_graph(package(), proposal(), run_id="r1", revision=1)
        proofs = {node_id: "by simp" for node_id in graph["execution_order"]}
        check = {
            "verdict": "red", "returncode": 124,
            "last_diagnostic_marker": "node:theorem",
            "diagnostic_marker_count": 2,
            "diagnostic": "timed out", "log": "/tmp/complete.log",
        }
        request = prepare_assembly_stabilization(graph, proofs, check)
        self.assertEqual(request["status"], "targeted")
        self.assertEqual(request["failure_class"], "ASSEMBLY_TIMEOUT")
        self.assertEqual(request["allowed_change"], "proof_body_only")
        self.assertEqual(request["target"]["node_id"], "node:theorem")
        self.assertEqual(request["target"]["proof_body"], "by simp")
        self.assertIn("statement", request["forbidden_changes"])
        self.assertEqual(request["target"]["dependencies"], ["node:helper"])
        self.assertNotIn("dependency_proof_bodies", request)

    def test_complete_check_markers_are_diagnostic_only(self):
        graph = build_graph(package(), proposal(), run_id="r1", revision=1)
        proofs = {node_id: "by simp" for node_id in graph["execution_order"]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checker = test_checker(root, root / "checks")
            diagnostic = checker._source(
                graph, proofs, theory_name="Diagnostic", diagnostic_markers=True,
            )
            generated = checker.complete_source(graph, proofs)
        self.assertIn("REGEN_NODE", diagnostic)
        self.assertNotIn("REGEN_NODE", generated)


if __name__ == "__main__":
    unittest.main()
