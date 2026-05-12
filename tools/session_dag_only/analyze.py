#!/usr/bin/env python3
"""
tools/session_dag_only/analyze.py
=================================

Single session-DAG analyzer. Collapses the previous two-graph (session +
theory) methodology into one graph at session granularity.

NODES: 29 sessions, weight = own elapsed from heaps/build_log.txt
EDGES: from verification/l4v/proof/ROOT
  - `+ X` parent edge  → kind=parent, weight=0 (heap-merged)
  - `sessions Y`       → kind=sessions, weight = Σ elapsed of theories
                          from Y that appear inside the downstream session's
                          theory_timings BLOB (= cross-session reprocessing
                          cost, the CSTR phenomenon)

OUTPUT: reports/session-dag-only.md (recommendations) + .json (machine)
        + records construction wall time at the bottom of the .md.

USAGE: python3 tools/session_dag_only/analyze.py
"""
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
L4V = REPO / "verification" / "l4v"
# Per-build authoritative pre-swap data:
DB_DIR = REPO / "heaps" / "db-archive-pre-swap"
BUILD_LOG = REPO / "heaps" / "build_log.txt"  # 2026-05-07 pre-swap canonical
OUT_MD = REPO / "reports" / "session-dag-only.md"
OUT_JSON = REPO / "reports" / "session-dag-only.json"

REC_RE = re.compile(
    r"name=([^\x06]+)\x06elapsed=([0-9.]+)\x06cpu=([0-9.]+)\x06gc=([0-9.]+)"
)

SESSION_HEADER_RE = re.compile(
    r"^session\s+(\S+)\s+(?:in\s+\"[^\"]+\"\s+)?=\s+(\S+)\s*\+\s*$"
)


def parse_root(root_path: Path) -> dict[str, dict]:
    """Parse one ROOT file → {session: {'parent': str, 'sessions': [str, ...]}}."""
    sessions: dict[str, dict] = {}
    cur = None
    in_sessions_block = False
    in_comment = False
    with root_path.open() as f:
        for raw in f:
            line = raw.rstrip()
            stripped = line.strip()
            # strip block comments
            if "(*" in stripped:
                in_comment = True
            if in_comment:
                if "*)" in stripped:
                    in_comment = False
                continue
            m = SESSION_HEADER_RE.match(stripped)
            if m:
                cur = m.group(1)
                parent = m.group(2)
                sessions[cur] = {"parent": parent, "sessions": []}
                in_sessions_block = False
                continue
            if cur is None:
                continue
            if stripped == "sessions":
                in_sessions_block = True
                continue
            if stripped in {"theories", "directories"} or stripped.startswith(
                ("theories ", "directories ", "theories[", "theories\t",
                 "directories ", "options ", "global_theories")
            ):
                in_sessions_block = False
                continue
            if in_sessions_block and stripped:
                # one bareword per line; reject anything with quotes or brackets
                if re.match(r"^[A-Za-z_][A-Za-z0-9_\-]*$", stripped):
                    sessions[cur]["sessions"].append(stripped)
    return sessions


def parse_all_roots(l4v_root: Path) -> dict[str, dict]:
    """Scan all ROOT files under l4v/ and merge session declarations."""
    merged: dict[str, dict] = {}
    for rt in sorted(l4v_root.rglob("ROOT")):
        # Skip non-Isabelle ROOTs (none expected, but be defensive)
        try:
            decls = parse_root(rt)
        except Exception:
            continue
        for s, d in decls.items():
            merged[s] = d
    return merged


def parse_build_log_own_elapsed(log_path: Path) -> dict[str, float]:
    """Parse only the top SESSION OVERVIEW table → {session: elapsed}.
    Table ends at TOTAL row or PER-THEORY TIMINGS header."""
    own: dict[str, float] = {}
    in_table = False
    saw_data_row = False
    with log_path.open() as f:
        for line in f:
            if "SESSION OVERVIEW" in line:
                in_table = True
                continue
            if "PER-THEORY TIMINGS" in line:
                break
            if not in_table:
                continue
            if line.startswith("===") or line.startswith("---"):
                continue
            parts = line.split()
            if len(parts) < 2:
                if saw_data_row:
                    # blank line after the table — done
                    if not line.strip():
                        break
                continue
            # First column is session name; second is elapsed
            if parts[0] == "Session" or parts[0] == "TOTAL":
                if parts[0] == "TOTAL":
                    break
                continue
            try:
                elapsed = float(parts[1])
            except ValueError:
                continue
            own[parts[0]] = elapsed
            saw_data_row = True
    return own


def extract_timings(db_path: Path):
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT session_name, theory_timings FROM isabelle_session_info"
        ).fetchone()
    except sqlite3.OperationalError:
        return None, []
    if row is None or row[1] is None:
        return (row[0] if row else None), []
    name, blob = row
    with tempfile.NamedTemporaryFile(suffix=".zst", delete=False) as fz:
        fz.write(blob)
        fz_path = Path(fz.name)
    try:
        text = subprocess.check_output(["zstd", "-dcq", str(fz_path)]).decode("utf-8")
    finally:
        fz_path.unlink(missing_ok=True)
    recs = [(n, float(e)) for n, e, c, g in REC_RE.findall(text)]
    return name, recs


def main() -> int:
    t0 = time.perf_counter()

    # Step 1: parse all ROOT files under l4v/
    t = time.perf_counter()
    sessions = parse_all_roots(L4V)
    t_root = time.perf_counter() - t
    print(f"[{t_root:6.3f}s] parsed ROOTs: {len(sessions)} sessions", file=sys.stderr)

    # Step 2: parse build_log
    t = time.perf_counter()
    own_elapsed = parse_build_log_own_elapsed(BUILD_LOG)
    t_log = time.perf_counter() - t
    print(f"[{t_log:6.3f}s] parsed build_log: {len(own_elapsed)} sessions", file=sys.stderr)

    # Step 3: load per-session theory_timings (downstream timings = what was
    # actually executed in that session's ML state, includes re-executed work)
    t = time.perf_counter()
    by_session: dict[str, dict[str, float]] = {}
    for db in sorted(DB_DIR.glob("*.db")):
        sess, recs = extract_timings(db)
        if sess and recs:
            by_session[sess] = dict(recs)
    t_db = time.perf_counter() - t
    print(
        f"[{t_db:6.3f}s] loaded theory_timings BLOBs: {len(by_session)} sessions",
        file=sys.stderr,
    )

    # Step 4: build graph + compute edge weights
    t = time.perf_counter()
    nodes = {
        s: {
            "elapsed": own_elapsed.get(s, 0.0),
            "n_theories_executed": len(by_session.get(s, {})),
        }
        for s in sessions
    }
    edges = []
    for s, decl in sessions.items():
        parent = decl["parent"]
        if parent in sessions:
            edges.append({"src": parent, "dst": s, "kind": "parent", "weight": 0.0})
        own_blob = by_session.get(s, {})
        for up in decl["sessions"]:
            if up not in sessions:
                continue
            up_blob = by_session.get(up, {})
            # Reprocess weight = theories that appear in BOTH up and s blobs
            # (i.e. theories declared by up but also executed inside s's
            #  ML state); weight = Σ of their elapsed inside s.
            shared = set(up_blob) & set(own_blob)
            w = sum(own_blob[th] for th in shared)
            edges.append(
                {
                    "src": up,
                    "dst": s,
                    "kind": "sessions",
                    "weight": round(w, 1),
                    "n_shared": len(shared),
                }
            )
    t_graph = time.perf_counter() - t
    print(
        f"[{t_graph:6.3f}s] built session-DAG: {len(nodes)} nodes, {len(edges)} edges",
        file=sys.stderr,
    )

    # Step 5: derive CSTR swap recommendations
    # For each session B with current parent P and a `sessions` edge from U:
    #   - if P ∈ ancestry(U): swapping (B's parent → U) lets us DROP `sessions P`
    #     entirely (P comes free through U's chain).  est_post_swap_cost ≈ 0.
    #   - else: P must move to a new `sessions P` clause and gets reprocessed.
    #     est_post_swap_cost ≈ elapsed(P) as an upper bound.
    # In both cases reprocessing cost of U (currently reprocessed) goes to 0
    # because U becomes the heap-merged parent.
    def ancestry(s: str) -> set[str]:
        out = set()
        cur = sessions.get(s, {}).get("parent")
        while cur and cur in sessions and cur not in out:
            out.add(cur)
            cur = sessions[cur].get("parent")
        return out

    recommendations = []
    for s, decl in sessions.items():
        if s not in by_session:
            continue
        own_blob = by_session[s]
        parent = decl["parent"]
        parent_elapsed = own_elapsed.get(parent, 0.0)
        for up in decl["sessions"]:
            if up not in sessions:
                continue
            up_blob = by_session.get(up, {})
            shared = set(up_blob) & set(own_blob)
            reproc_cost = sum(own_blob[th] for th in shared)
            # Will the old parent still be reachable through U's ancestry?
            up_anc = ancestry(up) | {up}
            if parent in up_anc:
                est_post_swap = 0.0  # `sessions P` becomes redundant, drop it
                old_parent_disposition = "drop (P in U's ancestry)"
            else:
                est_post_swap = parent_elapsed
                old_parent_disposition = "move P to `sessions P`"
            saving = reproc_cost - est_post_swap
            if saving > 50.0:  # only meaningful candidates
                recommendations.append(
                    {
                        "session": s,
                        "current_parent": parent,
                        "swap_with": up,
                        "current_reprocess_cost": round(reproc_cost, 1),
                        "est_post_swap_cost": round(est_post_swap, 1),
                        "est_aggregate_saving": round(saving, 1),
                        "n_shared_theories": len(shared),
                        "old_parent_disposition": old_parent_disposition,
                    }
                )
    recommendations.sort(key=lambda r: -r["est_aggregate_saving"])

    # Step 6: detect redundant `sessions` edges in CURRENT graph
    #          AND in the graph AFTER applying all recommended swaps.
    # If U is already in B's parent ancestry, `sessions U` is a no-op.
    def find_redundant(sess_map):
        out = []
        for s, decl in sess_map.items():
            cur = decl.get("parent")
            anc = set()
            while cur and cur in sess_map and cur not in anc:
                anc.add(cur)
                cur = sess_map[cur].get("parent")
            for up in decl["sessions"]:
                if up in anc:
                    out.append({
                        "session": s,
                        "redundant_sessions_clause": up,
                        "reason": f"{up} already in parent chain of {s}",
                    })
        return out

    redundant_now = find_redundant(sessions)

    # Apply recommended swaps to a copy and re-scan for new redundancies
    swapped = {k: {"parent": v["parent"], "sessions": list(v["sessions"])} for k, v in sessions.items()}
    for r in recommendations:
        b = r["session"]
        new_parent = r["swap_with"]
        old_parent = r["current_parent"]
        swapped[b]["parent"] = new_parent
        # Remove U from sessions (now the parent)
        if new_parent in swapped[b]["sessions"]:
            swapped[b]["sessions"].remove(new_parent)
        # Add old parent to sessions only if NOT in ancestry of new parent
        cur = new_parent
        anc = set()
        while cur and cur in swapped and cur not in anc:
            anc.add(cur)
            cur = swapped[cur].get("parent")
        if old_parent not in anc:
            swapped[b]["sessions"].append(old_parent)

    redundant_after_swap = find_redundant(swapped)
    # The new redundancies are those in after-swap but not before-swap
    redundant_now_set = {(r["session"], r["redundant_sessions_clause"]) for r in redundant_now}
    redundant_new = [
        r for r in redundant_after_swap
        if (r["session"], r["redundant_sessions_clause"]) not in redundant_now_set
    ]
    redundant = redundant_now

    t_total = time.perf_counter() - t0

    # Write outputs
    out_json = {
        "construction_time_s": {
            "parse_root": round(t_root, 3),
            "parse_build_log": round(t_log, 3),
            "load_theory_timings_blobs": round(t_db, 3),
            "build_graph": round(t_graph, 3),
            "total": round(t_total, 3),
        },
        "graph": {
            "nodes": {s: nodes[s] for s in sorted(nodes)},
            "edges": sorted(edges, key=lambda e: (e["src"], e["dst"])),
        },
        "swap_recommendations": recommendations,
        "redundant_sessions_clauses": redundant,
        "redundant_after_swap": redundant_new,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out_json, indent=2))

    md = []
    md.append("# Single session-DAG analysis\n")
    md.append("Replaces the previous two-graph (theory + session) model with a single\n")
    md.append("session-granularity DAG. Edge weights collapse the theory-level\n")
    md.append("reprocessing cost down to inter-session aggregates.\n\n")
    md.append("## Construction time\n\n")
    md.append("| Step | Wall (s) |\n")
    md.append("|---|---:|\n")
    md.append(f"| parse ROOT | {t_root:.3f} |\n")
    md.append(f"| parse build_log.txt | {t_log:.3f} |\n")
    md.append(f"| load 29 theory_timings BLOBs | {t_db:.3f} |\n")
    md.append(f"| build graph + edge weights | {t_graph:.3f} |\n")
    md.append(f"| **total** | **{t_total:.3f}** |\n\n")
    md.append(f"Graph size: {len(nodes)} nodes, {len(edges)} edges "
              f"(vs prior theory-DAG 1094 nodes / 1961 edges).\n\n")
    md.append("## Top CSTR swap recommendations (est_aggregate_saving > 50s)\n\n")
    md.append("| Session | Current parent | Swap with | Reprocess cost | Est. post-swap cost | Est. saving | Shared theories |\n")
    md.append("|---|---|---|---:|---:|---:|---:|\n")
    for r in recommendations[:10]:
        md.append(
            f"| {r['session']} | {r['current_parent']} | {r['swap_with']} | "
            f"{r['current_reprocess_cost']:.1f}s | {r['est_post_swap_cost']:.1f}s | "
            f"**{r['est_aggregate_saving']:.1f}s** | {r['n_shared_theories']} |\n"
        )
    md.append("\n## Redundant `sessions` clauses (parent ancestry overlap, current graph)\n\n")
    if redundant:
        md.append("| Session | Redundant `sessions` entry | Why |\n")
        md.append("|---|---|---|\n")
        for r in redundant:
            md.append(f"| {r['session']} | {r['redundant_sessions_clause']} | {r['reason']} |\n")
    else:
        md.append("(none)\n")
    md.append("\n## New redundancies created by applying the swaps above\n\n")
    if redundant_new:
        md.append("These clauses become no-ops only AFTER the recommended swaps are applied:\n\n")
        md.append("| Session | Newly-redundant `sessions` entry | Why |\n")
        md.append("|---|---|---|\n")
        for r in redundant_new:
            md.append(f"| {r['session']} | {r['redundant_sessions_clause']} | {r['reason']} |\n")
    else:
        md.append("(none)\n")
    md.append("\n## Top 15 sessions edges by reprocess weight\n\n")
    md.append("| Source (upstream) | Destination | Kind | Weight (s) | Shared thys |\n")
    md.append("|---|---|---|---:|---:|\n")
    top_edges = sorted(
        [e for e in edges if e["kind"] == "sessions"],
        key=lambda e: -e["weight"],
    )[:15]
    for e in top_edges:
        md.append(
            f"| {e['src']} | {e['dst']} | {e['kind']} | {e['weight']:.1f} | {e.get('n_shared', 0)} |\n"
        )
    OUT_MD.write_text("".join(md))

    # Stderr summary for human reader
    print(f"\n=== TOTAL construction time: {t_total:.3f}s ===", file=sys.stderr)
    print(f"=== Wrote {OUT_MD.relative_to(REPO)} ===", file=sys.stderr)
    print(f"=== Wrote {OUT_JSON.relative_to(REPO)} ===", file=sys.stderr)
    print(f"\nTop {min(5, len(recommendations))} swap recommendations:", file=sys.stderr)
    for r in recommendations[:5]:
        print(
            f"  {r['session']}: swap parent {r['current_parent']} ↔ {r['swap_with']} "
            f"(reprocess {r['current_reprocess_cost']:.0f}s → est {r['est_post_swap_cost']:.0f}s, "
            f"save ~{r['est_aggregate_saving']:.0f}s)",
            file=sys.stderr,
        )
    if redundant:
        print(f"\nRedundant `sessions` clauses ({len(redundant)}):", file=sys.stderr)
        for r in redundant:
            print(f"  {r['session']}: drop `sessions {r['redundant_sessions_clause']}`", file=sys.stderr)
    if redundant_new:
        print(f"\nNew redundancies after swap ({len(redundant_new)}):", file=sys.stderr)
        for r in redundant_new:
            print(f"  {r['session']}: drop `sessions {r['redundant_sessions_clause']}`", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
