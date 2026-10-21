#!/usr/bin/env python3
"""Build a contract-rooted AND/OR problem from one audited File Planner DAG.

The conversion is deliberately mechanical.  It neither asks a model nor runs
Isabelle.  A production caller must supply two authorities before a problem is
accepted:

* a real, no-tool File Planner transcript (the default validator checks the
  normal ``llm_transcript`` artifact family); and
* a native declaration-preflight receipt for every root-reachable goal.

``plan_target_problem`` is the orchestration wrapper.  It calls the existing
``file_planner.plan_file`` with the complete fixed package and an explicit
target-focus instruction, preserves each call, and retries only a bounded
number of rejected plans.  Tests inject both authorities and never start a
provider or native worker.
"""
from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from regen.execution.proof_commits import durable, freeze_evidence
from regen.infrastructure.llm_transcript import (
    artifact_paths,
    validate_call_artifact,
)
from regen.infrastructure.incremental_log import read_artifact
from regen.planning.contract_and_or import SCHEMA as AND_OR_METHOD, validate_problem
from regen.planning.file_planner import plan_file, planner_prompt, planning_environment
from regen.planning.proof_graph import (
    execution_order,
    validate_graph,
    verify_graph_payload_sha256,
)


PROBLEM_SCHEMA = "file-planner-contract-and-or-problem-v1"
TRANSCRIPT_RECEIPT_SCHEMA = "audited-file-planner-transcript-v1"
PREFLIGHT_RECEIPT_SCHEMA = "native-declaration-preflight-v1"
ORCHESTRATION_SCHEMA = "and-or-file-planning-v1"
PLANNING_RECIPE_SCHEMA = "contract-rooted-and-or-planning-recipe-v1"
FOCUS_POLICY = "authoritative-target-only-focus-v1"
TARGET_VIEW_POLICY = "authoritative-target-only-package-view-v1"
COST_POLICY = "fixed-uniform-route-cost-v1"

# These are a priori scheduler constants, not estimates fitted to earlier
# experiments and not observations used in the A/B result.  Equal route-local
# costs make the direct route the deterministic first choice; after its single
# attempt is exhausted, a decomposed route can expose the exact File Planner
# prerequisites.  The root itself remains in every plan and must be proved.
ROUTE_COSTS: dict[str, dict[str, float | int | str]] = {
    "direct": {
        "estimate_seconds": 1.0,
        "recheck_seconds": 1.0,
        "failure_penalty_seconds": 1.0,
        "probe_seconds": 0.0,
        "attempt_limit": 1,
        "probe_policy": "none",
    },
    "decomposed": {
        "estimate_seconds": 1.0,
        "recheck_seconds": 1.0,
        "failure_penalty_seconds": 1.0,
        "probe_seconds": 0.0,
        "attempt_limit": 1,
        "probe_policy": "none",
    },
}

_HEX = frozenset("0123456789abcdef")
_FORBIDDEN_MATERIAL_KEYS = frozenset({
    "proof", "proof_body", "proofs", "human_proof", "historical_proof",
    "accepted_proofs", "execution_history", "proof_history",
})
_ALLOWED_GRAPH_KEYS = frozenset({
    "schema_version", "run_id", "theory", "file_slot", "revision", "nodes",
    "contract_routes", "execution_order", "statement_certification",
    "planning_notes", "initial_graph_owner", "runtime_graph_owner",
    "planner_context", "upstream_bindings", "planner_call",
    "graph_payload_sha256",
})
_ALLOWED_NODE_KEYS = frozenset({
    "id", "name", "role", "statement", "context", "dependencies",
    "consumers", "strategy", "semantic_role", "reasoning_gap",
    "intended_consumers", "premise_evidence_ids", "premise_query",
})


class TargetPlanningExhausted(RuntimeError):
    """Every bounded File Planner/native-preflight attempt was rejected."""

    def __init__(self, message: str, attempts: list[dict[str, Any]]):
        self.attempts = deepcopy(attempts)
        super().__init__(message)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                return result.hexdigest()
            result.update(chunk)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str) and len(value) == 64
        and set(value) <= _HEX
    )


def _without_hash(value: dict[str, Any], field: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != field}


def problem_payload_sha256(problem: dict[str, Any]) -> str:
    return canonical_sha256(_without_hash(problem, "problem_payload_sha256"))


def verify_problem_payload_sha256(problem: dict[str, Any]) -> None:
    expected = problem.get("problem_payload_sha256")
    if not _is_sha256(expected) or expected != problem_payload_sha256(problem):
        raise ValueError("AND/OR problem payload hash mismatch")


def _assert_no_proof_or_history(value: Any, *, where: str) -> None:
    """Reject embedded proof/history values while allowing planning language.

    Exact keys are used intentionally: public fields such as ``proof_shape``
    and ``strategy`` describe a route but contain no proof body.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in _FORBIDDEN_MATERIAL_KEYS:
                raise ValueError(f"forbidden proof/history material at {where}.{key}")
            _assert_no_proof_or_history(item, where=f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_proof_or_history(item, where=f"{where}[{index}]")


def _verify_package(package: dict[str, Any]) -> str:
    if not isinstance(package, dict):
        raise ValueError("fixed package must be an object")
    expected = package.get("package_payload_sha256")
    actual = canonical_sha256(_without_hash(package, "package_payload_sha256"))
    if not _is_sha256(expected) or expected != actual:
        raise ValueError("fixed package payload hash mismatch")
    contracts = package.get("contracts")
    if not isinstance(contracts, list) or not contracts:
        raise ValueError("fixed package has no contracts")
    _assert_no_proof_or_history(package, where="package")
    return expected


def _target_related_rows(
    rows: Any, target_contract_id: str,
) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    result = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        contract_lists = [
            value for key, value in raw.items()
            if key in {"contract_ids", "for_contracts", "target_contract_ids"}
            and isinstance(value, list)
        ]
        contract_scalars = [
            value for key, value in raw.items()
            if key.endswith("contract_id") and isinstance(value, str)
        ]
        referenced = {
            item for values in contract_lists for item in values
        } | set(contract_scalars)
        if target_contract_id not in referenced:
            continue
        # Relations to another unproved contract are not premises for a
        # contract-rooted one-shot plan.  Group/intention membership lists are
        # safely projected to the selected target.
        if contract_scalars and any(item != target_contract_id for item in contract_scalars):
            continue
        row = deepcopy(raw)
        for key in ("contract_ids", "for_contracts", "target_contract_ids"):
            if isinstance(row.get(key), list):
                row[key] = [target_contract_id]
        result.append(row)
    return result


def target_package_view(
    package: dict[str, Any], target_contract_id: str,
) -> dict[str, Any]:
    """Derive the only legal one-shot planner view from a full frozen package."""
    full_hash = _verify_package(package)
    target = deepcopy(_target_contract(package, target_contract_id))
    body = deepcopy(_without_hash(package, "package_payload_sha256"))
    body["contracts"] = [target]
    body["support_contracts"] = []
    body["source_full_package_payload_sha256"] = full_hash
    body["target_view_policy"] = TARGET_VIEW_POLICY
    body["target_contract_id"] = target_contract_id
    for key in ("collaboration_groups",):
        if key in body:
            body[key] = _target_related_rows(body[key], target_contract_id)
    projection = body.get("repository_projection")
    if isinstance(projection, dict):
        projection = deepcopy(projection)
        projection["contracts"] = [target]
        projection["contract_ids"] = [target_contract_id]
        for key in (
            "auxiliary_intents", "contract_relations", "helper_decisions",
            "collaboration_groups",
        ):
            if key in projection:
                projection[key] = _target_related_rows(
                    projection[key], target_contract_id,
                )
        body["repository_projection"] = projection
    view = {**body, "package_payload_sha256": canonical_sha256(body)}
    _verify_package(view)
    return view


def planning_recipe(
    package: dict[str, Any], *, target_contract_id: str,
    checkpoint_sha256: str, provider: str = "codex",
    model: str = "gpt-6-astra", reasoning_effort: str = "medium",
    timeout: int = 1800, max_attempts: int = 3,
) -> dict[str, Any]:
    """Create the problem-free recipe that the frozen A/B plan can bind."""
    full_hash = _verify_package(package)
    view = target_package_view(package, target_contract_id)
    if not _is_sha256(checkpoint_sha256):
        raise ValueError("planning recipe requires a checkpoint SHA-256")
    if (
        not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1
        or not isinstance(max_attempts, int) or isinstance(max_attempts, bool)
        or max_attempts < 1
    ):
        raise ValueError("planning recipe limits must be positive integers")
    recipe = {
        "schema": PLANNING_RECIPE_SCHEMA,
        "target_contract_id": target_contract_id,
        "full_package_payload_sha256": full_hash,
        "target_view_package_payload_sha256": view["package_payload_sha256"],
        "checkpoint_sha256": checkpoint_sha256,
        "provider": provider,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "timeout_seconds": timeout,
        "max_attempts": max_attempts,
        "focus_policy": FOCUS_POLICY,
        "problem_builder": PROBLEM_SCHEMA,
        "and_or_method": AND_OR_METHOD,
        "cost_policy": COST_POLICY,
        "cost_constants_sha256": canonical_sha256(ROUTE_COSTS),
        "native_preflight_schema": PREFLIGHT_RECEIPT_SCHEMA,
        "probe_policy": "none",
    }
    recipe["recipe_sha256"] = canonical_sha256(recipe)
    return recipe


def verify_planning_recipe(
    recipe: dict[str, Any], expected_sha256: str,
) -> dict[str, Any]:
    """Verify and return a copy of a frozen problem-free planning recipe."""
    if (
        not isinstance(recipe, dict)
        or recipe.get("schema") != PLANNING_RECIPE_SCHEMA
        or not _is_sha256(expected_sha256)
        or recipe.get("recipe_sha256") != expected_sha256
        or expected_sha256 != canonical_sha256(
            _without_hash(recipe, "recipe_sha256")
        )
    ):
        raise ValueError("frozen AND/OR planning recipe mismatch")
    return deepcopy(recipe)


def _verify_planning_recipe(
    recipe: dict[str, Any], expected_sha256: str,
) -> None:
    verify_planning_recipe(recipe, expected_sha256)


def _verify_graph(graph: dict[str, Any], package: dict[str, Any]) -> str:
    if not isinstance(graph, dict):
        raise ValueError("File Planner graph must be an object")
    unknown = set(graph) - _ALLOWED_GRAPH_KEYS
    if unknown:
        raise ValueError(f"unsupported File Planner graph fields: {sorted(unknown)}")
    if "document_order" in graph:
        raise ValueError("AND/OR conversion requires a File Planner dependency DAG")
    for node in graph.get("nodes", []):
        if not isinstance(node, dict):
            raise ValueError("File Planner graph node must be an object")
        unknown_node = set(node) - _ALLOWED_NODE_KEYS
        if unknown_node:
            raise ValueError(
                f"unsupported File Planner node fields for {node.get('id')}: "
                f"{sorted(unknown_node)}"
            )
    _assert_no_proof_or_history(graph, where="graph")
    verify_graph_payload_sha256(graph)
    # validate_graph mutates only the derived statement-certification field.
    checked = deepcopy(graph)
    validate_graph(checked, deepcopy(package))
    expected_order = execution_order(checked)
    if graph.get("execution_order") != expected_order:
        raise ValueError("File Planner execution_order is missing or stale")
    return graph["graph_payload_sha256"]


def _target_contract(
    package: dict[str, Any], target_contract_id: str,
) -> dict[str, Any]:
    matches = [
        item for item in package["contracts"]
        if item.get("contract_id") == target_contract_id
    ]
    if len(matches) != 1:
        raise ValueError("target must identify exactly one fixed contract")
    return matches[0]


def root_reachable_node_ids(
    graph: dict[str, Any], package: dict[str, Any], target_contract_id: str,
) -> list[str]:
    """Return target plus its exact generated dependency closure in DAG order."""
    _target_contract(package, target_contract_id)
    by_id = {node["id"]: node for node in graph["nodes"]}
    if target_contract_id not in by_id:
        raise ValueError("fixed target is absent from File Planner graph")
    closure: set[str] = set()
    pending = [target_contract_id]
    while pending:
        node_id = pending.pop()
        if node_id in closure:
            continue
        node = by_id[node_id]
        if node_id != target_contract_id and node.get("role") == "contract":
            raise ValueError(
                "target route illegally depends on another unproved contract: "
                + node_id
            )
        closure.add(node_id)
        pending.extend(node["dependencies"])
    return [node_id for node_id in graph["execution_order"] if node_id in closure]


def _artifact_receipt(prefix: Path, meta: dict[str, Any]) -> dict[str, Any]:
    paths = artifact_paths(prefix)
    artifacts = {
        name: {
            "path": str(path.resolve()),
            "sha256": _file_sha256(path),
            "bytes": path.stat().st_size,
        }
        for name, path in sorted(paths.items())
    }
    receipt = {
        "schema": TRANSCRIPT_RECEIPT_SCHEMA,
        "validated_real": True,
        "no_tool_events": True,
        "sealable_generation": True,
        "stage": meta.get("stage"),
        "provider": meta.get("provider"),
        "model": meta.get("model"),
        "reasoning_effort": meta.get("reasoning_effort"),
        "theory": meta.get("theory"),
        "transcript_prefix": str(prefix.resolve()),
        "wall_ms": meta.get("wall_ms"),
        "artifacts": artifacts,
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def validate_file_planner_transcript(
    graph: dict[str, Any], *, model: str, reasoning_effort: str,
    expected_prompt_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate and bind the real no-tool call named by ``graph.planner_call``."""
    call = graph.get("planner_call")
    if not isinstance(call, dict):
        raise ValueError("File Planner graph has no planner_call binding")
    prefix_text = call.get("transcript_prefix")
    if not isinstance(prefix_text, str) or not prefix_text:
        raise ValueError("File Planner graph has no transcript prefix")
    prefix = Path(prefix_text).resolve()
    meta = validate_call_artifact(prefix, require_real=True)
    if meta.get("stage") != "file-planner":
        raise ValueError("transcript is not a File Planner call")
    if meta.get("theory") != graph.get("theory"):
        raise ValueError("File Planner transcript theory mismatch")
    if meta.get("model") != model or call.get("model") != model:
        raise ValueError("File Planner model mismatch")
    if meta.get("reasoning_effort") != reasoning_effort:
        raise ValueError("File Planner reasoning-effort mismatch")
    provider_pair = (call.get("provider"), meta.get("provider"))
    if provider_pair not in {
        ("codex", "codex-exec-audited"),
        ("claude", "claude-print-audited"),
    }:
        raise ValueError("File Planner provider binding mismatch")
    for field in ("prompt_sha256", "final_sha256"):
        if not _is_sha256(call.get(field)) or call[field] != meta.get(field):
            raise ValueError(f"File Planner {field} binding mismatch")
    if (
        expected_prompt_sha256 is not None
        and call["prompt_sha256"] != expected_prompt_sha256
    ):
        raise ValueError("File Planner prompt differs from focused request")
    receipt = _artifact_receipt(prefix, meta)
    _validate_transcript_receipt(
        receipt, graph=graph, model=model, reasoning_effort=reasoning_effort,
        expected_prompt_sha256=expected_prompt_sha256,
    )
    return receipt


def _validate_transcript_receipt(
    receipt: dict[str, Any], *, graph: dict[str, Any], model: str,
    reasoning_effort: str, expected_prompt_sha256: str | None = None,
) -> None:
    if not isinstance(receipt, dict) or receipt.get("schema") != TRANSCRIPT_RECEIPT_SCHEMA:
        raise ValueError("invalid File Planner transcript receipt schema")
    if not all(receipt.get(field) is True for field in (
        "validated_real", "no_tool_events", "sealable_generation",
    )):
        raise ValueError("File Planner transcript receipt is not real/no-tool/sealable")
    if receipt.get("stage") != "file-planner":
        raise ValueError("transcript receipt has wrong stage")
    provider_pair = (graph.get("planner_call", {}).get("provider"), receipt.get("provider"))
    if provider_pair not in {
        ("codex", "codex-exec-audited"),
        ("claude", "claude-print-audited"),
    }:
        raise ValueError("transcript receipt provider mismatch")
    if receipt.get("model") != model or receipt.get("reasoning_effort") != reasoning_effort:
        raise ValueError("transcript receipt model configuration mismatch")
    wall_ms = receipt.get("wall_ms")
    if not isinstance(wall_ms, int) or isinstance(wall_ms, bool) or wall_ms < 0:
        raise ValueError("transcript receipt has no authoritative wall time")
    if receipt.get("theory") != graph.get("theory"):
        raise ValueError("transcript receipt theory mismatch")
    call = graph.get("planner_call", {})
    try:
        receipt_prefix = str(Path(receipt.get("transcript_prefix", "")).resolve())
        call_prefix = str(Path(call.get("transcript_prefix", "")).resolve())
    except (TypeError, OSError) as error:
        raise ValueError("invalid transcript prefix in receipt") from error
    if not receipt.get("transcript_prefix") or receipt_prefix != call_prefix:
        raise ValueError("transcript receipt prefix mismatch")
    artifacts = receipt.get("artifacts")
    required = {"prompt", "raw", "final", "stderr", "meta", "audit"}
    if not isinstance(artifacts, dict) or set(artifacts) != required:
        raise ValueError("transcript receipt requires the complete artifact family")
    for name, item in artifacts.items():
        if (
            not isinstance(item, dict)
            or not _is_sha256(item.get("sha256"))
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("bytes"), int)
            or isinstance(item.get("bytes"), bool)
            or item["bytes"] < 0
        ):
            raise ValueError(f"invalid transcript artifact receipt: {name}")
    if artifacts["prompt"]["sha256"] != call.get("prompt_sha256"):
        raise ValueError("transcript receipt prompt hash mismatch")
    if artifacts["final"]["sha256"] != call.get("final_sha256"):
        raise ValueError("transcript receipt final hash mismatch")
    if expected_prompt_sha256 is not None and artifacts["prompt"]["sha256"] != expected_prompt_sha256:
        raise ValueError("transcript receipt does not bind focused prompt")
    expected_receipt = receipt.get("receipt_sha256")
    if (
        not _is_sha256(expected_receipt)
        or expected_receipt != canonical_sha256(_without_hash(receipt, "receipt_sha256"))
    ):
        raise ValueError("transcript receipt hash mismatch")


def _preflight_input_binding(
    graph: dict[str, Any], package: dict[str, Any], *,
    target_contract_id: str, required_node_ids: list[str], model: str,
    reasoning_effort: str,
) -> dict[str, Any]:
    by_id = {node["id"]: node for node in graph["nodes"]}
    declarations = [{
        key: deepcopy(by_id[node_id].get(key, [] if key == "context" else ""))
        for key in ("id", "name", "role", "statement", "context")
    } for node_id in required_node_ids]
    return {
        "schema": "and-or-prospective-problem-input-v1",
        "source_graph_payload_sha256": graph["graph_payload_sha256"],
        "source_package_payload_sha256": package["package_payload_sha256"],
        "target_contract_id": target_contract_id,
        "required_node_ids": list(required_node_ids),
        "declarations_sha256": canonical_sha256(declarations),
        "model": model,
        "reasoning_effort": reasoning_effort,
        "cost_policy_sha256": canonical_sha256(ROUTE_COSTS),
    }


def _validate_preflight_receipt(
    receipt: dict[str, Any], *, required_node_ids: list[str], allow_reject: bool,
    graph: dict[str, Any], package: dict[str, Any], target_contract_id: str,
    model: str, reasoning_effort: str, staging_run: Path,
    proof_arm_run: Path,
) -> None:
    if not isinstance(receipt, dict) or receipt.get("schema") != PREFLIGHT_RECEIPT_SCHEMA:
        raise ValueError("invalid native declaration-preflight receipt schema")
    verdict = receipt.get("verdict")
    if verdict not in ({"pass", "reject"} if allow_reject else {"pass"}):
        raise ValueError("native declaration preflight did not pass")
    if receipt.get("authority") != "isabelle-native":
        raise ValueError("declaration preflight lacks Isabelle native authority")
    binding = _preflight_input_binding(
        graph, package, target_contract_id=target_contract_id,
        required_node_ids=required_node_ids, model=model,
        reasoning_effort=reasoning_effort,
    )
    if (
        receipt.get("problem_input") != binding
        or receipt.get("problem_input_sha256") != canonical_sha256(binding)
    ):
        raise ValueError("native preflight input/problem binding mismatch")
    staging_run = staging_run.resolve()
    proof_arm_run = proof_arm_run.resolve()
    if receipt.get("staging_run") != str(staging_run):
        raise ValueError("native preflight staging-run binding mismatch")
    if receipt.get("proof_arm_run") != str(proof_arm_run):
        raise ValueError("native preflight proof-arm binding mismatch")
    if receipt.get("proof_arm_native_calls") != 0:
        raise ValueError("native preflight opened the proof arm")
    try:
        staging_run.relative_to(proof_arm_run)
        raise ValueError("native staging run is nested in the proof arm")
    except ValueError as error:
        if str(error) == "native staging run is nested in the proof arm":
            raise
    try:
        proof_arm_run.relative_to(staging_run)
        raise ValueError("proof arm is nested in the native staging run")
    except ValueError as error:
        if str(error) == "proof arm is nested in the native staging run":
            raise
    checked = receipt.get("checked_node_ids")
    if (
        not isinstance(checked, list) or len(checked) != len(set(checked))
        or any(node_id not in required_node_ids for node_id in checked)
    ):
        raise ValueError("invalid declaration-preflight checked node set")
    if checked != required_node_ids:
        raise ValueError("native preflight did not check the exact root closure")
    diagnostics = receipt.get("diagnostics")
    if (
        not isinstance(diagnostics, list)
        or not all(isinstance(item, str) for item in diagnostics)
        or (verdict == "reject" and not diagnostics)
    ):
        raise ValueError("invalid native preflight diagnostics")
    for field in ("started_at", "finished_at", "resources_released_at"):
        if not isinstance(receipt.get(field), str) or not receipt[field]:
            raise ValueError("native preflight has incomplete timing evidence")
    for field in ("wall_ms", "native_calls"):
        value = receipt.get(field)
        if (
            not isinstance(value, int) or isinstance(value, bool)
            or value < (1 if field == "native_calls" else 0)
        ):
            raise ValueError("native preflight has invalid " + field)
    if receipt.get("cleanup_verified") is not True:
        raise ValueError("native staging resources were not verified released")
    for field in ("native_evidence", "cleanup_evidence"):
        locator = receipt.get(field)
        if (
            not isinstance(locator, dict)
            or not isinstance(locator.get("path"), str)
            or not _is_sha256(locator.get("sha256"))
            or not isinstance(locator.get("bytes"), int)
            or isinstance(locator.get("bytes"), bool)
            or locator["bytes"] < 0
        ):
            raise ValueError(f"native preflight {field} is not bound")
        path = Path(locator["path"]).resolve()
        if (
            not path.is_file() or path.stat().st_size != locator["bytes"]
            or _file_sha256(path) != locator["sha256"]
        ):
            raise ValueError(f"native preflight {field} hash mismatch")
        try:
            path.relative_to(staging_run)
        except ValueError as error:
            raise ValueError(
                f"native preflight {field} escaped the staging run"
            ) from error
    expected = receipt.get("receipt_sha256")
    if not _is_sha256(expected) or expected != canonical_sha256(
        _without_hash(receipt, "receipt_sha256")
    ):
        raise ValueError("native preflight receipt hash mismatch")


def make_preflight_receipt(
    *, verdict: str, checked_node_ids: list[str], diagnostics: list[str],
    artifact_path: Path, cleanup_artifact_path: Path,
    graph: dict[str, Any], package: dict[str, Any], target_contract_id: str,
    model: str, reasoning_effort: str, staging_run: Path,
    proof_arm_run: Path,
    started_at: str, finished_at: str, wall_ms: int, native_calls: int,
    resources_released_at: str,
) -> dict[str, Any]:
    """Normalize an adapter result after it has written its native artifact."""
    path = artifact_path.resolve()
    cleanup_path = cleanup_artifact_path.resolve()
    if not path.is_file() or not cleanup_path.is_file():
        raise ValueError("native preflight adapter did not write its artifact")
    problem_input = _preflight_input_binding(
        graph, package, target_contract_id=target_contract_id,
        required_node_ids=checked_node_ids, model=model,
        reasoning_effort=reasoning_effort,
    )
    receipt = {
        "schema": PREFLIGHT_RECEIPT_SCHEMA,
        "authority": "isabelle-native",
        "verdict": verdict,
        "checked_node_ids": list(checked_node_ids),
        "diagnostics": list(diagnostics),
        "problem_input": problem_input,
        "problem_input_sha256": canonical_sha256(problem_input),
        "staging_run": str(staging_run.resolve()),
        "staging_instance_id": staging_run.resolve().name,
        "proof_arm_run": str(proof_arm_run.resolve()),
        "proof_arm_native_calls": 0,
        "started_at": started_at,
        "finished_at": finished_at,
        "wall_ms": wall_ms,
        "native_calls": native_calls,
        "native_evidence": {
            "path": str(path), "sha256": _file_sha256(path),
            "bytes": path.stat().st_size,
        },
        "cleanup_verified": True,
        "resources_released_at": resources_released_at,
        "cleanup_evidence": {
            "path": str(cleanup_path), "sha256": _file_sha256(cleanup_path),
            "bytes": cleanup_path.stat().st_size,
        },
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def run_staging_native_preflight(
    *, graph: dict[str, Any], package: dict[str, Any],
    authoritative_package: dict[str, Any], target_contract_id: str,
    required_node_ids: list[str], attempt: int, output_dir: Path,
    staging_run: Path, proof_arm_run: Path, problem_input: dict[str, Any],
    timeout: int = 1800,
    setup_fn: Callable[..., tuple[Any, ...]] | None = None,
) -> dict[str, Any]:
    """Run statement-shell validation in a disposable repository staging run.

    ``staging_run`` must already be a fresh derived repository run.  The
    production default is ``repository_execution.setup`` followed by exactly
    one ``check_graph`` call.  The checker is always closed, and both the raw
    native result and cleanup outcome are durable immutable artifacts.  No
    proof-arm path is passed to setup or the checker.
    """
    if (
        not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1
        or not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1
    ):
        raise ValueError("native preflight attempt/timeout must be positive integers")
    staging_run = staging_run.resolve()
    proof_arm_run = proof_arm_run.resolve()
    output_dir = output_dir.resolve()
    if (
        staging_run == proof_arm_run
        or staging_run.is_relative_to(proof_arm_run)
        or proof_arm_run.is_relative_to(staging_run)
    ):
        raise ValueError("native staging and proof arm must be disjoint")
    try:
        output_dir.relative_to(staging_run)
    except ValueError as error:
        raise ValueError("native preflight output escaped staging run") from error
    authoritative_hash = _verify_package(authoritative_package)
    if target_package_view(
        authoritative_package, target_contract_id,
    ) != package:
        raise ValueError("native preflight target view is not authoritative")
    _verify_graph(graph, package)
    closure = root_reachable_node_ids(graph, package, target_contract_id)
    if required_node_ids != closure:
        raise ValueError("native preflight did not receive the exact target closure")
    model = problem_input.get("model")
    reasoning_effort = problem_input.get("reasoning_effort")
    expected_input = _preflight_input_binding(
        graph, package, target_contract_id=target_contract_id,
        required_node_ids=closure, model=model,
        reasoning_effort=reasoning_effort,
    )
    if problem_input != expected_input:
        raise ValueError("native preflight prospective problem input changed")
    if setup_fn is None:
        from regen.execution.repository_execution import setup as setup_fn

    output_dir.mkdir(parents=True, exist_ok=True)
    native_path = output_dir / f"native-attempt-{attempt:03d}.json"
    cleanup_path = output_dir / f"cleanup-attempt-{attempt:03d}.json"
    started_at = _utc_now()
    started = time.monotonic_ns()
    checker = None
    check = None
    primary_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    try:
        runtime = setup_fn(staging_run, package["theory"], timeout)
        if not isinstance(runtime, tuple) or len(runtime) != 6:
            raise RuntimeError("staging setup returned an invalid runtime tuple")
        staged_package = runtime[1]
        checker = runtime[4]
        if (
            _verify_package(staged_package) != authoritative_hash
            or staged_package != authoritative_package
        ):
            raise ValueError("staging repository package differs from frozen input")
        if checker is None or not callable(getattr(checker, "check_graph", None)):
            raise RuntimeError("staging setup returned no graph checker")
        check = freeze_evidence(checker.check_graph(deepcopy(graph)))
        if not isinstance(check, dict):
            raise RuntimeError("native check_graph returned no evidence object")
        if check.get("verdict") not in {"green", "red"}:
            raise RuntimeError(
                "native check_graph infrastructure verdict: "
                + str(check.get("verdict"))
            )
        durable(native_path, {
            "schema": "and-or-native-preflight-evidence-v1",
            "attempt": attempt,
            "staging_run": str(staging_run),
            "proof_arm_native_calls": 0,
            "authoritative_package_payload_sha256": authoritative_hash,
            "target_view_package_payload_sha256": package[
                "package_payload_sha256"
            ],
            "problem_input": deepcopy(problem_input),
            "check_graph": check,
        }, immutable=True)
    except BaseException as error:
        primary_error = error
        if not native_path.exists():
            durable(native_path, {
                "schema": "and-or-native-preflight-evidence-v1",
                "attempt": attempt,
                "staging_run": str(staging_run),
                "proof_arm_native_calls": 0,
                "authoritative_package_payload_sha256": authoritative_hash,
                "target_view_package_payload_sha256": package[
                    "package_payload_sha256"
                ],
                "problem_input": deepcopy(problem_input),
                "infrastructure_error": (
                    f"{type(error).__name__}: {error}"
                ),
            }, immutable=True)
    finally:
        released = checker is None
        if checker is not None:
            try:
                checker.close_prefix()
                released = True
            except BaseException as error:
                cleanup_error = error
        resources_released_at = _utc_now()
        durable(cleanup_path, {
            "schema": "and-or-native-preflight-cleanup-v1",
            "attempt": attempt,
            "staging_run": str(staging_run),
            "proof_arm_native_calls": 0,
            "checker_acquired": checker is not None,
            "close_prefix_called": checker is not None,
            "released": released,
            "diagnostic": (
                None if cleanup_error is None else
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            ),
            "resources_released_at": resources_released_at,
        }, immutable=True)

    if cleanup_error is not None:
        raise RuntimeError(
            f"native staging cleanup failed: {cleanup_error}"
        ) from cleanup_error
    if primary_error is not None:
        raise primary_error
    assert check is not None
    verdict = "pass" if check["verdict"] == "green" else "reject"
    diagnostic = str(check.get("diagnostic") or "native declaration rejected")
    return make_preflight_receipt(
        verdict=verdict, checked_node_ids=closure,
        diagnostics=[] if verdict == "pass" else [diagnostic],
        artifact_path=native_path, cleanup_artifact_path=cleanup_path,
        graph=graph, package=package, target_contract_id=target_contract_id,
        model=model, reasoning_effort=reasoning_effort,
        staging_run=staging_run, proof_arm_run=proof_arm_run,
        started_at=started_at, finished_at=_utc_now(),
        wall_ms=(time.monotonic_ns() - started) // 1_000_000,
        native_calls=1, resources_released_at=resources_released_at,
    )


def _portable_preflight_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    """Remove mutable staging locators before embedding evidence in a problem."""
    portable = {
        key: deepcopy(value)
        for key, value in receipt.items()
        if key not in {
            "staging_run", "proof_arm_run", "native_evidence",
            "cleanup_evidence", "receipt_sha256",
        }
    }
    for field in ("native_evidence", "cleanup_evidence"):
        locator = receipt[field]
        portable[field] = {
            "sha256": locator["sha256"], "bytes": locator["bytes"],
        }
    portable["source_receipt_sha256"] = receipt["receipt_sha256"]
    portable["portable_receipt_sha256"] = canonical_sha256(portable)
    return portable


def _route(
    node: dict[str, Any], *, kind: str, requires: list[str],
    contract_route: dict[str, Any] | None,
) -> dict[str, Any]:
    guidance: dict[str, Any] = {
        "kind": (
            "direct-native-proof" if kind == "direct"
            else "prove-exact-dependencies-then-native-goal"
        ),
        "public_strategy": node.get("strategy", ""),
        "dependency_goal_ids": list(requires),
    }
    if contract_route is not None:
        guidance["file_planner_contract_route"] = deepcopy(contract_route)
    return {
        "id": f"route:{node['id']}:{kind}",
        "requires": list(requires),
        **deepcopy(ROUTE_COSTS[kind]),
        "planning_guidance": guidance,
    }


def build_contract_and_or_problem(
    graph: dict[str, Any], package: dict[str, Any], *,
    target_contract_id: str, model: str, reasoning_effort: str,
    proof_arm_run: Path, staging_run: Path,
    planning_recipe_artifact: dict[str, Any],
    native_preflight_receipt: dict[str, Any],
    transcript_validator: Callable[..., dict[str, Any]] = validate_file_planner_transcript,
    expected_prompt_sha256: str | None = None,
) -> dict[str, Any]:
    """Mechanically convert one validated DAG and two authority receipts."""
    package_hash = _verify_package(package)
    package_view = target_package_view(package, target_contract_id)
    view_hash = package_view["package_payload_sha256"]
    _verify_planning_recipe(
        planning_recipe_artifact,
        planning_recipe_artifact.get("recipe_sha256", ""),
    )
    if (
        planning_recipe_artifact.get("full_package_payload_sha256") != package_hash
        or planning_recipe_artifact.get("target_view_package_payload_sha256") != view_hash
        or planning_recipe_artifact.get("target_contract_id") != target_contract_id
        or planning_recipe_artifact.get("model") != model
        or planning_recipe_artifact.get("reasoning_effort") != reasoning_effort
    ):
        raise ValueError("planning recipe does not bind realized problem inputs")
    graph_hash = _verify_graph(graph, package_view)
    target = _target_contract(package, target_contract_id)
    closure = root_reachable_node_ids(graph, package_view, target_contract_id)
    transcript_receipt = transcript_validator(
        graph, model=model, reasoning_effort=reasoning_effort,
        expected_prompt_sha256=expected_prompt_sha256,
    )
    _validate_transcript_receipt(
        transcript_receipt, graph=graph, model=model,
        reasoning_effort=reasoning_effort,
        expected_prompt_sha256=expected_prompt_sha256,
    )
    proof_arm_run = proof_arm_run.resolve()
    staging_run = staging_run.resolve()
    treatment_root = proof_arm_run / "treatment-planning"
    for path_text in [
        transcript_receipt["transcript_prefix"],
        *(item["path"] for item in transcript_receipt["artifacts"].values()),
    ]:
        try:
            Path(path_text).resolve().relative_to(treatment_root)
        except ValueError as error:
            raise ValueError(
                "File Planner transcript is outside proof-arm treatment-planning"
            ) from error
    _validate_preflight_receipt(
        native_preflight_receipt, required_node_ids=closure, allow_reject=False,
        graph=graph, package=package_view, target_contract_id=target_contract_id,
        model=model, reasoning_effort=reasoning_effort,
        staging_run=staging_run, proof_arm_run=proof_arm_run,
    )

    by_id = {node["id"]: node for node in graph["nodes"]}
    goals = []
    for order, node_id in enumerate(closure):
        node = by_id[node_id]
        dependencies = list(node["dependencies"])
        contract_route = (
            graph.get("contract_routes", {}).get(node_id)
            if node["role"] == "contract" else None
        )
        routes = [_route(
            node, kind="direct", requires=[], contract_route=contract_route,
        )]
        if dependencies:
            routes.append(_route(
                node, kind="decomposed", requires=dependencies,
                contract_route=contract_route,
            ))
        goals.append({
            "id": node["id"],
            "name": node["name"],
            "role": node["role"],
            "statement": node["statement"],
            "context": list(node.get("context", [])),
            "strategy": node.get("strategy", ""),
            "order": order,
            "routes": routes,
        })

    problem: dict[str, Any] = {
        "schema_version": 1,
        "schema": PROBLEM_SCHEMA,
        "method": AND_OR_METHOD,
        "root_goal": target_contract_id,
        "goals": goals,
        "max_frontier_plans": 4096,
        "cost_policy": {
            "schema": COST_POLICY,
            "historically_fitted": False,
            "runtime_measurement": False,
            "purpose": "deterministic route ordering only",
            "constants": deepcopy(ROUTE_COSTS),
        },
        "acceptance_policy": {
            "root_requires_own_native_green": True,
            "accepted_dependencies_do_not_prove_parent": True,
            "probe_policy": "none",
        },
        "execution_binding_policy": {
            "arm_attestation_required": True,
            "arm_attestation_schema": "contract-and-or-arm-instance-v1",
            "arm_attestation_filename": "and-or-arm-attestation.json",
            "attestation_must_bind": "problem_payload_sha256",
            "proof_arm_must_be_fresh_from_shared_fixture": True,
            "planner_transcript_location": "proof-arm/treatment-planning",
            "native_preflight_location": "separate-fresh-staging-run",
            "proof_arm_native_calls_during_planning": 0,
        },
        "provenance": {
            "builder": PROBLEM_SCHEMA,
            "source_graph_payload_sha256": graph_hash,
            "source_full_package_payload_sha256": package_hash,
            "source_target_view_package_payload_sha256": view_hash,
            "target_contract_id": target_contract_id,
            "target_contract_semantic_sha256": target.get(
                "contract_semantic_sha256"
            ),
            "model": model,
            "reasoning_effort": reasoning_effort,
            "planning_recipe": deepcopy(planning_recipe_artifact),
            "checkpoint_sha256": planning_recipe_artifact[
                "checkpoint_sha256"
            ],
            "transcript": deepcopy(transcript_receipt),
            "native_declaration_preflight": _portable_preflight_receipt(
                native_preflight_receipt
            ),
            "root_reachable_node_ids": list(closure),
            "historical_proof_inputs_used": False,
            "proof_bodies_included": False,
        },
    }
    _assert_no_proof_or_history(problem["goals"], where="problem.goals")
    validate_problem(problem)
    problem["problem_payload_sha256"] = problem_payload_sha256(problem)
    return problem


def verify_contract_and_or_problem(
    problem: dict[str, Any], graph: dict[str, Any], package: dict[str, Any], *,
    target_contract_id: str,
) -> None:
    """Re-derive all mechanical fields except the already-bound authorities."""
    verify_problem_payload_sha256(problem)
    validate_problem(problem)
    package_hash = _verify_package(package)
    package_view = target_package_view(package, target_contract_id)
    graph_hash = _verify_graph(graph, package_view)
    closure = root_reachable_node_ids(graph, package_view, target_contract_id)
    provenance = problem.get("provenance", {})
    recipe = provenance.get("planning_recipe", {})
    _verify_planning_recipe(recipe, recipe.get("recipe_sha256", ""))
    if (
        problem.get("schema") != PROBLEM_SCHEMA
        or problem.get("root_goal") != target_contract_id
        or provenance.get("source_graph_payload_sha256") != graph_hash
        or provenance.get("source_full_package_payload_sha256") != package_hash
        or provenance.get("source_target_view_package_payload_sha256")
            != package_view["package_payload_sha256"]
        or provenance.get("root_reachable_node_ids") != closure
        or provenance.get("checkpoint_sha256") != recipe.get("checkpoint_sha256")
    ):
        raise ValueError("AND/OR problem provenance mismatch")
    if problem.get("cost_policy", {}).get("constants") != ROUTE_COSTS:
        raise ValueError("AND/OR problem cost constants changed")
    if problem.get("acceptance_policy") != {
        "root_requires_own_native_green": True,
        "accepted_dependencies_do_not_prove_parent": True,
        "probe_policy": "none",
    }:
        raise ValueError("AND/OR root acceptance policy changed")
    if problem.get("execution_binding_policy") != {
        "arm_attestation_required": True,
        "arm_attestation_schema": "contract-and-or-arm-instance-v1",
        "arm_attestation_filename": "and-or-arm-attestation.json",
        "attestation_must_bind": "problem_payload_sha256",
        "proof_arm_must_be_fresh_from_shared_fixture": True,
        "planner_transcript_location": "proof-arm/treatment-planning",
        "native_preflight_location": "separate-fresh-staging-run",
        "proof_arm_native_calls_during_planning": 0,
    }:
        raise ValueError("AND/OR arm-attestation policy changed")
    by_id = {node["id"]: node for node in graph["nodes"]}
    if [goal["id"] for goal in problem["goals"]] != closure:
        raise ValueError("AND/OR problem is not the exact target closure")
    for goal in problem["goals"]:
        node = by_id[goal["id"]]
        for field in ("id", "name", "role", "statement", "strategy"):
            if goal.get(field) != node.get(field, ""):
                raise ValueError(f"AND/OR goal changed File Planner field {field}")
        if goal.get("context") != node.get("context", []):
            raise ValueError("AND/OR goal changed File Planner context")
        expected_requires = list(node["dependencies"])
        expected_kinds = ["direct"] + (["decomposed"] if expected_requires else [])
        if [route["id"].rsplit(":", 1)[-1] for route in goal["routes"]] != expected_kinds:
            raise ValueError("AND/OR goal route set changed")
        for route, kind in zip(goal["routes"], expected_kinds):
            if route.get("requires") != ([] if kind == "direct" else expected_requires):
                raise ValueError("AND/OR route dependency set changed")
            for key, value in ROUTE_COSTS[kind].items():
                if route.get(key) != value:
                    raise ValueError("AND/OR route cost policy changed")


def focus_environment(
    package: dict[str, Any], target_contract_id: str,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Add a target focus without removing any fixed package contract."""
    target = _target_contract(package, target_contract_id)
    result = deepcopy(
        planning_environment(package) if environment is None else environment
    )
    if any(key in result for key in ("proofs", "proof_history", "execution_history")):
        raise ValueError("target planning environment contains proof/history state")
    result["and_or_target_focus"] = {
        "policy": FOCUS_POLICY,
        "target_contract_id": target_contract_id,
        "target_name": target["name"],
        "instruction": (
            "Plan exactly the selected fixed contract from this authoritative "
            "target-only package view. Make its generated dependency closure "
            "precise and executable; do not alter the contract or its context."
        ),
    }
    return result


def _expected_prompt_hash(
    package: dict[str, Any], environment: dict[str, Any],
    rejection_feedback: list[str],
) -> str:
    effective = deepcopy(environment)
    effective["retrieval_enabled"] = False
    return hashlib.sha256(planner_prompt(
        package, effective, rejection_feedback,
    ).encode("utf-8")).hexdigest()


def _transcript_prefixes(output_dir: Path) -> set[Path]:
    directory = output_dir / "llm"
    if not directory.is_dir():
        return set()
    suffixes = (
        ".prompt.txt", ".ndjson", ".final.txt", ".stderr.txt",
        ".meta.json", ".audit.json",
    )
    prefixes: set[Path] = set()
    for artifact in directory.rglob("*"):
        if not artifact.is_file():
            continue
        value = str(artifact.resolve())
        for suffix in suffixes:
            if value.endswith(suffix):
                prefixes.add(Path(value[:-len(suffix)]))
                break
    return prefixes


def _partial_transcript_inventory(prefix: Path) -> dict[str, Any]:
    """Bind every byte available even when a provider/planner call failed."""
    present = {}
    for name, path in sorted(artifact_paths(prefix).items()):
        if path.is_file():
            present[name] = {
                "path": str(path.resolve()),
                "sha256": _file_sha256(path),
                "bytes": path.stat().st_size,
            }
    meta_summary = {}
    meta_path = artifact_paths(prefix)["meta"]
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta_summary = {
                key: meta.get(key) for key in (
                    "state", "provider", "model", "reasoning_effort",
                    "stage", "wall_ms", "returncode", "timed_out",
                )
            }
        except (OSError, ValueError, AttributeError):
            meta_summary = {"state": "unreadable"}
    return {
        "transcript_prefix": str(prefix.resolve()),
        "meta": meta_summary,
        "artifacts": present,
        "complete_family": set(present) == {
            "prompt", "raw", "final", "stderr", "meta", "audit",
        },
    }


def _existing_attempt_receipts(
    output_dir: Path, *, identity: dict[str, Any], max_attempts: int,
) -> list[dict[str, Any]]:
    directory = output_dir / "and-or-planner-attempts"
    paths = sorted(directory.glob("attempt-*.json")) if directory.is_dir() else []
    if len(paths) > max_attempts:
        raise ValueError("planner restart found attempts beyond the frozen limit")
    result = []
    for expected, path in enumerate(paths, 1):
        if path.name != f"attempt-{expected:03d}.json":
            raise ValueError("planner restart found a non-contiguous attempt WAL")
        row = read_artifact(path)
        if (
            not isinstance(row, dict)
            or row.get("attempt") != expected
            or row.get("max_attempts") != max_attempts
            or row.get("receipt_path") != str(path.resolve())
            or row.get("receipt_sha256") != canonical_sha256(
                _without_hash(row, "receipt_sha256")
            )
        ):
            raise ValueError("planner restart attempt receipt replay mismatch")
        for field in (
            "full_package_payload_sha256",
            "target_view_package_payload_sha256",
            "plan_recipe_sha256",
            "proof_arm_run",
            "staging_run",
        ):
            if row.get(field) != identity[field]:
                raise ValueError("planner restart changed frozen attempt identity")
        if row.get("proof_arm_native_calls") != 0:
            raise ValueError("planner restart evidence opened the proof arm")
        result.append(row)
    return result


def _receipt_transcript_prefixes(
    attempts: list[dict[str, Any]],
) -> set[Path]:
    prefixes: set[Path] = set()
    for row in attempts:
        transcript = row.get("transcript")
        if isinstance(transcript, dict) and isinstance(
            transcript.get("transcript_prefix"), str,
        ):
            prefixes.add(Path(transcript["transcript_prefix"]).resolve())
        for call in row.get("provider_call_inventory", []):
            if isinstance(call, dict) and isinstance(
                call.get("transcript_prefix"), str,
            ):
                prefixes.add(Path(call["transcript_prefix"]).resolve())
    return prefixes


def _orphan_graph_inventory(
    output_dir: Path, attempts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    directory = output_dir / "and-or-planner-attempts"
    if not directory.is_dir():
        return []
    referenced = {
        str(Path(locator["path"]).resolve())
        for row in attempts
        if isinstance((locator := row.get("graph_artifact")), dict)
        and isinstance(locator.get("path"), str)
    }
    result = []
    for path in sorted(directory.glob("graph-attempt-*.json")):
        if str(path.resolve()) in referenced:
            continue
        result.append({
            "path": str(path.resolve()),
            "sha256": _file_sha256(path),
            "bytes": path.stat().st_size,
        })
    return result


def _write_attempt_receipt(
    output_dir: Path, receipt: dict[str, Any],
) -> dict[str, Any]:
    directory = output_dir / "and-or-planner-attempts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"attempt-{receipt['attempt']:03d}.json"
    normalized = deepcopy(receipt)
    normalized["receipt_path"] = str(path.resolve())
    normalized["receipt_sha256"] = canonical_sha256(normalized)
    durable(path, normalized, immutable=True)
    if read_artifact(path) != normalized:
        raise ValueError("immutable planner attempt receipt replay mismatch")
    return normalized


def _write_attempt_graph(
    output_dir: Path, attempt: int, graph: dict[str, Any],
) -> dict[str, Any]:
    directory = output_dir / "and-or-planner-attempts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"graph-attempt-{attempt:03d}.json"
    durable(path, graph, immutable=True)
    if read_artifact(path) != graph:
        raise ValueError("immutable planner attempt graph replay mismatch")
    return {
        "path": str(path.resolve()),
        "sha256": _file_sha256(path),
        "bytes": path.stat().st_size,
        "graph_payload_sha256": graph["graph_payload_sha256"],
    }


def _planning_accounting(
    attempts: list[dict[str, Any]], total_wall_ms: int,
    interrupted_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    raw_artifacts = []
    provider_wall_ms = 0
    provider_calls = 0
    native_receipts = []
    for row in attempts:
        transcript = row.get("transcript")
        if isinstance(transcript, dict):
            provider_calls += 1
            provider_wall_ms += transcript.get("wall_ms", 0)
            raw = transcript.get("artifacts", {}).get("raw")
            if raw is not None:
                raw_artifacts.append(raw)
        else:
            inventory = row.get("provider_call_inventory", [])
            provider_calls += len(inventory)
            for call in inventory:
                provider_wall_ms += call.get("meta", {}).get("wall_ms") or 0
                raw = call.get("artifacts", {}).get("raw")
                if raw is not None:
                    raw_artifacts.append(raw)
        if isinstance(row.get("native_preflight"), dict):
            native_receipts.append(row["native_preflight"])
    interrupted_calls = interrupted_calls or []
    for call in interrupted_calls:
        provider_calls += 1
        provider_wall_ms += call.get("meta", {}).get("wall_ms") or 0
        raw = call.get("artifacts", {}).get("raw")
        if raw is not None:
            raw_artifacts.append(raw)
    return {
        "charged_to_arm": "B",
        "model_calls": provider_calls,
        "orchestration_total_wall_ms": total_wall_ms,
        "planner_provider_wall_ms": provider_wall_ms,
        "native_preflight_wall_ms": sum(
            row["wall_ms"] for row in native_receipts
        ),
        "native_preflight_calls": sum(
            row["native_calls"] for row in native_receipts
        ),
        "proof_arm_native_calls_during_planning": 0,
        "interrupted_provider_calls": len(interrupted_calls),
        "staging_cleanup_verified": (
            all(row.get("cleanup_verified") is True for row in native_receipts)
            if native_receipts else None
        ),
        "attempt_receipts": deepcopy(attempts),
        "token_accounting": {
            "required": True,
            "source": "bound raw transcript artifacts",
            "raw_artifact_paths": [item["path"] for item in raw_artifacts],
            "raw_artifact_sha256": [item["sha256"] for item in raw_artifacts],
        },
    }


def _write_planning_result(
    output_dir: Path, result: dict[str, Any],
) -> dict[str, Any]:
    path = output_dir / "and-or-planning-result.json"
    normalized = deepcopy(result)
    normalized["result_path"] = str(path.resolve())
    normalized["result_sha256"] = canonical_sha256(normalized)
    durable(path, normalized, immutable=True)
    if read_artifact(path) != normalized:
        raise ValueError("immutable planning result replay mismatch")
    return normalized


def _write_realized_problem(
    output_dir: Path, problem: dict[str, Any],
) -> dict[str, Any]:
    path = output_dir / "realized-and-or-problem.json"
    durable(path, problem, immutable=True)
    if read_artifact(path) != problem:
        raise ValueError("immutable realized problem replay mismatch")
    return {
        "path": str(path.resolve()),
        "sha256": _file_sha256(path),
        "bytes": path.stat().st_size,
        "problem_payload_sha256": problem["problem_payload_sha256"],
    }


def _failure_result(
    *, output_dir: Path, status: str, failure_type: str,
    attempts: list[dict[str, Any]], total_wall_ms: int,
    max_attempts: int, diagnostic: str, identity: dict[str, Any],
    interrupted_call_inventory: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    interrupted_call_inventory = interrupted_call_inventory or []
    result = {
        "schema": ORCHESTRATION_SCHEMA,
        "status": status,
        "failure_type": failure_type,
        "diagnostic": diagnostic,
        "identity": deepcopy(identity),
        "frozen_limits": {"max_attempts": max_attempts},
        "planning_accounting": _planning_accounting(
            attempts, total_wall_ms, interrupted_call_inventory,
        ),
    }
    if interrupted_call_inventory:
        result["interrupted_call_inventory"] = deepcopy(
            interrupted_call_inventory
        )
    return _write_planning_result(output_dir, result)


def _verify_file_locator(
    locator: dict[str, Any], *, allowed_root: Path | None = None,
) -> Path:
    if (
        not isinstance(locator, dict)
        or not isinstance(locator.get("path"), str)
        or not _is_sha256(locator.get("sha256"))
        or not isinstance(locator.get("bytes"), int)
        or isinstance(locator.get("bytes"), bool)
        or locator["bytes"] < 0
    ):
        raise ValueError("invalid evidence file locator")
    path = Path(locator["path"]).resolve()
    if allowed_root is not None:
        try:
            path.relative_to(allowed_root.resolve())
        except ValueError as error:
            raise ValueError("evidence locator escapes its allowed root") from error
    if (
        not path.is_file() or path.stat().st_size != locator["bytes"]
        or _file_sha256(path) != locator["sha256"]
    ):
        raise ValueError("evidence file locator hash mismatch")
    return path


def verify_planning_result(
    result_path: Path, *, package: dict[str, Any], target_contract_id: str,
    frozen_recipe: dict[str, Any], proof_arm_run: Path, staging_run: Path,
) -> dict[str, Any]:
    """Fully replay a B-arm planning outcome for comparison aggregation."""
    result_path = result_path.resolve()
    proof_arm_run = proof_arm_run.resolve()
    staging_run = staging_run.resolve()
    expected_result_path = (
        proof_arm_run / "treatment-planning" / "and-or-planning-result.json"
    )
    if result_path != expected_result_path or not result_path.is_file():
        raise ValueError("planning result is outside the B treatment directory")
    result = read_artifact(result_path)
    if (
        not isinstance(result, dict)
        or result.get("schema") != ORCHESTRATION_SCHEMA
        or result.get("result_path") != str(result_path)
        or result.get("result_sha256") != canonical_sha256(
            _without_hash(result, "result_sha256")
        )
    ):
        raise ValueError("planning result identity/hash mismatch")
    _verify_planning_recipe(
        frozen_recipe, frozen_recipe.get("recipe_sha256", ""),
    )
    full_hash = _verify_package(package)
    package_view = target_package_view(package, target_contract_id)
    identity = {
        "target_contract_id": target_contract_id,
        "full_package_payload_sha256": full_hash,
        "target_view_package_payload_sha256":
            package_view["package_payload_sha256"],
        "proof_arm_run": str(proof_arm_run),
        "staging_run": str(staging_run),
        "checkpoint_sha256": frozen_recipe["checkpoint_sha256"],
        "plan_recipe_sha256": frozen_recipe["recipe_sha256"],
    }
    if result.get("identity") != identity:
        raise ValueError("planning result differs from frozen recipe identity")
    max_attempts = frozen_recipe["max_attempts"]
    if result.get("frozen_limits") != {"max_attempts": max_attempts}:
        raise ValueError("planning result changed frozen attempt limit")
    accounting = result.get("planning_accounting")
    if not isinstance(accounting, dict):
        raise ValueError("planning result lacks accounting")
    attempts = accounting.get("attempt_receipts")
    if (
        not isinstance(attempts, list) or len(attempts) > max_attempts
        or [row.get("attempt") for row in attempts]
            != list(range(1, len(attempts) + 1))
    ):
        raise ValueError("planning attempt sequence is invalid")
    for attempt, row in enumerate(attempts, 1):
        if (
            not isinstance(row, dict)
            or row.get("max_attempts") != max_attempts
            or row.get("receipt_sha256") != canonical_sha256(
                _without_hash(row, "receipt_sha256")
            )
        ):
            raise ValueError("planning attempt receipt hash mismatch")
        receipt_path = (
            proof_arm_run / "treatment-planning"
            / "and-or-planner-attempts" / f"attempt-{attempt:03d}.json"
        ).resolve()
        if row.get("receipt_path") != str(receipt_path):
            raise ValueError("planning attempt receipt locator mismatch")
        if read_artifact(receipt_path) != row:
            raise ValueError("planning attempt receipt replay mismatch")
        if (
            row.get("full_package_payload_sha256") != full_hash
            or row.get("target_view_package_payload_sha256")
                != package_view["package_payload_sha256"]
            or row.get("plan_recipe_sha256") != frozen_recipe["recipe_sha256"]
            or row.get("proof_arm_native_calls") != 0
        ):
            raise ValueError("planning attempt changed frozen identity")

        graph = None
        graph_locator = row.get("graph_artifact")
        if graph_locator is not None:
            graph_path = _verify_file_locator(
                graph_locator, allowed_root=proof_arm_run / "treatment-planning",
            )
            graph = read_artifact(graph_path)
            if (
                graph_locator.get("graph_payload_sha256")
                    != graph.get("graph_payload_sha256")
                or row.get("graph_payload_sha256")
                    != graph.get("graph_payload_sha256")
            ):
                raise ValueError("attempt graph locator identity mismatch")
            _verify_graph(graph, package_view)

        transcript = row.get("transcript")
        if transcript is not None:
            if graph is None:
                raise ValueError("accepted planner transcript lacks attempt graph")
            _validate_transcript_receipt(
                transcript, graph=graph, model=frozen_recipe["model"],
                reasoning_effort=frozen_recipe["reasoning_effort"],
                expected_prompt_sha256=row.get("expected_prompt_sha256"),
            )
            for locator in transcript["artifacts"].values():
                _verify_file_locator(
                    locator, allowed_root=proof_arm_run / "treatment-planning",
                )
        for call in row.get("provider_call_inventory", []):
            for locator in call.get("artifacts", {}).values():
                _verify_file_locator(
                    locator, allowed_root=proof_arm_run / "treatment-planning",
                )
        for locator in row.get("orphan_graph_artifacts", []):
            _verify_file_locator(
                locator, allowed_root=proof_arm_run / "treatment-planning",
            )
        preflight = row.get("native_preflight")
        if preflight is not None:
            if graph is None:
                raise ValueError("native preflight lacks its attempt graph")
            _validate_preflight_receipt(
                preflight,
                required_node_ids=root_reachable_node_ids(
                    graph, package_view, target_contract_id,
                ),
                allow_reject=True, graph=graph, package=package_view,
                target_contract_id=target_contract_id,
                model=frozen_recipe["model"],
                reasoning_effort=frozen_recipe["reasoning_effort"],
                staging_run=staging_run, proof_arm_run=proof_arm_run,
            )

    interrupted_calls = result.get("interrupted_call_inventory", [])
    if not isinstance(interrupted_calls, list):
        raise ValueError("invalid interrupted provider-call inventory")
    for call in interrupted_calls:
        for locator in call.get("artifacts", {}).values():
            _verify_file_locator(
                locator, allowed_root=proof_arm_run / "treatment-planning",
            )
    replayed_accounting = _planning_accounting(
        attempts, accounting.get("orchestration_total_wall_ms"),
        interrupted_calls,
    )
    if accounting != replayed_accounting:
        raise ValueError("planning accounting does not replay from attempts")
    status = result.get("status")
    if status == "accepted":
        problem, graph = result.get("problem"), result.get("graph")
        if not isinstance(problem, dict) or not isinstance(graph, dict):
            raise ValueError("accepted planning result lacks problem/graph")
        problem_path = _verify_file_locator(
            result.get("realized_problem_artifact"),
            allowed_root=proof_arm_run / "treatment-planning",
        )
        problem_locator = result["realized_problem_artifact"]
        if (
            result.get("realized_problem_sha256")
                != problem.get("problem_payload_sha256")
            or problem_locator.get("problem_payload_sha256")
                != problem.get("problem_payload_sha256")
            or read_artifact(problem_path) != problem
            or problem.get("provenance", {}).get("planning_recipe")
                != frozen_recipe
            or str(staging_run) in canonical_json(problem)
        ):
            raise ValueError("realized problem binding/path isolation mismatch")
        verify_contract_and_or_problem(
            problem, graph, package, target_contract_id=target_contract_id,
        )
    elif status == "treatment_planning_failure":
        if result.get("failure_type") != "bounded_attempts_exhausted":
            raise ValueError("unknown treatment planning failure")
        if len(attempts) != max_attempts or "problem" in result:
            raise ValueError("invalid exhausted treatment planning result")
    elif status == "infrastructure_failure":
        if "problem" in result or not result.get("failure_type"):
            raise ValueError("invalid infrastructure planning result")
    else:
        raise ValueError("unknown planning result status")
    return result


def plan_target_problem(
    package: dict[str, Any], *, target_contract_id: str, run_id: str,
    output_dir: Path, proof_arm_run: Path, staging_run: Path,
    native_preflight: Callable[..., dict[str, Any]],
    checkpoint_sha256: str, frozen_plan_recipe_sha256: str,
    provider: str = "codex", model: str = "gpt-6-astra",
    reasoning_effort: str = "medium", timeout: int = 1800,
    max_attempts: int = 3, environment: dict[str, Any] | None = None,
    graph_bindings: dict[str, Any] | None = None,
    module_log_path: Path | None = None,
    plan_file_fn: Callable[..., dict[str, Any]] = plan_file,
    transcript_validator: Callable[..., dict[str, Any]] = validate_file_planner_transcript,
    accounting_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Plan, preflight and convert one target with bounded corrections.

    An authoritative target-only view, hash-bound to the complete frozen
    package, is passed to every ``plan_file`` call.
    Retrieval is intentionally disabled, so no hidden observation/model calls
    can evade the returned transcript inventory.  Every attempt receipt is
    written before the next attempt and optionally delivered to an outer
    accounting sink.  The sink/returned inventory is the authority for charging
    B-arm planner wall time and token usage from the bound raw transcripts.
    """
    full_package_hash = _verify_package(package)
    _target_contract(package, target_contract_id)
    package_view = target_package_view(package, target_contract_id)
    target_view_hash = package_view["package_payload_sha256"]
    recipe = planning_recipe(
        package, target_contract_id=target_contract_id,
        checkpoint_sha256=checkpoint_sha256, provider=provider,
        model=model, reasoning_effort=reasoning_effort, timeout=timeout,
        max_attempts=max_attempts,
    )
    _verify_planning_recipe(recipe, frozen_plan_recipe_sha256)
    if (
        not isinstance(max_attempts, int) or isinstance(max_attempts, bool)
        or max_attempts < 1
    ):
        raise ValueError("max_attempts must be a positive integer")
    if provider != "codex":
        raise ValueError("target AND/OR experiment currently requires audited Codex")
    if not callable(native_preflight):
        raise ValueError("a native declaration-preflight adapter is required")

    output_dir = output_dir.resolve()
    proof_arm_run = proof_arm_run.resolve()
    staging_run = staging_run.resolve()
    if output_dir != proof_arm_run / "treatment-planning":
        raise ValueError(
            "File Planner output must be the final proof arm's treatment-planning directory"
        )
    if proof_arm_run == staging_run or (
        staging_run.is_relative_to(proof_arm_run)
        or proof_arm_run.is_relative_to(staging_run)
    ):
        raise ValueError("proof arm and native staging run must be disjoint")
    proof_arm_run.mkdir(parents=True, exist_ok=True)
    staging_run.mkdir(parents=True, exist_ok=True)
    identity = {
        "target_contract_id": target_contract_id,
        "full_package_payload_sha256": full_package_hash,
        "target_view_package_payload_sha256": target_view_hash,
        "proof_arm_run": str(proof_arm_run),
        "staging_run": str(staging_run),
        "checkpoint_sha256": checkpoint_sha256,
        "plan_recipe_sha256": frozen_plan_recipe_sha256,
    }
    focused = focus_environment(package_view, target_contract_id, environment)
    result_path = output_dir / "and-or-planning-result.json"
    if result_path.exists():
        return verify_planning_result(
            result_path, package=package,
            target_contract_id=target_contract_id,
            frozen_recipe=recipe, proof_arm_run=proof_arm_run,
            staging_run=staging_run,
        )

    prior_attempts = _existing_attempt_receipts(
        output_dir, identity=identity, max_attempts=max_attempts,
    )
    known_prefixes = _receipt_transcript_prefixes(prior_attempts)
    orphan_prefixes = sorted(
        _transcript_prefixes(output_dir) - known_prefixes, key=str,
    )
    orphan_calls = [
        _partial_transcript_inventory(prefix) for prefix in orphan_prefixes
    ]
    orphan_graphs = _orphan_graph_inventory(output_dir, prior_attempts)
    if prior_attempts or orphan_calls or orphan_graphs:
        attempts = list(prior_attempts)
        unassigned_calls: list[dict[str, Any]] = []
        partial_wall_ms = sum(
            (call.get("meta", {}).get("wall_ms") or 0)
            for call in orphan_calls
        )
        if (orphan_calls or orphan_graphs) and len(attempts) < max_attempts:
            attempt = len(attempts) + 1
            interrupted = _write_attempt_receipt(output_dir, {
                "schema": ORCHESTRATION_SCHEMA,
                "attempt": attempt,
                "max_attempts": max_attempts,
                "status": "interrupted-planning",
                "wall_ms": partial_wall_ms,
                "started_at": _utc_now(),
                "finished_at": _utc_now(),
                "full_package_payload_sha256": full_package_hash,
                "target_view_package_payload_sha256": target_view_hash,
                "plan_recipe_sha256": frozen_plan_recipe_sha256,
                "diagnostic": (
                    "restart found an unclosed planner call/artifact; "
                    "the request is not replayed"
                ),
                "rejection_feedback": [],
                "provider_call_inventory": deepcopy(orphan_calls),
                "orphan_graph_artifacts": deepcopy(orphan_graphs),
                "proof_arm_run": str(proof_arm_run),
                "staging_run": str(staging_run),
                "proof_arm_native_calls": 0,
                "charged_to_arm": "B",
            })
            attempts.append(interrupted)
            if accounting_sink is not None:
                accounting_sink(deepcopy(interrupted))
        else:
            # An already exhausted WAL cannot gain another attempt number.  Its
            # orphan transcript bytes remain separately hash-bound and charged.
            unassigned_calls = orphan_calls
        total_wall_ms = sum(row.get("wall_ms", 0) for row in attempts)
        if unassigned_calls:
            total_wall_ms += partial_wall_ms
        return _failure_result(
            output_dir=output_dir, status="infrastructure_failure",
            failure_type="interrupted_planning",
            attempts=attempts, total_wall_ms=total_wall_ms,
            max_attempts=max_attempts,
            diagnostic=(
                "planning WAL has no authoritative final result; preserved "
                f"{len(orphan_calls)} partial provider call(s) and "
                f"{len(orphan_graphs)} unclosed graph artifact(s)"
            ),
            identity=identity,
            interrupted_call_inventory=unassigned_calls,
        )

    feedback: list[str] = []
    attempts: list[dict[str, Any]] = []
    total_wall_ms = 0

    for attempt in range(1, max_attempts + 1):
        expected_prompt = _expected_prompt_hash(package_view, focused, feedback)
        before_prefixes = _transcript_prefixes(output_dir)
        started_at = _utc_now()
        started = time.monotonic_ns()
        try:
            graph = plan_file_fn(
                deepcopy(package_view), run_id=run_id, output_dir=output_dir,
                provider=provider, model=model, timeout=timeout,
                environment=deepcopy(focused), revision=attempt,
                module_log_path=module_log_path, call_index=attempt,
                rejection_feedback=list(feedback),
                graph_bindings=deepcopy(graph_bindings or {}),
                persist_graph=True, premise_database=None,
                generated_records=None, symbol_resolver=None,
            )
        except Exception as error:
            elapsed_ms = (time.monotonic_ns() - started) // 1_000_000
            total_wall_ms += elapsed_ms
            new_prefixes = sorted(
                _transcript_prefixes(output_dir) - before_prefixes,
                key=str,
            )
            correctable = isinstance(error, (ValueError, TypeError, KeyError))
            diagnostic = f"{type(error).__name__}: {error}"
            receipt = _write_attempt_receipt(output_dir, {
                "schema": ORCHESTRATION_SCHEMA,
                "attempt": attempt,
                "max_attempts": max_attempts,
                "status": (
                    "planner-rejected" if correctable
                    else "planner-infrastructure-failure"
                ),
                "wall_ms": elapsed_ms,
                "started_at": started_at,
                "finished_at": _utc_now(),
                "expected_prompt_sha256": expected_prompt,
                "full_package_payload_sha256": full_package_hash,
                "target_view_package_payload_sha256": target_view_hash,
                "plan_recipe_sha256": frozen_plan_recipe_sha256,
                "diagnostic": diagnostic,
                "rejection_feedback": list(feedback),
                "provider_call_inventory": [
                    _partial_transcript_inventory(prefix)
                    for prefix in new_prefixes
                ],
                "proof_arm_run": str(proof_arm_run),
                "staging_run": str(staging_run),
                "proof_arm_native_calls": 0,
                "charged_to_arm": "B",
            })
            attempts.append(receipt)
            if accounting_sink is not None:
                accounting_sink(deepcopy(receipt))
            if not correctable:
                return _failure_result(
                    output_dir=output_dir, status="infrastructure_failure",
                    failure_type="planner_transport_or_container_failure",
                    attempts=attempts, total_wall_ms=total_wall_ms,
                    max_attempts=max_attempts, diagnostic=diagnostic,
                    identity=identity,
                )
            feedback.append(diagnostic)
            continue

        new_prefixes = sorted(
            _transcript_prefixes(output_dir) - before_prefixes, key=str,
        )
        graph_artifact = None
        graph_hash = None
        try:
            _verify_package(package)
            _verify_package(package_view)
            graph_hash = _verify_graph(graph, package_view)
            graph_artifact = _write_attempt_graph(
                output_dir, attempt, graph,
            )
            closure = root_reachable_node_ids(
                graph, package_view, target_contract_id,
            )
            transcript = transcript_validator(
                graph, model=model, reasoning_effort=reasoning_effort,
                expected_prompt_sha256=expected_prompt,
            )
            _validate_transcript_receipt(
                transcript, graph=graph, model=model,
                reasoning_effort=reasoning_effort,
                expected_prompt_sha256=expected_prompt,
            )
            if plan_file_fn is plan_file:
                selected_prefix = Path(
                    transcript["transcript_prefix"]
                ).resolve()
                if selected_prefix not in new_prefixes:
                    raise ValueError(
                        "File Planner selected a transcript outside this attempt"
                    )
            preflight = native_preflight(
                graph=deepcopy(graph), package=deepcopy(package_view),
                authoritative_package=deepcopy(package),
                target_contract_id=target_contract_id,
                required_node_ids=list(closure), attempt=attempt,
                output_dir=staging_run / "and-or-native-preflight",
                staging_run=staging_run, proof_arm_run=proof_arm_run,
                problem_input=_preflight_input_binding(
                    graph, package_view, target_contract_id=target_contract_id,
                    required_node_ids=closure, model=model,
                    reasoning_effort=reasoning_effort,
                ),
                timeout=timeout,
            )
            _validate_preflight_receipt(
                preflight, required_node_ids=closure, allow_reject=True,
                graph=graph, package=package_view,
                target_contract_id=target_contract_id,
                model=model, reasoning_effort=reasoning_effort,
                staging_run=staging_run, proof_arm_run=proof_arm_run,
            )
        except Exception as error:
            elapsed_ms = (time.monotonic_ns() - started) // 1_000_000
            total_wall_ms += elapsed_ms
            receipt = _write_attempt_receipt(output_dir, {
                "schema": ORCHESTRATION_SCHEMA,
                "attempt": attempt,
                "max_attempts": max_attempts,
                "status": "authority-invalid",
                "wall_ms": elapsed_ms,
                "started_at": started_at,
                "finished_at": _utc_now(),
                "expected_prompt_sha256": expected_prompt,
                "full_package_payload_sha256": full_package_hash,
                "target_view_package_payload_sha256": target_view_hash,
                "plan_recipe_sha256": frozen_plan_recipe_sha256,
                "diagnostic": f"{type(error).__name__}: {error}",
                "rejection_feedback": list(feedback),
                "provider_call_inventory": [
                    _partial_transcript_inventory(prefix)
                    for prefix in new_prefixes
                ],
                "graph_artifact": deepcopy(graph_artifact),
                "graph_payload_sha256": graph_hash,
                "proof_arm_run": str(proof_arm_run),
                "staging_run": str(staging_run),
                "proof_arm_native_calls": 0,
                "charged_to_arm": "B",
            })
            attempts.append(receipt)
            if accounting_sink is not None:
                accounting_sink(deepcopy(receipt))
            failure_type = (
                "staging_cleanup_failure"
                if "cleanup" in str(error).lower()
                else "authority_or_native_infrastructure_failure"
            )
            return _failure_result(
                output_dir=output_dir, status="infrastructure_failure",
                failure_type=failure_type, attempts=attempts,
                total_wall_ms=total_wall_ms, max_attempts=max_attempts,
                diagnostic=f"{type(error).__name__}: {error}",
                identity=identity,
            )

        elapsed_ms = (time.monotonic_ns() - started) // 1_000_000
        total_wall_ms += elapsed_ms
        status = (
            "accepted" if preflight["verdict"] == "pass"
            else "native-preflight-rejected"
        )
        attempt_receipt = _write_attempt_receipt(output_dir, {
            "schema": ORCHESTRATION_SCHEMA,
            "attempt": attempt,
            "max_attempts": max_attempts,
            "status": status,
            "wall_ms": elapsed_ms,
            "started_at": started_at,
            "finished_at": _utc_now(),
            "provider_wall_ms": transcript["wall_ms"],
            "expected_prompt_sha256": expected_prompt,
            "rejection_feedback": list(feedback),
            "full_package_payload_sha256": full_package_hash,
            "target_view_package_payload_sha256": target_view_hash,
            "plan_recipe_sha256": frozen_plan_recipe_sha256,
            "graph_payload_sha256": graph_hash,
            "graph_artifact": graph_artifact,
            "transcript": transcript,
            "provider_call_inventory": [
                _partial_transcript_inventory(prefix)
                for prefix in new_prefixes
            ],
            "native_preflight": preflight,
            "proof_arm_run": str(proof_arm_run),
            "staging_run": str(staging_run),
            "proof_arm_native_calls": 0,
            "charged_to_arm": "B",
        })
        attempts.append(attempt_receipt)
        if accounting_sink is not None:
            accounting_sink(deepcopy(attempt_receipt))
        if preflight["verdict"] == "reject":
            feedback.extend(preflight["diagnostics"])
            continue
        problem = build_contract_and_or_problem(
            graph, package, target_contract_id=target_contract_id,
            model=model, reasoning_effort=reasoning_effort,
            proof_arm_run=proof_arm_run, staging_run=staging_run,
            planning_recipe_artifact=recipe,
            native_preflight_receipt=preflight,
            transcript_validator=lambda *args, **kwargs: deepcopy(transcript),
            expected_prompt_sha256=expected_prompt,
        )
        problem["provenance"]["planning_orchestration"] = {
            "schema": ORCHESTRATION_SCHEMA,
            "attempt_receipt_sha256": [
                row["receipt_sha256"] for row in attempts
            ],
            "attempts_sha256": canonical_sha256([
                row["receipt_sha256"] for row in attempts
            ]),
            "all_calls_charged_to_arm": "B",
        }
        problem["problem_payload_sha256"] = problem_payload_sha256(problem)
        problem_artifact = _write_realized_problem(output_dir, problem)
        result = {
            "schema": ORCHESTRATION_SCHEMA,
            "status": "accepted",
            "identity": identity,
            "frozen_limits": {"max_attempts": max_attempts},
            "realized_problem_sha256": problem["problem_payload_sha256"],
            "realized_problem_artifact": problem_artifact,
            "problem": problem,
            "graph": graph,
            "planning_accounting": _planning_accounting(
                attempts, total_wall_ms,
            ),
        }
        return _write_planning_result(output_dir, result)

    return _failure_result(
        output_dir=output_dir, status="treatment_planning_failure",
        failure_type="bounded_attempts_exhausted", attempts=attempts,
        total_wall_ms=total_wall_ms, max_attempts=max_attempts,
        diagnostic=(
            f"target File Planner exhausted {max_attempts} bounded attempts"
        ),
        identity=identity,
    )


__all__ = [
    "COST_POLICY", "FOCUS_POLICY", "ORCHESTRATION_SCHEMA",
    "PREFLIGHT_RECEIPT_SCHEMA", "PROBLEM_SCHEMA", "ROUTE_COSTS",
    "TRANSCRIPT_RECEIPT_SCHEMA", "TargetPlanningExhausted",
    "build_contract_and_or_problem", "canonical_sha256", "focus_environment",
    "make_preflight_receipt", "plan_target_problem", "problem_payload_sha256",
    "root_reachable_node_ids", "validate_file_planner_transcript",
    "verify_contract_and_or_problem", "verify_problem_payload_sha256",
]
