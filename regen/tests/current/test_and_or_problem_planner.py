import hashlib
import json
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

import regen.planning.and_or_problem_planner as planner_module
from regen.planning.and_or_problem_planner import (
    PREFLIGHT_RECEIPT_SCHEMA,
    ROUTE_COSTS,
    TRANSCRIPT_RECEIPT_SCHEMA,
    TargetPlanningExhausted,
    build_contract_and_or_problem,
    canonical_sha256,
    make_preflight_receipt,
    planning_recipe,
    plan_target_problem,
    root_reachable_node_ids,
    run_staging_native_preflight,
    target_package_view,
    verify_contract_and_or_problem,
)
from regen.planning.contract_and_or import best_plan, initial_state, next_decision
from regen.planning.file_planner import planner_prompt
from regen.planning.proof_graph import (
    execution_order,
    graph_payload_sha256,
    validate_graph,
)


TARGET = "contract:Target:target:0001"
OTHER = "contract:Target:other:0002"
HELPER = "node:Target:helper"
THEOREM = "node:Target:bridge"
UNRELATED = "node:Target:unrelated"


class AndOrProblemPlannerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.proof_arm = self.root / "proof-arm-B"
        self.treatment = self.proof_arm / "treatment-planning"
        self.staging = self.root / "native-staging"
        self.proof_arm.mkdir()
        self.staging.mkdir()
        body = {
            "theory": "Target",
            "file_slot": "Target.thy",
            "contracts": [
                {
                    "contract_id": TARGET,
                    "name": "target",
                    "exact_statement": 'lemma target: "P x"',
                    "contract_owned_context": [],
                    "contract_semantic_sha256": "1" * 64,
                },
                {
                    "contract_id": OTHER,
                    "name": "other",
                    "exact_statement": 'lemma other: "Q x"',
                    "contract_owned_context": [],
                    "contract_semantic_sha256": "2" * 64,
                },
            ],
            "support_contracts": [],
        }
        self.package = {
            **body, "package_payload_sha256": canonical_sha256(body),
        }
        self.checkpoint_sha256 = "c" * 64
        self.recipe = planning_recipe(
            self.package, target_contract_id=TARGET,
            checkpoint_sha256=self.checkpoint_sha256,
        )
        self.graph = self.make_graph()

    def make_graph(
        self, *, revision=1, run_id="run", prompt_sha="a" * 64,
        prefix=None,
    ):
        prefix = prefix or str(self.treatment / "llm/audited-file-planner-test")
        nodes = [
            {
                "id": TARGET, "name": "target", "role": "contract",
                "statement": 'lemma target: "P x"', "context": [],
                "dependencies": [THEOREM], "consumers": [],
                "strategy": "close P after the generated bridge",
            },
            {
                "id": HELPER, "name": "and_or_helper", "role": "helper",
                "statement": 'lemma and_or_helper: "R x"', "context": [],
                "dependencies": [], "consumers": [THEOREM],
                "strategy": "establish R directly",
            },
            {
                "id": THEOREM, "name": "and_or_bridge", "role": "theorem",
                "statement": 'lemma and_or_bridge: "S x"', "context": [],
                "dependencies": [HELPER], "consumers": [TARGET],
                "strategy": "derive S from R",
            },
        ]
        graph = {
            "schema_version": 1,
            "run_id": run_id,
            "theory": "Target",
            "file_slot": "Target.thy",
            "revision": revision,
            "nodes": nodes,
            "contract_routes": {
                TARGET: {
                    "proof_shape": "generated",
                    "semantic_steps": ["R", "S", "P"],
                },
            },
            "planner_context": {
                "policy": "fixed-contracts-only-v1",
                "external_premise_records_visible": False,
                "retrieval_queries": 0,
                "retrieval_enabled": False,
                "retrieved_record_ids": [],
            },
            "upstream_bindings": {"fixture": "fixed"},
            "planner_call": {
                "provider": "codex",
                "model": "gpt-6-astra",
                "transcript_prefix": prefix,
                "prompt_sha256": prompt_sha,
                "final_sha256": "b" * 64,
            },
        }
        validate_graph(graph, target_package_view(self.package, TARGET))
        graph["execution_order"] = execution_order(graph)
        graph["graph_payload_sha256"] = graph_payload_sha256(graph)
        return graph

    def transcript_receipt(self, graph=None, *, prompt_sha=None):
        graph = graph or self.graph
        call = graph["planner_call"]
        values = {
            name: (
                prompt_sha or call["prompt_sha256"] if name == "prompt"
                else call["final_sha256"] if name == "final"
                else hashlib.sha256(name.encode()).hexdigest()
            )
            for name in ("prompt", "raw", "final", "stderr", "meta", "audit")
        }
        receipt = {
            "schema": TRANSCRIPT_RECEIPT_SCHEMA,
            "validated_real": True,
            "no_tool_events": True,
            "sealable_generation": True,
            "stage": "file-planner",
            "provider": "codex-exec-audited",
            "model": "gpt-6-astra",
            "reasoning_effort": "medium",
            "theory": "Target",
            "transcript_prefix": call["transcript_prefix"],
            "wall_ms": 25,
            "artifacts": {
                name: {
                    "path": str(self.treatment / f"llm/mock.{name}"),
                    "sha256": sha, "bytes": 1,
                }
                for name, sha in values.items()
            },
        }
        receipt["receipt_sha256"] = canonical_sha256(receipt)
        return receipt

    def preflight_receipt(
        self, checked=None, *, verdict="pass", attempt=1, graph=None,
    ):
        graph = graph or self.graph
        artifact = self.staging / f"preflight-{attempt}.json"
        cleanup = self.staging / f"cleanup-{attempt}.json"
        artifact.write_text(json.dumps({
            "verdict": verdict, "attempt": attempt,
        }), encoding="utf-8")
        cleanup.write_text(json.dumps({
            "released": True, "attempt": attempt,
        }), encoding="utf-8")
        return make_preflight_receipt(
            verdict=verdict,
            checked_node_ids=(
                checked if checked is not None
                else root_reachable_node_ids(self.graph, self.package, TARGET)
            ),
            diagnostics=[] if verdict == "pass" else ["generated statement rejected"],
            artifact_path=artifact,
            cleanup_artifact_path=cleanup,
            graph=graph, package=target_package_view(self.package, TARGET),
            target_contract_id=TARGET,
            model="gpt-6-astra", reasoning_effort="medium",
            staging_run=self.staging, proof_arm_run=self.proof_arm,
            started_at="2026-10-08T00:00:00+00:00",
            finished_at="2026-10-08T00:00:01+00:00",
            wall_ms=1000, native_calls=1,
            resources_released_at="2026-10-08T00:00:01+00:00",
        )

    def build(self, graph=None, receipt=None):
        graph = graph or self.graph
        transcript = self.transcript_receipt(graph)
        return build_contract_and_or_problem(
            graph, self.package, target_contract_id=TARGET,
            model="gpt-6-astra", reasoning_effort="medium",
            proof_arm_run=self.proof_arm, staging_run=self.staging,
            planning_recipe_artifact=self.recipe,
            native_preflight_receipt=receipt or self.preflight_receipt(graph=graph),
            transcript_validator=lambda *args, **kwargs: deepcopy(transcript),
        )

    def test_exact_root_closure_roles_and_public_fields_are_preserved(self):
        problem = self.build()
        self.assertEqual(
            [goal["id"] for goal in problem["goals"]],
            [HELPER, THEOREM, TARGET],
        )
        self.assertNotIn(UNRELATED, {goal["id"] for goal in problem["goals"]})
        by_id = {goal["id"]: goal for goal in problem["goals"]}
        self.assertEqual(by_id[THEOREM]["role"], "theorem")
        for node_id in (HELPER, THEOREM, TARGET):
            source = next(node for node in self.graph["nodes"] if node["id"] == node_id)
            for field in ("id", "name", "role", "statement", "context", "strategy"):
                self.assertEqual(by_id[node_id][field], source[field])
        verify_contract_and_or_problem(
            problem, self.graph, self.package, target_contract_id=TARGET,
        )
        self.assertNotIn(str(self.staging), json.dumps(problem))
        self.assertEqual(
            problem["execution_binding_policy"][
                "proof_arm_native_calls_during_planning"], 0,
        )

    def test_every_goal_has_direct_route_and_dependencies_add_exact_decomposition(self):
        problem = self.build()
        by_id = {goal["id"]: goal for goal in problem["goals"]}
        self.assertEqual(len(by_id[HELPER]["routes"]), 1)
        self.assertEqual(len(by_id[THEOREM]["routes"]), 2)
        self.assertEqual(len(by_id[TARGET]["routes"]), 2)
        self.assertEqual(by_id[THEOREM]["routes"][1]["requires"], [HELPER])
        self.assertEqual(by_id[TARGET]["routes"][1]["requires"], [THEOREM])
        for goal in problem["goals"]:
            for route in goal["routes"]:
                kind = route["id"].rsplit(":", 1)[-1]
                for field, value in ROUTE_COSTS[kind].items():
                    self.assertEqual(route[field], value)
                self.assertEqual(route["probe_policy"], "none")
                self.assertEqual(route["attempt_limit"], 1)

    def test_dependencies_never_certify_root(self):
        problem = self.build()
        root_direct = f"route:{TARGET}:direct"
        state = initial_state(
            problem, certified=[HELPER, THEOREM],
            route_attempts={root_direct: 1},
        )
        plan = best_plan(problem, state)
        self.assertIn(TARGET, plan["required_goals"])
        self.assertEqual(plan["selected_routes"][TARGET], f"route:{TARGET}:decomposed")
        decision = next_decision(problem, state)
        self.assertEqual(decision["action"], "PROVE")
        self.assertEqual(decision["goal_id"], TARGET)

    def test_proof_material_and_cross_contract_dependencies_fail_closed(self):
        graph = deepcopy(self.graph)
        graph["nodes"][0]["proof_body"] = "by simp"
        graph["graph_payload_sha256"] = graph_payload_sha256(graph)
        with self.assertRaisesRegex(ValueError, "unsupported File Planner node fields"):
            self.build(graph)

        graph = deepcopy(self.graph)
        target = next(node for node in graph["nodes"] if node["id"] == TARGET)
        bridge = next(node for node in graph["nodes"] if node["id"] == THEOREM)
        target["dependencies"] = [OTHER]
        bridge["consumers"] = []
        graph["nodes"].append({
            "id": OTHER, "name": "other", "role": "contract",
            "statement": 'lemma other: "Q x"', "context": [],
            "dependencies": [], "consumers": [TARGET],
            "strategy": "illegal second contract",
        })
        with self.assertRaises(ValueError):
            validate_graph(graph, target_package_view(self.package, TARGET))

    def test_preflight_and_transcript_receipts_are_exact_and_hash_bound(self):
        incomplete = self.preflight_receipt()
        incomplete["checked_node_ids"] = [HELPER, THEOREM]
        incomplete["receipt_sha256"] = canonical_sha256({
            key: value for key, value in incomplete.items()
            if key != "receipt_sha256"
        })
        with self.assertRaisesRegex(ValueError, "exact root closure"):
            self.build(receipt=incomplete)

        transcript = self.transcript_receipt()
        transcript["artifacts"]["raw"]["sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "receipt hash mismatch"):
            build_contract_and_or_problem(
                self.graph, self.package, target_contract_id=TARGET,
                model="gpt-6-astra", reasoning_effort="medium",
                proof_arm_run=self.proof_arm, staging_run=self.staging,
                planning_recipe_artifact=self.recipe,
                native_preflight_receipt=self.preflight_receipt(attempt=2),
                transcript_validator=lambda *args, **kwargs: transcript,
            )

    def test_problem_tampering_breaks_canonical_hash(self):
        problem = self.build()
        problem["goals"][1]["role"] = "helper"
        with self.assertRaisesRegex(ValueError, "payload hash mismatch"):
            verify_contract_and_or_problem(
                problem, self.graph, self.package, target_contract_id=TARGET,
            )

    def test_wrapper_passes_target_view_retries_native_rejection_and_exposes_costs(self):
        seen_packages = []
        seen_environments = []
        seen_feedback = []
        transcripts = {}

        def fake_plan(package, **kwargs):
            seen_packages.append(deepcopy(package))
            seen_environments.append(deepcopy(kwargs["environment"]))
            seen_feedback.append(list(kwargs["rejection_feedback"]))
            effective = deepcopy(kwargs["environment"])
            effective["retrieval_enabled"] = False
            prompt_sha = hashlib.sha256(planner_prompt(
                package, effective, kwargs["rejection_feedback"],
            ).encode()).hexdigest()
            graph = self.make_graph(
                revision=kwargs["revision"], run_id=kwargs["run_id"],
                prompt_sha=prompt_sha,
                prefix=str(self.treatment / f"llm/mock-planner-{kwargs['call_index']}"),
            )
            transcripts[graph["graph_payload_sha256"]] = self.transcript_receipt(graph)
            return graph

        def fake_transcript(graph, **kwargs):
            return deepcopy(transcripts[graph["graph_payload_sha256"]])

        def fake_preflight(**kwargs):
            attempt = kwargs["attempt"]
            kwargs["output_dir"].mkdir(parents=True, exist_ok=True)
            artifact = kwargs["output_dir"] / f"wrapper-preflight-{attempt}.json"
            cleanup = kwargs["output_dir"] / f"wrapper-cleanup-{attempt}.json"
            artifact.write_text(json.dumps({"attempt": attempt}))
            cleanup.write_text(json.dumps({"released": True}))
            return make_preflight_receipt(
                verdict="reject" if attempt == 1 else "pass",
                checked_node_ids=kwargs["required_node_ids"],
                diagnostics=["repair exact generated statement"] if attempt == 1 else [],
                artifact_path=artifact,
                cleanup_artifact_path=cleanup,
                graph=kwargs["graph"], package=kwargs["package"],
                target_contract_id=TARGET,
                model="gpt-6-astra", reasoning_effort="medium",
                staging_run=kwargs["staging_run"],
                proof_arm_run=kwargs["proof_arm_run"],
                started_at="2026-10-08T00:00:00+00:00",
                finished_at="2026-10-08T00:00:02+00:00",
                wall_ms=2000, native_calls=1,
                resources_released_at="2026-10-08T00:00:02+00:00",
            )

        accounted = []
        result = plan_target_problem(
            self.package, target_contract_id=TARGET, run_id="ab-run",
            output_dir=self.treatment, proof_arm_run=self.proof_arm,
            staging_run=self.staging, native_preflight=fake_preflight,
            checkpoint_sha256=self.checkpoint_sha256,
            frozen_plan_recipe_sha256=planning_recipe(
                self.package, target_contract_id=TARGET,
                checkpoint_sha256=self.checkpoint_sha256,
                max_attempts=2,
            )["recipe_sha256"],
            max_attempts=2, plan_file_fn=fake_plan,
            transcript_validator=fake_transcript,
            accounting_sink=accounted.append,
        )
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(len(seen_packages), 2)
        expected_view = target_package_view(self.package, TARGET)
        self.assertTrue(all(item == expected_view for item in seen_packages))
        self.assertTrue(all(
            item["and_or_target_focus"]["target_contract_id"] == TARGET
            for item in seen_environments
        ))
        accounting = result["planning_accounting"]
        self.assertEqual(accounting["model_calls"], 2)
        self.assertEqual(accounting["native_preflight_calls"], 2)
        self.assertEqual(accounting["native_preflight_wall_ms"], 4000)
        self.assertEqual(accounting["proof_arm_native_calls_during_planning"], 0)
        self.assertTrue(accounting["staging_cleanup_verified"])
        self.assertEqual(len(accounting["token_accounting"]["raw_artifact_paths"]), 2)
        self.assertEqual([row["status"] for row in accounted], [
            "native-preflight-rejected", "accepted",
        ])
        self.assertEqual(seen_feedback, [[], ["repair exact generated statement"]])
        self.assertEqual(result["problem"]["provenance"]["model"], "gpt-6-astra")

    def test_wrapper_fails_closed_after_bounded_rejections(self):
        transcripts = {}

        def fake_plan(package, **kwargs):
            effective = deepcopy(kwargs["environment"])
            effective["retrieval_enabled"] = False
            prompt_sha = hashlib.sha256(planner_prompt(
                package, effective, kwargs["rejection_feedback"],
            ).encode()).hexdigest()
            graph = self.make_graph(
                revision=kwargs["revision"], prompt_sha=prompt_sha,
                prefix=str(self.treatment / f"llm/exhausted-{kwargs['call_index']}"),
            )
            transcripts[graph["graph_payload_sha256"]] = self.transcript_receipt(graph)
            return graph

        def reject(**kwargs):
            kwargs["output_dir"].mkdir(parents=True, exist_ok=True)
            artifact = kwargs["output_dir"] / f"reject-{kwargs['attempt']}.json"
            cleanup = kwargs["output_dir"] / f"cleanup-{kwargs['attempt']}.json"
            artifact.write_text("{}")
            cleanup.write_text('{"released":true}')
            return make_preflight_receipt(
                verdict="reject", checked_node_ids=kwargs["required_node_ids"],
                diagnostics=["still invalid"], artifact_path=artifact,
                cleanup_artifact_path=cleanup,
                graph=kwargs["graph"], package=kwargs["package"],
                target_contract_id=TARGET,
                model="gpt-6-astra", reasoning_effort="medium",
                staging_run=kwargs["staging_run"],
                proof_arm_run=kwargs["proof_arm_run"],
                started_at="2026-10-08T00:00:00+00:00",
                finished_at="2026-10-08T00:00:01+00:00",
                wall_ms=1000, native_calls=1,
                resources_released_at="2026-10-08T00:00:01+00:00",
            )

        result = plan_target_problem(
            self.package, target_contract_id=TARGET, run_id="ab-run",
            output_dir=self.treatment, proof_arm_run=self.proof_arm,
            staging_run=self.staging, native_preflight=reject,
            checkpoint_sha256=self.checkpoint_sha256,
            frozen_plan_recipe_sha256=planning_recipe(
                self.package, target_contract_id=TARGET,
                checkpoint_sha256=self.checkpoint_sha256,
                max_attempts=2,
            )["recipe_sha256"],
            max_attempts=2, plan_file_fn=fake_plan,
            transcript_validator=lambda graph, **kwargs: deepcopy(
                transcripts[graph["graph_payload_sha256"]]
            ),
        )
        self.assertEqual(result["status"], "treatment_planning_failure")
        self.assertEqual(result["failure_type"], "bounded_attempts_exhausted")
        attempts = result["planning_accounting"]["attempt_receipts"]
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(
            item["status"] == "native-preflight-rejected"
            for item in attempts
        ))

    def test_production_staging_adapter_runs_setup_check_and_close(self):
        events = []

        class Checker:
            def check_graph(inner_self, graph):
                events.append(("check_graph", graph["graph_payload_sha256"]))
                return {
                    "verdict": "green",
                    "diagnostic": "",
                    "wall_ms": 17,
                    "proofs_checked": False,
                }

            def close_prefix(inner_self):
                events.append(("close_prefix", None))

        checker = Checker()

        def setup(run_dir, theory, timeout):
            events.append(("setup", (run_dir, theory, timeout)))
            return (
                {"fixture": "run"}, deepcopy(self.package),
                self.staging / "theory", None, checker, [],
            )

        package_view = target_package_view(self.package, TARGET)
        closure = root_reachable_node_ids(
            self.graph, package_view, TARGET,
        )
        problem_input = planner_module._preflight_input_binding(
            self.graph, package_view, target_contract_id=TARGET,
            required_node_ids=closure, model="gpt-6-astra",
            reasoning_effort="medium",
        )
        receipt = run_staging_native_preflight(
            graph=deepcopy(self.graph), package=package_view,
            authoritative_package=deepcopy(self.package),
            target_contract_id=TARGET, required_node_ids=closure,
            attempt=1, output_dir=self.staging / "adapter-evidence",
            staging_run=self.staging, proof_arm_run=self.proof_arm,
            problem_input=problem_input, timeout=77, setup_fn=setup,
        )
        self.assertEqual([event[0] for event in events], [
            "setup", "check_graph", "close_prefix",
        ])
        self.assertEqual(events[0][1], (self.staging.resolve(), "Target", 77))
        self.assertEqual(receipt["verdict"], "pass")
        self.assertEqual(receipt["native_calls"], 1)
        self.assertTrue(receipt["cleanup_verified"])
        self.assertEqual(receipt["proof_arm_native_calls"], 0)
        for field in ("native_evidence", "cleanup_evidence"):
            Path(receipt[field]["path"]).resolve().relative_to(
                self.staging.resolve()
            )

    def test_orphan_partial_call_restart_fails_closed_without_replay(self):
        prefix = self.treatment / "llm" / "orphan-call"
        prefix.parent.mkdir(parents=True)
        Path(str(prefix) + ".prompt.txt").write_text(
            "unfinished planner request", encoding="utf-8",
        )
        Path(str(prefix) + ".meta.json").write_text(json.dumps({
            "state": "running", "provider": "codex-exec-audited",
            "model": "gpt-6-astra", "reasoning_effort": "medium",
            "stage": "file-planner", "wall_ms": 321,
        }), encoding="utf-8")
        calls = []

        def forbidden_plan(*args, **kwargs):
            calls.append("plan")
            raise AssertionError("an orphaned request must not be replayed")

        def forbidden_native(**kwargs):
            calls.append("native")
            raise AssertionError("native must not run after interrupted planning")

        result = plan_target_problem(
            self.package, target_contract_id=TARGET, run_id="restart-run",
            output_dir=self.treatment, proof_arm_run=self.proof_arm,
            staging_run=self.staging, native_preflight=forbidden_native,
            checkpoint_sha256=self.checkpoint_sha256,
            frozen_plan_recipe_sha256=self.recipe["recipe_sha256"],
            plan_file_fn=forbidden_plan,
        )
        self.assertEqual(calls, [])
        self.assertEqual(result["status"], "infrastructure_failure")
        self.assertEqual(result["failure_type"], "interrupted_planning")
        accounting = result["planning_accounting"]
        self.assertEqual(accounting["model_calls"], 1)
        self.assertEqual(accounting["planner_provider_wall_ms"], 321)
        receipts = accounting["attempt_receipts"]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["attempt"], 1)
        self.assertEqual(receipts[0]["status"], "interrupted-planning")
        inventory = receipts[0]["provider_call_inventory"]
        self.assertEqual(len(inventory), 1)
        self.assertFalse(inventory[0]["complete_family"])
        self.assertEqual(
            inventory[0]["transcript_prefix"], str(prefix.resolve()),
        )

        replay = plan_target_problem(
            self.package, target_contract_id=TARGET, run_id="restart-run",
            output_dir=self.treatment, proof_arm_run=self.proof_arm,
            staging_run=self.staging, native_preflight=forbidden_native,
            checkpoint_sha256=self.checkpoint_sha256,
            frozen_plan_recipe_sha256=self.recipe["recipe_sha256"],
            plan_file_fn=forbidden_plan,
        )
        self.assertEqual(calls, [])
        self.assertEqual(replay, result)


if __name__ == "__main__":
    unittest.main()
