#!/usr/bin/env python3
"""
tools/critical_path/focused_closure.py
======================================

Query the *real* rebuild closure for a single .thy file change, as
opposed to the worst-case 345-theory closure reported in
critical-path-proof.md (which assumes "any proof file may change").

If you change one specific .thy, the actual closure is:

    {target} ∪ {transitive importers of target in theory-DAG}

This script BFS-walks the theory-DAG in importer direction starting
from the target, sums weights (using the CSTR cross-session cost
model), and reports:

    - n theories in real closure
    - sessions touched
    - top heavy theories in the closure (likely Step-2 rebuilds)
    - duplication amplification (n_sess > 1) for the target itself
    - critical path within the focused closure

USAGE
-----
    python3 tools/critical_path/focused_closure.py PATH_OR_NAME

    where PATH_OR_NAME is one of:
      - a path like "proof/refine/ARM/CSpace1_R.thy" or
        "verification/l4v/proof/refine/ARM/CSpace1_R.thy"
      - a canonical name like "Refine.CSpace1_R"

EXAMPLES
--------
    python3 tools/critical_path/focused_closure.py proof/refine/ARM/CSpace1_R.thy
    python3 tools/critical_path/focused_closure.py Refine.Finalise_R
    python3 tools/critical_path/focused_closure.py proof/asmrefine/SEL4GraphRefine.thy
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict, deque
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
THEORY_DAG_JSON = REPO / "CSTR-2" / "outputs" / "theory-dag.json"
CRITICAL_PATH_JSON = REPO / "CSTR-2" / "outputs" / "critical-path-all.json"


def resolve_target(arg: str, nodes: list[dict]) -> dict | None:
    """Find the theory node matching `arg` (path or canonical name)."""
    # Canonical-name match (e.g., "Refine.Finalise_R")
    for n in nodes:
        if n["name"] == arg:
            return n
    # Path match — try exact, then with REPO prefix, then suffix
    arg_p = Path(arg).resolve() if Path(arg).is_absolute() else (REPO / arg).resolve()
    for n in nodes:
        if Path(n["path"]).resolve() == arg_p:
            return n
    # Suffix match (e.g., user gave "Finalise_R.thy")
    for n in nodes:
        if str(n["path"]).endswith(arg):
            return n
    return None


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2

    arg = sys.argv[1]
    theory_dag = json.loads(THEORY_DAG_JSON.read_text())
    cp_data = json.loads(CRITICAL_PATH_JSON.read_text())

    target = resolve_target(arg, theory_dag["nodes"])
    if target is None:
        print(f"ERROR: could not find theory matching '{arg}'", file=sys.stderr)
        print(f"Try: a path like proof/refine/ARM/CSpace1_R.thy or a name like Refine.CSpace1_R",
              file=sys.stderr)
        return 1

    # Build importer-direction adjacency: edge (f,t) means f imports t,
    # so for "downstream of t" we walk preds[t] (importers of t).
    edges = [tuple(e) for e in theory_dag["edges"]]
    importers: dict[str, set[str]] = defaultdict(set)
    for f, t in edges:
        importers[t].add(f)

    # BFS walk transitive importers
    target_name = target["name"]
    closure = {target_name}
    queue = deque([target_name])
    while queue:
        n = queue.popleft()
        for imp in importers.get(n, set()):
            if imp not in closure:
                closure.add(imp)
                queue.append(imp)

    # Pull weights from critical-path-all.json
    # Weights are in change_types[*].top_by_slack_low etc.; build a quick map
    # from any change type that includes this theory in its closure.
    weight_map: dict[str, dict] = {}
    for ct in cp_data["change_types"].values():
        for entry in (ct.get("critical_path_in_order", [])
                      + ct.get("top_by_fanout", [])
                      + ct.get("top_by_slack_low", [])):
            weight_map.setdefault(entry["theory"], entry)

    sessions_by_name = {n["name"]: n["session"] for n in theory_dag["nodes"]}

    # Categorise closure entries
    closure_entries = []
    for thy in closure:
        info = weight_map.get(thy)
        sess = sessions_by_name.get(thy, "<unknown>")
        if info:
            closure_entries.append({
                "theory": thy,
                "session": sess,
                "weight": info["weight_total_elapsed"],
                "n_sessions": info["n_sessions"],
                "duplication_overhead": info["duplication_overhead"],
                "fanout": info.get("fanout", 0),
            })
        else:
            closure_entries.append({
                "theory": thy,
                "session": sess,
                "weight": 0.0,
                "n_sessions": 0,
                "duplication_overhead": 0.0,
                "fanout": 0,
            })

    # Sort by weight desc
    closure_entries.sort(key=lambda r: -r["weight"])

    total_weight = sum(r["weight"] for r in closure_entries)
    total_dup = sum(r["duplication_overhead"] for r in closure_entries)
    sessions_touched = sorted({r["session"] for r in closure_entries})
    no_weight = sum(1 for r in closure_entries if r["weight"] == 0)

    # Print report
    print(f"Focused rebuild closure for: {target_name}")
    print(f"  source path:           {target['path']}")
    print(f"  owning session:        {target['session']}")
    print()
    print(f"=== closure stats ===")
    print(f"  theories:              {len(closure_entries)}")
    print(f"    with timing data:    {len(closure_entries) - no_weight}")
    print(f"    no timing data:      {no_weight}")
    print(f"  sessions touched ({len(sessions_touched)}): {', '.join(sessions_touched)}")
    print(f"  total weight (CSTR cost model):           {total_weight:>9.1f}s")
    print(f"  of which duplication overhead (CSTR):     {total_dup:>9.1f}s")
    print()

    # Target self-stats
    self_info = weight_map.get(target_name)
    if self_info:
        print(f"=== target self ===")
        print(f"  weight:                {self_info['weight_total_elapsed']:.1f}s")
        print(f"  n_sessions (CSTR):     {self_info['n_sessions']}  "
              f"({'YES' if self_info['n_sessions'] > 1 else 'no'} "
              f"— CSTR cross-session amplification)")
        print(f"  duplication_overhead:  {self_info['duplication_overhead']:.1f}s")
        print(f"  fanout (in proof CP):  {self_info.get('fanout', '—')}")
        print(f"  on critical path:      {self_info.get('on_critical_path', '—')}")
        print()

    print(f"=== top 15 heaviest theories in focused closure ===")
    print(f"  {'weight':>7}  {'n_sess':>6}  {'dup':>5}  {'session':<22}  theory")
    for r in closure_entries[:15]:
        if r["weight"] == 0:
            continue
        print(f"  {r['weight']:7.1f}  {r['n_sessions']:6d}  "
              f"{r['duplication_overhead']:5.1f}  {r['session']:<22}  {r['theory']}")

    print()
    full_proof_closure = cp_data["change_types"]["proof"]
    print(f"=== for comparison: full proof closure (worst-case) ===")
    print(f"  theories:              {full_proof_closure['theory_closure_size']} "
          f"(yours: {len(closure_entries)} → {100*len(closure_entries)/full_proof_closure['theory_closure_size']:.1f}%)")
    print(f"  sessions:              {full_proof_closure['session_closure_size']} "
          f"(yours: {len(sessions_touched)})")
    print(f"  closure total weight:  {full_proof_closure['closure_total_weight']:.0f}s "
          f"(yours: {total_weight:.0f}s → {100*total_weight/full_proof_closure['closure_total_weight']:.1f}%)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
