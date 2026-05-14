#!/usr/bin/env python3
"""
tools/critical_path/move_simulator.py

For each currently-existing cross-session theory duplication (i.e., each
remaining CSTR-style edge), estimate the wall savings of plausible structural
interventions and tier them by feasibility.

Inputs:
    reports/session-duplication-scan.json     # measured residual dups
    tools/lemma_inventory/parse_roots.py      # effective ROOT structure

Output:
    reports/move-simulator.md

The simulator does NOT touch any source file. It reports a ranked list of
candidate structural moves with their predicted wall delta, so the operator
can decide which to actually attempt next.

Feasibility tiers
-----------------
  Tier 1 SWAP-VIABLE — Promoting `origin` to `+` parent is wall-positive
                       (origin's heap is bigger than the current `+` parent,
                       so the trade puts the bigger thing into heap-merge).
  Tier 2 SWAP-NEUTRAL — Margin is small (<200s); needs empirical check.
  Tier 3 SWAP-REJECT — Origin smaller than current parent; swap would just
                       move CSTR onto current parent and likely regress.
                       These require multi-edge work, SPLIT/MERGE, or
                       Isabelle-level changes (multi-parent `+`).
  MERGE — origin session is near-empty (its own_total < 10% of consumer's
          own_total); collapse it into consumer.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SCAN_JSON = REPO / "reports" / "session-duplication-scan.json"
OUT_MD = REPO / "reports" / "move-simulator.md"

sys.path.insert(0, str(REPO / "tools" / "lemma_inventory"))
import parse_roots  # noqa: E402

# Apply the empirically-validated swaps to the in-memory ROOT structure so
# we reason about the POST-SWAP state (which is what the dedupe scan reflects).
# The ROOT file itself is still pre-swap — swap exists as
# experiments/cbaserefine-swap-parent.patch and was applied before measurement.
APPLIED_SWAPS = {
    "CBaseRefine":   {"new_parent": "Refine",  "drop_sessions": ["Refine"], "add_sessions": ["CSpec"]},
    "CRefineSyscall": {"new_parent": "CRefine", "drop_sessions": ["CRefine"], "add_sessions": []},
}


def effective_roots():
    data = parse_roots.parse_all_roots(REPO / "verification" / "l4v", arch="ARM")
    sessions = data["sessions"]
    for sess, swap in APPLIED_SWAPS.items():
        if sess not in sessions:
            continue
        sessions[sess]["parent"] = swap["new_parent"]
        sessions[sess]["imported_sessions"] = [
            s for s in sessions[sess]["imported_sessions"]
            if s not in swap["drop_sessions"]
        ] + swap["add_sessions"]
    return sessions


def ancestor_chain(session_name, sessions):
    """`+` parent chain only (heap-merged ancestry)."""
    chain = []
    cur = session_name
    seen = set()
    while cur and cur in sessions and cur not in seen:
        seen.add(cur)
        chain.append(cur)
        cur = sessions[cur]["parent"]
    if cur and cur not in sessions:
        chain.append(cur)  # e.g. HOL / Pure (out-of-tree bases)
    return chain


def build_ancestors(session_name, sessions):
    """Full build-DAG ancestors: must be built before `session_name`. Walks
    both `+` parent and `sessions X` declarations transitively."""
    seen = set()
    stack = [session_name]
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in sessions:
            continue
        seen.add(cur)
        info = sessions[cur]
        if info.get("parent"):
            stack.append(info["parent"])
        for s in info.get("imported_sessions", []):
            stack.append(s)
    seen.discard(session_name)
    return seen


def classify(consumer, origin, cost, own_sizes, sessions):
    """Return (tier, intervention_text, est_net, notes)."""
    cur_parent = sessions.get(consumer, {}).get("parent")
    cur_parent_size = own_sizes.get(cur_parent, 0) if cur_parent else 0
    origin_size = own_sizes.get(origin, 0)

    # Is origin already on consumer's `+` chain? Then this dup is a stale
    # `sessions X` declaration (X is heap-merged via `+` so source-re-exec
    # is unnecessary). Output: cleanup recommendation, 0 wall (no actual
    # re-execution happening since the heap is loaded).
    consumer_chain = ancestor_chain(consumer, sessions)
    if origin in consumer_chain[1:]:  # not consumer itself
        return (
            "STALE-DECL",
            f"Remove `sessions {origin}` (already heap-merged via {' → '.join(consumer_chain)})",
            0,
            "no wall cost; ROOT hygiene only",
        )

    # SWAP feasibility: origin heap > current parent heap?
    margin = origin_size - cur_parent_size
    if margin > 200:
        return (
            "TIER-1-VIABLE",
            f"SWAP: `{consumer} = {origin} + sessions {{{cur_parent}, ...}}`",
            cost - cur_parent_size,
            f"origin {origin_size:.0f}s vs cur_parent {cur_parent_size:.0f}s ({margin:+.0f}s margin)",
        )
    if margin > 0:
        return (
            "TIER-2-NEUTRAL",
            f"SWAP candidate (small margin): `{consumer} = {origin} +`",
            cost - cur_parent_size,
            f"origin only {margin:.0f}s bigger than cur_parent; verify amplification",
        )
    return (
        "TIER-3-REJECT",
        f"SWAP would regress (origin {origin_size:.0f}s < cur_parent {cur_parent_size:.0f}s)",
        0,
        "needs SPLIT / MERGE / multi-edge restructuring",
    )


def main():
    scan = json.loads(SCAN_JSON.read_text())
    own_sizes = {s["session"]: s["own_total_elapsed"] for s in scan["session_summary"]}
    sessions = effective_roots()

    # Build edges from co_appearing data: (consumer, origin) -> cost.
    # The dedupe scan records co_appearing bidirectionally — each shared
    # theory pair contributes to BOTH sessions' co_appearing entries. Three
    # cases for a (session_S, other_X) entry:
    #
    #   (a) other_X is in S's build ancestors → S is DOWNSTREAM, paying the
    #       amplified re-execution cost. This is a real CSTR consumer-pays
    #       edge: include as (consumer=S, origin=other_X).
    #   (b) S is in other_X's build ancestors → REVERSE of (a); the same
    #       pair will (or already did) appear in other_X's row. Skip.
    #   (c) Neither is upstream of the other → S and other_X share content
    #       via a COMMON ANCESTOR they both source-re-execute (e.g., both
    #       `sessions ExecSpec` which has ASpec as `+` parent). This is not
    #       fixable by a single SWAP between S and other_X — it's a
    #       broader "promote shared ancestor to +" pattern. Bucket
    #       separately so it doesn't pollute Tier 1 candidates.
    edges = []
    shared_ancestor_edges = []
    skipped_reverse = 0
    seen_shared = set()  # avoid double-counting case (c) symmetrically
    for s in scan["session_summary"]:
        consumer = s["session"]
        cons_ancestors = build_ancestors(consumer, sessions)
        for origin, cost in s.get("co_appearing_sessions", {}).items():
            if cost < 10:
                continue
            if origin in cons_ancestors:
                edges.append({"consumer": consumer, "origin": origin, "cost": cost})
            elif consumer in build_ancestors(origin, sessions):
                skipped_reverse += 1
            else:
                pair = tuple(sorted([consumer, origin]))
                if pair in seen_shared:
                    continue
                seen_shared.add(pair)
                shared_ancestor_edges.append({
                    "sess_a": consumer, "sess_b": origin, "cost": cost,
                })
    if skipped_reverse:
        print(f"  (filtered {skipped_reverse} reverse-direction edges)")
    if shared_ancestor_edges:
        print(f"  (bucketed {len(shared_ancestor_edges)} shared-ancestor edges)")

    # Classify each edge
    for e in edges:
        tier, intervention, est_net, notes = classify(
            e["consumer"], e["origin"], e["cost"], own_sizes, sessions
        )
        e["tier"] = tier
        e["intervention"] = intervention
        e["est_net"] = est_net
        e["notes"] = notes
        e["cur_parent"] = sessions.get(e["consumer"], {}).get("parent")
        e["cur_parent_size"] = own_sizes.get(e["cur_parent"], 0)
        e["origin_size"] = own_sizes.get(e["origin"], 0)

    edges.sort(key=lambda r: -r["cost"])

    # Tier groupings
    tiers = {
        "TIER-1-VIABLE": [],
        "TIER-2-NEUTRAL": [],
        "TIER-3-REJECT": [],
        "STALE-DECL": [],
    }
    for e in edges:
        tiers[e["tier"]].append(e)

    # Build markdown
    md = []
    md.append("# Move Simulator — Residual CSTR Edges & Structural Interventions\n")
    md.append("_Empirical ranking of cross-session theory duplications still present_")
    md.append("_after the CBaseRefine + CRefineSyscall ROOT swaps._\n")
    md.append("Source: [reports/session-duplication-scan.json](session-duplication-scan.json)")
    md.append("(post-swap `heaps/db-archive/*.db` BLOBs).\n")

    md.append("## Top 20 edges by cost\n")
    md.append("| # | Consumer | Origin (re-exec'd) | Cost (s) | Tier | Intervention |")
    md.append("|---:|---|---|---:|---|---|")
    for i, e in enumerate(edges[:20], 1):
        md.append(
            f"| {i} | `{e['consumer']}` | `{e['origin']}` | {e['cost']:.0f} | "
            f"{e['tier']} | {e['intervention']} |"
        )

    md.append("\n## Tier breakdown\n")
    for tier_name, label in [
        ("TIER-1-VIABLE", "Tier 1 — VIABLE SWAP (next actionable)"),
        ("TIER-2-NEUTRAL", "Tier 2 — NEUTRAL SWAP (small margin, verify empirically)"),
        ("STALE-DECL", "Stale `sessions X` declarations (ROOT hygiene)"),
        ("TIER-3-REJECT", "Tier 3 — SWAP REJECTED (needs SPLIT/MERGE/multi-edge)"),
    ]:
        group = tiers[tier_name]
        total = sum(e["cost"] for e in group)
        md.append(f"### {label}\n")
        md.append(f"_Total cost in this tier: {total:.0f}s ({len(group)} edges)_\n")
        if not group:
            md.append("(none)\n")
            continue
        md.append("| Consumer | Origin | Cost (s) | Notes |")
        md.append("|---|---|---:|---|")
        for e in group:
            md.append(
                f"| `{e['consumer']}` | `{e['origin']}` | {e['cost']:.0f} | {e['notes']} |"
            )
        md.append("")

    # Shared-ancestor pattern: unrelated sessions sharing source-re-executed
    # content via a common ancestor neither has heap-merged.
    md.append("## Shared-ancestor pattern (Target (b) candidates)\n")
    md.append("_Unrelated session pairs sharing source-re-executed content via_")
    md.append("_a common ancestor neither has on its `+` chain. Not fixable by a_")
    md.append("_single SWAP between the pair; fix is to promote the shared_")
    md.append("_ancestor (typically ExecSpec/ASpec/Lib) so it's `+`-merged into_")
    md.append("_all consuming sessions._\n")
    if shared_ancestor_edges:
        shared_ancestor_edges.sort(key=lambda x: -x["cost"])
        md.append("| Session A | Session B | Cost in A (s) | Likely shared ancestor |")
        md.append("|---|---|---:|---|")
        for e in shared_ancestor_edges[:15]:
            anc_a = build_ancestors(e["sess_a"], sessions)
            anc_b = build_ancestors(e["sess_b"], sessions)
            common = sorted(anc_a & anc_b, key=lambda x: -own_sizes.get(x, 0))
            common_str = ", ".join(common[:3]) or "—"
            md.append(f"| `{e['sess_a']}` | `{e['sess_b']}` | {e['cost']:.0f} | {common_str} |")
    else:
        md.append("(none)\n")

    # MERGE candidates: small sessions with mostly-dup content
    md.append("\n## MERGE candidates (near-empty sessions)\n")
    md.append("_A session X whose own mass is small AND mostly duplicated is a candidate_")
    md.append("_for collapse into its consumer C (CRefineSyscall pre-swap was the prototype:_")
    md.append("_100% dup, 1546s aggregate, fixed by the CRefineSyscall = CRefine + swap)._\n")
    md.append("_FEASIBILITY: C must have all of X's `+` ancestors on C's own `+` chain;_")
    md.append("_otherwise moving X's theories loses their heap-merged context._\n")
    # Build child map: for each session, list sessions that have it as `+` parent.
    children_of = defaultdict(list)
    for sname, info in sessions.items():
        if info.get("parent"):
            children_of[info["parent"]].append(sname)

    merge_cands = []
    for s in scan["session_summary"]:
        own = s["own_total_elapsed"]
        dup_pct = s.get("duplicated_pct", 0)
        if own > 0 and own < 200 and dup_pct > 90 and s.get("co_appearing_sessions"):
            sess_name = s["session"]
            top_consumer, _ = max(s["co_appearing_sessions"].items(), key=lambda x: x[1])
            sess_plus_chain = set(ancestor_chain(sess_name, sessions)[1:])
            consumer_plus_chain = set(ancestor_chain(top_consumer, sessions))
            missing = sess_plus_chain - consumer_plus_chain
            # Also check: does sess_name have its own children (other sessions
            # whose `+` parent points to it)? If so, merging would orphan them
            # unless we also update each child's ROOT — multi-step refactor.
            kids = children_of.get(sess_name, [])
            base_feasible = not missing and not kids
            merge_cands.append((sess_name, own, dup_pct, top_consumer,
                                base_feasible, missing, kids))
    merge_cands.sort(key=lambda x: (not x[4], -x[1]))
    if merge_cands:
        md.append("| Session | Own (s) | Dup % | Top consumer | Feasible? | Notes |")
        md.append("|---|---:|---:|---|:---:|---|")
        for sess, own, dup, cons, feasible, missing, kids in merge_cands:
            notes = []
            if missing:
                notes.append(f"`{cons}` `+` chain missing {sorted(missing)}")
            if kids:
                notes.append(f"{sess} is `+` parent of {kids} (multi-step refactor)")
            if not notes:
                notes.append(f"merge into `{cons}` viable as-is")
            mark = "✓" if feasible else "✗"
            md.append(f"| `{sess}` | {own:.0f} | {dup:.0f}% | `{cons}` | {mark} | {'; '.join(notes)} |")
    else:
        md.append("(none qualifying)\n")

    md.append("\n## Methodology caveats\n")
    md.append("- **Wall savings estimates ignore amplification offsets.** A SWAP")
    md.append("  promotes one session to `+` parent and demotes another to `sessions X` —")
    md.append("  the demoted side's content then source-re-executes in the consumer's")
    md.append("  enlarged ML state, costing some wall. We report `cost - cur_parent_own_total`")
    md.append("  as a first-order net; the empirical CBaseRefine swap showed actual gain")
    md.append("  was 1.3× the first-order prediction (better than naive estimate due to GC")
    md.append("  reduction). So Tier 1 estimates are conservative.")
    md.append("- **Single-session-pair view.** A residual edge may be fixable only if")
    md.append("  multiple edges change together. The simulator scores each in isolation.")
    md.append("- **Tier 3 ≠ unsolvable.** It just means single-edge SWAP is wrong; the")
    md.append("  fix may involve creating a new intermediate session (SPLIT), pruning")
    md.append("  imports (so `sessions X` actually pulls less content), or — as a")
    md.append("  long-term path — Isabelle gaining multi-parent `+` support.")

    OUT_MD.write_text("\n".join(md) + "\n")
    print(f"Wrote {OUT_MD}")
    print(f"\nSummary:")
    print(f"  Total edges with cost ≥ 10s: {len(edges)}  (cumulative {sum(e['cost'] for e in edges):.0f}s)")
    for tier_name in ("TIER-1-VIABLE", "TIER-2-NEUTRAL", "STALE-DECL", "TIER-3-REJECT"):
        g = tiers[tier_name]
        print(f"  {tier_name:20s}: {len(g):3d} edges  {sum(e['cost'] for e in g):6.0f}s")
    if edges:
        print(f"\n  Top edge: {edges[0]['consumer']} ← {edges[0]['origin']} ({edges[0]['cost']:.0f}s) [{edges[0]['tier']}]")


if __name__ == "__main__":
    main()
