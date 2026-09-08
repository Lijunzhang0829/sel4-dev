#!/usr/bin/env python3
"""Plan, generate, verify, and seal the Refine ArchAcc_R contract theory."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from audited_llm import sha256_text
from llm_transcript import call_llm, record_replay
from retrieval_utils import bm25
from theory_trace import TheoryAuditTrail
from theory_preflight import generated_premises


REPO = Path(__file__).resolve().parent.parent
DEFAULT_CAMPAIGN = REPO / "reports" / "regen" / "refine-arm-session"
CONTAINER = os.environ.get("REGEN_CONTAINER", "sel4-l4v")
THEORY = "Refine.ArchAcc_R"
PARENT_SESSION = "RegenRefineARM_L8"
SESSION = "RegenRefineARM_L9"
GENERATED_DIR = "generated-layer9-archacc"
DEPENDENCY_DIRS = [
    "generated", "generated-layer1", "generated-layer2-invariant",
    "generated-layer2-invocations", "generated-layer3-state-relation",
    "generated-layer4-corres", "generated-layer5-bits",
    "generated-layer6-support-wave", "generated-layer7-kheap",
    "generated-layer8-wave",
]
CONTEXT_LIMIT = 120_000
REPAIR_DIAGNOSTIC_LIMIT = 20_000
CONTRACT_REPAIR_LIMIT = 8
RUNTIME_PHASE_PROTOCOL_VERSION = 2
FORBIDDEN_RE = re.compile(r"\b(sorry|oops|axiomatization|admit)\b")
JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.S)
CONTRACT_LOCAL_FRAMEWORK = [
    {
        "id": "contract-framework:Refine.ArchAcc_R:requalify_ARM_H_ASIDPool",
        "theory": THEORY,
        "name": "ARM_H.ASIDPool",
        "kind": "requalify_consts",
        "statement": "requalify_consts ARM_H.ASIDPool",
        "ownership": "fixed_contract_parser_closure",
        "origin": "isabelle_parser_residual",
    },
    {
        "id": "contract-framework:Refine.ArchAcc_R:requalify_Structures_H_ArchObjectCap",
        "theory": THEORY,
        "name": "Structures_H.ArchObjectCap",
        "kind": "requalify_consts",
        "statement": "requalify_consts Structures_H.ArchObjectCap",
        "ownership": "fixed_contract_parser_closure",
        "origin": "isabelle_parser_residual",
    },
]


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
def valid_proof_body(proof: str) -> bool:
    proof = proof.strip()
    if not proof or FORBIDDEN_RE.search(proof):
        return False
    if re.match(r"^by(?:\s|$)", proof):
        return True
    if proof.startswith("apply"):
        return re.search(r"\bdone\s*$", proof) is not None
    if re.match(r"^proof(?:\s|$)", proof):
        return re.search(r"\bqed\s*$", proof) is not None
    if proof.startswith("using"):
        return (
            re.search(r"(?:^|\n)\s*by(?:\s|\()", proof) is not None
            or re.search(r"\bdone\s*$", proof) is not None
        )
    return False


def proof_outer_command(proof: str) -> str:
    return next(
        (line.strip() for line in proof.splitlines() if line.strip()),
        "",
    )


def preserves_outer_proof_prefix(candidate: str, frontier_proof: str) -> bool:
    expected = proof_outer_command(frontier_proof)
    return not expected or proof_outer_command(candidate) == expected


def machine_verified_proof_prefix(frontier: dict) -> str:
    progress = frontier.get("failure_progress")
    proof = frontier.get("proof", "").strip()
    if not isinstance(progress, int) or progress <= 1 or not proof:
        return ""
    lines = proof.splitlines()
    return "\n".join(lines[:min(progress - 1, len(lines))])


def container_run(
    arguments: list[str], timeout: int | None = None,
) -> subprocess.CompletedProcess:
    command = arguments
    if timeout is not None:
        command = [
            "timeout", "--signal=TERM", "--kill-after=15s", f"{timeout}s",
            *arguments,
        ]
    process = subprocess.run(
        [
            "docker", "exec", "-e", "L4V_ARCH=ARM", "-e",
            "L4V_DIR=/sel4-project/verification/l4v", CONTAINER, *command,
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        timeout=timeout + 30 if timeout is not None else None,
    )
    if timeout is not None and process.returncode == 124:
        target = arguments[-1]
        for signal in ("-TERM", "-KILL"):
            subprocess.run(
                ["docker", "exec", CONTAINER, "pkill", signal, "-f", target],
                cwd=REPO,
                text=True,
                capture_output=True,
                timeout=15,
            )
            if signal == "-TERM":
                time.sleep(0.2)
    return process



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


def normalized_proposition(statement: str) -> str:
    return re.sub(r"\s+", "", contract_body(statement))


def runtime_leaf_revision_changes_statement(
        current_statement: str, candidate_statement: str) -> bool:
    return normalized_proposition(current_statement) != normalized_proposition(
        candidate_statement,
    )


REPLAY_DIR: Path | None = None
PROVIDER = "codex"
FRESH_PROOFS = False
HELPER_BUNDLE_CONTRACT: str | None = None
HELPER_BUNDLE_SOURCE_GENERATION: str | None = None
HELPER_BUNDLE_ONLY = False
AUDIT_TRAIL: TheoryAuditTrail | None = None

HELPER_BUNDLE_V2_OBLIGATIONS = {
    "domain_preservation",
    "changed_point_relation",
    "same_owner_unchanged_region",
    "foreign_owner_non_interference",
    "pspace_relation_composition",
}
HELPER_BUNDLE_V2_ORIGINS = {
    "parent_abstract_guard",
    "parent_concrete_guard",
    "state_relation_projection",
    "operation_semantics",
    "accessible_premise",
    "earlier_bundle_helper",
}


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
    if len(contracts) != 24:
        raise SystemExit(f"expected 24 fixed ArchAcc_R contracts, found {len(contracts)}")

    visibility = read_json(campaign_dir / "visibility.json")[THEORY]
    allowed = set(visibility["frozen_external_ancestors"])
    catalog = read_json(campaign_dir / "premise_catalog.json")
    records_hash = sha256_text(json.dumps(catalog["records"], sort_keys=True))
    def normalize_candidates(item: dict) -> dict:
        candidates = set(item.get("candidate_names", [item["name"]]))
        if item.get("kind") == "definition":
            candidates.add(f"{item['name']}_def")
        if item.get("kind") == "abbreviation":
            candidates = {name for name in candidates if not name.endswith("_def")}
        return item | {"candidate_names": sorted(candidates)}

    accessible = [
        normalize_candidates(item)
        for item in catalog["records"] if item["theory"] in allowed
    ]
    malformed = [
        item["id"] for item in accessible
        if item["session"] == "Refine" or item["theory"] not in allowed
        or item["statement_hash"] != sha256_text(" ".join(item["statement"].split()))
    ]
    if malformed or records_hash != catalog["records_sha256"]:
        raise SystemExit("premise catalog health check failed")

    query = " ".join([
        "corres corres_underlying corres_no_fail pspace_aligned pspace_distinct",
        "getObject setObject get_pde get_pte get_master_pde get_master_pte",
        "set_pd set_pt storePDE storePTE lookupPTSlot findPDForASID",
        "asid_pool pde_relation pte_relation mapping_map valid_vspace_objs",
        *(item["name"] for item in contracts),
    ])
    required_names = {
        "corres_no_failI", "corres_guard_imp", "stronger_corres_guard_imp",
        "corres_symb_exec_l",
        "getObject_def", "setObject_def", "get_pde_def", "get_pte_def",
        "get_master_pde_def", "get_master_pte_def", "storePDE_def",
        "get_master_pde_wp",
        "storePTE_def", "lookupPTSlot_def", "findPDForASID_def",
        "get_asid_pool_def", "get_object_def", "obj_at_def", "typ_at",
        "get_pd_def", "pspace_dom_def", "and_not_mask_twice",
        "is_aligned_neg_mask", "is_aligned_mask", "mask_def", "word_size",
        "projectKO_opts_defs", "pdeBits_def", "pteBits_def",
        "mask_pd_bits_inner_beauty",
        "mask_pt_bits_inner_beauty", "pspace_alignedD",
        "mask_alignment_ugliness", "more_pd_inner_beauty",
        "test_bit_size", "pd_bits_def", "pageBits_def",
    }
    exact = exact_required_records(accessible, required_names)
    retrieved = bm25(accessible, query, 56)
    premise_by_id = {item["id"]: item for item in exact + retrieved}
    premises = [premise_by_id[key] for key in sorted(premise_by_id)]
    generated_all = [
        normalize_candidates(item)
        for item in generated_premises(
            campaign_dir, set(visibility["generated_target_ancestors"]),
        )
    ]
    generated_required_names = {
        "regen_obj_at'_def", "regen_projectKOs", "regen_objBits_simps'",
        "regen_objBits_defs", "regen_objBits_simps",
        "regen_lookupAround2_known1", "regen_ps_clear_lookupAround2",
        "regen_in_alignCheck", "regen_in_magnitude_check",
        "regen_map_to_ctes_upd_other", "regen_no_fail_setObject_other",
        "regen_no_fail_loadObject_default", "regen_no_fail_getEndpoint",
        "regen_projectKO_def2",
        "regen_obj_relation_cuts_def2",
        "pde_relation_aligned", "pte_relation_aligned",
        "pde_align'", "pte_align'",
        "typ_at'", "typ_at'_def", "ko_wp_at'", "ko_wp_at'_def",
        "regen_pspace_dom_update", "asid_pool_at'",
        "vs_valid_duplicates'", "vs_ptr_align",
    }
    generated_exact = [
        item for item in generated_all
        if generated_required_names & set(item["candidate_names"])
    ]
    generated_ranked = bm25(generated_all, query + " " + " ".join(generated_required_names), 40)
    generated_by_id = {item["id"]: item for item in generated_exact + generated_ranked}
    generated = [generated_by_id[key] for key in sorted(generated_by_id)]

    inventory = read_json(campaign_dir / "framework" / "inventory.json")
    framework = [
        item for item in inventory["declarations"]
        if item["id"] in {
            "framework:Refine.StateRelation:state_relation",
            "framework:Refine.Corres:corres",
        }
    ]
    framework.extend(CONTRACT_LOCAL_FRAMEWORK)
    session_visible_fact_names = sorted({
        name
        for item in [
            *accessible, *generated_all,
            *(normalize_candidates(item) for item in framework),
        ]
        for name in set(item.get("candidate_names", [])) | {item["name"]}
    })
    manifests = [
        read_json(campaign_dir / "seal-layer3-state-relation" / "manifest.json"),
        read_json(campaign_dir / "seal-layer4-corres" / "manifest.json"),
    ]
    if any(item["human_refine_heap_loaded"] for item in manifests):
        raise SystemExit("generated ancestor manifest reports human Refine heap use")
    hints_path = campaign_dir / "residual-hints" / "ArchAcc_R.json"
    residual_hints = read_json(hints_path) if hints_path.is_file() else {
        "schema_version": 1, "theory": THEORY, "hints": [],
    }
    if residual_hints.get("theory") != THEORY or any(
        hint.get("human_target_proof_used") for hint in residual_hints.get("hints", [])
    ):
        raise SystemExit("residual hint trust check failed")
    for hint in residual_hints.get("hints", []):
        source = REPO / hint.get("probe_source", "")
        log = REPO / hint.get("probe_log", "")
        if (
            hint.get("probe_verdict") != "pass"
            or not source.is_file()
            or not log.is_file()
            or sha256_path(source) != hint.get("probe_source_sha256")
            or sha256_path(log) != hint.get("probe_log_sha256")
            or hint.get("proof", "") not in source.read_text(encoding="utf-8")
            or "***" in log.read_text(encoding="utf-8")
        ):
            raise SystemExit("residual hint source/log/proof binding failed")
    history_path = campaign_dir / "residual-history" / "ArchAcc_R.json"
    residual_history = read_json(history_path)
    if residual_history.get("theory") != THEORY or any(
        item.get("human_target_proof_used")
        for item in residual_history.get("residuals", [])
    ):
        raise SystemExit("residual history trust check failed")
    for item in residual_history.get("residuals", []):
        log = REPO / item["isabelle_log"]
        response = REPO / item["failed_llm_response"]
        if (
            not log.is_file()
            or not response.is_file()
            or sha256_path(log) != item["isabelle_log_sha256"]
            or sha256_path(response) != item["failed_llm_response_sha256"]
            or "***" not in log.read_text(encoding="utf-8")
        ):
            raise SystemExit("residual history artifact binding failed")
        probe = item.get("resolution_probe")
        if probe:
            probe_source = REPO / probe.get("source", "")
            probe_log = REPO / probe.get("log", "")
            if (
                probe.get("verdict") != "pass"
                or not probe_source.is_file()
                or not probe_log.is_file()
                or sha256_path(probe_source) != probe.get("source_sha256")
                or sha256_path(probe_log) != probe.get("log_sha256")
                or item.get("verified_proof_skeleton", "") not in probe_source.read_text(encoding="utf-8")
                or (
                    item.get("verified_contract_proof_skeleton")
                    and item["verified_contract_proof_skeleton"] not in probe_source.read_text(encoding="utf-8")
                )
                or "***" in probe_log.read_text(encoding="utf-8")
            ):
                raise SystemExit("residual resolution probe binding failed")
    payload_residual_history = []
    for item in residual_history.get("residuals", []):
        projected_item = dict(item)
        projected_item.pop("isabelle_log", None)
        projected_item.pop("failed_llm_response", None)
        projected_item.pop("resolution_probe", None)
        payload_residual_history.append(projected_item)
    wave = read_json(campaign_dir / "seal-layer8-wave" / "manifest.json")
    if wave["human_refine_heap_loaded"] or wave["session"] != PARENT_SESSION:
        raise SystemExit("generated L8 parent heap identity is not healthy")
    return {
        "schema_version": 1,
        "target_theory": THEORY,
        "target_role": "architecture-access correspondence milestone with 24 fixed roots",
        "fixed_contracts": contracts,
        "fixed_framework_declarations": framework,
        "_session_visible_fact_names": session_visible_fact_names,
        "retrieved_legal_premises": premises,
        "retrieved_legal_generated_premises": generated,
        "isabelle_residual_history": payload_residual_history,
        "isabelle_verified_residual_hints": residual_hints["hints"],
        "generated_parent_heap": {
            "session": wave["session"],
            "sha256": wave["heap"]["sha256"],
            "size_bytes": wave["heap"]["size_bytes"],
            "human_refine_heap_loaded": wave["human_refine_heap_loaded"],
        },
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
            "initial_helpers_must_be_owned_by_ArchAcc_R": True,
            "upstream_helper_promotion_requires_concrete_Isabelle_residual": True,
            "new_repair_helpers_are_leaf_nodes": True,
        },
        "catalog_health": {
            "records_sha256": records_hash,
            "session_visible_fact_names_sha256": sha256_text(
                json.dumps(session_visible_fact_names, separators=(",", ":")),
            ),
            "accessible_records": len(accessible),
            "records_sent": len(premises),
            "target_session_leaks": sum(item["session"] == "Refine" for item in accessible),
            "source_classes": dict(Counter(item["source_class"] for item in accessible)),
        },
    }


def check_preflight(campaign_dir: Path, payload: dict) -> dict:
    out_dir = campaign_dir / "preflight" / "ArchAcc_R"
    out_dir.mkdir(parents=True, exist_ok=True)
    shell_path = out_dir / "ArchAcc_R_Preflight.thy"
    shell_path.write_text("\n".join([
        "theory ArchAcc_R_Preflight",
        "imports \"RegenRefineARM_L8.SubMonad_R\"",
        "begin",
        "context begin interpretation Arch .",
        *(item["statement"] for item in CONTRACT_LOCAL_FRAMEWORK),
        *(item["statement"].rstrip() + "\n  oops" for item in payload["fixed_contracts"]),
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
    ], timeout=600)
    log_path = out_dir / "context-check.log"
    log_path.write_text(check.stdout + check.stderr, encoding="utf-8")
    payload_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    projected_payload_text = json.dumps(
        proof_payload(payload), ensure_ascii=False, separators=(",", ":"),
    )
    planner_chars = len(family_analysis_prompt(payload))
    context_chars = max(planner_chars, len(projected_payload_text))
    framework_inventory = {
        "schema_version": 1,
        "theory": THEORY,
        "declarations": [
            item | {"statement_hash": sha256_text(" ".join(item["statement"].split()))}
            for item in CONTRACT_LOCAL_FRAMEWORK
        ],
        "human_target_source_used": False,
        "derivation": "minimal Isabelle parser residual",
    }
    framework_path = campaign_dir / "contract-local-semantics" / "ArchAcc_R.json"
    write_json(framework_path, framework_inventory)
    report = {
        "schema_version": 1,
        "theory": THEORY,
        "verdict": "pass" if check.returncode == 0 and context_chars <= CONTEXT_LIMIT else "fail",
        "minimal_context": {
            "verdict": "pass" if check.returncode == 0 else "fail",
            "returncode": check.returncode,
            "wall_ms": int((time.time() - started) * 1000),
            "shell": str(shell_path.relative_to(REPO)),
            "log": str(log_path.relative_to(REPO)),
        },
        "context_budget": {
            "verdict": "pass" if context_chars <= CONTEXT_LIMIT else "fail",
            "actual_chars": context_chars,
            "planner_prompt_chars": planner_chars,
            "dag_planning_payload_chars": len(json.dumps(
                dag_planning_payload(payload), ensure_ascii=False,
                separators=(",", ":"),
            )),
            "proof_projection_chars": len(projected_payload_text),
            "full_audit_payload_chars": len(payload_text),
            "max_chars": CONTEXT_LIMIT,
        },
        "catalog_health": payload["catalog_health"],
        "target_source_read_by_context_builder": False,
        "contract_local_framework": str(framework_path.relative_to(REPO)),
        "planner_input_sha256": sha256_text(payload_text),
    }
    write_json(out_dir / "planner-input.json", payload)
    write_json(out_dir / "report.json", report)
    if report["verdict"] != "pass":
        raise SystemExit(f"ArchAcc_R preflight failed; see {out_dir}")
    return report


def dag_planning_payload(payload: dict) -> dict:
    projected = proof_payload(payload)
    projected["isabelle_verified_residual_hints"] = [
        {key: item[key] for key in ("contract_id", "probe_verdict", "origin") if key in item}
        for item in payload["isabelle_verified_residual_hints"]
    ]
    projected["isabelle_residual_history"] = [
        {key: item[key] for key in (
            "contract_id", "classification", "failed_strategy",
            "remaining_obligations", "resolution_status",
            "required_support_statement", "verified_strategy",
        ) if key in item}
        for item in payload["isabelle_residual_history"]
    ]
    return projected


def family_analysis_prompt(payload: dict) -> str:
    return """Analyze the complete Refine.ArchAcc_R contract inventory before building a lemma DAG.
Use only the supplied fixed statements, framework declarations, legal premise records, and
verified residual summaries. Do not use tools, files, external knowledge, remembered human
Refine proofs, or proof text.

Partition all 24 contracts into semantic operation families. For every family, identify the
predictable proof-obligation dimensions that should become exact preplanned helpers when they
are not already legal premises: concrete no-fail, type projection, result relation, guard
lifting, relation-cut membership, address/slot normalization, alignment, duplicate-entry
consistency, monad reduction, update relation, and invariant preservation. Distinguish a
predictable helper interface from an uncertain proof tactic. Account for every contract once.

Return only JSON:
{
  "schema_version": 2,
  "theory": "Refine.ArchAcc_R",
  "families": [{
    "id": "family:...",
    "name": "...",
    "contracts": ["contract:..."],
    "obligation_dimensions": ["..."],
    "shared_symbols": ["..."],
    "available_interfaces": ["legal premise/framework IDs"],
    "helper_requirements": [{"dimension": "...", "consumers": ["contract:..."], "reason": "..."}],
    "rationale": "..."
  }],
  "cross_family_dependencies": [{"before": "family:...", "after": "family:...", "reason": "..."}],
  "uncertainties": ["..."]
}

PAYLOAD:
""" + json.dumps(dag_planning_payload(payload), ensure_ascii=False, separators=(",", ":"))


def planner_prompt(payload: dict, family_analysis: dict) -> str:
    return """Build the frozen statement-level lemma DAG for Refine.ArchAcc_R. The supplied
family analysis is an audited planning stage, not optional commentary. Use only this input. Do
not use tools, files, external knowledge, remembered human Refine helper names, or proof text.

Every fixed contract is an immutable root. Preplan exact helpers for predictable obligation
dimensions instead of deferring all decomposition to Isabelle residuals. Residual-provided
required_support_statement values are authoritative and must be preserved exactly. Initial
helpers may depend on earlier initial helpers, but support_order must be acyclic and every edge
must be explicit. All helpers are owned by Refine.ArchAcc_R, begin regen_, have exact Isabelle
statements, list consumers, family, obligation kind, ownership, and rationale. Legal premises
and framework declarations are terminal leaves. Do not include proof bodies.

Return only JSON:
{
  "schema_version": 2,
  "theory": "Refine.ArchAcc_R",
  "families": [{"id": "family:...", "contracts": ["contract:..."], "obligation_dimensions": ["..."], "rationale": "..."}],
  "contract_order": ["contract:..."],
  "support_order": ["support:Refine.ArchAcc_R:regen_name"],
  "support_nodes": [{
    "id": "support:Refine.ArchAcc_R:regen_name",
    "name": "regen_name",
    "statement": "lemma regen_name: ...",
    "ownership": "Refine.ArchAcc_R",
    "family": "family:...",
    "obligation_kind": "...",
    "dependencies": ["legal premise/framework or earlier support IDs"],
    "consumers": ["contract:..."],
    "rationale": "..."
  }],
  "contract_nodes": [{"id": "contract:...", "family": "family:...", "dependencies": ["support, legal premise, or earlier contract IDs"], "strategy": "..."}],
  "runtime_leaf_policy": {"max_new_leaves_per_contract": 1, "recursive_expansion": false, "statement_mutation": false},
  "uncertainties": ["..."]
}

FAMILY ANALYSIS:
""" + json.dumps(family_analysis, ensure_ascii=False, separators=(",", ":")) + "\n\nPAYLOAD:\n" + json.dumps(dag_planning_payload(payload), ensure_ascii=False, separators=(",", ":"))


def family_review_payload(analysis: dict) -> dict:
    return {
        "schema_version": analysis["schema_version"],
        "theory": analysis["theory"],
        "families": [
            {key: family[key] for key in (
                "id", "contracts", "obligation_dimensions", "helper_requirements",
            )}
            for family in analysis["families"]
        ],
        "cross_family_dependencies": analysis.get("cross_family_dependencies", []),
        "uncertainties": analysis.get("uncertainties", []),
    }


def plan_review_payload(payload: dict, draft: dict) -> dict:
    projected = dag_planning_payload(payload)
    projected["isabelle_residual_history"] = [
        {key: item[key] for key in (
            "contract_id", "remaining_obligations", "resolution_status",
            "required_support_statement",
        ) if key in item}
        for item in projected["isabelle_residual_history"]
    ]
    referenced = {
        dependency
        for node in draft.get("support_nodes", []) + draft.get("contract_nodes", [])
        for dependency in node.get("dependencies", [])
    }
    projected["retrieved_legal_premises"] = [
        item for item in projected["retrieved_legal_premises"] if item["id"] in referenced
    ]
    projected["retrieved_legal_generated_premises"] = [
        item for item in projected["retrieved_legal_generated_premises"] if item["id"] in referenced
    ]
    return projected


def plan_critique_prompt(payload: dict, family_analysis: dict, draft: dict,
                         validation_error: str | None = None) -> str:
    return (
        "Adversarially review this schema-2 Refine.ArchAcc_R frozen lemma DAG. "
        "Use only the family analysis, compact payload, draft, and validator residual. "
        "Check complete family/contract coverage, exact helper statements and ownership, "
        "support and contract topological order, premise closure, predictable obligation "
        "coverage, bounded runtime leaf policy, and absence of hidden forward edges. "
        "A support proposition must be strictly smaller than its consumer contract and must "
        "not restate any fixed contract proposition under whitespace-only normalization. "
        "Retain exact residual-supported statements. Repair the validator residual and "
        "return the complete revised schema-version-2 JSON only.\n\n"
        "FAMILY ANALYSIS:\n" + json.dumps(family_review_payload(family_analysis), ensure_ascii=False, separators=(",", ":"))
        + "\n\nPAYLOAD:\n" + json.dumps(plan_review_payload(payload, draft), ensure_ascii=False, separators=(",", ":"))
        + "\n\nDRAFT VALIDATOR RESIDUAL:\n" + (validation_error or "draft passed structural validation")
        + "\n\nDRAFT:\n" + json.dumps(draft, ensure_ascii=False, separators=(",", ":"))
    )



def validate_family_analysis(analysis: dict, payload: dict) -> None:
    contract_ids = {item["id"] for item in payload["fixed_contracts"]}
    legal_interfaces = {
        item["id"]
        for key in (
            "fixed_framework_declarations", "retrieved_legal_premises",
            "retrieved_legal_generated_premises",
        )
        for item in payload[key]
    }
    if not (analysis.get("schema_version") == 2 and analysis.get("theory") == THEORY):
        raise ValueError("invalid family-analysis schema or theory")
    families = analysis.get("families", [])
    family_ids = [item.get("id") for item in families]
    if not families or len(family_ids) != len(set(family_ids)) or any(not item for item in family_ids):
        raise ValueError("family analysis needs unique nonempty family IDs")
    flattened = [contract for family in families for contract in family.get("contracts", [])]
    if len(flattened) != len(contract_ids) or set(flattened) != contract_ids:
        raise ValueError("family analysis must cover every contract exactly once")
    for family in families:
        family_contracts = set(family["contracts"])
        if not family.get("obligation_dimensions"):
            raise ValueError(f"family {family['id']} has no obligation dimensions")
        if not set(family.get("available_interfaces", [])) <= legal_interfaces:
            raise ValueError(f"family {family['id']} cites an unavailable interface")
        for requirement in family.get("helper_requirements", []):
            consumers = set(requirement.get("consumers", []))
            if not requirement.get("dimension") or not consumers or not consumers <= family_contracts:
                raise ValueError(f"family {family['id']} has an invalid helper requirement")
    family_id_set = set(family_ids)
    for edge in analysis.get("cross_family_dependencies", []):
        if edge.get("before") not in family_id_set or edge.get("after") not in family_id_set:
            raise ValueError("cross-family dependency references an unknown family")
        if edge["before"] == edge["after"]:
            raise ValueError("cross-family dependency cannot be reflexive")


def statement_plan_repair_prompt(payload: dict, family_analysis: dict, plan: dict,
                                 diagnostic: str) -> str:
    return (
        "Repair only the declaration layer of this schema-2 Refine.ArchAcc_R lemma DAG. "
        "The complete statement shell failed to parse/typecheck in Isabelle. Use only the "
        "family analysis, compact payload, current plan, and exact Isabelle diagnostic. "
        "Return the complete revised schema-version-2 plan JSON only. Preserve all fixed "
        "contracts and every exact residual-required support statement. Keep the graph "
        "acyclic, every helper owned by Refine.ArchAcc_R, every consumer edge explicit, "
        "and the runtime leaf policy unchanged. Do not include proof bodies.\n\n"
        "FAMILY ANALYSIS:\n" + json.dumps(family_review_payload(family_analysis), ensure_ascii=False, separators=(",", ":"))
        + "\n\nPAYLOAD:\n" + json.dumps(plan_review_payload(payload, plan), ensure_ascii=False, separators=(",", ":"))
        + "\n\nCURRENT PLAN:\n" + json.dumps(plan, ensure_ascii=False, separators=(",", ":"))
        + "\n\nISABELLE STATEMENT-SHELL DIAGNOSTIC:\n" + diagnostic[-16000:]
    )


def graph_projection(plan: dict) -> dict:
    return {
        "schema_version": plan["schema_version"],
        "theory": plan["theory"],
        "families": plan["families"],
        "contract_order": plan["contract_order"],
        "support_order": plan["support_order"],
        "support_nodes": plan["support_nodes"],
        "contract_nodes": plan["contract_nodes"],
        "runtime_leaf_policy": plan["runtime_leaf_policy"],
    }


def normalize_dependency_ids(plan: dict, payload: dict) -> list[dict]:
    universe = {
        item["id"]
        for key in (
            "fixed_framework_declarations", "retrieved_legal_premises",
            "retrieved_legal_generated_premises", "fixed_contracts",
        )
        for item in payload[key]
    }
    universe.update(item.get("id", "") for item in plan.get("support_nodes", []))
    changes = []
    for node in plan.get("support_nodes", []) + plan.get("contract_nodes", []):
        normalized = []
        for dependency in node.get("dependencies", []):
            replacement = dependency
            if dependency not in universe and ":" in dependency:
                matches = sorted(item for item in universe if item.startswith(dependency))
                if len(matches) == 1:
                    replacement = matches[0]
                    changes.append({
                        "action": "resolve_unique_prefix",
                        "node_id": node.get("id"),
                        "from": dependency,
                        "to": replacement,
                    })
                elif dependency.startswith(("premise:", "generated-premise:", "framework:")):
                    changes.append({

                        "action": "drop_unresolved_import_annotation",
                        "node_id": node.get("id"),
                        "from": dependency,
                    })
                    continue
            normalized.append(replacement)
        node["dependencies"] = normalized
    contracts = {node.get("id"): node for node in plan.get("contract_nodes", [])}
    for support in plan.get("support_nodes", []):
        for consumer in support.get("consumers", []):
            contract = contracts.get(consumer)
            if contract is not None and support.get("id") not in contract.get("dependencies", []):
                contract.setdefault("dependencies", []).append(support["id"])
                changes.append({
                    "action": "complete_consumer_edge",
                    "support_id": support["id"],
                    "contract_id": consumer,
                })
    return changes


def validate_plan(plan: dict, payload: dict) -> None:
    contract_ids = {item["id"] for item in payload["fixed_contracts"]}
    if not (plan.get("schema_version") == 2 and plan.get("theory") == THEORY):
        raise ValueError("invalid schema-2 plan or theory")
    contract_order = plan.get("contract_order", [])
    contract_nodes = plan.get("contract_nodes", [])
    if not (len(contract_order) == len(contract_ids) and set(contract_order) == contract_ids):
        raise ValueError("plan must schedule every fixed contract exactly once")
    if not (len(contract_nodes) == len(contract_ids) and {item.get("id") for item in contract_nodes} == contract_ids):
        raise ValueError("contract node set differs from fixed inventory")


    families = plan.get("families", [])
    family_ids = [item.get("id") for item in families]
    if not families or not (len(family_ids) == len(set(family_ids))) or any(not item for item in family_ids):
        raise ValueError("families must have unique nonempty IDs")
    family_contracts = [contract for family in families for contract in family.get("contracts", [])]
    if not (len(family_contracts) == len(contract_ids) and set(family_contracts) == contract_ids):
        raise ValueError("families must cover every contract exactly once")
    if any(not family.get("obligation_dimensions") for family in families):
        raise ValueError("every family needs obligation dimensions")
    contract_family = {contract: family["id"] for family in families for contract in family["contracts"]}
    legal_import_ids = {item["id"] for key in ("fixed_framework_declarations", "retrieved_legal_premises", "retrieved_legal_generated_premises") for item in payload[key]}

    support_nodes = plan.get("support_nodes", [])
    support_ids = [item.get("id") for item in support_nodes]
    support_order = plan.get("support_order", [])
    if not (len(support_ids) == len(set(support_ids)) and len(support_order) == len(support_ids) and set(support_order) == set(support_ids)):
        raise ValueError("support_order must schedule every unique support exactly once")
    support_by_id = {item["id"]: item for item in support_nodes}
    statements = set()

    for node in support_nodes:
        name = node.get("name", "")
        statement = node.get("statement", "")
        if not (name.startswith("regen_") and node["id"] == f"support:{THEORY}:{name}"):
            raise ValueError("support name must begin regen_ and match its ID")
        if not statement.lstrip().startswith(f"lemma {name}") or FORBIDDEN_RE.search(statement):
            raise ValueError("support statement must declare its generated name")
        if statement in statements:
            raise ValueError("duplicate support statement")
        statements.add(statement)
        if not (node.get("ownership") == THEORY and node.get("family") in family_ids and node.get("obligation_kind")):
            raise ValueError("support ownership, family, and obligation kind are required")
        consumers = set(node.get("consumers", []))
        if not consumers or not consumers <= contract_ids:
            raise ValueError("support node has an unknown consumer")
        if any(not (contract_family[consumer] == node["family"]) for consumer in consumers):
            raise ValueError("support consumers must belong to its family")
    fixed_contract_propositions = {
        normalized_proposition(item["statement"]): item["id"]
        for item in payload["fixed_contracts"] if item.get("statement")
    }
    contract_equivalent_supports = [
        {
            "support_id": node["id"],
            "contract_id": fixed_contract_propositions[
                normalized_proposition(node["statement"])
            ],
        }
        for node in support_nodes
        if normalized_proposition(node["statement"]) in fixed_contract_propositions
    ]
    if contract_equivalent_supports:
        raise ValueError(
            "support propositions duplicate fixed contracts: "
            + json.dumps(contract_equivalent_supports, separators=(",", ":"))
        )
    required_statements = {
        item["required_support_statement"]
        for item in payload.get("isabelle_residual_history", [])
        if item.get("required_support_statement")
        and normalized_proposition(item["required_support_statement"])
            not in fixed_contract_propositions
    }
    if not required_statements <= statements:
        raise ValueError("plan dropped an Isabelle-verified required support statement")

    prior_supports = set()
    for node_id in support_order:
        dependencies = set(support_by_id[node_id].get("dependencies", []))
        if not dependencies <= legal_import_ids | prior_supports:
            raise ValueError(f"illegal or forward support dependency for {node_id}")
        prior_supports.add(node_id)

    contract_by_id = {item["id"]: item for item in contract_nodes}
    prior_contracts = set()
    all_supports = set(support_ids)
    for node_id in contract_order:
        node = contract_by_id[node_id]
        if not (node.get("family") == contract_family[node_id]):
            raise ValueError(f"contract family mismatch for {node_id}")
        dependencies = set(node.get("dependencies", []))
        if not dependencies <= legal_import_ids | all_supports | prior_contracts:
            raise ValueError(f"illegal or forward contract dependency for {node_id}")
        prior_contracts.add(node_id)
    missing_consumer_edges = [
        {"support_id": support["id"], "contract_id": consumer}
        for support in support_nodes
        for consumer in support["consumers"]
        if support["id"] not in set(contract_by_id[consumer].get("dependencies", []))
    ]
    if missing_consumer_edges:
        raise ValueError(
            "support consumer edges are not declared: "
            + json.dumps(missing_consumer_edges, separators=(",", ":"))
        )
    policy = plan.get("runtime_leaf_policy", {})
    if not (policy.get("max_new_leaves_per_contract") == 1 and policy.get("recursive_expansion") is False and policy.get("statement_mutation") is False):
        raise ValueError("runtime leaf policy must be bounded, nonrecursive, and immutable")


def proof_payload(payload: dict) -> dict:
    def project(records: list[dict], fields: tuple[str, ...]) -> list[dict]:
        return [
            {field: record[field] for field in fields if field in record}
            for record in records
        ]

    return {
        "schema_version": 1,
        "target_theory": payload["target_theory"],
        "fixed_contracts": project(
            payload["fixed_contracts"], ("id", "name", "statement"),
        ),
        "fixed_framework_declarations": project(
            payload["fixed_framework_declarations"],
            ("id", "name", "kind", "statement"),
        ),
        "retrieved_legal_premises": project(
            payload["retrieved_legal_premises"],
            ("id", "theory", "name", "candidate_names", "kind", "attributes", "statement"),
        ),
        "retrieved_legal_generated_premises": project(
            payload["retrieved_legal_generated_premises"],
            ("id", "theory", "name", "candidate_names", "kind", "statement"),
        ),
        "isabelle_verified_residual_hints": project(
            payload["isabelle_verified_residual_hints"],
            (
                "contract_id", "proof", "probe_verdict", "origin",
                "source_generation_id", "source_llm_sha256",
            ),
        ),
        "isabelle_residual_history": project(
            payload["isabelle_residual_history"],
            (
                "contract_id", "generation_id", "classification",
                "failed_strategy", "remaining_obligations",
                "isabelle_log_sha256", "failed_llm_response_sha256",
                "resolution_status", "required_support_statement",
                "verified_strategy", "verified_proof_skeleton",
                "verified_contract_proof_skeleton", "resolution_probe",
            ),
        ),
        "generated_parent_heap": payload["generated_parent_heap"],
        "policy": payload["policy"],
    }

def scoped_proof_context(payload: dict, plan: dict, target_ids: list[str]) -> tuple[dict, dict]:
    nodes = {node["id"]: node for node in plan["support_nodes"] + plan["contract_nodes"]}
    closure = set(target_ids)
    dependency_ids = set()
    pending = list(target_ids)
    while pending:
        node = nodes[pending.pop()]
        for dependency in node["dependencies"]:
            dependency_ids.add(dependency)
            if dependency in nodes and dependency not in closure:
                closure.add(dependency)
                pending.append(dependency)
    context = proof_payload(payload)
    support_consumers = {
        consumer
        for node_id in target_ids if node_id.startswith("support:")
        for consumer in nodes[node_id].get("consumers", [])
    }
    context["fixed_contracts"] = [
        item for item in context["fixed_contracts"]
        if item["id"] in closure | support_consumers
    ]
    context["retrieved_legal_premises"] = [item for item in context["retrieved_legal_premises"] if item["id"] in dependency_ids]
    context["retrieved_legal_generated_premises"] = [item for item in context["retrieved_legal_generated_premises"] if item["id"] in dependency_ids]
    target_contracts = (
        set(target_ids) & {node["id"] for node in plan["contract_nodes"]}
    ) | support_consumers
    target_support_statements = {nodes[node_id]["statement"] for node_id in target_ids if node_id.startswith("support:")}
    context["isabelle_verified_residual_hints"] = [item for item in context["isabelle_verified_residual_hints"] if item["contract_id"] in target_contracts]
    context["isabelle_residual_history"] = [item for item in context["isabelle_residual_history"] if item["contract_id"] in target_contracts or item.get("required_support_statement") in target_support_statements]
    scoped_plan = {
        "schema_version": plan["schema_version"],
        "theory": plan["theory"],
        "support_nodes": [node for node in plan["support_nodes"] if node["id"] in closure],
        "contract_nodes": [node for node in plan["contract_nodes"] if node["id"] in closure],
        "contract_order": [node_id for node_id in plan["contract_order"] if node_id in closure],
        "support_order": [node_id for node_id in plan["support_order"] if node_id in closure],
        "runtime_leaf_policy": plan["runtime_leaf_policy"],
    }
    return context, scoped_plan


def green_proof_templates(plan: dict, green_proofs: dict[str, str],
                          target_ids: list[str], limit: int = 2) -> list[dict]:
    nodes = {item["id"]: item for item in plan["support_nodes"] + plan["contract_nodes"]}
    target_text = " ".join(nodes[node_id].get("statement", "") for node_id in target_ids)
    target_tokens = set(re.findall(r"[A-Za-z][A-Za-z0-9_']{2,}", target_text))
    ranked = []
    for node_id, proof in green_proofs.items():
        node = nodes.get(node_id)
        if node is None or node_id in target_ids or not valid_proof_body(proof):
            continue
        tokens = set(re.findall(r"[A-Za-z][A-Za-z0-9_']{2,}", node.get("statement", "")))
        score = len(target_tokens & tokens)
        if score:
            ranked.append((score, node_id, node, proof))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [
        {"node_id": node_id, "statement": node.get("statement"),
         "proof": proof, "token_overlap": score, "use": "template_only"}
        for score, node_id, node, proof in ranked[:limit]
    ]


def structural_sibling_proof_templates(
        plan: dict, green_proofs: dict[str, str],
        target_ids: list[str]) -> list[dict]:
    """Project machine-green PDE/PD proofs as strategy evidence for PTE/PT peers."""
    nodes = {
        item["id"]: item
        for item in plan["support_nodes"] + plan["contract_nodes"]
    }

    def sibling_key(node_id: str) -> str:
        name = node_id.rsplit(":", 2)[-2]
        return (
            name.replace("PDE", "PXE").replace("PTE", "PXE")
            .replace("pde", "pxe").replace("pte", "pxe")
            .replace("_PD", "_PX").replace("_PT", "_PX")
            .replace("_pd", "_px").replace("_pt", "_px")
        )

    target_keys = {sibling_key(node_id) for node_id in target_ids}
    result = []
    for node_id, proof in green_proofs.items():
        if (
                node_id in target_ids
                or node_id not in nodes
                or not valid_proof_body(proof)
                or sibling_key(node_id) not in target_keys
        ):
            continue
        result.append({
            "node_id": node_id,
            "proof": proof,
            "use": "verified_structural_strategy_only",
            "citation_policy": (
                "Adapt the proof structure. Do not cite the sibling theorem unless "
                "it is an explicit target dependency."
            ),
        })
    return result


def load_prefix_probe_evidence(run_dir: Path, target_ids: list[str]) -> list[dict]:
    path = run_dir / "prefix-probes" / "evidence.json"
    if not path.is_file():
        return []
    evidence = read_json(path)
    if evidence.get("schema_version") != 1 or evidence.get("theory") != THEORY:
        raise ValueError("invalid proof prefix probe evidence")
    if evidence.get("node_id") in target_ids:
        return [evidence]
    return []



def validate_probe_strategy(proof: str, evidence_items: list[dict]) -> None:
    for evidence in evidence_items:
        prohibited = evidence.get("prohibited_method", "")
        if ":" not in prohibited:
            continue
        _, facts_text = prohibited.split(":", 1)
        dangerous_facts = set(facts_text.split())
        blocks = []
        current = []
        command_prefixes = (
            "apply ", "by ", "using ", "unfolding ", "proof",
            "show ", "have ", "then ", "also ", "finally ",
        )
        for raw_line in proof.splitlines() or [proof]:
            stripped = raw_line.lstrip()
            if current and stripped.startswith(command_prefixes):
                blocks.append(" ".join(" ".join(current).split()))
                current = []
            current.append(raw_line)
        if current:
            blocks.append(" ".join(" ".join(current).split()))

        dangerous_methods = ("simp add:", "clarsimp simp:", "fastforce simp:")
        hotspot_facts = {
            "vs_ptr_align_def", "pte_relation_aligned_def", "pte_align'_def",
        }
        for compact in blocks:
            block_facts = set(
                compact.replace(";", " ").replace(")", " ")
                .replace("(", " ").split()
            )
            if (
                    any(method in compact for method in dangerous_methods)
                    and dangerous_facts <= block_facts
            ):
                raise ValueError(
                    "candidate repeats the prefix-probe-localized broad simp bundle"
                )
            if (
                    hotspot_facts <= block_facts
                    and ("fastforce" in compact or "wpsimp" in compact)
            ):
                raise ValueError(
                    "candidate combines the localized PTE relation hotspot with "
                    "broad wpsimp/fastforce search"
                )



def consecutive_probe_strategy_rejections(
        events: list[dict], node_id: str) -> int:
    count = 0
    for event in reversed(events):
        details = event.get("details", {})
        if (
                event.get("kind") == "harness"
                and event.get("stage") == "best-failed-frontier-promoted"
                and details.get("node_id") == node_id
        ):
            break
        if (
                event.get("kind") == "harness"
                and event.get("stage") in {
                    "probe-strategy-rejection", "repair-action-rejection",
                }
                and details.get("node_id") == node_id
        ):
            count += 1
    return count



def trailing_contract_subgoal_plateau(
        attempts: list[dict], node_id: str, subgoal_count: int | None,
        minimum_failure_progress: int | None = None) -> int:
    if subgoal_count is None:
        return 0
    count = 0
    for attempt in reversed(attempts):
        if (
                attempt.get("contract_id") == node_id
                and attempt.get("role") in {
                    "runtime_leaf", "runtime_leaf_revision",
                }
                and attempt.get("verdict") == "pass"
        ):
            break
        if attempt.get("node_id") != node_id:
            continue
        if (
                attempt.get("verdict") == "fail"
                and attempt.get("subgoal_count") == subgoal_count
        ):
            attempt_progress = attempt.get("failure_progress")
            if (
                    minimum_failure_progress is not None
                    and (
                        not isinstance(attempt_progress, int)
                        or attempt_progress < minimum_failure_progress
                    )
            ):
                continue
            count += 1
            continue
        if attempt.get("verdict") == "pass":
            break
        candidate_count = attempt.get("subgoal_count")
        if candidate_count is None or candidate_count > subgoal_count:
            continue
        break
    return count


def trailing_contract_frontier_regressions(
        attempts: list[dict], node_id: str, subgoal_count: int | None,
        minimum_failure_progress: int | None) -> int:
    if subgoal_count is None or minimum_failure_progress is None:
        return 0
    count = 0
    for attempt in reversed(attempts):
        if (
                attempt.get("contract_id") == node_id
                and attempt.get("role") in {
                    "runtime_leaf", "runtime_leaf_revision",
                }
                and attempt.get("verdict") == "pass"
        ):
            break
        if attempt.get("node_id") != node_id:
            continue
        if attempt.get("verdict") != "fail":
            continue
        if attempt.get("returncode") == 124:
            count += 1
            continue
        candidate_count = attempt.get("subgoal_count")
        progress = attempt.get("failure_progress")
        if (
                isinstance(candidate_count, int)
                and (
                    candidate_count > subgoal_count
                    or (
                        candidate_count == subgoal_count
                        and isinstance(progress, int)
                        and progress < minimum_failure_progress
                    )
                )
        ):
            count += 1
    return count


def runtime_dependency_rejection(
        dependencies: list[str], allowed_ids: set[str],
        contract_dependencies: list[str]) -> str:
    invalid = sorted(set(dependencies) - allowed_ids)
    preferred = sorted(set(contract_dependencies) & allowed_ids)
    return (
        "invalid runtime helper dependency IDs: "
        + json.dumps(invalid, ensure_ascii=False, separators=(",", ":"))
        + ". Use exact IDs, never reconstructed hashes. Exact dependency IDs "
          "already declared for this consumer: "
        + json.dumps(preferred, ensure_ascii=False, separators=(",", ":"))
    )


def exact_required_records(
        records: list[dict], required_names: set[str]
) -> list[dict]:
    selected = {}
    for required_name in sorted(required_names):
        matches = [
            item for item in records
            if required_name in set(
                item.get("candidate_names", [item["name"]])
            )
        ]
        if not matches:
            continue
        item = min(
            matches, key=lambda record: (
                record["name"] != required_name, record["id"],
            ),
        )
        selected[item["id"]] = item
    return [selected[key] for key in sorted(selected)]


def payload_legal_premise_ids(payload: dict) -> set[str]:
    return {
        item["id"]
        for key in (
            "retrieved_legal_premises",
            "retrieved_legal_generated_premises",
        )
        for item in payload.get(key, [])
    }



def runtime_residual_premises(
        campaign_dir: Path, payload: dict, query: str, k: int = 16) -> list[dict]:
    catalog = read_json(campaign_dir / "premise_catalog.json")
    if sha256_text(json.dumps(catalog["records"], sort_keys=True)) != catalog["records_sha256"]:
        raise ValueError("runtime premise retrieval found an unhealthy catalog")
    allowed = set(
        read_json(campaign_dir / "visibility.json")[THEORY][
            "frozen_external_ancestors"
        ]
    )
    accessible = []
    for raw in catalog["records"]:
        if raw["theory"] not in allowed:
            continue
        if (
                raw["session"] == "Refine"
                or raw["statement_hash"]
                != sha256_text(" ".join(raw["statement"].split()))
        ):
            raise ValueError("runtime premise retrieval found an illegal record")
        candidates = set(raw.get("candidate_names", [raw["name"]]))
        if raw.get("kind") == "definition":
            candidates.add(f"{raw['name']}_def")
        if raw.get("kind") == "lemmas":
            rhs = raw.get("statement", "").partition("=")[2]
            candidates.update(re.findall(
                r"[A-Za-z][A-Za-z0-9_]*", rhs,
            ))
        accessible.append(raw | {"candidate_names": sorted(candidates)})
    required_names = {
        "no_fail_pre", "set_object", "in_monad", "a_type",
        "more_pd_inner_beauty", "exec_get", "exec_gets", "bind_assoc",
        "ko_wp_at'", "opt_map", "projectKO_opts_defs",
        "project_koType", "archObjSize", "archObjSize_def",
        "wordSizeCase", "wordSizeCase_def", "wordBits", "wordBits_def",
        "word_size", "word_size_def", "pdeBits", "pdeBits_def",
        "pageBits", "pageBits_def", "word_power_less_1",
        "setObject_def", "updateObject_default",
        "updateObject_default_def", "split_def", "put", "put_def",
        "get_object", "get_object_def", "bind_def", "gets_def",
        "get_def", "return_def", "assert_def", "fail_def",
        "caps_of_state_after_update", "cte_wp_at_after_update",
        "swp_def", "fun_upd_def", "dom_fun_upd2",
        "dom_insert_absorb", "dom_in",
        "ekheap_kheap_dom",
    }
    exact = exact_required_records(accessible, required_names)
    ranked = bm25(accessible, query, k)
    selected = {}
    for item in sorted(exact, key=lambda record: record["id"]) + ranked:
        selected.setdefault(
            item["id"],
            {key: value for key, value in item.items() if key != "score"},
        )
    return list(selected.values())


def runtime_residual_generated_premises(
        campaign_dir: Path, payload: dict, query: str, k: int = 10
) -> list[dict]:
    allowed = set(
        read_json(campaign_dir / "visibility.json")[THEORY][
            "generated_target_ancestors"
        ]
    )
    records = []
    for raw in generated_premises(campaign_dir, allowed):
        candidates = set(raw.get("candidate_names", [raw["name"]]))
        if raw.get("kind") == "definition":
            candidates.add(raw["name"] + "_def")
        records.append(raw | {"candidate_names": sorted(candidates)})
    required_names = {
        "regen_obj_at'_def", "regen_objBits_def", "regen_objBits_defs",
        "regen_objBits_simps", "regen_objBits_simps'", "typ_at'_def",
        "regen_projectKOs", "regen_lookupAround2_known1",
        "regen_ps_clear_lookupAround2",
        "pspace_relation", "pspace_relation_def",
        "ekheap_relation", "ekheap_relation_def",
        "ghost_relation", "ghost_relation_def",
        "tcb_relation_cut", "tcb_relation_cut_def",
        "regen_pspace_dom_update", "regen_map_to_ctes_upd_other",
        "regen_obj_relation_cuts_def2", "regen_obj_relation_cuts_def3",
        "regen_in_alignCheck", "regen_in_magnitude_check",
    }
    exact = exact_required_records(records, required_names)
    ranked = bm25(records, query, k)
    selected = {}
    for item in sorted(exact, key=lambda record: record["id"]) + ranked:
        selected.setdefault(
            item["id"],
            {key: value for key, value in item.items() if key != "score"},
        )
    return list(selected.values())


def runtime_leaf_response_file(attempt: dict, run_dir: Path) -> Path | None:
    source_name = Path(attempt.get("source", "")).parent.name
    source_match = re.match(r"^(\d+)-", source_name)
    stage_match = re.search(
        r"runtime_leaf(?:_revision)?_(\d+)_(\d+)", source_name,
    )
    if source_match is None:
        return None
    source_sequence = int(source_match.group(1))
    if stage_match is None:
        adjacent = sorted((run_dir / "llm").glob(
            f"{source_sequence - 1:04d}-repair-*.final.txt"
        ))
        return adjacent[0] if len(adjacent) == 1 else None
    candidates = []
    for path in (run_dir / "llm").glob(
            f"*-repair-{stage_match.group(1)}-{stage_match.group(2)}.final.txt"):
        event_match = re.match(r"^(\d+)-", path.name)
        if event_match is not None and int(event_match.group(1)) < source_sequence:
            candidates.append((int(event_match.group(1)), path))
    return max(candidates, default=(0, None))[1]


def best_failed_runtime_leaf_frontier(
        attempts: list[dict], node_id: str, run_dir: Path) -> dict | None:
    candidates = []
    start = 0
    for index, attempt in enumerate(attempts):
        if (
                attempt.get("contract_id") == node_id
                and attempt.get("role") == "runtime_leaf"
                and attempt.get("verdict") == "pass"
        ):
            start = index + 1
    for attempt in attempts[start:]:
        if not (
                attempt.get("contract_id") == node_id
                and attempt.get("role") == "runtime_leaf"
                and attempt.get("verdict") == "fail"
        ):
            continue
        match = re.match(r"^(\d+)-", Path(attempt.get("source", "")).parent.name)
        if match is None:
            continue
        response_file = runtime_leaf_response_file(attempt, run_dir)
        if response_file is None:
            continue
        response = parse_json(response_file.read_text(encoding="utf-8"))
        if response.get("action") != "local_helper":
            continue
        proof = response.get("proof", "").strip()
        statement = response.get("statement", "").strip()
        if not valid_proof_body(proof) or not statement:
            continue
        enriched = failed_frontier_record(attempt, proof, statement)
        enriched["subgoal_count"] = attempt_subgoal_count(enriched)
        enriched["diagnostic"] = attempt_diagnostic(enriched)
        enriched["response"] = response
        enriched["llm_final"] = str(response_file.resolve().relative_to(REPO))
        candidates.append(enriched)
    if not candidates:
        return None
    best_rank = min(failure_frontier_rank(item)[:3] for item in candidates)
    return next(
        item for item in reversed(candidates)
        if failure_frontier_rank(item)[:3] == best_rank
    )


def proof_rule_targets(proof: str) -> set[str]:
    return set(re.findall(
        r"\b(?:rule|erule|drule|frule)(?:_tac)?"
        r"(?:\([^)]*\))?\s+([A-Za-z][A-Za-z0-9_'.]*)",
        proof,
    ))


def repeated_runtime_leaf_principal_rules(
        attempts: list[dict], node_id: str, run_dir: Path,
        minimum_failures: int = 3,
        excluded_rules: set[str] | None = None) -> list[str]:
    counts = Counter()
    for attempt in attempts:
        if not (
                attempt.get("contract_id") == node_id
                and attempt.get("role") == "runtime_leaf"
                and attempt.get("verdict") == "fail"
        ):
            continue
        response_file = runtime_leaf_response_file(attempt, run_dir)
        if response_file is None:
            continue
        response = parse_json(response_file.read_text(encoding="utf-8"))
        if response.get("action") != "local_helper":
            continue
        counts.update(proof_rule_targets(response.get("proof", "")))
    wrappers = {"corres_guard_imp", "stronger_corres_guard_imp"}
    excluded = wrappers | (excluded_rules or set())
    return sorted(
        name for name, count in counts.items()
        if count >= minimum_failures and name not in excluded
    )


def failed_apply_targets_helper(frontier: dict, helper_name: str) -> bool:
    diagnostic = frontier.get("diagnostic", "")
    match = re.search(
        r'Failed to apply proof method \(line (\d+) of "[^"]+"\)',
        diagnostic,
    )
    source_name = frontier.get("source", "")
    if match is None or not source_name or not helper_name:
        return False
    source_path = Path(source_name)
    if not source_path.is_absolute():
        source_path = REPO / source_path
    if not source_path.is_file():
        return False
    lines = source_path.read_text(encoding="utf-8").splitlines()
    line_index = int(match.group(1)) - 1
    if line_index < 0 or line_index >= len(lines):
        return False
    command_lines = []
    parenthesis_depth = 0
    for line in lines[line_index:line_index + 200]:
        command_lines.append(line)
        parenthesis_depth += line.count("(") - line.count(")")
        if parenthesis_depth <= 0:
            break
    command_window = "\n".join(command_lines)
    return re.search(
        r"(?<![A-Za-z0-9_'])" + re.escape(helper_name)
        + r"(?![A-Za-z0-9_'])",
        command_window,
    ) is not None


def proof_prompt(payload: dict, plan: dict, target_ids: list[str],
                 green_proofs: dict[str, str]) -> str:
    context, scoped_plan = scoped_proof_context(payload, plan, target_ids)
    if AUDIT_TRAIL is not None:
        context["proof_prefix_probe_evidence"] = load_prefix_probe_evidence(
            AUDIT_TRAIL.run_dir, target_ids)
    return (
        """Generate Isabelle proof bodies for only the TARGET NODE IDS from
this certified Refine.ArchAcc_R lemma plan. Return exactly one proof item per
target and no other planned nodes. Use only declarations and premises in the
payload plus earlier proof-green nodes that are listed as dependencies. Do not
use tools, files, external knowledge, human Refine theorem names, sorry, oops,
axioms, or new declarations. A proof must be a complete `by ...`, `apply ... done`, or structured
`proof ... qed` body. Keep fixed contract statements unchanged.

Never guess conventional `_def` theorem names. Every named fact must be an exact
name from the payload or an earlier proof-green contract. For a matching
contract ID, prefer an `isabelle_verified_residual_hints` proof: it was generated
by an earlier audited model call and checked against the same parent heap.
For a matching `isabelle_residual_history` entry, do not repeat its failed
strategy. Prove the planned residual-derived leaf helper first when one exists.
Declaration records of kind `abbreviation` are syntax only, not named facts.
For a residual-derived support node, follow its `verified_strategy`; in particular,
do not erase explicit monad result types such as `:: asidpool kernel`. When a
`verified_proof_skeleton` is present, reproduce it or make only diagnostic-driven
minimal changes; it is bound to a green source/log pair for the same parent heap.
When `verified_contract_proof_skeleton` is present for the target contract,
reproduce that green consumer proof or make only diagnostic-driven minimal changes.

Return only JSON:
{
  "schema_version": 1,
  "theory": "Refine.ArchAcc_R",
  "proofs": [{"node_id": "...", "proof": "by ..."}]
}

TARGET NODE IDS:
"""
        + json.dumps(target_ids, ensure_ascii=False, separators=(",", ":"))
        + "\n\nEARLIER PROOF-GREEN NODES:\n"
        + json.dumps(list(green_proofs), ensure_ascii=False, separators=(",", ":"))
        + "\n\nMACHINE-GREEN SIBLING TEMPLATES (adapt structure; cite only declared dependencies):\n"
        + json.dumps(green_proof_templates(plan, green_proofs, target_ids), ensure_ascii=False, separators=(",", ":"))
        + "\n\nVERIFIED STRUCTURAL SIBLING PROOFS (strategy evidence only; never an implicit theorem dependency):\n"
        + json.dumps(structural_sibling_proof_templates(
            plan, green_proofs, target_ids,
        ), ensure_ascii=False, separators=(",", ":"))
        + "\n\nPAYLOAD:\n"
        + json.dumps(
            context, ensure_ascii=False, separators=(",", ":"),
        )
        + "\n\nCERTIFIED PLAN:\n"
        + json.dumps(scoped_plan, ensure_ascii=False, separators=(",", ":"))
    )


def validate_proofs(proofs: dict, expected: set[str]) -> dict[str, str]:
    if proofs.get("schema_version") != 1 or proofs.get("theory") != THEORY:
        raise ValueError("invalid proof response schema or theory")
    items = proofs.get("proofs", [])
    result = {item["node_id"]: item["proof"].strip() for item in items}
    if len(items) != len(result) or set(result) != expected:
        raise ValueError("proof response does not exactly cover the target node set")
    for node_id, proof in result.items():
        if not valid_proof_body(proof):
            raise ValueError(f"illegal or incomplete proof body for {node_id}")
    return result



def fact_name_ids(payload: dict, plan: dict) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    declaration_signatures: dict[str, set[str]] = {}
    for item in plan["support_nodes"]:
        result.setdefault(item["name"], set()).add(item["id"])
    for item in payload["fixed_contracts"]:
        result.setdefault(item["name"], set()).add(item["id"])
    for key in (
        "fixed_framework_declarations", "retrieved_legal_premises",
        "retrieved_legal_generated_premises",
    ):
        for item in payload[key]:
            signature = " ".join(item.get("statement", "").split())
            for name in set(item.get("candidate_names", [])) | {item["name"]}:
                seen = declaration_signatures.setdefault(name, set())
                if signature and signature in seen:
                    continue
                result.setdefault(name, set()).add(item["id"])
                if signature:
                    seen.add(signature)
    return result


def cited_fact_ids(payload: dict, plan: dict, proof: str) -> tuple[dict[str, set[str]], set[str]]:
    names = fact_name_ids(payload, plan)
    cited = {
        name: ids for name, ids in names.items()
        if re.search(r"(?<![A-Za-z0-9_'])" + re.escape(name) + r"(?![A-Za-z0-9_'])", proof)
    }
    unknown_generated = {
        name for name in re.findall(r"(?<![A-Za-z0-9_'])regen_[A-Za-z0-9_']+", proof)
        if name not in names
    }
    return cited, unknown_generated


def complete_unique_citation_dependencies(
        payload: dict, plan: dict, proof: str, dependencies: list[str],
        legal_dependency_ids: set[str]) -> tuple[list[str], list[dict]]:
    completed = list(dependencies)
    declared = set(dependencies)
    additions = []
    cited, _ = cited_fact_ids(payload, plan, proof)
    for name, ids in cited.items():
        if ids & declared:
            continue
        candidates = sorted(ids & legal_dependency_ids)
        if len(candidates) != 1:
            continue
        dependency_id = candidates[0]
        completed.append(dependency_id)
        declared.add(dependency_id)
        additions.append({
            "fact_name": name,
            "dependency_id": dependency_id,
        })
    return completed, additions


def validate_proof_citations(
        payload: dict, plan: dict, node_id: str, proof: str,
        *, check_fact_existence: bool = True) -> None:
    declared = set(next(
        item["dependencies"]
        for item in plan["support_nodes"] + plan["contract_nodes"]
        if item["id"] == node_id
    ))
    cited, unknown_generated = cited_fact_ids(payload, plan, proof)
    if unknown_generated:
        raise ValueError(f"proof for {node_id} cites unknown generated facts: {sorted(unknown_generated)}")
    known_names = set(fact_name_ids(payload, plan)) | set(
        payload.get("_session_visible_fact_names", [])
    )
    unknown_definition_facts = {
        name for name in re.findall(
            r"(?<![A-Za-z0-9_'])[A-Za-z][A-Za-z0-9_']*_def(?![A-Za-z0-9_'])",
            proof,
        )
        if name not in known_names
    }
    if check_fact_existence and unknown_definition_facts:
        raise ValueError(
            f"proof for {node_id} cites unknown definition facts: "
            f"{sorted(unknown_definition_facts)}"
        )
    node = next(
        item for item in plan["support_nodes"] + plan["contract_nodes"]
        if item["id"] == node_id
    )
    bundle_extension_ids = set(payload.get("_helper_bundle_extension_ids", []))
    enforce_bundle_extension_edges = (
        node.get("origin") in {
            "audited_atomic_helper_bundle",
            "audited_atomic_helper_bundle_v2",
        }
    )
    hidden = {
        name: sorted(ids) for name, ids in cited.items()
        if not ids & declared and node_id not in ids
        and (enforce_bundle_extension_edges or not set(ids) <= bundle_extension_ids)
    }
    if hidden:
        raise ValueError(
            f"proof for {node_id} has undeclared DAG citations: "
            + json.dumps(hidden, sort_keys=True, separators=(",", ":"))
        )


def complete_verified_citation_edges(plan: dict, payload: dict) -> list[dict]:
    support_by_statement = {
        item["statement"]: item for item in plan["support_nodes"]
    }
    node_by_id = {
        item["id"]: item for item in plan["support_nodes"] + plan["contract_nodes"]
    }
    legal_ids = {
        item["id"]
        for key in (
            "fixed_framework_declarations", "retrieved_legal_premises",
            "retrieved_legal_generated_premises",
        )
        for item in payload[key]
    }
    changes = []
    evidence = []
    for residual in payload.get("isabelle_residual_history", []):
        support = support_by_statement.get(residual.get("required_support_statement"))
        support_proof = residual.get("verified_proof_skeleton", "").strip()
        if support is not None and valid_proof_body(support_proof):
            evidence.append((support, support_proof, residual.get("generation_id")))
        contract = node_by_id.get(residual.get("contract_id"))
        contract_proof = residual.get("verified_contract_proof_skeleton", "").strip()
        if contract is not None and valid_proof_body(contract_proof):
            evidence.append((contract, contract_proof, residual.get("generation_id")))
            for support_node in plan["support_nodes"]:
                if residual.get("contract_id") in support_node.get("consumers", []):
                    evidence.append((
                        support_node, contract_proof, residual.get("generation_id"),
                    ))
    support_position = {
        node_id: index for index, node_id in enumerate(plan["support_order"])
    }
    contract_position = {
        node_id: index for index, node_id in enumerate(plan["contract_order"])
    }
    all_support_ids = set(support_position)
    for node, proof, source_generation in evidence:
        cited, unknown = cited_fact_ids(payload, plan, proof)
        if unknown:
            raise ValueError(
                f"verified residual for {node['id']} cites unavailable generated facts: {sorted(unknown)}"
            )
        if node["id"] in support_position:
            legal_predecessors = {
                node_id for node_id, index in support_position.items()
                if index < support_position[node["id"]]
            }
        else:
            legal_predecessors = all_support_ids | {
                node_id for node_id, index in contract_position.items()
                if index < contract_position[node["id"]]
            }
        for name, ids in cited.items():
            candidates = sorted(ids & (legal_ids | legal_predecessors))
            if not candidates or set(candidates) & set(node["dependencies"]):
                continue
            if len(candidates) != 1:
                raise ValueError(f"ambiguous verified premise citation {name}: {candidates}")
            dependency = candidates[0]
            node["dependencies"].append(dependency)
            changes.append({
                "action": "complete_verified_premise_edge",
                "node_id": node["id"],
                "fact_name": name,
                "dependency_id": dependency,
                "source_generation_id": source_generation,
            })
    return changes


def complete_residual_premise_edges(
    plan: dict, payload: dict, attempts_path: Path, events: list[dict] = (),
) -> list[dict]:
    if not attempts_path.is_file():
        return []
    attempts = read_json(attempts_path)
    triggers = [{
        "node_id": "contract:Refine.ArchAcc_R:getObject_PDE_corres':da16732fd056",
        "premise_name": "stronger_corres_guard_imp",
        "required_patterns": ["state_relation", "pde_at p", "pspace_aligned'"],
    }, {
        "node_id": "contract:Refine.ArchAcc_R:get_master_pde_corres':ad536c0b8013",
        "premise_name": "stronger_corres_guard_imp",
        "required_patterns": [
            "state_relation", "pde_at p", "vs_valid_duplicates'",
            "pspace_aligned'",
        ],
    }]
    nodes = {item["id"]: item for item in plan["contract_nodes"]}
    premise_by_name = {}
    for item in payload["retrieved_legal_premises"]:
        for name in set(item.get("candidate_names", [])) | {item["name"]}:
            premise_by_name.setdefault(name, []).append(item)
    changes = []
    for trigger in triggers:
        if trigger["node_id"] not in nodes:
            continue
        log_path = None
        stage_prefix = f"ArchAcc_R_prefix_{plan["contract_order"].index(trigger["node_id"]) + 1}"
        for event in reversed(events):
            if (event.get("kind") != "isabelle"
                    or event.get("verdict") != "fail"
                    or not event.get("stage", "").startswith(stage_prefix)):
                continue
            candidate_log = REPO / event.get("log", "")
            if not candidate_log.is_file():
                continue
            diagnostic = candidate_log.read_text(encoding="utf-8", errors="replace")
            if all(pattern in diagnostic for pattern in trigger["required_patterns"]):
                log_path = candidate_log
                break
        for attempt in reversed(attempts) if log_path is None else []:
            if (attempt.get("node_id") != trigger["node_id"]
                    or attempt.get("verdict") != "fail"):
                continue
            candidate_log = Path(attempt.get("log", ""))
            if not candidate_log.is_absolute():
                candidate_log = REPO / candidate_log
            if not candidate_log.is_file():
                continue
            diagnostic = candidate_log.read_text(encoding="utf-8", errors="replace")
            if all(pattern in diagnostic for pattern in trigger["required_patterns"]):
                log_path = candidate_log
                break
        if log_path is None:
            continue
        matches = premise_by_name.get(trigger["premise_name"], [])
        if len(matches) != 1:
            raise ValueError(
                f"residual premise retrieval for {trigger['premise_name']} "
                f"is not unique: {[item['id'] for item in matches]}"
            )
        node = nodes[trigger["node_id"]]
        premise_id = matches[0]["id"]
        if premise_id in node["dependencies"]:
            continue
        node["dependencies"].append(premise_id)
        changes.append({
            "action": "complete_residual_premise_edge",
            "node_id": node["id"],
            "premise_name": trigger["premise_name"],
            "dependency_id": premise_id,
            "evidence_log": str(log_path.relative_to(REPO)),
            "evidence_log_sha256": sha256_path(log_path),
        })
    return changes


def complete_recorded_candidate_premise_edges(
    plan: dict, payload: dict, llm_dir: Path,
) -> list[dict]:
    nodes = {item["id"]: item for item in plan["support_nodes"] + plan["contract_nodes"]}
    legal_ids = {
        item["id"]
        for key in (
            "fixed_framework_declarations", "retrieved_legal_premises",
            "retrieved_legal_generated_premises",
        )
        for item in payload[key]
    }
    support_position = {
        node_id: index for index, node_id in enumerate(plan["support_order"])
    }
    planned_support_order = [
        node_id for node_id in plan["support_order"]
        if nodes[node_id].get("origin") != "isabelle_residual_runtime_extension"
    ]
    contract_position = {
        node_id: index for index, node_id in enumerate(plan["contract_order"])
    }
    all_support_ids = set(support_position)
    changes = []
    for path in sorted(llm_dir.glob("*.final.txt")):
        try:
            response = parse_json(path.read_text(encoding="utf-8"))
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
        proof_items = list(response.get("proofs", []))
        if response.get("action") == "proof":
            support_match = re.match(r"^\d+-repair-support-(\d+)-", path.name)
            contract_match = re.match(r"^\d+-repair-(\d+)-", path.name)
            if support_match:
                index = int(support_match.group(1))
                if 1 <= index <= len(planned_support_order):
                    proof_items.append({
                        "node_id": planned_support_order[index - 1],
                        "proof": response.get("proof", ""),
                    })
            elif contract_match:
                index = int(contract_match.group(1))
                if 1 <= index <= len(plan["contract_order"]):
                    proof_items.append({
                        "node_id": plan["contract_order"][index - 1],
                        "proof": response.get("proof", ""),
                    })
        elif response.get("action") == "local_helper":
            contract_match = re.match(r"^\d+-repair-(\d+)-", path.name)
            if contract_match:
                index = int(contract_match.group(1))
                if 1 <= index <= len(plan["contract_order"]):
                    proof_items.append({
                        "node_id": plan["contract_order"][index - 1],
                        "proof": response.get("contract_proof", ""),
                    })
        for item in proof_items:
            node = nodes.get(item.get("node_id"))
            proof = item.get("proof", "")
            if node is None or not valid_proof_body(proof):
                continue
            if node["id"] in support_position:
                legal_predecessors = {
                    node_id for node_id, index in support_position.items()
                    if index < support_position[node["id"]]
                }
            else:
                legal_predecessors = all_support_ids | {
                    node_id for node_id, index in contract_position.items()
                    if index < contract_position[node["id"]]
                }
            cited, _ = cited_fact_ids(payload, plan, proof)
            for name, ids in cited.items():
                candidates = sorted(ids & (legal_ids | legal_predecessors))
                if not candidates or set(candidates) & set(node["dependencies"]):
                    continue
                if len(candidates) != 1:
                    continue
                node["dependencies"].append(candidates[0])
                changes.append({
                    "action": "complete_recorded_candidate_premise_edge",
                    "node_id": node["id"], "fact_name": name,
                    "dependency_id": candidates[0],
                    "source_response": str(path.relative_to(REPO)),
                })
    return changes



def reconcile_runtime_leaves_from_ledger(
        plan: dict, extension_path: Path) -> list[dict]:
    if not extension_path.is_file():
        return []
    accepted = {}
    for line in extension_path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        helper = record.get("helper", {})
        if (
                record.get("status") in {"helper_green", "helper_revision_green"}
                and helper.get("id")
        ):
            accepted[helper["id"]] = helper
    changes = []
    for node in plan["support_nodes"]:
        helper = accepted.get(node["id"])
        if helper is None or node == helper:
            continue
        prior = dict(node)
        node.clear()
        node.update(helper)
        changes.append({
            "action": "restore_machine_green_runtime_leaf",
            "helper_id": node["id"],
            "prior_statement_sha256": sha256_text(prior["statement"]),
            "restored_statement_sha256": sha256_text(node["statement"]),
            "prior_dependencies": prior.get("dependencies", []),
            "restored_dependencies": node.get("dependencies", []),
        })
    return changes


def rollback_unverified_runtime_leaves(
        plan: dict, attempts: list[dict]) -> list[dict]:
    green_nodes = {
        attempt.get("node_id") for attempt in attempts
        if attempt.get("verdict") == "pass"
    }
    provisional = [
        item for item in plan["support_nodes"]
        if (
            item.get("origin") == "isabelle_residual_runtime_extension"
            and item["id"] not in green_nodes
        )
    ]
    changes = []
    for helper in provisional:
        helper_id = helper["id"]
        plan["support_nodes"].remove(helper)
        if helper_id in plan["support_order"]:
            plan["support_order"].remove(helper_id)
        for contract in plan["contract_nodes"]:
            if helper_id in contract.get("dependencies", []):
                contract["dependencies"].remove(helper_id)
        changes.append({
            "action": "rollback_unverified_runtime_leaf",
            "helper_id": helper_id,
            "consumers": helper.get("consumers", []),
            "helper": helper,
        })
    return changes


def render_theory(payload: dict, plan: dict, proofs: dict[str, str], upto: int | None = None) -> str:
    contracts = {item["id"]: item for item in payload["fixed_contracts"]}
    support_by_id = {item["id"]: item for item in plan["support_nodes"]}
    support = [support_by_id[node_id] for node_id in plan["support_order"] if node_id in proofs]
    ordered_contracts = plan["contract_order"] if upto is None else plan["contract_order"][:upto]
    blocks = []
    for node in support:
        blocks.append(f"{node['statement'].rstrip()}\n{proofs[node['id']]}\n")
    for node_id in ordered_contracts:
        blocks.append(f"{contracts[node_id]['statement'].rstrip()}\n{proofs[node_id]}\n")
    source = "\n".join([
        "(* Generated from fixed contracts; no human Refine proof declarations used. *)",
        "theory ArchAcc_R",
        'imports "RegenRefineARM_L8.SubMonad_R"',
        "begin",
        "context begin interpretation Arch .",
        *(item["statement"] for item in CONTRACT_LOCAL_FRAMEWORK),
        "\n".join(blocks).rstrip(),
        "end",
        "end",
        "",
    ])
    if FORBIDDEN_RE.search(source):
        raise ValueError("generated source contains a forbidden command")
    return source


def isabelle_failure_diagnostic(
        output: str, returncode: int, timeout_seconds: int) -> str:
    if returncode == 124:
        return (
            "HARNESS ISABELLE TIMEOUT: proof checking exceeded "
            f"{timeout_seconds} seconds and produced no semantic residual. "
            "This is a proof-search performance failure. "
            "Do not repeat the same proof or merely add simp facts. "
            "Replace broad constructor clarsimp/case search with explicit, bounded "
            "branches and preserve the already terminating proof prefix."
        )
    return output[-16000:]


def bounded_repair_diagnostic(diagnostic: str) -> str:
    """Keep the newest repair evidence without recursively carrying old rounds."""
    if len(diagnostic) <= REPAIR_DIAGNOSTIC_LIMIT:
        return diagnostic
    return (
        "[older repair history omitted; complete evidence remains in events.jsonl]\n"
        + diagnostic[-REPAIR_DIAGNOSTIC_LIMIT:]
    )


def runtime_phase_rejection_diagnostic(record: dict) -> str:
    """Render the latest pre-Isabelle rejection as resumable repair evidence."""
    return bounded_repair_diagnostic(
        "HARNESS RESUMED RUNTIME-PHASE REJECTION: "
        + record.get("residual", "candidate violated the active phase strategy")
        + "\nContinue from this rejected candidate; change the specifically "
          "rejected tactic and preserve the rest of its progress.\n"
          "REJECTED LOCAL_HELPER RESPONSE:\n"
        + json.dumps(
            record.get("repair", {}), ensure_ascii=False, separators=(",", ":"),
        )[-12000:]
    )


def upgrade_runtime_phase_rejection(record: dict) -> dict:
    """Migrate persisted phase evidence when a mandatory tactic was disproved."""
    upgraded = dict(record)
    residual = upgraded.get("residual", "")
    repair_text = json.dumps(
        upgraded.get("repair", {}), ensure_ascii=False, separators=(",", ":"),
    )
    if (
            upgraded.get("protocol_version", 0)
            < RUNTIME_PHASE_PROTOCOL_VERSION
            and ("dom_in" in residual or "dom_insert_absorb" in repair_text)
    ):
        upgraded["residual"] = (
            "the cataloged dom_in theorem is oriented from an existential "
            "Some lookup to domain membership, so simp add: dom_in cannot "
            "solve the membership goal. After subst dom_insert_absorb use "
            "exactly `apply (rule iffD1[OF dom_in])`, "
            "`apply (rule exI)`, `apply assumption`, then close the rewritten "
            "pspace_dom equality with a second `apply assumption`"
        )
    upgraded["protocol_version"] = RUNTIME_PHASE_PROTOCOL_VERSION
    return upgraded


def direct_state_update_residual(diagnostic: str) -> bool:
    named_transition = all(
        marker in diagnostic
        for marker in ("state_relation", "setObject", "pspace_relation")
    ) and ("put (s" in diagnostic or "kheap :=" in diagnostic)
    expanded_transition = all(
        marker in diagnostic
        for marker in (
            "magnitudeCheck", "kheap :=", "cdt_relation",
            "arch_state_relation",
        )
    )
    return named_transition or expanded_transition


def phase_mandatory_principal_rules(diagnostic: str) -> set[str]:
    if not direct_state_update_residual(diagnostic):
        return set()
    expanded_markers = (
        "pspace_dom", "cdt_list_relation",
        "revokable_relation", "ksDomSchedule",
    )
    if all(marker in diagnostic for marker in expanded_markers):
        return {
            "conjI", "dom_in", "dom_insert_absorb",
            "regen_pspace_dom_update",
        }
    return set()



def runtime_phase_strategy_error(
        proof: str, diagnostic: str, frontier_diagnostic: str = "") -> str:
    diagnostic = frontier_diagnostic or diagnostic
    if not direct_state_update_residual(diagnostic):
        return ""
    if "fastforce" in proof:
        return (
            "direct state-update candidate uses broad fastforce; normalize "
            "alignCheck and magnitudeCheck memberships, then prove relation "
            "conjuncts with bounded interface-local steps"
        )
    if "regen_ps_clear_lookupAround2" in proof:
        return (
            "direct state-update candidate reintroduces the phase-inappropriate "
            "object-discovery rule regen_ps_clear_lookupAround2; use only the "
            "already exposed lookup pair or bounded pair normalization"
        )
    required_monad_defs = (
        "get_object_def", "bind_def", "gets_def", "get_def",
        "return_def", "assert_def", "fail_def",
    )
    missing_monad_defs = [
        name for name in required_monad_defs if name not in proof
    ]
    if missing_monad_defs:
        return (
            "direct state-update candidate omits mandatory abstract-monad "
            "definition closure before state_relation decomposition: "
            + ", ".join(missing_monad_defs)
        )
    closure_start = max(proof.rfind(name) for name in required_monad_defs)
    post_witness_commands = [
        match.group(0)
        for match in re.finditer(
            r"(?ms)^apply\b.*?(?=^apply\b|\Z)", proof,
        )
        if match.end() > closure_start
    ]
    state_relation_already_expanded = all(
        marker in diagnostic
        for marker in (
            "pspace_dom", "cdt_list_relation",
            "revokable_relation", "ksDomSchedule",
        )
    )
    if state_relation_already_expanded and any(
            "state_relation_def" in command
            for command in post_witness_commands
    ):
        return (
            "post-witness candidate repeats state_relation_def although the "
            "persisted residual already contains its expanded conjunctions; "
            "continue directly with bounded conjunct and local update steps"
        )
    if state_relation_already_expanded and any(
            "intro" in command
            and all(
                rule in command
                for rule in ("conjI", "allI", "ballI", "impI")
            )
            for command in post_witness_commands
    ):
        return (
            "expanded relation candidate introduces all conjunctions and "
            "quantifiers at once; use one rule conjI and close the current "
            "conjunct before splitting the next"
        )
    first_conjunct_split = next((
        index for index, command in enumerate(post_witness_commands)
        if "rule conjI" in command
    ), None)
    if state_relation_already_expanded and first_conjunct_split is None:
        return (
            "expanded relation candidate omits the mandatory first "
            "rule conjI; split and close pspace_dom before attempting the "
            "remaining relation conjunction"
        )
    if state_relation_already_expanded:
        next_conjunct_split = next((
            index for index in range(
                first_conjunct_split + 1, len(post_witness_commands)
            )
            if "rule conjI" in post_witness_commands[index]
        ), len(post_witness_commands))
        first_conjunct_window = "\n".join(
            post_witness_commands[
                first_conjunct_split:next_conjunct_split
            ]
        )
        if not any(
                "subst" in command
                and "regen_pspace_dom_update" in command
                for command in post_witness_commands[
                    first_conjunct_split:first_conjunct_split + 2
                ]
        ):
            return (
                "expanded relation first conjunct omits mandatory explicit "
                "subst of regen_pspace_dom_update; close pspace_dom before "
                "splitting the next conjunct"
            )
        if not re.search(
                r"\bwhere\s+x\s*=", first_conjunct_window
        ):
            return (
                "expanded relation pspace_dom substitution leaves the old "
                "object schematic; instantiate regen_pspace_dom_update with "
                "where x = ArchObj (PageDirectory pd) before discharging its "
                "lookup and a_type premises"
            )
        has_absorb_subst = re.search(
            r"(?m)^\s*apply\s+\(subst(?:\s+\(\d+\))?\s+"
            r"dom_insert_absorb\b", first_conjunct_window,
        ) is not None
        has_membership_sequence = re.search(
            r"(?ms)^\s*apply\s+\(rule\s+iffD1\[OF\s+dom_in\]\)\s*$"
            r"\s*^\s*apply\s+\(rule\s+exI\)\s*$"
            r"\s*^\s*apply\s+assumption\s*$"
            r"\s*^\s*apply\s+assumption\s*$",
            first_conjunct_window,
        ) is not None
        wrong_membership_tactic = re.search(
            r"(?m)^\s*apply\s+\((?:(?:e?rule)\s+dom_in\b|"
            r"(?:simp|clarsimp)\b[^\n]*\bdom_in\b)[^\n]*\)",
            first_conjunct_window,
        )
        if has_absorb_subst and wrong_membership_tactic:
            return (
                "the exact membership tactic line "
                + repr(wrong_membership_tactic.group(0).strip())
                + " is invalid here because dom_in has the opposite simp "
                  "orientation. After `subst dom_insert_absorb`, replace the "
                  "membership step with exactly `apply (rule iffD1[OF dom_in])`, "
                  "`apply (rule exI)`, `apply assumption`; then use a second "
                  "`apply assumption` for the rewritten pspace_dom equality"
            )
        if not (
                has_absorb_subst
                and has_membership_sequence
        ):
            return (
                "expanded relation pspace_dom branch must conditionally rewrite "
                "the target insertion with subst dom_insert_absorb, derive "
                "membership via iffD1[OF dom_in] plus exI and the concrete "
                "lookup assumption, and close the rewritten relation with a "
                "second assumption before the next split"
            )
        if "rule sym" in first_conjunct_window:
            return (
                "expanded relation pspace_dom branch applies rule sym before "
                "the contextual pspace_dom equality is simplified; remove that "
                "premature orientation step and use conditional absorption subst"
            )
    if any(
            "clarsimp" in command
            and "state_relation_def" in command
            and "pspace_relation_def" in command
            for command in post_witness_commands
    ):
        return (
            "post-witness candidate broadly unfolds state_relation_def and "
            "pspace_relation_def in one clarsimp; preserve the one-goal frontier "
            "with bounded update-point and unchanged-point relation steps"
        )
    return ""


def residual_strategy_hint(diagnostic: str) -> str:
    constant_arch_size = diagnostic.count("archObjSize (KOPDE") >= 2
    constant_arch_size_hint = ""
    if constant_arch_size:
        constant_arch_size_hint = r"""

CONSTANT ARCH OBJECT-SIZE HINT: preserve the machine-verified proof prefix
byte-for-byte. The current equality compares two `KOPDE` values, and
`archObjSize` is independent of the enclosed PDE. Replace the failing plain
`apply simp` with the bounded step
`apply (simp add: archObjSize_def)`.
Do not split `x3`, `pde'`, or any other PDE value with `case_tac`; that expands
this constant-size equality into unrelated constructor branches.
"""
    direct_state_update = direct_state_update_residual(diagnostic)
    state_update_hint = ""
    if direct_state_update:
        state_update_hint = r"""

DIRECT STATE-UPDATE PHASE: after the constant `KOPDE` object-size equality is
closed, the remaining goal is relation preservation across the abstract
`set_object` update and concrete `setObject` update. It is no longer a
lookup/no-fail object-discovery obligation. Do not cite
`regen_ps_clear_lookupAround2` after entering this branch; that eliminator
reintroduces a schematic lookup address and regresses the frontier.
`regen_lookupAround2_known1` remains legal only as bounded pair normalization
for an already exposed `lookupAround2` equality.
Normalize the concrete update memberships first with exact `setObject_def`,
`updateObject_default_def`, `split_def`, `put_def`, `regen_in_alignCheck`, and
`regen_in_magnitude_check`. Before using the magnitude rewrite, establish
`(1 :: word32) < 2 ^ pdeBits` with exact `pdeBits_def`; use the existing
`lookupAround2` pair equality to rewrite its second component to the residual
`x2`. Only then can the membership rewrite eliminate the final intermediate
concrete state.
Next, eliminate the outer abstract-monad result existential with the exact
`get_object_def`, `bind_def`, `gets_def`, `get_def`, `return_def`,
`assert_def`, and `fail_def` closure. This step must expose the unique state witness
on the abstract side without unfolding either relation definition.
Inspect the witness body before choosing the next step. If the residual already
spells out `pspace_dom`, `cdt_list_relation`, `revokable_relation`, and domain
field conjuncts, `state_relation_def` was expanded by the verified prefix: do
not cite it again after monad closure. Do not use
`apply (intro conjI allI ballI impI)`, which exposes the whole repository-scale
relation at once. Instead use one `apply (rule conjI)`, close the current
conjunct, and only then split the next. Start with the `pspace_dom` update using
an explicit
`apply (subst regen_pspace_dom_update[where x="ArchObj (PageDirectory pd)"])`.
The old object cannot be inferred from the rewrite target; omitting this `where`
leaves a schematic object and four open goals. Discharge the resulting lookup
premise from the existing abstract `kheap` lookup, discharge the same-`a_type` premise
by constructor simplification. Next rewrite the insertion on the right-hand side
with `apply (subst dom_insert_absorb)`. This exposes membership as a separate
conditional premise. The cataloged `dom_in` is oriented as
`(\<exists>x. c a = Some x) = (a \<in> dom c)`, so `simp add: dom_in` cannot
rewrite a membership goal backwards. Close it with the exact sequence
`apply (rule iffD1[OF dom_in])`, `apply (rule exI)`, `apply assumption` using
`ksPSpace b p = Some ko`, then close the rewritten
`pspace_dom (kheap a) = dom (ksPSpace b)` goal with `apply assumption`. Do not
apply `rule sym`, and do not hide the conditional premise inside a combined
simplifier. Both declarations are mandatory retrieved premises. `dom_fun_upd2`
and an unrecorded `domIff` are not substitutes. Do not merely add
`regen_pspace_dom_update` to a broad simp set; its conditional rewrite may fail
to instantiate and leave the conjunction unchanged. Then handle update-point
and unchanged-point object relations.
target, expose only the top-level conjunct structure of `state_relation_def`.
Never put `state_relation_def` and `pspace_relation_def` in the same `clarsimp`.
Use `regen_obj_relation_cuts_def2`, `regen_obj_relation_cuts_def3`,
`ekheap_relation_def`, `ghost_relation_def`, and
`regen_map_to_ctes_upd_other` for changed conjuncts; keep scheduler, interrupt,
architecture, and domain fields structural. Do not use broad `fastforce` and
do not invent a lookup address.
"""
    if all(
            marker in diagnostic
            for marker in ("ko_wp_at'", "ksPSpace", "projectKO")
    ):
        return constant_arch_size_hint + state_update_hint + r"""

RESIDUAL-SHAPE ORDERING HINT: the object witness is still hidden inside
`ko_wp_at'`. Before `exI`, normalize it with the exact supplied
`ko_wp_at'_def`, `opt_map_def`, `projectKO_opts_defs`, `regen_obj_at'_def`,
`regen_projectKOs`, `regen_objBits_simps'`, and
`regen_lookupAround2_known1` facts. Then follow this bounded sibling pattern:
`apply (erule(1) regen_ps_clear_lookupAround2)`, `apply simp`, create an
`\<exists>obj::ARM_H.pde. projectKO_opt ko = Some obj` subgoal, solve that
subgoal with `simp add: project_koType`, and only then simplify projection and
object-size with `projectKO_opts_defs`, `regen_projectKOs`, and
`regen_objBits_simps'`. Do not use `fastforce` for this residual and do not
introduce a schematic object witness while `ko_wp_at'` remains an assumption.
"""
    return constant_arch_size_hint + state_update_hint



def diagnostic_subgoal_count(output: str) -> int | None:
    counts = [
        int(match)
        for match in re.findall(r"\*\*\* goal \((\d+) subgoals?\):", output)
    ]
    return min(counts) if counts else None


def failure_frontier_rank(attempt: dict) -> tuple[int, int, int, int]:
    if attempt.get("verdict") == "pass":
        return (-1, 0, 0, attempt.get("wall_ms") or 0)
    if attempt.get("returncode") == 124:
        return (2, 10**9, 0, attempt.get("wall_ms") or 10**9)
    subgoal_count = attempt.get("subgoal_count")
    if isinstance(subgoal_count, int):
        return (
            0, subgoal_count, -int(attempt.get("failure_progress") or 0),
            attempt.get("wall_ms") or 10**9,
        )
    return (1, 10**9, 0, attempt.get("wall_ms") or 10**9)


ISABELLE_PROOF_COMMAND_START = re.compile(
    r"^(?:apply|by|done|qed|proof|using|unfolding|subgoal|prefer|defer|"
    r"back|next|case|show|have|then|thus|also|finally|moreover|"
    r"ultimately|from|with|note|let|fix|assume)\b"
)


def proof_command_start_index(lines: list[str], failed_index: int) -> int:
    """Map an Isabelle-reported continuation line to its command start."""
    failed_index = min(max(failed_index, 0), len(lines) - 1)
    for index in range(failed_index, -1, -1):
        if ISABELLE_PROOF_COMMAND_START.match(lines[index].strip()):
            return index
    return failed_index


def proof_failure_progress(
        attempt: dict, proof: str, statement: str = "") -> int:
    diagnostic = attempt.get("diagnostic") or attempt_diagnostic(attempt)
    match = re.search(
        r'Failed to apply proof method \(line (\d+) of "[^"]+"\)',
        diagnostic,
    )
    if match is None:
        return 0
    source_path = Path(attempt.get("source", ""))
    if not source_path.is_absolute():
        source_path = REPO / source_path
    if not source_path.is_file():
        return 0
    source = source_path.read_text(encoding="utf-8", errors="replace")
    search_start = 0
    if statement:
        statement_start = source.find(statement.rstrip())
        if statement_start >= 0:
            search_start = statement_start + len(statement.rstrip())
    proof_start = source.find(proof.strip(), search_start)
    if proof_start < 0:
        return 0
    proof_start_line = source.count("\n", 0, proof_start) + 1
    failed_line = int(match.group(1))
    if failed_line < proof_start_line:
        return 0
    proof_lines = proof.strip().splitlines()
    failed_index = min(
        failed_line - proof_start_line, len(proof_lines) - 1,
    )
    return proof_command_start_index(proof_lines, failed_index) + 1


def failed_frontier_record(
        result: dict, proof: str, statement: str = "") -> dict:
    record = dict(result)
    record["proof"] = proof
    record["proof_sha256"] = sha256_text(proof)
    record["failure_progress"] = proof_failure_progress(
        record, proof, statement,
    )
    return record



def attempt_subgoal_count(attempt: dict) -> int | None:
    if isinstance(attempt.get("subgoal_count"), int):
        return attempt["subgoal_count"]
    log_path = Path(attempt.get("log", ""))
    if not log_path.is_absolute():
        log_path = REPO / log_path
    if not log_path.is_file():
        return None
    return diagnostic_subgoal_count(
        log_path.read_text(encoding="utf-8", errors="replace"),
    )


def attempt_diagnostic(attempt: dict) -> str:
    log_path = Path(attempt.get("log", ""))
    if not log_path.is_absolute():
        log_path = REPO / log_path
    if attempt.get("returncode") == 124:
        return isabelle_failure_diagnostic(
            "", 124, max(1, int((attempt.get("wall_ms") or 180000) / 1000)),
        )
    if not log_path.is_file():
        return ""
    return isabelle_failure_diagnostic(
        log_path.read_text(encoding="utf-8", errors="replace"),
        attempt.get("returncode", 1), 180,
    )


def proof_from_attempt_source(attempt: dict, statement: str) -> str:
    source_path = Path(attempt.get("source", ""))
    if not source_path.is_absolute():
        source_path = REPO / source_path
    if not source_path.is_file():
        return ""
    source = source_path.read_text(encoding="utf-8", errors="replace")
    marker = statement.rstrip()
    start = source.find(marker)
    if start < 0:
        return ""
    tail = source[start + len(marker):].lstrip()
    boundary = re.search(r"\n(?=lemma\s|\bend\s*\nend\s*$)", tail, flags=re.M)
    proof = tail[:boundary.start() if boundary else len(tail)].strip()
    return proof if valid_proof_body(proof) else ""


def attempts_after_latest_runtime_leaf(
        attempts: list[dict], node_id: str) -> list[dict]:
    start = 0
    for index, attempt in enumerate(attempts):
        if (
                attempt.get("contract_id") == node_id
                and attempt.get("role") in {
                    "runtime_leaf", "runtime_leaf_revision",
                }
                and attempt.get("verdict") == "pass"
        ):
            start = index + 1
    return attempts[start:]


def best_failed_frontier(
        attempts: list[dict], node_id: str, statement: str) -> dict | None:
    candidates = []
    for attempt in attempts_after_latest_runtime_leaf(attempts, node_id):
        if attempt.get("node_id") != node_id or attempt.get("verdict") == "pass":
            continue
        enriched = dict(attempt)
        diagnostic = attempt_diagnostic(enriched)
        enriched["subgoal_count"] = attempt_subgoal_count(enriched)
        proof = proof_from_attempt_source(enriched, statement)
        if not proof:
            continue
        enriched["proof"] = proof
        enriched["proof_sha256"] = sha256_text(proof)
        enriched["diagnostic"] = diagnostic
        enriched["failure_progress"] = proof_failure_progress(
            enriched, proof, statement,
        )
        attempt["subgoal_count"] = enriched["subgoal_count"]
        attempt["failure_progress"] = enriched["failure_progress"]
        candidates.append(enriched)
    if not candidates:
        return None
    best_progress = min(failure_frontier_rank(item)[:3] for item in candidates)
    return next(
        item for item in reversed(candidates)
        if failure_frontier_rank(item)[:3] == best_progress
    )


def resume_failure_record(attempt: dict, previous_proof: str) -> dict:
    record = {
        "failure_kind": "isabelle_proof_search_timeout",
        "wall_ms": attempt.get("wall_ms"),
        "subgoal_count": attempt.get("subgoal_count"),
        "previous_failed_proof": previous_proof,
    }
    if attempt.get("returncode") == 124:
        return record
    record["failure_kind"] = "isabelle_semantic_failure"
    record["diagnostic_excerpt"] = (
        attempt.get("diagnostic") or attempt_diagnostic(attempt)
    )
    return record

def check_source(campaign_dir: Path, source: str, label: str) -> dict:
    check_dir = AUDIT_TRAIL.run_dir / "isabelle"
    check_dir.mkdir(parents=True, exist_ok=True)
    path = check_dir / f"{label}.thy"
    source = re.sub(r"^theory ArchAcc_R$", f"theory {label}", source, count=1, flags=re.M)
    path.write_text(source, encoding="utf-8")
    started = time.time()
    process = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "process",
        "-l", PARENT_SESSION, *dependency_args(campaign_dir),
        "-d", "/sel4-project/verification/l4v", "-T",
        "/workspace/" + str(path.with_suffix("").relative_to(REPO)),
    ], timeout=180)
    log = check_dir / f"{label}.log"
    raw_diagnostic = process.stdout + process.stderr
    log.write_text(raw_diagnostic, encoding="utf-8")
    diagnostic = isabelle_failure_diagnostic(raw_diagnostic, process.returncode, 180)
    record = {
        "verdict": "pass" if process.returncode == 0 else "fail",
        "returncode": process.returncode,
        "wall_ms": int((time.time() - started) * 1000),
        "source": str(path.relative_to(REPO)),
        "source_sha256": sha256_path(path),
        "log": str(log.relative_to(REPO)),
        "diagnostic": diagnostic,
        "subgoal_count": diagnostic_subgoal_count(raw_diagnostic),
    }
    if AUDIT_TRAIL is None:
        raise RuntimeError("theory audit trail was not initialized")
    AUDIT_TRAIL.record_isabelle(label, record)
    immutable_event = AUDIT_TRAIL.events()[-1]
    record["source"] = immutable_event["source"]
    record["log"] = immutable_event["log"]
    return record



def check_plan_statements(campaign_dir: Path, payload: dict, plan: dict,
                          label: str) -> dict:
    support_by_id = {item["id"]: item for item in plan["support_nodes"]}
    contracts = {item["id"]: item for item in payload["fixed_contracts"]}
    declarations = [
        support_by_id[node_id]["statement"].rstrip() + "\n  oops"
        for node_id in plan["support_order"]
    ] + [
        contracts[node_id]["statement"].rstrip() + "\n  oops"
        for node_id in plan["contract_order"]
    ]
    source = "\n".join([
        f"theory {label}",
        'imports "RegenRefineARM_L8.SubMonad_R"',
        "begin",
        "context begin interpretation Arch .",
        *(item["statement"] for item in CONTRACT_LOCAL_FRAMEWORK),
        *declarations,
        "end",
        "end",
        "",
    ])
    check_dir = AUDIT_TRAIL.run_dir / "statement-shell"
    check_dir.mkdir(parents=True, exist_ok=True)
    source_path = check_dir / f"{label}.thy"
    log_path = check_dir / f"{label}.log"
    source_path.write_text(source, encoding="utf-8")
    started = time.time()
    process = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "process",
        "-l", PARENT_SESSION, *dependency_args(campaign_dir),
        "-d", "/sel4-project/verification/l4v", "-T",
        "/workspace/" + str(source_path.with_suffix("").relative_to(REPO)),
    ], timeout=600)
    diagnostic = process.stdout + process.stderr
    log_path.write_text(diagnostic, encoding="utf-8")
    result = {
        "verdict": "pass" if process.returncode == 0 else "fail",
        "returncode": process.returncode,
        "wall_ms": int((time.time() - started) * 1000),
        "source": str(source_path.relative_to(REPO)),
        "source_sha256": sha256_path(source_path),
        "log": str(log_path.relative_to(REPO)),
        "log_sha256": sha256_path(log_path),
        "diagnostic": diagnostic[-16000:],
    }
    if AUDIT_TRAIL is None:
        raise RuntimeError("theory audit trail was not initialized")
    AUDIT_TRAIL.record_isabelle(label, result)
    return result


def repair_prompt(payload: dict, plan: dict, node_id: str, proof: str,
                  diagnostic: str, green_proofs: dict[str, str],
                  frontier: dict | None = None) -> str:
    diagnostic = bounded_repair_diagnostic(diagnostic)
    context, scoped_plan = scoped_proof_context(payload, plan, [node_id])
    frontier_instruction = ""
    if frontier:
        frontier_instruction = (
            "\n\nBEST FAILED FRONTIER (machine checked):\n"
            + json.dumps({
                "subgoal_count": frontier.get("subgoal_count"),
                "returncode": frontier.get("returncode"),
                "wall_ms": frontier.get("wall_ms"),
                "source": frontier.get("source"),
                "log": frontier.get("log"),
                "proof_sha256": frontier.get("proof_sha256"),
            }, ensure_ascii=False, separators=(",", ":"))
            + "\nPreserve the terminating outer proof strategy and repair only the "
              "reported residual branches. A candidate with more subgoals or a "
              "timeout will be rejected and this frontier restored. For three or "
              "fewer constructor-local goals, use explicit bounded branch proofs. "
              "Do not combine pte_relation_aligned_def, pte_align'_def, and "
              "vs_ptr_align_def with wpsimp or fastforce. If one shared residual "
              "fact is the real boundary, return one nonrecursive local_helper leaf. "
              "When Isabelle reports Failed to apply proof method at a command, "
              "no tactic after that command ran: put any matching normalization "
              "before the failed command or revise the helper boundary."
        )
    if AUDIT_TRAIL is not None:
        context["proof_prefix_probe_evidence"] = load_prefix_probe_evidence(
            AUDIT_TRAIL.run_dir, [node_id])
    return """Repair one failed Isabelle proof in a strict seL4 regeneration run.
Use only the supplied payload and diagnostic. Do not use tools, files, human
Refine theorem names, sorry, oops, or axioms. Prefer repairing the proof body.
You may add one Refine.ArchAcc_R-owned leaf helper only when the residual clearly
justifies it; its name must begin regen_. Do not propose an upstream helper here.

Never guess `_def` names: use exact supplied premise names. Treat an undefined
fact as a syntax/retrieval error before proposing a semantic helper. Prefer a
matching Isabelle-verified residual hint when supplied.
Declaration records of kind `abbreviation` are syntax only, not named facts.
If `wpsimp` leaves schematic no_fail subgoals such as ?R21/?P12, do not repeat
the same outer method. Switch to an explicit `no_fail_pre` proof, run `wp`, and
discharge lookupAround2, projection, object-size, and alignment obligations with
the exact supplied generated facts.

Return only JSON, either:
{"action":"proof","proof":"by ...","residual_explanation":"..."}
or:
{"action":"local_helper","name":"regen_...","statement":"lemma regen_...: ...","dependencies":["existing green node or legal premise IDs"],"proof":"by ...","contract_proof":"by ...","residual_explanation":"..."}
For `action=local_helper`, return `"dependencies":[]`. The harness parses the
proof and deterministically adds every uniquely resolved legal citation edge.
Never copy, abbreviate, or reconstruct premise hashes from an earlier response.

PAYLOAD:
""" + json.dumps(context, ensure_ascii=False, separators=(",", ":")) + \
        "\n\nPLAN:\n" + json.dumps(scoped_plan, ensure_ascii=False, separators=(",", ":")) + \
        "\n\nMACHINE-GREEN SIBLING TEMPLATES (adapt structure; cite only declared dependencies):\n" + \
        json.dumps(green_proof_templates(plan, green_proofs, [node_id]), ensure_ascii=False, separators=(",", ":")) + \
        "\n\nVERIFIED STRUCTURAL SIBLING PROOFS (strategy evidence only; never an implicit theorem dependency):\n" + \
        json.dumps(structural_sibling_proof_templates(
            plan, green_proofs, [node_id],
        ), ensure_ascii=False, separators=(",", ":")) + \
        f"\n\nFAILED NODE: {node_id}\nCURRENT PROOF:\n{proof}\n\nISABELLE DIAGNOSTIC:\n{diagnostic}" + \
        residual_strategy_hint(diagnostic) + \
        frontier_instruction


def helper_bundle_prompt(
        payload: dict, plan: dict, contract_id: str, records: list[dict],
        diagnostic: str, prior: dict | None = None,
        source_bundle: dict | None = None) -> str:
    contract = next(item for item in payload["fixed_contracts"] if item["id"] == contract_id)
    contract_node = next(item for item in plan["contract_nodes"] if item["id"] == contract_id)
    repair = ""
    if prior is not None:
        repair = (
            "\n\nREPAIR THE PREVIOUS V2 BUNDLE. Return the complete replacement "
            "four-node bundle, not a patch. Preserve declarations not implicated "
            "by the validator or Isabelle diagnostic.\nPREVIOUS V2 BUNDLE:\n"
            + json.dumps(prior, ensure_ascii=False, separators=(",", ":"))
        )
    source = ""
    if source_bundle is not None:
        source = (
            "\n\nPRIOR MACHINE-AUDITED BUNDLE (statement/interface evidence only):\n"
            + json.dumps(source_bundle, ensure_ascii=False, separators=(",", ":"))
        )
    return """Plan one finite, contract-scoped helper micro-DAG for a strict seL4
Refine regeneration run. Use only this prompt. Do not use tools, files, remembered
human Refine declarations, human proof bodies, sorry, oops, or axioms.

This is a human-informed HARNESS revision, not a human-proof prompt. The generic
heap-relation update schema below came from post-failure harness analysis. No
human target tactic sequence or local theorem body is available to you.

Return exactly four helpers, in this topological order:
1. domain_preservation: derive that the concrete PDE address is already in the
   concrete heap domain from the concrete parent guard.
2. changed_point_relation plus same_owner_unchanged_region: retain the concrete
   guard pde_at' p s' (domain membership alone does not provide address
   alignment), establish the updated PageDirectory slot, and preserve the other
   slots owned by that PageDirectory.
3. foreign_owner_non_interference: use the abstract parent guard pspace_aligned
   to establish that cuts owned by other abstract objects do not hit the updated
   concrete address.
4. pspace_relation_composition: combine helpers 1-3 into the final updated
   pspace_relation interface.

Every helper name must begin `regen_setObject_PD_h6_`. The bundle is frozen
atomically and cannot grow during proving. The fourth helper must depend on the
first three helpers and its statement must retain `pspace_aligned s`,
`pde_at' p s'`, the incoming pspace_relation, the PageDirectory lookup, address
normalization equations, and pde_relation_aligned.

For every explicit assumption, include a premise_provenance entry recording the
assumption text, one origin, and the frame obligation that consumes it. Allowed
origins are parent_abstract_guard, parent_concrete_guard,
state_relation_projection, operation_semantics, accessible_premise, and
earlier_bundle_helper. In particular, `pspace_aligned s` must have origin
parent_abstract_guard and be used for foreign_owner_non_interference.

Every statement must be an exact Isabelle `lemma regen_...: ...` declaration.
Keep the fixed contract unchanged. Do not use `corres` in helper conclusions.
Dependencies must be exact IDs from LEGAL RECORDS or IDs of earlier helpers in
this returned order. Do not guess `_def` facts; cite only candidate names exposed
by LEGAL RECORDS.

Return only JSON:
{
  "schema_version": 2,
  "theory": "Refine.ArchAcc_R",
  "contract_id": "...",
  "helpers": [
    {
      "name": "regen_setObject_PD_h6_...",
      "statement": "lemma regen_setObject_PD_h6_...:\\n  \\\"...\\\"",
      "frame_obligations": ["domain_preservation"],
      "dependencies": ["exact-id"],
      "premise_provenance": [
        {
          "assumption": "pde_at' p s'",
          "origin": "parent_concrete_guard",
          "used_for": ["domain_preservation"]
        }
      ],
      "rationale": "what this node proves and how the consumer uses it"
    }
  ],
  "composition_strategy": "how helper 4 composes helpers 1-3"
}

FIXED CONTRACT:
""" + json.dumps(contract, ensure_ascii=False, separators=(",", ":")) + \
        "\n\nCURRENT CONTRACT NODE:\n" + json.dumps(
            contract_node, ensure_ascii=False, separators=(",", ":"),
        ) + "\n\nLEGAL RECORDS:\n" + json.dumps(
            records, ensure_ascii=False, separators=(",", ":"),
        ) + "\n\nMACHINE-CHECKED PRIOR HELPER RESIDUAL:\n" + \
        bounded_repair_diagnostic(diagnostic) + source + repair


def helper_bundle_records(payload: dict, diagnostic: str) -> list[dict]:
    records = (
        payload["retrieved_legal_premises"]
        + payload["retrieved_legal_generated_premises"]
        + payload["fixed_framework_declarations"]
    )
    exact_names = {
        "a_type", "archObjSize", "dom_in", "dom_insert_absorb",
        "ko_wp_at'", "mask_alignment_ugliness",
        "mask_pd_bits_inner_beauty", "more_pd_inner_beauty",
        "obj_bits", "pageBits", "pd_bits", "pde_relation",
        "pde_relation_aligned", "pspace_alignedD", "pspace_relation",
        "regen_obj_relation_cuts_def2", "regen_obj_relation_cuts_def3",
        "regen_pspace_dom_update", "state_relation", "test_bit_size",
        "typ_at'",
    }
    exact = [
        item for item in records
        if exact_names & (
            set(item.get("candidate_names", [])) | {item.get("name", "")}
        )
    ]
    ranked = bm25(
        records,
        "PageDirectory PDE pde_at domain pde_relation pspace_relation "
        "pspace_aligned foreign owner noninterference relation cuts "
        "pspace_dom kheap ksPSpace update mask alignment "
        + diagnostic[-6000:],
        40,
    )
    by_id = {item["id"]: item for item in exact + ranked}
    return [by_id[key] for key in sorted(by_id)]


def normalize_helper_bundle_dependency_ids(response: dict) -> list[dict]:
    names = [
        item.get("name", "") for item in response.get("helpers", [])
    ]
    known = {
        name: f"support:{THEORY}:{name}" for name in names if name
    }
    changes = []
    for index, item in enumerate(response.get("helpers", [])):
        legal_earlier = set(names[:index])
        normalized = []
        for dependency in item.get("dependencies", []):
            replacement = (
                known[dependency]
                if dependency in legal_earlier else dependency
            )
            normalized.append(replacement)
            if replacement != dependency:
                changes.append({
                    "helper": item.get("name"),
                    "from": dependency,
                    "to": replacement,
                })
        item["dependencies"] = normalized
    return changes


def helper_bundle_applicability_report(response: dict, contract: dict) -> dict:
    if not (
            response.get("schema_version") == 2
            and response.get("theory") == THEORY
            and response.get("contract_id") == contract["id"]
    ):
        raise ValueError("invalid v2 helper-bundle response identity")
    items = response.get("helpers", [])
    if len(items) != 4:
        raise ValueError("v2 helper bundle must contain exactly four nodes")

    coverage = {
        obligation
        for item in items
        for obligation in item.get("frame_obligations", [])
    }
    if coverage != HELPER_BUNDLE_V2_OBLIGATIONS:
        raise ValueError(
            "v2 helper bundle frame coverage mismatch: "
            f"missing={sorted(HELPER_BUNDLE_V2_OBLIGATIONS - coverage)} "
            f"extra={sorted(coverage - HELPER_BUNDLE_V2_OBLIGATIONS)}"
        )

    expected_shapes = [
        {"domain_preservation"},
        {"changed_point_relation", "same_owner_unchanged_region"},
        {"foreign_owner_non_interference"},
        {"pspace_relation_composition"},
    ]
    for index, (item, expected) in enumerate(zip(items, expected_shapes), start=1):
        actual = set(item.get("frame_obligations", []))
        if actual != expected:
            raise ValueError(
                f"v2 helper {index} obligation shape mismatch: "
                f"expected={sorted(expected)} actual={sorted(actual)}"
            )
        provenance = item.get("premise_provenance", [])
        if not provenance:
            raise ValueError(f"v2 helper {index} lacks premise provenance")
        for entry in provenance:
            if entry.get("origin") not in HELPER_BUNDLE_V2_ORIGINS:
                raise ValueError(
                    f"v2 helper {index} has invalid premise origin: "
                    f"{entry.get('origin')}"
                )
            used_for = set(entry.get("used_for", []))
            if not used_for or not used_for <= HELPER_BUNDLE_V2_OBLIGATIONS:
                raise ValueError(
                    f"v2 helper {index} has invalid premise use: "
                    f"{sorted(used_for)}"
                )

    statement = contract.get("statement", "")
    if "pspace_aligned" not in statement or "pde_at'" not in statement:
        raise ValueError("fixed parent contract lacks required H6 guard atoms")

    owner = items[1]
    if "pde_at'" not in owner.get("statement", ""):
        raise ValueError(
            "changed/same-owner helper must retain pde_at' for address alignment"
        )
    if not any(
            entry.get("origin") == "parent_concrete_guard"
            and "pde_at'" in entry.get("assumption", "")
            and (
                "changed_point_relation" in entry.get("used_for", [])
                or "same_owner_unchanged_region" in entry.get("used_for", [])
            )
            for entry in owner.get("premise_provenance", [])
    ):
        raise ValueError(
            "changed/same-owner helper lacks concrete-guard alignment provenance"
        )

    foreign = items[2]
    if "pspace_aligned" not in foreign.get("statement", ""):
        raise ValueError(
            "foreign-owner helper must retain pspace_aligned in its statement"
        )
    foreign_alignment = [
        entry for entry in foreign.get("premise_provenance", [])
        if "pspace_aligned" in entry.get("assumption", "")
    ]
    if not any(
            entry.get("origin") == "parent_abstract_guard"
            and "foreign_owner_non_interference" in entry.get("used_for", [])
            for entry in foreign_alignment
    ):
        raise ValueError(
            "foreign-owner helper lacks parent-guard provenance for pspace_aligned"
        )

    domain = items[0]
    if "pde_at'" not in domain.get("statement", ""):
        raise ValueError("domain helper must retain the concrete pde_at' guard")
    if not any(
            entry.get("origin") == "parent_concrete_guard"
            and "pde_at'" in entry.get("assumption", "")
            for entry in domain.get("premise_provenance", [])
    ):
        raise ValueError("domain helper lacks concrete-parent-guard provenance")

    composition = items[3]
    composition_statement = composition.get("statement", "")
    for required in (
            "pspace_relation", "pspace_aligned", "pde_at'",
            "pde_relation_aligned",
    ):
        if required not in composition_statement:
            raise ValueError(
                f"composition helper statement drops required premise: {required}"
            )
    earlier_ids = {
        f"support:{THEORY}:{item['name']}" for item in items[:3]
    }
    if not earlier_ids <= set(composition.get("dependencies", [])):
        raise ValueError(
            "composition helper must depend on all three earlier H6 helpers"
        )

    origins = {
        entry.get("origin")
        for item in items
        for entry in item.get("premise_provenance", [])
    }
    required_origins = {
        "parent_abstract_guard", "parent_concrete_guard",
        "state_relation_projection", "operation_semantics",
    }
    if not required_origins <= origins:
        raise ValueError(
            "v2 helper bundle provenance coverage mismatch: "
            f"missing={sorted(required_origins - origins)}"
        )
    return {
        "schema_version": 1,
        "theory": THEORY,
        "contract_id": contract["id"],
        "verdict": "pass",
        "frame_obligations": sorted(coverage),
        "premise_origins": sorted(origins),
        "parent_guard_atoms": ["pspace_aligned", "pde_at'"],
        "foreign_owner_guard_preserved": True,
        "composition_dependencies_closed": True,
    }


def apply_helper_bundle_response(
        base_plan: dict, payload: dict, contract_id: str, response: dict,
        legal_record_ids: set[str]) -> tuple[dict, list[str]]:
    dependency_normalizations = normalize_helper_bundle_dependency_ids(
        response,
    )
    if dependency_normalizations and AUDIT_TRAIL is not None:
        AUDIT_TRAIL.record_harness(
            "helper-bundle-dependency-id-normalization",
            details={"changes": dependency_normalizations},
        )
    contract = next(
        item for item in payload["fixed_contracts"] if item["id"] == contract_id
    )
    helper_bundle_applicability_report(response, contract)
    items = response["helpers"]
    names = [item.get("name", "") for item in items]
    if len(names) != len(set(names)):
        raise ValueError("helper bundle names must be unique")

    plan = json.loads(json.dumps(base_plan))
    contract_node = next(
        item for item in plan["contract_nodes"] if item["id"] == contract_id
    )
    existing_ids = {item["id"] for item in plan["support_nodes"]}
    prior_bundle_ids: set[str] = set()
    helper_ids = []
    for item in items:
        name = item.get("name", "")
        statement = item.get("statement", "").strip()
        helper_id = f"support:{THEORY}:{name}"
        if (
                not name.startswith("regen_setObject_PD_h6_")
                or helper_id in existing_ids
                or not statement.startswith(f"lemma {name}")
                or FORBIDDEN_RE.search(statement)
                or re.search(r"(?<![A-Za-z0-9_'])corres(?![A-Za-z0-9_'])", statement)
        ):
            raise ValueError(f"invalid or non-reducing H6 declaration: {name}")
        dependencies = item.get("dependencies", [])
        if len(dependencies) != len(set(dependencies)):
            raise ValueError(f"duplicate helper dependencies: {name}")
        if not set(dependencies) <= legal_record_ids | prior_bundle_ids:
            illegal = sorted(set(dependencies) - legal_record_ids - prior_bundle_ids)
            raise ValueError(
                f"illegal or forward helper dependencies for {name}: {illegal}"
            )
        node = {
            "id": helper_id,
            "name": name,
            "statement": statement,
            "ownership": THEORY,
            "family": contract_node["family"],
            "obligation_kind": item["frame_obligations"][0],
            "frame_obligations": item["frame_obligations"],
            "premise_provenance": item["premise_provenance"],
            "dependencies": dependencies,
            "consumers": [contract_id],
            "rationale": item.get(
                "rationale", "contract-scoped H6 relation component",
            ),
            "origin": "audited_atomic_helper_bundle_v2",
        }
        plan["support_nodes"].append(node)
        plan["support_order"].append(helper_id)
        helper_ids.append(helper_id)
        prior_bundle_ids.add(helper_id)
    contract_node["dependencies"] = list(dict.fromkeys(
        contract_node.get("dependencies", []) + helper_ids
    ))
    contract_node["strategy"] = response.get(
        "composition_strategy", contract_node.get("strategy", ""),
    )
    validate_plan(plan, payload)
    return plan, helper_ids


def archive_helper_bundle_cutover_state(
        planner_dir: Path, contract_id: str, helper_ids: list[str]) -> tuple[Path, list[Path]]:
    archive_dir = planner_dir / "helper-bundle-precutover"
    archive_dir.mkdir(parents=True, exist_ok=True)
    state_names = (
        "attempts.json", "proofs.partial.json", "best-failed-frontiers.json",
        "best-failed-runtime-leaves.json", "latest-runtime-phase-rejections.json",
    )
    archived = []
    removed = {}
    old_helper_ids = {
        f"support:{THEORY}:regen_setObject_PD_corres_residual",
        f"support:{THEORY}:regen_setObject_PD_update_obligation",
    }
    for name in state_names:
        path = planner_dir / name
        if not path.is_file():
            continue
        snapshot = archive_dir / name
        shutil.copy2(path, snapshot)
        archived.append(snapshot)
        value = read_json(path)
        count = 0
        if name == "attempts.json":
            before = value
            value = [
                item for item in before
                if not (
                    item.get("node_id") == contract_id
                    or item.get("contract_id") == contract_id
                    or item.get("node_id") in old_helper_ids
                )
            ]
            count = len(before) - len(value)
        elif name == "proofs.partial.json":
            before = value.get("proofs", [])
            value["proofs"] = [
                item for item in before
                if item.get("node_id") != contract_id
                and item.get("node_id") not in old_helper_ids
            ]
            count = len(before) - len(value["proofs"])
        elif name == "best-failed-frontiers.json":
            before = value.get("frontiers", [])
            value["frontiers"] = [
                item for item in before if item.get("node_id") != contract_id
            ]
            count = len(before) - len(value["frontiers"])
        elif name == "best-failed-runtime-leaves.json":
            before = value.get("frontiers", [])
            value["frontiers"] = [
                item for item in before if item.get("contract_id") != contract_id
            ]
            count = len(before) - len(value["frontiers"])
        else:
            before = value.get("rejections", [])
            value["rejections"] = [
                item for item in before if item.get("contract_id") != contract_id
            ]
            count = len(before) - len(value["rejections"])
        write_json(path, value)
        removed[name] = count
    marker = planner_dir / "helper-bundle-cutover.json"
    write_json(marker, {
        "schema_version": 1,
        "theory": THEORY,
        "contract_id": contract_id,
        "helper_ids": helper_ids,
        "archived_state": [str(path.relative_to(REPO)) for path in archived],
        "removed_records": removed,
        "reason": "replace monolithic whole-corres runtime leaf and remove non-green proof state",
    })
    return marker, archived

def load_helper_bundle_source_evidence(
        campaign_dir: Path, contract: dict,
) -> tuple[str, dict | None]:
    if HELPER_BUNDLE_SOURCE_GENERATION is None:
        return "", None
    if AUDIT_TRAIL is None:
        raise RuntimeError("theory audit trail was not initialized")
    source_dir = (
        campaign_dir / "audit" / "ArchAcc_R"
        / HELPER_BUNDLE_SOURCE_GENERATION
    )
    identity = source_dir / "generation-id.txt"
    if (
            not source_dir.is_dir()
            or not identity.is_file()
            or identity.read_text(encoding="utf-8").strip()
            != HELPER_BUNDLE_SOURCE_GENERATION
    ):
        raise ValueError(
            "invalid helper-bundle source generation: "
            f"{HELPER_BUNDLE_SOURCE_GENERATION}"
        )
    source_bundle_path = (
        source_dir / "planner" / f"helper-bundle-{contract['name']}.json"
    )
    attempts_path = source_dir / "planner" / "attempts.json"
    if not source_bundle_path.is_file() or not attempts_path.is_file():
        raise ValueError(
            "source generation lacks helper bundle or attempt ledger"
        )
    source_bundle = read_json(source_bundle_path)
    candidate_names = {
        item.get("name")
        for item in source_bundle.get("helpers", [])
        if (
            "pspace_relation_update" in item.get("obligation_kind", "")
            or "changed_point_relation" in item.get(
                "frame_obligations", []
            )
            or item.get("name")
            == "regen_pspace_relation_update_page_directory_pde"
        )
    }
    source_attempts = read_json(attempts_path)
    failed_attempt = next((
        item for item in reversed(source_attempts)
        if (
            item.get("verdict") == "fail"
            and item.get("returncode") != 124
            and item.get("node_id", "").rsplit(":", 1)[-1]
            in candidate_names
            and item.get("log")
        )
    ), None) or next((
        item for item in reversed(source_attempts)
        if (
            item.get("verdict") == "fail"
            and item.get("node_id", "").rsplit(":", 1)[-1]
            in candidate_names
            and item.get("log")
        )
    ), None)
    if failed_attempt is None:
        raise ValueError(
            "source generation lacks a failed pspace-update helper check"
        )
    log_path = Path(failed_attempt["log"])
    if not log_path.is_absolute():
        log_path = REPO / log_path
    if not log_path.is_file():
        raise ValueError(f"source helper residual log is missing: {log_path}")
    diagnostic = log_path.read_text(encoding="utf-8", errors="replace")
    evidence_path = AUDIT_TRAIL.run_dir / "planner" / (
        "helper-bundle-source-evidence.json"
    )
    write_json(evidence_path, {
        "schema_version": 1,
        "theory": THEORY,
        "contract_id": contract["id"],
        "experiment_mode": "human-informed-harness-revision",
        "human_proof_exposed_to_generator": False,
        "source_generation_id": HELPER_BUNDLE_SOURCE_GENERATION,
        "source_bundle": str(source_bundle_path.relative_to(REPO)),
        "source_bundle_sha256": sha256_path(source_bundle_path),
        "source_attempt_ledger": str(attempts_path.relative_to(REPO)),
        "source_attempt_ledger_sha256": sha256_path(attempts_path),
        "source_residual_log": str(log_path.relative_to(REPO)),
        "source_residual_log_sha256": sha256_path(log_path),
        "source_node_id": failed_attempt["node_id"],
        "source_repair_round": failed_attempt.get("repair_round"),
        "source_verdict": failed_attempt["verdict"],
    })
    AUDIT_TRAIL.record_harness(
        "helper-bundle-source-evidence",
        artifacts=[
            evidence_path, source_bundle_path, attempts_path, log_path,
        ],
        details={
            "contract_id": contract["id"],
            "source_generation_id": HELPER_BUNDLE_SOURCE_GENERATION,
            "source_node_id": failed_attempt["node_id"],
            "human_proof_exposed_to_generator": False,
        },
    )
    return diagnostic, source_bundle


def maybe_replan_helper_bundle(
        campaign_dir: Path, payload: dict, plan: dict, model: str, timeout: int,
) -> tuple[dict, bool]:
    if HELPER_BUNDLE_CONTRACT is None:
        return plan, False
    if AUDIT_TRAIL is None:
        raise RuntimeError("theory audit trail was not initialized")
    contract = next((
        item for item in payload["fixed_contracts"]
        if item["name"] == HELPER_BUNDLE_CONTRACT or item["id"] == HELPER_BUNDLE_CONTRACT
    ), None)
    if contract is None:
        raise ValueError(f"unknown helper-bundle contract: {HELPER_BUNDLE_CONTRACT}")
    contract_id = contract["id"]
    planner_dir = AUDIT_TRAIL.run_dir / "planner"
    bundle_path = planner_dir / f"helper-bundle-{contract['name']}.json"
    existing_bundle = [
        item for item in plan["support_nodes"]
        if item.get("origin") in {
            "audited_atomic_helper_bundle",
            "audited_atomic_helper_bundle_v2",
        }
        and contract_id in item.get("consumers", [])
    ]
    if bundle_path.is_file() and existing_bundle:
        AUDIT_TRAIL.record_harness(
            "helper-bundle-resume", artifacts=[bundle_path], details={
                "contract_id": contract_id,
                "helper_ids": [item["id"] for item in existing_bundle],
            },
        )
        return plan, False

    frontier_path = planner_dir / "best-failed-runtime-leaves.json"
    frontier = read_json(frontier_path) if frontier_path.is_file() else {}
    failed = next((
        item for item in frontier.get("frontiers", [])
        if item.get("contract_id") == contract_id
    ), {})
    diagnostic = failed.get("diagnostic", "")
    source_diagnostic, source_bundle = load_helper_bundle_source_evidence(
        campaign_dir, contract,
    )
    if source_diagnostic:
        diagnostic = source_diagnostic
    if not diagnostic:
        raise ValueError(
            "helper-bundle replan requires a machine-checked current or source residual"
        )
    runtime_records_path = planner_dir / "runtime-premises-9df3f90e6407.json"
    extended_ids = []
    if runtime_records_path.is_file():
        known_ids = {
            item["id"]
            for key in (
                "retrieved_legal_premises", "retrieved_legal_generated_premises",
                "fixed_framework_declarations",
            )
            for item in payload[key]
        }
        for record in read_json(runtime_records_path).get("records", []):
            if record["id"] in known_ids:
                continue
            target = (
                "retrieved_legal_generated_premises"
                if record["id"].startswith("generated-premise:")
                else "retrieved_legal_premises"
            )
            payload[target].append(record)
            known_ids.add(record["id"])
            extended_ids.append(record["id"])
    if extended_ids:
        AUDIT_TRAIL.record_harness(
            "helper-bundle-residual-premise-extension",
            artifacts=[runtime_records_path], details={
                "contract_id": contract_id,
                "record_ids": extended_ids,
                "record_count": len(extended_ids),
            },
        )
        payload.setdefault("_helper_bundle_extension_ids", []).extend(
            extended_ids)
    records = helper_bundle_records(payload, diagnostic)
    legal_ids = {item["id"] for item in records}
    request = helper_bundle_prompt(
        payload, plan, contract_id, records, diagnostic,
        source_bundle=source_bundle,
    )
    if len(request) > CONTEXT_LIMIT:
        raise SystemExit("helper-bundle planning prompt exceeds context limit")
    recorded_plans = sorted(
        (AUDIT_TRAIL.run_dir / "llm").glob(
            f"*-02b-helper-bundle-{contract['name']}.final.txt"
        )
    )
    if recorded_plans:
        response = parse_json(recorded_plans[-1].read_text(encoding="utf-8"))
        AUDIT_TRAIL.record_harness(
            "helper-bundle-plan-resume", artifacts=[recorded_plans[-1]], details={
                "contract_id": contract_id,
                "source": str(recorded_plans[-1].relative_to(REPO)),
            },
        )
    else:
        response = parse_json(planner_call(
            request, planner_dir / f"02b-helper-bundle-{contract['name']}",
            model=model, timeout=timeout,
        ))
    base_plan = json.loads(json.dumps(plan))
    validation_round = 0
    while True:
        try:
            candidate, helper_ids = apply_helper_bundle_response(
                base_plan, payload, contract_id, response, legal_ids,
            )
            break
        except ValueError as error:
            validation_round += 1
            if validation_round > 2:
                raise ValueError(
                    f"bounded helper-bundle validation did not converge: {error}"
                ) from error
            repair_request = helper_bundle_prompt(
                payload, base_plan, contract_id, records,
                diagnostic + "\n\nHARNESS BUNDLE VALIDATOR RESIDUAL:\n" + str(error),
                prior=response, source_bundle=source_bundle,
            )
            if len(repair_request) > CONTEXT_LIMIT:
                raise SystemExit("helper-bundle validator repair exceeds context limit")
            response = parse_json(planner_call(
                repair_request,
                planner_dir / (
                    f"repair-helper-bundle-{contract['name']}-validation-"
                    f"{validation_round}"
                ),
                model=model, timeout=timeout,
            ))
    applicability_path = (
        planner_dir / f"helper-bundle-applicability-{contract['name']}.json"
    )
    applicability = helper_bundle_applicability_report(response, contract)
    write_json(applicability_path, applicability)
    AUDIT_TRAIL.record_harness(
        "helper-bundle-parent-applicability",
        artifacts=[applicability_path],
        details=applicability,
    )
    statement_attempts = []
    statement_result = check_plan_statements(
        campaign_dir, payload, candidate,
        f"ArchAcc_R_Helper_Bundle_{contract['name']}_0",
    )
    statement_attempts.append({
        key: value for key, value in statement_result.items() if key != "diagnostic"
    })
    repair_round = 0
    while statement_result["verdict"] != "pass" and repair_round < 2:
        repair_round += 1
        repair_request = helper_bundle_prompt(
            payload, base_plan, contract_id, records,
            statement_result["diagnostic"], prior=response,
            source_bundle=source_bundle,
        )
        if len(repair_request) > CONTEXT_LIMIT:
            raise SystemExit("helper-bundle statement repair prompt exceeds context limit")
        response = parse_json(planner_call(
            repair_request,
            planner_dir / f"repair-helper-bundle-{contract['name']}-{repair_round}",
            model=model, timeout=timeout,
        ))
        candidate, helper_ids = apply_helper_bundle_response(
            base_plan, payload, contract_id, response, legal_ids,
        )
        statement_result = check_plan_statements(
            campaign_dir, payload, candidate,
            f"ArchAcc_R_Helper_Bundle_{contract['name']}_{repair_round}",
        )
        statement_attempts.append({
            key: value for key, value in statement_result.items() if key != "diagnostic"
        })
    write_json(bundle_path, response | {
        "statement_attempts": statement_attempts,
        "statement_verdict": statement_result["verdict"],
        "parent_applicability": applicability,
    })
    if statement_result["verdict"] != "pass":
        AUDIT_TRAIL.record_harness(
            "helper-bundle-statement-failed", artifacts=[bundle_path], details={
                "contract_id": contract_id,
                "helper_ids": helper_ids,
                "repair_rounds": repair_round,
            },
        )
        raise SystemExit("helper bundle failed bounded Isabelle statement validation")

    prior_hash = plan.get("frozen_graph_sha256")
    frozen_graph = graph_projection(candidate)
    frozen_hash = sha256_text(json.dumps(
        frozen_graph, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ))
    candidate["frozen_graph_sha256"] = frozen_hash
    plan_path = planner_dir / "lemma-plan.json"
    frozen_path = planner_dir / "lemma-graph.frozen.json"
    write_json(plan_path, candidate)
    write_json(frozen_path, frozen_graph | {"graph_sha256": frozen_hash})
    marker, archived = archive_helper_bundle_cutover_state(
        planner_dir, contract_id, helper_ids,
    )
    AUDIT_TRAIL.record_harness(
        "helper-bundle-cutover", artifacts=[
            bundle_path, applicability_path, plan_path, frozen_path, marker, *archived,
        ], details={
            "contract_id": contract_id,
            "helper_ids": helper_ids,
            "old_graph_sha256": prior_hash,
            "new_graph_sha256": frozen_hash,
            "statement_verdict": "pass",
            "parent_applicability_verdict": applicability["verdict"],
            "bounded_runtime_growth": False,
            "atomic_preproof_bundle": True,
        },
    )
    return candidate, True
def plan_and_prove(campaign_dir: Path, payload: dict, model: str, timeout: int) -> tuple[dict, dict[str, str], list[dict]]:
    if AUDIT_TRAIL is None:
        raise RuntimeError("theory audit trail was not initialized")
    planner_dir = AUDIT_TRAIL.run_dir / "planner"
    planner_dir.mkdir(parents=True, exist_ok=True)
    runtime_rollback_evidence: dict[str, dict] = {}

    family_request = family_analysis_prompt(payload)
    family_path = planner_dir / "family-analysis.json"
    family_resumed = family_path.is_file()
    family_error = None
    if family_resumed:
        family_analysis = read_json(family_path)
        validate_family_analysis(family_analysis, payload)
    else:
        if len(family_request) > CONTEXT_LIMIT:
            raise SystemExit("ArchAcc_R family-analysis prompt exceeds context limit")
        family_analysis = parse_json(planner_call(
            family_request, planner_dir / "00-family-analysis", model=model, timeout=timeout,
        ))
        try:
            validate_family_analysis(family_analysis, payload)
        except ValueError as error:
            family_error = str(error)
        if family_error:
            repair_request = (
                "Repair this Refine.ArchAcc_R family analysis using only the original bounded "
                "planning payload and validator residual. Account for every fixed contract "
                "exactly once, cite only legal interface IDs, and return complete schema-2 "
                "family-analysis JSON only.\n\nVALIDATOR RESIDUAL:\n" + family_error
                + "\n\nCURRENT ANALYSIS:\n" + json.dumps(family_analysis, ensure_ascii=False, separators=(",", ":"))
                + "\n\nPAYLOAD:\n" + json.dumps(dag_planning_payload(payload), ensure_ascii=False, separators=(",", ":"))
            )
            if len(repair_request) > CONTEXT_LIMIT:
                raise SystemExit("ArchAcc_R family-analysis repair exceeds context limit")
            family_analysis = parse_json(planner_call(
                repair_request, planner_dir / "00-family-analysis-repair-1",
                model=model, timeout=timeout,
            ))
            validate_family_analysis(family_analysis, payload)
        write_json(family_path, family_analysis)
    AUDIT_TRAIL.record_harness(
        "family-analysis-resume" if family_resumed else "family-analysis-validation",
        artifacts=[family_path], details={
            "verdict": "pass", "initial_residual": family_error,
            "resumed": family_resumed,
            "family_count": len(family_analysis["families"]),
            "contract_count": sum(len(item["contracts"]) for item in family_analysis["families"]),
        },
    )

    dag_request = planner_prompt(payload, family_analysis)
    draft_path = planner_dir / "lemma-plan.draft.json"
    draft_resumed = draft_path.is_file()
    if draft_resumed:
        draft = read_json(draft_path)
    else:
        if len(dag_request) > CONTEXT_LIMIT:
            raise SystemExit("ArchAcc_R lemma-DAG prompt exceeds context limit")
        draft = parse_json(planner_call(
            dag_request, planner_dir / "01-lemma-dag", model=model, timeout=timeout,
        ))
    draft_normalizations = normalize_dependency_ids(draft, payload)
    write_json(draft_path, draft)
    validation_error = None
    try:
        validate_plan(draft, payload)
    except ValueError as error:
        validation_error = str(error)
    AUDIT_TRAIL.record_harness(
        "draft-resume-validation" if draft_resumed else "draft-validation",
        artifacts=[draft_path], details={
            "verdict": "fail" if validation_error else "pass",
            "residual": validation_error,
            "resumed": draft_resumed,
            "dependency_id_normalizations": draft_normalizations,
        },
    )

    critique_request = plan_critique_prompt(
        payload, family_analysis, draft, validation_error,
    )
    recorded_candidates = sorted(
        (AUDIT_TRAIL.run_dir / "llm").glob("*-02-plan-critique*.final.txt")
    )
    critique_resumed = bool(recorded_candidates)
    critique_changed = False
    critique_round = sum("-repair-" in item.name for item in recorded_candidates)
    if critique_resumed:
        plan = parse_json(recorded_candidates[-1].read_text(encoding="utf-8"))
    else:
        if len(critique_request) > CONTEXT_LIMIT:
            raise SystemExit("ArchAcc_R critique prompt exceeds context limit")
        plan = parse_json(planner_call(
            critique_request, planner_dir / "02-plan-critique", model=model, timeout=timeout,
        ))
    plan_normalizations = normalize_dependency_ids(plan, payload)
    candidate_path = planner_dir / f"lemma-plan.critique-{critique_round}.json"
    write_json(candidate_path, plan)
    critique_error = None
    try:
        validate_plan(plan, payload)
    except ValueError as error:
        critique_error = str(error)
    while critique_error and critique_round < 4:
        critique_round += 1
        critique_changed = True
        repair_critique = plan_critique_prompt(
            payload, family_analysis, plan, critique_error,
        )
        if len(repair_critique) > CONTEXT_LIMIT:
            raise SystemExit("ArchAcc_R critique repair prompt exceeds context limit")
        plan = parse_json(planner_call(
            repair_critique, planner_dir / f"02-plan-critique-repair-{critique_round}",
            model=model, timeout=timeout,
        ))
        plan_normalizations.extend(normalize_dependency_ids(plan, payload))
        candidate_path = planner_dir / f"lemma-plan.critique-{critique_round}.json"
        write_json(candidate_path, plan)
        try:
            validate_plan(plan, payload)
            critique_error = None
        except ValueError as error:
            critique_error = str(error)
    if critique_error:
        raise ValueError(f"bounded plan critique did not converge: {critique_error}")
    AUDIT_TRAIL.record_harness(
        "critique-resume-validation" if critique_resumed else "critique-validation",
        artifacts=[candidate_path], details={
            "verdict": "pass", "resumed": critique_resumed,
            "critique_round": critique_round,
            "dependency_id_normalizations": plan_normalizations,
        },
    )

    certified_plan_path = planner_dir / "lemma-plan.json"
    statement_candidates = sorted(planner_dir.glob("lemma-plan.statement-repair-*.json"))
    prior_frozen_hash = None
    certified_plan_resumed = certified_plan_path.is_file() and not critique_changed
    if certified_plan_resumed:
        plan = read_json(certified_plan_path)
        prior_frozen_hash = plan.get("frozen_graph_sha256")
        runtime_reconciliations = reconcile_runtime_leaves_from_ledger(
            plan, planner_dir / "runtime-leaf-extensions.jsonl",
        )
        if runtime_reconciliations:
            reconciliation_path = planner_dir / "runtime-leaf-ledger-reconciliation.json"
            write_json(reconciliation_path, {
                "schema_version": 1, "theory": THEORY,
                "changes": runtime_reconciliations,
            })
            AUDIT_TRAIL.record_harness(
                "runtime-leaf-ledger-reconciliation",
                artifacts=[reconciliation_path],
                details={
                    "prior_frozen_graph_sha256": prior_frozen_hash,
                    "changes": runtime_reconciliations,
                },
            )
        attempt_history = (
            read_json(planner_dir / "attempts.json")
            if (planner_dir / "attempts.json").is_file() else []
        )
        runtime_rollbacks = rollback_unverified_runtime_leaves(
            plan, attempt_history,
        )
        if runtime_rollbacks:
            for change in runtime_rollbacks:
                helper = change["helper"]
                helper_marker = helper["statement"].rstrip()
                failed_attempt = next((
                    attempt for attempt in reversed(attempt_history)
                    if (
                        attempt.get("verdict") != "pass"
                        and Path(
                            attempt.get("source", "")
                            if Path(attempt.get("source", "")).is_absolute()
                            else REPO / attempt.get("source", "")
                        ).is_file()
                        and helper_marker in Path(
                            attempt.get("source", "")
                            if Path(attempt.get("source", "")).is_absolute()
                            else REPO / attempt.get("source", "")
                        ).read_text(encoding="utf-8", errors="replace")
                    )
                ), None)
                if failed_attempt is None:
                    continue
                evidence = {
                    "helper_id": helper["id"],
                    "helper_statement": helper["statement"],
                    "source": failed_attempt.get("source"),
                    "log": failed_attempt.get("log"),
                    "diagnostic_excerpt": attempt_diagnostic(failed_attempt),
                }
                for consumer in change["consumers"]:
                    runtime_rollback_evidence[consumer] = evidence
            amended_graph = graph_projection(plan)
            amended_hash = sha256_text(json.dumps(
                amended_graph, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ))
            plan["frozen_graph_sha256"] = amended_hash
            frozen_path = planner_dir / "lemma-graph.frozen.json"
            rollback_path = planner_dir / "runtime-leaf-rollbacks.json"
            write_json(certified_plan_path, plan)
            write_json(
                frozen_path, amended_graph | {"graph_sha256": amended_hash},
            )
            write_json(rollback_path, {
                "schema_version": 1, "theory": THEORY,
                "prior_graph_sha256": prior_frozen_hash,
                "amended_graph_sha256": amended_hash,
                "changes": runtime_rollbacks,
            })
            AUDIT_TRAIL.record_harness(
                "provisional-runtime-leaf-rollback", artifacts=[
                    certified_plan_path, frozen_path, rollback_path,
                ], details={
                    "prior_graph_sha256": prior_frozen_hash,
                    "amended_graph_sha256": amended_hash,
                    "changes": runtime_rollbacks,
                },
            )
            prior_frozen_hash = amended_hash
        plan_normalizations.extend(normalize_dependency_ids(plan, payload))
        validate_plan(plan, payload)
        AUDIT_TRAIL.record_harness(
            "certified-plan-resume", artifacts=[certified_plan_path], details={
                "verdict": "pass", "prior_frozen_graph_sha256": prior_frozen_hash,
            },
        )
    elif critique_changed:
        if certified_plan_path.is_file():
            prior_frozen_hash = read_json(certified_plan_path).get("frozen_graph_sha256")
        plan_normalizations.extend(normalize_dependency_ids(plan, payload))
        validate_plan(plan, payload)
        AUDIT_TRAIL.record_harness(
            "certified-plan-superseded-by-critique", artifacts=[candidate_path], details={
                "verdict": "pass",
                "prior_frozen_graph_sha256": prior_frozen_hash,
                "critique_round": critique_round,
            },
        )
    elif statement_candidates:
        plan = read_json(statement_candidates[-1])
        plan_normalizations.extend(normalize_dependency_ids(plan, payload))
        validate_plan(plan, payload)
        AUDIT_TRAIL.record_harness(
            "statement-plan-resume", artifacts=[statement_candidates[-1]],
            details={"verdict": "pass"},
        )
    verified_edge_completions = complete_verified_citation_edges(plan, payload)
    candidate_edge_completions = complete_recorded_candidate_premise_edges(
        plan, payload, AUDIT_TRAIL.run_dir / "llm",
    )
    residual_premise_completions = complete_residual_premise_edges(
        plan, payload, planner_dir / "attempts.json", AUDIT_TRAIL.events(),
    )
    validate_plan(plan, payload)
    if verified_edge_completions:
        edge_path = planner_dir / "verified-residual-edge-completion.json"
        write_json(edge_path, {
            "schema_version": 1, "theory": THEORY,
            "changes": verified_edge_completions,
        })
        AUDIT_TRAIL.record_harness(
            "verified-residual-premise-closure", artifacts=[edge_path], details={
                "edge_count": len(verified_edge_completions),
                "prior_frozen_graph_sha256": prior_frozen_hash,
            },
        )

    if residual_premise_completions:
        residual_edge_path = planner_dir / "residual-premise-edge-completion.json"
        write_json(residual_edge_path, {
            "schema_version": 1, "theory": THEORY,
            "changes": residual_premise_completions,
        })
        evidence_logs = [
            REPO / item["evidence_log"] for item in residual_premise_completions
        ]
        AUDIT_TRAIL.record_harness(
            "residual-premise-retrieval",
            artifacts=[residual_edge_path, *evidence_logs], details={
                "edge_count": len(residual_premise_completions),
                "prior_frozen_graph_sha256": prior_frozen_hash,
            },
        )

    if candidate_edge_completions:
        candidate_edge_path = planner_dir / "recorded-candidate-edge-completion.json"
        write_json(candidate_edge_path, {
            "schema_version": 1, "theory": THEORY,
            "changes": candidate_edge_completions,
        })
        AUDIT_TRAIL.record_harness(
            "recorded-candidate-premise-closure", artifacts=[candidate_edge_path],
            details={"edge_count": len(candidate_edge_completions)},
        )

    plan, helper_bundle_changed = maybe_replan_helper_bundle(
        campaign_dir, payload, plan, model, timeout,
    )
    certified_plan_resumed = certified_plan_resumed and not helper_bundle_changed

    statement_report_path = planner_dir / "statement-shell-report.json"
    statement_gate_resumed = (
        certified_plan_resumed
        and statement_report_path.is_file()
        and read_json(statement_report_path).get("verdict") == "pass"
    )
    if statement_gate_resumed:
        statement_attempts = read_json(statement_report_path).get("attempts", [])
        statement_result = {"verdict": "pass"}
        AUDIT_TRAIL.record_harness(
            "statement-shell-resume", artifacts=[statement_report_path],
            details={"verdict": "pass", "isabelle_rerun": False},
        )
    else:
        statement_attempts = []
        statement_result = check_plan_statements(
            campaign_dir, payload, plan, "ArchAcc_R_Statement_Shell_0",
        )
        statement_attempts.append({key: value for key, value in statement_result.items() if key != "diagnostic"})
        statement_repair_round = 0
        while statement_result["verdict"] != "pass" and statement_repair_round < 4:
            statement_repair_round += 1
            request = statement_plan_repair_prompt(
                payload, family_analysis, plan, statement_result["diagnostic"],
            )
            if len(request) > CONTEXT_LIMIT:
                raise SystemExit("ArchAcc_R statement-plan repair exceeds context limit")
            plan = parse_json(planner_call(
                request, planner_dir / f"repair-statement-plan-{statement_repair_round}",
                model=model, timeout=timeout,
            ))
            plan_normalizations.extend(normalize_dependency_ids(plan, payload))
            validate_plan(plan, payload)
            candidate_path = planner_dir / f"lemma-plan.statement-repair-{statement_repair_round}.json"
            write_json(candidate_path, plan)
            statement_result = check_plan_statements(
                campaign_dir, payload, plan,
                f"ArchAcc_R_Statement_Shell_{statement_repair_round}",
            )
            statement_attempts.append({key: value for key, value in statement_result.items() if key != "diagnostic"})
        write_json(statement_report_path, {
            "schema_version": 1, "theory": THEORY,
            "verdict": statement_result["verdict"], "attempts": statement_attempts,
        })
        if statement_result["verdict"] != "pass":
            raise SystemExit("ArchAcc_R planned declarations failed bounded statement repair")

    frozen_graph = graph_projection(plan)
    frozen_hash = sha256_text(json.dumps(
        frozen_graph, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ))
    plan["planner_input_sha256"] = sha256_text(json.dumps(payload, sort_keys=True))
    plan["family_analysis_sha256"] = sha256_text(json.dumps(
        family_analysis, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ))
    plan["frozen_graph_sha256"] = frozen_hash
    plan["prompt_chars"] = {
        "family_analysis": len(family_request),
        "dag": len(dag_request),
        "critique": len(critique_request),
    }
    plan_path = planner_dir / "lemma-plan.json"
    frozen_path = planner_dir / "lemma-graph.frozen.json"
    write_json(plan_path, plan)
    write_json(frozen_path, frozen_graph | {"graph_sha256": frozen_hash})
    if prior_frozen_hash and prior_frozen_hash != frozen_hash:
        AUDIT_TRAIL.record_harness(
            "frozen-graph-superseded", artifacts=[plan_path, frozen_path], details={
                "old_graph_sha256": prior_frozen_hash,
                "new_graph_sha256": frozen_hash,
                "reason": (
                    "audited premise-citation closure from verified residuals or "
                    "recorded generation transcripts before proof generation resumed"
                ),
            },
        )
    AUDIT_TRAIL.record_harness(
        "frozen-lemma-dag", artifacts=[
            family_path, draft_path, plan_path, frozen_path, statement_report_path,
        ], details={
            "verdict": "pass", "graph_sha256": frozen_hash,
            "support_count": len(plan["support_nodes"]),
            "contract_count": len(plan["contract_nodes"]),
            "dependency_id_normalizations": plan_normalizations,
            "fresh_proofs": FRESH_PROOFS,
        },
    )

    proofs: dict[str, str] = {}
    attempts: list[dict] = []
    proof_call = 0
    reusable_proofs: dict[str, str] = {}
    reuse_origins: dict[str, dict] = {}
    residuals = payload.get("isabelle_residual_history", [])
    for node in plan["support_nodes"]:
        for residual in residuals:
            candidate = residual.get("verified_proof_skeleton", "").strip()
            if (residual.get("required_support_statement") == node["statement"]
                    and valid_proof_body(candidate)):
                reusable_proofs[node["id"]] = candidate
                reuse_origins[node["id"]] = {
                    "generation_id": residual.get("generation_id"),
                    "resolution_status": residual.get("resolution_status"),
                }
                break
    for node in plan["contract_nodes"]:
        residual = next((
            item for item in residuals
            if item["contract_id"] == node["id"]
            and valid_proof_body(item.get("verified_contract_proof_skeleton", ""))
        ), None)
        candidate = (residual or {}).get("verified_contract_proof_skeleton", "").strip()
        if valid_proof_body(candidate):
            reusable_proofs[node["id"]] = candidate
            reuse_origins[node["id"]] = {
                "generation_id": residual.get("generation_id"),
                "resolution_status": residual.get("resolution_status"),
            }
    AUDIT_TRAIL.record_harness(
        "verified-frontier-available",
        artifacts=[campaign_dir / "residual-history" / "ArchAcc_R.json"],
        details={
            "nodes": reuse_origins,
            "activation_enabled": not FRESH_PROOFS,
            "reason": "fresh proof generation requested" if FRESH_PROOFS else "verified reuse enabled",
        },
    )

    attempts_path = planner_dir / "attempts.json"
    partial_path = planner_dir / "proofs.partial.json"
    resumed_green: set[str] = set()
    resume_repair_pending = False
    resume_failure_evidence: dict[str, dict] = {}
    failed_frontiers: dict[str, dict] = {}
    frontier_path = planner_dir / "best-failed-frontiers.json"
    runtime_phase_rejection_path = (
        planner_dir / "latest-runtime-phase-rejections.json"
    )
    runtime_phase_rejections: dict[str, dict] = {}
    if runtime_phase_rejection_path.is_file():
        rejection_state = read_json(runtime_phase_rejection_path)
        runtime_phase_rejections = {
            item["contract_id"]: upgrade_runtime_phase_rejection(item)
            for item in rejection_state.get("rejections", [])
            if item.get("contract_id") in {
                node["id"] for node in plan["contract_nodes"]
            }
        }
        if any(
                item.get("protocol_version") != RUNTIME_PHASE_PROTOCOL_VERSION
                for item in rejection_state.get("rejections", [])
                if item.get("contract_id") in runtime_phase_rejections
        ):
            write_json(runtime_phase_rejection_path, {
                "schema_version": 1,
                "theory": THEORY,
                "rejections": [
                    runtime_phase_rejections[node_id]
                    for node_id in sorted(runtime_phase_rejections)
                ],
            })
            AUDIT_TRAIL.record_harness(
                "runtime-helper-phase-rejection-protocol-migrated",
                artifacts=[runtime_phase_rejection_path],
                details={
                    "protocol_version": RUNTIME_PHASE_PROTOCOL_VERSION,
                    "contracts": sorted(runtime_phase_rejections),
                },
            )
    else:
        events = AUDIT_TRAIL.events()
        valid_contract_ids = {node["id"] for node in plan["contract_nodes"]}
        for event_index, event in enumerate(events):
            if event.get("stage") != "runtime-helper-phase-strategy-rejection":
                continue
            node_id = event.get("details", {}).get("node_id")
            if node_id not in valid_contract_ids:
                continue
            llm_event = next((
                prior for prior in reversed(events[:event_index])
                if prior.get("kind") == "llm"
                and re.match(r"^repair-\d+-\d+$", prior.get("stage", ""))
            ), None)
            final_record = (llm_event or {}).get("artifacts", {}).get("final", {})
            final_path = REPO / final_record.get("path", "")
            if not final_record.get("path") or not final_path.is_file():
                continue
            try:
                rejected_repair = parse_json(final_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            runtime_phase_rejections[node_id] = {
                "contract_id": node_id,
                "protocol_version": RUNTIME_PHASE_PROTOCOL_VERSION,
                "repair_round": event.get("details", {}).get("repair_round"),
                "residual": event.get("details", {}).get("residual", ""),
                "repair": rejected_repair,
                "reconstructed_from_event": event.get("sequence"),
                "reconstructed_from_llm_event": llm_event.get("sequence"),
            }
        if runtime_phase_rejections:
            write_json(runtime_phase_rejection_path, {
                "schema_version": 1,
                "theory": THEORY,
                "rejections": [
                    runtime_phase_rejections[node_id]
                    for node_id in sorted(runtime_phase_rejections)
                ],
            })
            AUDIT_TRAIL.record_harness(
                "runtime-helper-phase-rejection-reconstructed",
                artifacts=[runtime_phase_rejection_path],
                details={
                    "contracts": {
                        node_id: record.get("reconstructed_from_event")
                        for node_id, record
                        in sorted(runtime_phase_rejections.items())
                    },
                },
            )
    if not attempts_path.is_file() and partial_path.is_file():
        reconstructed = []
        for event in AUDIT_TRAIL.events():
            if event.get("kind") != "isabelle":
                continue
            support_match = re.match(
                r"^ArchAcc_R_support_prefix_(\d+)(?:_repair_(\d+))?$",
                event.get("stage", ""),
            )
            contract_match = re.match(
                r"^ArchAcc_R_prefix_(\d+)(?:_repair_(\d+))?$",
                event.get("stage", ""),
            )
            if support_match:
                index = int(support_match.group(1))
                if 1 <= index <= len(plan["support_order"]):
                    reconstructed.append({
                        "node_id": plan["support_order"][index - 1],
                        "role": "support", "verdict": event["verdict"],
                        "returncode": event["returncode"],
                        "source": event.get("source"), "log": event.get("log"),
                        "reconstructed_from_event": event["sequence"],
                    })
            elif contract_match:
                index = int(contract_match.group(1))
                if 1 <= index <= len(plan["contract_order"]):
                    reconstructed.append({
                        "node_id": plan["contract_order"][index - 1],
                        "role": "contract", "verdict": event["verdict"],
                        "returncode": event["returncode"],
                        "source": event.get("source"), "log": event.get("log"),
                        "reconstructed_from_event": event["sequence"],
                    })
        if reconstructed:
            write_json(attempts_path, reconstructed)
            AUDIT_TRAIL.record_harness(
                "attempt-ledger-reconstruction", artifacts=[attempts_path],
                details={"event_count": len(reconstructed)},
            )
    if attempts_path.is_file() and partial_path.is_file():
        attempts = read_json(attempts_path)
        latest_attempt = {}
        for attempt in attempts:
            latest_attempt[attempt["node_id"]] = attempt
        latest_verdict = {node_id: item.get("verdict") for node_id, item in latest_attempt.items()}
        partial = {
            item["node_id"]: item["proof"]
            for item in read_json(partial_path).get("proofs", [])
        }
        current_node_ids = {
            item["id"] for item in plan["support_nodes"] + plan["contract_nodes"]
        }
        statement_by_id = {
            item["id"]: item["statement"]
            for item in payload["fixed_contracts"] + plan["support_nodes"]
        }
        for node_id, attempt in latest_attempt.items():
            if (
                    node_id not in current_node_ids
                    or attempt.get("verdict") == "pass"
            ):
                continue
            frontier = best_failed_frontier(
                attempts, node_id, statement_by_id[node_id],
            )
            if frontier is not None:
                failed_frontiers[node_id] = frontier
                resume_failure_evidence[node_id] = resume_failure_record(
                    frontier, frontier["proof"],
                )
            else:
                resume_failure_evidence[node_id] = resume_failure_record(
                    attempt, partial.get(node_id, ""),
                )
        write_json(attempts_path, attempts)
        for node_id, verdict in latest_verdict.items():
            if node_id not in current_node_ids:
                continue
            candidate = partial.get(node_id, "")
            if verdict == "pass" and valid_proof_body(candidate):
                validate_proof_citations(
                    payload, plan, node_id, candidate,
                    check_fact_existence=False,
                )
                proofs[node_id] = candidate
                resumed_green.add(node_id)
        if failed_frontiers:
            write_json(frontier_path, {
                "schema_version": 1, "theory": THEORY,
                "frontiers": [
                    {"node_id": node_id} | {
                        key: value for key, value in frontier.items()
                        if key in {
                            "verdict", "returncode", "wall_ms", "subgoal_count",
                            "failure_progress",
                            "source", "source_sha256", "log", "proof",
                            "proof_sha256", "diagnostic",
                        }
                    }
                    for node_id, frontier in sorted(failed_frontiers.items())
                ],
            })
            AUDIT_TRAIL.record_harness(
                "best-failed-frontier-reconstructed", artifacts=[frontier_path],
                details={
                    "frontiers": {
                        node_id: {
                            "subgoal_count": frontier.get("subgoal_count"),
                            "failure_progress": frontier.get("failure_progress"),
                            "returncode": frontier.get("returncode"),
                            "source": frontier.get("source"),
                            "proof_sha256": frontier.get("proof_sha256"),
                        }
                        for node_id, frontier in failed_frontiers.items()
                    },
                },
            )
        proof_call = len(list(
            (AUDIT_TRAIL.run_dir / "llm").glob("*-03-proof-generation*.final.txt")
        ))
        resume_repair_pending = any(
            node_id in current_node_ids and verdict != "pass"
            for node_id, verdict in latest_verdict.items()
        )
        AUDIT_TRAIL.record_harness(
            "generation-green-resume", artifacts=[
                attempts_path, partial_path,
                *([frontier_path] if failed_frontiers else []),
            ], details={
                "green_nodes": sorted(resumed_green),
                "best_failed_frontiers": {
                    node_id: frontier.get("subgoal_count")
                    for node_id, frontier in failed_frontiers.items()
                },
                "discarded_red_nodes": sorted(
                    node_id for node_id, verdict in latest_verdict.items()
                    if verdict != "pass"
                ),
            },
        )

    runtime_leaf_frontier_path = planner_dir / "best-failed-runtime-leaves.json"
    failed_runtime_leaf_frontiers = {
        node_id: frontier
        for node_id in plan["contract_order"]
        if (frontier := best_failed_runtime_leaf_frontier(
            attempts, node_id, AUDIT_TRAIL.run_dir,
        )) is not None
    }

    def persist_runtime_leaf_frontiers() -> None:
        write_json(runtime_leaf_frontier_path, {
            "schema_version": 1, "theory": THEORY,
            "frontiers": [
                {"contract_id": node_id} | {
                    key: value for key, value in frontier.items()
                    if key in {
                        "verdict", "returncode", "wall_ms", "subgoal_count",
                        "failure_progress", "source", "source_sha256", "log",
                        "proof", "proof_sha256", "diagnostic", "response",
                        "llm_final",
                    }
                }
                for node_id, frontier
                in sorted(failed_runtime_leaf_frontiers.items())
            ],
        })

    if failed_runtime_leaf_frontiers:
        persist_runtime_leaf_frontiers()
        AUDIT_TRAIL.record_harness(
            "best-failed-runtime-leaf-reconstructed",
            artifacts=[runtime_leaf_frontier_path],
            details={
                "frontiers": {
                    node_id: {
                        "subgoal_count": frontier.get("subgoal_count"),
                        "failure_progress": frontier.get("failure_progress"),
                        "source": frontier.get("source"),
                        "llm_final": frontier.get("llm_final"),
                    }
                    for node_id, frontier
                    in failed_runtime_leaf_frontiers.items()
                },
            },
        )

    def persist_proofs() -> None:
        write_json(planner_dir / "proofs.partial.json", {
            "schema_version": 1, "theory": THEORY,
            "proofs": [{"node_id": item_id, "proof": proof} for item_id, proof in proofs.items()],
        })

    def persist_failed_frontiers() -> None:
        write_json(frontier_path, {
            "schema_version": 1, "theory": THEORY,
            "frontiers": [
                {"node_id": node_id} | {
                    key: value for key, value in frontier.items()
                    if key in {
                        "verdict", "returncode", "wall_ms", "subgoal_count",
                        "failure_progress", "source", "source_sha256", "log", "proof",
                        "proof_sha256", "diagnostic",
                    }
                }
                for node_id, frontier in sorted(failed_frontiers.items())
            ],
        })

    def persist_runtime_phase_rejections() -> None:
        write_json(runtime_phase_rejection_path, {
            "schema_version": 1,
            "theory": THEORY,
            "rejections": [
                runtime_phase_rejections[node_id]
                for node_id in sorted(runtime_phase_rejections)
            ],
        })

    def invalidate_consumer_frontier_for_helper(
            node_id: str, helper_id: str, repair_round: int) -> None:
        prior = failed_frontiers.pop(node_id, None)
        if prior is None:
            return
        persist_failed_frontiers()
        AUDIT_TRAIL.record_harness(
            "consumer-frontier-invalidated-by-helper-revision",
            artifacts=[frontier_path], details={
                "node_id": node_id, "helper_id": helper_id,
                "repair_round": repair_round,
                "invalidated_subgoal_count": prior.get("subgoal_count"),
                "invalidated_failure_progress": prior.get("failure_progress"),
                "invalidated_proof_sha256": prior.get("proof_sha256"),
            },
        )

    def retain_or_restore_frontier(
            node_id: str, result: dict, repair_round: int) -> dict:
        if result["verdict"] == "pass":
            if node_id in failed_frontiers:
                del failed_frontiers[node_id]
                persist_failed_frontiers()
            if node_id in runtime_phase_rejections:
                del runtime_phase_rejections[node_id]
                persist_runtime_phase_rejections()
            return result
        candidate = failed_frontier_record(
            result, proofs[node_id], statement_by_id[node_id],
        )
        for attempt in reversed(attempts):
            if (
                    attempt.get("node_id") == node_id
                    and attempt.get("source") == result.get("source")
                    and attempt.get("verdict") == "fail"
            ):
                attempt["subgoal_count"] = candidate.get("subgoal_count")
                attempt["failure_progress"] = candidate.get("failure_progress")
                write_json(attempts_path, attempts)
                break
        prior = failed_frontiers.get(node_id)
        candidate_progress = failure_frontier_rank(candidate)[:3]
        prior_progress = (
            failure_frontier_rank(prior)[:3] if prior is not None else None
        )
        if prior is None or candidate_progress < prior_progress:
            failed_frontiers[node_id] = candidate
            persist_failed_frontiers()
            AUDIT_TRAIL.record_harness(
                "best-failed-frontier-promoted", artifacts=[frontier_path],
                details={
                    "node_id": node_id, "repair_round": repair_round,
                    "subgoal_count": candidate.get("subgoal_count"),
                    "failure_progress": candidate.get("failure_progress"),
                    "returncode": candidate.get("returncode"),
                    "proof_sha256": candidate.get("proof_sha256"),
                    "prior_subgoal_count": (
                        prior.get("subgoal_count") if prior is not None else None
                    ),
                },
            )
            return result
        if candidate_progress > prior_progress:
            AUDIT_TRAIL.record_harness(
                "repair-regression-restored", artifacts=[frontier_path],
                details={
                    "node_id": node_id, "repair_round": repair_round,
                    "candidate_subgoal_count": candidate.get("subgoal_count"),
                    "candidate_failure_progress": candidate.get("failure_progress"),
                    "candidate_returncode": candidate.get("returncode"),
                    "restored_subgoal_count": prior.get("subgoal_count"),
                    "restored_failure_progress": prior.get("failure_progress"),
                    "restored_returncode": prior.get("returncode"),
                    "restored_proof_sha256": prior.get("proof_sha256"),
                },
            )
            proofs[node_id] = prior["proof"]
            persist_proofs()
            restored = dict(prior)
            candidate_diagnostic = candidate.get("diagnostic", "").rstrip()
            if candidate_diagnostic:
                restored["diagnostic"] = bounded_repair_diagnostic(
                    prior.get("diagnostic", "").rstrip()
                    + "\n\nHARNESS REJECTED REGRESSING CANDIDATE DIAGNOSTIC:\n"
                    + candidate_diagnostic[-12000:]
                )
            return restored
        return result

    def reject_runtime_helper(
            node_id: str, repair_round: int, result: dict,
            repair: dict, residual: str) -> dict:
        AUDIT_TRAIL.record_harness(
            "runtime-helper-candidate-rejection", details={
                "node_id": node_id, "repair_round": repair_round,
                "helper_name": repair.get("name"), "residual": residual,
            },
        )
        rejected = json.dumps(
            repair, ensure_ascii=False, separators=(",", ":"),
        )[-12000:]
        updated = dict(result)
        updated["diagnostic"] = bounded_repair_diagnostic(
            result.get("diagnostic", "").rstrip()
            + "\n\nHARNESS RUNTIME-HELPER CANDIDATE REJECTION: "
            + residual
            + "\nRepair this same provisional leaf; do not return a whole-proof "
              "action.\nREJECTED LOCAL_HELPER RESPONSE:\n"
            + rejected
        )
        return updated

    def reject_runtime_helper_phase(
            node_id: str, repair_round: int, result: dict,
            repair: dict, residual: str) -> dict:
        runtime_phase_rejections[node_id] = {
            "contract_id": node_id,
            "protocol_version": RUNTIME_PHASE_PROTOCOL_VERSION,
            "repair_round": repair_round,
            "residual": residual,
            "repair": repair,
        }
        persist_runtime_phase_rejections()
        AUDIT_TRAIL.record_harness(
            "runtime-helper-phase-rejection-persisted",
            artifacts=[runtime_phase_rejection_path],
            details={
                "node_id": node_id,
                "repair_round": repair_round,
                "residual": residual,
            },
        )
        return reject_runtime_helper(
            node_id, repair_round, result, repair, residual,
        )

    def complete_runtime_helper_dependency_edges(
            node_id: str, repair_round: int, repair: dict, proof: str,
            legal_dependency_ids: set[str]) -> list[str]:
        dependencies, additions = complete_unique_citation_dependencies(
            payload, plan, proof, repair.get("dependencies", []),
            legal_dependency_ids,
        )
        if additions:
            repair["dependencies"] = dependencies
            AUDIT_TRAIL.record_harness(
                "runtime-helper-dependency-edge-completion", details={
                    "node_id": node_id,
                    "repair_round": repair_round,
                    "helper_name": repair.get("name"),
                    "additions": additions,
                    "source": "parsed_candidate_proof_unique_legal_fact",
                },
            )
        return dependencies

    def close_recorded_candidate_edges(stage: str) -> None:
        edge_completions = complete_recorded_candidate_premise_edges(
            plan, payload, AUDIT_TRAIL.run_dir / "llm",
        )
        if not edge_completions:
            return
        validate_plan(plan, payload)
        prior_hash = plan["frozen_graph_sha256"]
        amended_graph = graph_projection(plan)
        amended_hash = sha256_text(json.dumps(
            amended_graph, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ))
        plan["frozen_graph_sha256"] = amended_hash
        edge_path = planner_dir / (
            f"recorded-candidate-edge-completion-{stage}-{amended_hash[:12]}.json"
        )
        write_json(edge_path, {
            "schema_version": 1, "theory": THEORY, "changes": edge_completions,
        })
        plan_path = planner_dir / "lemma-plan.json"
        frozen_path = planner_dir / "lemma-graph.frozen.json"
        write_json(plan_path, plan)
        write_json(frozen_path, amended_graph | {"graph_sha256": amended_hash})
        AUDIT_TRAIL.record_harness(
            "recorded-candidate-premise-closure", artifacts=[edge_path], details={
                "edge_count": len(edge_completions), "stage": stage, "mode": "in-run",
            },
        )
        AUDIT_TRAIL.record_harness(
            "frozen-graph-superseded", artifacts=[plan_path, frozen_path], details={
                "old_graph_sha256": prior_hash,
                "new_graph_sha256": amended_hash,
                "reason": "audited in-run candidate premise-citation closure",
            },
        )

    def activate_reuse(node_id: str) -> bool:
        if FRESH_PROOFS or node_id not in reusable_proofs:
            return False
        validate_proof_citations(
            payload, plan, node_id, reusable_proofs[node_id],
            check_fact_existence=False,
        )
        proofs[node_id] = reusable_proofs[node_id]
        persist_proofs()
        return True

    def generate(target_ids: list[str]) -> None:
        nonlocal proof_call, resume_repair_pending
        target_ids = [node_id for node_id in target_ids if node_id not in proofs]
        if not target_ids:
            return
        proof_call += 1
        if resume_repair_pending:
            stage = f"repair-resume-proof-{proof_call:02d}"
            resume_repair_pending = False
        else:
            stage = "03-proof-generation" if proof_call == 1 else f"03-proof-generation-{proof_call:02d}"
        request = proof_prompt(payload, plan, target_ids, proofs)
        failure_evidence = {
            node_id: resume_failure_evidence[node_id]
            for node_id in target_ids if node_id in resume_failure_evidence
        }
        if failure_evidence:
            request += (
                "\n\nAUDITED RESUME FAILURE EVIDENCE:\n"
                + json.dumps(failure_evidence, ensure_ascii=False, separators=(",", ":"))
                + "\nDo not repeat the proof; address the recorded bounded residual."
            )
        if len(request) > CONTEXT_LIMIT:
            raise SystemExit(f"ArchAcc_R proof prompt exceeds context limit: {target_ids}")
        response = parse_json(planner_call(
            request, planner_dir / stage, model=model, timeout=timeout,
        ))
        generated = validate_proofs(response, set(target_ids))
        close_recorded_candidate_edges(stage)
        for generated_id, proof in generated.items():
            validate_proof_citations(payload, plan, generated_id, proof)
        proofs.update(generated)
        persist_proofs()

    helper_bundle_target_ids = {
        item["id"] for item in plan["support_nodes"]
        if (
            item.get("origin") == "audited_atomic_helper_bundle_v2"
            and HELPER_BUNDLE_CONTRACT in {
                consumer.rsplit(":", 2)[-2]
                if consumer.startswith("contract:") else consumer
                for consumer in item.get("consumers", [])
            }
        )
    }
    if HELPER_BUNDLE_ONLY and len(helper_bundle_target_ids) != 4:
        raise ValueError(
            "bundle-only mode requires exactly four active v2 helper nodes"
        )

    for support_index, node_id in enumerate(plan["support_order"], start=1):
        if HELPER_BUNDLE_ONLY and node_id not in helper_bundle_target_ids:
            continue
        if node_id in resumed_green:
            continue
        reused = activate_reuse(node_id)
        label = f"ArchAcc_R_support_prefix_{support_index}"
        try:
            generate([node_id])
            result = check_source(
                campaign_dir, render_theory(payload, plan, proofs, 0), label,
            )
        except ValueError as error:
            if "cites unknown" not in str(error) and "undeclared DAG citations" not in str(error):
                raise
            latest_llm_event = next(
                event for event in reversed(AUDIT_TRAIL.events())
                if event.get("kind") == "llm"
            )
            final_path = REPO / latest_llm_event["artifacts"]["final"]["path"]
            rejected = validate_proofs(
                parse_json(final_path.read_text(encoding="utf-8")), {node_id},
            )
            proofs.update(rejected)
            persist_proofs()
            AUDIT_TRAIL.record_harness(
                "proof-citation-rejection", artifacts=[final_path], details={
                    "node_id": node_id,
                    "repair_round": 0,
                    "residual": str(error),
                    "isabelle_invoked": False,
                },
            )
            result = {
                "verdict": "fail", "returncode": None, "wall_ms": 0,
                "subgoal_count": None,
                "diagnostic": (
                    "HARNESS CITATION-CLOSURE REJECTION:\n" + str(error)
                    + "\nDo not guess definition theorem names for abbreviations. "
                      "Use only exact supplied premise names; preserve a pde_at' "
                      "assumption directly or unfold it through an exact visible fact."
                ),
            }
        attempts.append({
            key: value for key, value in result.items() if key != "diagnostic"
        } | {"node_id": node_id, "role": "support", "reused_verified_frontier": reused})
        write_json(attempts_path, attempts)
        repair_round = 0
        while result["verdict"] != "pass" and repair_round < 6:
            repair_round += 1
            repair_request = repair_prompt(
                payload, plan, node_id, proofs[node_id], result["diagnostic"], proofs,
                failed_frontiers.get(node_id),
            ) + (
                "\n\nThis is a frozen support node. Return action=proof only; "
                "do not add or change helper statements. Consumer contract statements and "
                "residuals are strategy evidence, but those contract theorems are future "
                "nodes and must not be cited."
            )
            if len(repair_request) > CONTEXT_LIMIT:
                raise SystemExit(f"ArchAcc_R support repair exceeds context limit: {node_id}")
            repair = parse_json(planner_call(
                repair_request, planner_dir / f"repair-support-{support_index}-{repair_round}",
                model=model, timeout=timeout,
            ))
            if repair.get("action") != "proof":
                raise ValueError("frozen support repair must replace only its proof")
            candidate = repair.get("proof", "").strip()
            if not valid_proof_body(candidate):
                raise ValueError("support repair contains an illegal proof body")
            close_recorded_candidate_edges(
                f"repair-support-{support_index}-{repair_round}",
            )
            try:
                validate_proof_citations(payload, plan, node_id, candidate)
            except ValueError as error:
                AUDIT_TRAIL.record_harness(
                    "proof-citation-rejection", details={
                        "node_id": node_id,
                        "repair_round": repair_round,
                        "residual": str(error),
                    },
                )
                result = dict(result)
                result["diagnostic"] = (
                    "HARNESS CITATION-CLOSURE REJECTION:\n" + str(error)
                    + "\nThe consumer contract statements are evidence only. Their theorems "
                      "are not proof-green and cannot be cited. Repair the body using only "
                      "the declared premise IDs and earlier green dependencies."
                )
                continue
            proofs[node_id] = candidate
            persist_proofs()
            result = check_source(
                campaign_dir, render_theory(payload, plan, proofs, 0),
                f"{label}_repair_{repair_round}",
            )
            attempts.append({
                key: value for key, value in result.items() if key != "diagnostic"
            } | {"node_id": node_id, "role": "support", "repair_round": repair_round})
            write_json(attempts_path, attempts)
        if result["verdict"] != "pass":
            write_json(planner_dir / "attempts.json", attempts)
            raise SystemExit(f"failed to prove support {node_id} after bounded repair")

    if HELPER_BUNDLE_ONLY:
        ordered_targets = [
            node_id for node_id in plan["support_order"]
            if node_id in helper_bundle_target_ids
        ]
        missing = [node_id for node_id in ordered_targets if node_id not in proofs]
        if missing:
            raise ValueError(
                f"bundle-only run completed with missing proofs: {missing}"
            )
        result_path = planner_dir / "helper-bundle-only-result.json"
        support_by_id = {
            item["id"]: item for item in plan["support_nodes"]
        }
        write_json(result_path, {
            "schema_version": 1,
            "theory": THEORY,
            "generation_id": AUDIT_TRAIL.generation_id,
            "status": "helper_bundle_green",
            "experiment_mode": "human-informed-harness-revision",
            "human_proof_exposed_to_generator": False,
            "contract": HELPER_BUNDLE_CONTRACT,
            "source_generation_id": HELPER_BUNDLE_SOURCE_GENERATION,
            "helper_ids": ordered_targets,
            "helpers": [
                {
                    "id": node_id,
                    "statement": support_by_id[node_id]["statement"],
                    "statement_sha256": sha256_text(
                        support_by_id[node_id]["statement"]
                    ),
                    "proof": proofs[node_id],
                    "proof_sha256": sha256_text(proofs[node_id]),
                    "frame_obligations": support_by_id[node_id].get(
                        "frame_obligations", [],
                    ),
                    "premise_provenance": support_by_id[node_id].get(
                        "premise_provenance", [],
                    ),
                }
                for node_id in ordered_targets
            ],
            "attempt_count": sum(
                attempt.get("node_id") in helper_bundle_target_ids
                for attempt in attempts
            ),
            "full_contract_attempted": False,
            "theory_sealed": False,
        })
        AUDIT_TRAIL.record_harness(
            "helper-bundle-only-complete",
            artifacts=[result_path, attempts_path, partial_path],
            details={
                "contract": HELPER_BUNDLE_CONTRACT,
                "helper_ids": ordered_targets,
                "full_contract_attempted": False,
                "theory_sealed": False,
            },
        )
        return plan, proofs, attempts

    runtime_extensions: set[str] = {
        consumer
        for helper in plan["support_nodes"]
        if (
            helper.get("origin") == "isabelle_residual_runtime_extension"
            and helper["id"] in proofs
        )
        for consumer in helper.get("consumers", [])
    }
    legal_import_ids = {
        item["id"]
        for key in (
            "fixed_framework_declarations", "retrieved_legal_premises",
            "retrieved_legal_generated_premises",
        )
        for item in payload[key]
    }
    runtime_retrieval_cache: dict[str, list[dict]] = {}

    def ensure_runtime_residual_premises(
            node_id: str, result: dict, active_frontier: dict) -> list[dict]:
        if node_id in runtime_retrieval_cache:
            return runtime_retrieval_cache[node_id]
        contract = next(
            item for item in payload["fixed_contracts"] if item["id"] == node_id
        )
        plan_node = next(
            item for item in plan["contract_nodes"] if item["id"] == node_id
        )
        helper_frontier = failed_runtime_leaf_frontiers.get(node_id, {})
        query = " ".join([
            contract["statement"], plan_node.get("strategy", ""),
            active_frontier.get("proof", ""), result.get("diagnostic", "")[-16000:],
            helper_frontier.get("diagnostic", "")[-16000:],
            "direct refinement simulation after a generic correspondence "
            "rule stalls corres_no_failI no_fail_pre set_object setObject "
            "state_relation pspace_relation obj_relation_cuts pde_relation "
            "projectKOs in_magnitude_check objBits lookupAround2 "
            "setObject_def updateObject_default_def put_def "
            "pspace_relation_def ekheap_relation_def ghost_relation_def "
            "regen_pspace_dom_update regen_map_to_ctes_upd_other",
        ])
        catalog_retrieved = runtime_residual_premises(
            campaign_dir, payload, query,
        )
        generated_retrieved = runtime_residual_generated_premises(
            campaign_dir, payload, query,
        )
        retrieved = catalog_retrieved + generated_retrieved
        existing_catalog_ids = {
            item["id"] for item in payload["retrieved_legal_premises"]
        }
        payload["retrieved_legal_premises"].extend(
            item for item in catalog_retrieved
            if item["id"] not in existing_catalog_ids
        )
        existing_generated_ids = {
            item["id"]
            for item in payload["retrieved_legal_generated_premises"]
        }
        payload["retrieved_legal_generated_premises"].extend(
            item for item in generated_retrieved
            if item["id"] not in existing_generated_ids
        )
        legal_import_ids.update(item["id"] for item in retrieved)
        runtime_retrieval_cache[node_id] = retrieved
        artifact = planner_dir / (
            "runtime-premises-" + sha256_text(node_id)[:12] + ".json"
        )
        write_json(artifact, {
            "schema_version": 1, "theory": THEORY, "contract_id": node_id,
            "query_sha256": sha256_text(query),
            "catalog_records_sha256": payload["catalog_health"]["records_sha256"],
            "catalog_records": catalog_retrieved,
            "generated_records": generated_retrieved,
            "records": retrieved,
        })
        AUDIT_TRAIL.record_harness(
            "runtime-residual-premise-retrieval", artifacts=[artifact],
            details={
                "node_id": node_id, "record_count": len(retrieved),
                "catalog_record_count": len(catalog_retrieved),
                "generated_record_count": len(generated_retrieved),
                "record_ids": [item["id"] for item in retrieved],
                "helper_frontier_subgoals": helper_frontier.get("subgoal_count"),
            },
        )
        return retrieved

    extension_path = planner_dir / "runtime-leaf-extensions.jsonl"
    for index, node_id in enumerate(plan["contract_order"], start=1):
        if node_id in resumed_green:
            continue
        strategy_rejections = consecutive_probe_strategy_rejections(
            AUDIT_TRAIL.events(), node_id,
        )
        reused = activate_reuse(node_id)
        resumed_frontier = failed_frontiers.get(node_id)
        if resumed_frontier is not None:
            validate_proof_citations(
                payload, plan, node_id, resumed_frontier["proof"],
                check_fact_existence=False,
            )
            proofs[node_id] = resumed_frontier["proof"]
            persist_proofs()
            resume_repair_pending = False
            AUDIT_TRAIL.record_harness(
                "best-failed-frontier-activated", artifacts=[frontier_path],
                details={
                    "node_id": node_id,
                    "subgoal_count": resumed_frontier.get("subgoal_count"),
                    "failure_progress": resumed_frontier.get("failure_progress"),
                    "source": resumed_frontier.get("source"),
                    "proof_sha256": resumed_frontier.get("proof_sha256"),
                },
            )
        else:
            generate([node_id])
        result = check_source(
            campaign_dir, render_theory(payload, plan, proofs, index),
            f"ArchAcc_R_prefix_{index}",
        )
        attempts.append({
            key: value for key, value in result.items() if key != "diagnostic"
        } | {"node_id": node_id, "reused_verified_frontier": reused})
        write_json(attempts_path, attempts)
        if node_id in runtime_rollback_evidence:
            result = dict(result)
            result["diagnostic"] = (
                result.get("diagnostic", "").rstrip()
                + "\n\nROLLED-BACK PROVISIONAL RUNTIME LEAF EVIDENCE:\n"
                + json.dumps(
                    runtime_rollback_evidence[node_id],
                    ensure_ascii=False, separators=(",", ":"),
                )
            )
        result = retain_or_restore_frontier(node_id, result, 0)
        resumed_phase_rejection = runtime_phase_rejections.get(node_id)
        if result["verdict"] != "pass" and resumed_phase_rejection is not None:
            result = dict(result)
            result["diagnostic"] = bounded_repair_diagnostic(
                result.get("diagnostic", "").rstrip()
                + "\n\n"
                + runtime_phase_rejection_diagnostic(resumed_phase_rejection)
            )
            AUDIT_TRAIL.record_harness(
                "runtime-helper-phase-rejection-resumed",
                artifacts=[runtime_phase_rejection_path],
                details={
                    "node_id": node_id,
                    "prior_repair_round": resumed_phase_rejection.get(
                        "repair_round"
                    ),
                    "residual": resumed_phase_rejection.get("residual"),
                },
            )
        repair_round = 0
        while (
                result["verdict"] != "pass"
                and repair_round < CONTRACT_REPAIR_LIMIT
        ):
            repair_round += 1
            repair_request = repair_prompt(
                payload, plan, node_id, proofs[node_id], result["diagnostic"], proofs,
                failed_frontiers.get(node_id),
            )
            existing_runtime_leaf = node_id in runtime_extensions
            active_frontier = failed_frontiers.get(node_id, {})
            plateau_count = trailing_contract_subgoal_plateau(
                attempts, node_id, active_frontier.get("subgoal_count"),
                active_frontier.get("failure_progress"),
            )
            regression_count = trailing_contract_frontier_regressions(
                attempts, node_id, active_frontier.get("subgoal_count"),
                active_frontier.get("failure_progress"),
            )
            current_runtime_helper = next((
                helper for helper in plan["support_nodes"]
                if (
                    helper.get("origin") == "isabelle_residual_runtime_extension"
                    and node_id in helper.get("consumers", [])
                )
            ), None)
            active_subgoal_count = active_frontier.get("subgoal_count")
            if (
                    isinstance(active_subgoal_count, int)
                    and active_subgoal_count <= 3
                    and proof_outer_command(active_frontier.get("proof", ""))
            ):
                repair_request += (
                    "\n\nMANDATORY COMPLETE-CONTRACT-PROOF MODE: preserve the "
                    "current proof from its outermost command through the repaired "
                    "tail. Do not return only the residual branch or helper call. "
                    "The complete candidate must begin with this exact outer command: "
                    + proof_outer_command(active_frontier["proof"])
                )
            helper_application_failure = (
                existing_runtime_leaf
                and current_runtime_helper is not None
                and failed_apply_targets_helper(
                    active_frontier, current_runtime_helper["name"],
                )
            )
            helper_interface_plateau = (
                active_subgoal_count == 1
                and plateau_count >= 3
                and helper_application_failure
            )
            regression_exhaustion = (
                active_subgoal_count == 1
                and plateau_count >= 2
                and regression_count >= 3
                and helper_application_failure
            )
            revise_runtime_leaf = (
                existing_runtime_leaf
                and (
                    (
                        plateau_count >= 3
                        and (
                            (active_subgoal_count or 0) > 1
                            or helper_interface_plateau
                        )
                    )
                    or regression_exhaustion
                )
            )
            force_residual_leaf = (
                not existing_runtime_leaf
                and (strategy_rejections >= 2 or regression_count >= 3)
            )
            helper_frontier = failed_runtime_leaf_frontiers.get(node_id)
            helper_verified_prefix = machine_verified_proof_prefix(
                helper_frontier or {},
            )
            protected_helper_rules = proof_rule_targets(helper_verified_prefix)
            protected_helper_rules |= phase_mandatory_principal_rules(
                (helper_frontier or {}).get("diagnostic", ""),
            )
            exhausted_helper_rules = (
                repeated_runtime_leaf_principal_rules(
                    attempts, node_id, AUDIT_TRAIL.run_dir,
                    excluded_rules=protected_helper_rules,
                )
                if force_residual_leaf else []
            )
            if force_residual_leaf:
                visible_runtime_ids = payload_legal_premise_ids(payload)
                runtime_records = ensure_runtime_residual_premises(
                    node_id, result, active_frontier,
                )
                runtime_prompt_records = [
                    item for item in runtime_records
                    if item["id"] not in visible_runtime_ids
                ]
                if runtime_prompt_records:
                    repair_request += (
                        "\n\nRUNTIME RETRIEVED LEGAL PREMISES: these records "
                        "passed the same session-visibility and catalog-health "
                        "checks as the frozen inventory. A new leaf may cite "
                        "their exact IDs.\n"
                        + json.dumps(
                            runtime_prompt_records, ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    )
                if helper_frontier is not None:
                    repair_request += (
                        "\n\nBEST FAILED PROVISIONAL-HELPER FRONTIER (machine "
                        "checked and monotone):\n"
                        + json.dumps({
                            "subgoal_count": helper_frontier.get("subgoal_count"),
                            "failure_progress": helper_frontier.get("failure_progress"),
                            "machine_verified_proof_prefix": helper_verified_prefix,
                            "source": helper_frontier.get("source"),
                            "llm_final": helper_frontier.get("llm_final"),
                            "response": helper_frontier.get("response"),
                            "diagnostic": helper_frontier.get("diagnostic", "")[-12000:],
                        }, ensure_ascii=False, separators=(",", ":"))
                        + "\nRepair this exact helper frontier. Preserve the "
                          "machine_verified_proof_prefix byte-for-byte and edit "
                          "only from its first failing command onward. Do not "
                          "return a candidate with more residual subgoals or "
                          "reintroduce schematic guard goals already eliminated "
                          "by it."
                    )
                if exhausted_helper_rules:
                    repair_request += (
                        "\n\nMANDATORY PRINCIPAL-RULE PIVOT: at least three "
                        "machine-checked provisional helpers used these principal "
                        "rules and failed: "
                        + json.dumps(exhausted_helper_rules, separators=(",", ":"))
                        + ". Do not cite them anywhere in the new helper proof. "
                          "Use the retrieved direct-simulation interfaces, beginning "
                          "with corres_no_failI/no_fail_pre and explicit state-"
                          "relation decomposition. A repeated rule will be rejected "
                          "before Isabelle."
                    )
            if revise_runtime_leaf:
                plateau_reason = (
                    f"{plateau_count} best-depth failures plus "
                    f"{regression_count} proof-depth regressions; consumer-only "
                    "repairs repeatedly moved the failure before the verified prefix"
                    if regression_exhaustion else
                    "one residual goal whose failing apply command directly "
                    "targets this helper, demonstrating a consumer-interface "
                    "mismatch"
                    if helper_interface_plateau else
                    f"{active_subgoal_count} residual goals"
                )
                repair_request += (
                    "\n\nMANDATORY PROVISIONAL-LEAF REVISION MODE: the helper is "
                    "Isabelle-green but its direct consumer is stalled with "
                    f"{plateau_reason}. Return "
                    "action=local_helper with the exact same name and revise its "
                    "statement to match the concrete consumer residual at the "
                    "same syntactic abstraction boundary. This replaces "
                    "the one provisional leaf; it does not add a second node. Include "
                    "a complete helper proof and contract_proof.\nCURRENT PROVISIONAL "
                    "LEAF:\n"
                    + json.dumps({
                        "helper": current_runtime_helper,
                        "proof": proofs.get(
                            (current_runtime_helper or {}).get("id", ""), "",
                        ),
                    }, ensure_ascii=False, separators=(",", ":"))
                )
            elif existing_runtime_leaf:
                repair_request += (
                    "\n\nMANDATORY GREEN-LEAF CONSUMER MODE: the single runtime "
                    "helper is already Isabelle-green and frozen. Return action=proof "
                    "and repair only contract_proof using that helper. A local_helper "
                    "action would be recursive graph growth and will be rejected."
                )
            elif force_residual_leaf:
                repair_request += (
                    "\n\nMANDATORY RESIDUAL-LEAF MODE: multiple whole-proof candidates "
                    "repeated a prohibited strategy or regressed behind the best "
                    f"frontier {regression_count} times. Return action=local_helper. "
                    "The helper statement must be a top-level Refine.ArchAcc_R lemma "
                    "that quantifies its variables, depends only on supplied legal IDs "
                    "or green nodes, captures one shared branch-local residual boundary, "
                    "and is consumed by contract_proof. A proof action will be rejected."
                )
            if len(repair_request) > CONTEXT_LIMIT:
                raise SystemExit(f"ArchAcc_R repair prompt exceeds context limit: {node_id}")
            repair = parse_json(planner_call(
                repair_request, planner_dir / f"repair-{index}-{repair_round}",
                model=model, timeout=timeout,
            ))
            required_action = (
                "local_helper" if revise_runtime_leaf
                else "proof" if existing_runtime_leaf
                else "local_helper" if force_residual_leaf
                else None
            )
            if required_action and repair.get("action") != required_action:
                strategy_rejections += 1
                AUDIT_TRAIL.record_harness(
                    "repair-action-rejection", details={
                        "node_id": node_id, "repair_round": repair_round,
                        "required_action": required_action,
                        "actual_action": repair.get("action"),
                        "prior_strategy_rejections": strategy_rejections - 1,
                    },
                )
                result = dict(result)
                result["diagnostic"] = (
                    result.get("diagnostic", "").rstrip()
                    + "\n\nHARNESS ACTION REJECTION: return the required "
                    + required_action
                    + " action for the current runtime-leaf lifecycle state."
                )
                continue
            if repair.get("action") == "proof":
                candidate = repair.get("proof", "").strip()
                if not valid_proof_body(candidate):
                    AUDIT_TRAIL.record_harness(
                        "contract-proof-structure-rejection", details={
                            "node_id": node_id, "repair_round": repair_round,
                            "proof_excerpt": candidate[-4000:],
                        },
                    )
                    result = dict(result)
                    result["diagnostic"] = (
                        result.get("diagnostic", "").rstrip()
                        + "\n\nHARNESS CONTRACT-PROOF STRUCTURE REJECTION: "
                          "return the complete contract proof body, not only a "
                          "replacement tail; an apply-style proof must end in done. "
                          "Integrate this rejected tail into CURRENT PROOF:\n"
                        + candidate[-8000:]
                    )
                    continue
                if (
                        isinstance(active_subgoal_count, int)
                        and active_subgoal_count <= 3
                        and not preserves_outer_proof_prefix(
                            candidate, active_frontier.get("proof", ""),
                        )
                ):
                    AUDIT_TRAIL.record_harness(
                        "contract-proof-prefix-rejection", details={
                            "node_id": node_id, "repair_round": repair_round,
                            "expected_outer_command": proof_outer_command(
                                active_frontier.get("proof", ""),
                            ),
                            "actual_outer_command": proof_outer_command(candidate),
                            "proof_excerpt": candidate[:4000],
                        },
                    )
                    result = dict(result)
                    result["diagnostic"] = (
                        result.get("diagnostic", "").rstrip()
                        + "\n\nHARNESS CONTRACT-PROOF PREFIX REJECTION: the "
                          "candidate is a replacement tail, not a complete proof. "
                          "Keep the current outer proof prefix and begin with: "
                        + proof_outer_command(active_frontier.get("proof", ""))
                    )
                    continue
                probe_evidence = load_prefix_probe_evidence(
                    AUDIT_TRAIL.run_dir, [node_id])
                try:
                    validate_probe_strategy(candidate, probe_evidence)
                except ValueError as error:
                    strategy_rejections += 1
                    AUDIT_TRAIL.record_harness(
                        "probe-strategy-rejection", details={
                            "node_id": node_id,
                            "repair_round": repair_round,
                            "residual": str(error),
                        },
                    )
                    result = dict(result)
                    prior_diagnostic = result.get("diagnostic", "").rstrip()
                    result["diagnostic"] = (
                        prior_diagnostic + "\n\nHARNESS PREFIX-PROBE REJECTION: "
                        + str(error))
                    continue
                close_recorded_candidate_edges(
                    f"repair-{index}-{repair_round}",
                )
                validate_proof_citations(payload, plan, node_id, candidate)
                proofs[node_id] = candidate
            elif repair.get("action") == "local_helper" and revise_runtime_leaf:
                name = repair.get("name", "")
                statement = repair.get("statement", "").strip()
                helper_id = f"support:{THEORY}:{name}"
                if (
                        current_runtime_helper is None
                        or helper_id != current_runtime_helper["id"]
                        or not statement.startswith(f"lemma {name}")
                        or FORBIDDEN_RE.search(statement)
                ):
                    result = reject_runtime_helper(
                        node_id, repair_round, result, repair,
                        "a provisional leaf revision must keep the exact helper "
                        "name and provide a valid replacement statement",
                    )
                    continue
                helper_proof = repair.get("proof", "").strip()
                contract_proof = repair.get("contract_proof", "").strip()
                dependencies = repair.get("dependencies", [])
                green_nodes = set(proofs) - {node_id, helper_id}
                dependencies = complete_runtime_helper_dependency_edges(
                    node_id, repair_round, repair, helper_proof,
                    legal_import_ids | green_nodes,
                )
                structure_errors = []
                if not runtime_leaf_revision_changes_statement(
                        current_runtime_helper["statement"], statement):
                    structure_errors.append(
                        "statement must change the provisional helper proposition; "
                        "consumer-only changes require action=proof"
                    )
                if not valid_proof_body(helper_proof):
                    structure_errors.append(
                        "proof must be complete; apply-style proof must end in done"
                    )
                if not valid_proof_body(contract_proof):
                    structure_errors.append(
                        "contract_proof must be complete; apply-style proof must "
                        "end in done"
                    )
                if (
                        isinstance(active_subgoal_count, int)
                        and active_subgoal_count <= 3
                        and not preserves_outer_proof_prefix(
                            contract_proof, active_frontier.get("proof", ""),
                        )
                ):
                    structure_errors.append(
                        "contract_proof is only a replacement tail; it must preserve "
                        "the current outer proof command "
                        + proof_outer_command(active_frontier.get("proof", ""))
                    )
                if len(dependencies) != len(set(dependencies)):
                    structure_errors.append("dependencies must be unique")
                if not set(dependencies) <= legal_import_ids | green_nodes:
                    structure_errors.append(
                        runtime_dependency_rejection(
                            dependencies, legal_import_ids | green_nodes,
                            next(
                                item["dependencies"]
                                for item in plan["contract_nodes"]
                                if item["id"] == node_id
                            ) + [
                                item["id"]
                                for item in runtime_retrieval_cache.get(node_id, [])
                            ],
                        )
                    )
                if structure_errors:
                    result = reject_runtime_helper(
                        node_id, repair_round, result, repair,
                        "; ".join(structure_errors),
                    )
                    continue
                prior_helper = dict(current_runtime_helper)
                prior_helper_proof = proofs[helper_id]
                revised_helper = {
                    **prior_helper,
                    "statement": statement,
                    "dependencies": dependencies,
                    "rationale": repair.get(
                        "residual_explanation", "direct-consumer plateau revision",
                    ),
                    "origin": "isabelle_residual_runtime_extension",
                }
                current_runtime_helper.clear()
                current_runtime_helper.update(revised_helper)
                proofs[helper_id] = helper_proof
                try:
                    validate_plan(plan, payload)
                    validate_proof_citations(
                        payload, plan, helper_id, helper_proof,
                    )
                    validate_proof_citations(
                        payload, plan, node_id, contract_proof,
                    )
                except ValueError as error:
                    current_runtime_helper.clear()
                    current_runtime_helper.update(prior_helper)
                    proofs[helper_id] = prior_helper_proof
                    result = reject_runtime_helper(
                        node_id, repair_round, result, repair, str(error),
                    )
                    continue
                helper_result = check_source(
                    campaign_dir, render_theory(payload, plan, proofs, index - 1),
                    f"ArchAcc_R_runtime_leaf_revision_{index}_{repair_round}",
                )
                attempts.append({
                    key: value for key, value in helper_result.items()
                    if key != "diagnostic"
                } | {
                    "node_id": helper_id, "role": "runtime_leaf_revision",
                    "contract_id": node_id, "repair_round": repair_round,
                })
                write_json(attempts_path, attempts)
                if helper_result["verdict"] != "pass":
                    current_runtime_helper.clear()
                    current_runtime_helper.update(prior_helper)
                    proofs[helper_id] = prior_helper_proof
                    result = reject_runtime_helper(
                        node_id, repair_round, result, repair,
                        "Isabelle rejected the provisional helper revision:\n"
                        + helper_result["diagnostic"],
                    )
                    continue
                if node_id in failed_runtime_leaf_frontiers:
                    prior_runtime_frontier = failed_runtime_leaf_frontiers.pop(node_id)
                    persist_runtime_leaf_frontiers()
                    AUDIT_TRAIL.record_harness(
                        "runtime-leaf-frontier-closed",
                        artifacts=[runtime_leaf_frontier_path],
                        details={
                            "node_id": node_id, "helper_id": helper_id,
                            "repair_round": repair_round,
                            "prior_subgoal_count": prior_runtime_frontier.get(
                                "subgoal_count"
                            ),
                        },
                    )
                invalidate_consumer_frontier_for_helper(
                    node_id, helper_id, repair_round,
                )
                proofs[node_id] = contract_proof
                parent_graph_hash = plan["frozen_graph_sha256"]
                amended_graph = graph_projection(plan)
                amended_hash = sha256_text(json.dumps(
                    amended_graph, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                ))
                plan["frozen_graph_sha256"] = amended_hash
                plan_path = planner_dir / "lemma-plan.json"
                frozen_path = planner_dir / "lemma-graph.frozen.json"
                write_json(plan_path, plan)
                write_json(
                    frozen_path, amended_graph | {"graph_sha256": amended_hash},
                )
                revision = {
                    "schema_version": 1, "theory": THEORY,
                    "contract_id": node_id, "helper": revised_helper,
                    "prior_helper_statement_sha256": sha256_text(
                        prior_helper["statement"],
                    ),
                    "prior_graph_sha256": parent_graph_hash,
                    "amended_graph_sha256": amended_hash,
                    "helper_check": {
                        key: value for key, value in helper_result.items()
                        if key != "diagnostic"
                    },
                    "repair_stage": f"repair-{index}-{repair_round}",
                    "status": "helper_revision_green",
                }
                with extension_path.open("a", encoding="utf-8") as stream:
                    stream.write(
                        json.dumps(revision, ensure_ascii=False, sort_keys=True)
                        + "\n"
                    )
                AUDIT_TRAIL.record_harness(
                    "runtime-leaf-revision", artifacts=[
                        extension_path, plan_path, frozen_path,
                    ], details=revision,
                )
            elif repair.get("action") == "local_helper":
                if node_id in runtime_extensions:
                    raise ValueError("runtime leaf policy permits only one helper per contract")
                name = repair.get("name", "")
                statement = repair.get("statement", "").strip()
                if (not name.startswith("regen_")
                        or not statement.startswith(f"lemma {name}")
                        or FORBIDDEN_RE.search(statement)):
                    raise ValueError("runtime helper has an invalid name or statement")
                helper_id = f"support:{THEORY}:{name}"
                existing_helper = next((
                    item for item in plan["support_nodes"] if item["id"] == helper_id
                ), None)
                if existing_helper is not None:
                    if normalized_proposition(statement) != normalized_proposition(
                        existing_helper["statement"]
                    ):
                        raise ValueError(
                            "runtime helper conflicts with a planned node statement"
                        )
                    contract_proof = repair.get("contract_proof", "").strip()
                    if not valid_proof_body(contract_proof):
                        raise ValueError(
                            "planned-helper reuse has an illegal contract proof"
                        )
                    if (
                            isinstance(active_subgoal_count, int)
                            and active_subgoal_count <= 3
                            and not preserves_outer_proof_prefix(
                                contract_proof, active_frontier.get("proof", ""),
                            )
                    ):
                        result = reject_runtime_helper(
                            node_id, repair_round, result, repair,
                            "contract_proof is only a replacement tail; preserve "
                            "the current outer proof command",
                        )
                        continue
                    close_recorded_candidate_edges(
                        f"repair-{index}-{repair_round}",
                    )
                    validate_proof_citations(payload, plan, node_id, contract_proof)
                    proofs[node_id] = contract_proof
                    AUDIT_TRAIL.record_harness(
                        "planned-helper-reuse", details={
                            "contract_id": node_id, "support_id": helper_id,
                            "repair_stage": f"repair-{index}-{repair_round}",
                        },
                    )
                    persist_proofs()
                    result = check_source(
                        campaign_dir, render_theory(payload, plan, proofs, index),
                        f"ArchAcc_R_prefix_{index}_repair_{repair_round}",
                    )
                    attempts.append({
                        key: value for key, value in result.items() if key != "diagnostic"
                    } | {
                        "node_id": node_id, "repair_round": repair_round,
                        "planned_helper_reuse": helper_id,
                    })
                    write_json(attempts_path, attempts)
                    result = retain_or_restore_frontier(
                        node_id, result, repair_round,
                    )
                    continue
                helper_proof = repair.get("proof", "").strip()
                contract_proof = repair.get("contract_proof", "").strip()
                repeated_rules = sorted(
                    set(exhausted_helper_rules) & proof_rule_targets(helper_proof)
                )
                if repeated_rules:
                    AUDIT_TRAIL.record_harness(
                        "runtime-helper-principal-rule-rejection",
                        details={
                            "node_id": node_id, "repair_round": repair_round,
                            "repeated_rules": repeated_rules,
                        },
                    )
                    result = reject_runtime_helper(
                        node_id, repair_round, result, repair,
                        "repeated exhausted principal rules: "
                        + json.dumps(repeated_rules, separators=(",", ":")),
                    )
                    continue
                phase_error = runtime_phase_strategy_error(
                    helper_proof, result.get("diagnostic", ""),
                    failed_runtime_leaf_frontiers.get(node_id, {}).get(
                        "diagnostic", "",
                    ),
                )
                if phase_error:
                    AUDIT_TRAIL.record_harness(
                        "runtime-helper-phase-strategy-rejection", details={
                            "node_id": node_id, "repair_round": repair_round,
                            "residual": phase_error,
                        },
                    )
                    result = reject_runtime_helper_phase(
                        node_id, repair_round, result, repair, phase_error,
                    )
                    continue
                if (
                        not valid_proof_body(helper_proof)
                        or not valid_proof_body(contract_proof)
                ):
                    result = reject_runtime_helper(
                        node_id, repair_round, result, repair,
                        "both proof and contract_proof must be complete legal proof "
                        "bodies; an apply-style body must end in done",
                    )
                    continue
                if (
                        helper_verified_prefix
                        and not helper_proof.startswith(helper_verified_prefix)
                ):
                    result = reject_runtime_helper(
                        node_id, repair_round, result, repair,
                        "helper proof regressed before the machine-verified prefix; "
                        "preserve this exact prefix: "
                        + json.dumps(helper_verified_prefix),
                    )
                    continue
                if (
                        isinstance(active_subgoal_count, int)
                        and active_subgoal_count <= 3
                        and not preserves_outer_proof_prefix(
                            contract_proof, active_frontier.get("proof", ""),
                        )
                ):
                    result = reject_runtime_helper(
                        node_id, repair_round, result, repair,
                        "contract_proof is only a replacement tail; preserve "
                        "the current outer proof command",
                    )
                    continue
                dependencies = repair.get("dependencies", [])
                if len(dependencies) != len(set(dependencies)):
                    result = reject_runtime_helper(
                        node_id, repair_round, result, repair,
                        "runtime helper dependencies must be unique",
                    )
                    continue
                green_nodes = set(proofs) - {node_id}
                dependencies = complete_runtime_helper_dependency_edges(
                    node_id, repair_round, repair, helper_proof,
                    legal_import_ids | green_nodes,
                )
                if not set(dependencies) <= legal_import_ids | green_nodes:
                    result = reject_runtime_helper(
                        node_id, repair_round, result, repair,
                        runtime_dependency_rejection(
                            dependencies, legal_import_ids | green_nodes,
                            next(
                                item["dependencies"]
                                for item in plan["contract_nodes"]
                                if item["id"] == node_id
                            ) + [
                                item["id"]
                                for item in runtime_retrieval_cache.get(node_id, [])
                            ],
                        ),
                    )
                    continue
                plan_node = next(
                    item for item in plan["contract_nodes"] if item["id"] == node_id
                )
                helper = {
                    "id": helper_id,
                    "name": name,
                    "statement": statement,
                    "ownership": THEORY,
                    "family": plan_node["family"],
                    "obligation_kind": "isabelle_residual_leaf",
                    "dependencies": dependencies,
                    "consumers": [node_id],
                    "rationale": repair.get(
                        "residual_explanation", "residual-derived leaf",
                    ),
                    "origin": "isabelle_residual_runtime_extension",
                }
                plan["support_nodes"].append(helper)
                plan["support_order"].append(helper_id)
                plan_node.setdefault("dependencies", []).append(helper_id)
                try:
                    validate_plan(plan, payload)
                    validate_proof_citations(
                        payload, plan, helper_id, helper_proof,
                    )
                    validate_proof_citations(
                        payload, plan, node_id, contract_proof,
                    )
                except ValueError as error:
                    plan["support_nodes"].pop()
                    plan["support_order"].pop()
                    plan_node["dependencies"].remove(helper_id)
                    result = reject_runtime_helper(
                        node_id, repair_round, result, repair, str(error),
                    )
                    continue
                proofs[helper_id] = helper_proof
                helper_result = check_source(
                    campaign_dir, render_theory(payload, plan, proofs, index - 1),
                    f"ArchAcc_R_runtime_leaf_{index}_{repair_round}",
                )
                attempts.append({
                    key: value for key, value in helper_result.items()
                    if key != "diagnostic"
                } | {
                    "node_id": helper_id, "role": "runtime_leaf",
                    "contract_id": node_id, "repair_round": repair_round,
                })
                write_json(attempts_path, attempts)
                if helper_result["verdict"] != "pass":
                    candidate_frontier = failed_frontier_record(
                        helper_result, helper_proof, statement,
                    )
                    candidate_frontier["subgoal_count"] = attempt_subgoal_count(
                        candidate_frontier,
                    )
                    candidate_frontier["response"] = repair
                    latest_llm_event = next(
                        event for event in reversed(AUDIT_TRAIL.events())
                        if event.get("kind") == "llm"
                    )
                    candidate_frontier["llm_final"] = latest_llm_event[
                        "artifacts"
                    ]["final"]["path"]
                    attempts[-1]["failure_progress"] = candidate_frontier[
                        "failure_progress"
                    ]
                    write_json(attempts_path, attempts)
                    prior_helper_frontier = failed_runtime_leaf_frontiers.get(
                        node_id,
                    )
                    if (
                            prior_helper_frontier is None
                            or failure_frontier_rank(candidate_frontier)[:3]
                            <= failure_frontier_rank(prior_helper_frontier)[:3]
                    ):
                        failed_runtime_leaf_frontiers[node_id] = candidate_frontier
                        helper_frontier_stage = (
                            "best-failed-runtime-leaf-promoted"
                        )
                    else:
                        helper_frontier_stage = (
                            "runtime-leaf-regression-restored"
                        )
                    persist_runtime_leaf_frontiers()
                    active_helper_frontier = failed_runtime_leaf_frontiers[node_id]
                    AUDIT_TRAIL.record_harness(
                        helper_frontier_stage,
                        artifacts=[runtime_leaf_frontier_path],
                        details={
                            "node_id": node_id, "helper_id": helper_id,
                            "repair_round": repair_round,
                            "candidate_subgoal_count": candidate_frontier.get(
                                "subgoal_count"
                            ),
                            "candidate_failure_progress": candidate_frontier.get(
                                "failure_progress"
                            ),
                            "best_subgoal_count": active_helper_frontier.get(
                                "subgoal_count"
                            ),
                            "best_failure_progress": active_helper_frontier.get(
                                "failure_progress"
                            ),
                            "best_source": active_helper_frontier.get("source"),
                        },
                    )
                    del proofs[helper_id]
                    plan["support_nodes"].pop()
                    plan["support_order"].pop()
                    plan_node["dependencies"].remove(helper_id)
                    regression_note = (
                        "\nCandidate ranked below the retained provisional-helper "
                        "frontier; repair from the retained frontier below.\n"
                        + json.dumps({
                            "subgoal_count": active_helper_frontier.get(
                                "subgoal_count"
                            ),
                            "failure_progress": active_helper_frontier.get(
                                "failure_progress"
                            ),
                            "response": active_helper_frontier.get("response"),
                            "diagnostic": active_helper_frontier.get(
                                "diagnostic", ""
                            )[-12000:],
                        }, ensure_ascii=False, separators=(",", ":"))
                        if helper_frontier_stage
                        == "runtime-leaf-regression-restored" else ""
                    )
                    result = reject_runtime_helper(
                        node_id, repair_round, result, repair,
                        "Isabelle rejected the provisional helper:\n"
                        + helper_result["diagnostic"] + regression_note,
                    )
                    continue
                invalidate_consumer_frontier_for_helper(
                    node_id, helper_id, repair_round,
                )
                proofs[node_id] = contract_proof
                runtime_extensions.add(node_id)
                parent_graph_hash = plan["frozen_graph_sha256"]
                amended_graph = graph_projection(plan)
                amended_hash = sha256_text(json.dumps(
                    amended_graph, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                ))
                plan["frozen_graph_sha256"] = amended_hash
                plan_path = planner_dir / "lemma-plan.json"
                frozen_path = planner_dir / "lemma-graph.frozen.json"
                write_json(plan_path, plan)
                write_json(
                    frozen_path, amended_graph | {"graph_sha256": amended_hash},
                )
                extension = {
                    "schema_version": 1, "theory": THEORY,
                    "frozen_graph_sha256": parent_graph_hash,
                    "amended_graph_sha256": amended_hash,
                    "contract_id": node_id, "helper": helper,
                    "helper_check": {
                        key: value for key, value in helper_result.items()
                        if key != "diagnostic"
                    },
                    "repair_stage": f"repair-{index}-{repair_round}",
                    "status": "helper_green",
                }
                with extension_path.open("a", encoding="utf-8") as stream:
                    stream.write(
                        json.dumps(extension, ensure_ascii=False, sort_keys=True)
                        + "\n"
                    )
                AUDIT_TRAIL.record_harness(
                    "runtime-leaf-extension", artifacts=[
                        extension_path, plan_path, frozen_path,
                    ], details=extension,
                )
            else:
                raise ValueError("unknown repair action")
            persist_proofs()
            result = check_source(
                campaign_dir, render_theory(payload, plan, proofs, index),
                f"ArchAcc_R_prefix_{index}_repair_{repair_round}",
            )
            attempts.append({
                key: value for key, value in result.items() if key != "diagnostic"
            } | {"node_id": node_id, "repair_round": repair_round})
            write_json(attempts_path, attempts)
            result = retain_or_restore_frontier(
                node_id, result, repair_round,
            )
        if result["verdict"] != "pass":
            if node_id in failed_frontiers:
                proofs[node_id] = failed_frontiers[node_id]["proof"]
                persist_proofs()
            write_json(planner_dir / "attempts.json", attempts)
            raise SystemExit(f"failed to prove {node_id} after bounded repair")

    write_json(planner_dir / "attempts.json", attempts)
    write_json(planner_dir / "lemma-plan.final.json", plan)
    write_json(planner_dir / "proofs.final.json", proofs)
    return plan, proofs, attempts


def seal(campaign_dir: Path, payload: dict, plan: dict, proofs: dict[str, str], attempts: list[dict]) -> dict:
    generated = campaign_dir / GENERATED_DIR
    generated.mkdir(parents=True, exist_ok=True)
    theory_path = generated / "ArchAcc_R.thy"
    theory_path.write_text(render_theory(payload, plan, proofs), encoding="utf-8")
    (generated / "ROOT").write_text("\n".join([
        f'session {SESSION} in "." = {PARENT_SESSION} +',
        "  theories",
        "    ArchAcc_R",
        "",
    ]), encoding="utf-8")
    final_check = check_source(campaign_dir, theory_path.read_text(encoding="utf-8"), "ArchAcc_R_final")
    if final_check["verdict"] != "pass":
        raise SystemExit("final ArchAcc_R theory check failed")

    generated_ctr = "/workspace/" + str(generated.relative_to(REPO))
    seal_dir = campaign_dir / "seal-layer9-archacc"
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
        raise SystemExit(f"ArchAcc_R heap build failed; see {build_log}")
    getenv = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "getenv", "-b", "ISABELLE_HEAPS",
    ])
    heap_path = f"{getenv.stdout.strip()}/polyml-5.9.1_x86_64_32-linux/{SESSION}"
    identity = container_run(["bash", "-lc", f"sha256sum '{heap_path}'; stat -c %s '{heap_path}'"])
    identity_lines = identity.stdout.splitlines()
    if identity.returncode or len(identity_lines) < 2:
        raise SystemExit("could not identify ArchAcc_R heap")

    smoke_dir = seal_dir / "smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    smoke_path = smoke_dir / "ArchAcc_R_Smoke.thy"
    smoke_path.write_text("\n".join([
        "theory ArchAcc_R_Smoke",
        f"imports \"{SESSION}.ArchAcc_R\"",
        "begin",
        "thm " + " ".join(item["name"] for item in payload["fixed_contracts"]),
        "end",
        "",
    ]), encoding="utf-8")
    smoke_started = time.time()
    smoke = container_run([
        "/workspace/verification/isabelle/bin/isabelle", "process",
        "-l", SESSION, *dependency_args(campaign_dir), "-d", generated_ctr,
        "-d", "/sel4-project/verification/l4v", "-T",
        "/workspace/" + str(smoke_path.with_suffix("").relative_to(REPO)),
    ], timeout=600)
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
        raise SystemExit(f"ArchAcc_R child-import smoke failed; see {smoke_log}")


    fixed_contracts = {item["id"]: item for item in payload["fixed_contracts"]}
    exports_path = campaign_dir / "exports" / "ArchAcc_R.json"
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
    write_json(campaign_dir / "lemma_graph-ArchAcc_R.json", {
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
        campaign_dir / "lemma_graph-ArchAcc_R.json",
        campaign_dir / "contract-local-semantics" / "ArchAcc_R.json",
        campaign_dir / "residual-hints" / "ArchAcc_R.json",
        campaign_dir / "residual-history" / "ArchAcc_R.json",
    ])
    AUDIT_TRAIL.record_harness("heap-build", artifacts=[build_log], details={
        "session": SESSION, "returncode": build.returncode,
        "heap_sha256": identity_lines[0].split()[0],
    })
    AUDIT_TRAIL.record_harness("downstream-smoke", artifacts=[
        smoke_path, smoke_log,
    ], details=downstream_smoke)
    audit_manifest = AUDIT_TRAIL.seal(
        theory_path, required_llm_stages={"00-family-analysis", "01-lemma-dag", "02-plan-critique", "03-proof-generation"},
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
    if "ArchAcc_R" not in theories:
        theories.append("ArchAcc_R")
    completed = {name if "." in name else f"Refine.{name}" for name in theories}
    graph = read_json(campaign_dir / "theory_graph.json")["theories"]
    next_ready = sorted(
        item["name"] for item in graph
        if item["name"] not in completed
        and set(item["target_imports"]) <= completed
    )
    state.update({
        "layer": 9,
        "status": "proof_complete_seal",
        "session": SESSION,
        "theories": theories,
        "next_ready_theories": next_ready,
        "wave_status": "complete",
        "latest_contract_milestone": {
            "theory": THEORY,
            "contracts_green": len(plan["contract_order"]),
            "contracts_total": len(payload["fixed_contracts"]),
        },
    })
    state["invalidatable_theories"] = [
        item for item in state.get("invalidatable_theories", [])
        if item != "Refine.SubMonad_R"
    ]
    state.setdefault("consumer_validations", {})["Refine.SubMonad_R"] = [THEORY]
    write_json(campaign_dir / "state.json", state)
    return manifest


def restore_active_helper_bundle_premises(payload: dict) -> None:
    if AUDIT_TRAIL is None or HELPER_BUNDLE_CONTRACT is None:
        return
    planner_dir = AUDIT_TRAIL.run_dir / "planner"
    bundle_path = planner_dir / f"helper-bundle-{HELPER_BUNDLE_CONTRACT}.json"
    runtime_path = planner_dir / "runtime-premises-9df3f90e6407.json"
    if not bundle_path.is_file() or not runtime_path.is_file():
        return
    bundle = read_json(bundle_path)
    required_ids = {
        dependency
        for helper in bundle.get("helpers", [])
        for dependency in helper.get("dependencies", [])
        if dependency.startswith(("premise:", "generated-premise:", "framework:"))
    }
    known_ids = {
        item["id"]
        for key in (
            "retrieved_legal_premises", "retrieved_legal_generated_premises",
            "fixed_framework_declarations",
        )
        for item in payload[key]
    }
    by_id = {
        item["id"]: item for item in read_json(runtime_path).get("records", [])
    }
    restored = []
    for record_id in sorted(required_ids - known_ids):
        record = by_id.get(record_id)
        if record is None:
            raise ValueError(f"missing archived helper-bundle premise: {record_id}")
        target = (
            "retrieved_legal_generated_premises"
            if record_id.startswith("generated-premise:")
            else "retrieved_legal_premises"
        )
        payload[target].append(record)
        restored.append(record_id)
    payload.setdefault("_helper_bundle_extension_ids", []).extend(restored)
    if restored:
        AUDIT_TRAIL.record_harness(
            "helper-bundle-premise-resume", artifacts=[bundle_path, runtime_path],
            details={"record_ids": restored, "record_count": len(restored)},
        )


def run(campaign_dir: Path, model: str, timeout: int) -> dict:
    payload = relevant_context(campaign_dir)
    restore_active_helper_bundle_premises(payload)
    preflight = check_preflight(campaign_dir, payload)
    if AUDIT_TRAIL is None:
        raise RuntimeError("theory audit trail was not initialized")
    preflight_dir = campaign_dir / "preflight" / "ArchAcc_R"
    AUDIT_TRAIL.record_harness("preflight", artifacts=[
        preflight_dir / "planner-input.json", preflight_dir / "report.json",
        preflight_dir / "ArchAcc_R_Preflight.thy", preflight_dir / "context-check.log",
        campaign_dir / "contract-local-semantics" / "ArchAcc_R.json",
        campaign_dir / "residual-hints" / "ArchAcc_R.json",
        campaign_dir / "residual-history" / "ArchAcc_R.json",
    ], details={"verdict": preflight["verdict"]})
    plan, proofs, attempts = plan_and_prove(campaign_dir, payload, model, timeout)
    if HELPER_BUNDLE_ONLY:
        if AUDIT_TRAIL is None:
            raise RuntimeError("theory audit trail was not initialized")
        result_path = (
            AUDIT_TRAIL.run_dir / "planner" / "helper-bundle-only-result.json"
        )
        if not result_path.is_file():
            raise RuntimeError("bundle-only result was not persisted")
        return {
            "preflight": preflight,
            "plan": plan,
            "helper_bundle_result": read_json(result_path),
        }
    manifest = seal(campaign_dir, payload, plan, proofs, attempts)
    return {"preflight": preflight, "plan": plan, "manifest": manifest}


def main() -> None:
    global AUDIT_TRAIL, PROVIDER, REPLAY_DIR, FRESH_PROOFS
    global HELPER_BUNDLE_CONTRACT, HELPER_BUNDLE_SOURCE_GENERATION
    global HELPER_BUNDLE_ONLY
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    parser.add_argument("--provider", choices=("codex", "claude"), default="codex")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--allow-external-payload", action="store_true")
    parser.add_argument("--replay-dir", type=Path)
    parser.add_argument("--fresh-proofs", action="store_true")
    parser.add_argument("--helper-bundle-contract")
    parser.add_argument("--helper-bundle-source-generation")
    parser.add_argument("--helper-bundle-only", action="store_true")
    parser.add_argument("--generation-id", required=True)
    args = parser.parse_args()
    campaign_dir = args.campaign_dir.resolve()
    PROVIDER = args.provider
    REPLAY_DIR = args.replay_dir.resolve() if args.replay_dir else None
    FRESH_PROOFS = args.fresh_proofs
    HELPER_BUNDLE_CONTRACT = args.helper_bundle_contract
    HELPER_BUNDLE_SOURCE_GENERATION = args.helper_bundle_source_generation
    HELPER_BUNDLE_ONLY = args.helper_bundle_only
    if HELPER_BUNDLE_ONLY and (
            HELPER_BUNDLE_CONTRACT is None
            or HELPER_BUNDLE_SOURCE_GENERATION is None
    ):
        raise SystemExit(
            "--helper-bundle-only requires --helper-bundle-contract and "
            "--helper-bundle-source-generation"
        )
    if HELPER_BUNDLE_SOURCE_GENERATION and HELPER_BUNDLE_CONTRACT is None:
        raise SystemExit(
            "--helper-bundle-source-generation requires --helper-bundle-contract"
        )
    if REPLAY_DIR is None and not args.allow_external_payload:
        raise SystemExit("external planner/prover calls require --allow-external-payload")
    AUDIT_TRAIL = TheoryAuditTrail(
        campaign_dir, THEORY, generation_id=args.generation_id,
    )
    AUDIT_TRAIL.record_harness("generation-start", details={
        "provider": PROVIDER, "model": args.model, "replay": REPLAY_DIR is not None,
        "fresh_proofs": FRESH_PROOFS,
        "helper_bundle_contract": HELPER_BUNDLE_CONTRACT,
        "helper_bundle_source_generation": HELPER_BUNDLE_SOURCE_GENERATION,
        "helper_bundle_only": HELPER_BUNDLE_ONLY,
        "experiment_mode": (
            "human-informed-harness-revision"
            if HELPER_BUNDLE_ONLY else "repo-generation"
        ),
        "human_proof_exposed_to_generator": False,
    })
    try:
        print(json.dumps(run(campaign_dir, args.model, args.timeout), indent=2))
    except BaseException as error:
        if AUDIT_TRAIL is not None and not AUDIT_TRAIL.manifest_path.exists():
            AUDIT_TRAIL.record_harness("generation-failed", details={
                "error_type": type(error).__name__,
                "message": str(error),
            })
        raise

if __name__ == "__main__":
    main()
