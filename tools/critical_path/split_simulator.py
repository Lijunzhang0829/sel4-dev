#!/usr/bin/env python3
"""
tools/critical_path/split_simulator.py

For each Tier-3-REJECT CSTR edge identified by move_simulator (i.e., single-
edge SWAP would regress), simulate whether SPLITTING the origin session into
a consumer-needed subset + an unused subset would unlock a wall-positive
intervention.

The reasoning chain:

  1. A Tier-3 reject happens because origin's heap is smaller than consumer's
     current `+` parent — swapping origin to `+` would force the bigger
     current-parent content into re-execution and regress.

  2. But consumer might not need ALL of origin. If we extract a consumer-
     needed subset A from origin (theories transitively imported by consumer's
     own theories), A's heap could be much smaller than origin's full heap.

  3. If A's heap is smaller than consumer's current `+` parent — same
     rejection. If A is small enough that promoting A to `+` parent saves
     wall while keeping current parent re-execution manageable — viable
     SPLIT.

  4. Even if SPLIT doesn't directly unlock a swap, knowing "consumer needs
     X% of origin" tells us whether origin is MONOLITHIC (X near 100% — no
     split helps) or LAYERED (X small — split might).

Inputs:
    reports/theory-dag.json     # theory import edges
    heaps/db-archive/*.db       # per-theory wall (post-swap)
    reports/move-simulator.md   # Tier-3 edge list (or recompute via dedupe scan)

Output:
    reports/split-simulator.md
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
THY_DAG = REPO / "reports" / "theory-dag.json"
DB_DIR = REPO / "heaps" / "db-archive"
SCAN_JSON = REPO / "reports" / "session-duplication-scan.json"
OUT_MD = REPO / "reports" / "split-simulator.md"

sys.path.insert(0, str(REPO / "tools" / "lemma_inventory"))
sys.path.insert(0, str(REPO / "tools" / "critical_path"))
import parse_roots  # noqa: E402
from move_simulator import (  # noqa: E402
    APPLIED_SWAPS, effective_roots, build_ancestors, ancestor_chain,
)

REC_RE = re.compile(
    r"name=([^\x06]+)\x06elapsed=([0-9.]+)\x06cpu=([0-9.]+)\x06gc=([0-9.]+)"
)


def extract_db(db_path):
    con = sqlite3.connect(str(db_path))
    name, blob = con.execute(
        "SELECT session_name, theory_timings FROM isabelle_session_info"
    ).fetchone()
    con.close()
    if blob is None:
        return name, {}
    with tempfile.NamedTemporaryFile(suffix=".zst", delete=False) as fz:
        fz.write(blob); fz_path = fz.name
    try:
        text = subprocess.check_output(["zstd", "-dcq", fz_path]).decode("utf-8")
    finally:
        os.unlink(fz_path)
    return name, {n: (float(e), float(c), float(g))
                  for n, e, c, g in REC_RE.findall(text)}


def load_session_data():
    """session -> {theory_fqn -> (elapsed, cpu, gc)} from post-swap DBs."""
    data = {}
    for db in sorted(DB_DIR.glob("*.db")):
        sess, theories = extract_db(db)
        if theories:
            data[sess] = theories
    return data


def load_theory_dag():
    """Return (nodes_by_fqn, imports_of) where imports_of[A] = set of theories A imports."""
    raw = json.loads(THY_DAG.read_text())
    nodes_by_fqn = {n["name"]: n for n in raw["nodes"]}
    imports_of = defaultdict(set)
    for importer, imported in raw["edges"]:
        imports_of[importer].add(imported)
    return nodes_by_fqn, dict(imports_of)


def find_consumer_closure_in_origin(consumer, origin, imports_of, session_data):
    """Find subset of origin's theories transitively imported (any path) by
    consumer's own theories.

    Important: the path may traverse intermediate namespaces — e.g., a
    CBaseRefine.X imports CSpec.Y, and CSpec.Y imports CKernel.Z. Restricting
    the BFS to edges-that-stay-in-origin would miss CKernel.Z entirely.
    So we compute the FULL transitive closure of consumer's imports, then
    project onto origin namespace."""
    consumer_own = {t for t in session_data.get(consumer, {})
                    if t.startswith(consumer + ".")}
    visited = set()
    stack = list(consumer_own)
    while stack:
        cur = stack.pop()
        if cur in visited:
            continue
        visited.add(cur)
        for imp in imports_of.get(cur, ()):
            if imp not in visited:
                stack.append(imp)
    return {t for t in visited if t.startswith(origin + ".")}


def get_origin_own_theories(origin, session_data):
    """All theories with namespace `origin` that appear in any DB."""
    out = set()
    for sess, theories in session_data.items():
        for t in theories:
            if t.startswith(origin + "."):
                out.add(t)
    return out


def classify_split(consumer, origin, cost, fraction, needed_wall_in_origin,
                   unneeded_wall_in_origin, cur_parent_size,
                   origin_in_dag, consumer_in_dag):
    """Return (class, recommendation_text)."""
    # If the theory DAG doesn't index origin or consumer, the closure is
    # unreliable. The seL4 theory-DAG (built by parse_theory_imports.py) only
    # scans `verification/l4v/` — sessions from the Isabelle distribution
    # (HOL, Pure, Simpl-VCG) have 0 DAG nodes, so closures can't find their
    # dependencies even when those theories are present in BLOBs.
    if not origin_in_dag:
        return (
            "DAG-INCOMPLETE",
            f"origin `{origin}` has 0 nodes in theory-DAG (likely lives outside "
            f"verification/l4v/); cannot verify needed-subset. Treat as MONOLITHIC "
            f"by default — splitting Isabelle-distribution sessions is out of scope."
        )
    if not consumer_in_dag:
        return (
            "DAG-INCOMPLETE",
            f"consumer `{consumer}` has 0 nodes in theory-DAG; cannot trace its "
            f"import closure. Result unreliable."
        )
    if fraction >= 0.85:
        return (
            "MONOLITHIC",
            f"consumer needs {fraction*100:.0f}% of origin; SPLIT won't help — "
            f"would still need to `+`-merge most of it."
        )
    if fraction <= 0.30:
        if needed_wall_in_origin < cur_parent_size * 0.7:
            return (
                "PROMISING-SPLIT",
                f"consumer needs only {fraction*100:.0f}% ({needed_wall_in_origin:.0f}s) "
                f"of origin; SPLIT origin into 2 sessions and `+` the smaller half may "
                f"yield net-positive wall (cur parent {cur_parent_size:.0f}s)."
            )
        return (
            "PARTIAL-SPLIT",
            f"consumer needs {fraction*100:.0f}% ({needed_wall_in_origin:.0f}s); "
            f"SPLIT plausible but needed subset still > cur_parent({cur_parent_size:.0f}s)."
        )
    return (
        "MIXED",
        f"consumer needs {fraction*100:.0f}% ({needed_wall_in_origin:.0f}s) of origin; "
        f"split would extract a sizeable subset but may not be wall-positive."
    )


def main():
    print("Loading data...")
    sessions = effective_roots()
    session_data = load_session_data()
    nodes_by_fqn, imports_of = load_theory_dag()
    dag_sessions_with_nodes = {n["session"] for n in
                               json.loads(THY_DAG.read_text())["nodes"]}
    scan = json.loads(SCAN_JSON.read_text())
    own_sizes = {s["session"]: s["own_total_elapsed"] for s in scan["session_summary"]}

    # Identify Tier-3 reject edges (same logic as move_simulator)
    tier3_edges = []
    for s in scan["session_summary"]:
        consumer = s["session"]
        cons_anc = build_ancestors(consumer, sessions)
        for origin, cost in s.get("co_appearing_sessions", {}).items():
            if cost < 10:
                continue
            if origin not in cons_anc:
                continue  # not a downstream-consumer edge
            cur_parent = sessions.get(consumer, {}).get("parent")
            cur_parent_size = own_sizes.get(cur_parent, 0) if cur_parent else 0
            origin_size = own_sizes.get(origin, 0)
            margin = origin_size - cur_parent_size
            if margin > 0:
                continue  # not Tier-3 reject; would be Tier-1/2
            tier3_edges.append({
                "consumer": consumer,
                "origin": origin,
                "cost": cost,
                "cur_parent": cur_parent,
                "cur_parent_size": cur_parent_size,
                "origin_size": origin_size,
            })
    tier3_edges.sort(key=lambda e: -e["cost"])

    print(f"Tier-3 reject edges to analyze: {len(tier3_edges)}")

    # For each Tier-3 edge, run SPLIT analysis
    rows = []
    for e in tier3_edges:
        consumer, origin = e["consumer"], e["origin"]
        # Origin's full theory set (everything with namespace `origin` in any DB)
        origin_thys = get_origin_own_theories(origin, session_data)
        if not origin_thys:
            continue

        # Wall of origin's theories in consumer's BLOB
        consumer_blob = session_data.get(consumer, {})
        origin_in_consumer = {t: consumer_blob[t][0] for t in origin_thys
                              if t in consumer_blob}
        all_origin_wall_in_consumer = sum(origin_in_consumer.values())

        # Consumer's needed subset of origin (via import closure)
        needed_set = find_consumer_closure_in_origin(
            consumer, origin, imports_of, session_data
        )
        needed_wall_in_consumer = sum(consumer_blob[t][0]
                                      for t in needed_set if t in consumer_blob)
        unneeded_set = set(origin_in_consumer.keys()) - needed_set
        unneeded_wall_in_consumer = sum(consumer_blob[t][0]
                                        for t in unneeded_set if t in consumer_blob)

        # Wall in origin session (the "lighter" cost — what A-subset would cost
        # as a heap-merged smaller session)
        origin_blob = session_data.get(origin, {})
        needed_wall_in_origin = sum(origin_blob[t][0]
                                    for t in needed_set if t in origin_blob)
        unneeded_wall_in_origin = sum(origin_blob[t][0]
                                      for t in origin_thys - needed_set
                                      if t in origin_blob)

        fraction = (needed_wall_in_consumer / max(all_origin_wall_in_consumer, 1)
                    if all_origin_wall_in_consumer > 0 else 0)

        cls, rec = classify_split(
            consumer, origin, e["cost"], fraction,
            needed_wall_in_origin, unneeded_wall_in_origin, e["cur_parent_size"],
            origin_in_dag=(origin in dag_sessions_with_nodes),
            consumer_in_dag=(consumer in dag_sessions_with_nodes),
        )
        rows.append({
            **e,
            "n_origin_thys": len(origin_thys),
            "n_needed": len(needed_set),
            "needed_wall_in_consumer": needed_wall_in_consumer,
            "unneeded_wall_in_consumer": unneeded_wall_in_consumer,
            "needed_wall_in_origin": needed_wall_in_origin,
            "unneeded_wall_in_origin": unneeded_wall_in_origin,
            "fraction": fraction,
            "class": cls,
            "rec": rec,
            "needed_examples": sorted(needed_set)[:5],
            "unneeded_examples": sorted(origin_thys - needed_set)[:5],
        })

    # Build markdown
    md = []
    md.append("# Split Simulator — Per-Edge Decomposition Analysis\n")
    md.append("_For each Tier-3 (single-edge SWAP rejected) CSTR edge identified_")
    md.append("_by [move-simulator.md](move-simulator.md), this report runs an_")
    md.append("_import-closure analysis to determine whether SPLITTING the origin_")
    md.append("_session into a consumer-needed subset + an unused subset would_")
    md.append("_unlock a wall-positive intervention._\n")
    md.append("Method: for each (consumer, origin) edge, compute the transitive")
    md.append("closure of theories that consumer's own theories import from origin.")
    md.append("If this closure is a small fraction of origin's total content, SPLIT")
    md.append("becomes plausible — promote the closure as a new `+` parent.\n")

    md.append("## Summary by class\n")
    class_groups = defaultdict(list)
    for r in rows:
        class_groups[r["class"]].append(r)
    for cls in ("PROMISING-SPLIT", "PARTIAL-SPLIT", "MIXED", "MONOLITHIC",
                "DAG-INCOMPLETE"):
        g = class_groups.get(cls, [])
        total = sum(r["cost"] for r in g)
        md.append(f"- **{cls}**: {len(g)} edges, total cost {total:.0f}s")
    md.append("")

    md.append("## Per-edge analysis\n")
    md.append("| # | Consumer ← Origin | Cost (s) | #orig thys | #needed | Needed % | Wall(needed)@origin | Class |")
    md.append("|---:|---|---:|---:|---:|---:|---:|---|")
    for i, r in enumerate(rows, 1):
        md.append(
            f"| {i} | `{r['consumer']}` ← `{r['origin']}` | {r['cost']:.0f} | "
            f"{r['n_origin_thys']} | {r['n_needed']} | {r['fraction']*100:.0f}% | "
            f"{r['needed_wall_in_origin']:.0f} | {r['class']} |"
        )
    md.append("")

    md.append("## Detailed recommendations\n")
    for cls in ("PROMISING-SPLIT", "PARTIAL-SPLIT", "MIXED", "MONOLITHIC",
                "DAG-INCOMPLETE"):
        g = class_groups.get(cls, [])
        if not g:
            continue
        md.append(f"### {cls}\n")
        for r in g:
            md.append(
                f"#### `{r['consumer']}` ← `{r['origin']}` ({r['cost']:.0f}s)\n"
            )
            md.append(f"- {r['rec']}")
            md.append(
                f"- Origin breakdown: {r['n_origin_thys']} theories total, "
                f"{r['n_needed']} ({r['fraction']*100:.0f}% of wall) needed by consumer."
            )
            md.append(
                f"- Wall in origin's own BLOB: needed = {r['needed_wall_in_origin']:.0f}s, "
                f"unneeded = {r['unneeded_wall_in_origin']:.0f}s."
            )
            if r['needed_examples']:
                md.append(f"- Sample needed: {', '.join(r['needed_examples'][:3])}")
            if r['unneeded_examples']:
                md.append(f"- Sample unneeded: {', '.join(r['unneeded_examples'][:3])}")
            md.append("")

    md.append("## Methodology notes\n")
    md.append("- **Import closure** is approximate. Indirect imports via")
    md.append("  Eisbach methods, ML attribute lookups, or `crunch`-generated")
    md.append("  facts may not appear in the theory-DAG; the simulator might")
    md.append("  under-estimate the actually-needed subset.")
    md.append("- A **PROMISING-SPLIT** classification is necessary but not")
    md.append("  sufficient. Splitting a session means moving `.thy` files into")
    md.append("  a new directory, declaring a new `ROOT` session, and updating")
    md.append("  every downstream `imports \"origin.X\"` to point at the new")
    md.append("  session's namespace. Cost: invasive but mechanical.")
    md.append("- Estimates ignore amplification on the swapped-out current")
    md.append("  parent; if cur_parent is large its re-execution cost in the")
    md.append("  new heap context will exceed `cur_parent_size`. Treat the")
    md.append("  ratio `needed_wall/cur_parent_size` as a NECESSARY threshold,")
    md.append("  not a sufficient one. Validate any SPLIT empirically before")
    md.append("  committing.")

    OUT_MD.write_text("\n".join(md) + "\n")
    print(f"Wrote {OUT_MD}")
    for cls in ("PROMISING-SPLIT", "PARTIAL-SPLIT", "MIXED", "MONOLITHIC",
                "DAG-INCOMPLETE"):
        g = class_groups.get(cls, [])
        total = sum(r["cost"] for r in g)
        print(f"  {cls:20s}: {len(g):2d} edges  {total:6.0f}s")


if __name__ == "__main__":
    main()
