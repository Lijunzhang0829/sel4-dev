#!/usr/bin/env python3
"""Session Runner for the modular Refine theorem-generation pipeline."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from regen.planning.file_planner import plan_file, planning_environment
from regen.infrastructure.module_run_log import attach_module_outputs
from regen.execution.assembly_stabilizer import prepare_assembly_stabilization
from regen.preparation.environment_gate import payload_sha256, sha256_path, validate_environment_gate
from regen.execution.graph_revision_agent import (
    normalized_retry_directive, prior_node_decisions, retry_directive_sha256,
    revise_graph, validate_decision,
)
from regen.execution.isabelle_node_checker import IsabelleChecker, dependency_closure
from regen.execution.lemma_prover import prove_node
from regen.infrastructure.progress_events import (
    close_event_monitor, configure_event_monitor, emit_event, short_node,
)
from regen.planning.proof_graph import (
    definitional_wrapper_risks, executable_nodes, validate_graph,
    verify_graph_payload_sha256, write_immutable_graph,
)
from regen.analysis.retained_semantic_impact import audit_theory


_MUTATION_LOCK_STATE = threading.local()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def acquire_run_mutation_lock(
    run_dir: Path, *, operation: str, theory: str | None = None,
):
    """Serialize every mutation of one run and its canonical session ledger."""
    key = str(run_dir.resolve())
    held = getattr(_MUTATION_LOCK_STATE, "held", {})
    existing = held.get(key)
    if existing is not None:
        if existing["operation"] != "run-session" or operation != "run-file":
            raise RuntimeError(
                f"another runner owns mutable run state in {run_dir}; "
                f"owner={existing['owner']}"
            )
        existing["depth"] += 1
        return {"key": key, "nested": True}
    lock_dir = run_dir / ".runner-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "session-mutation.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.seek(0)
        owner = handle.read().strip() or "unknown"
        handle.close()
        raise RuntimeError(
            f"another runner owns mutable run state in {run_dir}; owner={owner}"
        )
    handle.seek(0)
    handle.truncate()
    owner = {
        "pid": os.getpid(),
        "operation": operation,
        "theory": theory,
        "acquired_at": datetime.now(timezone.utc).isoformat(),
    }
    handle.write(json.dumps(owner, ensure_ascii=False, sort_keys=True) + "\n")
    handle.flush()
    held[key] = {
        "handle": handle, "depth": 1, "operation": operation,
        "owner": json.dumps(owner, ensure_ascii=False, sort_keys=True),
    }
    _MUTATION_LOCK_STATE.held = held
    return {"key": key, "nested": False}


def release_run_mutation_lock(token) -> None:
    held = getattr(_MUTATION_LOCK_STATE, "held", {})
    state = held.get(token["key"])
    if state is None:
        raise RuntimeError("run mutation lock is not held by this thread")
    state["depth"] -= 1
    if state["depth"] > 0:
        return
    handle = state["handle"]
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()
    del held[token["key"]]


def latest_graph_path(theory_dir: Path) -> Path | None:
    candidates: list[tuple[int, Path]] = []
    for path in theory_dir.glob("graph-v*.json"):
        match = re.fullmatch(r"graph-v(\d+)\.json", path.name)
        if match:
            candidates.append((int(match.group(1)), path))
    return max(candidates, default=(0, None), key=lambda item: item[0])[1]


def next_graph_decision_index(theory_dir: Path) -> int:
    """Allocate after every prior request/transcript, including partial calls."""
    indices: list[int] = []
    for path in (theory_dir / "graph-revisions").glob("decision-*"):
        match = re.fullmatch(r"decision-(\d+)", path.name)
        if match:
            indices.append(int(match.group(1)))
    for path in (theory_dir / "llm").glob("graph-decision-*-from-v*.meta.json"):
        match = re.match(r"graph-decision-(\d+)-", path.name)
        if match:
            indices.append(int(match.group(1)))
    return max(indices, default=0) + 1


def latest_incomplete_graph_request(
    theory_dir: Path, graph_revision: int,
) -> dict[str, Any] | None:
    """Recover a GRA request whose provider call ended before a decision."""
    candidates: list[tuple[int, Path]] = []
    for path in (theory_dir / "graph-revisions").glob("decision-*"):
        match = re.fullmatch(r"decision-(\d+)", path.name)
        if match:
            candidates.append((int(match.group(1)), path))
    for _, directory in sorted(candidates, reverse=True):
        request_path = directory / "request.json"
        if not request_path.is_file() or (directory / "decision.json").exists():
            continue
        request = read_json(request_path)
        if request.get("current_graph", {}).get("revision") != graph_revision:
            continue
        request["interrupted_decision_id"] = directory.name
        return request
    return None


def next_node_round_id(node_dir: Path, recorded_round: int = 0) -> int:
    """Allocate after the highest prior round, even when directories have gaps."""
    prior = [recorded_round]
    for path in node_dir.glob("round-*"):
        match = re.fullmatch(r"round-(\d+)", path.name)
        if match:
            prior.append(int(match.group(1)))
    return max(prior) + 1


def node_log_directory(theory_dir: Path, node_id: str) -> Path:
    """Use the same node directory spelling as Lemma Prover."""
    return theory_dir / "nodes" / node_id.replace("/", "_").replace(":", "_")


def active_graph_path(entry: dict[str, Any], theory_dir: Path) -> Path | None:
    """Use the ledger pointer; highest-version scanning is migration fallback."""
    source = entry.get("graph_source")
    if source:
        path = Path(source)
        if not path.is_absolute():
            path = REPO / path
        if path.is_file():
            return path
    return latest_graph_path(theory_dir)


def prior_certified_graph(
    theory_dir: Path, revision: int,
) -> tuple[int, dict[str, Any]] | None:
    """Find the newest earlier graph with an Isabelle-green shell check."""
    for candidate in range(revision - 1, 0, -1):
        check_path = theory_dir / f"graph-check-v{candidate}.json"
        graph_path = theory_dir / f"graph-v{candidate}.json"
        if not check_path.is_file() or not graph_path.is_file():
            continue
        check = read_json(check_path)
        if check.get("verdict") == "green":
            return candidate, read_json(graph_path)
    return None


def statement_delta_graph(
    graph: dict[str, Any], base: dict[str, Any],
) -> dict[str, Any]:
    """Return declarations not covered by an earlier green graph check."""
    base_nodes = {node["id"]: node for node in base["nodes"]}
    changed_ids = {
        node["id"] for node in graph["nodes"]
        if node["id"] not in base_nodes
        or node["statement"] != base_nodes[node["id"]]["statement"]
        or node.get("context", []) != base_nodes[node["id"]].get("context", [])
    }
    return {
        **graph,
        "nodes": [
            node for node in graph["nodes"] if node["id"] in changed_ids
        ],
        "execution_order": [
            node_id for node_id in graph["execution_order"]
            if node_id in changed_ids
        ],
    }


def check_statement_delta(
    checker: IsabelleChecker, theory_dir: Path, graph: dict[str, Any],
) -> dict[str, Any] | None:
    """Elaborate only statements not covered by a prior green graph shell."""
    prior = prior_certified_graph(theory_dir, graph["revision"])
    if prior is None:
        return None
    base_revision, base_graph = prior
    delta_graph = statement_delta_graph(graph, base_graph)
    if delta_graph["nodes"]:
        result = checker.check_graph(delta_graph)
    else:
        result = {
            "verdict": "green",
            "returncode": 0,
            "diagnostic": "No changed declarations to elaborate.",
            "failed_node_id": None,
            "source": None,
            "log": None,
            "wall_ms": 0,
        }
    result["certification_mode"] = "delta"
    result["base_graph_revision"] = base_revision
    result["checked_node_ids"] = delta_graph["execution_order"]
    return result


def credit_structural_evidence_round(
    proof_rounds_by_node: dict[str, int], node_id: str,
) -> None:
    """Charge a structural-evidence round to revision instead of proof."""
    proof_rounds_by_node[node_id] = max(
        0, proof_rounds_by_node.get(node_id, 0) - 1
    )


def compact_round_feedback(
    node: dict[str, Any], result: dict[str, Any], round_id: int,
) -> dict[str, Any]:
    """Keep exactly the prior evidence needed by the next prover round."""
    attempts = result.get("attempts", [])
    last = attempts[-1] if attempts else {}
    check = last.get("check", {})
    salvage_plans = result.get("salvage_plans", [])
    local_outline = (
        salvage_plans[-1].get("outline") if salvage_plans else None
    )
    return {
        "round_id": round_id,
        **{k:result[k] for k in ('status','observation_facts','observation_pages','observation_requests',
           'interaction_state','failure_scope','failure_class','previous_round_summaries') if k in result},
        "statement": node["statement"],
        "dependencies": list(node.get("dependencies", [])),
        "backend": result.get("backend"),
        "last_proof_body": last.get("proof_body", ""),
        "proof_candidates": [{
            "attempt": item.get("attempt"),
            "proof_body": item.get("proof_body", ""),
            "diagnostic": item.get("check", {}).get("diagnostic", ""),
        } for item in attempts[-5:] if item.get("proof_body")],
        "residual": result.get("residual") or check.get("diagnostic", ""),
        "stepwise_residual": result.get(
            "stepwise_result", {}
        ).get("residual", ""),
        "retrieved_premise_names": last.get(
            "retrieved_premise_names",
            result.get("retrieved_premise_names", []),
        ),
        "local_proof_outline": local_outline,
    }


def feedback_matches_node(
    node: dict[str, Any], feedback: dict[str, Any] | None,
    graph: dict[str, Any] | None = None,
) -> bool:
    return bool(
        feedback
        and feedback.get("statement") == node["statement"]
        and feedback.get("dependencies") == node.get("dependencies", [])
        and (
            not feedback.get("graph_payload_sha256")
            or graph is None
            or feedback.get("graph_payload_sha256")
            == graph.get("graph_payload_sha256")
        )
    )


def drop_stale_proof_evidence(
    graph: dict[str, Any], statuses: dict[str, str],
    proof_rounds_by_node: dict[str, int], retrieval_hints: dict[str, str],
    proof_feedback_by_node: dict[str, dict[str, Any]],
    gra_feedback_by_node: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    by_id = {node["id"]: node for node in graph["nodes"]}
    if gra_feedback_by_node is not None:
        for node_id, feedback in list(gra_feedback_by_node.items()):
            node = by_id.get(node_id)
            if node is None or not gra_feedback_matches_node(
                graph, node, feedback,
            ):
                gra_feedback_by_node.pop(node_id, None)
    reset = []
    for node_id, status in statuses.items():
        if status == "green" or proof_rounds_by_node.get(node_id, 0) <= 0:
            continue
        if feedback_matches_node(
            by_id[node_id], proof_feedback_by_node.get(node_id), graph,
        ):
            continue
        proof_rounds_by_node[node_id] = 0
        retrieval_hints.pop(node_id, None)
        proof_feedback_by_node.pop(node_id, None)
        if gra_feedback_by_node is not None:
            gra_feedback_by_node.pop(node_id, None)
        reset.append(node_id)
    return reset


def hydrate_feedback_candidates(
    theory_dir: Path, node_id: str, feedback: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Recover candidate frontier for ledgers written before multi-replay."""
    if not feedback or feedback.get("proof_candidates"):
        return feedback
    round_id = feedback.get("round_id")
    if not isinstance(round_id, int):
        return feedback
    safe = node_id.replace("/", "_").replace(":", "_")
    attempts_path = (
        theory_dir / "nodes" / safe / f"round-{round_id:02d}"
        / "attempts.json"
    )
    if not attempts_path.is_file():
        return feedback
    attempts = json.loads(attempts_path.read_text(encoding="utf-8"))
    hydrated = dict(feedback)
    hydrated["proof_candidates"] = [{
        "attempt": item.get("attempt"),
        "proof_body": item.get("proof_body", ""),
        "diagnostic": item.get("check", {}).get("diagnostic", ""),
    } for item in attempts[-5:] if item.get("proof_body")]
    hydrated["candidate_frontier_source"] = str(attempts_path)
    return hydrated


def recover_round_feedback(
    theory_dir: Path, graph: dict[str, Any], node_id: str,
) -> dict[str, Any] | None:
    """Recover the newest completed RED round from immutable decision evidence."""
    history = prior_node_decisions(theory_dir, node_id)
    if not history:
        return None
    latest = history[-1]
    request_path = (
        theory_dir / "graph-revisions" / latest["decision_id"] / "request.json"
    )
    request = read_json(request_path)
    failed = request.get("failed_node", {})
    node = next(
        (item for item in graph["nodes"] if item["id"] == node_id), None
    )
    request_node = next((
        item for item in request.get("current_graph", {}).get("nodes", [])
        if item["id"] == node_id
    ), None)
    if (
        node is None
        or failed.get("node_id") != node_id
        or request_node is None
        or request_node.get("statement") != node.get("statement")
        or request_node.get("dependencies", []) != node.get("dependencies", [])
    ):
        return None
    return compact_round_feedback(
        node, failed, int(latest.get("proof_round") or 0),
    )


def recover_latest_attempt_feedback(
    theory_dir: Path, graph: dict[str, Any], node_id: str,
) -> dict[str, Any] | None:
    """Recover the newest checker-bound RED attempt after interruption."""
    by_id = {node["id"]: node for node in graph["nodes"]}
    if not graph.get("graph_payload_sha256"):
        return None
    node = by_id.get(node_id)
    if node is None:
        return None
    rounds: list[tuple[int, Path]] = []
    for round_dir in node_log_directory(theory_dir, node_id).glob("round-*"):
        match = re.fullmatch(r"round-(\d+)", round_dir.name)
        if match and (round_dir / "attempts.json").is_file():
            rounds.append((int(match.group(1)), round_dir))
    required_statements = [node["statement"], *(
        by_id[dep]["statement"]
        for dep in sorted(dependency_closure(graph, node_id))
    )]
    for round_id, round_dir in sorted(rounds, reverse=True):
        attempts_path = round_dir / "attempts.json"
        try:
            attempts = json.loads(attempts_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(attempts, list) or not attempts:
            continue
        selected: dict[str, Any] | None = None
        selected_source: Path | None = None
        for attempt in reversed(attempts):
            check = attempt.get("check", {})
            if check.get("verdict") != "red" or check.get("returncode") in {
                None, 0, 124, 137,
            }:
                continue
            proof = str(attempt.get("proof_body", "")).strip()
            source_value = check.get("source")
            source = Path(source_value) if source_value else None
            if not proof or source is None or not source.is_file():
                continue
            source_text = source.read_text(encoding="utf-8")
            if proof not in source_text:
                continue
            if not all(statement in source_text for statement in required_statements):
                continue
            selected = attempt
            selected_source = source
            break
        if selected is None or selected_source is None:
            continue
        check = selected["check"]
        return {
            "round_id": round_id,
            "statement": node["statement"],
            "dependencies": list(node.get("dependencies", [])),
            "graph_payload_sha256": graph.get("graph_payload_sha256"),
            "backend": "whole-proof",
            "last_proof_body": selected.get("proof_body", ""),
            "proof_candidates": [{
                "attempt": item.get("attempt"),
                "proof_body": item.get("proof_body", ""),
                "diagnostic": item.get("check", {}).get("diagnostic", ""),
            } for item in attempts[-5:] if item.get("proof_body")],
            "residual": check.get("diagnostic", ""),
            "stepwise_residual": "",
            "retrieved_premise_names": selected.get(
                "retrieved_premise_names", []
            ),
            "local_proof_outline": None,
            "candidate_frontier_source": str(attempts_path),
            "checker_source": str(selected_source),
            "checker_source_sha256": sha256_path(selected_source),
            "recovered_after_interruption": True,
        }
    return None


def select_resume_feedback(
    current: dict[str, Any] | None, recovered: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Prefer newer recovery evidence without erasing richer same-round state."""
    if recovered is None:
        return current
    if current is None:
        return recovered
    recovered_round = int(recovered.get("round_id", 0))
    current_round = int(current.get("round_id", 0))
    if recovered_round > current_round:
        return recovered
    if recovered_round < current_round:
        return current
    merged = dict(current)
    for key, value in recovered.items():
        if key not in merged or merged[key] in (None, "", [], {}):
            merged[key] = value
    return merged


def gra_feedback_matches_node(
    graph: dict[str, Any], node: dict[str, Any],
    feedback: dict[str, Any] | None,
) -> bool:
    return bool(
        feedback
        and graph.get("graph_payload_sha256")
        and feedback.get("graph_payload_sha256")
        == graph.get("graph_payload_sha256")
        and feedback.get("statement") == node["statement"]
        and feedback.get("dependencies") == node.get("dependencies", [])
        and feedback.get("retry_directive_sha256")
        == retry_directive_sha256(feedback.get("decision", {}))
    )


def gra_feedback_consumption_path(theory_dir: Path, decision_id: str) -> Path:
    return (
        theory_dir / "graph-revisions" / decision_id
        / "gra-feedback-consumed.json"
    )


def recover_gra_feedback(
    theory_dir: Path, graph: dict[str, Any], node_id: str,
) -> dict[str, Any] | None:
    """Recover the newest valid, unconsumed RETRY_PROOF decision."""
    node = next((item for item in graph["nodes"] if item["id"] == node_id), None)
    if node is None:
        return None
    history = prior_node_decisions(theory_dir, node_id)
    if not history or history[-1].get("decision") != "RETRY_PROOF":
        return None
    decision_id = history[-1]["decision_id"]
    if gra_feedback_consumption_path(theory_dir, decision_id).exists():
        return None
    directory = theory_dir / "graph-revisions" / decision_id
    request_path = directory / "request.json"
    decision_path = directory / "decision.json"
    if not request_path.is_file() or not decision_path.is_file():
        return None
    request = read_json(request_path)
    decision = read_json(decision_path)
    request_graph = request.get("current_graph", {})
    request_node = next((
        item for item in request_graph.get("nodes", [])
        if item.get("id") == node_id
    ), None)
    if (
        not graph.get("graph_payload_sha256")
        or request_graph.get("graph_payload_sha256")
        != graph.get("graph_payload_sha256")
        or request_node is None
        or request_node.get("statement") != node["statement"]
        or request_node.get("dependencies", []) != node.get("dependencies", [])
    ):
        return None
    try:
        validate_decision(decision, request)
    except ValueError:
        return None
    directive = normalized_retry_directive(decision)
    directive_sha256 = retry_directive_sha256(decision)
    if not directive or not directive_sha256:
        return None
    return {
        "decision_id": decision_id,
        "statement": node["statement"],
        "dependencies": list(node.get("dependencies", [])),
        "graph_payload_sha256": graph.get("graph_payload_sha256"),
        "retry_directive": directive,
        "retry_directive_sha256": directive_sha256,
        "decision": decision,
    }


def pending_gra_feedback(
    graph: dict[str, Any], node: dict[str, Any], decision: dict[str, Any],
) -> dict[str, Any]:
    """Bind a newly accepted RETRY_PROOF decision to the active node."""
    directive = normalized_retry_directive(decision)
    directive_sha256 = retry_directive_sha256(decision)
    decision_id = decision.get("decision_id")
    if not decision_id or not directive or not directive_sha256:
        raise ValueError("accepted RETRY_PROOF lacks auditable material guidance")
    return {
        "decision_id": decision_id,
        "statement": node["statement"],
        "dependencies": list(node.get("dependencies", [])),
        "graph_payload_sha256": graph.get("graph_payload_sha256"),
        "retry_directive": directive,
        "retry_directive_sha256": directive_sha256,
        "decision": decision,
    }


def consume_gra_feedback(
    theory_dir: Path, graph: dict[str, Any], node: dict[str, Any],
    feedback: dict[str, Any], *, proof_round: int,
) -> Path:
    """Bind one RETRY_PROOF directive to exactly one subsequent proof round."""
    if not gra_feedback_matches_node(graph, node, feedback):
        raise ValueError("cannot consume stale Graph Revision Agent feedback")
    path = gra_feedback_consumption_path(theory_dir, feedback["decision_id"])
    value = {
        "schema_version": 1,
        "decision_id": feedback["decision_id"],
        "node_id": node["id"],
        "graph_payload_sha256": graph.get("graph_payload_sha256"),
        "statement_sha256": hashlib.sha256(
            node["statement"].encode("utf-8")
        ).hexdigest(),
        "dependencies": list(node.get("dependencies", [])),
        "retry_directive_sha256": feedback["retry_directive_sha256"],
        "proof_round": proof_round,
    }
    if path.is_file():
        if read_json(path) != value:
            raise ValueError("GRA feedback was already consumed by another round")
        return path
    write_json(path, value)
    return path


def gra_feedback_hint(feedback: dict[str, Any] | None) -> str:
    if not feedback:
        return ""
    directive = feedback.get("retry_directive", {})
    return "\n".join(
        f"GRA {field}: {value}" for field, value in directive.items()
    )


def environment_context(environment_run: Path) -> dict[str, Any]:
    environment_run = environment_run.resolve()
    gate_path = environment_run / "Environment-Preparation" / "environment-gate.json"
    gate = validate_environment_gate(environment_run, gate_path)
    return {
        "gate": gate,
        "gate_path": gate_path,
        "contract_package_dir": (
            environment_run / gate["contract_packages"]["path"]
        ).resolve(),
        "premise_snapshot": (
            environment_run / gate["premise_catalog"]["snapshot"]["path"]
        ).resolve(),
        "premise_database": (
            environment_run / gate["premise_catalog"]["database"]["path"]
        ).resolve(),
        "semantic_workspace": (
            environment_run / gate["runtime"]["workspace_path"]
        ).resolve(),
        "heaps": (environment_run / gate["runtime"]["heaps_path"]).resolve(),
        "l4v": (environment_run / gate["runtime"]["l4v_path"]).resolve(),
    }


def package_paths(
    environment_run: Path, context: dict[str, Any] | None = None,
) -> list[Path]:
    context = context or environment_context(environment_run)
    return sorted(context["contract_package_dir"].glob("*.json"))


def generation_packages(
    environment_run: Path, context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return [
        package
        for package in (
            read_json(path) for path in package_paths(environment_run, context)
        )
        if package.get("contracts") or package.get("framework_obligations")
    ]


def thin_environment_gate(environment_run: Path) -> dict[str, Any]:
    context = environment_context(environment_run)
    gate = context["gate"]
    return {
        "clean_environment": "pass",
        "premise_catalog": "pass",
        "generation_files": gate["contract_packages"]["package_count"],
        "contracts": gate["contract_packages"]["contract_count"],
        "base_session": gate["runtime"]["base_session"],
        "environment_gate_id": gate["gate_id"],
        "semantic_base_seal_id": gate["semantic_base_seal_id"],
    }


def legacy_schedule_api():
    """Load schema-1 schedule support only for an explicitly legacy run."""
    from regen.legacy import session_planner
    return session_planner


def schedule_entries(
    environment_run: Path, schedule_path: Path | None,
) -> list[dict[str, Any]]:
    if schedule_path is None:
        raise ValueError("planning file list is required")
    packages = [read_json(path) for path in package_paths(environment_run)]
    by_theory = {item["theory"]: item for item in packages}
    schedule = legacy_schedule_api().validate_planning_bundle(
        schedule_path.resolve(), environment_context(environment_run)["gate"],
    )
    result = []
    for layer in schedule["layers"]:
        for item in layer["theories"]:
            if item["theory"] not in by_theory:
                raise ValueError(f"schedule names an unknown generation theory: {item['theory']}")
            package = by_theory[item["theory"]]
            if (
                item["file_slot"] != package["file_slot"]
                or item["contract_ids"] != [
                    contract["contract_id"] for contract in package["contracts"]
                ]
            ):
                raise ValueError(
                    f"schedule/package identity mismatch: {item['theory']}"
                )
            result.append({
                "theory": item["theory"], "file_slot": item["file_slot"],
                "layer": layer["layer"], "status": "pending",
            })
    if set(item["theory"] for item in result) != set(by_theory):
        raise ValueError("schedule does not cover every generation theory exactly once")
    return result


def schedule_binding(
    environment_run: Path, schedule_path: Path | None,
) -> dict[str, Any]:
    if schedule_path is None:
        raise ValueError("planning file list is required")
    schedule_path = schedule_path.resolve()
    gate = environment_context(environment_run)["gate"]
    schedule = legacy_schedule_api().validate_planning_bundle(schedule_path, gate)
    return {
        "source": str(schedule_path),
        "session_plan_bound": True,
        "campaign_id": schedule["campaign_id"],
        "environment_gate_id": schedule["environment_gate_id"],
        "contract_package_payload_sha256": schedule[
            "contract_package_payload_sha256"
        ],
        "file_list_sha256": schedule["list_payload_sha256"],
        "dependency_evidence_sha256": schedule["dependency_evidence_sha256"],
        "policy_sha256": schedule["policy_sha256"],
        "layer_count": schedule.get("layer_count", len(schedule["layers"])),
        "file_count": sum(
            len(layer["theories"]) for layer in schedule["layers"]
        ),
        "gate": "canonical-environment-gate",
    }


def canonical_progress_path(schedule_path: Path) -> Path:
    path = schedule_path.resolve().parent / "session-progress-ledger.json"
    if not path.is_file():
        raise ValueError(f"planning progress ledger is missing: {path}")
    return path


def load_progress(run_dir: Path) -> dict[str, Any]:
    from regen.execution.repository_execution import is_repository, progress
    if is_repository(run_dir): return progress(run_dir)
    run = read_json(run_dir / "run.json")
    ledger_path = Path(run["session_progress_ledger"])
    ledger = read_json(ledger_path)
    file_list = read_json(Path(run["session_schedule"]["source"]))
    binding = run["session_schedule"]
    gate = {
        "campaign_id": binding["campaign_id"],
        "gate_id": binding["environment_gate_id"],
    }
    legacy_schedule_api().validate_progress_ledger(ledger, file_list, gate)
    if ledger.get("active_run_id") != run["run_id"]:
        raise ValueError("canonical progress ledger is bound to another proof run")
    return ledger


def save_progress(run_dir: Path, ledger: dict[str, Any]) -> None:
    run = read_json(run_dir / "run.json")
    ledger_path = Path(run["session_progress_ledger"])
    file_list = read_json(Path(run["session_schedule"]["source"]))
    binding = run["session_schedule"]
    gate = {
        "campaign_id": binding["campaign_id"],
        "gate_id": binding["environment_gate_id"],
    }
    legacy_schedule_api().validate_progress_ledger(ledger, file_list, gate)
    temporary = ledger_path.with_suffix(ledger_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(ledger_path)


def theory_generation_id(run: dict[str, Any], file_slot: str) -> str:
    return f"{run['run_id']}--{file_slot.removesuffix('.thy')}"


def _init_run_unlocked(
    run_dir: Path, input_root: Path, *, run_id: str,
    schedule_path: Path | None = None,
) -> dict[str, Any]:
    if schedule_path and read_json(schedule_path).get('artifact_type') == 'repository-file-generation-list':
        from regen.execution.repository_execution import init_run as init_repository
        return init_repository(run_dir, input_root, run_id, schedule_path)
    # Everything below is the compatibility path for pre-schema-5 runs.
    environment_run = input_root.resolve()
    context = environment_context(environment_run)
    source_heaps = context["heaps"]
    gate = thin_environment_gate(environment_run)
    binding = schedule_binding(environment_run, schedule_path)
    assert schedule_path is not None
    progress_path = canonical_progress_path(schedule_path)
    progress = read_json(progress_path)
    file_list = read_json(schedule_path)
    legacy_schedule_api().validate_progress_ledger(progress, file_list, context["gate"])
    active_run = progress.get("active_run_id")
    if active_run not in {None, run_id}:
        raise ValueError(
            f"canonical progress ledger is already bound to run {active_run}"
        )
    if active_run is None:
        progress["active_run_id"] = run_id
        progress["session_status"] = (
            "complete"
            if all(item["status"] == "sealed" for item in progress["files"])
            else "running"
        )
        progress["revision"] += 1
        progress["events"].append({
            "revision": progress["revision"],
            "event": "proof-run-bound",
            "run_id": run_id,
        })
    run = {
        "schema_version": 1,
        "run_id": run_id,
        "target": "Refine",
        "environment_run": str(environment_run.resolve()),
        "premise_database": str(context["premise_database"]),
        "premise_snapshot": str(context["premise_snapshot"]),
        "contract_package_dir": str(context["contract_package_dir"]),
        "semantic_workspace": str(context["semantic_workspace"]),
        "l4v": str(context["l4v"]),
        "base_session": context["gate"]["runtime"]["base_session"],
        "base_import": context["gate"]["runtime"]["base_import"],
        "base_catalog": context["gate"]["runtime"]["base_catalog"],
        "state": "ready",
        "environment_gate": gate,
        "session_schedule": binding,
        "session_progress_ledger": str(progress_path),
    }
    write_json(run_dir / "run.json", run)
    save_progress(run_dir, progress)
    if not (run_dir / "heaps").exists():
        shutil.copytree(source_heaps, run_dir / "heaps")
    (run_dir / "theories").mkdir(parents=True, exist_ok=True)
    (run_dir / "generated").mkdir(parents=True, exist_ok=True)
    return run


def init_run(
    run_dir: Path, input_root: Path, *, run_id: str,
    schedule_path: Path | None = None,
) -> dict[str, Any]:
    lock = acquire_run_mutation_lock(run_dir, operation="init")
    try:
        return _init_run_unlocked(
            run_dir, input_root, run_id=run_id,
            schedule_path=schedule_path,
        )
    finally:
        release_run_mutation_lock(lock)


def update_file(
    files: dict[str, Any], theory: str, status: str, *,
    generation_id: str | None = None, seal_id: str | None = None,
    **extra: Any,
) -> None:
    entry = next(item for item in files["files"] if item["theory"] == theory)
    if generation_id is not None and generation_id not in entry["generation_ids"]:
        entry["generation_ids"].append(generation_id)
    effective_generation_id = generation_id or (
        entry["generation_ids"][-1] if entry["generation_ids"] else None
    )
    if status == "sealed":
        if not seal_id:
            raise ValueError("sealed theory progress requires a seal ID")
        entry["seal_id"] = seal_id
        entry.pop("failed_node", None)
        entry.pop("reason", None)
    else:
        entry["seal_id"] = None
    entry.update({"status": status, **extra})
    files["revision"] += 1
    all_sealed = all(item["status"] == "sealed" for item in files["files"])
    files["session_status"] = "complete" if all_sealed else "running"
    files["events"].append({
        "revision": files["revision"],
        "theory": theory,
        "status": status,
        "generation_id": effective_generation_id,
        "seal_id": seal_id,
    })


def package_for(run: dict[str, Any], file_slot: str) -> dict[str, Any]:
    return read_json(
        Path(run["contract_package_dir"]) / f"{file_slot}.json"
    )


def frozen_role_model(run: dict[str, Any], role: str) -> str:
    if "execution_models" in run:
        return run["execution_models"][role]
    manifest = read_json(
        Path(run["environment_run"])
        / "Environment-Freeze/campaign-manifest.json"
    )
    matches = [
        item["model"] for item in manifest.get("model_backends", [])
        if role.lower() in item.get("role", "").lower()
    ]
    if len(matches) != 1:
        raise ValueError(f"frozen model identity is ambiguous for role {role}: {matches}")
    return matches[0]


def planner_graph_bindings(
    run: dict[str, Any], package: dict[str, Any], generation_id: str,
) -> dict[str, Any]:
    return {
        "campaign_id": run["session_schedule"]["campaign_id"],
        "environment_gate_id": run["session_schedule"]["environment_gate_id"],
        "session_file_list_sha256": run["session_schedule"]["file_list_sha256"],
        "contract_package_payload_sha256": package["package_payload_sha256"],
        "generation_id": generation_id,
    }


def validate_planner_graph_bindings(
    graph: dict[str, Any], expected: dict[str, Any],
) -> None:
    verify_graph_payload_sha256(graph)
    if graph.get("upstream_bindings") != expected:
        raise ValueError("planned graph does not bind the active upstream artifacts")
    planner_call = graph.get("planner_call", {})
    if not planner_call.get("prompt_sha256") or not planner_call.get("final_sha256"):
        raise ValueError("planned graph does not bind its File Planner transcript")
    if graph.get("planner_context", {}).get(
        "external_premise_records_visible"
    ) is not False:
        raise ValueError("planned graph did not enforce the no-premise planning boundary")


def generated_checkpoint_theory(file_slot: str) -> str:
    """Use a runtime name distinct from Semantic Base source theories."""
    return f"Regen_{Path(file_slot).stem}"


def sealed_checkpoint_theories(files: dict[str, Any]) -> list[str]:
    return [
        item.get("checkpoint_theory")
        or generated_checkpoint_theory(item["file_slot"])
        for item in files["files"] if item["status"] == "sealed"
    ]


def publish_generated_records(
    run_dir: Path, graph: dict[str, Any], theory_seal_id: str,
    package: dict[str, Any],
) -> None:
    path = run_dir / "generated-premises.json"
    value = read_json(path) if path.exists() else {"records": []}
    records = {item["record_id"]: item for item in value["records"]}
    contract_attributes = {
        item["contract_id"]: list(item.get("contract_owned_attributes", []))
        for item in package["contracts"]
    }
    commit_ids: dict[str, str] = {}
    accepted_proofs: dict[str, str] = {}
    commit_dir = (
        run_dir / "theories" / Path(graph["file_slot"]).stem
        / "acceptance" / "commits"
    )
    if commit_dir.is_dir():
        for commit_path in sorted(commit_dir.glob("*.json")):
            commit = read_json(commit_path)
            accepted_proofs.update(commit.get("state", {}).get("proofs", {}))
            if commit.get("event") == "green":
                node_id = commit.get("evidence", {}).get("node_id")
                if node_id:
                    commit_ids[node_id] = commit_path.stem
            elif commit.get('event') == 'batch' or (commit.get('event') == 'initial'
                    and any(k in commit.get('evidence', {}) for k in ('continuation', 'sealed_reuse'))):
                for node_id in commit.get('state', {}).get('proofs', {}):
                    commit_ids.setdefault(node_id, commit_path.stem)
    isabelle_theory = generated_checkpoint_theory(graph["file_slot"])
    for node in graph["nodes"]:
        record_id = f"generated:{graph['theory']}:{node['name']}"
        attributes = (
            contract_attributes.get(node["id"], [])
            if node["role"] == "contract"
            else list(node.get("attributes", []))
        )
        from regen.execution.proof_commits import digest
        body = accepted_proofs.get(node["id"])
        example = ({"proof_body":body, "proof_sha256":digest(body)}
                   if body and commit_ids.get(node["id"]) else {})
        records[record_id] = {
            **example,
            "context": list(node.get("context", [])),
            "record_id": record_id,
            "canonical_name": f"{isabelle_theory}.{node['name']}",
            "exact_statement": node["statement"],
            "owner_theory": graph["theory"],
            "owner_session": "Generated_Refine",
            "source_class": "generated_theorem",
            "attributes": attributes,
            "theory_seal_id": theory_seal_id,
            "status": "sealed",
            "graph_revision": graph["revision"],
            "statement_sha256": hashlib.sha256(
                node["statement"].encode("utf-8")
            ).hexdigest(),
            "proof_commit_id": commit_ids.get(node["id"]),
            "publication_scope": "sealed-downstream-theories",
        }
    value["records"] = sorted(records.values(), key=lambda item: item["record_id"])
    write_json(path, value)


def create_theory_seal(
    run_dir: Path, run: dict[str, Any], entry: dict[str, Any],
    generation_id: str, graph: dict[str, Any], generated_path: Path,
    theory_dir: Path,
) -> dict[str, Any]:
    artifacts = {
        "graph": theory_dir / f"graph-v{graph['revision']}.json",
        "complete_check": theory_dir / "complete-check.json",
        "session_build": theory_dir / "session-build.json",
        "retained_semantic_impact": (
            theory_dir / "retained-semantic-impact.json"
        ),
        "generated_source": generated_path,
    }
    missing = [name for name, path in artifacts.items() if not path.is_file()]
    if missing:
        raise ValueError(f"theory seal inputs are missing: {missing}")
    if read_json(artifacts["complete_check"]).get("verdict") != "green":
        raise ValueError("theory seal requires a GREEN complete check")
    if read_json(artifacts["session_build"]).get("verdict") != "green":
        raise ValueError("theory seal requires a GREEN session build")
    if read_json(artifacts["retained_semantic_impact"]).get("verdict") != "pass":
        raise ValueError("theory seal requires a PASS dependency impact audit")
    core = {
        "schema_version": 1,
        "artifact_type": "generated-theory-seal",
        "verdict": "pass",
        "run_id": run["run_id"],
        "campaign_id": run["session_schedule"]["campaign_id"],
        "environment_gate_id": run["session_schedule"]["environment_gate_id"],
        "generation_id": generation_id,
        "theory": graph["theory"],
        "file_slot": entry["file_slot"],
        "graph_revision": graph["revision"],
        "artifacts": {
            name: {
                "path": str(path.resolve()),
                "sha256": sha256_path(path),
            }
            for name, path in artifacts.items()
        },
    }
    seal = {
        **core,
        "seal_id": f"theory-seal:{payload_sha256(core)[:20]}",
    }
    seal_path = theory_dir / "theory-seal.json"
    if seal_path.exists():
        if read_json(seal_path) != seal:
            raise ValueError("existing theory seal differs from rebuilt evidence")
    else:
        write_json(seal_path, seal)
    return seal


def _plan_run_file_unlocked(
    run_dir: Path, theory: str, *, provider: str,
    planner_model: str, timeout: int,
) -> dict[str, Any]:
    from regen.execution.repository_execution import is_repository, plan as plan_repository
    if is_repository(run_dir):
        return plan_repository(run_dir,theory,provider=provider,planner_model=planner_model,timeout=timeout)
    emit_event(
        "runner_started", run_dir=str(run_dir), theory=theory,
        provider=provider, lemma_backend="file-planner",
    )
    run = read_json(run_dir / "run.json")
    files = load_progress(run_dir)
    entry = next(item for item in files["files"] if item["theory"] == theory)
    generation_id = theory_generation_id(run, entry["file_slot"])
    environment_run = Path(run["environment_run"])
    theory_dir = run_dir / "theories" / entry["file_slot"].removesuffix(".thy")
    graph_path = theory_dir / "graph-v1.json"
    package = package_for(run, entry["file_slot"])
    bindings = planner_graph_bindings(run, package, generation_id)
    expected_model = frozen_role_model(run, "File Planner")
    if planner_model != expected_model:
        raise ValueError(
            f"File Planner model {planner_model!r} does not match frozen "
            f"identity {expected_model!r}"
        )
    if entry["status"] == "planned" and graph_path.is_file():
        graph = read_json(graph_path)
        validate_planner_graph_bindings(graph, bindings)
        graph_check = read_json(theory_dir / "graph-check-v1.json")
        if graph_check.get("verdict") != "green":
            raise ValueError("planned graph has no GREEN statement-shell certificate")
        return graph
    environment = planning_environment(package)
    theory_dir.mkdir(parents=True, exist_ok=True)
    sealed_ancestors = sealed_checkpoint_theories(files)
    planner_checker = IsabelleChecker(
        environment_run, theory_dir / "isabelle", timeout=timeout,
        previous_theories=sealed_ancestors,
        generated_dir=run_dir / "generated",
        heaps_dir=run_dir / "heaps",
        base_session=run["base_session"], base_import=run["base_import"],
        base_catalog=run["base_catalog"], l4v_dir=Path(run["l4v"]),
        semantic_workspace=Path(run["semantic_workspace"]),
    )
    first_call_index = 1 + len(list(
        (theory_dir / "llm").glob("file-plan-v1*.meta.json")
    ))
    update_file(files, theory, "planning", generation_id=generation_id)
    save_progress(run_dir, files)
    try:
        rejections: list[str] = []
        for offset in range(3):
            try:
                graph = plan_file(
                    package, run_id=generation_id, output_dir=theory_dir,
                    provider=provider, model=planner_model, timeout=timeout,
                    environment=environment,
                    module_log_path=(
                        run_dir / "module-logs/file-planner"
                        / f"{entry['file_slot'].removesuffix('.thy')}.json"
                    ),
                    call_index=first_call_index + offset,
                    rejection_feedback=rejections,
                    graph_bindings=bindings, persist_graph=False,
                )
                validate_planner_graph_bindings(graph, bindings)
                graph_check = planner_checker.check_graph(graph)
                graph_check["planner_attempt"] = first_call_index + offset
                attempt_check_path = (
                    theory_dir / f"graph-plan-attempt-{first_call_index + offset:03d}-check.json"
                )
                write_json(attempt_check_path, graph_check)
                if graph_check.get("verdict") != "green":
                    rejections.append(
                        "Isabelle statement-shell certification failed: "
                        + graph_check.get("diagnostic", "unknown diagnostic")[-6000:]
                    )
                    continue
                write_immutable_graph(graph_path, graph)
                write_json(theory_dir / "graph-check-v1.json", graph_check)
                attach_module_outputs(
                    run_dir / "module-logs/file-planner"
                    / f"{entry['file_slot'].removesuffix('.thy')}.json",
                    {
                        "generated_graph": graph_path,
                        "statement_shell_certificate": (
                            theory_dir / "graph-check-v1.json"
                        ),
                    },
                )
                break
            except (ValueError, KeyError, json.JSONDecodeError) as error:
                rejections.append(str(error))
        else:
            raise ValueError(
                "File Planner exhausted three quality-repair attempts: "
                + " | ".join(rejections)
            )
    except Exception:
        files = load_progress(run_dir)
        update_file(
            files, theory, "planning_failed", generation_id=generation_id,
        )
        save_progress(run_dir, files)
        raise
    files = load_progress(run_dir)
    update_file(
        files, theory, "planned", graph_revision=graph["revision"],
        graph_source=str(graph_path), generation_id=generation_id,
    )
    save_progress(run_dir, files)
    return graph


def plan_run_file(
    run_dir: Path, theory: str, *, provider: str,
    planner_model: str, timeout: int,
) -> dict[str, Any]:
    lock = acquire_run_mutation_lock(
        run_dir, operation="plan-file", theory=theory,
    )
    try:
        return _plan_run_file_unlocked(
            run_dir, theory, provider=provider,
            planner_model=planner_model, timeout=timeout,
        )
    finally:
        release_run_mutation_lock(lock)


def _run_file_unlocked(
    run_dir: Path, theory: str, *, provider: str,
    planner_model: str, revision_model: str, prover_model: str,
    lemma_backend: str, timeout: int, max_attempts: int,
    max_node_rounds: int, max_node_revisions: int,
    graph_timeout: int = 900, think_timeout: int = 180,
    node_timeout: int = 300, salvage_provider: str | None = None,
) -> dict[str, Any]:
    from regen.execution.repository_execution import is_repository, run_file as run_repository_file
    if is_repository(run_dir):
        return run_repository_file(run_dir,theory,provider=provider,planner_model=planner_model,
            revision_model=revision_model,prover_model=prover_model,lemma_backend=lemma_backend,
            timeout=timeout,max_attempts=max_attempts,max_node_rounds=max_node_rounds,
            max_node_revisions=max_node_revisions,graph_timeout=graph_timeout,think_timeout=think_timeout,
            node_timeout=node_timeout,salvage_provider=salvage_provider)
    run = read_json(run_dir / "run.json")
    configure_event_monitor(
        run_dir, run_id=run["run_id"], theory=theory,
    )
    emit_event(
        "runner_started", run_dir=str(run_dir), theory=theory,
        provider=provider, lemma_backend=lemma_backend,
    )
    run["node_budget"] = {
        "policy": "per-node-doubled-v1",
        "whole_proof_attempts_per_round": max_attempts,
        "prover_rounds_per_node": max_node_rounds,
        "structural_revisions_per_node": max_node_revisions,
        "stepwise_round_timeout_seconds": timeout,
        "stepwise_think_timeout_seconds": min(timeout, think_timeout),
        "node_check_timeout_seconds": node_timeout,
        "whole_proof_attempt_ceiling_per_node": (
            max_attempts * max_node_rounds
        ),
    }
    write_json(run_dir / "run.json", run)
    files = load_progress(run_dir)
    entry = next(item for item in files["files"] if item["theory"] == theory)
    generation_id = theory_generation_id(run, entry["file_slot"])
    if entry["status"] == "sealed":
        return entry
    environment_run = Path(run["environment_run"])
    package = package_for(run, entry["file_slot"])
    theory_dir = run_dir / "theories" / entry["file_slot"].removesuffix(".thy")
    theory_dir.mkdir(parents=True, exist_ok=True)
    graph_path = active_graph_path(entry, theory_dir)
    if entry["status"] in {"planned", "failed", "generating"} and graph_path:
        graph = read_json(graph_path)
    else:
        graph = _plan_run_file_unlocked(
            run_dir, theory, provider=provider,
            planner_model=planner_model, timeout=timeout,
        )
        files = load_progress(run_dir)
        graph_path = theory_dir / "graph-v1.json"
    update_file(
        files, theory, "generating", graph_revision=graph["revision"],
        graph_source=str(graph_path), generation_id=generation_id,
    )
    save_progress(run_dir, files)

    sealed_ancestors = sealed_checkpoint_theories(files)
    checker_options = {
        "base_session": run["base_session"],
        "base_import": run["base_import"],
        "base_catalog": run["base_catalog"],
        "l4v_dir": Path(run["l4v"]),
        "semantic_workspace": Path(run["semantic_workspace"]),
    }
    checker = IsabelleChecker(
        environment_run, theory_dir / "isabelle", timeout=node_timeout,
        previous_theories=sealed_ancestors,
        generated_dir=run_dir / "generated",
        heaps_dir=run_dir / "heaps",
        **checker_options,
    )
    graph_checker = IsabelleChecker(
        environment_run, theory_dir / "isabelle", timeout=graph_timeout,
        previous_theories=sealed_ancestors,
        generated_dir=run_dir / "generated",
        heaps_dir=run_dir / "heaps",
        **checker_options,
    )
    statuses = {node["id"]: "planned" for node in graph["nodes"]}
    proofs: dict[str, str] = {}
    proof_rounds: dict[str, int] = {}
    proof_rounds_by_node: dict[str, int] = {}
    node_revision_counts: dict[str, int] = {}
    retrieval_hints: dict[str, str] = {}
    proof_feedback_by_node: dict[str, dict[str, Any]] = {}
    gra_feedback_by_node: dict[str, dict[str, Any]] = {}
    graph_revision_rejections: list[str] = []
    stalled_resume_reviews: set[str] = set()
    progress_path = theory_dir / "progress.json"
    if progress_path.is_file():
        progress = read_json(progress_path)
        if progress.get("graph_revision") == graph["revision"]:
            known_nodes = set(statuses)
            proofs = {
                node_id: proof for node_id, proof in progress.get("proofs", {}).items()
                if node_id in known_nodes
            }
            statuses.update({
                node_id: "green"
                for node_id, status in progress.get("statuses", {}).items()
                if node_id in known_nodes and status == "green" and node_id in proofs
            })
            proof_rounds_by_node = {
                node_id: int(count)
                for node_id, count in progress.get(
                    "proof_rounds_by_node",
                    progress.get("proof_rounds_this_graph", {}),
                ).items()
                if node_id in known_nodes
            }
            node_revision_counts = {
                node_id: int(count)
                for node_id, count in progress.get(
                    "node_revision_counts", {}
                ).items()
                if node_id in known_nodes or node_id == "__graph__"
            }
            retrieval_hints = {
                node_id: hint
                for node_id, hint in progress.get("retrieval_hints", {}).items()
                if node_id in known_nodes
            }
            proof_feedback_by_node = {
                node_id: feedback
                for node_id, feedback in progress.get(
                    "proof_feedback_by_node", {}
                ).items()
                if node_id in known_nodes
            }
            gra_feedback_by_node = {
                node_id: feedback
                for node_id, feedback in progress.get(
                    "gra_feedback_by_node", {}
                ).items()
                if node_id in known_nodes
                and gra_feedback_matches_node(
                    graph,
                    next(node for node in graph["nodes"] if node["id"] == node_id),
                    feedback,
                )
                and not gra_feedback_consumption_path(
                    theory_dir, feedback["decision_id"]
                ).exists()
            }
    known_nodes = set(statuses)
    for node_id in known_nodes:
        feedback = proof_feedback_by_node.get(node_id)
        feedback = select_resume_feedback(
            feedback, recover_round_feedback(theory_dir, graph, node_id),
        )
        feedback = select_resume_feedback(
            feedback,
            recover_latest_attempt_feedback(theory_dir, graph, node_id),
        )
        if feedback is not None:
            proof_feedback_by_node[node_id] = feedback
        if node_id not in gra_feedback_by_node:
            recovered_gra = recover_gra_feedback(
                theory_dir, graph, node_id,
            )
            if recovered_gra is not None:
                gra_feedback_by_node[node_id] = recovered_gra
    drop_stale_proof_evidence(
        graph, statuses, proof_rounds_by_node, retrieval_hints,
        proof_feedback_by_node, gra_feedback_by_node,
    )

    emit_event(
        "runner_state_loaded", theory=theory,
        graph_revision=graph["revision"],
        resumed=progress_path.is_file(),
        green=sum(status == "green" for status in statuses.values()),
        total=len(statuses),
    )

    def persist_progress() -> None:
        write_json(progress_path, {
            "graph_revision": graph["revision"],
            "statuses": statuses,
            "proofs": proofs,
            "proof_rounds_by_node": proof_rounds_by_node,
            "node_revision_counts": node_revision_counts,
            "retrieval_hints": retrieval_hints,
            "proof_feedback_by_node": proof_feedback_by_node,
            "gra_feedback_by_node": gra_feedback_by_node,
        })

    interrupted_request = latest_incomplete_graph_request(
        theory_dir, graph["revision"],
    )
    if (
        interrupted_request is not None
        and interrupted_request.get("decision_context", {}).get("phase")
        == "node-proof"
    ):
        result = interrupted_request["failed_node"]
        node_id = result["node_id"]
        if node_id not in statuses:
            raise RuntimeError(
                "interrupted graph request targets a node outside the active "
                f"graph: {node_id}"
            )
        green_ids = {
            item for item, status in statuses.items() if status == "green"
        }
        node_revisions = node_revision_counts.get(node_id, 0)
        emit_event(
            "graph_revision_resumed", theory=theory, node_id=node_id,
            node=short_node(node_id),
            interrupted_decision_id=interrupted_request[
                "interrupted_decision_id"
            ],
        )
        revised, decision = revise_graph(
            package, graph, result, green_ids, output_dir=theory_dir,
            provider=provider, model=revision_model,
            timeout=graph_timeout,
            decision_index=next_graph_decision_index(theory_dir),
            allow_structural_revision=(
                node_revisions < max_node_revisions
            ),
            decision_context=interrupted_request["decision_context"],
            premise_database=Path(run["premise_database"]),
        )
        emit_event(
            "graph_decision", theory=theory, node_id=node_id,
            node=short_node(node_id), decision=decision.get("decision"),
            failure_class=decision.get("failure_class"),
            reason=decision.get("reason"), resumed=True,
        )
        if decision["decision"] == "ABANDON_GENERATION":
            update_file(files, theory, "failed", failed_node=node_id)
            save_progress(run_dir, files)
            return next(
                item for item in files["files"] if item["theory"] == theory
            )
        if decision["decision"] == "RETRY_PROOF":
            node = next(
                item for item in graph["nodes"] if item["id"] == node_id
            )
            gra_feedback_by_node[node_id] = pending_gra_feedback(
                graph, node, decision,
            )
            persist_progress()
        else:
            if node_revisions >= max_node_revisions:
                update_file(
                    files, theory, "failed", failed_node=node_id,
                    reason="Graph Revision Agent requested a structural change "
                    "after this node's revision budget was exhausted",
                )
                save_progress(run_dir, files)
                return next(
                    item for item in files["files"]
                    if item["theory"] == theory
                )
            node_revision_counts[node_id] = node_revisions + 1
            credit_structural_evidence_round(proof_rounds_by_node, node_id)
            graph = revised
            update_file(
                files, theory, "generating", graph_revision=graph["revision"],
                graph_source=str(
                    theory_dir / f"graph-v{graph['revision']}.json"
                ),
            )
            save_progress(run_dir, files)
            statuses = {
                node["id"]: (
                    "green" if node["id"] in green_ids else "planned"
                )
                for node in graph["nodes"]
            }
            proofs = {
                item: proof for item, proof in proofs.items()
                if item in statuses and item in green_ids
            }
            proof_rounds = {
                item: proof_rounds.get(item, 0) for item in statuses
            }
            proof_rounds_by_node = {
                item: proof_rounds_by_node.get(item, 0)
                for item in statuses
            }
            graph_revision_count = node_revision_counts.get("__graph__", 0)
            node_revision_counts = {
                item: node_revision_counts.get(item, 0) for item in statuses
            }
            if graph_revision_count:
                node_revision_counts["__graph__"] = graph_revision_count
            retrieval_hints = {
                item: retrieval_hints.get(item, "") for item in statuses
            }
            drop_stale_proof_evidence(
                graph, statuses, proof_rounds_by_node, retrieval_hints,
                proof_feedback_by_node, gra_feedback_by_node,
            )
            persist_progress()

    while True:
        validate_graph(graph, package)
        graph_check_path = theory_dir / f"graph-check-v{graph['revision']}.json"
        if graph_check_path.is_file():
            graph_check = read_json(graph_check_path)
            if graph_check.get("returncode") == 124:
                delta_check = check_statement_delta(
                    graph_checker, theory_dir, graph,
                )
                if delta_check is not None:
                    graph_check = delta_check
                    write_json(graph_check_path, graph_check)
                elif graph_check.get("wall_ms", 0) < graph_timeout * 1000:
                    graph_check = graph_checker.check_graph(graph)
                    write_json(graph_check_path, graph_check)
        else:
            graph_check = check_statement_delta(
                graph_checker, theory_dir, graph,
            ) or graph_checker.check_graph(graph)
            write_json(graph_check_path, graph_check)
        if graph_check["verdict"] != "green":
            graph_revisions = node_revision_counts.get("__graph__", 0)
            green_ids = {
                item for item, status in statuses.items()
                if status == "green"
            }
            if graph_revisions >= max_node_revisions:
                update_file(files, theory, "failed", reason="statement certification failed")
                save_progress(run_dir, files)
                return next(item for item in files["files"] if item["theory"] == theory)
            graph, decision = revise_graph(
                package, graph, {
                    "status": "red",
                    "node_id": graph_check.get("failed_node_id") or "graph",
                    "residual": graph_check["diagnostic"], "attempts": [],
                }, green_ids, output_dir=theory_dir, provider=provider,
                model=revision_model, timeout=graph_timeout,
                decision_index=next_graph_decision_index(theory_dir),
                allow_structural_revision=(
                    graph_revisions < max_node_revisions
                ),
                decision_context={
                    "phase": "statement-certification",
                    "retry_proof_allowed": False,
                    "failed_statement_node_id": graph_check.get(
                        "failed_node_id"
                    ),
                    "budget_scope": "graph-certification",
                    "revisions_used": graph_revisions,
                    "revisions_remaining": (
                        max_node_revisions - graph_revisions
                    ),
                    "proof_attempt_limit_per_round": max_attempts,
                    "previous_revision_rejections": graph_revision_rejections,
                },
                premise_database=Path(run["premise_database"]),
            )
            node_revision_counts["__graph__"] = graph_revisions + 1
            persist_progress()
            if decision["decision"] == "RETRY_PROOF":
                if decision.get("internal_disposition") != "REJECT_INVALID_REVISION":
                    raise RuntimeError(
                        "Graph Revision Agent returned RETRY_PROOF during "
                        "statement certification; there is no proof body to retry"
                    )
                graph_revision_rejections.append(
                    decision.get("repair_guidance")
                    or decision.get("reason")
                    or "statement certification returned unchanged graph"
                )
                continue
            emit_event(
                "graph_decision", theory=theory, node_id=node_id,
                node=short_node(node_id), decision=decision.get("decision"),
                failure_class=decision.get("failure_class"),
                reason=decision.get("reason"),
            )
            if decision["decision"] == "ABANDON_GENERATION":
                update_file(
                    files, theory, "failed",
                    reason="statement-invalid graph generation was abandoned",
                )
                save_progress(run_dir, files)
                return next(item for item in files["files"] if item["theory"] == theory)
            graph_revision_rejections = []
            update_file(
                files, theory, "generating", graph_revision=graph["revision"],
                graph_source=str(theory_dir / f"graph-v{graph['revision']}.json"),
            )
            save_progress(run_dir, files)
            graph_node_ids = {node["id"] for node in graph["nodes"]}
            proofs = {
                node_id: proof for node_id, proof in proofs.items()
                if node_id in graph_node_ids
            }
            statuses = {
                node["id"]: (
                    "green" if node["id"] in green_ids else "planned"
                )
                for node in graph["nodes"]
            }
            drop_stale_proof_evidence(
                graph, statuses, proof_rounds_by_node, retrieval_hints,
                proof_feedback_by_node, gra_feedback_by_node,
            )
            persist_progress()
            continue

        while not all(statuses.get(node["id"]) == "green" for node in graph["nodes"]):
            ready = executable_nodes(graph, statuses)
            if not ready:
                raise RuntimeError("no executable node remains in a certified DAG")
            node_id = ready[0]
            history = prior_node_decisions(theory_dir, node_id)
            consecutive_retries = 0
            for prior in reversed(history):
                if prior.get("decision") != "RETRY_PROOF":
                    break
                consecutive_retries += 1
            wrapper_stalled = (
                proof_rounds_by_node.get(node_id, 0) >= 2
                and any(
                    item["node_id"] == node_id
                    for item in definitional_wrapper_risks(graph)
                )
            )
            full_round_stalled = (
                proof_rounds_by_node.get(node_id, 0) >= 2
                and bool(history)
                and history[-1].get("decision") == "RETRY_PROOF"
            )
            abandoned_resume = (
                proof_rounds_by_node.get(node_id, 0) >= max_node_rounds
                and bool(history)
                and history[-1].get("decision") == "ABANDON_GENERATION"
            )
            if (
                (
                    consecutive_retries >= max_node_rounds
                    or wrapper_stalled
                    or abandoned_resume
                    or (
                        full_round_stalled
                        and proof_rounds_by_node.get(node_id, 0)
                        >= max_node_rounds
                    )
                )
                and node_id not in stalled_resume_reviews
            ):
                stalled_resume_reviews.add(node_id)
                last_request = read_json(
                    theory_dir / "graph-revisions"
                    / history[-1]["decision_id"] / "request.json"
                )
                green_ids = {
                    item for item, status in statuses.items()
                    if status == "green"
                }
                node_revisions = node_revision_counts.get(node_id, 0)
                revised, decision = revise_graph(
                    package, graph, last_request["failed_node"], green_ids,
                    output_dir=theory_dir, provider=provider,
                    model=revision_model, timeout=graph_timeout,
                    decision_index=next_graph_decision_index(theory_dir),
                    allow_structural_revision=(
                        node_revisions < max_node_revisions
                    ),
                    decision_context={
                        "phase": "node-proof",
                        "resume_review": True,
                        "resume_after_abandon": abandoned_resume,
                        "predecessor_decision_id": (
                            history[-1].get("decision_id") if history else None
                        ),
                        "budget_scope": "node",
                        "budget_node_id": node_id,
                        "proof_round": proof_rounds_by_node.get(node_id, 0),
                        "proof_attempt_limit_per_round": max_attempts,
                        "proof_retry_limit": max_node_rounds,
                        "proof_rounds_used": proof_rounds_by_node.get(node_id, 0),
                        "proof_rounds_remaining": (
                            max_node_rounds
                            - proof_rounds_by_node.get(node_id, 0)
                        ),
                        "revisions_used": node_revisions,
                        "revisions_remaining": (
                            max_node_revisions - node_revisions
                        ),
                        "lemma_backend": lemma_backend,
                    },
                    premise_database=Path(run["premise_database"]),
                )
                if decision["decision"] == "ABANDON_GENERATION":
                    update_file(
                        files, theory, "failed", failed_node=node_id,
                        reason="stalled proof generation was abandoned",
                    )
                    save_progress(run_dir, files)
                    return next(
                        item for item in files["files"]
                        if item["theory"] == theory
                    )
                if decision["decision"] == "RETRY_PROOF" or revised is graph:
                    update_file(
                        files, theory, "failed", failed_node=node_id,
                        reason=(
                            "stalled resume review did not produce a valid "
                            "structural revision"
                        ),
                    )
                    save_progress(run_dir, files)
                    return next(
                        item for item in files["files"]
                        if item["theory"] == theory
                    )
                node_revision_counts[node_id] = node_revisions + 1
                credit_structural_evidence_round(
                    proof_rounds_by_node, node_id,
                )
                graph = revised
                update_file(
                    files, theory, "generating",
                    graph_revision=graph["revision"],
                    graph_source=str(
                        theory_dir / f"graph-v{graph['revision']}.json"
                    ),
                )
                save_progress(run_dir, files)
                statuses = {
                    node["id"]: (
                        "green" if node["id"] in green_ids else "planned"
                    )
                    for node in graph["nodes"]
                }
                proofs = {
                    item: proof for item, proof in proofs.items()
                    if item in statuses and item in green_ids
                }
                proof_rounds = {
                    item: proof_rounds.get(item, 0) for item in statuses
                }
                proof_rounds_by_node = {
                    item: proof_rounds_by_node.get(item, 0)
                    for item in statuses
                }
                node_revision_counts = {
                    item: node_revision_counts.get(item, 0)
                    for item in statuses
                } | {"__graph__": node_revision_counts.get("__graph__", 0)}
                retrieval_hints = {
                    item: retrieval_hints.get(item, "") for item in statuses
                }
                drop_stale_proof_evidence(
                    graph, statuses, proof_rounds_by_node, retrieval_hints,
                    proof_feedback_by_node, gra_feedback_by_node,
                )
                persist_progress()
                break
            if proof_rounds_by_node.get(node_id, 0) >= max_node_rounds:
                update_file(
                    files, theory, "failed", failed_node=node_id,
                    reason="per-node proof round budget exhausted",
                )
                save_progress(run_dir, files)
                return next(
                    item for item in files["files"] if item["theory"] == theory
                )
            proof_rounds_by_node[node_id] = (
                proof_rounds_by_node.get(node_id, 0) + 1
            )
            persist_progress()
            proof_rounds[node_id] = next_node_round_id(
                node_log_directory(theory_dir, node_id),
                proof_rounds.get(node_id, 0),
            )
            emit_event(
                "node_started", theory=theory, graph_revision=graph["revision"],
                node_id=node_id, node=short_node(node_id),
                proof_round=proof_rounds[node_id],
                green=sum(status == "green" for status in statuses.values()),
                total=len(statuses),
            )
            current_node = next(
                node for node in graph["nodes"] if node["id"] == node_id
            )
            active_gra_feedback = gra_feedback_by_node.get(node_id)
            if active_gra_feedback is not None:
                consumed_path = consume_gra_feedback(
                    theory_dir, graph, current_node, active_gra_feedback,
                    proof_round=proof_rounds[node_id],
                )
                gra_feedback_by_node.pop(node_id, None)
                persist_progress()
                emit_event(
                    "gra_feedback_consumed", theory=theory,
                    node_id=node_id, node=short_node(node_id),
                    proof_round=proof_rounds[node_id],
                    decision_id=active_gra_feedback["decision_id"],
                    artifact=str(consumed_path),
                )
            try:
                result = prove_node(
                    graph, node_id, proofs, checker=checker,
                    premise_database=Path(run["premise_database"]),
                    output_dir=theory_dir,
                    generated_records=run_dir / "generated-premises.json",
                    provider=provider, model=prover_model, timeout=timeout,
                    max_attempts=max_attempts, backend=lemma_backend,
                    round_id=proof_rounds[node_id],
                    retrieval_hint="\n".join(filter(None, [
                        retrieval_hints.get(node_id, ""),
                        gra_feedback_hint(active_gra_feedback),
                    ])),
                    salvage_provider=salvage_provider,
                    think_timeout=think_timeout,
                    previous_round_feedback=(
                        hydrate_feedback_candidates(
                            theory_dir, node_id,
                            proof_feedback_by_node.get(node_id),
                        )
                        if proof_feedback_by_node.get(node_id, {}).get("statement")
                        == current_node["statement"]
                        else None
                    ),
                )
            except BaseException:
                # An interrupted/crashed backend produced no GREEN/RED result and
                # therefore must not consume a logical lemma retry.
                proof_rounds_by_node[node_id] -= 1
                persist_progress()
                raise
            if result.get("failure_scope") == "infrastructure":
                # No theorem-level RED evidence was produced. Preserve both
                # the node retry budget and the graph-revision budget.
                proof_rounds_by_node[node_id] = max(
                    0, proof_rounds_by_node[node_id] - 1,
                )
                persist_progress()
                emit_event(
                    "node_infrastructure_error", theory=theory,
                    node_id=node_id, node=short_node(node_id),
                    proof_round=proof_rounds[node_id],
                    failure_class=result.get("failure_class"),
                    residual=str(result.get("residual", ""))[-800:],
                )
                raise RuntimeError(
                    "lemma backend infrastructure failure for "
                    f"{node_id}: {result.get('failure_class')}"
                )

            if not result.get("sealable_generation"):
                proof_rounds_by_node[node_id] = max(
                    0, proof_rounds_by_node[node_id] - 1,
                )
                persist_progress()
                raise RuntimeError(
                    "lemma backend returned non-sealable generation evidence "
                    f"for {node_id}"
                )

            if result["status"] == "green":
                statuses[node_id] = "green"
                proofs[node_id] = result["proof_body"]
                proof_feedback_by_node.pop(node_id, None)
                persist_progress()
                emit_event(
                    "node_green", theory=theory, node_id=node_id,
                    node=short_node(node_id), proof_round=proof_rounds[node_id],
                    green=sum(status == "green" for status in statuses.values()),
                    total=len(statuses),
                )
                continue

            emit_event(
                "node_red", theory=theory, node_id=node_id,
                node=short_node(node_id), proof_round=proof_rounds[node_id],
                residual=str(result.get("residual", ""))[-800:],
            )
            current_node = next(
                node for node in graph["nodes"] if node["id"] == node_id
            )
            proof_feedback_by_node[node_id] = compact_round_feedback(
                current_node, result, proof_rounds[node_id],
            )
            persist_progress()

            green_ids = {item for item, status in statuses.items() if status == "green"}
            node_revisions = node_revision_counts.get(node_id, 0)
            revised, decision = revise_graph(
                package, graph, result, green_ids, output_dir=theory_dir,
                provider=provider, model=revision_model,
                timeout=graph_timeout,
                decision_index=next_graph_decision_index(theory_dir),
                allow_structural_revision=(
                    node_revisions < max_node_revisions
                ),
                decision_context={
                    "phase": "node-proof",
                    "budget_scope": "node",
                    "budget_node_id": node_id,
                    "proof_round": proof_rounds[node_id],
                    "proof_attempt_limit_per_round": max_attempts,
                    "proof_retry_limit": max_node_rounds,
                    "proof_rounds_used": proof_rounds_by_node[node_id],
                    "proof_rounds_remaining": (
                        max_node_rounds - proof_rounds_by_node[node_id]
                    ),
                    "revisions_used": node_revisions,
                    "revisions_remaining": (
                        max_node_revisions - node_revisions
                    ),
                    "lemma_backend": lemma_backend,
                },
                premise_database=Path(run["premise_database"]),
            )
            emit_event(
                "graph_decision", theory=theory, node_id=node_id,
                node=short_node(node_id), decision=decision.get("decision"),
                failure_class=decision.get("failure_class"),
                reason=decision.get("reason"),
            )
            if decision["decision"] == "ABANDON_GENERATION":
                update_file(files, theory, "failed", failed_node=node_id)
                save_progress(run_dir, files)
                return next(item for item in files["files"] if item["theory"] == theory)
            if decision["decision"] == "RETRY_PROOF":
                gra_feedback_by_node[node_id] = pending_gra_feedback(
                    graph, current_node, decision,
                )
                persist_progress()
                continue
            if node_revisions >= max_node_revisions:
                update_file(
                    files, theory, "failed", failed_node=node_id,
                    reason="Graph Revision Agent requested a structural change "
                    "after this node's revision budget was exhausted",
                )
                save_progress(run_dir, files)
                return next(item for item in files["files"] if item["theory"] == theory)
            node_revision_counts[node_id] = node_revisions + 1
            credit_structural_evidence_round(proof_rounds_by_node, node_id)
            graph = revised
            update_file(
                files, theory, "generating", graph_revision=graph["revision"],
                graph_source=str(theory_dir / f"graph-v{graph['revision']}.json"),
            )
            save_progress(run_dir, files)
            statuses = {
                node["id"]: ("green" if node["id"] in green_ids else "planned")
                for node in graph["nodes"]
            }
            proof_rounds = {
                item: proof_rounds.get(item, 0) for item in statuses
            }
            proof_rounds_by_node = {
                item: proof_rounds_by_node.get(item, 0) for item in statuses
            }
            graph_revision_count = node_revision_counts.get("__graph__", 0)
            node_revision_counts = {
                item: node_revision_counts.get(item, 0) for item in statuses
            }
            if graph_revision_count:
                node_revision_counts["__graph__"] = graph_revision_count
            retrieval_hints = {
                item: retrieval_hints.get(item, "") for item in statuses
            }
            drop_stale_proof_evidence(
                graph, statuses, proof_rounds_by_node, retrieval_hints,
                proof_feedback_by_node, gra_feedback_by_node,
            )
            persist_progress()
            break
        else:
            complete = checker.check_complete(graph, proofs)
            write_json(theory_dir / "complete-check.json", complete)
            if complete["verdict"] != "green":
                stabilization = prepare_assembly_stabilization(
                    graph, proofs, complete,
                )
                write_json(
                    theory_dir / "assembly-stabilization.json", stabilization,
                )
                emit_event(
                    "assembly_stabilization_requested", theory=theory,
                    status=stabilization["status"],
                    failure_class=stabilization["failure_class"],
                    target_node_id=(
                        stabilization.get("target", {}).get("node_id")
                    ),
                    marker_count=stabilization["diagnostic_marker_count"],
                )
                target = stabilization.get("target", {}).get("node_id")
                suffix = f" at {target}" if target else " without a marker target"
                raise RuntimeError(
                    "all nodes were green but complete theory check failed" + suffix
                )
            impact_path = theory_dir / "retained-semantic-impact.json"
            impact = audit_theory(
                checker=checker, graph=graph, proofs=proofs,
                premise_snapshot_path=Path(run["premise_snapshot"]),
                output_path=impact_path,
            )
            if impact["verdict"] != "pass":
                raise RuntimeError("retained semantic impact audit did not pass")
            emit_event(
                "retained_semantic_impact_audited", theory=theory,
                affected_theorems=impact["actual_impact"][
                    "theorems_with_certificate_dependencies"
                ],
                used_retained_facts=impact["actual_impact"][
                    "used_retained_fact_count"
                ],
                report=str(impact_path),
            )
            checkpoint_theory = generated_checkpoint_theory(entry["file_slot"])
            source = checker.complete_source(
                graph, proofs, theory_name=checkpoint_theory,
            )
            generated_path = (
                run_dir / "generated" / f"{checkpoint_theory}.thy"
            )
            generated_path.write_text(source, encoding="utf-8")
            session_build = checker.build_generated_session([
                *sealed_ancestors, checkpoint_theory,
            ])
            write_json(theory_dir / "session-build.json", session_build)
            if session_build["verdict"] != "green":
                update_file(files, theory, "failed", reason="Generated_Refine build failed")
                save_progress(run_dir, files)
                return next(item for item in files["files"] if item["theory"] == theory)
            seal = create_theory_seal(
                run_dir, run, entry, generation_id, graph, generated_path,
                theory_dir,
            )
            publish_generated_records(
                run_dir, graph, seal["seal_id"], package,
            )
            update_file(
                files, theory, "sealed", generation_id=generation_id,
                seal_id=seal["seal_id"], graph_revision=graph["revision"],
                generated_source=str(generated_path),
                checkpoint_theory=checkpoint_theory,
                retained_semantic_impact=str(impact_path),
                theory_seal=str(theory_dir / "theory-seal.json"),
            )
            save_progress(run_dir, files)
            return next(item for item in files["files"] if item["theory"] == theory)


def run_file(
    run_dir: Path, theory: str, *, provider: str,
    planner_model: str, revision_model: str, prover_model: str,
    lemma_backend: str, timeout: int, max_attempts: int,
    max_node_rounds: int, max_node_revisions: int,
    graph_timeout: int = 900, think_timeout: int = 180,
    node_timeout: int = 300, salvage_provider: str | None = None,
) -> dict[str, Any]:
    lock = acquire_run_mutation_lock(
        run_dir, operation="run-file", theory=theory,
    )
    try:
        return _run_file_unlocked(
            run_dir, theory, provider=provider,
            planner_model=planner_model, revision_model=revision_model,
            prover_model=prover_model, lemma_backend=lemma_backend,
            timeout=timeout, max_attempts=max_attempts,
            max_node_rounds=max_node_rounds,
            max_node_revisions=max_node_revisions,
            graph_timeout=graph_timeout, think_timeout=think_timeout,
            node_timeout=node_timeout, salvage_provider=salvage_provider,
        )
    finally:
        release_run_mutation_lock(lock)


def _run_session_unlocked(
    run_dir: Path, *, retry_failed: bool = False, **run_file_options: Any,
) -> dict[str, Any]:
    from regen.execution.repository_execution import is_repository, run_session as run_repository_session
    if is_repository(run_dir):
        return run_repository_session(run_dir,retry_failed=retry_failed,**run_file_options)
    run = read_json(run_dir / "run.json")
    schedule = read_json(Path(run["session_schedule"]["source"]))
    results: list[dict[str, Any]] = []
    for theory in schedule["serial_fallback"]:
        ledger = load_progress(run_dir)
        entry = next(item for item in ledger["files"] if item["theory"] == theory)
        if entry["status"] == "sealed":
            results.append({"theory": theory, "status": "sealed", "action": "reuse"})
            continue
        if entry["status"] in {"failed", "planning_failed", "skipped"} and not retry_failed:
            results.append({
                "theory": theory, "status": entry["status"],
                "action": "preserve-failure",
            })
            continue
        try:
            result = run_file(run_dir, theory, **run_file_options)
            results.append({
                "theory": theory, "status": result["status"], "action": "run",
            })
        except Exception as error:
            ledger = load_progress(run_dir)
            update_file(
                ledger, theory, "failed",
                generation_id=theory_generation_id(run, entry["file_slot"]),
                reason=f"{type(error).__name__}: {error}",
            )
            save_progress(run_dir, ledger)
            results.append({
                "theory": theory, "status": "failed", "action": "run",
                "reason": f"{type(error).__name__}: {error}",
            })

    ledger = load_progress(run_dir)
    ledger["revision"] += 1
    ledger["session_status"] = (
        "complete"
        if all(item["status"] == "sealed" for item in ledger["files"])
        else "partial"
    )
    ledger["events"].append({
        "revision": ledger["revision"],
        "event": "session-traversal-complete",
        "run_id": run["run_id"],
        "session_status": ledger["session_status"],
    })
    save_progress(run_dir, ledger)
    return {
        "artifact_type": "session-traversal-result",
        "run_id": run["run_id"],
        "session_status": ledger["session_status"],
        "sealed": sum(item["status"] == "sealed" for item in ledger["files"]),
        "failed": sum(
            item["status"] in {"failed", "planning_failed"}
            for item in ledger["files"]
        ),
        "remaining": sum(
            item["status"] not in {"sealed", "failed", "planning_failed", "skipped"}
            for item in ledger["files"]
        ),
        "results": results,
        "session_progress_ledger": run["session_progress_ledger"],
    }


def run_session(
    run_dir: Path, *, retry_failed: bool = False, **run_file_options: Any,
) -> dict[str, Any]:
    lock = acquire_run_mutation_lock(run_dir, operation="run-session")
    try:
        return _run_session_unlocked(
            run_dir, retry_failed=retry_failed, **run_file_options,
        )
    finally:
        release_run_mutation_lock(lock)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--run-dir", type=Path, required=True)
    init.add_argument("--run-id", required=True)
    init.add_argument("--dataset-root", type=Path)
    init.add_argument(
        "--environment-run", type=Path,
        help="Legacy schema only; new repository runs use --dataset-root.",
    )
    init.add_argument("--schedule", type=Path)

    plan = sub.add_parser("plan-file")
    plan.add_argument("--run-dir", type=Path, required=True)
    plan.add_argument("--theory", required=True)
    plan.add_argument("--provider", default="codex")
    plan.add_argument("--planner-model", default="gpt-5.6-sol")
    plan.add_argument("--timeout", type=int, default=1800)

    def add_execution_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--run-dir", type=Path, required=True)
        command.add_argument("--provider", default="codex")
        command.add_argument(
            "--salvage-provider",
            choices=("deterministic", "codex", "codex-relay", "claude"),
            help="Opt in to separate local proof-outline salvage; default sends diagnostics directly to the Prover.",
        )
        command.add_argument("--planner-model", default="gpt-5.6-sol")
        command.add_argument("--revision-model", default="gpt-5.6-sol")
        command.add_argument("--prover-model", default=None, help="Defaults to the run-frozen Lemma Prover model.")
        command.add_argument(
            "--lemma-backend", choices=("stepwise", "whole-proof"),
            default="whole-proof",
        )
        command.add_argument(
            "--timeout", type=int, default=1800,
            help="General provider and Stepwise wall-time budget.",
        )
        command.add_argument(
            "--node-timeout", type=int, default=300,
            help="Wall-time limit for one Isabelle proof-candidate check.",
        )
        command.add_argument(
            "--graph-timeout", type=int, default=900,
            help="Wall-time budget for whole-DAG statement-shell elaboration.",
        )
        command.add_argument(
            "--think-timeout", type=int, default=180,
            help="Hard wall-time limit for one Stepwise proof-state model call.",
        )
        command.add_argument("--max-attempts", type=int, default=5)
        command.add_argument("--max-node-rounds", type=int, default=5)
        command.add_argument("--max-node-revisions", type=int, default=8)
        command.add_argument("--unlimited-gra-revisions", action="store_true",
            help="Disable per-node and generation-wide GRA proposal count limits for repository runs; preserve counters and timeouts.")

    execute = sub.add_parser("run-file")
    add_execution_arguments(execute)
    execute.add_argument("--theory", required=True)

    session = sub.add_parser("run-session")
    add_execution_arguments(session)
    session.add_argument(
        "--retry-failed", action="store_true",
        help="Retry entries already marked failed instead of preserving them.",
    )
    session.add_argument(
        "--max-parallel-theories", type=int, default=1,
        help="Run this many dependency-ready theories concurrently (direct repository plans only).",
    )
    batch = sub.add_parser("batch-file", help="Run one continuing File Planner with atomic proof batches")
    batch.add_argument("--run-dir", type=Path, required=True)
    batch.add_argument("--theory", required=True)
    batch.add_argument("--provider", default="codex")
    batch.add_argument("--planner-model", default=None)
    batch.add_argument("--call-timeout", type=int, default=600)
    batch.add_argument("--check-timeout", type=int, default=900)
    batch.add_argument("--candidate-timeout", type=int, default=120,
                       help="Seconds per candidate proof batch; separate from graph and final seal checks")
    batch.add_argument("--max-requests", type=int, default=0,
                       help="Checkpoint after this many requests; 0 means no count limit")
    args = parser.parse_args()
    if args.command == "init" and bool(args.dataset_root) == bool(args.environment_run):
        parser.error("init requires exactly one of --dataset-root or --environment-run")

    try:
        if args.command == "init":
            result = init_run(
                args.run_dir, args.dataset_root or args.environment_run,
                run_id=args.run_id,
                schedule_path=args.schedule,
            )
        elif args.command == "batch-file":
            from regen.planning.batch_file_planner import run_batch_file
            result = run_batch_file(args.run_dir, args.theory, provider=args.provider,
                model=args.planner_model, call_timeout=args.call_timeout,
                check_timeout=args.check_timeout, candidate_timeout=args.candidate_timeout,
                max_requests=args.max_requests)
        elif args.command == "plan-file":
            result = plan_run_file(
                args.run_dir, args.theory, provider=args.provider,
                planner_model=args.planner_model, timeout=args.timeout,
            )
        else:
            if args.unlimited_gra_revisions:
                from regen.execution.repository_execution import is_repository
                if not is_repository(args.run_dir):
                    parser.error('--unlimited-gra-revisions requires a repository projection run')
            options = {
                "provider": args.provider,
                "planner_model": args.planner_model,
                "revision_model": args.revision_model,
                "prover_model": args.prover_model or frozen_role_model(read_json(args.run_dir / "run.json"), "Lemma Prover"),
                "lemma_backend": args.lemma_backend,
                "timeout": args.timeout,
                "max_attempts": args.max_attempts,
                "max_node_rounds": args.max_node_rounds,
                "max_node_revisions": None if args.unlimited_gra_revisions else args.max_node_revisions,
                "graph_timeout": args.graph_timeout,
                "think_timeout": args.think_timeout,
                "node_timeout": args.node_timeout,
                "salvage_provider": args.salvage_provider,
            }
            if args.command == "run-file":
                result = run_file(args.run_dir, args.theory, **options)
            else:
                result = run_session(
                    args.run_dir, retry_failed=args.retry_failed,
                    max_parallel_theories=args.max_parallel_theories, **options,
                )
    except BaseException as error:
        emit_event(
            "runner_failed", error_type=type(error).__name__, error=str(error),
        )
        close_event_monitor("failed")
        raise
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
