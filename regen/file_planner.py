#!/usr/bin/env python3
"""File Planner: produce statements and a generated-theorem DAG, never proofs."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from regen.llm_transcript import allocate_artifact_prefix, call_llm
from regen.module_run_log import attach_module_outputs, write_llm_module_log
from regen.premise_retrieval import retrieve_premises, tokens
from regen.proof_graph import (
    build_graph, parse_json_response, write_immutable_graph,
)


VAGUE_EXTERNAL_SUPPORT = re.compile(
    r"\b(?:existing|available|known|standard)\b", re.IGNORECASE,
)
PLANNING_STOP_TOKENS = {
    "assumes", "contract", "corres", "dc", "fixes", "lemma", "longrightarrow",
    "relation", "shows", "state", "theorem", "using",
}


def planning_query(statement: str) -> str:
    """Keep operation/relation identifiers, avoiding broad proof vocabulary."""
    terms = [
        term for term in dict.fromkeys(tokens(statement))
        if term not in PLANNING_STOP_TOKENS
        and (
            "_" in term
            or term.endswith(("corres", "relation", "object", "endpoint"))
            or any(character.isupper() for character in term)
        )
    ]
    if not terms:
        terms = [
            term for term in dict.fromkeys(tokens(statement))
            if term not in PLANNING_STOP_TOKENS
        ]
    return " ".join(terms[:16])


def planning_environment(
    package: dict[str, Any], premise_database: Path, *,
    per_contract: int = 12, total_limit: int = 48,
) -> dict[str, Any]:
    """Retrieve legal context for understanding contracts, not DAG premises."""
    records: dict[str, dict[str, Any]] = {}
    for contract in package["contracts"]:
        statement = contract["exact_statement"]
        query = planning_query(statement)
        for record in retrieve_premises(
            premise_database, query, limit=per_contract,
            focus_query=query,
        ):
            records.setdefault(record["record_id"], record)
    ranked = sorted(
        records.values(),
        key=lambda item: (-item.get("score", 0.0), item["canonical_name"]),
    )[:total_limit]
    return {
        "policy": "legal-declaration-context-v1",
        "use": (
            "Context for semantic planning only. These declarations are not "
            "DAG nodes or committed proof dependencies."
        ),
        "legal_declarations": [{
            "name": item["canonical_name"],
            "statement": item["exact_statement"],
            "owner_theory": item["owner_theory"],
            "source_class": item["source_class"],
        } for item in ranked],
    }


def validate_plan_quality(
    package: dict[str, Any], proposal: dict[str, Any],
    environment: dict[str, Any] | None,
) -> None:
    """Reject unsupported direct-proof claims before creating graph-v1."""
    strategies = proposal.get("contract_strategies", {})
    routes = proposal.get("contract_routes", {})
    dependencies = proposal.get("contract_dependencies", {})
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
    return """You are the File Planner for one Isabelle/seL4 Proof theory.

This is a closed-payload planning call. Do not invoke tools, skills, shell
commands, file reads, repository search, or network search. Use only the INPUT
payload below. Return the requested JSON directly.

Design the complete generated-theorem DAG needed to prove every fixed contract.
You must provide the exact Isabelle statement of every generated theorem.
Do not write proof bodies. Do not list, predict, or create external premise
nodes. The planning context contains legal declarations only so that you can
understand definitions and likely semantic boundaries. It is not a commitment
that any declaration will be used; the Lemma Prover retrieves premises later.

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
    "strategy": "short proof idea"
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


def plan_file(
    package: dict[str, Any], *, run_id: str, output_dir: Path,
    provider: str = "codex", model: str = "gpt-5.5", timeout: int = 1800,
    environment: dict[str, Any] | None = None, revision: int = 1,
    module_log_path: Path | None = None,
    call_index: int = 1,
    rejection_feedback: list[str] | None = None,
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
            stage="file-planner",
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
    graph["planner_context"] = {
        "policy": (environment or {}).get("policy", "contracts-only"),
        "legal_declaration_count": len(
            (environment or {}).get("legal_declarations", [])
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"graph-v{revision}.json"
    write_immutable_graph(path, graph)
    if module_log_path is not None:
        attach_module_outputs(module_log_path, {"generated_graph": path})
    return graph
