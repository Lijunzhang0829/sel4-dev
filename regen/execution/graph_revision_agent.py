#!/usr/bin/env python3
"""Graph Revision Agent: the only model component allowed to change a DAG."""
from __future__ import annotations

import json
import time
import hashlib
import re
from pathlib import Path
from typing import Any
from regen.execution.proof_input import render_prompt, InputBudgetExceeded, expand_graph_decision, graph_view_scope

from regen.infrastructure.llm_transcript import allocate_artifact_prefix, call_llm
from regen.infrastructure.module_run_log import attach_module_outputs, write_llm_module_log
from regen.planning.premise_retrieval import retrieve_premises
from regen.planning.proof_graph import (
    assert_green_nodes_unchanged,
    build_graph,
    definitional_wrapper_risks,
    parse_json_response,
    write_immutable_graph,
)


RETRY_FAILURE_CLASSES = {
    "OBSERVATION_BUDGET_EXHAUSTED",
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
RETRY_MATERIAL_FIELDS = (
    "retrieval_query",
    "repair_guidance",
    "outline_directive",
    "frontier_selection",
    "automation_policy",
    "proof_repair_diagnosis",
)


def normalized_retry_directive(decision: dict[str, Any]) -> dict[str, str]:
    """Return the material, reviewable change proposed by RETRY_PROOF."""
    directive: dict[str, str] = {}
    for field in RETRY_MATERIAL_FIELDS:
        value = decision.get(field)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, str):
            rendered = " ".join(value.split())
        else:
            rendered = json.dumps(
                value, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            )
        if rendered:
            directive[field] = rendered
    return directive


def retry_directive_sha256(decision: dict[str, Any]) -> str | None:
    directive = normalized_retry_directive(decision)
    if not directive:
        return None
    payload = json.dumps(
        directive, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


from regen.execution.proof_feedback import semantic_goal as _semantic_goal


def semantic_residual_fingerprints(red_result: dict[str, Any]) -> list[str]:
    fingerprints = set()
    for attempt in red_result.get("checked_history", []) + red_result.get("attempts", []):
        if attempt.get("check", {}).get("isabelle_called") is False:
            continue
        goal = _semantic_goal(attempt.get("check", {}).get("diagnostic", ""))
        if goal:
            fingerprints.add(hashlib.sha256(goal.encode("utf-8")).hexdigest())
    return sorted(fingerprints)



def historical_node_residuals(
    output_dir: Path, node_name: str, *, node_id: str = "",
) -> list[dict[str, Any]]:
    """Recover deeper Isabelle goals that a shallow latest failure can hide."""
    evidence: list[dict[str, Any]] = []
    for attempts_path in sorted(
        (output_dir / "nodes").glob(
            (node_id.replace('/', '_').replace(':', '_') if node_id else f"*{node_name}")
            + "/round-*/attempts.json")
    ):
        try:
            attempts = json.loads(attempts_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(attempts, list):
            continue
        for attempt in attempts:
            check = attempt.get("check", {})
            if check.get("isabelle_called") is False:
                continue
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
            "retry_directive": normalized_retry_directive(decision),
            "retry_directive_sha256": retry_directive_sha256(decision),
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
        if "accepted_decision_history" in context:
            branch_history.extend(item for item in context["accepted_decision_history"] if item["node_id"] == branch_id)
        else:
            branch_history.extend(prior_node_decisions(output_dir, branch_id))
    branch_history.sort(key=lambda item: item["decision_id"])
    history = [item for item in branch_history if item["node_id"] == node_id]
    # Retained checked_history is repair evidence, not a new observation.
    # Comparing it with historical rounds would let an old goal match itself.
    current = set(semantic_residual_fingerprints({
        "attempts": red_result.get("attempts", []),
    }))
    repeated_rounds = 1 if current else 0
    for item in history:
        if current.intersection(item["semantic_residual_fingerprints"]):
            repeated_rounds += 1
    consecutive_retries = 0
    for item in reversed(history):
        if item["decision"] != "RETRY_PROOF":
            break
        consecutive_retries += 1
    latest_retry = next((
        item for item in reversed(history)
        if item.get("decision") == "RETRY_PROOF"
        and item.get("retry_directive_sha256")
    ), None)
    proof_rounds_used = int(context.get("proof_rounds_used") or 0)
    proof_retry_limit = int(context.get("proof_retry_limit") or 5)
    full_round_retry_cap_reached = proof_rounds_used >= proof_retry_limit
    node_name = by_id.get(node_id, {}).get("name", "")
    historical_residuals = historical_node_residuals(output_dir, node_name, node_id=node_id)
    current_round = int(context.get("proof_round") or context.get("proof_rounds_used") or 0)
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
        "current_retry_directive": (
            latest_retry.get("retry_directive") if latest_retry else None
        ),
        "current_retry_directive_sha256": (
            latest_retry.get("retry_directive_sha256")
            if latest_retry else None
        ),
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
    input_audit_path: Path | None = None,
    observations: list | None = None,
) -> str:
    payload = revision_request(
        package, graph, red_result, green_ids, decision_context,
        guard_semantic_premises,
    )
    payload["observations"] = observations or []
    instructions = """You are the Graph Revision Agent. Closed-payload JSON only; no tools or proof bodies.
Observation budgets are separate from proof attempts. observation_budget_exhausted
means a query-control failure, not an empty proof or Isabelle rejection. Inspect
observation_requests and the persistent recalled_facts before selecting a retry plan.
Fixed contracts and GREEN nodes are immutable. Syntax/tactic/search/timeout failures
may justify RETRY_PROOF; repeated failures must also be assessed for structural gaps.
Use historical residuals and prior decisions to distinguish tactic repair from a node
that combines several unresolved obligations. SPLIT can isolate these obligations;
CHANGE_EDGE can reuse an accepted result. Explain why another retry can make progress.
Structural revisions need concrete residual evidence, not merely exhausted attempts.
Never add a generated helper that merely renames or wraps an existing legal premise.
Unknown facts are not evidence of absence. Inspect consumer guards and shared types
before abandonment. New assumptions need an existing provider and consumer_path;
prove missing bridges. Trusted counterevidence forbids retrying false statements.
SPLIT preserves its parent. Auxiliary replacement/revocation is explicit. Promote
helpers with children to theorem. Preserve GREEN contexts and unrelated branches.
Respect decision_policy, phase, retry/deletion restrictions, rejections and budgets.
Statement certification has no proof to retry. Stable residuals may justify splitting;
revisions never reset costs. Replace definitional wrappers with genuine obligations.
Required response fields: decision, failure_class (from decision_policy), reason,
residual_evidence (nonempty list of nonempty strings describing actual checker evidence),
alternatives_considered (list of strings, not objects), confidence (0..1).
RETRY_PROOF: include a concrete retrieval_query or repair_guidance.
ABANDON_GENERATION: explain why no sound continuation remains.
REVISE_GRAPH: operation (ADD|DELETE|SPLIT|CHANGE_ROLE|REPLACE_STATEMENT|CHANGE_EDGE),
generated_nodes (changed/new nodes only), contract_dependencies (ID to ID list);
optional contract_strategies, contract_routes. Node fields: id,name,role (helper|theorem),
statement,context,strategy,dependencies. No external premise nodes. Runner merges
omitted nodes unchanged. The local graph is an initial view, not a restriction on legal edits.
Use current IDs. All proposed edits are validated against the complete graph.
If previous_revision_rejection is present, its proposal was NOT committed. Correct
its generated declarations using the statement checker feedback and resubmit a complete
revision against the active graph. Never alter fixed contracts or GREEN statements.
A rejected declaration is not a failed proof of that proposition.
Before deciding you may request goal/context retrieval from a v2 catalog using
{"observation_request":{"proof_state_query":true}} (optional "use_vectors":true).
Alternatively return {"observation_request":{"premise_query":"name or terms",
"scope":"all","offset":0}} for complete facts from upstream/sealed/local GREEN sources;
or {"observation_request":{"auxiliary_query":{"node_ids":["accepted-node-id"]}}}
for full checked same-theory proofs and contexts (at most 4, accepted visible prefix only);
or {"observation_request":{"graph_query":{"node_ids":[],"query":"terms","offset":0}}}
for other graph nodes (empty query lists nodes); or
{"observation_request":{"diagnostic_id":"hash","offset":0}} for checker pages.
Premise mode is auto (default), exact (one fact name/ID), definition (one constant),
or search (focused identifiers). Exact/definition misses do not fill with fuzzy facts.
Auto supports lists of _def names with per-name lookup results. Search requires all
identifiers; query independent constants separately. Name matches
take priority over body-only references. Search preserves case and underscores.
Inspect match_kind/matched_terms and any symbol_resolution metadata: checked types and
expanded terms explain syntax but are not extra proved facts.
Each GRA invocation starts with no inherited facts. Select one batch of at most 4
queries with {"observation_request":{"premise_queries":[{"query":"constant",
"mode":"definition","scope":"all"}]}}. At most 12 complete facts are fixed for this
invocation; no premise paging/additions afterwards. Graph and diagnostic queries remain
available. Older Prover/GRA facts stay in audit, not in this input.
Recall misses are not proof of absence. There are at most seven observation requests,
followed by a decision, sharing this decision's timeout. No direct file/tool access.
"""
    return render_prompt(instructions, payload, role="gra", audit_path=input_audit_path)



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
        and (
            request.get("decision_context", {}).get("phase")
            == "statement-certification"
            or not request.get("decision_policy", {}).get(
                "retry_proof_allowed", True
            )
        )
    ):
        raise ValueError(
            "RETRY_PROOF forbidden by the current graph-decision phase"
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
    if action == "RETRY_PROOF":
        directive = normalized_retry_directive(decision)
        if not directive:
            raise ValueError(
                "RETRY_PROOF requires a material retrieval, outline, frontier, "
                "automation, or repair change"
            )
        proposed_sha256 = retry_directive_sha256(decision)
        current_sha256 = (
            request.get("decision_context", {}).get(
                "current_retry_directive_sha256"
            )
            if request is not None else None
        )
        if current_sha256 and proposed_sha256 == current_sha256:
            raise ValueError(
                "RETRY_PROOF repeats the previously consumed directive"
            )


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
    if (decision.get("operation") == "DELETE" or (decision.get("operation") == "SPLIT"
            and graph.get("context_policy") != "committed-prefix-v1")) and replaced_node_id:
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


def _revise_graph(
    package: dict[str, Any], graph: dict[str, Any], red_result: dict[str, Any],
    green_ids: set[str], *, output_dir: Path, provider: str = "codex",
    model: str = "gpt-5.5", timeout: int = 1800,
    decision_index: int = 1,
    decision_context: dict[str, Any] | None = None,
    allow_structural_revision: bool = True,
    premise_database: Path | None = None,
    generated_records: Path | None = None,
    symbol_resolver=None,
    accepted_proofs: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    revision = next_graph_revision(output_dir, graph["revision"])
    decision_context = augment_decision_context(
        output_dir, graph, red_result, decision_context,
    )
    guard_semantic_premises = []  # No automatic premise preload.
    request = revision_request(
        package, graph, red_result, green_ids, decision_context,
        guard_semantic_premises,
    )
    audit_dir = output_dir / "graph-revisions" / f"decision-{decision_index:03d}"
    audit_dir.mkdir(parents=True)
    (audit_dir / "request.json").write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    preferred_transcript_prefix = output_dir / "llm" / (
        f"graph-decision-{decision_index:03d}-from-v{graph['revision']}"
    )
    module_log_path = (
        output_dir.parent.parent / "module-logs/graph-revision-agent"
        / (
            f"{graph['file_slot'].removesuffix('.thy')}"
            f"-decision-{decision_index:03d}.json"
        )
    )
    provider_failures: list[dict[str, Any]] = []
    observations = []
    facts_locked = False
    deadline = time.monotonic() + timeout
    decision = None
    for observation_round in range(8):
        if time.monotonic() >= deadline:
            raise RuntimeError('GRA decision deadline exhausted during observation')
        prompt = revision_prompt(package, graph, red_result, green_ids, decision_context,
                                 input_audit_path=audit_dir / 'input.raw.json', observations=observations)
        response = ""
        transcript_prefix = None
        for provider_attempt in range(1, 4):
            if time.monotonic() >= deadline:
                raise RuntimeError('GRA decision deadline exhausted')
            transcript_prefix = allocate_artifact_prefix(
                preferred_transcript_prefix
            )
            try:
                response = call_llm(
                    provider, prompt, transcript_prefix,
                    model=model, timeout=max(1,int(deadline-time.monotonic())), theory=graph["theory"],
                    stage="graph-revision-agent",
                    clean_runtime=True,
                )
            except RuntimeError as error:
                provider_failures.append({
                    "attempt": provider_attempt,
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "transcript_prefix": str(transcript_prefix),
                })
                (audit_dir / "provider-failures.json").write_text(
                    json.dumps(
                        {"failures": provider_failures},
                        ensure_ascii=False, indent=2,
                    ) + "\n",
                    encoding="utf-8",
                )
                if provider_attempt == 3:
                    raise
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
                            "provider_attempt": provider_attempt,
                        },
                    )
            if response:
                break
        assert transcript_prefix is not None
        transcript_meta = json.loads(
            transcript_prefix.with_suffix(".meta.json").read_text(encoding="utf-8")
        )
        if not transcript_meta.get("sealable_generation"):
            raise RuntimeError("Graph Revision Agent transport was not sealable")
        (audit_dir/f'response-{observation_round:03d}.json').write_text(json.dumps({'raw_response':response},ensure_ascii=False)+'\n')
        candidate = parse_json_response(response)
        if not isinstance(candidate,dict):
            raise ValueError('GRA response must be a JSON object')
        observation = candidate.get('observation_request')
        if observation is None:
            decision = candidate
            break
        if observation_round == 7:
            raise RuntimeError('GRA observation budget exhausted without a decision')
        try:
            if not isinstance(observation,dict):
                raise ValueError('invalid GRA observation_request')
            from regen.execution.round_recall import is_fact_request, select_round_facts, frozen_response
            if is_fact_request(observation):
                if facts_locked:
                    page=frozen_response()
                else:
                    if premise_database is None:
                        raise RuntimeError('GRA premise catalog unavailable')
                    local=[{'id':n['id'],'name':n['name'],'statement':n['statement'],
                            'context':n.get('context',[])} for n in graph['nodes'] if n['id'] in green_ids]
                    page=select_round_facts(premise_database,observation,
                        generated_records=(generated_records if generated_records is not None else output_dir.parent.parent/'generated-premises.json'),
                        local_facts=local,audit_path=audit_dir/'fact-selection.json',symbol_resolver=symbol_resolver,
                        proof_state=next(('\n'.join([*n.get('context',[]),n['statement']]) for n in graph['nodes']
                            if n['id']==red_result.get('node_id')),None))
                    facts_locked=True
            elif 'auxiliary_query' in observation:
                from regen.execution.auxiliary_reuse import auxiliary_page
                page=auxiliary_page(graph, accepted_proofs or {}, red_result.get('node_id'), observation['auxiliary_query'])
            elif 'graph_query' in observation:
                from regen.execution.gra_input import graph_page
                page=graph_page(graph,green_ids,observation['graph_query'])
            elif 'diagnostic_id' in observation:
                from regen.execution.proof_input import diagnostic_page
                page=diagnostic_page([red_result,decision_context or {}],observation['diagnostic_id'],observation.get('offset',0))
            else:
                raise ValueError('unknown GRA observation request')
        except (ValueError, TypeError) as error:
            page={'kind':'request_error','status':'rejected_request','diagnostic':str(error),
                  'isabelle_called':False,'notice':'Correct this request within the remaining observation budget; selected facts are unchanged.'}
        observations.append({**page, "request":observation})
        (audit_dir/f'observation-{observation_round+1:03d}.json').write_text(
            json.dumps({'request':observation,'result':page,'transcript_prefix':str(transcript_prefix)},ensure_ascii=False,indent=2)+'\n')
    assert decision is not None
    validate_decision(decision, request)
    decision["decision_id"] = f"decision-{decision_index:03d}"
    if decision.get("decision") == "RETRY_PROOF":
        decision["retry_directive"] = normalized_retry_directive(decision)
        decision["retry_directive_sha256"] = retry_directive_sha256(decision)
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
    if graph.get("context_policy") != "committed-prefix-v1" and replacement_root and decision.get("operation") in {
        "REPLACE_STATEMENT", "SPLIT", "CHANGE_EDGE",
    }:
        removable = replacement_scope(graph, replacement_root) - green_ids
        merged_decision, pruned_nodes = prune_unconsumed_replacement_nodes(
            merged_decision, removable,
        )
    merged_decision, preserved_green_edges = (
        preserve_orphaned_green_dependencies(
            graph, merged_decision, replacement_root, green_ids,
        ) if replacement_root and graph.get("context_policy") != "committed-prefix-v1" else (merged_decision, [])
    )
    if graph.get('context_policy') == 'committed-prefix-v1':
        if decision.get('operation') == 'DELETE':
            removed = replacement_root
            for node in merged_decision.get('generated_nodes', []):
                node['dependencies'] = [key for key in node.get('dependencies', []) if key != removed]
            merged_decision['contract_dependencies'] = {
                key:[dep for dep in deps if dep != removed]
                for key,deps in merged_decision.get('contract_dependencies', {}).items()}
        if decision.get('operation') == 'SPLIT':
            old = {n['id']:n for n in graph['nodes']}
            children = [n['id'] for n in merged_decision['generated_nodes'] if n['id'] not in old]
            if not children:
                raise ValueError('SPLIT must introduce children and preserve its parent')
            parent = old[replacement_root]
            if parent['role'] == 'contract':
                merged_decision['contract_dependencies'][replacement_root] = list(dict.fromkeys([*parent['dependencies'], *children]))
            else:
                merged_decision['generated_nodes'] = [n for n in merged_decision['generated_nodes'] if n['id'] != replacement_root]
                merged_decision['generated_nodes'].append({**parent, 'dependencies':list(dict.fromkeys([*parent['dependencies'], *children]))})
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
        if graph.get('context_policy') == 'committed-prefix-v1':
            revised['context_policy'] = graph['context_policy']
            revised['accepted_order'] = list(graph.get('accepted_order', []))
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
        if graph.get('context_policy') == 'committed-prefix-v1':
            raise ProposalRejected(str(error), decision) from error
        retry_decision = {
            **decision,
            "decision": "RETRY_PROOF",
            "failure_class": "PROOF_BODY_ERROR",
            "internal_disposition": "REJECT_INVALID_REVISION",
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
        if graph.get('context_policy') == 'committed-prefix-v1':
            raise ProposalRejected(str(error), decision) from error
        retry_decision = {
            **decision,
            "decision": "RETRY_PROOF",
            "failure_class": "PROOF_BODY_ERROR",
            "internal_disposition": "REJECT_INVALID_REVISION",
            "repair_guidance": str(error),
        }
        return graph, retry_decision
    transaction_path = audit_dir / "transaction.json"
    transaction_path.write_text(json.dumps({
        "applied": graph.get("context_policy") != "committed-prefix-v1",
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


class ProposalRejected(ValueError):
    """Unaccepted GRA output; never a failure of the target proof."""
    def __init__(self, diagnostic, proposal):
        super().__init__(diagnostic)
        self.proposal=proposal


def revise_graph(package, graph, red_result, green_ids, **options):
    try:
        return _revise_graph(package,graph,red_result,green_ids,**options)
    except ProposalRejected:
        raise
    except (ValueError, KeyError, TypeError) as error:
        if graph.get('context_policy') != 'committed-prefix-v1': raise
        directory=options['output_dir']/'graph-revisions'/f"decision-{options.get('decision_index',1):03d}"
        proposal={}
        decision=directory/'decision.json'
        if decision.is_file():
            proposal=json.loads(decision.read_text())
        else:
            responses=sorted(directory.glob('response-*.json'))
            if responses:
                raw=json.loads(responses[-1].read_text())['raw_response']
                try: proposal=parse_json_response(raw)
                except (ValueError,TypeError): proposal={'raw_response':raw}
                if not isinstance(proposal,dict): proposal={'raw_response':raw}
        (directory/'proposal-rejection.json').write_text(json.dumps({
            'accepted':False,'isabelle_called':False,'diagnostic':str(error),'proposal':proposal},ensure_ascii=False,indent=2)+'\n')
        raise ProposalRejected(str(error),proposal) from error
