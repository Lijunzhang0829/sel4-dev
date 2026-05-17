#!/usr/bin/env python3
"""
tools/critical_path/promote_simulator.py

For each "shared ancestor" candidate (a session whose theories are
source-re-executed in 2+ downstream consumers), estimate the wall
savings of promoting it into the consumers' `+` chains, and check
feasibility under Isabelle's single-`+`-parent rule.

This is the formal version of target (b) from the original critical-
path-summary: "shared 3rd-party heap-merging."

Output:
    reports/promote-simulator.md
    reports/promote-simulator.json

Method:

1. For every theory T appearing in N ≥ 2 session BLOBs (post-swap data
   from heaps/db-archive/), tally cost = sum(elapsed in each BLOB).
   This is the wall T currently consumes across the build.

2. Best-achievable wall for T after promotion = max(elapsed across
   sessions). T runs once in the lightest ambient ML state; everywhere
   else it's heap-loaded for free.

3. Group T's costs by origin namespace (the session whose `+` chain
   T's .thy file lives in). Aggregate.

4. For each candidate origin O and its consumers {C_i}:

   - Feasibility: for each C_i, can O be inserted into C_i's `+` chain?
     The classical promotion is "insert O as a heap-merged ancestor".
     Constraints:
       a) No cycle: O must not be downstream of C_i.
       b) O's own `+` chain must not conflict with C_i's existing `+` chain.
       c) Promoting O adds O.own_total to C_i's ML state. If C_i is
          already large, the amplification of OTHER theories in C_i
          may grow more than the savings.

   - Honest accounting: the wall is also paid at the synthetic combined
     session's OWN build. The savings are only NET if the combined
     session is built ONCE while the re-exec is currently paid N times.

5. Filter: by user 2026-05-17 instruction, drop candidates with savings
   < 100s — those don't justify ROOT churn.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
DB_DIR = REPO / "heaps" / "db-archive"
SCAN_JSON = REPO / "reports" / "session-duplication-scan.json"
OUT_MD = REPO / "reports" / "promote-simulator.md"
OUT_JSON = REPO / "reports" / "promote-simulator.json"

sys.path.insert(0, str(REPO / "tools" / "lemma_inventory"))
sys.path.insert(0, str(REPO / "tools" / "critical_path"))
import parse_roots  # noqa: E402
from move_simulator import effective_roots, build_ancestors, ancestor_chain  # noqa: E402

REC_RE = re.compile(
    r"name=([^\x06]+)\x06elapsed=([0-9.]+)\x06cpu=([0-9.]+)\x06gc=([0-9.]+)"
)

# User instruction 2026-05-17: ignore changes whose savings are <100s.
SAVINGS_THRESHOLD_S = 100.0


def load_blobs():
    data = {}
    for db in sorted(DB_DIR.glob("*.db")):
        con = sqlite3.connect(str(db))
        n, blob = con.execute(
            "SELECT session_name, theory_timings FROM isabelle_session_info"
        ).fetchone()
        con.close()
        if blob is None:
            continue
        with tempfile.NamedTemporaryFile(suffix=".zst", delete=False) as fz:
            fz.write(blob); fz_path = fz.name
        try:
            text = subprocess.check_output(["zstd", "-dcq", fz_path]).decode("utf-8")
        finally:
            os.unlink(fz_path)
        data[n] = {t: float(e) for t, e, _, _ in REC_RE.findall(text)}
    return data


def feasibility(origin, consumers, sessions, own_sizes):
    """For origin (a candidate to promote) and consumers (sessions that
    currently re-execute origin's content), check whether origin can be
    made a `+` ancestor of each consumer.

    Returns a dict: consumer -> (verdict, explanation).
    """
    out = {}
    for c in consumers:
        if c not in sessions:
            out[c] = ("UNKNOWN", f"`{c}` not in parsed ROOTs")
            continue
        c_ancestors = build_ancestors(c, sessions)
        # Case 1: origin is ALREADY in c's `+` chain (shouldn't appear in BLOB then).
        if origin in ancestor_chain(c, sessions):
            out[c] = ("STALE-DECL", "already in `+` chain (re-exec is a redundant `sessions` decl)")
            continue
        # Case 2: cycle check — c must not be in origin's ancestors.
        if origin not in sessions:
            out[c] = ("UNKNOWN", f"origin session `{origin}` not in parsed ROOTs")
            continue
        if c in build_ancestors(origin, sessions):
            out[c] = ("CYCLE", f"`{c}` is upstream of `{origin}`; promoting would cycle")
            continue
        # Case 3: single-`+` constraint. c currently has some `+` parent.
        # Promoting origin means we have to choose: keep current parent as `+`
        # and somehow also make origin `+`, OR swap to origin and demote current
        # parent to `sessions` (re-exec).
        cur_parent = sessions.get(c, {}).get("parent")
        cur_parent_size = own_sizes.get(cur_parent, 0) if cur_parent else 0
        origin_size = own_sizes.get(origin, 0)
        # Insertion option: can origin be placed in c's existing `+` chain
        # without replacing the current parent? Only if origin happens to be a
        # parent of cur_parent already (then it'd be there for free, which it
        # isn't since we're examining a re-exec edge). So insertion requires
        # one of:
        #   (a) cur_parent inherits from origin → origin already in chain (Case 1)
        #   (b) we rebuild cur_parent to inherit from origin → cascading change
        # We mark these as RESTRUCTURE-REQUIRED.
        if origin_size > cur_parent_size:
            margin = origin_size - cur_parent_size
            out[c] = ("SWAP-VIABLE",
                      f"swap candidate (origin {origin_size:.0f}s > parent {cur_parent_size:.0f}s, "
                      f"margin {margin:+.0f}s)")
        else:
            out[c] = ("SWAP-REJECTED",
                      f"swap would regress (origin {origin_size:.0f}s ≤ parent {cur_parent_size:.0f}s); "
                      f"insertion would require restructuring `{cur_parent}` to inherit from `{origin}`")
    return out


def main():
    sessions = effective_roots()
    blobs = load_blobs()

    # session own walls
    scan = json.loads(SCAN_JSON.read_text())
    own_sizes = {s["session"]: s["own_total_elapsed"] for s in scan["session_summary"]}

    # theory -> [(session, elapsed)]
    occs = defaultdict(list)
    for sess, recs in blobs.items():
        for thy, e in recs.items():
            occs[thy].append((sess, e))

    # Per-origin aggregation
    by_origin = defaultdict(lambda: {
        "n_theories": 0,
        "total_cost": 0.0,
        "max_per_theory": 0.0,
        "savings_if_heap_merged_everywhere": 0.0,
        "consumers": Counter(),
        "consumer_cost": defaultdict(float),
        "theories": [],
    })
    for thy, lst in occs.items():
        if len(lst) < 2:
            continue
        origin = thy.split(".", 1)[0] if "." in thy else thy
        g = by_origin[origin]
        walls = [e for _, e in lst]
        g["n_theories"] += 1
        g["total_cost"] += sum(walls)
        g["max_per_theory"] += max(walls)
        g["savings_if_heap_merged_everywhere"] += sum(walls) - max(walls)
        for sess, e in lst:
            if sess != origin:
                g["consumers"][sess] += 1
                g["consumer_cost"][sess] += e
        g["theories"].append(thy)

    # Filter to ≥ threshold
    cands = sorted(
        ((o, g) for o, g in by_origin.items()
         if g["savings_if_heap_merged_everywhere"] >= SAVINGS_THRESHOLD_S),
        key=lambda x: -x[1]["savings_if_heap_merged_everywhere"]
    )

    # Per-candidate feasibility on each consumer
    rows = []
    for origin, g in cands:
        feasi = feasibility(origin, list(g["consumers"].keys()), sessions, own_sizes)
        # Per-consumer rollup
        per_consumer = []
        for c in g["consumers"]:
            verdict, expl = feasi[c]
            per_consumer.append({
                "consumer": c,
                "n_theories": g["consumers"][c],
                "cost_in_consumer": round(g["consumer_cost"][c], 1),
                "verdict": verdict,
                "explanation": expl,
            })
        per_consumer.sort(key=lambda x: -x["cost_in_consumer"])
        rows.append({
            "origin": origin,
            "n_theories": g["n_theories"],
            "total_wall_across_blobs": round(g["total_cost"], 1),
            "max_per_theory_sum": round(g["max_per_theory"], 1),
            "potential_savings": round(g["savings_if_heap_merged_everywhere"], 1),
            "consumers": per_consumer,
        })

    # Build markdown
    md = []
    md.append("# Promote Simulator — Shared-Ancestor Heap-Merge Candidates\n")
    md.append("_Target (b) from the original critical-path summary: for each_")
    md.append("_origin session whose theories are source-re-executed in 2+_")
    md.append("_downstream consumers, estimate the wall savings of promoting_")
    md.append("_the origin to a `+`-merged heap ancestor of each consumer._\n")
    md.append(f"Filter: candidates with potential savings ≥ {SAVINGS_THRESHOLD_S:.0f}s only")
    md.append("(per user 2026-05-17 instruction — no small wins).\n")
    md.append("**Method**: per shared theory T appearing in K sessions, current")
    md.append("wall = sum of elapsed across K BLOBs. Best achievable = max(elapsed)")
    md.append("(T runs once in the lightest ambient ML state, heap-loaded elsewhere).")
    md.append("Savings = sum − max. This is an UPPER BOUND ignoring amplification.\n")

    md.append("## Summary\n")
    md.append("| Origin | #shared theories | Total cost (s) | Best achievable (s) | Potential savings (s) | Consumers |")
    md.append("|---|---:|---:|---:|---:|---|")
    for r in rows:
        cons_str = ", ".join(c["consumer"] for c in r["consumers"][:3])
        if len(r["consumers"]) > 3:
            cons_str += f", +{len(r['consumers'])-3}"
        md.append(f"| `{r['origin']}` | {r['n_theories']} | {r['total_wall_across_blobs']:.0f} | "
                  f"{r['max_per_theory_sum']:.0f} | **{r['potential_savings']:.0f}** | {cons_str} |")
    md.append("")
    total_potential = sum(r["potential_savings"] for r in rows)
    md.append(f"_Cumulative potential: **{total_potential:.0f}s** across {len(rows)} candidates._\n")

    # Per-candidate detailed analysis
    md.append("## Per-candidate feasibility\n")
    for r in rows:
        md.append(f"### `{r['origin']}` — potential savings {r['potential_savings']:.0f}s\n")
        md.append(f"_{r['n_theories']} theories shared, total {r['total_wall_across_blobs']:.0f}s wall across all BLOBs_\n")
        md.append("| Consumer | #thys re-exec'd | Cost in consumer (s) | Verdict | Explanation |")
        md.append("|---|---:|---:|---|---|")
        for c in r["consumers"]:
            md.append(f"| `{c['consumer']}` | {c['n_theories']} | {c['cost_in_consumer']:.1f} | "
                      f"{c['verdict']} | {c['explanation']} |")
        md.append("")
        # Roll up verdicts
        verdicts = Counter(c["verdict"] for c in r["consumers"])
        md.append("**Roll-up**:")
        for v, n in verdicts.most_common():
            md.append(f"- {v}: {n}/{len(r['consumers'])} consumers")
        # Bottom line
        viable_savings = sum(c["cost_in_consumer"] for c in r["consumers"]
                             if c["verdict"] in ("SWAP-VIABLE", "STALE-DECL"))
        blocked_savings = sum(c["cost_in_consumer"] for c in r["consumers"]
                              if c["verdict"] in ("SWAP-REJECTED", "CYCLE"))
        md.append(f"- **Viable savings (single-edge swap or stale-decl cleanup)**: {viable_savings:.0f}s")
        md.append(f"- **Blocked by single-`+` constraint**: {blocked_savings:.0f}s")
        md.append("")

    md.append("## Bottom line\n")
    total_viable = sum(
        sum(c["cost_in_consumer"] for c in r["consumers"] if c["verdict"] in ("SWAP-VIABLE", "STALE-DECL"))
        for r in rows
    )
    total_blocked = sum(
        sum(c["cost_in_consumer"] for c in r["consumers"] if c["verdict"] in ("SWAP-REJECTED", "CYCLE"))
        for r in rows
    )
    md.append(f"Across all candidates with potential savings ≥ {SAVINGS_THRESHOLD_S:.0f}s:")
    md.append(f"- **Viable (single-edge structural change)**: {total_viable:.0f}s")
    md.append(f"- **Blocked by Isabelle single-`+`-parent rule**: {total_blocked:.0f}s")
    md.append("")
    md.append("If `viable` is ~0 and `blocked` is large, the residual cost is the")
    md.append("irreducible CSTR floor under the current Isabelle model — same")
    md.append("conclusion as move_simulator / split_simulator but now quantified")
    md.append("at the theory-shared-cost granularity rather than edge granularity.")
    md.append("")
    md.append("## What 'STALE-DECL' would unlock\n")
    md.append("A `STALE-DECL` consumer has the origin ALREADY in its `+` chain,")
    md.append("but the origin's theories still appear in the consumer's BLOB —")
    md.append("meaning the `sessions <origin>` declaration in the consumer's ROOT")
    md.append("is redundant. Removing it doesn't change wall (the heap-merge is")
    md.append("already happening) but cleans up the ROOT and may avoid spurious")
    md.append("re-execution if Isabelle's resolution prefers `sessions` over `+`.\n")
    md.append("These are 0-risk ROOT cleanups but won't save wall.")

    OUT_MD.write_text("\n".join(md) + "\n")
    OUT_JSON.write_text(json.dumps({
        "savings_threshold_s": SAVINGS_THRESHOLD_S,
        "candidates": rows,
        "total_potential_savings": total_potential,
        "total_viable_savings": total_viable,
        "total_blocked_savings": total_blocked,
    }, indent=2))

    print(f"Wrote {OUT_MD}")
    print(f"\nCandidates ≥ {SAVINGS_THRESHOLD_S:.0f}s savings:")
    for r in rows:
        viable = sum(c["cost_in_consumer"] for c in r["consumers"]
                     if c["verdict"] in ("SWAP-VIABLE", "STALE-DECL"))
        blocked = sum(c["cost_in_consumer"] for c in r["consumers"]
                      if c["verdict"] in ("SWAP-REJECTED", "CYCLE"))
        print(f"  {r['origin']:18s} potential={r['potential_savings']:6.0f}s  "
              f"viable={viable:5.0f}s  blocked={blocked:5.0f}s")
    print(f"\nTotal: potential={total_potential:.0f}s, viable={total_viable:.0f}s, blocked={total_blocked:.0f}s")


if __name__ == "__main__":
    main()
