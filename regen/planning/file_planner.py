#!/usr/bin/env python3
"""File Planner: produce statements and a generated-theorem DAG, never proofs."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from regen.infrastructure.llm_transcript import (
    allocate_artifact_prefix, call_llm, validate_call_artifact,
)
from regen.infrastructure.module_run_log import attach_module_outputs, write_llm_module_log
from regen.planning.proof_graph import (
    build_graph, graph_payload_sha256, parse_json_response, write_immutable_graph,
)


VAGUE_EXTERNAL_SUPPORT = re.compile(
    r"\b(?:existing|available|known|standard)\b", re.IGNORECASE,
)


def planning_environment(package: dict[str, Any]) -> dict[str, Any]:
    """Expose only fixed-contract planning policy, never premise records."""
    if package.get("repository_projection"):
        return {"policy": "repository-projection-v1", **package["repository_projection"],
                "support_contracts": package.get("support_contracts", []),
                "support_contracts_are_premises": False}
    return {
        "policy": "fixed-contracts-only-v1",
        "use": (
            "The File Planner sees fixed contracts only. External premise "
            "existence, names, statements, attributes, and retrieval results "
            "belong exclusively to the Lemma Prover."
        ),
        "contract_count": len(package["contracts"]),
    }


def validate_plan_quality(
    package: dict[str, Any], proposal: dict[str, Any],
    environment: dict[str, Any] | None,
) -> None:
    """Reject unsupported direct-proof claims before creating graph-v1."""
    strategies = proposal.get("contract_strategies", {})
    routes = proposal.get("contract_routes", {})
    dependencies = proposal.get("contract_dependencies", {})
    if package.get("repository_projection"):
        allowed_premises = {
            item["record_id"] for item in (environment or {}).get("legal_premises", [])
        }
        generated = proposal.get("generated_nodes", [])
        consumers = {item.get("id"): [] for item in generated}
        for item in generated:
            required = {
                "semantic_role", "reasoning_gap", "intended_consumers",
                "premise_evidence_ids", "premise_query",
            }
            if not required <= item.keys():
                raise ValueError(
                    f"generated node lacks evidence-driven interface fields: {item.get('id')}"
                )
            if not all(
                isinstance(item[field], str) and item[field].strip()
                for field in ("semantic_role", "reasoning_gap")
            ):
                raise ValueError(f"generated node has no semantic gap: {item.get('id')}")
            evidence = item["premise_evidence_ids"]
            if (
                not isinstance(evidence, list) or len(evidence) != len(set(evidence))
                or not set(evidence) <= allowed_premises
            ):
                raise ValueError(f"generated node cites illegal premise evidence: {item.get('id')}")
            if not isinstance(item["premise_query"], str):
                raise ValueError(f"generated node premise_query must be text: {item.get('id')}")
            if not evidence and not item["premise_query"].strip():
                raise ValueError(f"generated node has neither premise evidence nor query: {item.get('id')}")
            for dependency in item.get("dependencies", []):
                if dependency in consumers:
                    consumers[dependency].append(item["id"])
        for contract_id, node_dependencies in dependencies.items():
            for dependency in node_dependencies:
                if dependency in consumers:
                    consumers[dependency].append(contract_id)
        for item in generated:
            intended = item["intended_consumers"]
            if (
                not isinstance(intended, list) or len(intended) != len(set(intended))
                or sorted(intended) != sorted(consumers[item["id"]])
            ):
                raise ValueError(f"generated node consumer intent mismatches DAG: {item['id']}")
    for contract in package["contracts"]:
        contract_id = contract["contract_id"]
        strategy = strategies.get(contract_id, "").strip()
        if not strategy:
            raise ValueError(f"contract has no planning strategy: {contract_id}")
        route = routes.get(contract_id)
        if not isinstance(route, dict):
            raise ValueError(f"contract has no auditable route: {contract_id}")
        steps = route.get("semantic_steps")
        if not isinstance(steps, list) or not steps or not all(
            isinstance(step, str) and step.strip() for step in steps
        ):
            raise ValueError(f"contract route has no semantic steps: {contract_id}")
        generated_dependencies = dependencies.get(contract_id, [])
        if generated_dependencies:
            if route.get("proof_shape") != "generated":
                raise ValueError(
                    f"contract with generated dependencies has wrong route: {contract_id}"
                )
            continue
        if route.get("proof_shape") != "direct":
            raise ValueError(
                f"contract without generated dependencies must justify direct proof: "
                f"{contract_id}"
            )
        evidence = route.get("direct_evidence")
        if not isinstance(evidence, dict):
            raise ValueError(f"direct contract has no evidence: {contract_id}")
        kind = evidence.get("kind")
        if kind == "legal_premise" and package.get("repository_projection"):
            allowed = {p['record_id'] for p in (environment or {}).get('legal_premises', [])}
            ids = evidence.get('premise_ids', [])
            if not ids or not set(ids) <= allowed or not evidence.get('detail'):
                raise ValueError('direct route cites no valid visible premise evidence')
            continue
        if kind in {"definitional", "semantic_reduction"}:
            if (
                not isinstance(evidence.get("detail"), str)
                or not evidence["detail"].strip()
            ):
                raise ValueError(
                    f"definitional direct contract has no normalization detail: "
                    f"{contract_id}"
                )
        else:
            raise ValueError(f"direct contract has invalid evidence kind: {contract_id}")
        if VAGUE_EXTERNAL_SUPPORT.search(strategy):
            raise ValueError(
                "direct contract strategy assumes unnamed external support: "
                f"{contract_id}"
            )


def planner_prompt(
    package: dict[str, Any], environment: dict[str, Any] | None = None,
    rejection_feedback: list[str] | None = None,
) -> str:
    payload = {
        "theory": package["theory"],
        "file_slot": package["file_slot"],
        "fixed_contracts": [{
            "id": item["contract_id"],
            "name": item["name"],
            "statement": item["exact_statement"],
            "context": item.get("contract_owned_context", []),
        } for item in package["contracts"]],
        "planning_context": environment or {},
        "previous_plan_rejections": rejection_feedback or [],
    }
    text = """You are the File Planner for one Isabelle/seL4 Proof theory.

This is a closed-payload planning call. Do not invoke tools, skills, shell
commands, file reads, repository search, or network search. Use only the INPUT
payload below. Return the requested JSON directly.

Design the complete generated-theorem DAG needed to prove every fixed contract.
You must provide the exact Isabelle statement of every generated theorem.
Do not write proof bodies. Do not list, predict, or create external premise
nodes. The planning context contains policy metadata only. You do not receive
external premise names or statements. The Lemma Prover retrieves premises later
for one certified executable node.

Roles:
- theorem: an intermediate generated result; it may depend on generated nodes.
- helper: a reusable generated leaf; it has no generated dependencies.
Fixed contracts are roots and cannot be changed. Avoid cosmetic wrappers.
A generated node whose proposition is identical to a contract or another
generated node after removing only the declaration name is invalid.

Return only JSON:
{
  "generated_nodes": [{
    "id": "node:<theory>:<name>",
    "name": "regen_name",
    "role": "theorem|helper",
    "statement": "lemma regen_name: \"...\"",
    "context": [],
    "dependencies": ["node:..."],
    "strategy": "short proof idea",
    "semantic_role": "normalization|semantic_bridge|invariant_preservation|other",
    "reasoning_gap": "the precise missing link between visible premises and consumers",
    "intended_consumers": ["contract or generated node ID"],
    "premise_evidence_ids": ["visible legal premise ID"],
    "premise_query": "bounded query to use when no visible premise has been selected"
  }],
  "contract_dependencies": {"<contract-id>": ["node:..."]},
  "contract_strategies": {"<contract-id>": "short proof idea"},
  "contract_routes": {
    "<contract-id>": {
      "proof_shape": "direct|generated",
      "semantic_steps": ["precise semantic bridge required by the contract"],
      "direct_evidence": {
        "kind": "definitional|semantic_reduction",
        "detail": "exact reductions or semantic bridge; do not name a premise"
      }
    }
  }
}

The context of a generated node must be [] or exactly one context listed on a
fixed contract. A direct contract proof is represented by an empty dependency
list. Use a direct proof only when the contract is plausibly definitional or
requires one precisely described semantic reduction. Never write vague
strategies such as "use the existing/available correspondence rule". If a
contract crosses several semantic operations or relations, expose the
intermediate generated theorem obligations in the DAG. For a generated route, omit
direct_evidence and give at least one contract dependency. For a direct route,
give no generated dependency and justify it by detailed definitional
normalization or a precise one-step semantic reduction. Do not select or name
an external premise; premise choice belongs to runtime retrieval.

INPUT:
""" + json.dumps(payload, ensure_ascii=False, indent=2)
    if package.get("repository_projection"):
        text = text.replace("- helper: a reusable generated leaf; it has no generated dependencies.",
                            "- helper: an ordinary auxiliary result; generated dependencies are allowed.")
        text = text.replace("nodes. The planning context contains policy metadata only. You do not receive\nexternal premise names or statements. The Lemma Prover retrieves premises later\nfor one certified executable node.",
            "nodes. The planning context includes legal premises and revisable repository suggestions. "
            "Only sealed facts are premises; suggested nodes and support contracts are unproved goals. "
            "Refine auxiliary intents into exact statements, or explain why an intent is unnecessary in planning_notes. "
            "A file may have no fixed contracts; then plan its auxiliary goals. "
            "Dependencies are local scheduling prerequisites, never mandatory proof citations. "
            "Cross-file suggestions are not local node IDs. Unconsumed auxiliaries are allowed. "
            "Creation provenance is recorded by the runner; do not invent accepted transactions.")
        text = text.replace("fixed contract. A direct contract", "fixed or support contract. A direct contract")
        text = text.replace("definitional|semantic_reduction", "definitional|semantic_reduction|legal_premise")
        text = text.replace("exact reductions or semantic bridge; do not name a premise", "precise route; legal_premise also requires premise_ids from visible records")
        text = text.replace("Do not select or name\nan external premise; premise choice belongs to runtime retrieval.",
                            "You may cite visible legal premises; actual premise use is decided during proof generation.")
    return text



def plan_file(
    package: dict[str, Any], *, run_id: str, output_dir: Path,
    provider: str = "codex", model: str = "gpt-5.5", timeout: int = 1800,
    environment: dict[str, Any] | None = None, revision: int = 1,
    module_log_path: Path | None = None,
    call_index: int = 1,
    rejection_feedback: list[str] | None = None,
    graph_bindings: dict[str, Any] | None = None,
    persist_graph: bool = True,
) -> dict[str, Any]:
    prompt = planner_prompt(package, environment, rejection_feedback)
    transcript_prefix = allocate_artifact_prefix(
        output_dir / "llm"
        / f"file-plan-v{revision}-attempt-{call_index:03d}"
    )
    try:
        response = call_llm(
            provider, prompt, transcript_prefix,
            model=model, timeout=timeout, theory=package["theory"],
            stage="file-planner", clean_runtime=True,
        )
    finally:
        if (
            module_log_path is not None
            and transcript_prefix.with_suffix(".meta.json").is_file()
        ):
            write_llm_module_log(
                transcript_prefix, module_log_path, module="File Planner",
                run_name=package["file_slot"].removesuffix(".thy"),
                context={
                    "run_id": run_id,
                    "theory": package["theory"],
                    "file_slot": package["file_slot"],
                    "contract_ids": [
                        item["contract_id"] for item in package["contracts"]
                    ],
                    "revision": revision,
                    "call_index": call_index,
                },
            )
    proposal = parse_json_response(response)
    validate_plan_quality(package, proposal, environment)
    graph = build_graph(
        package, proposal, run_id=run_id, revision=revision,
    )
    if package.get("repository_projection"):
        graph["planning_notes"] = proposal.get("planning_notes", "")
        graph["initial_graph_owner"] = "File Planner"
        graph["runtime_graph_owner"] = "Graph Revision Agent"
    transcript = validate_call_artifact(transcript_prefix, require_real=True)
    graph["planner_context"] = {
        "policy": (environment or {}).get("policy", "fixed-contracts-only-v1"),
        "external_premise_records_visible": bool(package.get("repository_projection")),
    }
    graph["upstream_bindings"] = dict(graph_bindings or {})
    graph["planner_call"] = {
        "provider": provider,
        "model": model,
        "transcript_prefix": str(transcript_prefix.resolve()),
        "prompt_sha256": transcript["prompt_sha256"],
        "final_sha256": transcript["final_sha256"],
    }
    graph["graph_payload_sha256"] = graph_payload_sha256(graph)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"graph-v{revision}.json"
    if persist_graph:
        write_immutable_graph(path, graph)
        if module_log_path is not None:
            attach_module_outputs(module_log_path, {"generated_graph": path})
    return graph
