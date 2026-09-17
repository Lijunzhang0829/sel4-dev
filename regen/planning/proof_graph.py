#!/usr/bin/env python3
"""Data model and deterministic checks for generated theorem DAGs."""
from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

FORBIDDEN = re.compile(r"\b(?:sorry|oops|axiomatization|admit)\b")
DECLARATION = re.compile(
    r"^\s*(?:lemma|theorem|corollary|proposition)\s+([A-Za-z0-9_'.]+)"
)
DECLARATION_PREFIX = re.compile(
    r"^\s*(?:lemma|theorem|corollary|proposition)\s+"
    r"[A-Za-z0-9_'.]+(?:\s*\[[^\]]*\])?\s*:?\s*"
)
TERM_TYPE_ANNOTATION = re.compile(
    r"\(\s*([A-Za-z0-9_'.]+)\s*::\s*[^()]+\)"
)
ASSUMPTION = re.compile(
    r"\bassumes(?:\s+[A-Za-z0-9_'.]+)?\s*:\s*\"([^\"]*)\"", re.S
)
SHOWS = re.compile(r"\bshows\s*\"([^\"]*)\"", re.S)
FIXES_BLOCK = re.compile(
    r"\bfixes\s+(.+?)(?=\s+\b(?:fixes|assumes|defines|shows|obtains)\b|$)",
    re.S,
)
FIXES_TYPED_GROUP = re.compile(r"(.+?)\s*::\s*\"([^\"]+)\"", re.S)
TERM_IDENTIFIER = re.compile(r"(?<![A-Za-z0-9_'])([a-z][A-Za-z0-9_]*')(?![A-Za-z0-9_'])")


def graph_payload_sha256(graph: dict[str, Any]) -> str:
    """Hash one immutable graph while excluding its self-authenticating field."""
    payload = dict(graph)
    payload.pop("graph_payload_sha256", None)
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verify_graph_payload_sha256(graph: dict[str, Any]) -> None:
    expected = graph.get("graph_payload_sha256")
    if not isinstance(expected, str) or expected != graph_payload_sha256(graph):
        raise ValueError("graph payload hash mismatch")


def normalized_declaration_body(statement: str) -> str:
    """Compare declarations modulo command, theorem name and whitespace."""
    body = DECLARATION_PREFIX.sub("", statement, count=1)
    body = TERM_TYPE_ANNOTATION.sub(r"\1", body)
    return " ".join(body.split())


def normalized_proposition(proposition: str) -> str:
    return " ".join(TERM_TYPE_ANNOTATION.sub(r"\1", proposition).split())


def logical_signature(statement: str) -> tuple[tuple[str, ...], str] | None:
    """Normalize assumes A / shows B and shows A --> B to the same shape."""
    shows = SHOWS.search(statement)
    if not shows:
        return None
    assumptions = [
        normalized_proposition(value) for value in ASSUMPTION.findall(statement)
    ]
    parts = [
        normalized_proposition(value)
        for value in shows.group(1).split(r"\<Longrightarrow>")
    ]
    return tuple(sorted([*assumptions, *parts[:-1]])), parts[-1]



def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, 1):
        current = [row]
        for column, right_char in enumerate(right, 1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (left_char != right_char),
            ))
        previous = current
    return previous[-1]


def explicit_fixed_variables(statement: str) -> dict[str, str]:
    """Extract simple, explicitly typed term variables from fixes clauses."""
    result: dict[str, str] = {}
    for block in FIXES_BLOCK.findall(statement):
        for names_text, type_text in FIXES_TYPED_GROUP.findall(block):
            names = re.findall(r"[a-z][A-Za-z0-9_']*", names_text)
            for name in names:
                normalized_type = " ".join(type_text.split())
                if name in result and result[name] != normalized_type:
                    raise ValueError(
                        f"fixed variable {name} has incompatible types: "
                        f"{result[name]!r} and {normalized_type!r}"
                    )
                result[name] = normalized_type
    return result


def statement_interface_diagnostics(node: dict[str, Any]) -> list[str]:
    """Find conservative binding mistakes without reimplementing Isabelle."""
    if node.get("role") == "contract":
        return []
    statement = node.get("statement", "")
    fixed = explicit_fixed_variables(statement)
    if not fixed:
        return []

    proposition_text = " ".join([
        *ASSUMPTION.findall(statement),
        *SHOWS.findall(statement),
    ])
    implicit_primed = sorted(
        set(TERM_IDENTIFIER.findall(proposition_text)) - set(fixed)
    )
    diagnostics = []
    for implicit in implicit_primed:
        nearest = [name for name in fixed if _edit_distance(implicit, name) == 1]
        if nearest:
            diagnostics.append(
                f"ambiguous implicit variable {implicit} is one edit from "
                f"explicitly fixed {', '.join(sorted(nearest))}; explicitly fix "
                "both variables or correct the spelling"
            )
    return diagnostics


def certify_statement_interfaces(graph: dict[str, Any]) -> dict[str, Any]:
    diagnostics = []
    checked = 0
    for node in graph.get("nodes", []):
        if node.get("role") == "contract":
            continue
        checked += 1
        try:
            node_diagnostics = statement_interface_diagnostics(node)
        except ValueError as error:
            node_diagnostics = [str(error)]
        diagnostics.extend(
            {"node_id": node.get("id"), "diagnostic": diagnostic}
            for diagnostic in node_diagnostics
        )
    if diagnostics:
        first = diagnostics[0]
        raise ValueError(
            f"statement interface check failed for {first['node_id']}: "
            f"{first['diagnostic']}"
        )
    return {
        "policy": "generated-statement-interface-v1",
        "generated_nodes_checked": checked,
        "checks": [
            "explicit-fixes-type-consistency",
            "near-name-implicit-primed-variable",
        ],
        "verdict": "pass",
    }


def definitional_wrapper_risks(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """Find generated corres nodes that barely differ from their consumer."""
    by_id = {node["id"]: node for node in graph["nodes"]}
    risks = []
    for node in graph["nodes"]:
        if node["role"] == "contract":
            continue
        source = normalized_declaration_body(node["statement"])
        if "corres " not in source:
            continue
        for consumer_id in node["consumers"]:
            consumer = by_id[consumer_id]
            target = normalized_declaration_body(consumer["statement"])
            if "corres " not in target:
                continue
            similarity = SequenceMatcher(None, source, target).ratio()
            source_tokens = re.findall(r"[A-Za-z0-9_'.]+|\\<[^>]+>", source)
            target_tokens = re.findall(r"[A-Za-z0-9_'.]+|\\<[^>]+>", target)
            token_changes = sum(
                left != right for left, right in zip(source_tokens, target_tokens)
            ) + abs(len(source_tokens) - len(target_tokens))
            if similarity >= 0.92 or token_changes <= 1:
                risks.append({
                    "node_id": node["id"],
                    "consumer_id": consumer_id,
                    "similarity": round(similarity, 4),
                    "token_changes": token_changes,
                    "reason": (
                        "generated corres node differs from its consumer only "
                        "by a small definitional rewrite"
                    ),
                })
    return risks


def assert_no_definitional_wrappers(graph: dict[str, Any]) -> None:
    risks = definitional_wrapper_risks(graph)
    if risks:
        risk = risks[0]
        raise ValueError(
            "definitional/cosmetic wrapper risk: "
            f"{risk['node_id']} -> {risk['consumer_id']} "
            f"(similarity {risk['similarity']})"
        )


def _escape_invalid_json_backslashes(payload: str) -> str:
    """Escape Isabelle backslashes without corrupting valid JSON escapes."""
    output: list[str] = []
    in_string = False
    index = 0
    while index < len(payload):
        char = payload[index]
        if char == '"':
            in_string = not in_string
            output.append(char)
            index += 1
            continue
        if not in_string or char != "\\":
            output.append(char)
            index += 1
            continue

        end = index
        while end < len(payload) and payload[end] == "\\":
            end += 1
        count = end - index
        output.append("\\" * (count - count % 2))
        if count % 2:
            next_char = payload[end] if end < len(payload) else ""
            valid_escape = next_char in '"\\/bfnrt'
            valid_unicode = (
                next_char == "u"
                and end + 4 < len(payload)
                and all(c in "0123456789abcdefABCDEF" for c in payload[end + 1:end + 5])
            )
            output.append("\\" if valid_escape or valid_unicode else "\\\\")
        index = end
    return "".join(output)


def parse_json_response(text: str) -> dict[str, Any]:
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    payload = (match.group(1) if match else text).strip()
    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        # Isabelle syntax such as \<in> is often emitted with a single
        # backslash inside otherwise valid JSON. Escape only sequences that
        # JSON itself does not define, then retain normal schema validation.
        normalized = _escape_invalid_json_backslashes(payload)
        value = json.loads(normalized)
    if not isinstance(value, dict):
        raise ValueError("model response must be one JSON object")
    return value


def write_immutable_graph(path: Path, graph: dict[str, Any]) -> None:
    """Create a graph artifact once; identical replay is idempotent."""
    payload = json.dumps(graph, ensure_ascii=False, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") == payload:
            return
        raise FileExistsError(f"graph artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def build_graph(
    package: dict[str, Any], proposal: dict[str, Any], *,
    run_id: str, revision: int,
) -> dict[str, Any]:
    contract_dependencies = proposal.get("contract_dependencies", {})
    nodes = []
    for contract in package["contracts"]:
        nodes.append({
            "id": contract["contract_id"],
            "name": contract["name"],
            "role": "contract",
            "statement": contract["exact_statement"],
            "context": contract.get("contract_owned_context", []),
            "dependencies": list(contract_dependencies.get(contract["contract_id"], [])),
            "strategy": proposal.get("contract_strategies", {}).get(
                contract["contract_id"], ""
            ),
        })
    for node in proposal.get("generated_nodes", []):
        nodes.append({
            "id": node["id"],
            "name": node["name"],
            "role": node["role"],
            "statement": node["statement"],
            "context": list(node.get("context", [])),
            "dependencies": list(node.get("dependencies", [])),
            "strategy": node.get("strategy", ""),
        })

    consumers = {node["id"]: [] for node in nodes}
    for node in nodes:
        for dependency in node["dependencies"]:
            if dependency in consumers:
                consumers[dependency].append(node["id"])
    for node in nodes:
        node["consumers"] = sorted(consumers[node["id"]])

    graph = {
        "schema_version": 1,
        "run_id": run_id,
        "theory": package["theory"],
        "file_slot": package["file_slot"],
        "revision": revision,
        "nodes": nodes,
        "contract_routes": proposal.get("contract_routes", {}),
    }
    validate_graph(graph, package)
    assert_no_definitional_wrappers(graph)
    graph["execution_order"] = execution_order(graph)
    return graph


def validate_graph(graph: dict[str, Any], package: dict[str, Any]) -> None:
    nodes = graph.get("nodes")
    if not isinstance(nodes, list) or (not nodes and not package.get("repository_projection")):
        raise ValueError("graph must contain nodes")
    by_id = {node.get("id"): node for node in nodes}
    if None in by_id or len(by_id) != len(nodes):
        raise ValueError("node IDs must be non-empty and unique")
    names = [node.get("name") for node in nodes]
    if any(not name for name in names) or len(names) != len(set(names)):
        raise ValueError("node names must be non-empty and unique")

    fixed = {item["contract_id"]: item for item in package["contracts"]}
    actual_contracts = {
        node["id"]: node for node in nodes if node.get("role") == "contract"
    }
    if set(actual_contracts) != set(fixed):
        raise ValueError("graph must contain exactly the fixed contracts")
    allowed_contexts = {
        tuple(item.get("contract_owned_context", [])) for item in [*package["contracts"], *package.get("support_contracts", [])]
    } | {()}

    for node in nodes:
        if node.get("role") not in {"contract", "theorem", "helper"}:
            raise ValueError(f"invalid role for {node['id']}")
        if any(key in node for key in ("proof", "proof_body", "premises")):
            raise ValueError("File Planner output may not contain proofs or premises")
        statement = node.get("statement", "")
        match = DECLARATION.match(statement)
        if not match or match.group(1) != node["name"]:
            raise ValueError(f"statement/name mismatch for {node['id']}")
        if FORBIDDEN.search(statement):
            raise ValueError(f"forbidden command in statement for {node['id']}")
        if tuple(node.get("context", [])) not in allowed_contexts:
            raise ValueError(f"unapproved contract context for {node['id']}")
        dependencies = node.get("dependencies")
        consumers = node.get("consumers")
        if not isinstance(dependencies, list) or len(dependencies) != len(set(dependencies)):
            raise ValueError(f"invalid dependencies for {node['id']}")
        if not isinstance(consumers, list) or len(consumers) != len(set(consumers)):
            raise ValueError(f"invalid consumers for {node['id']}")
        missing = set(dependencies) - set(by_id)
        if missing:
            raise ValueError(f"unknown generated dependencies for {node['id']}: {missing}")
        if node["id"] in dependencies:
            raise ValueError(f"self dependency for {node['id']}")
        if node["role"] == "helper" and dependencies and not package.get("repository_projection"):
            raise ValueError(f"helper must be a generated leaf: {node['id']}")
        if node["role"] != "contract" and not consumers and not package.get("repository_projection"):
            raise ValueError(f"generated node has no consumer: {node['id']}")

    bodies: dict[str, str] = {}
    signatures: dict[tuple[tuple[str, ...], str], str] = {}
    for node in nodes:
        body = normalized_declaration_body(node["statement"])
        if body in bodies:
            raise ValueError(
                "duplicate proposition/cosmetic wrapper: "
                f"{bodies[body]} and {node['id']}"
            )
        bodies[body] = node["id"]
        signature = logical_signature(node["statement"])
        if signature is not None and signature in signatures:
            raise ValueError(
                "logically equivalent/cosmetic wrapper: "
                f"{signatures[signature]} and {node['id']}"
            )
        if signature is not None:
            signatures[signature] = node["id"]

    for contract_id, contract in fixed.items():
        node = actual_contracts[contract_id]
        if (
            node["name"] != contract["name"]
            or node["statement"] != contract["exact_statement"]
            or node.get("context", []) != contract.get("contract_owned_context", [])
        ):
            raise ValueError(f"fixed contract changed: {contract_id}")

    expected_consumers = {node_id: [] for node_id in by_id}
    for node in nodes:
        for dependency in node["dependencies"]:
            expected_consumers[dependency].append(node["id"])
    for node_id, consumers in expected_consumers.items():
        if sorted(consumers) != sorted(by_id[node_id]["consumers"]):
            raise ValueError(f"consumer/dependency mismatch for {node_id}")
    graph["statement_certification"] = certify_statement_interfaces(graph)
    execution_order(graph)


def execution_order(graph: dict[str, Any]) -> list[str]:
    nodes = graph["nodes"]
    by_id = {node["id"]: node for node in nodes}
    indegree = {node["id"]: len(node["dependencies"]) for node in nodes}
    consumers = {node["id"]: list(node["consumers"]) for node in nodes}
    role_rank = {"helper": 0, "theorem": 1, "contract": 2}

    def priority(node_id: str) -> tuple[int, str]:
        return role_rank[by_id[node_id]["role"]], node_id

    ready = sorted(
        (node_id for node_id, degree in indegree.items() if degree == 0),
        key=priority,
    )
    result = []
    while ready:
        node_id = ready.pop(0)
        result.append(node_id)
        for consumer in sorted(consumers[node_id]):
            indegree[consumer] -= 1
            if indegree[consumer] == 0:
                ready.append(consumer)
                ready.sort(key=priority)
    if len(result) != len(by_id):
        raise ValueError("generated theorem graph contains a cycle")
    return result


def executable_nodes(graph: dict[str, Any], statuses: dict[str, str]) -> list[str]:
    by_id = {node["id"]: node for node in graph["nodes"]}
    return [
        node_id for node_id in graph["execution_order"]
        if statuses.get(node_id, "planned") == "planned"
        and all(statuses.get(dep) == "green" for dep in by_id[node_id]["dependencies"])
    ]


def assert_green_nodes_unchanged(
    old_graph: dict[str, Any], new_graph: dict[str, Any], green_ids: set[str],
) -> None:
    old = {node["id"]: node for node in old_graph["nodes"]}
    new = {node["id"]: node for node in new_graph["nodes"]}
    immutable_fields = (
        "id", "name", "role", "statement", "context", "dependencies",
    )
    for node_id in green_ids:
        if node_id not in new or any(
            old[node_id].get(field) != new[node_id].get(field)
            for field in immutable_fields
        ):
            raise ValueError(f"graph revision changed green node: {node_id}")
