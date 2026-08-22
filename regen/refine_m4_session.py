#!/usr/bin/env python3
"""Plan and seal the M4 Refine Proof-file execution schedule."""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from llm_transcript import artifact_paths, call_codex, validate_call_artifact
from refine_campaign import (
    read_json,
    sha256_bytes,
    sha256_json,
    verify_frozen_inputs,
    verify_manifest,
    write_immutable_json,
)
from refine_contracts import verify_m1_sources
from refine_m3_premises import verify_published_m3
from refine_m2_isolated import verify_m2_stage


SCHEMA_VERSION = 2
REPO = Path(__file__).resolve().parent.parent
DEFAULT_POLICY = Path("regen/config/refine-m4-scheduling-policy.json")
DEFAULT_OUTPUT_ROOT = Path("reports/regen/refine-setting-b")
JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
SYMBOL_RE = re.compile(r"[A-Za-z][A-Za-z0-9_'.]*")


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def payload_without_hash(value: dict[str, Any], hash_field: str) -> dict[str, Any]:
    result = dict(value)
    result.pop(hash_field, None)
    return result


def verify_payload_hash(value: dict[str, Any], hash_field: str) -> None:
    expected = value.get(hash_field)
    actual = sha256_json(payload_without_hash(value, hash_field))
    if expected != actual:
        raise ValueError(f"payload hash mismatch for {value.get('artifact_type')}: {expected} != {actual}")


def find_m2_workspace_manifest(campaign_dir: Path) -> Path:
    candidates = sorted(campaign_dir.glob("M2-preflight-*/sanitized-workspace-manifest.json"))
    if not candidates:
        raise ValueError("M2 sanitized workspace manifest is missing")
    if len(candidates) != 1:
        raise ValueError(f"expected exactly one finalized M2 preflight, found {len(candidates)}")
    return candidates[0]


def verify_stage_inputs(repo: Path, campaign_dir: Path, policy_path: Path) -> dict[str, Any]:
    manifest = read_json(campaign_dir / "M0/campaign-manifest.json")
    verify_manifest(manifest)
    verify_frozen_inputs(repo, manifest)
    verify_m1_sources(repo, campaign_dir / "M1/private/contract-manifest.json")
    m2_gate_path = campaign_dir / "M2/m2-gate.json"
    m2_gate = verify_m2_stage(repo, campaign_dir, campaign_dir / "M2")
    m3_stage = campaign_dir / "M3"
    m3_gate_path = m3_stage / "m3-gate.json"
    m3_gate = read_json(m3_gate_path)
    verified_m3 = verify_published_m3(campaign_dir, campaign_dir / "M2", m3_stage)
    if verified_m3.get("campaign_id") != manifest["campaign_id"]:
        raise ValueError("M3 verification belongs to another campaign")
    snapshot = read_json(m3_stage / "premise-snapshot.json")
    if snapshot.get("snapshot_id") != m3_gate.get("snapshot_id"):
        raise ValueError("M3 snapshot identity does not match its gate")

    policy = read_json(policy_path)
    if policy.get("artifact_type") != "m4-scheduling-policy" or policy.get("stage") != "Refine":
        raise ValueError("invalid Refine M4 scheduling policy")
    frozen = {item["path"]: item["sha256"] for item in manifest["frozen_inputs"]}
    relative_policy = str(policy_path.resolve().relative_to(repo.resolve()))
    if frozen.get(relative_policy) != sha256_path(policy_path):
        raise ValueError("M4 scheduling policy is not frozen by M0")

    return {
        "manifest": manifest,
        "m2_gate": m2_gate,
        "m2_gate_sha256": sha256_path(m2_gate_path),
        "m2_workspace_manifest_path": find_m2_workspace_manifest(campaign_dir),
        "m3_gate": m3_gate,
        "m3_gate_sha256": sha256_path(m3_gate_path),
        "snapshot": snapshot,
        "policy": policy,
        "policy_sha256": sha256_path(policy_path),
    }


def load_public_packages(campaign_dir: Path, campaign_id: str) -> list[dict[str, Any]]:
    paths = sorted((campaign_dir / "M1/public/proof-file-packages").glob("*.json"))
    if not paths:
        raise ValueError("M1 public Proof-file packages are missing")
    packages = []
    seen_slots: set[str] = set()
    seen_contracts: set[str] = set()
    for path in paths:
        package = read_json(path)
        if package.get("campaign_id") != campaign_id or package.get("stage") != "Refine":
            raise ValueError(f"invalid package campaign/stage: {path}")
        verify_payload_hash(package, "package_payload_sha256")
        slot = package.get("file_slot")
        if not isinstance(slot, str) or slot in seen_slots:
            raise ValueError(f"duplicate or invalid file slot: {slot}")
        if path.name != f"{slot}.json":
            raise ValueError(f"package path does not match file slot: {path}")
        seen_slots.add(slot)
        for contract in package.get("contracts", []):
            contract_id = contract.get("contract_id")
            if not isinstance(contract_id, str) or contract_id in seen_contracts:
                raise ValueError(f"duplicate or invalid contract ID: {contract_id}")
            if contract.get("owner_file_slot") != slot:
                raise ValueError(f"contract owner mismatch: {contract_id}")
            seen_contracts.add(contract_id)
        packages.append(package)
    return sorted(packages, key=lambda item: item["file_slot"])


def normalized_support_plan(
    workspace_manifest: dict[str, Any], packages: list[dict[str, Any]],
) -> dict[str, Any]:
    raw = workspace_manifest.get("initial_support_plan")
    if not isinstance(raw, dict):
        raise ValueError("M2 initial support plan is missing")
    theory_to_slot = {package["theory"]: package["file_slot"] for package in packages}
    eligible = [{
        "theory": item["theory"],
        "file_slot": item["file_slot"],
        "support_class": "initial-frozen-support",
        "initial_status": "published",
    } for item in raw.get("eligible_theories", [])]
    blocked = []
    for item in raw.get("blocked_support_theories", []):
        unlock_by = []
        for theory in item.get("direct_proof_dependencies", []):
            if theory not in theory_to_slot:
                raise ValueError(f"blocked Support refers to unknown Proof theory: {theory}")
            unlock_by.append(theory_to_slot[theory])
        blocked.append({
            "theory": item["theory"],
            "file_slot": item["file_slot"],
            "support_class": "proof-dependent-frozen-support",
            "initial_status": "blocked",
            "unlock_by_proof_slots": sorted(unlock_by),
            "also_waits_for_support": sorted(
                dep for dep in item.get("unavailable_local_dependencies", [])
                if dep not in theory_to_slot
            ),
        })
    result = {
        "eligible": sorted(eligible, key=lambda item: item["file_slot"]),
        "blocked": sorted(blocked, key=lambda item: item["file_slot"]),
    }
    return result


def planner_payload(
    campaign_id: str,
    packages: list[dict[str, Any]],
    support_plan: dict[str, Any],
    snapshot: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "m4-planner-payload",
        "campaign_id": campaign_id,
        "stage": "Refine",
        "fixed_proof_files": [{
            "file_slot": package["file_slot"],
            "theory": package["theory"],
            "contracts": [{
                "contract_id": contract["contract_id"],
                "name": contract["name"],
                "exact_statement": contract["exact_statement"],
            } for contract in package["contracts"]],
        } for package in packages],
        "normalized_support_availability": support_plan,
        "premise_snapshot_identity_only": {
            "snapshot_id": snapshot["snapshot_id"],
            "record_count": snapshot["record_count"],
            "source_class_counts": snapshot["source_class_counts"],
            "note": "No premise records or retrieval statistics are included or usable for ordering.",
        },
        "policy": policy,
    }


def planner_prompt(payload: dict[str, Any]) -> str:
    schema = {
        "schema_version": 1,
        "candidate_edges": [{
            "from_file": "Bits_R.thy",
            "to_file": "Example_R.thy",
            "type": "contract-composition",
            "confidence": 0.75,
            "contract_ids": ["one exact contract ID owned by from_file", "one exact contract ID owned by to_file"],
            "symbols": ["find_pd_for_asid", "invoke_tcb"],
            "reason": "brief explanation grounded only in the cited contracts",
        }],
        "notes": ["optional uncertainty notes; no schedule or theorem plan"],
    }
    return (
        "You are the single M4 candidate-edge proposer for a sanitized Isabelle/seL4 Refine campaign.\n"
        "The fixed file slots and exact selected contract statements below are the complete legal Proof-theory input.\n"
        "Propose only evidence-citing generation-priority edges. You do NOT decide the final schedule.\n\n"
        "Semantics:\n"
        "- from_file -> to_file means generate from_file earlier when possible.\n"
        "- contract-composition means cited contracts expose a credible smaller-to-larger correspondence composition.\n"
        "- operation-call means cited contract surfaces suggest a callee-before-caller priority; this is weak evidence, never a proof dependency.\n"
        "- Support unlock facts are already deterministic. Do not invent Support edges.\n\n"
        "Hard rules:\n"
        "1. Use only file names, contract IDs, statements, and normalized Support facts present in the payload.\n"
        "2. Do not rely on remembered seL4 source architecture, human imports, helper names, or human proof order.\n"
        "3. Every edge must cite at least one contract owned by each endpoint.\n"
        "4. Every symbols item must be exactly one Isabelle identifier matching "
        "[A-Za-z][A-Za-z0-9_'.]*, without arguments or surrounding syntax, and must "
        "occur as a complete identifier token in a cited statement or contract name.\n"
        "5. Omit an edge when the visible contracts do not support it. It is valid to return few or zero edges.\n"
        "6. Do not propose helpers, theorem statements, imports, proof tactics, file grouping, or a final order.\n"
        "7. Return JSON only, with at most 128 candidate_edges and this exact shape:\n"
        f"{json.dumps(schema, ensure_ascii=False, sort_keys=True)}\n\n"
        "LEGAL M4 PAYLOAD:\n"
        f"{json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}\n"
    )


def parse_model_json(text: str) -> dict[str, Any]:
    cleaned = JSON_FENCE_RE.sub("", text.strip())
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("M4 model response contains no JSON object")
        value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("M4 model response must be a JSON object")
    return value


def symbol_is_grounded(symbol: str, contracts: list[dict[str, Any]]) -> bool:
    if not symbol or not SYMBOL_RE.fullmatch(symbol):
        return False
    return any(
        symbol in set(SYMBOL_RE.findall(f'{contract["name"]} {contract["exact_statement"]}'))
        for contract in contracts
    )


def validate_candidate_edges(
    response: dict[str, Any], packages: list[dict[str, Any]], policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_edges = response.get("candidate_edges")
    if response.get("schema_version") != 1 or not isinstance(raw_edges, list):
        raise ValueError("M4 response schema_version/candidate_edges is invalid")
    maximum = int(policy["model"]["max_candidate_edges"])
    if len(raw_edges) > maximum:
        raise ValueError(f"M4 response exceeds candidate edge budget: {len(raw_edges)} > {maximum}")

    by_slot = {package["file_slot"]: package for package in packages}
    contracts = {
        contract["contract_id"]: contract
        for package in packages for contract in package["contracts"]
    }
    minimum = float(policy["model"]["minimum_confidence"])
    accepted, rejected = [], []
    seen: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(raw_edges):
        reasons = []
        if not isinstance(raw, dict):
            rejected.append({"candidate_index": index, "candidate": raw, "reasons": ["not-an-object"]})
            continue
        source, target, edge_type = raw.get("from_file"), raw.get("to_file"), raw.get("type")
        if source not in by_slot:
            reasons.append("unknown-from-file")
        if target not in by_slot:
            reasons.append("unknown-to-file")
        if source == target:
            reasons.append("self-edge")
        if edge_type not in {"contract-composition", "operation-call"}:
            reasons.append("unsupported-edge-type")
        confidence = raw.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
            reasons.append("invalid-confidence")
        elif confidence < minimum:
            reasons.append("below-confidence-threshold")
        contract_ids = raw.get("contract_ids")
        cited = []
        if not isinstance(contract_ids, list) or not contract_ids or not all(isinstance(x, str) for x in contract_ids):
            reasons.append("invalid-contract-citations")
        else:
            unknown = sorted(set(contract_ids) - contracts.keys())
            if unknown:
                reasons.append("unknown-contract-citation")
            else:
                cited = [contracts[item] for item in contract_ids]
                owners = {item["owner_file_slot"] for item in cited}
                if source not in owners or target not in owners:
                    reasons.append("citations-do-not-cover-both-endpoints")
                if owners - {source, target}:
                    reasons.append("citation-owned-by-third-file")
        symbols = raw.get("symbols")
        if not isinstance(symbols, list) or not symbols or not all(isinstance(x, str) for x in symbols):
            reasons.append("invalid-symbol-evidence")
        elif cited and not all(symbol_is_grounded(symbol, cited) for symbol in symbols):
            reasons.append("ungrounded-symbol-evidence")
        if not isinstance(raw.get("reason"), str) or not raw["reason"].strip():
            reasons.append("missing-reason")
        key = (str(source), str(target), str(edge_type))
        if key in seen:
            reasons.append("duplicate-edge")
        if reasons:
            rejected.append({"candidate_index": index, "candidate": raw, "reasons": sorted(set(reasons))})
            continue
        seen.add(key)
        accepted.append({
            "candidate_index": index,
            "from_file": source,
            "to_file": target,
            "type": edge_type,
            "confidence": float(confidence),
            "contract_ids": sorted(set(contract_ids)),
            "symbols": sorted(set(symbols)),
            "reason": raw["reason"].strip(),
            "effect": (
                "composition-stratum-edge" if edge_type == "contract-composition"
                else "within-stratum-precedence-candidate"
            ),
        })
    accepted.sort(key=lambda item: (item["from_file"], item["to_file"], item["type"]))
    return accepted, rejected


def strongly_connected_components(nodes: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    graph: dict[str, list[str]] = {node: [] for node in nodes}
    for source, target in edges:
        graph[source].append(target)
    for node in graph:
        graph[node].sort()
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

    for node in sorted(nodes):
        if node not in indices:
            visit(node)
    return sorted(result, key=lambda component: component[0])


def compute_layers(
    packages: list[dict[str, Any]], accepted_edges: list[dict[str, Any]], support_plan: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[list[str]], list[list[str]]]:
    slots = [package["file_slot"] for package in packages]
    composition = [
        (edge["from_file"], edge["to_file"])
        for edge in accepted_edges if edge["type"] == "contract-composition"
    ]
    composition_components = strongly_connected_components(slots, composition)
    component_of = {
        slot: index for index, group in enumerate(composition_components) for slot in group
    }
    predecessors: dict[int, set[int]] = defaultdict(set)
    for source, target in composition:
        left, right = component_of[source], component_of[target]
        if left != right:
            predecessors[right].add(left)

    depths: dict[int, int] = {}
    remaining = set(range(len(composition_components)))
    while remaining:
        ready = sorted(index for index in remaining if predecessors[index] <= depths.keys())
        if not ready:
            raise ValueError("component graph unexpectedly contains a cycle")
        for index in ready:
            depths[index] = 0 if not predecessors[index] else 1 + max(depths[p] for p in predecessors[index])
            remaining.remove(index)

    support_scores: dict[str, int] = defaultdict(int)
    for item in support_plan["blocked"]:
        for slot in item["unlock_by_proof_slots"]:
            support_scores[slot] += 1
    operation_scores: dict[str, int] = defaultdict(int)
    for edge in accepted_edges:
        if edge["type"] == "operation-call":
            operation_scores[edge["from_file"]] += 1

    by_slot = {package["file_slot"]: package for package in packages}
    grouped: dict[int, list[str]] = defaultdict(list)
    for slot in slots:
        grouped[depths[component_of[slot]]].append(slot)

    def order_one_stratum(stratum: list[str]) -> tuple[list[str], list[list[str]]]:
        members = set(stratum)
        operation = [
            (edge["from_file"], edge["to_file"])
            for edge in accepted_edges
            if edge["type"] == "operation-call"
            and edge["from_file"] in members
            and edge["to_file"] in members
        ]
        operation_components = strongly_connected_components(stratum, operation)
        operation_component_of = {
            slot: index
            for index, component in enumerate(operation_components)
            for slot in component
        }
        operation_predecessors: dict[int, set[int]] = defaultdict(set)
        for source, target in operation:
            left = operation_component_of[source]
            right = operation_component_of[target]
            if left != right:
                operation_predecessors[right].add(left)

        remaining_components = set(range(len(operation_components)))
        ordered_slots: list[str] = []
        while remaining_components:
            ready = [
                index for index in remaining_components
                if not (operation_predecessors[index] & remaining_components)
            ]
            if not ready:
                raise ValueError("operation component graph unexpectedly contains a cycle")
            ready.sort(key=lambda index: (
                -max(support_scores[slot] for slot in operation_components[index]),
                -sum(operation_scores[slot] for slot in operation_components[index]),
                operation_components[index],
            ))
            selected = ready[0]
            ordered_slots.extend(sorted(
                operation_components[selected],
                key=lambda slot: (-support_scores[slot], -operation_scores[slot], slot),
            ))
            remaining_components.remove(selected)
        cycles = [component for component in operation_components if len(component) > 1]
        return ordered_slots, cycles

    layers = []
    operation_cycles: list[list[str]] = []
    serial_position = 0
    for depth in sorted(grouped):
        ordered, stratum_cycles = order_one_stratum(grouped[depth])
        operation_cycles.extend(stratum_cycles)
        records = []
        for slot in ordered:
            package = by_slot[slot]
            records.append({
                "file_slot": slot,
                "theory": package["theory"],
                "contract_ids": [contract["contract_id"] for contract in package["contracts"]],
                "contract_count": len(package["contracts"]),
                "initial_status": "pending",
                "serial_fallback_position": serial_position,
                "support_unlock_count": support_scores[slot],
                "operation_priority_score": operation_scores[slot],
            })
            serial_position += 1
        layers.append({
            "layer": depth,
            "meaning": "no accepted contract-composition ordering separates files within this layer",
            "theories": records,
        })
    return layers, composition_components, sorted(operation_cycles)


def support_unlock_evidence(support_plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [{
        "type": "support-unlock",
        "proof_file": slot,
        "unlocks_support": item["file_slot"],
        "support_theory": item["theory"],
        "source": "M2 normalized direct Proof dependency",
    } for item in support_plan["blocked"] for slot in item["unlock_by_proof_slots"]]


def planner_payload_checks(
    payload: dict[str, Any], campaign_id: str, packages: list[dict[str, Any]],
    support_plan: dict[str, Any], snapshot: dict[str, Any], policy: dict[str, Any],
) -> dict[str, bool]:
    expected = planner_payload(campaign_id, packages, support_plan, snapshot, policy)
    top_level_keys = {
        "schema_version", "artifact_type", "campaign_id", "stage",
        "fixed_proof_files", "normalized_support_availability",
        "premise_snapshot_identity_only", "policy",
    }
    proof_inputs_contract_only = (
        set(payload) == top_level_keys
        and all(
            set(item) == {"file_slot", "theory", "contracts"}
            and all(
                set(contract) == {"contract_id", "name", "exact_statement"}
                for contract in item.get("contracts", [])
            )
            for item in payload.get("fixed_proof_files", [])
        )
    )
    identity = payload.get("premise_snapshot_identity_only", {})
    premise_identity_only = (
        isinstance(identity, dict)
        and set(identity) == {"snapshot_id", "record_count", "source_class_counts", "note"}
        and not any(key in identity for key in {"records", "premises", "retrieval_results"})
    )
    return {
        "planner_payload_matches_deterministic_projection": payload == expected,
        "no_premise_records_used_for_ordering": premise_identity_only,
        "no_human_proof_import_graph_used": proof_inputs_contract_only,
    }


def inspect_planner_transcript(
    transcript_prefix: Path, expected_prompt: str, expected_model: str,
) -> dict[str, Any]:
    meta = validate_call_artifact(transcript_prefix, require_real=True)
    paths = artifact_paths(transcript_prefix)
    transcript_count = len(list(transcript_prefix.parent.glob("*.meta.json")))
    prompt_matches = paths["prompt"].read_text(encoding="utf-8") == expected_prompt
    audit = read_json(paths["audit"])
    return {
        "transcript_count": transcript_count,
        "provider": meta.get("provider"),
        "model": meta.get("model"),
        "stage": meta.get("stage"),
        "theory": meta.get("theory"),
        "returncode": meta.get("returncode"),
        "timed_out": meta.get("timed_out"),
        "provider_network_call": meta.get("provider_network_call"),
        "sealable_generation": meta.get("sealable_generation"),
        "meta_no_tool_events": meta.get("no_tool_events"),
        "audit_no_tool_events": audit.get("no_tool_events"),
        "prompt_matches_reconstructed_input": prompt_matches,
        "model_matches_policy": meta.get("model") == expected_model,
        "prompt_sha256": meta.get("prompt_sha256"),
        "raw_sha256": meta.get("raw_sha256"),
        "final_sha256": meta.get("final_sha256"),
        "stderr_sha256": meta.get("stderr_sha256"),
        "audit_sha256": meta.get("audit_sha256"),
    }


def initial_progress_ledger(
    campaign_id: str, file_list: dict[str, Any], layers: list[dict[str, Any]],
    support_plan: dict[str, Any], snapshot_id: str,
) -> dict[str, Any]:
    progress = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "refine-session-progress",
        "campaign_id": campaign_id,
        "stage": "Refine",
        "mutable": True,
        "revision": 0,
        "file_generation_list_sha256": file_list["list_payload_sha256"],
        "proof_theories": [{
            "file_slot": record["file_slot"],
            "theory": record["theory"],
            "layer": layer["layer"],
            "serial_fallback_position": record["serial_fallback_position"],
            "status": "pending",
            "generation_ids": [],
            "seal_id": None,
        } for layer in layers for record in layer["theories"]],
        "support_theories": [
            {**item, "status": item["initial_status"], "publication_snapshot_id": snapshot_id}
            for item in support_plan["eligible"]
        ] + [{**item, "status": item["initial_status"]} for item in support_plan["blocked"]],
        "sealed_theory_inventory": [],
        "generated_heap_identity": None,
        "session_status": "pending",
        "events": [],
    }
    progress["initial_ledger_sha256"] = sha256_json(progress)
    return progress


def validate_progress_ledger(
    progress: dict[str, Any], file_list: dict[str, Any], support_plan: dict[str, Any],
    gate: dict[str, Any],
) -> None:
    if progress.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("M4 progress schema mismatch")
    if progress.get("campaign_id") != gate.get("campaign_id") or progress.get("stage") != "Refine":
        raise ValueError("M4 progress campaign/stage mismatch")
    if progress.get("file_generation_list_sha256") != file_list["list_payload_sha256"]:
        raise ValueError("M4 progress/list hash mismatch")
    if progress.get("initial_ledger_sha256") != gate.get("initial_ledger_sha256"):
        raise ValueError("M4 gate/progress initial-ledger hash mismatch")

    scheduled = [record for layer in file_list["layers"] for record in layer["theories"]]
    proof_records = progress.get("proof_theories")
    if not isinstance(proof_records, list) or len(proof_records) != len(scheduled):
        raise ValueError("M4 progress Proof-theory coverage mismatch")
    immutable_fields = ("file_slot", "theory", "layer", "serial_fallback_position")
    for actual, expected in zip(proof_records, scheduled):
        expected_identity = {
            "file_slot": expected["file_slot"],
            "theory": expected["theory"],
            "layer": next(
                layer["layer"] for layer in file_list["layers"]
                if expected in layer["theories"]
            ),
            "serial_fallback_position": expected["serial_fallback_position"],
        }
        if any(actual.get(field) != expected_identity[field] for field in immutable_fields):
            raise ValueError(f'M4 progress theory identity drift: {actual.get("file_slot")}')

    revision = progress.get("revision")
    events = progress.get("events")
    if not isinstance(revision, int) or revision < 0 or not isinstance(events, list):
        raise ValueError("M4 progress revision/events are invalid")
    if revision != len(events):
        raise ValueError("M4 progress revision must equal event count")

    proof_states = {record["file_slot"]: "pending" for record in scheduled}
    support_states = {
        item["file_slot"]: item["initial_status"]
        for item in support_plan["eligible"] + support_plan["blocked"]
    }
    session_state = "pending"
    proof_generation_ids = {record["file_slot"]: [] for record in scheduled}
    seen_generation_ids: set[str] = set()
    proof_transitions = {
        "pending": {"generating", "skipped"},
        "generating": {"sealed", "failed", "retry"},
        "retry": {"generating", "failed", "skipped"},
        "failed": {"retry"},
        "skipped": {"retry"},
        "sealed": set(),
    }
    support_transitions = {"blocked": {"published"}, "published": set()}
    session_transitions = {
        "pending": {"generating", "partial"},
        "generating": {"partial", "sealed", "failed"},
        "partial": {"generating", "failed"},
        "failed": set(),
        "sealed": set(),
    }
    for expected_revision, event in enumerate(events, 1):
        if not isinstance(event, dict) or event.get("revision") != expected_revision:
            raise ValueError("M4 progress event revision sequence is invalid")
        kind = event.get("kind")
        if kind == "proof-status":
            states, transitions = proof_states, proof_transitions
        elif kind == "support-status":
            states, transitions = support_states, support_transitions
        elif kind == "session-status":
            if event.get("from_status") != session_state:
                raise ValueError("M4 session event from_status mismatch")
            target = event.get("to_status")
            if target not in session_transitions[session_state]:
                raise ValueError("M4 session event transition is illegal")
            session_state = target
            continue
        elif kind == "checkpoint":
            continue
        else:
            raise ValueError(f"unknown M4 progress event kind: {kind}")
        slot = event.get("file_slot")
        if slot not in states or event.get("from_status") != states[slot]:
            raise ValueError(f"M4 progress event source mismatch: {slot}")
        target = event.get("to_status")
        if target not in transitions[states[slot]]:
            raise ValueError(f"illegal M4 progress transition for {slot}: {states[slot]} -> {target}")
        if kind == "proof-status" and target == "generating":
            generation_id = event.get("generation_id")
            if not isinstance(generation_id, str) or not generation_id:
                raise ValueError(f"M4 generating event lacks generation_id: {slot}")
            if generation_id in seen_generation_ids:
                raise ValueError(f"M4 generation_id is reused: {generation_id}")
            seen_generation_ids.add(generation_id)
            proof_generation_ids[slot].append(generation_id)
        states[slot] = target

    if any(record.get("status") != proof_states[record["file_slot"]] for record in proof_records):
        raise ValueError("M4 progress Proof statuses do not replay from events")
    support_records = progress.get("support_theories")
    if not isinstance(support_records, list) or {
        item.get("file_slot"): item.get("status") for item in support_records
    } != support_states:
        raise ValueError("M4 progress Support statuses do not replay from events")
    if progress.get("session_status") != session_state:
        raise ValueError("M4 progress session status does not replay from events")
    for record in proof_records:
        generation_ids = record.get("generation_ids")
        if not isinstance(generation_ids, list) or not all(isinstance(item, str) for item in generation_ids):
            raise ValueError("M4 progress generation_ids are invalid")
        if generation_ids != proof_generation_ids[record["file_slot"]]:
            raise ValueError("M4 progress generation_ids do not replay from events")
        if record["status"] == "sealed" and not isinstance(record.get("seal_id"), str):
            raise ValueError("sealed M4 progress record lacks seal_id")
        if record["status"] != "sealed" and record.get("seal_id") is not None:
            raise ValueError("unsealed M4 progress record has seal_id")


def build_artifacts(
    inputs: dict[str, Any], packages: list[dict[str, Any]], support_plan: dict[str, Any],
    payload: dict[str, Any], response: dict[str, Any], transcript_evidence: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    accepted, rejected = validate_candidate_edges(response, packages, inputs["policy"])
    layers, composition_components, operation_cycles = compute_layers(
        packages, accepted, support_plan,
    )
    rebuilt_layers, rebuilt_components, rebuilt_operation_cycles = compute_layers(
        packages, accepted, support_plan,
    )
    campaign_id = inputs["manifest"]["campaign_id"]
    layer_by_slot = {
        record["file_slot"]: layer["layer"]
        for layer in layers for record in layer["theories"]
    }
    position = {
        record["file_slot"]: record["serial_fallback_position"]
        for layer in layers for record in layer["theories"]
    }
    operation_cycle_sets = [set(component) for component in operation_cycles]
    operation_violations = []
    operation_overrides = []
    for edge in accepted:
        if edge["type"] != "operation-call":
            continue
        source, target = edge["from_file"], edge["to_file"]
        if layer_by_slot[source] != layer_by_slot[target]:
            if position[source] > position[target]:
                operation_overrides.append({
                    "from_file": source,
                    "to_file": target,
                    "reason": "stronger contract-composition stratum order",
                })
            continue
        in_same_cycle = any(source in component and target in component for component in operation_cycle_sets)
        if not in_same_cycle and position[source] >= position[target]:
            operation_violations.append({"from_file": source, "to_file": target})

    dependency = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "m4-dependency-evidence",
        "campaign_id": campaign_id,
        "stage": "Refine",
        "policy_sha256": inputs["policy_sha256"],
        "planner_payload_sha256": sha256_json(payload),
        "m2_gate_sha256": inputs["m2_gate_sha256"],
        "m3_snapshot_id": inputs["snapshot"]["snapshot_id"],
        "model_candidate_count": len(response.get("candidate_edges", [])),
        "accepted_model_edges": accepted,
        "rejected_model_edges": rejected,
        "deterministic_support_unlock_evidence": support_unlock_evidence(support_plan),
        "composition_sccs": [group for group in composition_components if len(group) > 1],
        "operation_sccs": operation_cycles,
        "operation_edges_overridden_by_composition": operation_overrides,
        "interpretation": (
            "Contract-composition edges form strata. Operation-call edges are weak "
            "within-stratum precedence constraints; cycles are collapsed and reported."
        ),
    }
    dependency["evidence_payload_sha256"] = sha256_json(dependency)

    file_list = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "refine-file-generation-list",
        "campaign_id": campaign_id,
        "stage": "Refine",
        "immutable": True,
        "policy_sha256": inputs["policy_sha256"],
        "dependency_evidence_sha256": dependency["evidence_payload_sha256"],
        "proof_file_count": len(packages),
        "contract_count": sum(len(package["contracts"]) for package in packages),
        "layer_count": len(layers),
        "layers": layers,
        "serial_fallback": [
            record["file_slot"] for layer in layers for record in layer["theories"]
        ],
    }
    file_list["list_payload_sha256"] = sha256_json(file_list)
    progress = initial_progress_ledger(
        campaign_id, file_list, layers, support_plan, inputs["snapshot"]["snapshot_id"],
    )

    payload_checks = planner_payload_checks(
        payload, campaign_id, packages, support_plan, inputs["snapshot"], inputs["policy"],
    )
    expected_contract_ids = {
        contract["contract_id"] for package in packages for contract in package["contracts"]
    }
    scheduled_contract_ids = [
        contract_id
        for layer in layers for record in layer["theories"]
        for contract_id in record["contract_ids"]
    ]
    transcript_sealable = all([
        transcript_evidence["transcript_count"] == 1,
        transcript_evidence["provider"] == "codex-exec-audited",
        transcript_evidence["stage"] == "M4-session-planner",
        transcript_evidence["theory"] == "Refine",
        transcript_evidence["model_matches_policy"],
        transcript_evidence["prompt_matches_reconstructed_input"],
        transcript_evidence["returncode"] == 0,
        not transcript_evidence["timed_out"],
        bool(transcript_evidence["provider_network_call"]),
        bool(transcript_evidence["sealable_generation"]),
        bool(transcript_evidence["meta_no_tool_events"]),
        bool(transcript_evidence["audit_no_tool_events"]),
    ])
    checks = {
        "m2_gate_pass": inputs["m2_gate"]["verdict"] == "pass",
        "m3_gate_pass": inputs["m3_gate"]["verdict"] == "pass",
        "all_proof_slots_exactly_once": (
            len(file_list["serial_fallback"]) == len(set(file_list["serial_fallback"])) == len(packages)
            and set(file_list["serial_fallback"]) == {package["file_slot"] for package in packages}
        ),
        "all_contracts_exactly_once": (
            len(scheduled_contract_ids) == len(set(scheduled_contract_ids))
            and set(scheduled_contract_ids) == expected_contract_ids
        ),
        **payload_checks,
        "final_schedule_rebuilds_deterministically": (
            layers == rebuilt_layers
            and composition_components == rebuilt_components
            and operation_cycles == rebuilt_operation_cycles
        ),
        "operation_precedence_respected": not operation_violations,
        "model_call_count_one": transcript_evidence["transcript_count"] == 1,
        "transcript_sealable": transcript_sealable,
    }
    audit = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "m4-audit",
        "campaign_id": campaign_id,
        "stage": "Refine",
        "checks": checks,
        "evidence": {
            "planner_payload_sha256": sha256_json(payload),
            "transcript": transcript_evidence,
            "operation_precedence_violations": operation_violations,
            "schedule_rebuild_sha256": sha256_json(rebuilt_layers),
        },
        "counts": {
            "proof_files": len(packages),
            "contracts": file_list["contract_count"],
            "support_initially_published": len(support_plan["eligible"]),
            "support_initially_blocked": len(support_plan["blocked"]),
            "candidate_edges": len(response.get("candidate_edges", [])),
            "accepted_edges": len(accepted),
            "rejected_edges": len(rejected),
            "layers": len(layers),
            "model_calls": transcript_evidence["transcript_count"],
        },
    }
    audit["verdict"] = "pass" if all(audit["checks"].values()) else "fail"
    audit["audit_payload_sha256"] = sha256_json(audit)
    gate = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "m4-gate",
        "campaign_id": campaign_id,
        "stage": "Refine",
        "verdict": audit["verdict"],
        "file_generation_list_sha256": file_list["list_payload_sha256"],
        "dependency_evidence_sha256": dependency["evidence_payload_sha256"],
        "audit_sha256": audit["audit_payload_sha256"],
        "initial_ledger_sha256": progress["initial_ledger_sha256"],
        "m3_snapshot_id": inputs["snapshot"]["snapshot_id"],
    }
    return {
        "dependency": dependency, "file_list": file_list, "progress": progress,
        "audit": audit, "gate": gate,
    }



def verify_reconstructed_immutable(
    root: Path, expected: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    names = {
        "dependency": ("dependency-evidence.json", "evidence_payload_sha256"),
        "file_list": ("file-generation-list.json", "list_payload_sha256"),
        "audit": ("m4-audit.json", "audit_payload_sha256"),
        "gate": ("m4-gate.json", None),
    }
    actual: dict[str, dict[str, Any]] = {}
    for key, (filename, hash_field) in names.items():
        actual[key] = read_json(root / filename)
        if hash_field:
            verify_payload_hash(actual[key], hash_field)
        if actual[key] != expected[key]:
            raise ValueError(f"M4 {filename} does not reconstruct from frozen inputs and transcript")
    if actual["gate"].get("verdict") != "pass":
        raise ValueError("existing M4 gate is not PASS")
    if actual["gate"].get("audit_sha256") != actual["audit"].get("audit_payload_sha256"):
        raise ValueError("M4 gate/audit hash mismatch")
    return actual


def verify_existing_m4(campaign_dir: Path, inputs: dict[str, Any]) -> dict[str, Any]:
    root = campaign_dir / "M4"
    packages = load_public_packages(campaign_dir, inputs["manifest"]["campaign_id"])
    workspace_manifest = read_json(inputs["m2_workspace_manifest_path"])
    support_plan = normalized_support_plan(workspace_manifest, packages)
    payload = planner_payload(
        inputs["manifest"]["campaign_id"], packages, support_plan,
        inputs["snapshot"], inputs["policy"],
    )
    prompt = planner_prompt(payload)
    transcript_prefix = root / "transcript/session-planner"
    transcript_evidence = inspect_planner_transcript(
        transcript_prefix, prompt, inputs["policy"]["model"]["model"],
    )
    final = artifact_paths(transcript_prefix)["final"].read_text(encoding="utf-8")
    response = parse_model_json(final)
    expected = build_artifacts(
        inputs, packages, support_plan, payload, response, transcript_evidence,
    )

    actual = verify_reconstructed_immutable(root, expected)

    progress = read_json(root / "session-progress.json")
    validate_progress_ledger(progress, actual["file_list"], support_plan, actual["gate"])
    if progress.get("revision") == 0 and progress != expected["progress"]:
        raise ValueError("initial M4 progress ledger does not reconstruct")
    return {
        "campaign_id": actual["gate"]["campaign_id"],
        "replay": "reconstructed-and-unchanged",
        "proof_files": actual["file_list"]["proof_file_count"],
        "contracts": actual["file_list"]["contract_count"],
        "layers": actual["file_list"]["layer_count"],
        "list_payload_sha256": actual["file_list"]["list_payload_sha256"],
        "ledger_revision": progress["revision"],
    }


def write_artifacts(root: Path, artifacts: dict[str, dict[str, Any]]) -> dict[str, str]:
    dispositions = {
        "dependency-evidence.json": write_immutable_json(root / "dependency-evidence.json", artifacts["dependency"]),
        "file-generation-list.json": write_immutable_json(root / "file-generation-list.json", artifacts["file_list"]),
        "m4-audit.json": write_immutable_json(root / "m4-audit.json", artifacts["audit"]),
        "m4-gate.json": write_immutable_json(root / "m4-gate.json", artifacts["gate"]),
    }
    progress_path = root / "session-progress.json"
    rendered = json.dumps(artifacts["progress"], ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if progress_path.exists():
        if progress_path.read_text(encoding="utf-8") != rendered:
            raise FileExistsError("refusing to reset an existing mutable M4 progress ledger")
        dispositions["session-progress.json"] = "unchanged"
    else:
        progress_path.write_text(rendered, encoding="utf-8")
        dispositions["session-progress.json"] = "created"
    return dispositions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--allow-external-call", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = args.repo.resolve()
    campaign_dir = args.campaign_dir if args.campaign_dir.is_absolute() else repo / args.campaign_dir
    policy_path = args.policy if args.policy.is_absolute() else repo / args.policy
    inputs = verify_stage_inputs(repo, campaign_dir, policy_path)
    if (campaign_dir / "M4/m4-gate.json").is_file():
        print(json.dumps(verify_existing_m4(campaign_dir, inputs), indent=2, sort_keys=True))
        return 0
    if not args.allow_external_call:
        raise ValueError("fresh M4 planning requires explicit --allow-external-call")

    packages = load_public_packages(campaign_dir, inputs["manifest"]["campaign_id"])
    workspace_manifest = read_json(inputs["m2_workspace_manifest_path"])
    support_plan = normalized_support_plan(workspace_manifest, packages)
    payload = planner_payload(
        inputs["manifest"]["campaign_id"], packages, support_plan,
        inputs["snapshot"], inputs["policy"],
    )
    prompt = planner_prompt(payload)
    budget = int(inputs["manifest"]["budgets"].get("m4_planner_context_chars", 0))
    if not budget or len(prompt) > budget:
        raise ValueError(f"M4 prompt exceeds frozen character budget: {len(prompt)} > {budget}")
    root = campaign_dir / "M4"
    transcript_prefix = root / "transcript/session-planner"
    transcript_final = artifact_paths(transcript_prefix)["final"]
    if transcript_final.is_file():
        final = transcript_final.read_text(encoding="utf-8")
    else:
        final = call_codex(
            prompt, transcript_prefix,
            model=inputs["policy"]["model"]["model"],
            timeout=int(inputs["manifest"]["budgets"]["model_call_timeout_seconds"]),
            theory="Refine", stage="M4-session-planner",
        )
    transcript_evidence = inspect_planner_transcript(
        transcript_prefix, prompt, inputs["policy"]["model"]["model"],
    )
    response = parse_model_json(final)
    artifacts = build_artifacts(
        inputs, packages, support_plan, payload, response, transcript_evidence,
    )
    dispositions = write_artifacts(root, artifacts)
    summary = {
        "campaign_id": inputs["manifest"]["campaign_id"],
        "verdict": artifacts["gate"]["verdict"],
        "proof_files": artifacts["file_list"]["proof_file_count"],
        "contracts": artifacts["file_list"]["contract_count"],
        "layers": artifacts["file_list"]["layer_count"],
        "accepted_edges": artifacts["audit"]["counts"]["accepted_edges"],
        "rejected_edges": artifacts["audit"]["counts"]["rejected_edges"],
        "prompt_chars": len(prompt),
        "list_payload_sha256": artifacts["file_list"]["list_payload_sha256"],
        "dispositions": dispositions,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if artifacts["gate"]["verdict"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
