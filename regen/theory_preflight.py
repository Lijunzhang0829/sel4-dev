#!/usr/bin/env python3
"""Audit premise health and build a bounded, target-private-free planner context."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from theory_trace import TheoryAuditTrail


REPO = Path(__file__).resolve().parent.parent
DEFAULT_CAMPAIGN = REPO / "reports" / "regen" / "refine-arm-session"
DEFAULT_THEORY = "Refine.InvariantUpdates_H"
CONTAINER = os.environ.get("REGEN_CONTAINER", "sel4-l4v")
PARENT_SESSION = "RegenRefineARM_L1"
MAX_PLANNER_CHARS = 120_000
RETRIEVAL_K = 32
QUERY = (
    "kernel state update invariant preservation invs' valid_state' valid_pspace' "
    "machine state scheduler queues interrupt architecture state"
)

L8_DEPENDENCY_DIRS = [
    "generated", "generated-layer1", "generated-layer2-invariant",
    "generated-layer2-invocations", "generated-layer3-state-relation",
    "generated-layer4-corres", "generated-layer5-bits",
    "generated-layer6-support-wave", "generated-layer7-kheap",
]
PREFLIGHTS = {
    "Refine.InvariantUpdates_H": {
        "parent_session": "RegenRefineARM_L1",
        "imports": ["RegenRefineARM_L1.Invariants_H"],
        "dependency_dirs": ["generated", "generated-layer1"],
        "heap_manifest": "framework/seal-layer1/manifest.json",
        "query": QUERY,
    },
    "Refine.Invocations_R": {
        "parent_session": "RegenRefineARM_L1",
        "imports": ["RegenRefineARM_L1.Invariants_H"],
        "dependency_dirs": ["generated", "generated-layer1"],
        "heap_manifest": "framework/seal-layer1/manifest.json",
        "query": QUERY,
    },
    "Refine.Init_R": {
        "parent_session": "RegenRefineARM_L7",
        "imports": ["RegenRefineARM_L7.KHeap_R"],
        "dependency_dirs": L8_DEPENDENCY_DIRS,
        "heap_manifest": "seal-layer7-kheap/manifest.json",
        "query": "initialisation kernel state object heap relation correspondence",
    },
    "Refine.SubMonad_R": {
        "parent_session": "RegenRefineARM_L7",
        "imports": ["RegenRefineARM_L6.EmptyFail", "RegenRefineARM_L7.KHeap_R"],
        "dependency_dirs": L8_DEPENDENCY_DIRS,
        "heap_manifest": "seal-layer7-kheap/manifest.json",
        "query": "submonad lift getObject setObject correspondence empty_fail bind stateAssert",
    },
}


def read_json(path: Path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalized_statement_hash(statement: str) -> str:
    return sha256_text(" ".join(statement.split()))


def qualified_theories(names: list[str]) -> set[str]:
    return {name if "." in name else f"Refine.{name}" for name in names}


def check_shell(campaign_dir: Path, theory_name: str, config: dict) -> dict:
    basename = theory_name.split(".", 1)[1]
    preflight_dir = campaign_dir / "preflight" / basename
    shell = preflight_dir / f"{basename}.thy"
    shell.parent.mkdir(parents=True, exist_ok=True)
    shell.write_text("\n".join([
        "(* Generated preflight shell; contains no target-private declarations. *)",
        f"theory {basename}", "imports",
        *(f"  \"{item}\"" for item in config["imports"]),
        "begin", "end", "",
    ]), encoding="utf-8")
    dependency_args = []
    for item in config["dependency_dirs"]:
        path = "/workspace/" + str((campaign_dir / item).relative_to(REPO))
        dependency_args.extend(["-d", path])
    shell_ctr = "/workspace/" + str(shell.with_suffix("").relative_to(REPO))
    command = [
        "docker", "exec", "-e", "L4V_ARCH=ARM",
        "-e", "L4V_DIR=/sel4-project/verification/l4v", CONTAINER,
        "/workspace/verification/isabelle/bin/isabelle", "process",
        "-l", config["parent_session"], *dependency_args,
        "-d", "/sel4-project/verification/l4v", "-T", shell_ctr,
    ]
    started = time.time()
    process = subprocess.run(command, cwd=REPO, text=True, capture_output=True)
    log = preflight_dir / "context-check.log"
    log.write_text(process.stdout + process.stderr, encoding="utf-8")
    return {
        "verdict": "pass" if process.returncode == 0 else "fail",
        "returncode": process.returncode,
        "wall_ms": int((time.time() - started) * 1000),
        "parent_session": config["parent_session"],
        "shell": str(shell.relative_to(REPO)),
        "shell_sha256": hashlib.sha256(shell.read_bytes()).hexdigest(),
        "log": str(log.relative_to(REPO)), "human_refine_imports": [],
    }


def check_heap(campaign_dir: Path, config: dict) -> dict:
    manifest = read_json(campaign_dir / config["heap_manifest"])
    expected = manifest["heap"]
    process = subprocess.run(
        ["docker", "exec", CONTAINER, "sha256sum", expected["container_path"]],
        cwd=REPO, text=True, capture_output=True,
    )
    actual = process.stdout.split()[0] if process.returncode == 0 and process.stdout else None
    return {
        "verdict": "pass" if actual == expected["sha256"] else "fail",
        "session": manifest["session"], "expected_sha256": expected["sha256"],
        "actual_sha256": actual,
        "human_refine_heap_loaded": manifest["human_refine_heap_loaded"],
        "downstream_smoke": manifest["downstream_smoke"]["verdict"],
    }


def generated_premises(campaign_dir: Path, allowed: set[str]) -> list[dict]:
    sys.path.insert(0, str(REPO / "regen" / "scripts"))
    from regen_extract import slice_decls

    records = []
    seen = set()
    for directory in L8_DEPENDENCY_DIRS:
        root = campaign_dir / directory
        for path in sorted(root.glob("*.thy")) if root.is_dir() else []:
            theory = f"Refine.{path.stem}"
            if theory not in allowed:
                continue
            source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            for declaration in slice_decls(path.read_text(encoding="utf-8")):
                name, statement = declaration.get("name"), declaration.get("stmt", "")
                if not name or not statement.strip():
                    continue
                key = (theory, name, normalized_statement_hash(statement))
                if key in seen:
                    continue
                seen.add(key)
                records.append({
                    "id": f"generated-premise:{theory}:{name}:{key[2][:12]}",
                    "theory": theory, "session": "GeneratedRefine",
                    "name": name, "candidate_names": [name],
                    "kind": declaration["kind"], "record_role": "generated_export",
                    "source_class": "generated_ancestor",
                    "source_path": str(path.relative_to(REPO)),
                    "source_sha256": source_hash, "statement": statement,
                    "statement_hash": key[2],
                })
    return records


def shortest_consumer_path(graph: list[dict], start: str, goal: str) -> list[str]:
    children: dict[str, list[str]] = {}
    for item in graph:
        for parent in item["target_imports"]:
            children.setdefault(parent, []).append(item["name"])
    queue = [[start]]
    seen = {start}
    while queue:
        path = queue.pop(0)
        if path[-1] == goal:
            return path
        for child in sorted(children.get(path[-1], [])):
            if child not in seen:
                seen.add(child)
                queue.append([*path, child])
    return []


def run(campaign_dir: Path, theory_name: str) -> dict:
    if theory_name not in PREFLIGHTS:
        raise SystemExit(f"no preflight configuration for {theory_name}")
    config = PREFLIGHTS[theory_name]
    graph = read_json(campaign_dir / "theory_graph.json")
    by_theory = {item["name"]: item for item in graph["theories"]}
    if theory_name not in by_theory:
        raise SystemExit(f"unknown target theory: {theory_name}")
    theory = by_theory[theory_name]
    state = read_json(campaign_dir / "state.json")
    sealed = qualified_theories(state.get("theories", []))
    target_imports = set(theory["target_imports"])
    waiting_for = sorted(target_imports - sealed)
    readiness = {
        "verdict": "pass" if not waiting_for else "fail",
        "target_imports": sorted(target_imports),
        "sealed_target_theories": sorted(sealed),
        "waiting_for": waiting_for,
    }

    visibility = read_json(campaign_dir / "visibility.json")[theory_name]
    allowed_theories = set(visibility["frozen_external_ancestors"])
    catalog = read_json(campaign_dir / "premise_catalog.json")
    catalog_records_hash = sha256_text(json.dumps(catalog["records"], sort_keys=True))
    accessible = [
        record for record in catalog["records"]
        if record["theory"] in allowed_theories
    ]
    malformed = [
        record["id"] for record in accessible
        if not record["statement"].strip()
        or record["statement_hash"] != normalized_statement_hash(record["statement"])
        or record["session"] == "Refine"
        or record["theory"] not in allowed_theories
    ]
    names = Counter(
        name for record in accessible for name in record.get("candidate_names", [])
    )

    sys.path.insert(0, str(REPO / "regen"))
    from session_harness import bm25
    selected = bm25(accessible, config["query"], RETRIEVAL_K)
    selected_payload = []
    for record in selected:
        candidate_names = record.get("candidate_names", [])
        selected_payload.append({
            key: record[key]
            for key in (
                "id", "score", "theory", "session", "name", "candidate_names",
                "kind", "record_role", "source_class", "statement", "statement_hash",
            )
        } | {
            "reference_policy": (
                "qualified_required"
                if any(names[name] > 1 for name in candidate_names)
                else "unqualified_unique"
            ),
        })
    generated_accessible = generated_premises(
        campaign_dir, set(visibility["generated_target_ancestors"]),
    )
    selected_generated = bm25(
        generated_accessible, config["query"], min(RETRIEVAL_K, 24),
    )

    contracts = read_json(campaign_dir / "contracts.json")["contracts"]
    downstream = set(theory["descendants"])
    downstream_all = [item for item in contracts if item["theory"] in downstream]
    nearest_layer = min(
        (by_theory[item["theory"]]["topological_layer"] for item in downstream_all),
        default=None,
    )
    nearest_all = [
        contract for contract in downstream_all
        if by_theory[contract["theory"]]["topological_layer"] == nearest_layer
    ]
    downstream_contracts = [
        {
            **{key: contract[key] for key in ("id", "theory", "name", "statement")},
            "attributes": contract.get("attributes", ""),
        }
        for contract in nearest_all
    ]
    contracts_by_theory: dict[str, list[dict]] = {}
    for contract in nearest_all:
        contracts_by_theory.setdefault(contract["theory"], []).append(contract)
    consumer_import_paths = [
        {
            "consumer_theory": consumer,
            "import_path": shortest_consumer_path(graph["theories"], theory_name, consumer),
            "contract_ids": [item["id"] for item in items],
        }
        for consumer, items in sorted(contracts_by_theory.items())
    ]
    symbol_counts = Counter(
        symbol
        for contract in nearest_all
        for symbol in set(re.findall(
            r"[A-Za-z_][A-Za-z0-9_]*", contract["statement"],
        ))
    )
    contract_symbol_frequencies = [
        {"symbol": symbol, "contract_count": count}
        for symbol, count in sorted(symbol_counts.items(), key=lambda item: (-item[1], item[0]))
        if count >= 2 and symbol not in {"lemma", "and", "or", "K", "p", "s", "dc"}
    ][:80]
    inventory = read_json(campaign_dir / "framework" / "inventory.json")
    generated_ancestors = set(theory["target_ancestors"]) | target_imports
    framework = [
        {key: declaration[key] for key in ("id", "theory", "name", "kind")}
        for declaration in inventory["declarations"]
        if declaration["theory"] in generated_ancestors
    ]
    heap = check_heap(campaign_dir, config)
    payload = {
        "schema_version": 1,
        "target_theory": theory_name,
        "target_role": "support-only theory with no fixed local contract",
        "direct_generated_imports": config["imports"],
        "generated_ancestor_heap": heap,
        "fixed_framework_declaration_summaries": framework,
        "reachable_fixed_contract_summaries": downstream_contracts,
        "consumer_import_paths": consumer_import_paths,
        "contract_symbol_frequencies": contract_symbol_frequencies,
        "retrieved_legal_generated_premises": selected_generated,
        "retrieved_legal_upstream_premises": selected_payload,
        "context_policy": {
            "human_target_source_included": False,
            "human_target_declarations_included": False,
            "original_target_premises_allowed": False,
            "retrieval_applied_after_accessibility_filter": True,
            "all_ambiguous_premise_names_must_be_theory_qualified": True,
            "full_statements_require_a_second_concept-specific_retrieval": False,
        },
    }
    payload_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    context_budget = {
        "verdict": "pass" if len(payload_text) <= MAX_PLANNER_CHARS else "fail",
        "actual_chars": len(payload_text),
        "max_chars": MAX_PLANNER_CHARS,
        "retrieval_k": RETRIEVAL_K,
        "accessible_catalog_records": len(accessible),
        "records_sent_to_planner": len(selected_payload),
        "framework_summaries_sent": len(framework),
        "generated_records_sent_to_planner": len(selected_generated),
        "contract_summaries_sent": len(downstream_contracts),
    }
    catalog_health = {
        "verdict": "pass" if not malformed and catalog_records_hash == catalog["records_sha256"] else "fail",
        "catalog_records": catalog["record_count"],
        "accessible_records": len(accessible),
        "accessible_theories": len({record["theory"] for record in accessible}),
        "target_session_leaks": sum(record["session"] == "Refine" for record in accessible),
        "malformed_records": malformed,
        "source_classes": dict(Counter(record["source_class"] for record in accessible)),
        "ambiguous_candidate_names": sum(count > 1 for count in names.values()),
        "max_name_collision": max(names.values(), default=0),
        "collision_policy": "qualify selected ambiguous names; never resolve by first match",
        "extractor_sha256": catalog["extractor_sha256"],
        "records_sha256": catalog["records_sha256"],
    }

    basename = theory_name.split(".", 1)[1]
    out_dir = campaign_dir / "preflight" / basename
    write_json(out_dir / "planner-input.json", payload)
    shell = check_shell(campaign_dir, theory_name, config)
    checks = {
        "readiness": readiness,
        "catalog_health": catalog_health,
        "heap_identity": heap,
        "context_budget": context_budget,
        "minimal_context": shell,
    }
    verdict = "pass" if all(item["verdict"] == "pass" for item in checks.values()) else "fail"
    report = {
        "schema_version": 1,
        "theory": theory_name,
        "verdict": verdict,
        "checks": checks,
        "planner_input": str((out_dir / "planner-input.json").relative_to(REPO)),
        "planner_input_sha256": sha256_text(payload_text),
        "target_source_read_by_context_builder": False,
    }
    write_json(out_dir / "report.json", report)
    if verdict != "pass":
        raise SystemExit(json.dumps(report, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    parser.add_argument("--theory", default=DEFAULT_THEORY)
    parser.add_argument("--generation-id", required=True)
    args = parser.parse_args()
    campaign_dir = args.campaign_dir.resolve()
    audit_trail = TheoryAuditTrail(
        campaign_dir, args.theory, generation_id=args.generation_id,
    )
    audit_trail.record_harness("preflight-start")
    basename = args.theory.split(".", 1)[-1]
    out_dir = campaign_dir / "preflight" / basename
    candidates = [
        out_dir / "planner-input.json", out_dir / "report.json",
        out_dir / f"{basename}.thy", out_dir / "context-check.log",
    ]
    try:
        report = run(campaign_dir, args.theory)
    except BaseException as error:
        audit_trail.record_harness(
            "preflight-failed",
            artifacts=[path for path in candidates if path.is_file()],
            details={"error_type": type(error).__name__, "error": str(error)},
        )
        raise
    audit_trail.record_harness(
        "preflight-complete", artifacts=candidates,
        details={"verdict": report["verdict"]},
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
