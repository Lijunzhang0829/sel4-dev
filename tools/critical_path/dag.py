#!/usr/bin/env python3
"""
tools/critical_path/dag.py
==========================

Step 4 of the DAG critical-path pipeline.

WHAT IT DOES
------------
For each of the four change types (proof, spec, haskell, c) defined in
tools/critical_path/manifests/, this script:

  1. Computes the rebuild closure (sessions ∪ theories that must be
     re-verified when a change of this type lands).
  2. Builds the closure-induced subgraph of the theory DAG.
  3. Runs CPM (Critical Path Method) on the subgraph using each theory's
     `total_elapsed_across_sessions` as node weight.
  4. Reports per-theory: EST/EFT, slack, fanout, on_critical_path.

EDGE / GRAPH SEMANTICS
----------------------
reports/theory-dag.json edges are in IMPORT direction: [importer, imported].
For CPM (build order), we reverse: a dependency (imported) must be built
BEFORE the dependent (importer). After reversal:

    build_preds[v]  = theories v depends on = theories v imports
    build_succs[v]  = theories that depend on v = theories that import v

This means:
    - sources (build_preds empty) = foundational theories (Word_Lib, etc.)
    - sinks (build_succs empty) = top-level theories (CRefine.ADT_C, etc.)
    - longest weighted path = bottleneck dependency chain

INPUTS
------
    reports/theory-dag.json
    reports/session-dag.json
    reports/session-duplication-scan.json
    heaps/db-archive/*.db    (full per-theory cross-session elapsed)
    tools/critical_path/manifests/{proof,spec,haskell,c}.py

OUTPUT
------
    reports/critical-path-all.json
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections import defaultdict, deque
from fnmatch import fnmatch
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tools" / "critical_path"))
from manifests import c as m_c                                # noqa: E402
from manifests import haskell as m_haskell                    # noqa: E402
from manifests import proof as m_proof                        # noqa: E402
from manifests import spec_abstract as m_spec_abstract        # noqa: E402
from manifests import spec_cspec as m_spec_cspec              # noqa: E402
from manifests import spec_invariant as m_spec_invariant      # noqa: E402
from manifests import spec_lib as m_spec_lib                  # noqa: E402

THEORY_DAG_JSON = REPO / "reports" / "theory-dag.json"
SESSION_DAG_JSON = REPO / "reports" / "session-dag.json"
DUP_SCAN_JSON = REPO / "reports" / "session-duplication-scan.json"
DB_DIR = REPO / "heaps" / "db-archive"
OUT_JSON = REPO / "reports" / "critical-path-all.json"

REC_RE = re.compile(
    r"name=([^\x06]+)\x06elapsed=([0-9.]+)\x06cpu=([0-9.]+)\x06gc=([0-9.]+)"
)


# ----------------------------------------------------------------------
# Per-theory weights (cross-session sum)
# ----------------------------------------------------------------------

def extract_theory_timings(db_path: Path) -> list[tuple[str, float, float, float]]:
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT session_name, theory_timings FROM isabelle_session_info"
        ).fetchone()
    except sqlite3.OperationalError:
        return []
    if row is None or row[1] is None:
        return []
    blob = row[1]
    with tempfile.NamedTemporaryFile(suffix=".zst", delete=False) as fz:
        fz.write(blob)
        fz_path = Path(fz.name)
    try:
        text = subprocess.check_output(["zstd", "-dcq", str(fz_path)]).decode("utf-8")
    finally:
        fz_path.unlink(missing_ok=True)
    return [(n, float(e), float(c), float(g)) for n, e, c, g in REC_RE.findall(text)]


def build_theory_weights() -> dict[str, dict]:
    """Returns {theory_name: {total_elapsed, n_sessions, max_per_session, sessions_with}}.
    Aggregates across every db-archive/*.db file."""
    per_theory: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for db in sorted(DB_DIR.glob("*.db")):
        sess = db.stem
        for n, e, _c, _g in extract_theory_timings(db):
            per_theory[n].append((sess, e))
    weights: dict[str, dict] = {}
    for thy, rows in per_theory.items():
        total = sum(e for _, e in rows)
        max_e = max(e for _, e in rows)
        weights[thy] = {
            "total_elapsed": round(total, 1),
            "n_sessions": len(rows),
            "max_per_session": round(max_e, 1),
            "sessions_with": [s for s, _ in rows],
            "duplication_overhead": round(total - max_e, 1),
        }
    return weights


# ----------------------------------------------------------------------
# Closure computation
# ----------------------------------------------------------------------

def matches_any_pattern(path_str: str, patterns: list[str]) -> bool:
    """Glob match with simple {a,b,c} brace expansion."""
    for p in patterns:
        if "{" in p and "}" in p:
            pre, rest = p.split("{", 1)
            options, post = rest.split("}", 1)
            for opt in options.split(","):
                if fnmatch(path_str, pre + opt.strip() + post):
                    return True
        else:
            if fnmatch(path_str, p):
                return True
    return False


def find_entry_theories(manifest: dict, theory_nodes: list[dict]) -> list[str]:
    """Return canonical names of theory nodes whose path matches an entry_paths glob."""
    matched = []
    for n in theory_nodes:
        try:
            rel = str(Path(n["path"]).resolve().relative_to(REPO))
        except ValueError:
            continue
        if matches_any_pattern(rel, manifest["entry_paths"]):
            matched.append(n["name"])
    return matched


def manifest_touches_session_dir(manifest: dict, session_dir: str) -> bool:
    """Heuristic: any entry_path glob has a prefix overlapping this session_dir."""
    try:
        sd = str(Path(session_dir).relative_to(REPO))
    except ValueError:
        return False
    for p in manifest["entry_paths"]:
        prefix = p.split("**")[0].split("*")[0].rstrip("/")
        if not prefix:
            continue
        if sd.startswith(prefix) or prefix.startswith(sd):
            return True
    return False


def session_closure(
    manifest: dict,
    entry_sessions: set[str],
    session_nodes: list[dict],
    session_edges: list[dict],
) -> set[str]:
    """Compute the rebuild closure as a set of session names."""
    sessions_by_name = {n["session"]: n for n in session_nodes}

    starts: set[str] = set()
    starts.update(entry_sessions)
    starts.update(manifest.get("entry_session_hints", []))
    for ge in manifest.get("generated_edges", []):
        starts.add(ge["to_session"])
    starts.update(manifest.get("affected_layer_sessions", []))

    # Reverse adjacency in session-DAG: edges are "from descendant -> to ancestor".
    # For closure (downstream of a change in X), find sessions that have X as
    # parent OR imported_session (i.e., the descendants in build-order).
    rev_adj: dict[str, set[str]] = defaultdict(set)
    for e in session_edges:
        rev_adj[e["to"]].add(e["from"])

    closure = set(starts)
    queue = deque(starts)
    while queue:
        s = queue.popleft()
        for desc in rev_adj.get(s, set()):
            if desc not in closure:
                closure.add(desc)
                queue.append(desc)

    # Apply closure_excludes_unless_in_path
    for ex in manifest.get("closure_excludes_unless_in_path", []):
        node = sessions_by_name.get(ex)
        if node is None:
            closure.discard(ex)
            continue
        sd = node.get("session_dir", "")
        if not sd or not manifest_touches_session_dir(manifest, sd):
            closure.discard(ex)

    return closure


def theory_closure_from_sessions(
    closure_sessions: set[str], theory_nodes: list[dict]
) -> set[str]:
    """All theories whose owning session is in closure_sessions."""
    out: set[str] = set()
    for n in theory_nodes:
        if n["session"] in closure_sessions:
            out.add(n["name"])
    return out


# ----------------------------------------------------------------------
# CPM (Critical Path Method)
# ----------------------------------------------------------------------

def critical_path_analysis(
    closure_theories: set[str],
    theory_edges_import_dir: list[tuple[str, str]],
    weights: dict[str, dict],
) -> dict:
    """CPM on the closure subgraph, using build-order edges (reversed from import-dir)."""
    nodes = sorted(closure_theories)

    # Build-order graph: dep -> dependent. Reverse import direction.
    build_preds: dict[str, set[str]] = defaultdict(set)
    build_succs: dict[str, set[str]] = defaultdict(set)
    for f, t in theory_edges_import_dir:
        if f in closure_theories and t in closure_theories:
            # Original: f imports t => in build order, t -> f
            build_preds[f].add(t)
            build_succs[t].add(f)

    # Topo sort in build order (Kahn): sources first
    in_deg = {n: len(build_preds[n]) for n in nodes}
    q = deque([n for n in nodes if in_deg[n] == 0])
    topo: list[str] = []
    while q:
        n = q.popleft()
        topo.append(n)
        for s in build_succs[n]:
            in_deg[s] -= 1
            if in_deg[s] == 0:
                q.append(s)

    if len(topo) != len(nodes):
        print(
            f"  WARNING: cycle in closure subgraph; topo={len(topo)}/{len(nodes)}",
            file=sys.stderr,
        )

    def w(n: str) -> float:
        return weights.get(n, {}).get("total_elapsed", 0.0)

    EST: dict[str, float] = {}
    EFT: dict[str, float] = {}
    for n in topo:
        if not build_preds[n]:
            EST[n] = 0.0
        else:
            EST[n] = max(EFT.get(p, 0.0) for p in build_preds[n])
        EFT[n] = EST[n] + w(n)

    project_total = max(EFT.values()) if EFT else 0.0

    LFT: dict[str, float] = {}
    LST: dict[str, float] = {}
    for n in reversed(topo):
        if not build_succs[n]:
            LFT[n] = project_total
        else:
            LFT[n] = min(LST.get(s, project_total) for s in build_succs[n])
        LST[n] = LFT[n] - w(n)

    slack: dict[str, float] = {n: LFT[n] - EFT[n] for n in nodes}
    EPS = 0.05
    critical: set[str] = {n for n in nodes if slack[n] < EPS and w(n) > 0.0}

    # Fanout: # of theories transitively depending on this one (importers)
    fanout: dict[str, int] = {}
    for n in nodes:
        seen: set[str] = set()
        q2 = deque([n])
        while q2:
            cur = q2.popleft()
            for s in build_succs[cur]:
                if s not in seen:
                    seen.add(s)
                    q2.append(s)
        fanout[n] = len(seen)

    return {
        "topo_order": topo,
        "EST": EST,
        "EFT": EFT,
        "LFT": LFT,
        "LST": LST,
        "slack": slack,
        "fanout": fanout,
        "critical_set": critical,
        "project_total_weight": round(project_total, 1),
    }


# ----------------------------------------------------------------------
# Main pipeline
# ----------------------------------------------------------------------

def main() -> int:
    print("loading inputs ...", file=sys.stderr)
    theory_dag = json.loads(THEORY_DAG_JSON.read_text())
    session_dag = json.loads(SESSION_DAG_JSON.read_text())

    print("extracting full per-theory weights from db-archive ...", file=sys.stderr)
    weights = build_theory_weights()
    print(f"  weights computed for {len(weights)} theories", file=sys.stderr)

    manifests = {
        "proof": m_proof.MANIFEST,
        "spec_abstract": m_spec_abstract.MANIFEST,
        "spec_invariant": m_spec_invariant.MANIFEST,
        "spec_cspec": m_spec_cspec.MANIFEST,
        "spec_lib": m_spec_lib.MANIFEST,
        "haskell": m_haskell.MANIFEST,
        "c": m_c.MANIFEST,
    }

    theory_edges = [tuple(e) for e in theory_dag["edges"]]
    sessions_by_thy = {n["name"]: n["session"] for n in theory_dag["nodes"]}

    results: dict[str, dict] = {}
    for name, manifest in manifests.items():
        print(f"\n=== change type: {name} ===", file=sys.stderr)

        entry_thys = find_entry_theories(manifest, theory_dag["nodes"])
        entry_sess = {sessions_by_thy[t] for t in entry_thys if t in sessions_by_thy}

        sclos = session_closure(manifest, entry_sess, session_dag["nodes"], session_dag["edges"])
        sclos.update(entry_sess)

        tclos = theory_closure_from_sessions(sclos, theory_dag["nodes"])

        cp = critical_path_analysis(tclos, theory_edges, weights)

        # ---- Rankings ----

        def make_entry(n: str) -> dict:
            wd = weights.get(n, {})
            return {
                "theory": n,
                "session": sessions_by_thy.get(n, "<unknown>"),
                "weight_total_elapsed": wd.get("total_elapsed", 0.0),
                "n_sessions": wd.get("n_sessions", 0),
                "max_per_session": wd.get("max_per_session", 0.0),
                "duplication_overhead": wd.get("duplication_overhead", 0.0),
                "EST": round(cp["EST"][n], 1),
                "EFT": round(cp["EFT"][n], 1),
                "slack": round(cp["slack"][n], 1),
                "fanout": cp["fanout"][n],
                "on_critical_path": n in cp["critical_set"],
            }

        # Critical path nodes in topo (build) order
        critical_in_order = [n for n in cp["topo_order"] if n in cp["critical_set"]]
        # Top by fanout (high-fanout = high cross-cutting impact)
        ranked_by_fanout = sorted(tclos, key=lambda n: (-cp["fanout"][n], -weights.get(n, {}).get("total_elapsed", 0.0)))
        # Top by low slack among nodes with non-trivial weight
        nontrivial = [n for n in tclos if weights.get(n, {}).get("total_elapsed", 0.0) > 5.0]
        ranked_by_slack = sorted(nontrivial, key=lambda n: (cp["slack"][n], -weights.get(n, {}).get("total_elapsed", 0.0)))

        # Closure-internal totals (for sanity / sizing)
        closure_total_weight = sum(weights.get(n, {}).get("total_elapsed", 0.0) for n in tclos)

        results[name] = {
            "manifest": {
                "name": manifest["name"],
                "description": manifest["description"],
                "entry_paths": manifest["entry_paths"],
            },
            "entry_theory_count": len(entry_thys),
            "entry_session_count": len(entry_sess),
            "session_closure": sorted(sclos),
            "session_closure_size": len(sclos),
            "theory_closure_size": len(tclos),
            "closure_total_weight": round(closure_total_weight, 1),
            "critical_path_total_weight": cp["project_total_weight"],
            "critical_path_node_count": len(cp["critical_set"]),
            "critical_path_in_order": [make_entry(n) for n in critical_in_order if weights.get(n, {}).get("total_elapsed", 0.0) > 0.0],
            "top_by_fanout": [make_entry(n) for n in ranked_by_fanout[:15]],
            "top_by_slack_low": [make_entry(n) for n in ranked_by_slack[:25]],
        }

        print(f"  entry theories matched:    {len(entry_thys)}", file=sys.stderr)
        print(f"  entry sessions:            {len(entry_sess)}", file=sys.stderr)
        print(f"  session closure size:      {len(sclos)}", file=sys.stderr)
        print(f"  theory closure size:       {len(tclos)}", file=sys.stderr)
        print(f"  closure total weight:      {closure_total_weight:.1f}s", file=sys.stderr)
        print(f"  critical path total wgt:   {cp['project_total_weight']:.1f}s", file=sys.stderr)
        print(f"  critical path nodes:       {len(cp['critical_set'])}", file=sys.stderr)

    out = {
        "metadata": {
            "data_sources": {
                "theory_dag": str(THEORY_DAG_JSON.relative_to(REPO)),
                "session_dag": str(SESSION_DAG_JSON.relative_to(REPO)),
                "duplication_scan": str(DUP_SCAN_JSON.relative_to(REPO)),
                "db_archive": str(DB_DIR.relative_to(REPO)),
            },
            "edge_semantics": (
                "theory-DAG edges are in import direction (importer -> imported); "
                "CPM uses reversed (build-order) graph: dep -> dependent."
            ),
            "weight_definition": (
                "weight(theory) = sum of elapsed across every session whose "
                ".db theory_timings BLOB lists this theory (P0.5 cost model)."
            ),
        },
        "theory_weights_summary": {
            "n_theories_with_weight": len(weights),
            "total_weight_all": round(sum(w["total_elapsed"] for w in weights.values()), 1),
            "duplication_total_overhead": round(sum(w["duplication_overhead"] for w in weights.values()), 1),
        },
        "change_types": results,
    }

    OUT_JSON.write_text(json.dumps(out, indent=2) + "\n")
    print(f"\nwrote {OUT_JSON}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
