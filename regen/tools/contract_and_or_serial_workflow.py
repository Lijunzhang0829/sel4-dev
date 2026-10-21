#!/usr/bin/env python3
"""Serially execute a frozen contract-rooted AND/OR A/B plan.

The workflow is deliberately a coordinator.  Mathematical work remains in the
existing ``parallel-workspace`` service, B-only problem realization remains in
``plan_target_problem``, and result authority remains in
``contract_and_or_ab.aggregate``.  This layer owns lazy fixture derivation,
resource admission, append-only recovery state, the first-pair evidence gate,
and verified release of services/native resources.

All effectful boundaries are injectable.  Focused tests therefore exercise the
real state machine without starting a provider, Isabelle, systemd, or Docker.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Callable

from regen.execution.proof_commits import digest, durable
from regen.infrastructure.incremental_log import read_artifact
from regen.planning.contract_and_or import initial_state
from regen.planning.online_policy_composition import (
    AND_OR_SEARCH,
    ARM_ATTESTATION_NAME,
    EXISTING_SEARCH,
    arm_policy,
    write_arm_attestation,
)
from regen.tools import contract_and_or_ab as ab


WORKFLOW_SCHEMA = "contract-and-or-serial-workflow-v1"
WAL_SCHEMA = "contract-and-or-serial-workflow-event-v1"
ARM_RUNTIME_SCHEMA = "contract-and-or-serial-arm-runtime-v1"
CONTROL_NAME = "workflow-binding.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def _reference(path: Path) -> dict[str, Any]:
    path = Path(path).resolve()
    if not path.is_file() or path.is_symlink():
        raise ValueError("workflow artifact is missing or unsafe: " + str(path))
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
    }


class WorkflowWAL:
    """Small append-only, hash-chained workflow journal."""

    def __init__(self, control_dir: Path, plan_id: str):
        self.control_dir = Path(control_dir).resolve()
        self.directory = self.control_dir / "wal"
        self.lock_path = self.control_dir / "workflow.lock"
        self.plan_id = plan_id
        self.control_dir.mkdir(parents=True, exist_ok=True)
        self.directory.mkdir(parents=True, exist_ok=True)

    def replay(self) -> list[dict[str, Any]]:
        rows = []
        previous = None
        paths = sorted(self.directory.glob("*.json"))
        for sequence, path in enumerate(paths, 1):
            if path.name != f"{sequence:06d}.json" or path.is_symlink():
                raise ValueError("workflow WAL is non-contiguous or unsafe")
            row = read_artifact(path)
            body = {key: value for key, value in row.items() if key != "event_id"}
            if (
                not isinstance(row, dict)
                or row.get("schema") != WAL_SCHEMA
                or row.get("sequence") != sequence
                or row.get("plan_id") != self.plan_id
                or row.get("previous_event_id") != previous
                or row.get("event_id") != digest(body)
            ):
                raise ValueError("workflow WAL hash-chain mismatch")
            rows.append(row)
            previous = row["event_id"]
        return rows

    def append(self, event: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(event, str) or not event or not isinstance(payload, dict):
            raise ValueError("invalid workflow event")
        with self.lock_path.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            rows = self.replay()
            body = {
                "schema": WAL_SCHEMA,
                "sequence": len(rows) + 1,
                "previous_event_id": rows[-1]["event_id"] if rows else None,
                "plan_id": self.plan_id,
                "event": event,
                "at": _now(),
                "payload": deepcopy(payload),
            }
            row = {**body, "event_id": digest(body)}
            durable(
                self.directory / f"{row['sequence']:06d}.json",
                row, immutable=True,
            )
            durable(self.control_dir / "status.json", {
                "schema": WORKFLOW_SCHEMA,
                "plan_id": self.plan_id,
                "last_sequence": row["sequence"],
                "last_event": event,
                "last_event_id": row["event_id"],
                "updated_at": row["at"],
            })
            return row


def _event(
    rows: list[dict[str, Any]], name: str, *, arm_id: str | None = None,
    case_id: str | None = None, pair: int | None = None,
) -> dict[str, Any] | None:
    for row in reversed(rows):
        if row["event"] != name:
            continue
        payload = row["payload"]
        if arm_id is not None and payload.get("arm_id") != arm_id:
            continue
        if case_id is not None and payload.get("case_id") != case_id:
            continue
        if pair is not None and payload.get("pair") != pair:
            continue
        return row
    return None


def validate_execution_plan(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate workflow-only invariants after ``ab.load_plan`` replay."""
    if not isinstance(plan, dict) or plan.get("schema") != ab.PLAN_SCHEMA:
        raise ValueError("workflow requires a frozen contract AND/OR A/B plan")
    selected = plan.get("selected_cases")
    if not isinstance(selected, list) or len(selected) != 8:
        raise ValueError("serial experiment requires exactly eight preregistered cases")
    case_ids = [case.get("case_id") for case in selected]
    if len(set(case_ids)) != 8 or any(not isinstance(item, str) for item in case_ids):
        raise ValueError("selected case identities are invalid")
    common = plan.get("common", {})
    if (
        common.get("model") != "gpt-6-astra"
        or common.get("reasoning_effort") != "medium"
        or common.get("acceptance_policy") != ab.ACCEPTANCE_POLICY
        or not isinstance(common.get("resources"), dict)
        or not common["resources"]
        or not isinstance(common.get("limits"), dict)
        or common["limits"].get("effective_proving_seconds", 0) <= 0
        or common["limits"].get("wall_seconds", 0) <= 0
    ):
        raise ValueError("workflow common model/budget/resource preregistration changed")
    order = plan.get("execution_case_order")
    if (
        not isinstance(order, list)
        or sorted(row.get("case_id") for row in order) != sorted(case_ids)
        or plan.get("safety_gate", {}).get("case_id") != order[0].get("case_id")
    ):
        raise ValueError("workflow case order/safety gate is not frozen")
    cases = {case["case_id"]: case for case in selected}
    for case in selected:
        checkpoint = case.get("checkpoint", {})
        if (
            checkpoint.get("source_kind") != "target-pristine-existing-v1"
            or case.get("problem_generation_recipe", {}).get("recipe_sha256")
                is None
            or not isinstance(case.get("target_contract_id"), str)
        ):
            raise ValueError("case is not a target-pristine recipe-bound fixture")
    arms = sorted(plan.get("arms", []), key=lambda row: row.get("sequence", -1))
    if [row.get("sequence") for row in arms] != list(range(1, len(arms) + 1)):
        raise ValueError("arm execution sequence is not contiguous")
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for arm in arms:
        case = cases.get(arm.get("case_id"))
        if (
            case is None
            or arm.get("search_policy") not in {EXISTING_SEARCH, AND_OR_SEARCH}
            or arm.get("prepared_fixture_path")
                != case["checkpoint"]["prepared_fixture"]
            or arm.get("prepared_manifest_sha256")
                != case["checkpoint"]["prepared_manifest"]["sha256"]
            or arm.get("problem_generation_recipe_sha256")
                != case["problem_generation_recipe"]["recipe_sha256"]
        ):
            raise ValueError("arm differs from its frozen case fixture/recipe")
        grouped.setdefault((arm["case_id"], arm["pair"]), []).append(arm)
    for key, pair_arms in grouped.items():
        if (
            len(pair_arms) != 2
            or {arm["search_policy"] for arm in pair_arms}
                != {EXISTING_SEARCH, AND_OR_SEARCH}
            or len({arm["prepared_fixture_path"] for arm in pair_arms}) != 1
            or len({arm["prepared_manifest_sha256"] for arm in pair_arms}) != 1
        ):
            raise ValueError("paired arms do not share one frozen fixture")
    if not grouped:
        raise ValueError("workflow plan has no A/B pairs")
    return cases


def build_parallel_workspace_command(
    plan: dict[str, Any], arm: dict[str, Any], case: dict[str, Any],
    run_dir: Path, *, and_or_control: Path | None,
) -> list[str]:
    """Build the one-theory service command from frozen common settings."""
    resources = plan["common"]["resources"]
    generation = plan["common"]["problem_generation"]
    command = [
        sys.executable, "-u", "-m", "regen.execution.proof_session_runner",
        "parallel-workspace", "--run-dir", str(Path(run_dir).resolve()),
        "--theory", case["theory"], "--planner-model", "gpt-6-astra",
        "--call-timeout", str(resources.get("call_timeout_seconds", 900)),
        "--check-timeout", str(resources.get("check_timeout_seconds", 900)),
        "--candidate-timeout", str(resources.get("candidate_timeout_seconds", 120)),
        "--max-parallel-theories", "1",
        "--native-slots", str(resources.get("native_slots", 1)),
        "--native-threads", str(resources.get("native_threads", 4)),
        "--resident-limit", str(resources.get("resident_limit", 1)),
        "--native-cpus", str(resources.get("native_cpus", 4)),
        "--native-memory-mb", str(resources.get("native_memory_mb", 16384)),
        "--native-pids-limit", str(resources.get("native_pids_limit", 1024)),
        "--provider-build-timeout",
        str(resources.get("provider_build_timeout_seconds", 1800)),
        "--file-planner-interface", "document",
        "--generation-strategy", ab.ACCEPTANCE_POLICY,
        "--focus-contract-id", case["target_contract_id"],
        "--stop-at-focus-contract", "--allow-external-call",
    ]
    if and_or_control is not None:
        command.extend(["--and-or-control", str(and_or_control.resolve())])
    if generation.get("timeout_seconds", 0) <= 0:
        raise ValueError("invalid frozen generation timeout")
    return command


def _default_load_plan(plan_path: Path) -> dict[str, Any]:
    return ab.load_plan(plan_path)


def _default_verify_application(
    *, plan: dict[str, Any], repository: Path, **_: Any,
) -> dict[str, Any]:
    from regen.tools.application_release import verify
    reference = plan["common"]["application_manifest"]
    path = Path(reference["path"]).resolve()
    if _sha256(path) != reference["sha256"]:
        raise ValueError("application manifest changed")
    return {**verify(repository, path), "manifest": _reference(path)}


def _resource_requirements(plan: dict[str, Any]) -> dict[str, int]:
    resources = plan["common"]["resources"]
    return {
        "min_free_disk_bytes": int(resources.get(
            "min_free_disk_bytes", 10 * 1024 ** 3,
        )),
        "min_available_memory_bytes": int(resources.get(
            "min_available_memory_bytes",
            (resources.get("native_memory_mb", 16384) + 2048) * 1024 ** 2,
        )),
    }


def _default_admit(
    *, plan: dict[str, Any], control_dir: Path, **_: Any,
) -> dict[str, Any]:
    from regen.tools.parallel_workspace_service import _resource_admission
    return _resource_admission(control_dir, _resource_requirements(plan))


def _default_derive(
    *, arm: dict[str, Any], destination: Path, **_: Any,
) -> dict[str, Any]:
    from regen.tools.prepared_checkpoint import derive
    return derive(
        arm["prepared_fixture_path"], destination,
        expected_manifest_sha256=arm["prepared_manifest_sha256"],
    )


def _default_verify_derived(
    *, arm: dict[str, Any], destination: Path, **_: Any,
) -> dict[str, Any]:
    instance_path = Path(destination).resolve() / "arm-instance.json"
    value = read_artifact(instance_path)
    if (
        value.get("schema") != "prepared-checkpoint-arm-v1"
        or Path(value.get("fixture", "")).resolve()
            != Path(arm["prepared_fixture_path"]).resolve()
        or value.get("fixture_manifest_sha256")
            != arm["prepared_manifest_sha256"]
    ):
        raise ValueError("derived arm does not bind its frozen fixture")
    return {"arm_instance": _reference(instance_path), "value": value}


def _default_plan_b(
    *, plan: dict[str, Any], arm: dict[str, Any], case: dict[str, Any],
    run_dir: Path, staging_dir: Path, **_: Any,
) -> dict[str, Any]:
    from regen.planning.and_or_problem_planner import plan_target_problem
    package = read_artifact(Path(case["package"]["path"]))
    generation = plan["common"]["problem_generation"]
    return plan_target_problem(
        package, target_contract_id=case["target_contract_id"],
        run_id=f"{plan['plan_id']}:{arm['arm_id']}",
        output_dir=Path(run_dir) / "treatment-planning",
        proof_arm_run=run_dir, staging_run=staging_dir,
        checkpoint_sha256=arm["checkpoint_sha256"],
        frozen_plan_recipe_sha256=arm[
            "problem_generation_recipe_sha256"],
        provider=generation["provider"], model=plan["common"]["model"],
        reasoning_effort=plan["common"]["reasoning_effort"],
        timeout=generation["timeout_seconds"],
        max_attempts=generation["max_attempts"],
    )


def _repository_authority(run_dir: Path, theory: str):
    from regen.execution.repository_execution import journal, package_for
    run = read_artifact(Path(run_dir) / "run.json")
    data = read_artifact(Path(run_dir) / "execution-input.json")
    entry = next(row for row in data["files"] if row["theory"] == theory)
    package = package_for(run, entry["file_slot"])
    return package, journal(Path(run_dir), run, package)


def _default_configure_arm(
    *, plan: dict[str, Any], arm: dict[str, Any], case: dict[str, Any],
    run_dir: Path, planning_result: dict[str, Any] | None,
    repository: Path, **_: Any,
) -> dict[str, Any]:
    package, journal = _repository_authority(run_dir, case["theory"])
    treatment = arm["search_policy"] == AND_OR_SEARCH
    problem = (
        planning_result.get("problem")
        if treatment and planning_result is not None
        and planning_result.get("status") == "accepted" else None
    )
    label = "B" if treatment else "A"
    # The shared attestation API is recipe based: A binds no realized problem;
    # B binds it only after accepted runtime planning.
    attestation = write_arm_attestation(
        problem=problem, package=package, journal=journal,
        policy=arm_policy(label), plan_id=plan["plan_id"],
        arm_id=arm["arm_id"], case_name=arm["case_id"],
        arm_instance_id=arm["arm_instance_id"],
        input_binding_sha256=arm["input_binding_sha256"],
        checkpoint_sha256=arm["checkpoint_sha256"],
        pristine_provenance_sha256=arm["pristine_provenance_sha256"],
        prepared_manifest_sha256=arm["prepared_manifest_sha256"],
        application_manifest_sha256=arm["application_manifest_sha256"],
        problem_generation_recipe_sha256=arm[
            "problem_generation_recipe_sha256"],
        realized_problem_sha256=(
            planning_result.get("realized_problem_sha256")
            if planning_result else None
        ),
        target_contract_id=case["target_contract_id"],
    )
    control_path = None
    if treatment:
        if problem is None:
            raise ValueError("accepted B execution requires a realized problem")
        control_path = Path(run_dir) / "and-or-runtime-control.json"
        control = {
            "schema": ARM_RUNTIME_SCHEMA,
            "problem": problem,
            "state": initial_state(problem),
            "execution_identity": {
                "plan_id": plan["plan_id"], "arm_id": arm["arm_id"],
                "case_name": arm["case_id"],
                "arm_instance_id": arm["arm_instance_id"],
                "input_binding_sha256": arm["input_binding_sha256"],
                "checkpoint_sha256": arm["checkpoint_sha256"],
                "pristine_provenance_sha256": arm[
                    "pristine_provenance_sha256"],
                "prepared_manifest_sha256": arm[
                    "prepared_manifest_sha256"],
                "application_manifest_sha256": arm[
                    "application_manifest_sha256"],
            },
            "arm_attestation_path": attestation["path"],
            "supervisor_directory": str(
                Path(run_dir) / "theories" / case["theory"]
                / "and-or-supervisor" / arm["arm_instance_id"]
            ),
        }
        durable(control_path, control, immutable=True)
    command = build_parallel_workspace_command(
        plan, arm, case, run_dir, and_or_control=control_path,
    )
    experiment_path = Path(run_dir) / "experiment.json"
    manifest = {
        "schema": ARM_RUNTIME_SCHEMA,
        "command": command,
        "cwd": str(repository.resolve()),
        "resource_requirements": _resource_requirements(plan),
        "plan_id": plan["plan_id"], "arm_id": arm["arm_id"],
        "search_policy": arm["search_policy"],
        "target_contract_id": case["target_contract_id"],
        "effective_proving_seconds": plan["common"]["limits"][
            "effective_proving_seconds"],
        "wall_seconds": plan["common"]["limits"]["wall_seconds"],
        "application_manifest_sha256": arm[
            "application_manifest_sha256"],
    }
    durable(experiment_path, manifest, immutable=True)
    return {
        "attestation": _reference(Path(attestation["path"])),
        "and_or_control": (
            _reference(control_path) if control_path is not None else None
        ),
        "experiment": _reference(experiment_path),
    }


def _default_execute_arm(
    *, plan: dict[str, Any], arm: dict[str, Any], case: dict[str, Any],
    run_dir: Path, control_dir: Path, **_: Any,
) -> dict[str, Any]:
    from regen.tools import green_contract_ab
    from regen.tools import parallel_workspace_service as service
    run_dir = Path(run_dir).resolve()
    launch = run_dir / "service-launch.json"
    if launch.exists():
        observed = service.status(run_dir)
        if not observed.get("identity_verified") and observed.get("status") not in {
            "previous_boot", "not_loaded",
        }:
            raise RuntimeError("existing arm service identity is not verified")
    else:
        observed = service.start(run_dir)
    output = Path(control_dir) / "observations" / arm["arm_id"]
    report = output / "report.json"
    if report.is_file():
        return read_artifact(report)
    return green_contract_ab.observe(
        run_dir, output, case["theory"], case["target_contract_id"],
        budget=plan["common"]["limits"]["effective_proving_seconds"],
        wall_limit=plan["common"]["limits"]["wall_seconds"],
    )


def _default_finalize_arm(
    *, plan: dict[str, Any], arm: dict[str, Any], case: dict[str, Any],
    run_dir: Path, results_dir: Path,
    planning_result: dict[str, Any] | None,
    configuration: dict[str, Any] | None,
    observation: dict[str, Any] | None, **_: Any,
) -> dict[str, Any]:
    run_dir, results_dir = Path(run_dir).resolve(), Path(results_dir).resolve()
    result_path = results_dir / f"{arm['arm_id']}.json"
    if result_path.is_file():
        return {"result": _reference(result_path), "value": read_artifact(result_path)}
    treatment = arm["search_policy"] == AND_OR_SEARCH
    planning_reference = None
    if treatment:
        planning_reference = _reference(
            run_dir / "treatment-planning" / "and-or-planning-result.json"
        )
        planning_reference.pop("bytes")
    planning_status = planning_result.get("status") if planning_result else None
    if planning_status in {"treatment_planning_failure", "infrastructure_failure"}:
        status = planning_status
    else:
        outcome = (observation or {}).get("outcome_class")
        status = {
            "target_success": "completed",
            "budget_censored": "budget_censored",
            "measurement_invalid": "measurement_invalid",
            "proof_failure": "proof_failure",
        }.get(outcome, "infrastructure_failure")
    transcripts = [
        {"path": str(path), "sha256": _sha256(path)}
        for path in ab._transcript_inventory(run_dir)
    ]
    native = [
        {"path": row["path"], "sha256": row["sha256"]}
        for row in ab._native_check_inventory(run_dir, case["theory"])
    ]
    attestation_path = run_dir / ARM_ATTESTATION_NAME
    value = {
        "schema": ab.RESULT_SCHEMA,
        "plan_id": plan["plan_id"], "arm_id": arm["arm_id"],
        "arm_instance_id": arm["arm_instance_id"],
        "case_id": arm["case_id"],
        "search_policy": arm["search_policy"],
        "input_binding_sha256": arm["input_binding_sha256"],
        "application_manifest_sha256": arm[
            "application_manifest_sha256"],
        "checkpoint_sha256": arm["checkpoint_sha256"],
        "prepared_manifest_sha256": arm["prepared_manifest_sha256"],
        "pristine_provenance_sha256": arm[
            "pristine_provenance_sha256"],
        "problem_generation_recipe_sha256": arm[
            "problem_generation_recipe_sha256"],
        "run_dir": str(run_dir), "planning_result": planning_reference,
        "execution_status": status,
        "acceptance_journal": str(
            run_dir / "theories" / case["theory"] / "acceptance"
        ),
        "arm_attestation_sha256": _sha256(attestation_path),
        "session_transcripts": transcripts,
        "timing": ({
            "service_launch": {
                "path": str(run_dir / "service-launch.json"),
                "sha256": _sha256(run_dir / "service-launch.json"),
            },
            "service_log": {
                "path": str(run_dir / "service-runner.log"),
                "sha256": _sha256(run_dir / "service-runner.log"),
            },
        } if observation is not None else None),
        "native_checks": native, "probes": [],
        "proof_failures": (
            [{"scope": "planning", "reason": "bounded attempts exhausted"}]
            if planning_status == "treatment_planning_failure" else []
        ),
        "infrastructure_failures": (
            [{"scope": "planning-or-execution", "reason": status}]
            if status in {"infrastructure_failure", "measurement_invalid"}
            else []
        ),
    }
    results_dir.mkdir(parents=True, exist_ok=True)
    durable(result_path, value, immutable=True)
    return {"result": _reference(result_path), "value": value}


def _default_cleanup_arm(*, run_dir: Path, **_: Any) -> dict[str, Any]:
    from regen.tools import contract_closure_validation as closure
    from regen.tools import parallel_workspace_service as service
    run_dir = Path(run_dir).resolve()
    stopped = None
    launch = run_dir / "service-launch.json"
    if launch.is_file():
        observed = service.status(run_dir)
        if observed.get("running"):
            stopped = service.stop(run_dir)
        else:
            stopped = observed
    native = closure.native_exit_gate(run_dir)
    if not native.get("all_exited"):
        raise RuntimeError("owned native resources remain alive")
    return {"service": stopped, "native_exit_gate": native, "verified": True}


def _default_aggregate(
    *, plan_path: Path, results_dir: Path, output: Path, **_: Any,
) -> dict[str, Any]:
    return ab.aggregate(plan_path, results_dir, output)


DEFAULT_HOOKS: dict[str, Callable[..., Any]] = {
    "load_plan": _default_load_plan,
    "verify_application": _default_verify_application,
    "admit": _default_admit,
    "derive": _default_derive,
    "verify_derived": _default_verify_derived,
    "plan_b": _default_plan_b,
    "configure_arm": _default_configure_arm,
    "execute_arm": _default_execute_arm,
    "finalize_arm": _default_finalize_arm,
    "cleanup_arm": _default_cleanup_arm,
    "aggregate": _default_aggregate,
}


def _hooks(overrides: dict[str, Callable[..., Any]] | None):
    result = dict(DEFAULT_HOOKS)
    if overrides:
        unknown = set(overrides) - set(result)
        if unknown:
            raise ValueError("unknown workflow hooks: " + ", ".join(sorted(unknown)))
        result.update(overrides)
    return result


def _initialize(
    plan_path: Path, control_dir: Path, results_dir: Path,
    plan: dict[str, Any], repository: Path,
) -> WorkflowWAL:
    control_dir.mkdir(parents=True, exist_ok=True)
    binding_path = control_dir / CONTROL_NAME
    binding = {
        "schema": WORKFLOW_SCHEMA,
        "plan_id": plan["plan_id"],
        "plan": {"path": str(plan_path), "sha256": _sha256(plan_path)},
        "results_dir": str(results_dir),
        "repository": str(repository),
        "explicit_execution_request": True,
        "runtime_problem_policy": "B-only; A never consumes realized problem",
        "resume_policy": "append-only WAL; no blind model/service replay",
    }
    if binding_path.exists():
        if read_artifact(binding_path) != binding:
            raise ValueError("workflow resume binding changed")
    else:
        durable(binding_path, binding, immutable=True)
    wal = WorkflowWAL(control_dir, plan["plan_id"])
    if not wal.replay():
        wal.append("workflow_initialized", binding)
    return wal


def _ensure_derived(
    *, wal: WorkflowWAL, rows: list[dict[str, Any]], hookset,
    plan, arm, case, destination: Path, role: str, common: dict[str, Any],
) -> dict[str, Any]:
    event_name = role + "_derived"
    prior = _event(rows, event_name, arm_id=arm["arm_id"])
    arguments = {
        **common, "plan": plan, "arm": arm, "case": case,
        "destination": destination, "role": role,
    }
    if prior is not None:
        return hookset["verify_derived"](**arguments)
    started = _event(rows, role + "_derive_started", arm_id=arm["arm_id"])
    if started is None:
        wal.append(role + "_derive_started", {
            "arm_id": arm["arm_id"], "destination": str(destination),
            "fixture": arm["prepared_fixture_path"],
            "prepared_manifest_sha256": arm["prepared_manifest_sha256"],
        })
    if destination.exists():
        result = hookset["verify_derived"](**arguments)
        recovered = True
    else:
        result = hookset["derive"](**arguments)
        hookset["verify_derived"](**arguments)
        recovered = False
    wal.append(event_name, {
        "arm_id": arm["arm_id"], "destination": str(destination),
        "fixture": arm["prepared_fixture_path"], "recovered": recovered,
        "receipt": deepcopy(result),
    })
    return result


def _gate_valid(report: dict[str, Any], case_id: str) -> bool:
    matches = [
        row for row in report.get("pairs", [])
        if row.get("case_id") == case_id and row.get("pair") == 1
    ]
    return bool(
        len(matches) == 1
        and matches[0].get("complete") is True
        and matches[0].get("primary_comparison_eligible") is True
    )


def run_serial_workflow(
    plan_path: Path, control_dir: Path, results_dir: Path, *,
    repository: Path | None = None,
    hooks: dict[str, Callable[..., Any]] | None = None,
) -> dict[str, Any]:
    """Run/resume one frozen plan; never replay a completed arm."""
    plan_path = Path(plan_path).resolve()
    control_dir = Path(control_dir).resolve()
    results_dir = Path(results_dir).resolve()
    repository = Path(
        repository or Path(__file__).resolve().parents[2]
    ).resolve()
    hookset = _hooks(hooks)
    plan = hookset["load_plan"](plan_path)
    cases = validate_execution_plan(plan)
    wal = _initialize(plan_path, control_dir, results_dir, plan, repository)
    rows = wal.replay()
    common = {
        "plan_path": plan_path, "control_dir": control_dir,
        "results_dir": results_dir, "repository": repository,
    }
    if _event(rows, "application_verified") is None:
        verified = hookset["verify_application"](plan=plan, **common)
        wal.append("application_verified", {"receipt": deepcopy(verified)})
        rows = wal.replay()

    gate_case = plan["safety_gate"]["case_id"]
    arms = sorted(plan["arms"], key=lambda row: row["sequence"])
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for arm in arms:
        grouped.setdefault((arm["case_id"], arm["pair"]), []).append(arm)

    for arm in arms:
        rows = wal.replay()
        if _event(rows, "workflow_completed") is not None:
            return read_artifact(control_dir / "final-report.json")
        if arm["case_id"] != gate_case and _event(rows, "safety_gate_passed") is None:
            raise RuntimeError("first-pair evidence gate has not passed")
        if _event(rows, "arm_completed", arm_id=arm["arm_id"]):
            continue
        case = cases[arm["case_id"]]
        if _event(rows, "resource_admitted", arm_id=arm["arm_id"]) is None:
            admission = hookset["admit"](
                plan=plan, arm=arm, case=case, **common,
            )
            wal.append("resource_admitted", {
                "arm_id": arm["arm_id"], "receipt": deepcopy(admission),
            })
            rows = wal.replay()
        if _event(rows, "application_reverified", arm_id=arm["arm_id"]) is None:
            verified = hookset["verify_application"](
                plan=plan, arm=arm, case=case, **common,
            )
            wal.append("application_reverified", {
                "arm_id": arm["arm_id"], "receipt": deepcopy(verified),
            })
            rows = wal.replay()

        run_dir = Path(arm["arm_instance_path"]).resolve()
        _ensure_derived(
            wal=wal, rows=rows, hookset=hookset, plan=plan,
            arm=arm, case=case, destination=run_dir, role="proof_arm",
            common=common,
        )
        rows = wal.replay()
        planning_result = None
        staging_dir = Path(arm["planning_staging_path"]).resolve()
        treatment = arm["search_policy"] == AND_OR_SEARCH
        if treatment:
            _ensure_derived(
                wal=wal, rows=rows, hookset=hookset, plan=plan,
                arm=arm, case=case, destination=staging_dir,
                role="planning_staging", common=common,
            )
            rows = wal.replay()
            planning_event = _event(
                rows, "runtime_planning_completed", arm_id=arm["arm_id"],
            )
            if planning_event is not None:
                planning_path = (
                    run_dir / "treatment-planning"
                    / "and-or-planning-result.json"
                )
                planning_result = read_artifact(planning_path)
            else:
                if _event(rows, "runtime_planning_started", arm_id=arm["arm_id"]) is None:
                    wal.append("runtime_planning_started", {
                        "arm_id": arm["arm_id"],
                        "recipe_sha256": arm[
                            "problem_generation_recipe_sha256"],
                        "proof_arm": str(run_dir),
                        "staging": str(staging_dir),
                    })
                planning_result = hookset["plan_b"](
                    plan=plan, arm=arm, case=case, run_dir=run_dir,
                    staging_dir=staging_dir, **common,
                )
                wal.append("runtime_planning_completed", {
                    "arm_id": arm["arm_id"],
                    "status": planning_result["status"],
                    "failure_type": planning_result.get("failure_type"),
                    "result_sha256": planning_result.get("result_sha256"),
                    "realized_problem_sha256": planning_result.get(
                        "realized_problem_sha256"),
                })
            rows = wal.replay()

        configuration = None
        observation = None
        planning_status = planning_result.get("status") if planning_result else None
        proof_execution = not treatment or planning_status == "accepted"
        try:
            if proof_execution:
                configured = _event(rows, "arm_configured", arm_id=arm["arm_id"])
                if configured is None:
                    configuration = hookset["configure_arm"](
                        plan=plan, arm=arm, case=case, run_dir=run_dir,
                        planning_result=planning_result, **common,
                    )
                    wal.append("arm_configured", {
                        "arm_id": arm["arm_id"],
                        "configuration": deepcopy(configuration),
                    })
                else:
                    configuration = configured["payload"]["configuration"]
                rows = wal.replay()
                if _event(rows, "proof_execution_completed", arm_id=arm["arm_id"]) is None:
                    if _event(rows, "proof_execution_started", arm_id=arm["arm_id"]) is None:
                        wal.append("proof_execution_started", {
                            "arm_id": arm["arm_id"], "run_dir": str(run_dir),
                            "service": "parallel-workspace",
                            "target_contract_id": case["target_contract_id"],
                            "effective_budget_seconds": plan["common"]["limits"][
                                "effective_proving_seconds"],
                        })
                    observation = hookset["execute_arm"](
                        plan=plan, arm=arm, case=case, run_dir=run_dir,
                        planning_result=planning_result,
                        configuration=configuration, **common,
                    )
                    wal.append("proof_execution_completed", {
                        "arm_id": arm["arm_id"],
                        "observation": deepcopy(observation),
                    })
                else:
                    observation = _event(
                        wal.replay(), "proof_execution_completed",
                        arm_id=arm["arm_id"],
                    )["payload"]["observation"]

            rows = wal.replay()
            if _event(rows, "arm_result_written", arm_id=arm["arm_id"]) is None:
                finalized = hookset["finalize_arm"](
                    plan=plan, arm=arm, case=case, run_dir=run_dir,
                    planning_result=planning_result,
                    configuration=configuration, observation=observation,
                    **common,
                )
                wal.append("arm_result_written", {
                    "arm_id": arm["arm_id"], "result": deepcopy(finalized),
                })
        except BaseException as error:
            try:
                cleanup = hookset["cleanup_arm"](
                    plan=plan, arm=arm, case=case, run_dir=run_dir,
                    staging_dir=(staging_dir if treatment else None), **common,
                )
                wal.append("arm_cleanup_verified", {
                    "arm_id": arm["arm_id"], "receipt": deepcopy(cleanup),
                    "after_failure": True,
                })
            except BaseException as cleanup_error:
                error.add_note(
                    "workflow cleanup failed: "
                    + f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            wal.append("workflow_paused", {
                "arm_id": arm["arm_id"],
                "error_type": type(error).__name__, "diagnostic": str(error),
            })
            raise

        rows = wal.replay()
        prior_cleanup = _event(
            rows, "arm_cleanup_verified", arm_id=arm["arm_id"],
        )
        if (
            prior_cleanup is None
            or prior_cleanup["payload"].get("after_failure") is True
        ):
            cleanup = hookset["cleanup_arm"](
                plan=plan, arm=arm, case=case, run_dir=run_dir,
                staging_dir=(staging_dir if treatment else None), **common,
            )
            if cleanup.get("verified") is not True:
                raise RuntimeError("arm cleanup is not verified")
            wal.append("arm_cleanup_verified", {
                "arm_id": arm["arm_id"], "receipt": deepcopy(cleanup),
                "after_failure": False,
            })
        wal.append("arm_completed", {
            "arm_id": arm["arm_id"], "case_id": arm["case_id"],
            "pair": arm["pair"], "sequence": arm["sequence"],
        })

        rows = wal.replay()
        pair_arms = grouped[(arm["case_id"], arm["pair"])]
        if all(_event(rows, "arm_completed", arm_id=item["arm_id"])
               for item in pair_arms):
            if _event(
                rows, "pair_completed", case_id=arm["case_id"],
                pair=arm["pair"],
            ) is None:
                wal.append("pair_completed", {
                    "case_id": arm["case_id"], "pair": arm["pair"],
                    "arm_ids": [item["arm_id"] for item in pair_arms],
                })
            if arm["case_id"] == gate_case and arm["pair"] == 1:
                rows = wal.replay()
                if _event(rows, "safety_gate_passed") is None:
                    gate_output = control_dir / "first-pair-report.json"
                    report = hookset["aggregate"](
                        output=gate_output, plan=plan, **common,
                    )
                    if not _gate_valid(report, gate_case):
                        wal.append("safety_gate_failed", {
                            "case_id": gate_case, "pair": 1,
                            "report_id": report.get("report_id"),
                        })
                        raise RuntimeError(
                            "first pair lacks complete primary evidence"
                        )
                    wal.append("safety_gate_passed", {
                        "case_id": gate_case, "pair": 1,
                        "report": _reference(gate_output),
                        "policy": "evidence validation only; no tuning/replacement",
                    })

    rows = wal.replay()
    if _event(rows, "workflow_completed") is None:
        output = control_dir / "final-report.json"
        report = hookset["aggregate"](
            output=output, plan=plan, **common,
        )
        if report.get("complete") is not True:
            raise RuntimeError("final aggregate is incomplete")
        wal.append("workflow_completed", {
            "report": _reference(output), "report_id": report.get("report_id"),
        })
    return read_artifact(control_dir / "final-report.json")


def workflow_status(control_dir: Path) -> dict[str, Any]:
    control_dir = Path(control_dir).resolve()
    binding = read_artifact(control_dir / CONTROL_NAME)
    rows = WorkflowWAL(control_dir, binding["plan_id"]).replay()
    completed = [row["payload"]["arm_id"] for row in rows
                 if row["event"] == "arm_completed"]
    return {
        "schema": WORKFLOW_SCHEMA, "plan_id": binding["plan_id"],
        "events": len(rows), "last_event": rows[-1]["event"] if rows else None,
        "completed_arms": completed,
        "safety_gate_passed": any(
            row["event"] == "safety_gate_passed" for row in rows),
        "complete": any(row["event"] == "workflow_completed" for row in rows),
    }


def stop_workflow(
    plan_path: Path, control_dir: Path, *,
    hooks: dict[str, Callable[..., Any]] | None = None,
) -> dict[str, Any]:
    hookset = _hooks(hooks)
    plan = hookset["load_plan"](Path(plan_path).resolve())
    cases = validate_execution_plan(plan)
    binding = read_artifact(Path(control_dir).resolve() / CONTROL_NAME)
    if binding["plan_id"] != plan["plan_id"]:
        raise ValueError("stop request plan differs from workflow binding")
    stopped = []
    for arm in plan["arms"]:
        run_dir = Path(arm["arm_instance_path"]).resolve()
        if not run_dir.exists():
            continue
        receipt = hookset["cleanup_arm"](
            plan=plan, arm=arm, case=cases[arm["case_id"]],
            run_dir=run_dir,
            staging_dir=(Path(arm["planning_staging_path"]).resolve()
                         if arm["search_policy"] == AND_OR_SEARCH else None),
            control_dir=Path(control_dir).resolve(),
            results_dir=Path(binding["results_dir"]),
            repository=Path(binding["repository"]),
            plan_path=Path(plan_path).resolve(),
        )
        if receipt.get("verified") is not True:
            raise RuntimeError("stop cleanup is not verified")
        stopped.append({"arm_id": arm["arm_id"], "receipt": receipt})
    wal = WorkflowWAL(Path(control_dir), plan["plan_id"])
    wal.append("workflow_stopped", {"arms": stopped})
    return {"verified": True, "arms": stopped}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    run = commands.add_parser("run")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--control-dir", type=Path, required=True)
    run.add_argument("--results-dir", type=Path, required=True)
    run.add_argument("--repository", type=Path)
    status = commands.add_parser("status")
    status.add_argument("--control-dir", type=Path, required=True)
    stop = commands.add_parser("stop")
    stop.add_argument("--plan", type=Path, required=True)
    stop.add_argument("--control-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.operation == "run":
        value = run_serial_workflow(
            args.plan, args.control_dir, args.results_dir,
            repository=args.repository,
        )
    elif args.operation == "status":
        value = workflow_status(args.control_dir)
    else:
        value = stop_workflow(args.plan, args.control_dir)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
