#!/usr/bin/env python3
"""Plan Refine proof repositories from verified environment dataflow; replay legacy schedules."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from regen.preparation.environment_gate import read_json, sha256_path, validate_environment_gate
from regen.infrastructure.llm_transcript import (
    allocate_artifact_prefix,
    artifact_paths,
    call_codex,
    reconcile_transcript_family,
    validate_call_artifact,
)
from regen.preparation.refine_campaign import sha256_json, write_immutable_json


REPO = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = Path("regen/config/session-planning-policy.json")
SYMBOL_RE = re.compile(r"[A-Za-z][A-Za-z0-9_'.]*")
GENERIC_EDGE_SYMBOLS = {
    "lemma", "theorem", "corollary", "corres", "ccorres", "connect",
    "True", "False", "if", "then", "else", "case", "of", "dc",
}
FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def payload_without(value: dict[str, Any], field: str) -> dict[str, Any]:
    result = dict(value)
    result.pop(field, None)
    return result


def verify_payload_hash(value: dict[str, Any], field: str) -> None:
    if value.get(field) != sha256_json(payload_without(value, field)):
        raise ValueError(f"payload hash mismatch: {value.get('artifact_type')}")


def verify_policy_frozen(
    repo: Path, environment_root: Path, policy_path: Path,
) -> tuple[dict[str, Any], str]:
    manifest = read_json(
        environment_root / "Environment-Freeze/campaign-manifest.json"
    )
    policy = read_json(policy_path)
    if (
        policy.get("artifact_type") != "session-planning-policy"
        or policy.get("stage") != "Refine"
    ):
        raise ValueError("invalid Refine Session Planning policy")
    resolved = policy_path.resolve()
    snapshot_root = (environment_root / "Environment-Freeze/frozen-inputs").resolve()
    if resolved.is_relative_to(snapshot_root):
        relative = str(resolved.relative_to(snapshot_root))
    else:
        relative = str(resolved.relative_to(repo.resolve()))
    frozen = {item["path"]: item["sha256"] for item in manifest["frozen_inputs"]}
    digest = sha256_path(policy_path)
    if frozen.get(relative) != digest:
        raise ValueError("Session Planning policy is not frozen by Environment Freeze")
    return policy, digest


def select_completed_transcript(
    preferred: Path, *, prompt: str, model: str,
) -> tuple[Path, dict[str, Any]] | None:
    """Return the newest sealable physical call for one logical planner call."""
    expected_prompt = prompt.encode("utf-8")
    for item in reversed(reconcile_transcript_family(preferred)):
        if item["underlying_state"] != "complete":
            continue
        prefix = Path(item["prefix"])
        try:
            meta = validate_call_artifact(prefix, require_real=True)
        except ValueError:
            continue
        paths = artifact_paths(prefix)
        if paths["prompt"].read_bytes() != expected_prompt:
            continue
        if meta.get("model") != model:
            continue
        return prefix, meta
    return None


def generation_tasks(
    environment_root: Path, gate: dict[str, Any],
) -> list[dict[str, Any]]:
    directory = environment_root / gate["contract_packages"]["path"]
    tasks = []
    seen_contracts: set[str] = set()
    for path in sorted(directory.glob("*.json")):
        package = read_json(path)
        if package.get("campaign_id") != gate["campaign_id"]:
            raise ValueError(f"contract package belongs to another campaign: {path}")
        if package.get("artifact_type") != "contract-package":
            raise ValueError(f"invalid contract package: {path}")
        contracts = package.get("contracts", [])
        if not contracts:
            continue
        slot = package.get("file_slot")
        theory = package.get("theory")
        if path.name != f"{slot}.json":
            raise ValueError(f"package path does not match file slot: {path}")
        normalized_contracts = []
        for contract in contracts:
            contract_id = contract.get("contract_id")
            if not isinstance(contract_id, str) or contract_id in seen_contracts:
                raise ValueError(f"duplicate or invalid contract ID: {contract_id}")
            if (
                contract.get("owner_file_slot") not in {None, slot}
                or contract.get("owner_theory") not in {None, theory}
            ):
                raise ValueError(f"contract owner mismatch: {contract_id}")
            seen_contracts.add(contract_id)
            normalized_contracts.append({
                "contract_id": contract_id,
                "name": contract.get("name", ""),
                "exact_statement": contract.get("exact_statement", ""),
            })
        tasks.append({
            "theory": theory,
            "file_slot": slot,
            "contract_ids": [item["contract_id"] for item in normalized_contracts],
            "root_ids": [item["contract_id"] for item in normalized_contracts],
            "contracts": normalized_contracts,
        })
    if len(tasks) != gate["contract_packages"]["package_count"]:
        raise ValueError("generation tasks do not cover every contract package")
    if len(seen_contracts) != gate["contract_packages"]["contract_count"]:
        raise ValueError("generation tasks do not cover every fixed contract")
    return sorted(tasks, key=lambda item: item["theory"])


def planner_payload(
    gate: dict[str, Any], tasks: list[dict[str, Any]], policy: dict[str, Any],
    policy_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_type": "session-planner-input",
        "campaign_id": gate["campaign_id"],
        "environment_gate_id": gate["gate_id"],
        "contract_package_payload_sha256": gate["contract_packages"]["payload_sha256"],
        "premise_snapshot_identity_only": {
            "snapshot_id": gate["premise_catalog"]["snapshot_id"],
            "record_count": gate["premise_catalog"]["record_count"],
            "note": "Premise records and retrieval statistics are not planning inputs.",
        },
        "policy_sha256": policy_sha256,
        "theories": [{
            "theory": task["theory"],
            "file_slot": task["file_slot"],
            "contracts": task["contracts"],
        } for task in tasks],
        "policy": policy,
    }


def planner_prompt(payload: dict[str, Any]) -> str:
    schema = {
        "schema_version": 1,
        "candidate_edges": [{
            "provider": "Refine.Example_A",
            "consumer": "Refine.Example_B",
            "type": "contract-composition | operation-call",
            "confidence": 0.75,
            "contract_ids": ["provider contract ID", "consumer contract ID"],
            "symbols": ["identifier_visible_in_the_cited_contracts"],
            "reason": "brief explanation grounded only in the cited contracts",
        }],
        "notes": ["optional uncertainty note"],
    }
    return (
        "You are the Repo/Session-level scheduler for a sanitized Isabelle/seL4 "
        "Refine regeneration campaign. The payload contains the complete legal "
        "planning input: fixed theory slots and exact contract statements.\n\n"
        "Propose candidate ordering evidence only. Do not emit the final schedule.\n"
        "- contract-composition: the provider exposes a smaller correspondence "
        "that credibly composes into the consumer; this may create a later layer.\n"
        "- operation-call: visible contract surfaces suggest callee before caller; "
        "this is weak same-layer priority evidence, never a proof dependency.\n\n"
        "Hard rules:\n"
        "1. Use only theory names, contract IDs, names, and statements in the payload.\n"
        "2. Do not use remembered seL4 imports, source layout, helper names, human "
        "proof order, premise records, or human proof architecture.\n"
        "3. Every edge must cite at least one current contract owned by each endpoint.\n"
        "4. Every symbol must be one Isabelle identifier and occur as a complete "
        "identifier token in a cited contract name or statement.\n"
        "At least one non-generic symbol must occur in cited contracts from both endpoints.\n"
        "5. Omit uncertain edges. Zero edges is valid.\n"
        "6. Do not propose imports, theorem statements, helpers, tactics, or files.\n"
        "7. Return JSON only, with at most the policy budget and this shape:\n"
        f"{json.dumps(schema, ensure_ascii=False, sort_keys=True)}\n\n"
        "LEGAL PAYLOAD:\n"
        f"{json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}\n"
    )


def parse_model_json(text: str) -> dict[str, Any]:
    cleaned = FENCE_RE.sub("", text.strip())
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Session Planner response contains no JSON object")
        value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("Session Planner response must be a JSON object")
    return value


def symbol_is_grounded(symbol: str, contracts: list[dict[str, Any]]) -> bool:
    if not SYMBOL_RE.fullmatch(symbol):
        return False
    return any(
        symbol in set(SYMBOL_RE.findall(
            f"{contract['name']} {contract['exact_statement']}"
        ))
        for contract in contracts
    )


def validate_candidate_edges(
    response: dict[str, Any], tasks: list[dict[str, Any]], policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_edges = response.get("candidate_edges")
    if response.get("schema_version") != 1 or not isinstance(raw_edges, list):
        raise ValueError("invalid Session Planner response schema")
    maximum = int(policy["model"]["max_candidate_edges"])
    if len(raw_edges) > maximum:
        raise ValueError(f"candidate edge budget exceeded: {len(raw_edges)} > {maximum}")
    by_theory = {item["theory"]: item for item in tasks}
    contracts = {
        contract["contract_id"]: {**contract, "owner": task["theory"]}
        for task in tasks for contract in task["contracts"]
    }
    minimum = float(policy["model"]["minimum_confidence"])
    accepted, rejected = [], []
    seen: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(raw_edges):
        if not isinstance(raw, dict):
            rejected.append({
                "candidate_index": index, "candidate": raw,
                "reasons": ["not-an-object"],
            })
            continue
        reasons = []
        provider, consumer = raw.get("provider"), raw.get("consumer")
        edge_type = raw.get("type")
        if provider not in by_theory:
            reasons.append("unknown-provider")
        if consumer not in by_theory:
            reasons.append("unknown-consumer")
        if provider == consumer:
            reasons.append("self-edge")
        if edge_type not in {"contract-composition", "operation-call"}:
            reasons.append("unsupported-edge-type")
        confidence = raw.get("confidence")
        if (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not 0 <= confidence <= 1
        ):
            reasons.append("invalid-confidence")
        elif confidence < minimum:
            reasons.append("below-confidence-threshold")
        cited_ids = raw.get("contract_ids")
        cited = []
        if not isinstance(cited_ids, list) or not cited_ids or not all(
            isinstance(item, str) for item in cited_ids
        ):
            reasons.append("invalid-contract-citations")
        else:
            unknown = set(cited_ids) - contracts.keys()
            if unknown:
                reasons.append("unknown-contract-citation")
            else:
                cited = [contracts[item] for item in cited_ids]
                owners = {item["owner"] for item in cited}
                if provider not in owners or consumer not in owners:
                    reasons.append("citations-do-not-cover-both-endpoints")
                if owners - {provider, consumer}:
                    reasons.append("citation-owned-by-third-theory")
        symbols = raw.get("symbols")
        if not isinstance(symbols, list) or not symbols or not all(
            isinstance(item, str) for item in symbols
        ):
            reasons.append("invalid-symbol-evidence")
        elif cited:
            if not all(symbol_is_grounded(item, cited) for item in symbols):
                reasons.append("ungrounded-symbol-evidence")
            provider_contracts = [
                item for item in cited if item["owner"] == provider
            ]
            consumer_contracts = [
                item for item in cited if item["owner"] == consumer
            ]
            if not any(
                symbol not in GENERIC_EDGE_SYMBOLS
                and symbol_is_grounded(symbol, provider_contracts)
                and symbol_is_grounded(symbol, consumer_contracts)
                for symbol in symbols
            ):
                reasons.append("no-shared-endpoint-symbol")
        if not isinstance(raw.get("reason"), str) or not raw["reason"].strip():
            reasons.append("missing-reason")
        key = (str(provider), str(consumer), str(edge_type))
        if key in seen:
            reasons.append("duplicate-edge")
        if reasons:
            rejected.append({
                "candidate_index": index, "candidate": raw,
                "reasons": sorted(set(reasons)),
            })
            continue
        seen.add(key)
        accepted.append({
            "candidate_index": index,
            "provider": provider,
            "consumer": consumer,
            "type": edge_type,
            "confidence": float(confidence),
            "contract_ids": sorted(set(cited_ids)),
            "symbols": sorted(set(symbols)),
            "reason": raw["reason"].strip(),
        })
    accepted.sort(key=lambda item: (
        item["provider"], item["consumer"], item["type"]
    ))
    return accepted, rejected


def strongly_connected_components(
    nodes: Iterable[str], edges: Iterable[tuple[str, str]],
) -> list[list[str]]:
    graph = {node: [] for node in nodes}
    for source, target in edges:
        graph[source].append(target)
    for successors in graph.values():
        successors.sort()
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    result: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for successor in graph[node]:
            if successor not in indices:
                visit(successor)
                lowlinks[node] = min(lowlinks[node], lowlinks[successor])
            elif successor in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[successor])
        if lowlinks[node] == indices[node]:
            component = []
            while True:
                current = stack.pop()
                on_stack.remove(current)
                component.append(current)
                if current == node:
                    break
            result.append(sorted(component))

    for node in sorted(graph):
        if node not in indices:
            visit(node)
    return sorted(result, key=lambda item: item[0])


def component_depths(
    components: list[list[str]], edges: list[tuple[str, str]],
) -> dict[str, int]:
    component_of = {
        node: index for index, component in enumerate(components)
        for node in component
    }
    predecessors: dict[int, set[int]] = defaultdict(set)
    for source, target in edges:
        left, right = component_of[source], component_of[target]
        if left != right:
            predecessors[right].add(left)
    depth: dict[int, int] = {}
    remaining = set(range(len(components)))
    while remaining:
        ready = sorted(item for item in remaining if predecessors[item] <= depth.keys())
        if not ready:
            raise ValueError("component graph contains a cycle")
        for item in ready:
            depth[item] = 0 if not predecessors[item] else 1 + max(
                depth[parent] for parent in predecessors[item]
            )
            remaining.remove(item)
    return {node: depth[component_of[node]] for node in component_of}


def weak_order(
    nodes: list[str], edges: list[tuple[str, str]],
) -> tuple[list[str], list[list[str]]]:
    components = strongly_connected_components(nodes, edges)
    component_of = {
        node: index for index, component in enumerate(components)
        for node in component
    }
    predecessors: dict[int, set[int]] = defaultdict(set)
    outgoing: dict[int, set[int]] = defaultdict(set)
    for source, target in edges:
        left, right = component_of[source], component_of[target]
        if left != right:
            predecessors[right].add(left)
            outgoing[left].add(right)
    emitted: set[int] = set()
    order = []
    while len(emitted) < len(components):
        ready = [
            index for index in range(len(components))
            if index not in emitted and predecessors[index] <= emitted
        ]
        ready.sort(key=lambda item: (-len(outgoing[item]), components[item][0]))
        if not ready:
            raise ValueError("weak-edge component graph contains a cycle")
        current = ready[0]
        emitted.add(current)
        order.extend(components[current])
    return order, [item for item in components if len(item) > 1]


def build_layers(
    tasks: list[dict[str, Any]], accepted: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    nodes = [item["theory"] for item in tasks]
    strong = [
        (item["provider"], item["consumer"])
        for item in accepted if item["type"] == "contract-composition"
    ]
    weak = [
        (item["provider"], item["consumer"])
        for item in accepted if item["type"] == "operation-call"
    ]
    composition_components = strongly_connected_components(nodes, strong)
    depth_by_theory = component_depths(composition_components, strong)
    by_theory = {item["theory"]: item for item in tasks}
    grouped: dict[int, list[str]] = defaultdict(list)
    for theory, depth in depth_by_theory.items():
        grouped[depth].append(theory)
    layers, operation_cycles = [], []
    for depth in sorted(grouped):
        members = sorted(grouped[depth])
        member_set = set(members)
        local_weak = [
            edge for edge in weak if edge[0] in member_set and edge[1] in member_set
        ]
        order, cycles = weak_order(members, local_weak)
        operation_cycles.extend(cycles)
        layers.append({
            "layer": depth,
            "theories": [{
                "order_in_layer": index,
                **{key: value for key, value in by_theory[theory].items() if key != "contracts"},
            } for index, theory in enumerate(order)],
        })
    return layers, {
        "composition_sccs": [item for item in composition_components if len(item) > 1],
        "operation_sccs": operation_cycles,
        "strong_edges": [
            {"provider": source, "consumer": target} for source, target in strong
        ],
        "weak_edges": [
            {"provider": source, "consumer": target} for source, target in weak
        ],
    }


def initial_progress_ledger(
    file_list: dict[str, Any], audit: dict[str, Any],
) -> dict[str, Any]:
    files = []
    order = 0
    for layer in file_list["layers"]:
        for item in layer["theories"]:
            files.append({
                "order": order,
                "layer": layer["layer"],
                "theory": item["theory"],
                "file_slot": item["file_slot"],
                "status": "pending",
                "generation_ids": [],
                "seal_id": None,
            })
            order += 1
    return {
        "schema_version": 2,
        "artifact_type": "session-progress-ledger",
        "campaign_id": file_list["campaign_id"],
        "environment_gate_id": file_list["environment_gate_id"],
        "file_list_sha256": file_list["list_payload_sha256"],
        "planning_audit_sha256": audit["audit_payload_sha256"],
        "revision": 0,
        "session_status": "pending",
        "files": files,
        "events": [],
    }


def build_artifacts(
    gate: dict[str, Any], tasks: list[dict[str, Any]], policy: dict[str, Any],
    policy_sha256: str, planner_input: dict[str, Any], proposal: dict[str, Any],
    *, transcript_meta: dict[str, Any], transcript_prefix: Path,
) -> dict[str, dict[str, Any]]:
    accepted, rejected = validate_candidate_edges(proposal, tasks, policy)
    layers, structure = build_layers(tasks, accepted)
    evidence = {
        "schema_version": 1,
        "artifact_type": "session-dependency-evidence",
        "campaign_id": gate["campaign_id"],
        "environment_gate_id": gate["gate_id"],
        "policy_sha256": policy_sha256,
        "planner_input_sha256": sha256_json(planner_input),
        "proposal_sha256": sha256_json(proposal),
        "accepted_candidate_edges": accepted,
        "rejected_candidate_edges": rejected,
        **structure,
        "interpretation": (
            "Contract-composition evidence determines layers. Operation-call "
            "evidence affects only serial order inside a layer and is not a proof dependency."
        ),
    }
    evidence["evidence_payload_sha256"] = sha256_json(evidence)
    file_list = {
        "schema_version": 2,
        "artifact_type": "refine-file-generation-list",
        "campaign_id": gate["campaign_id"],
        "environment_gate_id": gate["gate_id"],
        "contract_package_payload_sha256": gate["contract_packages"]["payload_sha256"],
        "policy_sha256": policy_sha256,
        "dependency_evidence_sha256": evidence["evidence_payload_sha256"],
        "immutable": True,
        "layers": layers,
        "serial_fallback": [
            item["theory"] for layer in layers for item in layer["theories"]
        ],
        "layer_count": len(layers),
        "file_count": len(tasks),
        "contract_count": sum(len(item["contract_ids"]) for item in tasks),
    }
    file_list["list_payload_sha256"] = sha256_json(file_list)
    audit = {
        "schema_version": 1,
        "artifact_type": "session-planning-audit",
        "campaign_id": gate["campaign_id"],
        "environment_gate_id": gate["gate_id"],
        "checks": {
            "environment_gate_pass": gate.get("proof_generation_allowed") is True,
            "all_theories_exactly_once": len(file_list["serial_fallback"]) == len(
                set(file_list["serial_fallback"])
            ) == len(tasks),
            "all_contracts_exactly_once": file_list["contract_count"] == gate[
                "contract_packages"
            ]["contract_count"],
            "policy_frozen": True,
            "transcript_sealable": bool(transcript_meta.get("sealable_generation")),
            "transcript_model_matches_policy": transcript_meta.get("model")
            == policy["model"]["model"],
            "transcript_stage_matches": transcript_meta.get("stage")
            == "Session-Planning",
            "transcript_provider_audited": transcript_meta.get("provider")
            in {"codex-exec-audited", "claude-print-audited"},
        },
        "counts": {
            "files": file_list["file_count"],
            "contracts": file_list["contract_count"],
            "candidate_edges": len(proposal.get("candidate_edges", [])),
            "accepted_edges": len(accepted),
            "rejected_edges": len(rejected),
            "layers": len(layers),
        },
        "dependency_evidence_sha256": evidence["evidence_payload_sha256"],
        "file_list_sha256": file_list["list_payload_sha256"],
        "transcript": {
            "prefix": str(transcript_prefix),
            "provider": transcript_meta.get("provider"),
            "model": transcript_meta.get("model"),
            "prompt_sha256": transcript_meta.get("prompt_sha256"),
            "final_sha256": transcript_meta.get("final_sha256"),
        },
    }
    audit["verdict"] = "pass" if all(audit["checks"].values()) else "fail"
    audit["audit_payload_sha256"] = sha256_json(audit)
    return {
        "dependency": evidence,
        "file_list": file_list,
        "audit": audit,
        "ledger": initial_progress_ledger(file_list, audit),
    }


def validate_file_list(
    file_list: dict[str, Any], gate: dict[str, Any],
) -> None:
    if file_list.get("artifact_type") == "repository-file-generation-list":
        raise ValueError(
            "repository plan requires the file-projection consumer; the current proof Runner "
            "cannot consume free generated theory grouping yet"
        )
    if file_list.get("artifact_type") != "refine-file-generation-list":
        raise ValueError("Session Planner output is not a Refine generation list")
    verify_payload_hash(file_list, "list_payload_sha256")
    if not file_list.get("immutable"):
        raise ValueError("Session Planner file list is not immutable")
    if file_list.get("campaign_id") != gate["campaign_id"]:
        raise ValueError("Session Planner file list belongs to another campaign")
    if file_list.get("environment_gate_id") != gate["gate_id"]:
        raise ValueError("Session Planner file list targets another environment gate")
    if file_list.get("contract_package_payload_sha256") != gate[
        "contract_packages"
    ]["payload_sha256"]:
        raise ValueError("Session Planner file list targets another contract package set")
    scheduled = [
        item for layer in file_list.get("layers", []) for item in layer["theories"]
    ]
    if (
        len(scheduled) != file_list.get("file_count")
        or len(scheduled) != gate["contract_packages"]["package_count"]
    ):
        raise ValueError("Session Planner file count mismatch")
    if file_list.get("layer_count") != len(file_list.get("layers", [])):
        raise ValueError("Session Planner layer count mismatch")
    if file_list.get("serial_fallback") != [
        item["theory"] for item in scheduled
    ]:
        raise ValueError("Session Planner serial order mismatch")
    if len({item["theory"] for item in scheduled}) != len(scheduled):
        raise ValueError("Session Planner repeats a theory")
    if len({item["file_slot"] for item in scheduled}) != len(scheduled):
        raise ValueError("Session Planner repeats a file slot")
    contract_ids = [
        contract_id for item in scheduled for contract_id in item["contract_ids"]
    ]
    if (
        len(contract_ids) != file_list.get("contract_count")
        or len(contract_ids) != len(set(contract_ids))
        or len(contract_ids) != gate["contract_packages"]["contract_count"]
    ):
        raise ValueError("Session Planner contract coverage mismatch")


def validate_planning_bundle(
    file_list_path: Path, gate: dict[str, Any],
) -> dict[str, Any]:
    file_list = read_json(file_list_path)
    validate_file_list(file_list, gate)
    directory = file_list_path.parent
    evidence = read_json(directory / "dependency-evidence.json")
    verify_payload_hash(evidence, "evidence_payload_sha256")
    audit = read_json(directory / "session-planning-audit.json")
    verify_payload_hash(audit, "audit_payload_sha256")
    for artifact in (evidence, audit):
        if (
            artifact.get("campaign_id") != gate["campaign_id"]
            or artifact.get("environment_gate_id") != gate["gate_id"]
        ):
            raise ValueError("Session Planning bundle targets another environment")
    if file_list["dependency_evidence_sha256"] != evidence[
        "evidence_payload_sha256"
    ]:
        raise ValueError("Session Planning dependency evidence binding mismatch")
    if (
        audit.get("verdict") != "pass"
        or audit.get("dependency_evidence_sha256")
        != evidence["evidence_payload_sha256"]
        or audit.get("file_list_sha256") != file_list["list_payload_sha256"]
    ):
        raise ValueError("Session Planning audit binding mismatch")
    return file_list


PROGRESS_STATUSES = {
    "pending", "planning", "planned", "planning_failed", "generating",
    "failed", "skipped", "sealed",
}


def validate_progress_ledger(
    ledger: dict[str, Any], file_list: dict[str, Any], gate: dict[str, Any],
) -> None:
    if ledger.get("artifact_type") != "session-progress-ledger":
        raise ValueError("invalid session progress ledger")
    if (
        ledger.get("campaign_id") != gate["campaign_id"]
        or ledger.get("environment_gate_id") != gate["gate_id"]
        or ledger.get("file_list_sha256") != file_list["list_payload_sha256"]
    ):
        raise ValueError("Session progress ledger binding mismatch")
    scheduled = [
        {"layer": layer["layer"], **item}
        for layer in file_list["layers"] for item in layer["theories"]
    ]
    files = ledger.get("files", [])
    if len(files) != len(scheduled):
        raise ValueError("Session progress ledger file count mismatch")
    for order, (progress, planned) in enumerate(zip(files, scheduled)):
        if (
            progress.get("order") != order
            or progress.get("layer") != planned["layer"]
            or progress.get("theory") != planned["theory"]
            or progress.get("file_slot") != planned["file_slot"]
        ):
            raise ValueError("Session progress ledger order differs from file list")
        if progress.get("status") not in PROGRESS_STATUSES:
            raise ValueError("Session progress ledger contains an invalid status")
        generation_ids = progress.get("generation_ids")
        if (
            not isinstance(generation_ids, list)
            or len(generation_ids) != len(set(generation_ids))
        ):
            raise ValueError("Session progress ledger generation IDs are invalid")
        if progress["status"] == "sealed" and not progress.get("seal_id"):
            raise ValueError("sealed progress entry lacks a theory seal ID")
        if progress["status"] != "sealed" and progress.get("seal_id") is not None:
            raise ValueError("unsealed progress entry carries a theory seal ID")
    if not isinstance(ledger.get("revision"), int) or ledger["revision"] < 0:
        raise ValueError("Session progress ledger revision is invalid")
    if ledger.get("session_status") not in {
        "pending", "running", "partial", "complete",
    }:
        raise ValueError("Session progress ledger session status is invalid")
    all_sealed = all(item["status"] == "sealed" for item in files)
    if ledger["session_status"] == "complete" and not all_sealed:
        raise ValueError("incomplete files cannot have complete session status")
    if all_sealed and ledger["session_status"] != "complete":
        raise ValueError("all sealed files require complete session status")


def write_new_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
    except FileExistsError as error:
        raise ValueError(f"refusing to overwrite mutable session ledger: {path}") from error


def write_artifacts(output: Path, artifacts: dict[str, dict[str, Any]]) -> None:
    if artifacts["audit"]["verdict"] != "pass":
        raise ValueError("Session Planning audit did not pass")
    write_immutable_json(output / "dependency-evidence.json", artifacts["dependency"])
    write_immutable_json(
        output / "refine-file-generation-list.json", artifacts["file_list"]
    )
    write_immutable_json(output / "session-planning-audit.json", artifacts["audit"])
    write_new_json(output / "session-progress-ledger.json", artifacts["ledger"])


def verify_existing(
    output: Path, expected: dict[str, dict[str, Any]], gate: dict[str, Any],
) -> dict[str, Any]:
    names = {
        "dependency": ("dependency-evidence.json", "evidence_payload_sha256"),
        "file_list": ("refine-file-generation-list.json", "list_payload_sha256"),
        "audit": ("session-planning-audit.json", "audit_payload_sha256"),
    }
    for key, (filename, hash_field) in names.items():
        actual = read_json(output / filename)
        verify_payload_hash(actual, hash_field)
        if actual != expected[key]:
            raise ValueError(f"Session Planning artifact does not reconstruct: {filename}")
    validate_file_list(expected["file_list"], gate)
    ledger = read_json(output / "session-progress-ledger.json")
    validate_progress_ledger(ledger, expected["file_list"], gate)
    return {
        "verdict": "pass",
        "replay": "reconstructed-and-unchanged",
        "files": expected["file_list"]["file_count"],
        "contracts": expected["file_list"]["contract_count"],
        "layers": expected["file_list"]["layer_count"],
        "accepted_edges": expected["audit"]["counts"]["accepted_edges"],
        "rejected_edges": expected["audit"]["counts"]["rejected_edges"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--environment-root", type=Path, required=True)
    parser.add_argument("--environment-gate", type=Path)
    parser.add_argument("--dataflow", type=Path, help="environment-index.json; defaults to preparation output")
    parser.add_argument("--verify-only", action="store_true", help="verify a completed repository plan without new calls")
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-external-call", action="store_true")
    parser.add_argument("--prepare-only", action="store_true", help="validate and save planning input without any model call")
    parser.add_argument("--freeze-planning", action="store_true", help="freeze a v3 planning revision over the existing verified environment")
    args = parser.parse_args()
    if args.prepare_only and (args.verify_only or args.allow_external_call):
        parser.error("--prepare-only cannot be combined with --verify-only or --allow-external-call")

    repo = args.repo.resolve()
    environment_root = args.environment_root.resolve()
    gate_path = args.environment_gate or environment_root / "Environment-Preparation/environment-gate.json"
    gate = validate_environment_gate(environment_root, gate_path)
    if not gate.get("proof_generation_allowed"):
        raise ValueError("environment gate does not allow proof generation")
    selected_policy = args.policy or DEFAULT_POLICY
    policy_path = selected_policy if selected_policy.is_absolute() else repo / selected_policy
    output = args.output or environment_root / "Session-Planning"
    if args.freeze_planning or (output / "Planning-Freeze/manifest.json").is_file():
        if args.policy is None and (output / "Planning-Freeze/manifest.json").is_file():
            frozen = read_json(output / "Planning-Freeze/manifest.json")
            policy, policy_sha256 = frozen["policy"], frozen["policy_sha256"]
        else:
            policy, policy_sha256 = read_json(policy_path), sha256_path(policy_path)
        if policy.get("schema_version") != 3:
            raise ValueError("independent planning freeze requires policy v3")
    else:
        policy, policy_sha256 = verify_policy_frozen(repo, environment_root, policy_path)
    if policy.get("schema_version") in {2, 3}:
        from regen.planning.repository_planner import run
        summary = run(
            environment_root, gate_path,
            args.dataflow or environment_root / "Environment-Dataflow/environment-index.json",
            policy, policy_sha256, args.output or environment_root / "Session-Planning",
            allow_external_call=args.allow_external_call, verify_only=args.verify_only,
            freeze_planning=args.freeze_planning, prepare_only=args.prepare_only,
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    if args.dataflow or args.verify_only:
        raise ValueError("dataflow/verify-only options require repository planning policy schema 2")
    tasks = generation_tasks(environment_root, gate)
    planner_input = planner_payload(gate, tasks, policy, policy_sha256)
    prompt = planner_prompt(planner_input)
    manifest = read_json(
        environment_root / "Environment-Freeze/campaign-manifest.json"
    )
    context_budget = int(manifest["budgets"]["session_planner_context_chars"])
    if len(prompt) > context_budget:
        raise ValueError(
            f"Session Planner prompt exceeds frozen budget: {len(prompt)} > {context_budget}"
        )
    output = args.output or environment_root / "Session-Planning"
    preferred_prefix = output / "transcript/session-planner"
    completed = select_completed_transcript(
        preferred_prefix, prompt=prompt, model=policy["model"]["model"],
    )
    if completed is not None:
        prefix, transcript_meta = completed
        paths = artifact_paths(prefix)
        proposal = parse_model_json(paths["final"].read_text(encoding="utf-8"))
    else:
        if not args.allow_external_call:
            raise ValueError("fresh Session Planning requires --allow-external-call")
        prefix = allocate_artifact_prefix(preferred_prefix)
        final = call_codex(
            prompt, prefix,
            model=policy["model"]["model"],
            timeout=int(manifest["budgets"]["model_call_timeout_seconds"]),
            theory="Refine", stage="Session-Planning", clean_runtime=True,
        )
        transcript_meta = validate_call_artifact(prefix, require_real=True)
        proposal = parse_model_json(final)
        paths = artifact_paths(prefix)
    if transcript_meta.get("prompt_sha256") != sha256_path(paths["prompt"]):
        raise ValueError("Session Planner transcript prompt hash mismatch")
    artifacts = build_artifacts(
        gate, tasks, policy, policy_sha256, planner_input, proposal,
        transcript_meta=transcript_meta, transcript_prefix=prefix,
    )
    if (output / "refine-file-generation-list.json").is_file():
        summary = verify_existing(output, artifacts, gate)
    else:
        write_artifacts(output, artifacts)
        summary = {
            "verdict": artifacts["audit"]["verdict"],
            "files": artifacts["file_list"]["file_count"],
            "contracts": artifacts["file_list"]["contract_count"],
            "layers": artifacts["file_list"]["layer_count"],
            "accepted_edges": artifacts["audit"]["counts"]["accepted_edges"],
            "rejected_edges": artifacts["audit"]["counts"]["rejected_edges"],
        }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
