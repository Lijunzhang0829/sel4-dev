#!/usr/bin/env python3
"""Deterministic targeting for complete-theory proof-body stabilization."""
from __future__ import annotations

import re
from typing import Any

NODE_MARKER = re.compile(r"(?:^|\n)REGEN_NODE ([^\r\n]+)")


def extract_last_node_marker(output: str) -> tuple[str | None, int]:
    """Return the last completed diagnostic marker and the total marker count."""
    markers = NODE_MARKER.findall(output)
    return (markers[-1] if markers else None), len(markers)


def prepare_assembly_stabilization(
    graph: dict[str, Any], proofs: dict[str, str], check: dict[str, Any],
) -> dict[str, Any]:
    """Bind a complete-check failure to one proof-body-only repair target."""
    by_id = {node["id"]: node for node in graph["nodes"]}
    marker = check.get("last_diagnostic_marker")
    target = by_id.get(marker)
    failure_class = (
        "ASSEMBLY_TIMEOUT" if check.get("returncode") == 124
        else "ASSEMBLY_CHECK_RED"
    )
    result = {
        "schema_version": 1,
        "status": "targeted" if target is not None else "unlocalized",
        "failure_class": failure_class,
        "graph_revision": graph["revision"],
        "last_diagnostic_marker": marker,
        "diagnostic_marker_count": check.get("diagnostic_marker_count", 0),
        "complete_check_log": check.get("log"),
        "complete_check_diagnostic": check.get("diagnostic", ""),
        "allowed_change": "proof_body_only",
        "forbidden_changes": [
            "statement", "role", "dependencies", "graph", "contract",
        ],
    }
    if target is not None:
        result["target"] = {
            "node_id": target["id"],
            "name": target["name"],
            "role": target["role"],
            "statement": target["statement"],
            "dependencies": target["dependencies"],
            "proof_body": proofs[target["id"]],
        }
    else:
        result["reason"] = (
            "complete check emitted no marker that names a current graph node"
        )
    return result
