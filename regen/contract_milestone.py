#!/usr/bin/env python3
"""Plan, generate, verify, and seal the first Refine contract theory."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from audited_llm import sha256_text
from llm_transcript import call_llm, record_replay
from session_harness import bm25
from theory_trace import TheoryAuditTrail


REPO = Path(__file__).resolve().parent.parent
DEFAULT_CAMPAIGN = REPO / "reports" / "regen" / "refine-arm-session"
CONTAINER = os.environ.get("REGEN_CONTAINER", "sel4-l4v")
THEORY = "Refine.Bits_R"
PARENT_SESSION = "RegenRefineARM_L4"
SESSION = "RegenRefineARM_L5"
GENERATED_DIR = "generated-layer5-bits"
DEPENDENCY_DIRS = [
    "generated", "generated-layer1", "generated-layer2-invariant",
    "generated-layer2-invocations", "generated-layer3-state-relation",
    "generated-layer4-corres",
]
CONTEXT_LIMIT = 120_000
FORBIDDEN_RE = re.compile(r"\b(sorry|oops|axiomatization|admit)\b")
JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.S)


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


def parse_json(text: str):
    match = JSON_FENCE_RE.search(text)
    return json.loads((match.group(1) if match else text).strip())


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


def contract_body(statement: str) -> str:
    start = statement.find('"')
    end = statement.rfind('"')
    if start < 0 or end <= start:
        raise ValueError("contract statement has no quoted proposition")
    return statement[start:end + 1]


REPLAY_DIR: Path | None = None
PROVIDER = "codex"
AUDIT_TRAIL: TheoryAuditTrail | None = None


def planner_call(prompt: str, prefix: Path, *, model: str, timeout: int) -> str:
    if AUDIT_TRAIL is None:
        raise RuntimeError("theory audit trail was not initialized")
    stage = prefix.name
    artifact_prefix = AUDIT_TRAIL.run_dir / "llm" / f"{len(AUDIT_TRAIL.events()) + 1:04d}-{stage}"
    if REPLAY_DIR is None:
        return call_llm(
            PROVIDER, prompt, artifact_prefix, model=model, timeout=timeout,
            theory=THEORY, stage=stage, transcript_sink=AUDIT_TRAIL,
        )
    response_path = REPLAY_DIR / f"{stage}.final.txt"
    if not response_path.exists():
        raise SystemExit(f"missing replay response: {response_path}")
    response = response_path.read_text(encoding="utf-8")
    return record_replay(
        prompt, response, artifact_prefix, model="current-codex-session-replay",
        theory=THEORY, stage=stage, transcript_sink=AUDIT_TRAIL,
    )


def relevant_context(campaign_dir: Path) -> dict:
    graph = read_json(campaign_dir / "theory_graph.json")["theories"]
    graph_entry = next(item for item in graph if item["name"] == THEORY)
    state = read_json(campaign_dir / "state.json")
    completed = {name if "." in name else f"Refine.{name}" for name in state["theories"]}
    waiting = sorted(set(graph_entry["target_imports"]) - completed)
    if waiting:
        raise SystemExit(f"{THEORY} is not ready; waiting for {waiting}")

    all_contracts = read_json(campaign_dir / "contracts.json")["contracts"]
    contracts = [item for item in all_contracts if item["theory"] == THEORY]
    if len(contracts) != 2:
        raise SystemExit(f"expected two fixed Bits_R contracts, found {len(contracts)}")

    visibility = read_json(campaign_dir / "visibility.json")[THEORY]
    allowed = set(visibility["frozen_external_ancestors"])
    catalog = read_json(campaign_dir / "premise_catalog.json")
    records_hash = sha256_text(json.dumps(catalog["records"], sort_keys=True))
    accessible = [item for item in catalog["records"] if item["theory"] in allowed]
    malformed = [
        item["id"] for item in accessible
        if item["session"] == "Refine" or item["theory"] not in allowed
        or item["statement_hash"] != sha256_text(" ".join(item["statement"].split()))
    ]
    if malformed or records_hash != catalog["records_sha256"]:
        raise SystemExit("premise catalog health check failed")

    required_names = {
        "getCurThread", "getIdleThread", "corres_underlying", "corres_gets",
        "corres_gets_trivial", "gets",
    }
    exact = [item for item in accessible if item["name"] in required_names]
    retrieved = bm25(
        accessible,
        "getCurThread getIdleThread cur_thread idle_thread state_relation "
        "corres corres_underlying gets equality relation projection",
        24,
    )
    premise_by_id = {item["id"]: item for item in exact + retrieved}
    premises = [premise_by_id[key] for key in sorted(premise_by_id)]

    inventory = read_json(campaign_dir / "framework" / "inventory.json")
    framework = [
        item for item in inventory["declarations"]
        if item["id"] in {
            "framework:Refine.StateRelation:state_relation",
            "framework:Refine.Corres:corres",
        }
    ]
    manifests = [
        read_json(campaign_dir / "seal-layer3-state-relation" / "manifest.json"),
        read_json(campaign_dir / "seal-layer4-corres" / "manifest.json"),
    ]
    if any(item["human_refine_heap_loaded"] for item in manifests):
        raise SystemExit("generated ancestor manifest reports human Refine heap use")
    return {
        "schema_version": 1,
        "target_theory": THEORY,
        "target_role": "first operation-level correspondence milestone",
        "fixed_contracts": contracts,
        "fixed_framework_declarations": framework,
        "retrieved_legal_premises": premises,
        "generated_ancestor_manifests": [
            {
                "theory": item["theory"],
                "session": item["session"],
                "source_sha256": item["source_sha256"],
                "heap_sha256": item["heap"]["sha256"],
                "human_refine_heap_loaded": item["human_refine_heap_loaded"],
            }
            for item in manifests
        ],
        "policy": {
            "human_target_source_included": False,
            "human_target_proofs_allowed": False,
            "fixed_contract_statements_must_be_verbatim": True,
            "new_contract_roots_allowed": False,
            "initial_helpers_must_be_owned_by_Bits_R": True,
            "upstream_helper_promotion_requires_concrete_Isabelle_residual": True,
            "new_repair_helpers_are_leaf_nodes": True,
        },
        "catalog_health": {
            "records_sha256": records_hash,
            "accessible_records": len(accessible),
            "records_sent": len(premises),
            "target_session_leaks": sum(item["session"] == "Refine" for item in accessible),
            "source_classes": dict(Counter(item["source_class"] for item in accessible)),
        },
    }


def check_preflight(campaign_dir: Path, payload: dict) -> dict:
    out_dir = campaign_dir / "preflight" / "Bits_R"
    out_dir.mkdir(parents=True, exist_ok=True)
    shell_path = out_dir / "Bits_R_Preflight.thy"
    shell_path.write_text("\n".join([
        "theory Bits_R_Preflight",
        "imports \"RegenRefineARM_L4.Corres\"",
        "begin",
        "context begin interpretation Arch .",
        *(f"term {contract_body(item['statement'])}" for item in payload["fixed_contracts"]),
        "end",
        "end",
        "",
    ]), encoding="utf-8")
    started = time.time()
    check = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "process",
        "-l", PARENT_SESSION, *dependency_args(campaign_dir),
        "-d", "/sel4-project/verification/l4v", "-T",
        "/workspace/" + str(shell_path.with_suffix("").relative_to(REPO)),
    ])
    log_path = out_dir / "context-check.log"
    log_path.write_text(check.stdout + check.stderr, encoding="utf-8")
    payload_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    report = {
        "schema_version": 1,
        "theory": THEORY,
        "verdict": "pass" if check.returncode == 0 and len(payload_text) <= CONTEXT_LIMIT else "fail",
        "minimal_context": {
            "verdict": "pass" if check.returncode == 0 else "fail",
            "returncode": check.returncode,
            "wall_ms": int((time.time() - started) * 1000),
            "shell": str(shell_path.relative_to(REPO)),
            "log": str(log_path.relative_to(REPO)),
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
    write_json(out_dir / "planner-input.json", payload)
    write_json(out_dir / "report.json", report)
    if report["verdict"] != "pass":
        raise SystemExit(f"Bits_R preflight failed; see {out_dir}")
    return report


def planner_prompt(payload: dict) -> str:
    return """You are the theory planner for the first contract-bearing theory in a
strict seL4 Refine proof-regeneration campaign. The JSON payload is your complete
input. Do not use tools, files, external knowledge, or remembered human Refine
helper names.

Build a small lemma DAG rooted at every fixed contract. Contract statements are
immutable. Prefer no helper when a direct proof using generated framework
definitions and legal frozen-parent premises is plausible. An initial helper
must be owned by Refine.Bits_R, have a generated name beginning regen_, be used
by a listed contract, and be a dependency leaf. Upstream promotion is forbidden
until an Isabelle residual demonstrates need.

Return only JSON:
{
  "schema_version": 1,
  "theory": "Refine.Bits_R",
  "contract_order": ["contract:..."],
  "support_nodes": [{
    "id": "support:Refine.Bits_R:regen_name",
    "name": "regen_name",
    "statement": "lemma regen_name: ...",
    "dependencies": ["legal premise or framework IDs"],
    "consumers": ["contract:..."],
    "rationale": "..."
  }],
  "contract_nodes": [{
    "id": "contract:...",
    "dependencies": ["support or legal premise IDs"],
    "strategy": "proof idea without proof text"
  }],
  "uncertainties": ["..."]
}

PAYLOAD:
""" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def validate_plan(plan: dict, payload: dict) -> None:
    contract_ids = {item["id"] for item in payload["fixed_contracts"]}
    if plan.get("schema_version") != 1 or plan.get("theory") != THEORY:
        raise ValueError("invalid plan schema or theory")
    if set(plan.get("contract_order", [])) != contract_ids:
        raise ValueError("plan must schedule every fixed contract exactly once")
    nodes = plan.get("contract_nodes", [])
    if {item.get("id") for item in nodes} != contract_ids:
        raise ValueError("contract node set differs from fixed inventory")
    support_ids = set()
    for node in plan.get("support_nodes", []):
        if not node.get("name", "").startswith("regen_"):
            raise ValueError("support name must begin regen_")
        if not node.get("statement", "").lstrip().startswith(f"lemma {node['name']}"):
            raise ValueError("support statement must declare its generated name")
        if not set(node.get("consumers", [])) <= contract_ids:
            raise ValueError("support node has an unknown consumer")
        if node["id"] in support_ids:
            raise ValueError("duplicate support node ID")
        support_ids.add(node["id"])


def proof_prompt(payload: dict, plan: dict) -> str:
    return """Generate Isabelle proof bodies for this certified Refine.Bits_R
lemma plan. Use only declarations and premises in the payload plus earlier nodes
listed as dependencies. Do not use tools, files, external knowledge, human
Refine theorem names, sorry, oops, axioms, or new declarations. A proof must be
a complete `by ...` body or an `apply ... done` body. Keep fixed contract
statements unchanged.

Return only JSON:
{
  "schema_version": 1,
  "theory": "Refine.Bits_R",
  "proofs": [{"node_id": "...", "proof": "by ..."}]
}

PAYLOAD:
""" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + \
        "\n\nCERTIFIED PLAN:\n" + json.dumps(plan, ensure_ascii=False, separators=(",", ":"))


def validate_proofs(proofs: dict, plan: dict) -> dict[str, str]:
    expected = set(plan["contract_order"]) | {item["id"] for item in plan["support_nodes"]}
    if proofs.get("schema_version") != 1 or proofs.get("theory") != THEORY:
        raise ValueError("invalid proof response schema or theory")
    result = {item["node_id"]: item["proof"].strip() for item in proofs.get("proofs", [])}
    if set(result) != expected:
        raise ValueError("proof response does not cover the planned node set")
    for node_id, proof in result.items():
        if FORBIDDEN_RE.search(proof) or not (proof.startswith("by ") or proof.startswith("apply")):
            raise ValueError(f"illegal or incomplete proof body for {node_id}")
    return result


def render_theory(payload: dict, plan: dict, proofs: dict[str, str], upto: int | None = None) -> str:
    contracts = {item["id"]: item for item in payload["fixed_contracts"]}
    support = plan["support_nodes"]
    ordered_contracts = plan["contract_order"] if upto is None else plan["contract_order"][:upto]
    blocks = []
    for node in support:
        blocks.append(f"{node['statement'].rstrip()}\n{proofs[node['id']]}\n")
    for node_id in ordered_contracts:
        blocks.append(f"{contracts[node_id]['statement'].rstrip()}\n{proofs[node_id]}\n")
    source = "\n".join([
        "(* Generated from fixed contracts; no human Refine proof declarations used. *)",
        "theory Bits_R",
        'imports "RegenRefineARM_L4.Corres"',
        "begin",
        "context begin interpretation Arch .",
        "\n".join(blocks).rstrip(),
        "end",
        "end",
        "",
    ])
    if FORBIDDEN_RE.search(source):
        raise ValueError("generated source contains a forbidden command")
    return source


def check_source(campaign_dir: Path, source: str, label: str) -> dict:
    check_dir = campaign_dir / "checks-layer5-bits"
    check_dir.mkdir(parents=True, exist_ok=True)
    path = check_dir / f"{label}.thy"
    source = re.sub(r"^theory Bits_R$", f"theory {label}", source, count=1, flags=re.M)
    path.write_text(source, encoding="utf-8")
    started = time.time()
    process = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "process",
        "-l", PARENT_SESSION, *dependency_args(campaign_dir),
        "-d", "/sel4-project/verification/l4v", "-T",
        "/workspace/" + str(path.with_suffix("").relative_to(REPO)),
    ])
    log = check_dir / f"{label}.log"
    log.write_text(process.stdout + process.stderr, encoding="utf-8")
    record = {
        "verdict": "pass" if process.returncode == 0 else "fail",
        "returncode": process.returncode,
        "wall_ms": int((time.time() - started) * 1000),
        "source": str(path.relative_to(REPO)),
        "source_sha256": sha256_path(path),
        "log": str(log.relative_to(REPO)),
        "diagnostic": (process.stdout + process.stderr)[-16000:],
    }
    if AUDIT_TRAIL is None:
        raise RuntimeError("theory audit trail was not initialized")
    AUDIT_TRAIL.record_isabelle(label, record)
    return record


def repair_prompt(payload: dict, plan: dict, node_id: str, proof: str, diagnostic: str) -> str:
    return """Repair one failed Isabelle proof in a strict seL4 regeneration run.
Use only the supplied payload and diagnostic. Do not use tools, files, human
Refine theorem names, sorry, oops, or axioms. Prefer repairing the proof body.
You may add one Refine.Bits_R-owned leaf helper only when the residual clearly
justifies it; its name must begin regen_. Do not propose an upstream helper here.

Return only JSON, either:
{"action":"proof","proof":"by ...","residual_explanation":"..."}
or:
{"action":"local_helper","name":"regen_...","statement":"lemma regen_...: ...","proof":"by ...","contract_proof":"by ...","residual_explanation":"..."}

PAYLOAD:
""" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + \
        "\n\nPLAN:\n" + json.dumps(plan, ensure_ascii=False, separators=(",", ":")) + \
        f"\n\nFAILED NODE: {node_id}\nCURRENT PROOF:\n{proof}\n\nISABELLE DIAGNOSTIC:\n{diagnostic}"


def plan_and_prove(campaign_dir: Path, payload: dict, model: str, timeout: int) -> tuple[dict, dict[str, str], list[dict]]:
    planner_dir = campaign_dir / "planner" / "Bits_R"
    prompt = planner_prompt(payload)
    if len(prompt) > CONTEXT_LIMIT:
        raise SystemExit("Bits_R planner prompt exceeds context limit")
    plan = parse_json(planner_call(prompt, planner_dir / "01-lemma-dag", model=model, timeout=timeout))
    validate_plan(plan, payload)
    plan["planner_input_sha256"] = sha256_text(json.dumps(payload, sort_keys=True))
    plan["prompt_chars"] = len(prompt)
    write_json(planner_dir / "lemma-plan.json", plan)

    prompt = proof_prompt(payload, plan)
    if len(prompt) > CONTEXT_LIMIT:
        raise SystemExit("Bits_R proof prompt exceeds context limit")
    response = parse_json(planner_call(prompt, planner_dir / "02-proof-generation", model=model, timeout=timeout))
    proofs = validate_proofs(response, plan)
    write_json(planner_dir / "proofs.json", response)

    attempts = []
    for index, node_id in enumerate(plan["contract_order"], start=1):
        result = check_source(campaign_dir, render_theory(payload, plan, proofs, index), f"Bits_R_prefix_{index}")
        attempts.append({key: value for key, value in result.items() if key != "diagnostic"} | {"node_id": node_id})
        repair_round = 0
        while result["verdict"] != "pass" and repair_round < 3:
            repair_round += 1
            repair = parse_json(planner_call(
                repair_prompt(payload, plan, node_id, proofs[node_id], result["diagnostic"]),
                planner_dir / f"repair-{index}-{repair_round}", model=model, timeout=timeout,
            ))
            if repair.get("action") == "proof":
                candidate = repair.get("proof", "").strip()
                if FORBIDDEN_RE.search(candidate):
                    raise ValueError("repair contains forbidden command")
                proofs[node_id] = candidate
            elif repair.get("action") == "local_helper":
                name = repair.get("name", "")
                if not name.startswith("regen_"):
                    raise ValueError("repair helper name must begin regen_")
                helper_id = f"support:{THEORY}:{name}"
                helper = {
                    "id": helper_id,
                    "name": name,
                    "statement": repair["statement"],
                    "dependencies": [],
                    "consumers": [node_id],
                    "rationale": repair.get("residual_explanation", "residual-derived leaf"),
                    "origin": "isabelle_residual",
                }
                plan["support_nodes"].append(helper)
                plan_node = next(item for item in plan["contract_nodes"] if item["id"] == node_id)
                plan_node.setdefault("dependencies", []).append(helper_id)
                proofs[helper_id] = repair["proof"].strip()
                proofs[node_id] = repair["contract_proof"].strip()
            else:
                raise ValueError("unknown repair action")
            result = check_source(
                campaign_dir, render_theory(payload, plan, proofs, index),
                f"Bits_R_prefix_{index}_repair_{repair_round}",
            )
            attempts.append({key: value for key, value in result.items() if key != "diagnostic"} | {
                "node_id": node_id, "repair_round": repair_round,
            })
        if result["verdict"] != "pass":
            write_json(planner_dir / "attempts.json", attempts)
            raise SystemExit(f"failed to prove {node_id} after bounded repair")
    write_json(planner_dir / "attempts.json", attempts)
    write_json(planner_dir / "lemma-plan.final.json", plan)
    write_json(planner_dir / "proofs.final.json", proofs)
    return plan, proofs, attempts


def seal(campaign_dir: Path, payload: dict, plan: dict, proofs: dict[str, str], attempts: list[dict]) -> dict:
    generated = campaign_dir / GENERATED_DIR
    generated.mkdir(parents=True, exist_ok=True)
    theory_path = generated / "Bits_R.thy"
    theory_path.write_text(render_theory(payload, plan, proofs), encoding="utf-8")
    (generated / "ROOT").write_text("\n".join([
        f'session {SESSION} in "." = {PARENT_SESSION} +',
        "  theories",
        "    Bits_R",
        "",
    ]), encoding="utf-8")
    final_check = check_source(campaign_dir, theory_path.read_text(encoding="utf-8"), "Bits_R_final")
    if final_check["verdict"] != "pass":
        raise SystemExit("final Bits_R theory check failed")

    generated_ctr = "/workspace/" + str(generated.relative_to(REPO))
    seal_dir = campaign_dir / "seal-layer5-bits"
    seal_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    build = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "build",
        *dependency_args(campaign_dir), "-d", generated_ctr,
        "-d", "/sel4-project/verification/l4v", "-b", SESSION,
    ])
    build_log = seal_dir / "build.log"
    build_log.write_text(build.stdout + build.stderr, encoding="utf-8")
    if build.returncode:
        raise SystemExit(f"Bits_R heap build failed; see {build_log}")
    getenv = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "getenv", "-b", "ISABELLE_HEAPS",
    ])
    heap_path = f"{getenv.stdout.strip()}/polyml-5.9.1_x86_64_32-linux/{SESSION}"
    identity = container_run(["bash", "-lc", f"sha256sum '{heap_path}'; stat -c %s '{heap_path}'"])
    identity_lines = identity.stdout.splitlines()
    if identity.returncode or len(identity_lines) < 2:
        raise SystemExit("could not identify Bits_R heap")

    smoke_dir = seal_dir / "smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    smoke_path = smoke_dir / "Bits_R_Smoke.thy"
    smoke_path.write_text("\n".join([
        "theory Bits_R_Smoke",
        f"imports \"{SESSION}.Bits_R\"",
        "begin",
        "thm getCurThread_corres getIdleThread_corres",
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
    downstream_smoke = {
        "verdict": "pass" if smoke.returncode == 0 else "fail",
        "returncode": smoke.returncode,
        "wall_ms": int((time.time() - smoke_started) * 1000),
        "source": str(smoke_path.relative_to(REPO)),
        "log": str(smoke_log.relative_to(REPO)),
    }
    if smoke.returncode:
        raise SystemExit(f"Bits_R child-import smoke failed; see {smoke_log}")


    fixed_contracts = {item["id"]: item for item in payload["fixed_contracts"]}
    exports_path = campaign_dir / "exports" / "Bits_R.json"
    write_json(exports_path, {
        "schema_version": 1,
        "theory": THEORY,
        "interface_kind": "proof_complete_contract_theory",
        "contracts": [
            {
                "id": node_id,
                "name": fixed_contracts[node_id]["name"],
                "statement_hash": fixed_contracts[node_id]["statement_hash"],
                "status": "proof_green",
            }
            for node_id in plan["contract_order"]
        ],
        "support_exports": [item["id"] for item in plan["support_nodes"]],
        "source_sha256": sha256_path(theory_path),
    })
    nodes = [
        {
            "id": item["id"], "role": "support", "name": item["name"],
            "statement": item["statement"], "dependencies": item.get("dependencies", []),
            "consumers": item.get("consumers", []), "status": "proof_green",
            "origin": item.get("origin", "initial_plan"),
        }
        for item in plan["support_nodes"]
    ] + [
        {
            "id": item["id"], "role": "contract", "name": fixed_contracts[item["id"]]["name"],
            "statement": fixed_contracts[item["id"]]["statement"],
            "statement_hash": fixed_contracts[item["id"]]["statement_hash"],
            "dependencies": item.get("dependencies", []), "status": "proof_green",
        }
        for item in plan["contract_nodes"]
    ]
    write_json(campaign_dir / "lemma_graph-Bits_R.json", {
        "schema_version": 1,
        "theory": THEORY,
        "status": "proof_complete_seal",
        "contract_roots": plan["contract_order"],
        "nodes": nodes,
        "dynamic_growth_policy": "new support nodes only below a red frontier",
        "export_interface": str(exports_path.relative_to(REPO)),
        "seal_session": SESSION,
        "source_sha256": sha256_path(theory_path),
    })
    if AUDIT_TRAIL is None:
        raise RuntimeError("theory audit trail was not initialized")
    AUDIT_TRAIL.record_harness("assembled-interface", artifacts=[
        theory_path, generated / "ROOT", exports_path,
        campaign_dir / "lemma_graph-Bits_R.json",
    ])
    AUDIT_TRAIL.record_harness("heap-build", artifacts=[build_log], details={
        "session": SESSION, "returncode": build.returncode,
        "heap_sha256": identity_lines[0].split()[0],
    })
    AUDIT_TRAIL.record_harness("downstream-smoke", artifacts=[
        smoke_path, smoke_log,
    ], details=downstream_smoke)
    audit_manifest = AUDIT_TRAIL.seal(
        theory_path, required_llm_stages={"01-lemma-dag", "02-proof-generation"},
    )
    transcript_files = sorted(
        str(path.relative_to(REPO)) for path in (AUDIT_TRAIL.run_dir / "llm").glob("*")
        if path.is_file()
    )
    manifest = {
        "schema_version": 1,
        "status": "proof_complete_seal",
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "session": SESSION,
        "parent_session": PARENT_SESSION,
        "theory": THEORY,
        "generation_audit": {
            "generation_id": audit_manifest["generation_id"],
            "manifest": str(AUDIT_TRAIL.manifest_path.relative_to(REPO)),
            "events_sha256": audit_manifest["events_sha256"],
        },
        "source": str(theory_path.relative_to(REPO)),
        "source_sha256": sha256_path(theory_path),
        "fixed_contract_count": len(payload["fixed_contracts"]),
        "proof_green_contract_count": len(plan["contract_order"]),
        "generated_support_count": len(plan["support_nodes"]),
        "final_theory_check": {key: value for key, value in final_check.items() if key != "diagnostic"},
        "attempts": attempts,
        "build_returncode": build.returncode,
        "build_wall_ms": int((time.time() - started) * 1000),
        "build_log": str(build_log.relative_to(REPO)),
        "downstream_smoke": downstream_smoke,
        "heap": {
            "container_path": heap_path,
            "sha256": identity_lines[0].split()[0],
            "size_bytes": int(identity_lines[1]),
        },
        "human_refine_heap_loaded": False,
        "human_refine_imports": [],
        "planner_transcripts": transcript_files,
        "export_interface": str(exports_path.relative_to(REPO)),
    }
    write_json(seal_dir / "manifest.json", manifest)
    state = read_json(campaign_dir / "state.json")
    theories = list(state["theories"])
    if "Bits_R" not in theories:
        theories.append("Bits_R")
    write_json(campaign_dir / "state.json", {
        "schema_version": 1,
        "layer": 5,
        "status": "proof_complete_seal",
        "session": SESSION,
        "theories": theories,
        "next_ready_theories": ["Refine.EmptyFail", "Refine.Machine_R"],
        "wave_status": "complete",
        "first_contract_milestone": {
            "theory": THEORY,
            "contracts_green": len(plan["contract_order"]),
            "contracts_total": len(payload["fixed_contracts"]),
        },
    })
    return manifest


def run(campaign_dir: Path, model: str, timeout: int) -> dict:
    payload = relevant_context(campaign_dir)
    preflight = check_preflight(campaign_dir, payload)
    if AUDIT_TRAIL is None:
        raise RuntimeError("theory audit trail was not initialized")
    preflight_dir = campaign_dir / "preflight" / "Bits_R"
    AUDIT_TRAIL.record_harness("preflight", artifacts=[
        preflight_dir / "planner-input.json", preflight_dir / "report.json",
        preflight_dir / "Bits_R_Preflight.thy", preflight_dir / "context-check.log",
    ], details={"verdict": preflight["verdict"]})
    plan, proofs, attempts = plan_and_prove(campaign_dir, payload, model, timeout)
    manifest = seal(campaign_dir, payload, plan, proofs, attempts)
    return {"preflight": preflight, "plan": plan, "manifest": manifest}


def main() -> None:
    global AUDIT_TRAIL, PROVIDER, REPLAY_DIR
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    parser.add_argument("--provider", choices=("codex", "claude"), default="codex")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--allow-external-payload", action="store_true")
    parser.add_argument("--replay-dir", type=Path)
    parser.add_argument("--generation-id", required=True)
    args = parser.parse_args()
    campaign_dir = args.campaign_dir.resolve()
    PROVIDER = args.provider
    REPLAY_DIR = args.replay_dir.resolve() if args.replay_dir else None
    if REPLAY_DIR is None and not args.allow_external_payload:
        raise SystemExit("external planner/prover calls require --allow-external-payload")
    AUDIT_TRAIL = TheoryAuditTrail(
        campaign_dir, THEORY, generation_id=args.generation_id,
    )
    AUDIT_TRAIL.record_harness("generation-start", details={
        "provider": PROVIDER, "model": args.model, "replay": REPLAY_DIR is not None,
    })
    print(json.dumps(run(campaign_dir, args.model, args.timeout), indent=2))

if __name__ == "__main__":
    main()
