#!/usr/bin/env python3
"""Plan and checkpoint the support-only Refine wave above Bits_R."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from audited_llm import record_file_agent, sha256_text
from session_harness import bm25


REPO = Path(__file__).resolve().parent.parent
DEFAULT_CAMPAIGN = REPO / "reports" / "regen" / "refine-arm-session"
CONTAINER = os.environ.get("REGEN_CONTAINER", "sel4-l4v")
PARENT_SESSION = "RegenRefineARM_L5"
SESSION = "RegenRefineARM_L6"
TARGETS = ("Refine.EmptyFail", "Refine.Machine_R")
GENERATED_DIR = "generated-layer6-support-wave"
CHECK_DIR = "checks-layer6-support-wave"
SEAL_DIR = "seal-layer6-support-wave"
DEPENDENCY_DIRS = [
    "generated", "generated-layer1", "generated-layer2-invariant",
    "generated-layer2-invocations", "generated-layer3-state-relation",
    "generated-layer4-corres", "generated-layer5-bits",
]
CONTEXT_LIMIT = 120_000
RETRIEVAL_K = 24
FORBIDDEN_RE = re.compile(r"\b(sorry|oops|axiomatization|admit)\b")


def read_json(path: Path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def container_run(arguments: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "docker", "exec", "-e", "L4V_ARCH=ARM", "-e",
            "L4V_DIR=/sel4-project/verification/l4v", CONTAINER, *arguments,
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
    )


def dependency_args(campaign_dir: Path) -> list[str]:
    result = []
    for name in DEPENDENCY_DIRS:
        result.extend([
            "-d", "/workspace/" + str((campaign_dir / name).relative_to(REPO)),
        ])
    return result


def qualified(names: list[str]) -> set[str]:
    return {name if "." in name else f"Refine.{name}" for name in names}


def planner_prompt(payload: dict) -> str:
    return """Plan the exported proof interface of one support-only Isabelle
theory in a strict seL4 Refine regeneration campaign. The payload is the complete
input. Do not use tools, files, external knowledge, remembered human Refine
lemmas, or theorem names inferred from the target theory name.

This theory owns no fixed operation-level contract. Propose an export only when
the exact fixed consumer contracts or endpoint/premise evidence establish both
the theorem concept and why this theory should own it. Otherwise return empty.
Do not invent an Isabelle statement yet.

Return only JSON:
{
  "schema_version": 1,
  "theory": "Refine.X",
  "decision": "empty | exports",
  "requirements": [{
    "id": "requirement-id",
    "suggested_name": "regen_name",
    "concept": "semantic proof concept",
    "consumer_contract_ids": ["contract:..."],
    "owner_rationale": "...",
    "risk": "low | medium | high"
  }],
  "uncertainties": ["..."]
}

PAYLOAD:
""" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def critique_prompt(payload: dict, draft: dict) -> str:
    return """Critique this support-only Refine export plan. Remove every
requirement whose exact theorem shape or owner is not entailed by the supplied
consumer contracts, endpoint declarations, and legal premises. Contract names
alone are not enough. Prefer an empty structural checkpoint to speculative
scaffolding. Do not use tools or external information. Return the complete
revised JSON only.

PAYLOAD:
""" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + \
        "\n\nDRAFT:\n" + json.dumps(draft, ensure_ascii=False, separators=(",", ":"))


def validate_plan(plan: dict, theory: str, contract_ids: set[str]) -> None:
    if plan.get("schema_version") != 1 or plan.get("theory") != theory:
        raise ValueError("invalid support plan schema or theory")
    if plan.get("decision") not in {"empty", "exports"}:
        raise ValueError("invalid support plan decision")
    requirements = plan.get("requirements", [])
    if (plan["decision"] == "empty") != (not requirements):
        raise ValueError("support plan decision disagrees with requirements")
    for item in requirements:
        if not item.get("suggested_name", "").startswith("regen_"):
            raise ValueError("generated support name must begin regen_")
        if not set(item.get("consumer_contract_ids", [])) <= contract_ids:
            raise ValueError("support requirement cites an unknown contract")


def build_payload(campaign_dir: Path, theory: str) -> dict:
    graph = read_json(campaign_dir / "theory_graph.json")["theories"]
    by_theory = {item["name"]: item for item in graph}
    state = read_json(campaign_dir / "state.json")
    waiting = sorted(set(by_theory[theory]["target_imports"]) - qualified(state["theories"]))
    if waiting:
        raise SystemExit(f"{theory} is not ready; waiting for {waiting}")

    children = defaultdict(list)
    for item in graph:
        for parent in item["target_imports"]:
            children[parent].append(item["name"])
    immediate = sorted(children[theory])
    contracts = read_json(campaign_dir / "contracts.json")["contracts"]
    immediate_contracts = [item for item in contracts if item["theory"] in immediate]
    descendant_names = set(by_theory[theory]["descendants"])
    descendant_contracts = [item for item in contracts if item["theory"] in descendant_names]
    contract_layers = [
        by_theory[item["theory"]]["topological_layer"] for item in descendant_contracts
    ]
    nearest_layer = min(contract_layers, default=None)
    nearest = [
        item for item in descendant_contracts
        if by_theory[item["theory"]]["topological_layer"] == nearest_layer
    ][:40]

    visibility = read_json(campaign_dir / "visibility.json")[theory]
    allowed = set(visibility["frozen_external_ancestors"])
    catalog = read_json(campaign_dir / "premise_catalog.json")
    accessible = [item for item in catalog["records"] if item["theory"] in allowed]
    records_hash = sha256_text(json.dumps(catalog["records"], sort_keys=True))
    malformed = [
        item["id"] for item in accessible
        if item["session"] == "Refine" or item["theory"] not in allowed
        or item["statement_hash"] != sha256_text(" ".join(item["statement"].split()))
    ]
    if malformed or records_hash != catalog["records_sha256"]:
        raise SystemExit(f"premise catalog health failed for {theory}")
    query = {
        "Refine.EmptyFail": (
            "empty_fail failure monad project object thread get set capability "
            "submonad correspondence no failure"
        ),
        "Refine.Machine_R": (
            "machine operation irq interrupt mask invariant valid state doMachineOp "
            "kernel object correspondence"
        ),
    }[theory]
    premises = bm25(accessible, query, RETRIEVAL_K)
    bits_manifest = read_json(campaign_dir / "seal-layer5-bits" / "manifest.json")
    if bits_manifest["human_refine_heap_loaded"]:
        raise SystemExit("Bits_R manifest reports human Refine heap use")
    payload = {
        "schema_version": 1,
        "target_theory": theory,
        "target_role": "support-only theory with no fixed local contract",
        "direct_generated_imports": [f"{PARENT_SESSION}.Bits_R"],
        "immediate_consumer_theories": immediate,
        "immediate_consumer_contracts": immediate_contracts,
        "nearest_descendant_contract_summaries": [
            {key: item[key] for key in ("id", "theory", "name")}
            for item in nearest
        ],
        "retrieved_legal_upstream_premises": premises,
        "generated_ancestor": {
            "theory": bits_manifest["theory"],
            "session": bits_manifest["session"],
            "source_sha256": bits_manifest["source_sha256"],
            "heap_sha256": bits_manifest["heap"]["sha256"],
            "human_refine_heap_loaded": False,
        },
        "policy": {
            "human_target_source_included": False,
            "human_target_declarations_included": False,
            "original_target_premises_allowed": False,
            "empty_is_structural_checkpoint_not_proof_complete": True,
            "consumer_residual_may_invalidate_and_backpatch": True,
        },
        "catalog_health": {
            "records_sha256": records_hash,
            "accessible_records": len(accessible),
            "records_sent": len(premises),
            "target_session_leaks": sum(item["session"] == "Refine" for item in accessible),
            "source_classes": dict(Counter(item["source_class"] for item in accessible)),
        },
    }
    return payload


def preflight(campaign_dir: Path, theory: str, payload: dict) -> dict:
    basename = theory.split(".", 1)[1]
    out_dir = campaign_dir / "preflight" / basename
    out_dir.mkdir(parents=True, exist_ok=True)
    payload_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    write_json(out_dir / "planner-input.json", payload)
    shell = out_dir / f"{basename}_Preflight.thy"
    shell.write_text("\n".join([
        f"theory {basename}_Preflight",
        f'imports "{PARENT_SESSION}.Bits_R"',
        "begin",
        "end",
        "",
    ]), encoding="utf-8")
    started = time.time()
    check = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "process",
        "-l", PARENT_SESSION, *dependency_args(campaign_dir),
        "-d", "/sel4-project/verification/l4v", "-T",
        "/workspace/" + str(shell.with_suffix("").relative_to(REPO)),
    ])
    log = out_dir / "context-check.log"
    log.write_text(check.stdout + check.stderr, encoding="utf-8")
    report = {
        "schema_version": 1,
        "theory": theory,
        "verdict": (
            "pass" if check.returncode == 0 and len(payload_text) <= CONTEXT_LIMIT else "fail"
        ),
        "minimal_context": {
            "verdict": "pass" if check.returncode == 0 else "fail",
            "returncode": check.returncode,
            "wall_ms": int((time.time() - started) * 1000),
            "shell": str(shell.relative_to(REPO)),
            "log": str(log.relative_to(REPO)),
        },
        "context_budget": {
            "verdict": "pass" if len(payload_text) <= CONTEXT_LIMIT else "fail",
            "actual_chars": len(payload_text),
            "max_chars": CONTEXT_LIMIT,
        },
        "catalog_health": payload["catalog_health"],
        "target_source_read_by_context_builder": False,
        "planner_input_sha256": sha256_text(payload_text),
    }
    write_json(out_dir / "report.json", report)
    if report["verdict"] != "pass":
        raise SystemExit(f"preflight failed for {theory}")
    return report


def plan(campaign_dir: Path, theory: str, payload: dict, response_dir: Path) -> dict:
    basename = theory.split(".", 1)[1]
    planner_dir = campaign_dir / "planner" / basename
    first_prompt = planner_prompt(payload)
    if len(first_prompt) > CONTEXT_LIMIT:
        raise SystemExit(f"planner prompt exceeds limit for {theory}")
    draft_response = response_dir / f"{basename}-plan.json"
    draft = json.loads(record_file_agent(
        first_prompt,
        draft_response.read_text(encoding="utf-8"),
        planner_dir / "01-export-plan",
        model="current-codex-session-contaminated",
    ))
    contract_ids = {
        item["id"] for item in payload["immediate_consumer_contracts"]
    } | {
        item["id"] for item in payload["nearest_descendant_contract_summaries"]
    }
    validate_plan(draft, theory, contract_ids)
    write_json(planner_dir / "01-export-plan.parsed.json", draft)

    second_prompt = critique_prompt(payload, draft)
    if len(second_prompt) > CONTEXT_LIMIT:
        raise SystemExit(f"critique prompt exceeds limit for {theory}")
    critique_response = response_dir / f"{basename}-critique.json"
    revised = json.loads(record_file_agent(
        second_prompt,
        critique_response.read_text(encoding="utf-8"),
        planner_dir / "02-export-critique",
        model="current-codex-session-contaminated",
    ))
    validate_plan(revised, theory, contract_ids)
    revised["planner_input_sha256"] = sha256_text(json.dumps(payload, sort_keys=True))
    revised["draft_sha256"] = sha256_text(json.dumps(draft, sort_keys=True))
    revised["context_chars"] = {
        "draft": len(first_prompt), "critique": len(second_prompt), "limit": CONTEXT_LIMIT,
    }
    write_json(planner_dir / "export-plan.json", revised)
    return revised


def render(basename: str) -> str:
    return "\n".join([
        "(* Generated from an audited empty support-interface plan. *)",
        f"theory {basename}",
        f'imports "{PARENT_SESSION}.Bits_R"',
        "begin",
        "end",
        "",
    ])


def assemble_and_seal(campaign_dir: Path, plans: dict[str, dict], preflights: dict[str, dict]) -> dict:
    if any(plan["decision"] != "empty" for plan in plans.values()):
        raise SystemExit("support wave assembler currently accepts only reviewed empty plans")
    generated = campaign_dir / GENERATED_DIR
    generated.mkdir(parents=True, exist_ok=True)
    checks = []
    paths = []
    check_dir = campaign_dir / CHECK_DIR
    check_dir.mkdir(parents=True, exist_ok=True)
    for theory in TARGETS:
        basename = theory.split(".", 1)[1]
        path = generated / f"{basename}.thy"
        path.write_text(render(basename), encoding="utf-8")
        source = path.read_text(encoding="utf-8")
        if FORBIDDEN_RE.search(source) or re.search(
            r"^\s*(?:lemma|theorem|corollary|lemmas|schematic_goal)\b", source, re.M
        ):
            raise SystemExit(f"empty support theory contains a proof command: {theory}")
        started = time.time()
        check = container_run([
            "/workspace/verification/isabelle/bin/isabelle", "process",
            "-l", PARENT_SESSION, *dependency_args(campaign_dir),
            "-d", "/sel4-project/verification/l4v", "-T",
            "/workspace/" + str(path.with_suffix("").relative_to(REPO)),
        ])
        log = check_dir / f"{basename}.log"
        log.write_text(check.stdout + check.stderr, encoding="utf-8")
        record = {
            "theory": theory,
            "verdict": "pass" if check.returncode == 0 else "fail",
            "returncode": check.returncode,
            "wall_ms": int((time.time() - started) * 1000),
            "source": str(path.relative_to(REPO)),
            "source_sha256": sha256_path(path),
            "log": str(log.relative_to(REPO)),
        }
        checks.append(record)
        if check.returncode:
            write_json(check_dir / "checks.json", {"checks": checks})
            raise SystemExit(f"theory check failed for {theory}")
        paths.append(path)
    write_json(check_dir / "checks.json", {"checks": checks})

    (generated / "ROOT").write_text("\n".join([
        f'session {SESSION} in "." = {PARENT_SESSION} +',
        "  theories",
        *(f"    {path.stem}" for path in paths),
        "",
    ]), encoding="utf-8")
    generated_ctr = "/workspace/" + str(generated.relative_to(REPO))
    seal_dir = campaign_dir / SEAL_DIR
    seal_dir.mkdir(parents=True, exist_ok=True)
    build_started = time.time()
    build = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "build",
        *dependency_args(campaign_dir), "-d", generated_ctr,
        "-d", "/sel4-project/verification/l4v", "-b", SESSION,
    ])
    build_log = seal_dir / "build.log"
    build_log.write_text(build.stdout + build.stderr, encoding="utf-8")
    if build.returncode:
        raise SystemExit(f"support wave heap build failed; see {build_log}")
    getenv = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "getenv", "-b", "ISABELLE_HEAPS",
    ])
    heap_path = f"{getenv.stdout.strip()}/polyml-5.9.1_x86_64_32-linux/{SESSION}"
    identity = container_run(["bash", "-lc", f"sha256sum '{heap_path}'; stat -c %s '{heap_path}'"])
    identity_lines = identity.stdout.splitlines()
    if identity.returncode or len(identity_lines) < 2:
        raise SystemExit("could not identify support wave heap")

    smoke_dir = seal_dir / "smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    smoke_path = smoke_dir / "SupportWaveSmoke.thy"
    smoke_path.write_text("\n".join([
        "theory SupportWaveSmoke",
        "imports",
        f'  "{SESSION}.EmptyFail"',
        f'  "{SESSION}.Machine_R"',
        "begin",
        "end",
        "",
    ]), encoding="utf-8")
    smoke_started = time.time()
    smoke = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "process",
        "-l", SESSION, *dependency_args(campaign_dir), "-d", generated_ctr,
        "-d", "/sel4-project/verification/l4v", "-T",
        "/workspace/" + str(smoke_path.with_suffix("").relative_to(REPO)),
    ])
    smoke_log = smoke_dir / "check.log"
    smoke_log.write_text(smoke.stdout + smoke.stderr, encoding="utf-8")
    if smoke.returncode:
        raise SystemExit(f"support wave child smoke failed; see {smoke_log}")

    heap = {
        "container_path": heap_path,
        "sha256": identity_lines[0].split()[0],
        "size_bytes": int(identity_lines[1]),
    }
    for theory, path in zip(TARGETS, paths):
        basename = theory.split(".", 1)[1]
        exports_path = campaign_dir / "exports" / f"{basename}.json"
        write_json(exports_path, {
            "schema_version": 1,
            "theory": theory,
            "interface_kind": "empty_structural_checkpoint",
            "proof_exports": [],
            "attributes": [],
            "source_sha256": sha256_path(path),
            "planner_plan": str((campaign_dir / "planner" / basename / "export-plan.json").relative_to(REPO)),
        })
        write_json(campaign_dir / f"lemma_graph-{basename}.json", {
            "schema_version": 1,
            "theory": theory,
            "status": "structural_checkpoint",
            "contract_roots": [],
            "nodes": [],
            "invalidatable_until_consumer_validated": True,
            "export_interface": str(exports_path.relative_to(REPO)),
            "seal_session": SESSION,
            "source_sha256": sha256_path(path),
        })

    manifest = {
        "schema_version": 1,
        "status": "structural_checkpoint",
        "checkpoint_kind": "consumer_planned_empty_support_wave",
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "session": SESSION,
        "parent_session": PARENT_SESSION,
        "theories": list(TARGETS),
        "sources": {
            theory: {
                "path": str(path.relative_to(REPO)), "sha256": sha256_path(path),
            }
            for theory, path in zip(TARGETS, paths)
        },
        "preflights": {
            theory: str((campaign_dir / "preflight" / theory.split(".", 1)[1] / "report.json").relative_to(REPO))
            for theory in TARGETS
        },
        "plans": {
            theory: str((campaign_dir / "planner" / theory.split(".", 1)[1] / "export-plan.json").relative_to(REPO))
            for theory in TARGETS
        },
        "checks": checks,
        "build_returncode": build.returncode,
        "build_wall_ms": int((time.time() - build_started) * 1000),
        "build_log": str(build_log.relative_to(REPO)),
        "heap": heap,
        "downstream_smoke": {
            "verdict": "pass",
            "returncode": smoke.returncode,
            "wall_ms": int((time.time() - smoke_started) * 1000),
            "source": str(smoke_path.relative_to(REPO)),
            "log": str(smoke_log.relative_to(REPO)),
        },
        "human_refine_heap_loaded": False,
        "human_refine_imports": [],
        "invalidatable_until_consumer_validated": True,
    }
    write_json(seal_dir / "manifest.json", manifest)
    state = read_json(campaign_dir / "state.json")
    theories = list(state["theories"])
    for basename in ("EmptyFail", "Machine_R"):
        if basename not in theories:
            theories.append(basename)
    write_json(campaign_dir / "state.json", {
        "schema_version": 1,
        "layer": 6,
        "status": "structural_checkpoint",
        "checkpoint_kind": "consumer_planned_empty_support_wave",
        "session": SESSION,
        "theories": theories,
        "next_ready_theories": ["Refine.KHeap_R"],
        "wave_status": "complete",
        "invalidatable_theories": ["Refine.EmptyFail", "Refine.Machine_R"],
        "first_contract_milestone": state.get("first_contract_milestone"),
    })
    return manifest


def run(campaign_dir: Path, response_dir: Path) -> dict:
    payloads = {theory: build_payload(campaign_dir, theory) for theory in TARGETS}
    preflights = {
        theory: preflight(campaign_dir, theory, payloads[theory]) for theory in TARGETS
    }
    plans = {
        theory: plan(campaign_dir, theory, payloads[theory], response_dir)
        for theory in TARGETS
    }
    manifest = assemble_and_seal(campaign_dir, plans, preflights)
    return {"preflights": preflights, "plans": plans, "manifest": manifest}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    parser.add_argument("--response-dir", type=Path, required=True)
    parser.add_argument("--non-production-replay", action="store_true")
    args = parser.parse_args()
    if not args.non_production_replay:
        raise SystemExit(
            "support_wave.py is replay-only and cannot production-seal; use "
            "theory_export_planner.py plus theory_checkpoint.py with one generation id"
        )
    print(json.dumps(run(
        args.campaign_dir.resolve(), args.response_dir.resolve()
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
