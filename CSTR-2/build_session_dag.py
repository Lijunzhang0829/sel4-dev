#!/usr/bin/env python3
"""
tools/critical_path/build_session_dag.py
========================================

Step 2 of the DAG critical-path pipeline.

WHAT IT DOES
------------
Reuses tools/lemma_inventory/parse_roots.py to extract session-level
DAG (parent edges via "+", import edges via "sessions ..."), then
joins each session with the duplication+timing facts from
reports/session-duplication-scan.json so downstream consumers can
do cost-aware critical-path computation.

OUTPUT
------
    reports/session-dag.json
      {
        "stats": {...},
        "nodes": [{
          "session": str,
          "parent": str|None,
          "imported_sessions": [str, ...],
          "session_dir": str,
          "own_total_elapsed": float,           # from CSTR (None if not in db-archive)
          "duplicated_pct": float|None,
          "n_duplicated_theories": int|None,
          "co_appearing_sessions": {str: float}|None
        }, ...],
        "edges": [
          {"from": "CBaseRefine", "to": "CSpec",   "kind": "parent"},
          {"from": "CBaseRefine", "to": "Refine",  "kind": "imported_sessions"},
          ...
        ]
      }

USAGE
-----
    python3 tools/critical_path/build_session_dag.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import parse_roots  # noqa: E402

L4V = REPO / "verification" / "l4v"
DUP_JSON = REPO / "CSTR-2" / "outputs" / "session-duplication-scan.json"
OUT_JSON = REPO / "CSTR-2" / "outputs" / "session-dag.json"


def main() -> int:
    print(f"parsing ROOTs under {L4V} ...", file=sys.stderr)
    parsed = parse_roots.parse_all_roots(L4V.resolve(), arch="ARM")
    sessions = parsed["sessions"]

    # Load CSTR duplication scan (session-level facts)
    dup = json.loads(DUP_JSON.read_text())
    dup_by_session = {s["session"]: s for s in dup["session_summary"]}

    nodes = []
    for name in sorted(sessions):
        s = sessions[name]
        d = dup_by_session.get(name, {})
        nodes.append(
            {
                "session": name,
                "parent": s["parent"],
                "imported_sessions": s["imported_sessions"],
                "session_dir": s["session_dir"],
                "own_total_elapsed": d.get("own_total_elapsed"),
                "duplicated_pct": d.get("duplicated_pct"),
                "n_duplicated_theories": d.get("n_duplicated_theories"),
                "duplicated_elapsed_in_self": d.get("duplicated_elapsed_in_self"),
                "co_appearing_sessions": d.get("co_appearing_sessions"),
                "n_theories_in_db": d.get("n_theories"),
            }
        )

    edges = []
    for name in sorted(sessions):
        s = sessions[name]
        if s["parent"]:
            edges.append({"from": name, "to": s["parent"], "kind": "parent"})
        for imp in s["imported_sessions"]:
            edges.append({"from": name, "to": imp, "kind": "imported_sessions"})

    # Sanity: which sessions have no parent (root of DAG)?
    parented = {s["parent"] for s in sessions.values() if s["parent"]}
    no_parent = sorted([n for n in sessions if not sessions[n]["parent"]])
    leaves = sorted([n for n in sessions if n not in {e["to"] for e in edges if e["kind"] == "parent"}])

    # Cross-check edge integrity: do all edge.to sessions exist in our nodes set?
    node_names = {n["session"] for n in nodes}
    dangling_edges = [e for e in edges if e["to"] not in node_names]

    stats = {
        "n_sessions": len(nodes),
        "n_parent_edges": sum(1 for e in edges if e["kind"] == "parent"),
        "n_import_edges": sum(1 for e in edges if e["kind"] == "imported_sessions"),
        "n_sessions_in_dup_scan": len(dup_by_session),
        "n_sessions_without_dup_data": len([n for n in nodes if n["own_total_elapsed"] is None]),
        "no_parent_sessions": no_parent,
        "n_dangling_edges": len(dangling_edges),
        "dangling_edges_examples": dangling_edges[:10],
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps({"stats": stats, "nodes": nodes, "edges": edges}, indent=2) + "\n"
    )

    print("=== sanity ===")
    print(f"  sessions:                       {stats['n_sessions']}")
    print(f"  parent edges:                   {stats['n_parent_edges']}")
    print(f"  imported_sessions edges:        {stats['n_import_edges']}")
    print(f"  sessions covered by CSTR scan:  {stats['n_sessions_in_dup_scan']}")
    print(f"  sessions WITHOUT timing data:   {stats['n_sessions_without_dup_data']}")
    print(f"  dangling edges:                 {stats['n_dangling_edges']}")
    print(f"  no-parent (root) sessions:      {len(no_parent)}: {no_parent[:8]}...")
    print(f"\nwrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
