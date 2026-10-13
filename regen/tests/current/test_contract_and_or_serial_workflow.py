import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from regen.execution.proof_commits import digest, durable
from regen.planning.online_policy_composition import AND_OR_SEARCH, EXISTING_SEARCH
from regen.tools import contract_and_or_ab as ab
from regen.tools.contract_and_or_serial_workflow import (
    WorkflowWAL,
    build_parallel_workspace_command,
    run_serial_workflow,
    validate_execution_plan,
    workflow_status,
)


class SyntheticWorkflow:
    def __init__(self, root: Path, *, gate_valid=True, fail_once_sequence=None):
        self.root = root
        self.instances = root / "instances"
        self.results = root / "results"
        self.control = root / "control"
        self.repository = root / "repository"
        self.repository.mkdir()
        self.plan_path = root / "plan.json"
        self.calls = []
        self.execute_counts = Counter()
        self.gate_valid = gate_valid
        self.fail_once_sequence = fail_once_sequence
        self.failed = False
        self.plan = self.make_plan()
        durable(self.plan_path, self.plan, immutable=True)

    def make_plan(self):
        sha = lambda value: digest({"value": value})
        manifest = self.root / "application-manifest.json"
        durable(manifest, {"synthetic": True}, immutable=True)
        selected = []
        arms = []
        sequence = 0
        for index in range(8):
            case_id = f"case-{index + 1}"
            fixture = self.root / "fixtures" / case_id
            prepared_sha = sha("prepared-" + case_id)
            recipe_sha = sha("recipe-" + case_id)
            case = {
                "case_id": case_id,
                "stratum": "ordinary" if index < 6 else "long",
                "step_sequence": index + 1,
                "theory": f"Theory{index + 1}",
                "target_contract_id": f"contract:Theory{index + 1}:target:0001",
                "checkpoint": {
                    "prepared_fixture": str(fixture),
                    "prepared_manifest": {
                        "path": str(fixture / "manifest.json"),
                        "sha256": prepared_sha,
                    },
                    "pristine_provenance_sha256": sha("pristine-" + case_id),
                    "source_kind": "target-pristine-existing-v1",
                },
                "package": {
                    "path": str(self.root / f"{case_id}-package.json"),
                    "sha256": sha("package-" + case_id),
                },
                "problem_generation_recipe": {
                    "schema": "contract-rooted-and-or-planning-recipe-v1",
                    "recipe_sha256": recipe_sha,
                },
            }
            selected.append(case)
            order = (
                [EXISTING_SEARCH, AND_OR_SEARCH]
                if index % 2 == 0 else [AND_OR_SEARCH, EXISTING_SEARCH]
            )
            for within_pair, policy in enumerate(order, 1):
                sequence += 1
                suffix = "a" if policy == EXISTING_SEARCH else "b"
                arm_id = f"{case_id}--pair1--{suffix}"
                run_dir = self.instances / arm_id
                arms.append({
                    "arm_id": arm_id,
                    "arm_instance_id": sha("instance-" + arm_id),
                    "arm_instance_path": str(run_dir),
                    "planning_staging_path": str(
                        self.instances / f"{arm_id}--planning-staging"
                    ),
                    "application_manifest_sha256": self.file_sha(manifest),
                    "checkpoint_sha256": prepared_sha,
                    "prepared_manifest_sha256": prepared_sha,
                    "pristine_provenance_sha256": case[
                        "checkpoint"]["pristine_provenance_sha256"],
                    "prepared_fixture_path": str(fixture),
                    "problem_generation_recipe_sha256": recipe_sha,
                    "case_id": case_id,
                    "stratum": case["stratum"],
                    "pair": 1,
                    "sequence": sequence,
                    "within_pair": within_pair,
                    "search_policy": policy,
                    "input_binding_sha256": sha("binding-" + case_id),
                })
        body = {
            "schema": ab.PLAN_SCHEMA,
            "common": {
                "model": "gpt-6-astra",
                "reasoning_effort": "medium",
                "acceptance_policy": ab.ACCEPTANCE_POLICY,
                "problem_generation": {
                    "provider": "codex", "timeout_seconds": 1800,
                    "max_attempts": 3,
                },
                "resources": {
                    "native_slots": 1, "native_threads": 4,
                    "native_memory_mb": 16384,
                    "min_free_disk_bytes": 123,
                    "min_available_memory_bytes": 456,
                },
                "limits": {
                    "effective_proving_seconds": 3600,
                    "wall_seconds": 10800,
                },
                "application_manifest": {
                    "path": str(manifest), "sha256": self.file_sha(manifest),
                },
            },
            "selected_cases": selected,
            "execution_case_order": [
                {"case_id": case["case_id"], "stratum": case["stratum"],
                 "step_sequence": case["step_sequence"]}
                for case in selected
            ],
            "safety_gate": {"case_id": selected[0]["case_id"],
                            "step_sequence": 1},
            "arms": arms,
        }
        return {**body, "plan_id": digest(body)}

    @staticmethod
    def file_sha(path):
        import hashlib
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def write(self, path, value):
        path = Path(path)
        durable(path, value, immutable=True)
        return path

    def load_plan(self, path):
        self.calls.append(("load_plan", None))
        return deepcopy(self.plan)

    def verify_application(self, **kwargs):
        arm = kwargs.get("arm")
        self.calls.append(("verify_application", arm and arm["arm_id"]))
        return {"verified": True}

    def admit(self, **kwargs):
        arm = kwargs["arm"]
        self.calls.append(("admit", arm["arm_id"]))
        resources = kwargs["plan"]["common"]["resources"]
        self.assert_equal(resources["min_free_disk_bytes"], 123)
        self.assert_equal(resources["min_available_memory_bytes"], 456)
        return {"admitted": True, "resources": deepcopy(resources)}

    @staticmethod
    def assert_equal(left, right):
        if left != right:
            raise AssertionError((left, right))

    def derive(self, **kwargs):
        arm, destination, role = (
            kwargs["arm"], Path(kwargs["destination"]), kwargs["role"]
        )
        self.calls.append(("derive:" + role, arm["arm_id"]))
        destination.mkdir(parents=True)
        value = {
            "schema": "prepared-checkpoint-arm-v1",
            "fixture": arm["prepared_fixture_path"],
            "fixture_manifest_sha256": arm["prepared_manifest_sha256"],
            "instance_id": digest({"destination": str(destination)}),
        }
        self.write(destination / "arm-instance.json", value)
        return value

    def verify_derived(self, **kwargs):
        arm, destination, role = (
            kwargs["arm"], Path(kwargs["destination"]), kwargs["role"]
        )
        value = json.loads((destination / "arm-instance.json").read_text())
        self.assert_equal(value["fixture"], arm["prepared_fixture_path"])
        self.assert_equal(
            value["fixture_manifest_sha256"], arm["prepared_manifest_sha256"],
        )
        self.calls.append(("verify:" + role, arm["arm_id"]))
        return value

    def plan_b(self, **kwargs):
        arm, run_dir, staging = (
            kwargs["arm"], Path(kwargs["run_dir"]), Path(kwargs["staging_dir"])
        )
        self.calls.append(("plan_b", arm["arm_id"]))
        if arm["search_policy"] != AND_OR_SEARCH or not staging.is_dir():
            raise AssertionError("B planning did not use its fresh staging run")
        value = {
            "status": "accepted",
            "result_sha256": digest({"planning": arm["arm_id"]}),
            "realized_problem_sha256": digest({"problem": arm["arm_id"]}),
            "problem": {"root_goal": kwargs["case"]["target_contract_id"]},
        }
        self.write(
            run_dir / "treatment-planning" / "and-or-planning-result.json",
            value,
        )
        return value

    def configure_arm(self, **kwargs):
        arm, run_dir = kwargs["arm"], Path(kwargs["run_dir"])
        self.calls.append(("configure", arm["arm_id"]))
        planning = kwargs["planning_result"]
        if arm["search_policy"] == EXISTING_SEARCH and planning is not None:
            raise AssertionError("A consumed a B-only realized problem")
        if arm["search_policy"] == AND_OR_SEARCH and planning["status"] != "accepted":
            raise AssertionError("B lacks accepted runtime planning")
        attestation = self.write(run_dir / "and-or-arm-attestation.json", {
            "arm_id": arm["arm_id"],
            "problem_generation_recipe_sha256": arm[
                "problem_generation_recipe_sha256"],
            "realized_problem_sha256": (
                planning["realized_problem_sha256"] if planning else None
            ),
        })
        experiment = self.write(run_dir / "experiment.json", {
            "service": "parallel-workspace",
            "budget": kwargs["plan"]["common"]["limits"],
        })
        return {"attestation": str(attestation), "experiment": str(experiment)}

    def execute_arm(self, **kwargs):
        arm = kwargs["arm"]
        self.calls.append(("execute", arm["arm_id"]))
        self.execute_counts[arm["arm_id"]] += 1
        if (
            self.fail_once_sequence == arm["sequence"]
            and not self.failed
        ):
            self.failed = True
            raise RuntimeError("synthetic interruption")
        return {
            "outcome_class": "target_success",
            "effective_budget_seconds": kwargs["plan"]["common"]["limits"][
                "effective_proving_seconds"],
            "service": "parallel-workspace",
        }

    def finalize_arm(self, **kwargs):
        arm = kwargs["arm"]
        self.calls.append(("finalize", arm["arm_id"]))
        path = Path(kwargs["results_dir"]) / f"{arm['arm_id']}.json"
        value = {
            "schema": ab.RESULT_SCHEMA, "arm_id": arm["arm_id"],
            "search_policy": arm["search_policy"], "synthetic": True,
        }
        self.write(path, value)
        return {"path": str(path), "sha256": self.file_sha(path)}

    def cleanup_arm(self, **kwargs):
        arm = kwargs["arm"]
        self.calls.append(("cleanup", arm["arm_id"]))
        return {
            "verified": True, "service_stopped": True,
            "native_resources_released": True,
            "owned_heaps": "synthetic-none",
        }

    def aggregate(self, **kwargs):
        output = Path(kwargs["output"])
        self.calls.append(("aggregate", output.name))
        available = {
            path.stem for path in self.results.glob("*.json")
        }
        pairs = []
        for case in self.plan["selected_cases"]:
            pair = [
                arm for arm in self.plan["arms"]
                if arm["case_id"] == case["case_id"] and arm["pair"] == 1
            ]
            complete = all(arm["arm_id"] in available for arm in pair)
            primary = complete
            if case["case_id"] == self.plan["safety_gate"]["case_id"]:
                primary = complete and self.gate_valid
            pairs.append({
                "case_id": case["case_id"], "pair": 1,
                "complete": complete,
                "primary_comparison_eligible": primary,
            })
        value = {
            "schema": ab.REPORT_SCHEMA,
            "report_id": digest({"available": sorted(available),
                                 "output": output.name}),
            "complete": len(available) == len(self.plan["arms"]),
            "pairs": pairs,
        }
        self.write(output, value)
        return value

    def hooks(self):
        return {
            "load_plan": self.load_plan,
            "verify_application": self.verify_application,
            "admit": self.admit,
            "derive": self.derive,
            "verify_derived": self.verify_derived,
            "plan_b": self.plan_b,
            "configure_arm": self.configure_arm,
            "execute_arm": self.execute_arm,
            "finalize_arm": self.finalize_arm,
            "cleanup_arm": self.cleanup_arm,
            "aggregate": self.aggregate,
        }


class ContractAndOrSerialWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_serial_workflow_is_lazy_b_only_planned_and_gate_ordered(self):
        fixture = SyntheticWorkflow(self.root)
        cases = validate_execution_plan(fixture.plan)
        self.assertEqual(len(cases), 8)
        result = run_serial_workflow(
            fixture.plan_path, fixture.control, fixture.results,
            repository=fixture.repository, hooks=fixture.hooks(),
        )
        self.assertTrue(result["complete"])
        self.assertEqual(
            [arm for kind, arm in fixture.calls if kind == "plan_b"],
            [arm["arm_id"] for arm in fixture.plan["arms"]
             if arm["search_policy"] == AND_OR_SEARCH],
        )
        self.assertEqual(
            len([1 for kind, _ in fixture.calls if kind == "derive:proof_arm"]),
            16,
        )
        self.assertEqual(
            len([1 for kind, _ in fixture.calls
                 if kind == "derive:planning_staging"]),
            8,
        )
        status = workflow_status(fixture.control)
        self.assertTrue(status["complete"])
        self.assertTrue(status["safety_gate_passed"])
        self.assertEqual(len(status["completed_arms"]), 16)
        rows = WorkflowWAL(
            fixture.control, fixture.plan["plan_id"],
        ).replay()
        gate_sequence = next(
            row["sequence"] for row in rows
            if row["event"] == "safety_gate_passed"
        )
        later_derives = [
            row["sequence"] for row in rows
            if row["event"] == "proof_arm_derive_started"
            and row["payload"]["arm_id"].startswith("case-2--")
        ]
        self.assertTrue(later_derives)
        self.assertLess(gate_sequence, min(later_derives))

    def test_wal_resume_skips_completed_pair_and_reuses_derived_arm(self):
        fixture = SyntheticWorkflow(self.root, fail_once_sequence=3)
        with self.assertRaisesRegex(RuntimeError, "synthetic interruption"):
            run_serial_workflow(
                fixture.plan_path, fixture.control, fixture.results,
                repository=fixture.repository, hooks=fixture.hooks(),
            )
        first_pair = [arm["arm_id"] for arm in fixture.plan["arms"][:2]]
        for arm_id in first_pair:
            self.assertEqual(fixture.execute_counts[arm_id], 1)
        third = fixture.plan["arms"][2]["arm_id"]
        third_derives = len([
            1 for kind, arm_id in fixture.calls
            if kind == "derive:proof_arm" and arm_id == third
        ])
        result = run_serial_workflow(
            fixture.plan_path, fixture.control, fixture.results,
            repository=fixture.repository, hooks=fixture.hooks(),
        )
        self.assertTrue(result["complete"])
        for arm_id in first_pair:
            self.assertEqual(fixture.execute_counts[arm_id], 1)
        self.assertEqual(fixture.execute_counts[third], 2)
        self.assertEqual(len([
            1 for kind, arm_id in fixture.calls
            if kind == "derive:proof_arm" and arm_id == third
        ]), third_derives)
        cleanup = [
            row for row in WorkflowWAL(
                fixture.control, fixture.plan["plan_id"],
            ).replay()
            if row["event"] == "arm_cleanup_verified"
            and row["payload"]["arm_id"] == third
        ]
        self.assertEqual(
            [row["payload"]["after_failure"] for row in cleanup],
            [True, False],
        )

    def test_failed_first_pair_gate_blocks_all_later_derivation(self):
        fixture = SyntheticWorkflow(self.root, gate_valid=False)
        with self.assertRaisesRegex(
            RuntimeError, "first pair lacks complete primary evidence",
        ):
            run_serial_workflow(
                fixture.plan_path, fixture.control, fixture.results,
                repository=fixture.repository, hooks=fixture.hooks(),
            )
        derived = [
            arm_id for kind, arm_id in fixture.calls
            if kind == "derive:proof_arm"
        ]
        self.assertEqual(
            derived, [arm["arm_id"] for arm in fixture.plan["arms"][:2]],
        )
        status = workflow_status(fixture.control)
        self.assertFalse(status["safety_gate_passed"])
        self.assertFalse(status["complete"])

    def test_service_command_freezes_target_stop_and_b_control(self):
        fixture = SyntheticWorkflow(self.root)
        treatment = next(
            arm for arm in fixture.plan["arms"]
            if arm["search_policy"] == AND_OR_SEARCH
        )
        case = next(
            row for row in fixture.plan["selected_cases"]
            if row["case_id"] == treatment["case_id"]
        )
        control = self.root / "and-or-control.json"
        control.write_text("{}", encoding="utf-8")
        command = build_parallel_workspace_command(
            fixture.plan, treatment, case,
            Path(treatment["arm_instance_path"]),
            and_or_control=control,
        )
        self.assertEqual(command[3:5], [
            "regen.execution.proof_session_runner", "parallel-workspace",
        ])
        self.assertIn("--stop-at-focus-contract", command)
        self.assertIn("--and-or-control", command)
        self.assertEqual(command[command.index("--max-parallel-theories") + 1], "1")
        self.assertEqual(command[command.index("--generation-strategy") + 1],
                         ab.ACCEPTANCE_POLICY)
        self.assertEqual(command[command.index("--focus-contract-id") + 1],
                         case["target_contract_id"])


if __name__ == "__main__":
    unittest.main()
