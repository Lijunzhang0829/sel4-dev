#!/usr/bin/env python3
"""Session Runner for the modular Refine theorem-generation pipeline."""
from __future__ import annotations

import argparse
import json
import re
import sys
import shutil
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "regen") not in sys.path:
    sys.path.insert(0, str(REPO / "regen"))

from regen.file_planner import plan_file, planning_environment
from regen.assembly_stabilizer import prepare_assembly_stabilization
from regen.graph_revision_agent import prior_node_decisions, revise_graph
from regen.isabelle_node_checker import IsabelleChecker
from regen.lemma_prover import prove_node
from regen.progress_events import emit_event, short_node
from regen.proof_graph import (
    definitional_wrapper_risks, executable_nodes, validate_graph,
)


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
) -> bool:
    return bool(
        feedback
        and feedback.get("statement") == node["statement"]
        and feedback.get("dependencies") == node.get("dependencies", [])
    )


def drop_stale_proof_evidence(
    graph: dict[str, Any], statuses: dict[str, str],
    proof_rounds_by_node: dict[str, int], retrieval_hints: dict[str, str],
    proof_feedback_by_node: dict[str, dict[str, Any]],
) -> list[str]:
    by_id = {node["id"]: node for node in graph["nodes"]}
    reset = []
    for node_id, status in statuses.items():
        if status == "green" or proof_rounds_by_node.get(node_id, 0) <= 0:
            continue
        if feedback_matches_node(by_id[node_id], proof_feedback_by_node.get(node_id)):
            continue
        proof_rounds_by_node[node_id] = 0
        retrieval_hints.pop(node_id, None)
        proof_feedback_by_node.pop(node_id, None)
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


def package_paths(environment_run: Path) -> list[Path]:
    return sorted(
        (environment_run / "M1" / "public" / "theory-packages").glob("*.json")
    )


def generation_packages(environment_run: Path) -> list[dict[str, Any]]:
    return [
        package
        for package in (read_json(path) for path in package_paths(environment_run))
        if package.get("contracts") or package.get("framework_obligations")
    ]


def thin_environment_gate(environment_run: Path) -> dict[str, Any]:
    m2 = read_json(environment_run / "M2" / "environment-gate.json")
    snapshot = read_json(environment_run / "M3" / "premise-snapshot.json")
    database = environment_run / "M3" / "premises.sqlite3"
    if m2.get("verdict") != "pass" or not database.is_file():
        raise ValueError(
            "Semantic Base Isabelle environment and premise database must be PASS"
        )
    packages = generation_packages(environment_run)
    if snapshot.get("artifact_type") != "semantic-base-premise-snapshot":
        raise ValueError("invalid Semantic Base premise snapshot")
    return {
        "clean_environment": "pass",
        "premise_database": "pass",
        "generation_files": len(packages),
        "contracts": sum(len(item.get("contracts", [])) for item in packages),
        "base_session": m2["checker_manifest"]["base_session"],
    }

def schedule_entries(
    environment_run: Path, schedule_path: Path | None,
) -> list[dict[str, Any]]:
    packages = [read_json(path) for path in package_paths(environment_run)]
    by_theory = {item["theory"]: item for item in packages}
    if schedule_path is None:
        return [{
            "theory": item["theory"], "file_slot": item["file_slot"],
            "layer": 0, "status": "pending",
        } for item in packages]
    schedule = read_json(schedule_path)
    result = []
    for layer in schedule["layers"]:
        for item in layer["theories"]:
            if item["theory"] not in by_theory:
                raise ValueError(f"schedule names an unknown generation theory: {item['theory']}")
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
        return {"source": "package-order-fallback", "session_plan_bound": False}
    schedule_path = schedule_path.resolve()
    schedule = read_json(schedule_path)
    gate_path = schedule_path.parent / "m4-gate.json"
    valid_artifacts = {
        "refine-file-generation-list", "semantic-base-file-generation-list",
    }
    if schedule.get("artifact_type") not in valid_artifacts:
        raise ValueError("Session Planner output is not a generation list")
    if schedule.get("artifact_type") == "refine-file-generation-list" and not schedule.get("immutable"):
        raise ValueError("Session Planner file list is not marked immutable")
    if schedule.get("artifact_type") == "semantic-base-file-generation-list":
        gate = {"verdict": "pass", "source": "deterministic-semantic-base-m4"}
    else:
        gate = read_json(gate_path)
        if gate.get("verdict") != "pass":
            raise ValueError("Session Planner file-list check is not PASS")
    environment_campaign = (
        read_json(environment_run / "M0" / "campaign-manifest.json")["campaign_id"]
        if (environment_run / "M0" / "campaign-manifest.json").is_file()
        else read_json(environment_run / "M1" / "environment-manifest.json")["campaign_id"]
    )
    if schedule.get("campaign_id") != environment_campaign:
        raise ValueError("Session Planner file list belongs to another environment")
    return {
        "source": str(schedule_path),
        "session_plan_bound": True,
        "campaign_id": schedule["campaign_id"],
        "layer_count": schedule.get("layer_count", len(schedule["layers"])),
        "file_count": sum(
            len(layer["theories"]) for layer in schedule["layers"]
        ),
        "gate": (
            str(gate_path)
            if gate_path.is_file()
            else "deterministic-semantic-base-m4"
        ),
    }


def init_run(
    run_dir: Path, environment_run: Path, *, run_id: str,
    schedule_path: Path | None = None,
) -> dict[str, Any]:
    environment_run = environment_run.resolve()
    source_heaps = (
        environment_run / "M2" / "isolated-environment" / "run-heaps"
    )
    if not source_heaps.is_dir():
        raise ValueError(
            "M2 PASS metadata exists, but its executable run-heaps directory is missing"
        )
    gate = thin_environment_gate(environment_run)
    binding = schedule_binding(environment_run, schedule_path)
    run = {
        "schema_version": 1,
        "run_id": run_id,
        "target": "Refine",
        "environment_run": str(environment_run.resolve()),
        "premise_database": str(
            (environment_run / "M3" / "premises.sqlite3").resolve()
        ),
        "state": "ready",
        "environment_gate": gate,
        "session_schedule": binding,
    }
    files = {
        "run_id": run_id,
        "session_schedule": binding,
        "files": schedule_entries(environment_run, schedule_path),
        "events": [],
    }
    write_json(run_dir / "run.json", run)
    write_json(run_dir / "files.json", files)
    if not (run_dir / "heaps").exists():
        shutil.copytree(source_heaps, run_dir / "heaps")
    (run_dir / "theories").mkdir(parents=True, exist_ok=True)
    (run_dir / "generated").mkdir(parents=True, exist_ok=True)
    return run


def update_file(
    files: dict[str, Any], theory: str, status: str, **extra: Any,
) -> None:
    entry = next(item for item in files["files"] if item["theory"] == theory)
    if status in {"built", "sealed"}:
        entry.pop("failed_node", None)
        entry.pop("reason", None)
    entry.update({"status": status, **extra})
    files["events"].append({"theory": theory, "status": status})


def package_for(environment_run: Path, file_slot: str) -> dict[str, Any]:
    return read_json(
        environment_run / "M1" / "public" / "theory-packages"
        / f"{file_slot}.json"
    )


def publish_generated_records(
    run_dir: Path, graph: dict[str, Any],
) -> None:
    path = run_dir / "generated-premises.json"
    value = read_json(path) if path.exists() else {"records": []}
    existing = {item["record_id"] for item in value["records"]}
    for node in graph["nodes"]:
        record_id = f"generated:{graph['theory']}:{node['name']}"
        if record_id in existing:
            continue
        value["records"].append({
            "record_id": record_id,
            "canonical_name": f"{graph['theory']}.{node['name']}",
            "exact_statement": node["statement"],
            "owner_theory": graph["theory"],
            "owner_session": "Generated_Refine",
            "source_class": "generated_theorem",
            "attributes": [],
            "theory_seal_id": (
                f"seal:{graph['theory']}:graph-v{graph['revision']}"
            ),
        })
    value["records"].sort(key=lambda item: item["record_id"])
    write_json(path, value)


def plan_run_file(
    run_dir: Path, theory: str, *, provider: str,
    planner_model: str, timeout: int,
) -> dict[str, Any]:
    emit_event(
        "runner_started", run_dir=str(run_dir), theory=theory,
        provider=provider, lemma_backend=lemma_backend,
    )
    run = read_json(run_dir / "run.json")
    files = read_json(run_dir / "files.json")
    entry = next(item for item in files["files"] if item["theory"] == theory)
    environment_run = Path(run["environment_run"])
    theory_dir = run_dir / "theories" / entry["file_slot"].removesuffix(".thy")
    graph_path = theory_dir / "graph-v1.json"
    if entry["status"] == "planned" and graph_path.is_file():
        return read_json(graph_path)
    package = package_for(environment_run, entry["file_slot"])
    environment = planning_environment(
        package, Path(run["premise_database"]),
    )
    theory_dir.mkdir(parents=True, exist_ok=True)
    first_call_index = 1 + len(list(
        (theory_dir / "llm").glob("file-plan-v1*.meta.json")
    ))
    update_file(files, theory, "planning")
    write_json(run_dir / "files.json", files)
    try:
        rejections: list[str] = []
        for offset in range(3):
            try:
                graph = plan_file(
                    package, run_id=run["run_id"], output_dir=theory_dir,
                    provider=provider, model=planner_model, timeout=timeout,
                    environment=environment,
                    module_log_path=(
                        run_dir / "module-logs/file-planner"
                        / f"{entry['file_slot'].removesuffix('.thy')}.json"
                    ),
                    call_index=first_call_index + offset,
                    rejection_feedback=rejections,
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
        files = read_json(run_dir / "files.json")
        update_file(files, theory, "planning_failed")
        write_json(run_dir / "files.json", files)
        raise
    files = read_json(run_dir / "files.json")
    update_file(
        files, theory, "planned", graph_revision=graph["revision"],
        graph_source=str(graph_path),
    )
    write_json(run_dir / "files.json", files)
    return graph


def run_file(
    run_dir: Path, theory: str, *, provider: str,
    planner_model: str, revision_model: str, prover_model: str,
    lemma_backend: str, timeout: int, max_attempts: int,
    max_node_rounds: int, max_node_revisions: int,
    graph_timeout: int = 900, think_timeout: int = 180,
    salvage_provider: str | None = None,
) -> dict[str, Any]:
    emit_event(
        "runner_started", run_dir=str(run_dir), theory=theory,
        provider=provider, lemma_backend=lemma_backend,
    )
    run = read_json(run_dir / "run.json")
    run["node_budget"] = {
        "policy": "per-node-doubled-v1",
        "whole_proof_attempts_per_round": max_attempts,
        "prover_rounds_per_node": max_node_rounds,
        "structural_revisions_per_node": max_node_revisions,
        "stepwise_round_timeout_seconds": timeout,
        "stepwise_think_timeout_seconds": min(timeout, think_timeout),
        "whole_proof_attempt_ceiling_per_node": (
            max_attempts * max_node_rounds
        ),
    }
    write_json(run_dir / "run.json", run)
    files = read_json(run_dir / "files.json")
    entry = next(item for item in files["files"] if item["theory"] == theory)
    if entry["status"] == "built":
        return entry
    environment_run = Path(run["environment_run"])
    package = package_for(environment_run, entry["file_slot"])
    theory_dir = run_dir / "theories" / entry["file_slot"].removesuffix(".thy")
    theory_dir.mkdir(parents=True, exist_ok=True)
    graph_path = active_graph_path(entry, theory_dir)
    if entry["status"] in {"planned", "failed", "generating"} and graph_path:
        graph = read_json(graph_path)
    else:
        graph = plan_run_file(
            run_dir, theory, provider=provider,
            planner_model=planner_model, timeout=timeout,
        )
        files = read_json(run_dir / "files.json")
        graph_path = theory_dir / "graph-v1.json"
    update_file(
        files, theory, "generating", graph_revision=graph["revision"],
        graph_source=str(graph_path),
    )
    write_json(run_dir / "files.json", files)

    built = [
        item["file_slot"].removesuffix(".thy") for item in files["files"]
        if item["status"] == "built"
    ]
    m2_gate = read_json(environment_run / "M2" / "environment-gate.json")
    base_session = m2_gate["checker_manifest"]["base_session"]
    checker_options = {
        "base_session": base_session,
        "base_import": f"{base_session}.Semantic_Root",
        "base_catalog": "/m2/semantic-base",
    }
    checker = IsabelleChecker(
        environment_run, theory_dir / "isabelle", timeout=timeout,
        previous_theories=built,
        generated_dir=run_dir / "generated",
        heaps_dir=run_dir / "heaps",
        **checker_options,
    )
    graph_checker = IsabelleChecker(
        environment_run, theory_dir / "isabelle", timeout=graph_timeout,
        previous_theories=built,
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
            for node_id in known_nodes - proof_feedback_by_node.keys():
                feedback = recover_round_feedback(
                    theory_dir, graph, node_id,
                )
                if feedback is not None:
                    proof_feedback_by_node[node_id] = feedback
            drop_stale_proof_evidence(
                graph, statuses, proof_rounds_by_node, retrieval_hints,
                proof_feedback_by_node,
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
        })

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
                write_json(run_dir / "files.json", files)
                return next(item for item in files["files"] if item["theory"] == theory)
            graph, decision = revise_graph(
                package, graph, {
                    "status": "red",
                    "node_id": graph_check.get("failed_node_id") or "graph",
                    "residual": graph_check["diagnostic"], "attempts": [],
                }, green_ids, output_dir=theory_dir, provider=provider,
                model=revision_model, timeout=timeout,
                decision_index=next_graph_decision_index(theory_dir),
                allow_structural_revision=(
                    graph_revisions < max_node_revisions
                ),
                decision_context={
                    "phase": "statement-certification",
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
            if decision["decision"] == "RETRY_PROOF":
                if decision.get("repair_guidance"):
                    graph_revision_rejections.append(
                        decision["repair_guidance"]
                    )
                continue
            emit_event(
                "graph_decision", theory=theory, node_id=node_id,
                node=short_node(node_id), decision=decision.get("decision"),
                failure_class=decision.get("failure_class"),
                reason=decision.get("reason"),
            )
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
                write_json(run_dir / "files.json", files)
                return next(item for item in files["files"] if item["theory"] == theory)
            node_revision_counts["__graph__"] = graph_revisions + 1
            graph_revision_rejections = []
            update_file(
                files, theory, "generating", graph_revision=graph["revision"],
                graph_source=str(theory_dir / f"graph-v{graph['revision']}.json"),
            )
            write_json(run_dir / "files.json", files)
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
                proof_feedback_by_node,
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
                    model=revision_model, timeout=timeout,
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
                    write_json(run_dir / "files.json", files)
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
                    write_json(run_dir / "files.json", files)
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
                write_json(run_dir / "files.json", files)
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
                    proof_feedback_by_node,
                )
                persist_progress()
                break
            if proof_rounds_by_node.get(node_id, 0) >= max_node_rounds:
                update_file(
                    files, theory, "failed", failed_node=node_id,
                    reason="per-node proof round budget exhausted",
                )
                write_json(run_dir / "files.json", files)
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
            try:
                result = prove_node(
                    graph, node_id, proofs, checker=checker,
                    premise_database=Path(run["premise_database"]),
                    output_dir=theory_dir,
                    generated_records=run_dir / "generated-premises.json",
                    provider=provider, model=prover_model, timeout=timeout,
                    max_attempts=max_attempts, backend=lemma_backend,
                    round_id=proof_rounds[node_id],
                    retrieval_hint=retrieval_hints.get(node_id, ""),
                    salvage_provider=salvage_provider,
                    think_timeout=think_timeout,
                    previous_round_feedback=(
                        hydrate_feedback_candidates(
                            theory_dir, node_id,
                            proof_feedback_by_node.get(node_id),
                        )
                        if proof_feedback_by_node.get(node_id, {}).get("statement")
                        == next(
                            node["statement"] for node in graph["nodes"]
                            if node["id"] == node_id
                        )
                        else None
                    ),
                )
            except BaseException:
                # An interrupted/crashed backend produced no GREEN/RED result and
                # therefore must not consume a logical lemma retry.
                proof_rounds_by_node[node_id] -= 1
                persist_progress()
                raise
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
                provider=provider, model=revision_model, timeout=timeout,
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
                write_json(run_dir / "files.json", files)
                return next(item for item in files["files"] if item["theory"] == theory)
            if decision["decision"] == "RETRY_PROOF":
                retrieval_hints[node_id] = " ".join(filter(None, [
                    decision.get("retrieval_query", ""),
                    decision.get("repair_guidance", ""),
                ]))
                continue
            if node_revisions >= max_node_revisions:
                update_file(
                    files, theory, "failed", failed_node=node_id,
                    reason="Graph Revision Agent requested a structural change "
                    "after this node's revision budget was exhausted",
                )
                write_json(run_dir / "files.json", files)
                return next(item for item in files["files"] if item["theory"] == theory)
            node_revision_counts[node_id] = node_revisions + 1
            credit_structural_evidence_round(proof_rounds_by_node, node_id)
            graph = revised
            update_file(
                files, theory, "generating", graph_revision=graph["revision"],
                graph_source=str(theory_dir / f"graph-v{graph['revision']}.json"),
            )
            write_json(run_dir / "files.json", files)
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
                proof_feedback_by_node,
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
            source = checker.complete_source(graph, proofs)
            generated_path = run_dir / "generated" / entry["file_slot"]
            generated_path.write_text(source, encoding="utf-8")
            session_build = checker.build_generated_session([
                *built, entry["file_slot"].removesuffix(".thy"),
            ])
            write_json(theory_dir / "session-build.json", session_build)
            if session_build["verdict"] != "green":
                update_file(files, theory, "failed", reason="Generated_Refine build failed")
                write_json(run_dir / "files.json", files)
                return next(item for item in files["files"] if item["theory"] == theory)
            publish_generated_records(run_dir, graph)
            update_file(
                files, theory, "built", graph_revision=graph["revision"],
                generated_source=str(generated_path),
            )
            write_json(run_dir / "files.json", files)
            return next(item for item in files["files"] if item["theory"] == theory)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--run-dir", type=Path, required=True)
    init.add_argument("--run-id", required=True)
    init.add_argument("--environment-run", type=Path, required=True)
    init.add_argument("--schedule", type=Path)

    plan = sub.add_parser("plan-file")
    plan.add_argument("--run-dir", type=Path, required=True)
    plan.add_argument("--theory", required=True)
    plan.add_argument("--provider", default="codex")
    plan.add_argument("--planner-model", default="gpt-5.5")
    plan.add_argument("--timeout", type=int, default=1800)

    execute = sub.add_parser("run-file")
    execute.add_argument("--run-dir", type=Path, required=True)
    execute.add_argument("--theory", required=True)
    execute.add_argument("--provider", default="codex")
    execute.add_argument(
        "--salvage-provider",
        choices=("deterministic", "codex", "codex-relay", "claude"),
        help="Optional provider used only for local proof-outline salvage.",
    )
    execute.add_argument("--planner-model", default="gpt-5.5")
    execute.add_argument("--revision-model", default="gpt-5.5")
    execute.add_argument("--prover-model", default="gpt-5.5")
    execute.add_argument(
        "--lemma-backend", choices=("stepwise", "whole-proof"),
        default="stepwise",
    )
    execute.add_argument("--timeout", type=int, default=1800)
    execute.add_argument(
        "--graph-timeout", type=int, default=900,
        help="Wall-time budget for whole-DAG statement-shell elaboration.",
    )
    execute.add_argument(
        "--think-timeout", type=int, default=180,
        help="Hard wall-time limit for one Stepwise proof-state model call.",
    )
    execute.add_argument("--max-attempts", type=int, default=5)
    execute.add_argument("--max-node-rounds", type=int, default=5)
    execute.add_argument("--max-node-revisions", type=int, default=8)
    args = parser.parse_args()

    if args.command == "init":
        result = init_run(
            args.run_dir, args.environment_run, run_id=args.run_id,
            schedule_path=args.schedule,
        )
    elif args.command == "plan-file":
        result = plan_run_file(
            args.run_dir, args.theory, provider=args.provider,
            planner_model=args.planner_model, timeout=args.timeout,
        )
    else:
        result = run_file(
            args.run_dir, args.theory, provider=args.provider,
            planner_model=args.planner_model,
            revision_model=args.revision_model,
            prover_model=args.prover_model,
            lemma_backend=args.lemma_backend,
            timeout=args.timeout, max_attempts=args.max_attempts,
            max_node_rounds=args.max_node_rounds,
            max_node_revisions=args.max_node_revisions,
            graph_timeout=args.graph_timeout,
            think_timeout=args.think_timeout,
            salvage_provider=args.salvage_provider,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
