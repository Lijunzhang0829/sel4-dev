#!/usr/bin/env python3
"""Graph Revision Agent: the only model component allowed to change a DAG."""
from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path
from typing import Any

from regen.llm_transcript import allocate_artifact_prefix, call_llm
from regen.module_run_log import attach_module_outputs, write_llm_module_log
from regen.premise_retrieval import retrieve_premises
from regen.proof_graph import (
    assert_green_nodes_unchanged,
    build_graph,
    definitional_wrapper_risks,
    parse_json_response,
    write_immutable_graph,
)


RETRY_FAILURE_CLASSES = {
    "PROOF_BODY_ERROR",
    "PREMISE_RETRIEVAL_GAP",
    "SEARCH_EXHAUSTED",
    "TOOL_OR_TIMEOUT",
}
REVISION_FAILURE_CLASSES = {
    "STATEMENT_CONSUMER_MISMATCH",
    "NODE_TOO_BROAD",
    "GRAPH_DEPENDENCY_GAP",
    "DUPLICATE_GENERATED_NODE",
}
ABANDON_FAILURE_CLASSES = {
    "RESOURCE_BUDGET_EXHAUSTED",
    "UNSOUND_GENERATION",
}


def _semantic_goal(diagnostic: str) -> str | None:
    match = re.search(
        r"\*\*\* goal .*?:\s*(.*?)(?=\n\*\*\* At command|\n### theory|\Z)",
        diagnostic,
        re.S,
    )
    if not match:
        return None
    return " ".join(match.group(1).split())


def semantic_residual_fingerprints(red_result: dict[str, Any]) -> list[str]:
    fingerprints = set()
    for attempt in red_result.get("attempts", []):
        goal = _semantic_goal(attempt.get("check", {}).get("diagnostic", ""))
        if goal:
            fingerprints.add(hashlib.sha256(goal.encode("utf-8")).hexdigest())
    return sorted(fingerprints)



def historical_node_residuals(
    output_dir: Path, node_name: str,
) -> list[dict[str, Any]]:
    """Recover deeper Isabelle goals that a shallow latest failure can hide."""
    evidence: list[dict[str, Any]] = []
    for attempts_path in sorted(
        (output_dir / "nodes").glob(f"*{node_name}/round-*/attempts.json")
    ):
        try:
            attempts = json.loads(attempts_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(attempts, list):
            continue
        for attempt in attempts:
            check = attempt.get("check", {})
            diagnostic = str(check.get("diagnostic", ""))
            goal = _semantic_goal(diagnostic)
            if not goal:
                continue
            lines = [int(item) for item in re.findall(r"line (\d+)", diagnostic)]
            evidence.append({
                "round": attempts_path.parent.name,
                "attempt": attempt.get("attempt"),
                "source_progress_line": max(lines, default=0),
                "semantic_goal": goal[:12000],
                "fingerprint": hashlib.sha256(goal.encode("utf-8")).hexdigest(),
            })
    unique: dict[str, dict[str, Any]] = {}
    for item in evidence:
        old = unique.get(item["fingerprint"])
        if old is None or item["source_progress_line"] > old["source_progress_line"]:
            unique[item["fingerprint"]] = item
    return sorted(
        unique.values(), key=lambda item: item["source_progress_line"], reverse=True,
    )[:12]

def prior_node_decisions(output_dir: Path, node_id: str) -> list[dict[str, Any]]:
    history = []
    for directory in sorted((output_dir / "graph-revisions").glob("decision-*")):
        request_path = directory / "request.json"
        decision_path = directory / "decision.json"
        if not request_path.is_file() or not decision_path.is_file():
            continue
        request = json.loads(request_path.read_text(encoding="utf-8"))
        failed = request.get("failed_node", {})
        context = request.get("decision_context", {})
        if failed.get("node_id") != node_id or context.get("phase") != "node-proof":
            continue
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        transaction_path = directory / "transaction.json"
        transaction = (
            json.loads(transaction_path.read_text(encoding="utf-8"))
            if transaction_path.is_file() else {}
        )
        history.append({
            "decision_id": directory.name,
            "node_id": node_id,
            "proof_round": context.get("proof_round"),
            "decision": decision.get("decision"),
            "failure_class": decision.get("failure_class"),
            "reason": decision.get("reason"),
            "transaction_applied": transaction.get("applied"),
            "transaction_action": transaction.get("action"),
            "transaction_reason": transaction.get("reason"),
            "semantic_residual_fingerprints": semantic_residual_fingerprints(failed),
        })
    return history


def augment_decision_context(
    output_dir: Path, graph: dict[str, Any], red_result: dict[str, Any],
    decision_context: dict[str, Any] | None,
) -> dict[str, Any]:
    context = dict(decision_context or {})
    node_id = red_result.get("node_id", "")
    by_id = {node["id"]: node for node in graph["nodes"]}
    branch_ids = {node_id} if node_id else set()
    pending = [node_id] if node_id else []
    while pending:
        current_id = pending.pop()
        for consumer_id in by_id.get(current_id, {}).get("consumers", []):
            if consumer_id not in branch_ids:
                branch_ids.add(consumer_id)
                pending.append(consumer_id)
    branch_history = []
    for branch_id in branch_ids:
        branch_history.extend(prior_node_decisions(output_dir, branch_id))
    branch_history.sort(key=lambda item: item["decision_id"])
    history = [item for item in branch_history if item["node_id"] == node_id]
    current = set(semantic_residual_fingerprints(red_result))
    repeated_rounds = 1 if current else 0
    for item in history:
        if current.intersection(item["semantic_residual_fingerprints"]):
            repeated_rounds += 1
    consecutive_retries = 0
    for item in reversed(history):
        if item["decision"] != "RETRY_PROOF":
            break
        consecutive_retries += 1
    proof_rounds_used = int(context.get("proof_rounds_used") or 0)
    proof_retry_limit = int(context.get("proof_retry_limit") or 5)
    full_round_retry_cap_reached = proof_rounds_used >= proof_retry_limit
    node_name = by_id.get(node_id, {}).get("name", "")
    historical_residuals = historical_node_residuals(output_dir, node_name)
    current_round = int(context.get("proof_round") or 0)
    stable_historical_residual = any(
        item["fingerprint"] in current
        and int(str(item["round"]).removeprefix("round-") or 0) < current_round
        for item in historical_residuals
    )
    # A caller may have deterministic evidence that the generated statement is
    # invalid. Never weaken that explicit prohibition while enriching context.
    retry_allowed = bool(context.get("retry_proof_allowed", True)) and not (
        full_round_retry_cap_reached or stable_historical_residual
    )
    wrapper_risks = [
        item for item in definitional_wrapper_risks(graph)
        if item["node_id"] == node_id
    ]
    consumer_ids = {item["consumer_id"] for item in wrapper_risks}
    consumer_previously_failed_direct = any(
        item["node_id"] in consumer_ids
        and item["decision"] == "REVISE_GRAPH"
        for item in branch_history
    )
    delete_only_forbidden = bool(
        wrapper_risks and consumer_previously_failed_direct
    )
    context.update({
        "prior_node_decisions": history,
        "historical_isabelle_semantic_residuals": historical_residuals,
        "prior_branch_decisions": branch_history,
        "current_semantic_residual_fingerprints": sorted(current),
        "repeated_semantic_residual_rounds": repeated_rounds,
        "consecutive_retry_proof_decisions": consecutive_retries,
        "proof_retry_limit": proof_retry_limit,
        "full_round_retry_cap_reached": full_round_retry_cap_reached,
        "stable_historical_residual": stable_historical_residual,
        "retry_proof_allowed": retry_allowed,
        "consumer_previously_failed_direct": consumer_previously_failed_direct,
        "delete_only_forbidden": delete_only_forbidden,
    })
    return context


def _statement_assumptions(statement: str) -> list[str]:
    """Return complete outer-syntax assumption blocks from a theorem shell."""
    lines = statement.splitlines()
    assumptions: list[str] = []
    current: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("assumes "):
            if current:
                assumptions.append("\n".join(current))
            current = [stripped]
        elif current and stripped.startswith(("shows ", "obtains ")):
            assumptions.append("\n".join(current))
            current = []
        elif current:
            current.append(stripped)
    if current:
        assumptions.append("\n".join(current))
    return assumptions


def _statement_local_antecedents(statement: str) -> list[str]:
    """Expose premises embedded on the left of a shows implication."""
    antecedents: list[str] = []
    for block in re.findall(
        r"\\<lbrakk>(.*?)\\<rbrakk>\s*\\<Longrightarrow>", statement, re.S,
    ):
        antecedents.extend(
            item.strip() for item in block.split(";") if item.strip()
        )
    shows = re.search(r'shows\s+"(.*)"\s*$', statement, re.S)
    if shows and "\\<lbrakk>" not in shows.group(1):
        implication = shows.group(1).split("\\<Longrightarrow>", 1)
        if len(implication) == 2 and implication[0].strip():
            antecedents.append(implication[0].strip())
    return antecedents


def consumer_assumption_provenance(
    graph: dict[str, Any], failed_node_id: str,
) -> list[dict[str, Any]]:
    """Expose guards that consumers can legally thread into a failed branch."""
    by_id = {node["id"]: node for node in graph["nodes"]}
    if failed_node_id not in by_id:
        return []
    provenance: list[dict[str, Any]] = []
    pending: list[tuple[str, list[str]]] = [(failed_node_id, [failed_node_id])]
    visited = {failed_node_id}
    while pending:
        node_id, path = pending.pop(0)
        for consumer_id in by_id[node_id].get("consumers", []):
            if consumer_id in visited:
                continue
            visited.add(consumer_id)
            consumer = by_id[consumer_id]
            consumer_path = [*path, consumer_id]
            assumptions = _statement_assumptions(consumer.get("statement", ""))
            local_antecedents = _statement_local_antecedents(
                consumer.get("statement", ""),
            )
            if assumptions or local_antecedents:
                provenance.append({
                    "provider_node_id": consumer_id,
                    "provider_role": consumer.get("role"),
                    "distance_from_failed_node": len(consumer_path) - 1,
                    "consumer_path": consumer_path,
                    "available_assumptions": assumptions,
                    "available_local_antecedents": local_antecedents,
                    "provider_statement": consumer.get("statement", ""),
                })
            pending.append((consumer_id, consumer_path))
    return provenance


def revision_request(
    package: dict[str, Any], graph: dict[str, Any],
    red_result: dict[str, Any], green_ids: set[str],
    decision_context: dict[str, Any] | None = None,
    guard_semantic_premises: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "fixed_contracts": [{
            "id": item["contract_id"], "statement": item["exact_statement"],
        } for item in package["contracts"]],
        "current_graph": graph,
        "green_node_ids": sorted(green_ids),
        "failed_node": red_result,
        "assumption_provenance": consumer_assumption_provenance(
            graph, red_result.get("node_id", ""),
        ),
        "guard_semantic_premises": guard_semantic_premises or [],
        "decision_context": decision_context or {},
        "definitional_wrapper_risks": definitional_wrapper_risks(graph),
        "decision_policy": {
            "retry_failure_classes": sorted(RETRY_FAILURE_CLASSES),
            "revision_failure_classes": sorted(REVISION_FAILURE_CLASSES),
            "abandon_failure_classes": sorted(ABANDON_FAILURE_CLASSES),
            "structural_change_requires_residual_evidence": True,
            "fixed_contract_statement_is_immutable": True,
            "fixed_contract_dependencies_are_revisable": True,
            "existing_legal_premise_must_not_be_recreated": True,
            "omitted_consumer_guards_must_be_ruled_out_before_abandon": True,
            "retry_proof_allowed": (decision_context or {}).get(
                "retry_proof_allowed", True
            ),
            "delete_only_forbidden": (decision_context or {}).get(
                "delete_only_forbidden", False
            ),
        },
    }


def revision_prompt(
    package: dict[str, Any], graph: dict[str, Any],
    red_result: dict[str, Any], green_ids: set[str],
    decision_context: dict[str, Any] | None = None,
    guard_semantic_premises: list[dict[str, Any]] | None = None,
) -> str:
    payload = revision_request(
        package, graph, red_result, green_ids, decision_context,
        guard_semantic_premises,
    )
    return """You are the independent Graph Revision Agent.

This is a closed-payload decision call. Do not invoke tools, skills, shell
commands, file reads, repository search, or network search. Use only INPUT and
return the required JSON directly.

Use the Isabelle residual and attempt history to decide whether the generated
theorem graph must change. Fixed contracts and GREEN nodes are immutable.
You may add/delete/split unproved nodes, replace an unproved generated
statement, change an unproved node role, or change generated dependency edges.
A helper with generated children must be promoted to theorem.

"Fixed contract" means its name, context, attributes, and proposition cannot be
changed. It does not mean the contract must remain a leaf: you may add generated
theorem/helper dependencies consumed by a fixed contract. The absence of any
generated node in the initial graph is never, by itself, a reason to reject a
revision.

Classify the failure before deciding. Syntax/type/tactic errors, a weak premise
query, one-off search exhaustion, and a timeout are proof-level failures and
must not change the graph. Revise only when the Isabelle residual and failed consumer
show a statement/consumer mismatch, an overloaded node that needs splitting, a
missing generated dependency, or a duplicate generated node.

decision_context may contain trusted_framework_semantic_evidence produced by
the deterministic harness. If that evidence gives defining equations or a
counterexample showing that an unproved generated statement is false, this is
not a proof-retrieval failure. RETRY_PROOF is forbidden: remove or replace the
false node and every unproved node whose statement depends on the same invalid
inference. Re-plan that branch from fixed-contract assumptions and legal
framework semantics. Do not expose or infer any human proof body.

Never repair a failed node by moving its residual goal or missing bridge into a
new assumption. Every assumption newly introduced by a replacement must name
an available provider in assumption_provenance: a fixed-consumer assumption, a
GREEN generated dependency, or a legal framework premise. If no provider is
available, preserve the genuine obligation and request focused premise
retrieval, or introduce a smaller generated node that proves the bridge rather
than assuming it.

Before abandoning an unsound generated branch theorem, compare its assumptions
with those of its direct and transitive consumers. A planner may have dropped a
fixed-contract or ancestor guard while extracting the branch. If such a guard
is listed in assumption_provenance and makes the branch smaller or impossible,
prefer REPLACE_STATEMENT or SPLIT to thread that existing guard through the
generated branch. This is not assumption laundering: the replacement must name
the exact existing provider, preserve the guard unchanged, and connect the
revised node to the consumer that supplies it. ABANDON_GENERATION is permitted
only after ruling out omitted available guards as well as legal premises.
guard_semantic_premises is a focused legal-premise query over those consumer
guards. Use its defining equations to decide whether a guard makes the failed
branch impossible or changes the needed statement. Do not ignore a guard merely
because it was absent from the failed leaf's original premise query.
assumption_provenance includes both outer available_assumptions and premises
embedded in a consumer's implication as available_local_antecedents. The latter
may carry essential type constraints such as P obj paired with a typed P and
ob'. A revision may thread them only along the listed consumer_path where that
consumer establishes them.
Isabelle type constraints are semantic guards too. When the failed node uses
`obj` and `ob'` through the same type variable and class instance (for example
`obj ob' :: 'a::pspace_storable`, or `P :: 'a => bool` with `P obj`), retain
that shared type in any replacement. Combine it with defining equations in
guard_semantic_premises: if a relation restricts `injectKO ob'` to a constructor
family, the same injectKO instance can make a PDE/PTE branch impossible. In
that case prefer replacing the transfer lemma by an impossibility lemma (or
threading the guard into the parent) instead of demanding payload equality.

If Isabelle show_types exposes an unconstrained schematic result type in a generated
node (for example an existential result has type 'b while the successful local
fact has a concrete endpoint type), classify it as STATEMENT_CONSUMER_MISMATCH.
Use REPLACE_STATEMENT to add the intended type constraint. Do not add a helper:
no helper can make an accidentally polymorphic generated proposition match its
monomorphic consumer. Fixed contract statements remain immutable.

For statement certification, decision_context may name
failed_statement_node_id. In that case, change only that node and its transitive
consumers. Preserve every unrelated branch exactly. Review
previous_revision_rejections before proposing another transaction.

Repeated search exhaustion can become structural evidence when attempts expose
the same stable intermediate obligation and the retrieved legal premises do not
contain a theorem connecting that obligation to its consumer. In that case,
classify the failure as GRAPH_DEPENDENCY_GAP or NODE_TOO_BROAD and propose the
smallest generated theorem DAG that isolates the obligation. Do not keep
retrying unchanged merely because the consumer is a fixed contract.

decision_context contains prior_node_decisions and normalized semantic residual
fingerprints. If retry_proof_allowed is false, RETRY_PROOF is forbidden: choose
the smallest justified structural revision, or abandon if no sound split can be
stated. A final invalid fact reference must not hide stable semantic residuals
from earlier candidates or rounds. Remove or replace any node listed in
definitional_wrapper_risks; unfolding one wrapper operation is not meaningful
theorem decomposition.
If delete_only_forbidden is true, deleting the wrapper and returning its fixed
consumer to a direct proof is forbidden because that direct route already
failed. Replace or split the wrapper into strictly smaller semantic obligations
that lower proof complexity. Typical semantic boundaries include no-fail,
successful-result simulation, lookup characterization, and state-relation
preservation; choose only boundaries justified by the supplied residuals.

Before revising, inspect retrieved_legal_premises in the failed result. If one
of those legal premises already states the residual bridge, or can discharge it
by direct specialization and simplification, classify this as
PREMISE_RETRIEVAL_GAP or PROOF_BODY_ERROR and RETRY_PROOF. Never add a generated
helper that merely renames, specializes, or cosmetically wraps an existing
legal premise. A structural revision requires evidence that no retrieved legal
premise supplies the missing semantic connection.

Every answer must include:
- failure_class: exactly one class listed in decision_policy;
- reason: a concise public approval rationale;
- residual_evidence: a non-empty list of concrete observations from the input;
- alternatives_considered: a list explaining why retry or revision was rejected;
- confidence: a number from 0 to 1.

Return exactly one of:

{
  "decision":"RETRY_PROOF",
  "failure_class":"PROOF_BODY_ERROR|PREMISE_RETRIEVAL_GAP|SEARCH_EXHAUSTED|TOOL_OR_TIMEOUT",
  "reason":"the graph is sound; search or retrieval should continue",
  "residual_evidence":["..."],
  "alternatives_considered":["..."],
  "confidence":0.8,
  "retrieval_query":"optional focused query",
  "repair_guidance":"what the next prover round should try"
}

{
  "decision":"ABANDON_GENERATION",
  "failure_class":"RESOURCE_BUDGET_EXHAUSTED|UNSOUND_GENERATION",
  "reason":"the current generation cannot proceed",
  "residual_evidence":["..."],
  "alternatives_considered":["..."],
  "confidence":0.8
}

or

{
  "decision":"REVISE_GRAPH",
  "failure_class":"STATEMENT_CONSUMER_MISMATCH|NODE_TOO_BROAD|GRAPH_DEPENDENCY_GAP|DUPLICATE_GENERATED_NODE",
  "reason":"residual evidence shows a missing/split/wrong generated obligation",
  "residual_evidence":["..."],
  "alternatives_considered":["..."],
  "confidence":0.8,
  "operation":"ADD|DELETE|SPLIT|CHANGE_ROLE|REPLACE_STATEMENT|CHANGE_EDGE",
  "generated_nodes":[same schema as File Planner],
  "contract_dependencies":{"<contract-id>":["node:..."]},
  "contract_strategies":{"<contract-id>":"..."},
  "contract_routes":{"<contract-id>":{
    "proof_shape":"direct|generated",
    "semantic_steps":["..."],
    "direct_evidence":{"kind":"definitional|semantic_reduction","detail":"..."}
  }}
}

The replacement describes the complete generated portion of the graph. Do not
include proof bodies or external premise nodes.

INPUT:
""" + json.dumps(payload, ensure_ascii=False, indent=2)


def validate_decision(
    decision: dict[str, Any], request: dict[str, Any] | None = None,
) -> None:
    action = decision.get("decision")
    failure_class = decision.get("failure_class")
    expected = {
        "RETRY_PROOF": RETRY_FAILURE_CLASSES,
        "REVISE_GRAPH": REVISION_FAILURE_CLASSES,
        "ABANDON_GENERATION": ABANDON_FAILURE_CLASSES,
    }.get(action)
    if expected is None:
        raise ValueError("unknown Graph Revision Agent decision")
    if failure_class not in expected:
        raise ValueError(
            f"failure class {failure_class!r} cannot authorize {action}"
        )
    if (
        action == "RETRY_PROOF"
        and request is not None
        and not request.get("decision_policy", {}).get("retry_proof_allowed", True)
    ):
        raise ValueError(
            "RETRY_PROOF forbidden after repeated semantic non-progress"
        )
    if (
        action == "REVISE_GRAPH"
        and decision.get("operation") == "DELETE"
        and request is not None
        and request.get("decision_policy", {}).get(
            "delete_only_forbidden", False
        )
    ):
        raise ValueError(
            "DELETE-only revision forbidden after direct consumer failure"
        )
    if not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
        raise ValueError("graph decision has no public approval rationale")
    evidence = decision.get("residual_evidence")
    if not isinstance(evidence, list) or not evidence or not all(
        isinstance(item, str) and item.strip() for item in evidence
    ):
        raise ValueError("graph decision requires concrete residual evidence")
    alternatives = decision.get("alternatives_considered")
    if not isinstance(alternatives, list) or not all(
        isinstance(item, str) and item.strip() for item in alternatives
    ):
        raise ValueError("graph decision alternatives must be a list of strings")
    confidence = decision.get("confidence")
    if (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not 0 <= confidence <= 1
    ):
        raise ValueError("graph decision confidence must be between zero and one")


def graph_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    old_nodes = {node["id"]: node for node in old["nodes"]}
    new_nodes = {node["id"]: node for node in new["nodes"]}
    changed = []
    for node_id in sorted(old_nodes.keys() & new_nodes.keys()):
        fields = sorted(
            key for key in set(old_nodes[node_id]) | set(new_nodes[node_id])
            if old_nodes[node_id].get(key) != new_nodes[node_id].get(key)
        )
        if fields:
            changed.append({"node_id": node_id, "fields": fields})
    return {
        "from_revision": old["revision"],
        "to_revision": new["revision"],
        "added_nodes": sorted(new_nodes.keys() - old_nodes.keys()),
        "deleted_nodes": sorted(old_nodes.keys() - new_nodes.keys()),
        "changed_nodes": changed,
        "execution_order_changed": (
            old.get("execution_order") != new.get("execution_order")
        ),
    }


def revision_scope(graph: dict[str, Any], failed_node_id: str) -> set[str]:
    by_id = {node["id"]: node for node in graph["nodes"]}
    if failed_node_id not in by_id:
        raise ValueError(f"statement failure names unknown node: {failed_node_id}")
    scope = {failed_node_id}
    pending = [failed_node_id]
    while pending:
        node_id = pending.pop()
        for consumer in by_id[node_id]["consumers"]:
            if consumer not in scope:
                scope.add(consumer)
                pending.append(consumer)
    return scope


def replacement_scope(graph: dict[str, Any], failed_node_id: str) -> set[str]:
    """Include downstream nodes and helpers used only by that downstream."""
    by_id = {node["id"]: node for node in graph["nodes"]}
    scope = revision_scope(graph, failed_node_id)
    while True:
        added = {
            node_id for node_id, node in by_id.items()
            if node_id not in scope
            and node.get("role") != "contract"
            and node.get("consumers")
            and all(
                consumer in scope
                and by_id.get(consumer, {}).get("role") != "contract"
                for consumer in node["consumers"]
            )
        }
        if not added:
            return scope
        scope.update(added)


def assert_nodes_outside_scope_unchanged(
    old: dict[str, Any], new: dict[str, Any], scope: set[str],
) -> None:
    old_nodes = {node["id"]: node for node in old["nodes"]}
    new_nodes = {node["id"]: node for node in new["nodes"]}
    for node_id in set(old_nodes) - scope:
        if new_nodes.get(node_id) != old_nodes[node_id]:
            raise ValueError(
                f"graph revision changed node outside failure scope: {node_id}"
            )


def merge_contract_metadata(
    graph: dict[str, Any], decision: dict[str, Any],
) -> dict[str, Any]:
    old_contracts = {
        node["id"]: node for node in graph["nodes"]
        if node["role"] == "contract"
    }
    merged = dict(decision)
    old_dependencies = {
        node_id: list(node["dependencies"])
        for node_id, node in old_contracts.items()
    }
    proposed_dependencies = decision.get("contract_dependencies", {})
    if decision.get("operation") == "ADD":
        merged["contract_dependencies"] = {
            node_id: list(dict.fromkeys(
                old_dependencies.get(node_id, [])
                + proposed_dependencies.get(node_id, [])
            ))
            for node_id in old_dependencies.keys() | proposed_dependencies.keys()
        }
    else:
        merged["contract_dependencies"] = {
            **old_dependencies, **proposed_dependencies,
        }
    merged["contract_strategies"] = {
        **{
            node_id: node.get("strategy", "")
            for node_id, node in old_contracts.items()
        },
        **decision.get("contract_strategies", {}),
    }
    merged["contract_routes"] = {
        **graph.get("contract_routes", {}),
        **decision.get("contract_routes", {}),
    }
    return merged


def merge_generated_metadata(
    graph: dict[str, Any], decision: dict[str, Any],
    replaced_node_id: str | None = None,
) -> dict[str, Any]:
    """Overlay a partial revision payload and preserve unrelated nodes."""
    current = {
        node["id"]: {
            key: value for key, value in node.items()
            if key != "consumers"
        }
        for node in graph["nodes"] if node["role"] != "contract"
    }
    if decision.get("operation") in {"SPLIT", "DELETE"} and replaced_node_id:
        current.pop(replaced_node_id, None)
    for node in decision.get("generated_nodes", []):
        current[node["id"]] = dict(node)
    merged = dict(decision)
    merged["generated_nodes"] = list(current.values())
    return merged


def prune_unconsumed_replacement_nodes(
    proposal: dict[str, Any], removable_ids: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """Drop stale unproved nodes made unreachable by a local replacement."""
    nodes = {
        node["id"]: dict(node)
        for node in proposal.get("generated_nodes", [])
    }
    removed: list[str] = []
    while True:
        referenced = {
            dependency
            for node in nodes.values()
            for dependency in node.get("dependencies", [])
        }
        referenced.update(
            dependency
            for dependencies in proposal.get(
                "contract_dependencies", {}
            ).values()
            for dependency in dependencies
        )
        stale = sorted(
            node_id for node_id in nodes
            if node_id in removable_ids and node_id not in referenced
        )
        if not stale:
            break
        for node_id in stale:
            nodes.pop(node_id)
            removed.append(node_id)
    normalized = dict(proposal)
    normalized["generated_nodes"] = list(nodes.values())
    return normalized, removed


def preserve_orphaned_green_dependencies(
    graph: dict[str, Any], proposal: dict[str, Any], replacement_node_id: str,
    green_ids: set[str],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Keep sealed dependencies reachable when replacing their only consumer."""
    old_nodes = {node["id"]: node for node in graph["nodes"]}
    new_nodes = {
        node["id"]: dict(node)
        for node in proposal.get("generated_nodes", [])
    }
    replacement = new_nodes.get(replacement_node_id)
    old_replacement = old_nodes.get(replacement_node_id)
    if replacement is None or old_replacement is None:
        return proposal, []

    referenced = {
        dependency
        for node in new_nodes.values()
        for dependency in node.get("dependencies", [])
    }
    for dependencies in proposal.get("contract_dependencies", {}).values():
        referenced.update(dependencies)

    changes = []
    dependencies = list(replacement.get("dependencies", []))
    for dependency in old_replacement.get("dependencies", []):
        old_dependency = old_nodes.get(dependency)
        if (
            dependency in green_ids
            and dependency not in referenced
            and old_dependency is not None
            and old_dependency.get("consumers") == [replacement_node_id]
        ):
            dependencies.append(dependency)
            referenced.add(dependency)
            changes.append({
                "node_id": replacement_node_id,
                "field": "dependencies",
                "from": "dropped-green-dependency",
                "to": dependency,
                "reason": "GREEN generated nodes cannot become unconsumed",
            })
    if not changes:
        return proposal, []
    replacement["dependencies"] = dependencies
    normalized = dict(proposal)
    normalized["generated_nodes"] = list(new_nodes.values())
    return normalized, changes


def normalize_generated_roles(
    proposal: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Promote generated helpers that gained generated dependencies."""
    normalized = dict(proposal)
    generated_nodes = []
    changes = []
    for node in proposal.get("generated_nodes", []):
        normalized_node = dict(node)
        if (
            normalized_node.get("role") == "helper"
            and normalized_node.get("dependencies")
        ):
            normalized_node["role"] = "theorem"
            changes.append({
                "node_id": normalized_node["id"],
                "field": "role",
                "from": "helper",
                "to": "theorem",
                "reason": "generated nodes with children are not helper leaves",
            })
        generated_nodes.append(normalized_node)
    normalized["generated_nodes"] = generated_nodes
    return normalized, changes


def next_graph_revision(output_dir: Path, current_revision: int) -> int:
    revisions = [
        int(path.stem.removeprefix("graph-v"))
        for path in output_dir.glob("graph-v*.json")
        if path.stem.removeprefix("graph-v").isdigit()
    ]
    return max([current_revision, *revisions], default=current_revision) + 1


def revise_graph(
    package: dict[str, Any], graph: dict[str, Any], red_result: dict[str, Any],
    green_ids: set[str], *, output_dir: Path, provider: str = "codex",
    model: str = "gpt-5.5", timeout: int = 1800,
    decision_index: int = 1,
    decision_context: dict[str, Any] | None = None,
    allow_structural_revision: bool = True,
    premise_database: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    revision = next_graph_revision(output_dir, graph["revision"])
    decision_context = augment_decision_context(
        output_dir, graph, red_result, decision_context,
    )
    provenance = consumer_assumption_provenance(
        graph, red_result.get("node_id", ""),
    )
    guard_semantic_premises: list[dict[str, Any]] = []
    if premise_database is not None and provenance:
        guard_query = "\n".join(
            premise
            for item in provenance
            for premise in (
                item["available_assumptions"]
                + item["available_local_antecedents"]
            )
        )
        guard_semantic_premises = retrieve_premises(
            premise_database, guard_query, limit=24, focus_query=guard_query,
        )
    request = revision_request(
        package, graph, red_result, green_ids, decision_context,
        guard_semantic_premises,
    )
    audit_dir = output_dir / "graph-revisions" / f"decision-{decision_index:03d}"
    audit_dir.mkdir(parents=True)
    (audit_dir / "request.json").write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    prompt = revision_prompt(
        package, graph, red_result, green_ids, decision_context,
        guard_semantic_premises,
    )
    transcript_prefix = allocate_artifact_prefix(
        output_dir / "llm" / (
            f"graph-decision-{decision_index:03d}-from-v{graph['revision']}"
        )
    )
    module_log_path = (
        output_dir.parent.parent / "module-logs/graph-revision-agent"
        / (
            f"{graph['file_slot'].removesuffix('.thy')}"
            f"-decision-{decision_index:03d}.json"
        )
    )
    try:
        response = call_llm(
            provider, prompt, transcript_prefix,
            model=model, timeout=timeout, theory=graph["theory"],
            stage="graph-revision-agent",
        )
    finally:
        if transcript_prefix.with_suffix(".meta.json").is_file():
            write_llm_module_log(
                transcript_prefix, module_log_path,
                module="Graph Revision Agent",
                run_name=(
                    f"{graph['file_slot'].removesuffix('.thy')}"
                    f"-decision-{decision_index:03d}"
                ),
                context={
                    "theory": graph["theory"],
                    "graph_revision": graph["revision"],
                    "decision_index": decision_index,
                },
            )
    decision = parse_json_response(response)
    validate_decision(decision, request)
    (audit_dir / "decision.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    if decision.get("decision") in {"RETRY_PROOF", "ABANDON_GENERATION"}:
        transaction_path = audit_dir / "transaction.json"
        transaction_path.write_text(json.dumps({
            "applied": False,
            "action": decision["decision"],
            "graph_revision": graph["revision"],
            "reason": "graph unchanged by policy",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        attach_module_outputs(module_log_path, {
            "request": audit_dir / "request.json",
            "decision": audit_dir / "decision.json",
            "transaction": transaction_path,
        })
        return graph, decision
    if not allow_structural_revision:
        transaction_path = audit_dir / "transaction.json"
        transaction_path.write_text(json.dumps({
            "applied": False,
            "action": decision["decision"],
            "graph_revision": graph["revision"],
            "reason": "structural revision budget exhausted before transaction",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        attach_module_outputs(module_log_path, {
            "request": audit_dir / "request.json",
            "decision": audit_dir / "decision.json",
            "transaction": transaction_path,
        })
        return graph, decision
    if decision.get("operation") not in {
        "ADD", "DELETE", "SPLIT", "CHANGE_ROLE",
        "REPLACE_STATEMENT", "CHANGE_EDGE",
    }:
        raise ValueError("graph revision has no valid operation")
    merged_decision = merge_contract_metadata(graph, decision)
    merged_decision = merge_generated_metadata(
        graph, merged_decision, replaced_node_id=red_result.get("node_id"),
    )
    pruned_nodes: list[str] = []
    failed_statement = (decision_context or {}).get(
        "failed_statement_node_id"
    )
    replacement_root = failed_statement or red_result.get("node_id")
    if replacement_root and decision.get("operation") in {
        "REPLACE_STATEMENT", "SPLIT", "CHANGE_EDGE",
    }:
        removable = replacement_scope(graph, replacement_root) - green_ids
        merged_decision, pruned_nodes = prune_unconsumed_replacement_nodes(
            merged_decision, removable,
        )
    merged_decision, preserved_green_edges = (
        preserve_orphaned_green_dependencies(
            graph, merged_decision, replacement_root, green_ids,
        ) if replacement_root else (merged_decision, [])
    )
    normalized_decision, normalizations = normalize_generated_roles(
        merged_decision
    )
    normalizations.extend(preserved_green_edges)
    normalizations.extend({
        "node_id": node_id,
        "field": "node",
        "from": "stale-unproved",
        "to": "removed",
        "reason": "unconsumed after local replacement",
    } for node_id in pruned_nodes)
    try:
        revised = build_graph(
            package, normalized_decision,
            run_id=graph["run_id"], revision=revision,
        )
        assert_green_nodes_unchanged(graph, revised, green_ids)
        if failed_statement:
            assert_nodes_outside_scope_unchanged(
                graph, revised, replacement_scope(graph, failed_statement),
            )
        revised["parent_revision"] = graph["revision"]
        revised["source_decision"] = f"decision-{decision_index:03d}"
    except ValueError as error:
        transaction_path = audit_dir / "transaction.json"
        transaction_path.write_text(json.dumps({
            "applied": False,
            "action": "REJECT_INVALID_REVISION",
            "graph_revision": graph["revision"],
            "reason": str(error),
            "deterministic_normalizations": normalizations,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        attach_module_outputs(module_log_path, {
            "request": audit_dir / "request.json",
            "decision": audit_dir / "decision.json",
            "transaction": transaction_path,
        })
        retry_decision = {
            **decision,
            "decision": "RETRY_PROOF",
            "failure_class": "PROOF_BODY_ERROR",
            "retrieval_query": decision.get("retrieval_query", ""),
            "repair_guidance": (
                "The proposed graph transaction was rejected by deterministic "
                f"validation: {error}"
            ),
        }
        return graph, retry_decision
    path = output_dir / f"graph-v{revision}.json"
    try:
        write_immutable_graph(path, revised)
    except FileExistsError as error:
        transaction_path = audit_dir / "transaction.json"
        transaction_path.write_text(json.dumps({
            "applied": False,
            "action": "REJECT_REVISION_COLLISION",
            "graph_revision": graph["revision"],
            "reason": str(error),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        retry_decision = {
            **decision,
            "decision": "RETRY_PROOF",
            "failure_class": "PROOF_BODY_ERROR",
            "repair_guidance": str(error),
        }
        return graph, retry_decision
    transaction_path = audit_dir / "transaction.json"
    transaction_path.write_text(json.dumps({
        "applied": True,
        "action": decision["decision"],
        "operation": decision["operation"],
        "diff": graph_diff(graph, revised),
        "deterministic_normalizations": normalizations,
        "certification_pending": True,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    attach_module_outputs(module_log_path, {
        "request": audit_dir / "request.json",
        "decision": audit_dir / "decision.json",
        "transaction": transaction_path,
        "revised_graph": path,
    })
    return revised, decision
