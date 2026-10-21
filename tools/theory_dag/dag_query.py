#!/usr/bin/env python3
"""dag_query.py — coevolve-facing queries over reports/theory-dag-<ARCH>.json.

Edges in the JSON are IMPORT direction: [importer, imported].
For repair we need the REVERSE view:

  dependents T   — transitive closure of theories that (transitively) import T
                   = the blast-radius upper bound of a change in T,
                   emitted in topological repair order (dependencies first).
  order T1 T2 …  — topo-sort the given theories among themselves (repair order).

Usage:
  python3 dag_query.py [--dag <json>] dependents AInvs.Machine_AI [--session AInvs]
  python3 dag_query.py [--dag <json>] order Refine.Bits_R Refine.Untyped_R ...

The DAG is a heuristic index (over-approximation; recomputed once per repair
campaign, read-only during it). The build oracle remains the only truth.
"""
import argparse, json, os, sys
from collections import defaultdict, deque
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent


def load(dag_path):
    d = json.load(open(dag_path))
    importers = defaultdict(set)   # imported -> {importer}
    imports = defaultdict(set)     # importer -> {imported}
    for a, b in d["edges"]:
        imports[a].add(b)
        importers[b].add(a)
    nodes = {n["name"]: n for n in d["nodes"]}
    return nodes, imports, importers


def dependents_closure(importers, roots):
    seen = set(roots)
    q = deque(roots)
    while q:
        v = q.popleft()
        for w in importers.get(v, ()):
            if w not in seen:
                seen.add(w)
                q.append(w)
    return seen - set(roots)


def topo_order(imports, subset):
    """Order subset so that dependencies come before dependents."""
    sub = set(subset)
    indeg = {v: 0 for v in sub}
    for v in sub:
        for w in imports.get(v, ()):
            if w in sub:
                indeg[v] += 1
    q = deque(sorted(v for v, d in indeg.items() if d == 0))
    out = []
    while q:
        v = q.popleft()
        out.append(v)
        for u in sub:
            if v in imports.get(u, ()) :
                indeg[u] -= 1
                if indeg[u] == 0:
                    q.append(u)
    if len(out) != len(sub):   # cycle (shouldn't happen in a theory DAG)
        out += sorted(sub - set(out))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dag", default=None)
    ap.add_argument("cmd", choices=["dependents", "order"])
    ap.add_argument("names", nargs="+")
    ap.add_argument("--session", default=None,
                    help="restrict output to one session (e.g. AInvs)")
    ap.add_argument("--by-session", action="store_true",
                    help="summarize dependents per session (derived session "
                         "view: L1/L3 classification + implicated heaps)")
    args = ap.parse_args()

    dag_path = args.dag or (REPO / "reports" /
                            f"theory-dag-{os.environ.get('L4V_ARCH','ARM')}.json")
    nodes, imports, importers = load(dag_path)
    for n in args.names:
        if n not in nodes:
            sys.exit(f"unknown theory {n!r} (canonical form: Session.Theory; "
                     f"dag={dag_path})")

    if args.cmd == "dependents":
        dep = dependents_closure(importers, args.names)
        if args.by_session:
            per = {}
            for v in dep:
                per.setdefault(nodes[v]["session"], []).append(v)
            root_sess = {nodes[n]["session"] for n in args.names}
            for sess in sorted(per, key=lambda k: -len(per[k])):
                tag = "SAME" if sess in root_sess else "CROSS"
                print("%-18s %4d theories  [%s]" % (sess, len(per[sess]), tag))
            print("# level hint: %d session(s) implicated -> %s"
                  % (len(per), "L1 (single-session)" if len(per) <= 1
                     else "L3-shaped (cross-session upper bound)"))
            return
        if args.session:
            dep = {v for v in dep if nodes[v]["session"] == args.session}
        for v in topo_order(imports, dep):
            print(v)
        print(f"# {len(dep)} dependent theories of {','.join(args.names)}"
              + (f" within {args.session}" if args.session else ""),
              file=sys.stderr)
    else:
        for v in topo_order(imports, set(args.names)):
            print(v)


if __name__ == "__main__":
    main()
