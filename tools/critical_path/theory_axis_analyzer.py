#!/usr/bin/env python3
"""
tools/critical_path/theory_axis_analyzer.py

THEORY-LEVEL axis-dependency analysis. For each .thy file in the
seL4 verification:

  1. Source pillar = which of the 4 sel4 pillars its file lives in
     (spec / haskell / c / proof, plus the universal `lib` bucket).
  2. Closure pillars = set of pillars transitively reached via imports.

The CSTR optimization question — "can we split/merge sessions to align
with the 4-pillar architecture?" — reduces at this granularity to:

  - Are there proof theories whose CLOSURE stays inside a single non-proof
    pillar plus lib? Those could be moved to a session that `+`-merges
    only that pillar's content, paying NO CSTR.
  - Are there proof theories whose closure spans MULTIPLE non-proof
    pillars (e.g., haskell + c)? Those are intrinsically cross-cutting;
    they pay CSTR no matter where you put them.

The trade-off framing (per user 2026-05-14):
  - One huge session with everything = no CSTR, but every change rebuilds
    everything (unrealistic).
  - Many tiny sessions = great change-locality, max CSTR.
  - Optimum = sessions whose theories share an axis-closure profile.

Output:
  reports/theory-axis-analysis.md           — narrative + tables
  reports/theory-axis-analysis.json         — per-theory data for downstream tools
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
DAG_PATH = REPO / "reports" / "theory-dag.json"
DB_DIR = REPO / "heaps" / "db-archive"
OUT_MD = REPO / "reports" / "theory-axis-analysis.md"
OUT_JSON = REPO / "reports" / "theory-axis-analysis.json"


# ─── Pillar assignment from source path ──────────────────────────────────
#
# The 4 sel4 pillars per the SOSP'09 architecture + Phase A's manifests:
#   spec    — abstract specification (spec/abstract/) AND its invariant
#             proofs (proof/invariant-abstract/). When the abstract spec
#             changes, both rebuild.
#   haskell — executable spec (spec/haskell/ source, spec/design/
#             generated). Change to Haskell ⇒ spec/design/ regenerates.
#   c       — C source (seL4/src/) and its parsed Isabelle form
#             (spec/cspec/c/build/, spec/cspec/). C change ⇒ regen.
#   proof   — refinement / info-flow / drefine proofs (proof/refine/,
#             proof/crefine/, proof/infoflow/, proof/drefine/, etc.).
#
# Plus a `lib` universal bucket for foundational libraries (lib/, tools/)
# that everyone depends on; their changes are rare enough that we don't
# count them as a pillar.
PATH_PILLARS = [
    # Specific paths first (most-specific match wins)
    ("c",       "/spec/cspec/c/build/"),     # C parser output
    ("c",       "/spec/cspec/"),             # rest of CSpec (parser interface)
    ("c",       "verification/seL4/src/"),
    ("haskell", "/spec/design/"),            # generated FROM haskell
    ("haskell", "/spec/haskell/"),           # haskell source itself
    ("spec",    "/spec/abstract/"),
    ("spec",    "/proof/invariant-abstract/"),  # proofs ABOUT spec — change-locality dual-coupled
    ("spec",    "/spec/"),                   # other specs (Bisim_Spec, etc.)
    ("proof",   "/proof/"),                  # all other proof subdirs
    ("lib",     "/lib/"),
    ("lib",     "/tools/"),
    ("lib",     "verification/isabelle/"),
]


def pillar_of_path(path):
    for pillar, key in PATH_PILLARS:
        if key in path:
            return pillar
    return "?"


# ─── DAG + BLOB loaders ──────────────────────────────────────────────────

def load_dag():
    raw = json.loads(DAG_PATH.read_text())
    imports_of = defaultdict(set)
    for src, dst in raw["edges"]:
        imports_of[src].add(dst)
    nodes = {n["name"]: n for n in raw["nodes"]}
    return nodes, dict(imports_of)


REC_RE = re.compile(
    r"name=([^\x06]+)\x06elapsed=([0-9.]+)\x06cpu=([0-9.]+)\x06gc=([0-9.]+)"
)

def load_blobs():
    """session -> {theory -> elapsed}"""
    data = {}
    for db in sorted(DB_DIR.glob("*.db")):
        con = sqlite3.connect(str(db))
        name, blob = con.execute(
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
        data[name] = {n: float(e) for n, e, _, _ in REC_RE.findall(text)}
    return data


# ─── Axis closure ────────────────────────────────────────────────────────

def transitive_closure(start, imports_of):
    """Set of all theory FQNs reachable from `start` (including start)."""
    visited = set()
    stack = [start]
    while stack:
        cur = stack.pop()
        if cur in visited:
            continue
        visited.add(cur)
        for imp in imports_of.get(cur, ()):
            if imp not in visited:
                stack.append(imp)
    return visited


def closure_pillars(closure, theory_pillar, exclude_self=None):
    """Return the set of pillars touched by closure."""
    pillars = set()
    for t in closure:
        if t == exclude_self:
            continue
        p = theory_pillar.get(t, "?")
        if p != "?":
            pillars.add(p)
    return frozenset(pillars)


# ─── Profile classification ──────────────────────────────────────────────

def classify_proof_theory(closure_pillars_set):
    """For a theory whose SOURCE is proof-axis, classify its closure
    by which OTHER axes it requires."""
    non_lib = closure_pillars_set - {"lib"}
    has_haskell = "haskell" in non_lib
    has_c = "c" in non_lib
    has_spec = "spec" in non_lib
    if has_haskell and has_c:
        return "cross-cut-HC"   # the hard case: needs both
    if has_haskell and not has_c:
        return "haskell-axis"
    if has_c and not has_haskell:
        return "c-axis"
    if has_spec and not has_haskell and not has_c:
        return "spec-only"
    return "minimal"


# ─── Main analysis ───────────────────────────────────────────────────────

def main():
    print("Loading theory DAG and BLOBs...")
    nodes, imports_of = load_dag()
    blobs = load_blobs()

    # Theory → source pillar
    theory_pillar = {}
    for fqn, n in nodes.items():
        theory_pillar[fqn] = pillar_of_path(n.get("path", ""))

    # For every theory in the DAG, compute closure pillars
    print(f"Computing closures for {len(nodes)} theories...")
    theory_closure_pillars = {}
    for fqn in nodes:
        cl = transitive_closure(fqn, imports_of)
        theory_closure_pillars[fqn] = closure_pillars(cl, theory_pillar,
                                                     exclude_self=fqn)

    # Classification per proof theory
    proof_theories = [fqn for fqn, p in theory_pillar.items() if p == "proof"]
    classify_counts = Counter()
    classify_examples = defaultdict(list)
    classify_walls = defaultdict(float)

    # Origin walls (theory's elapsed in its origin session)
    theory_origin_wall = {}
    for sess, recs in blobs.items():
        for thy, wall in recs.items():
            prefix = thy.split(".", 1)[0] if "." in thy else thy
            if prefix == sess and thy not in theory_origin_wall:
                theory_origin_wall[thy] = wall

    for fqn in proof_theories:
        cl = classify_proof_theory(theory_closure_pillars[fqn])
        classify_counts[cl] += 1
        wall = theory_origin_wall.get(fqn, 0)
        classify_walls[cl] += wall
        if len(classify_examples[cl]) < 5:
            classify_examples[cl].append((fqn, wall))

    print("\nProof-axis theory classification:")
    for cl, n in classify_counts.most_common():
        print(f"  {cl:18s}: {n:4d} theories, total wall = {classify_walls[cl]:.0f}s")

    # Per-session distribution: for each session, what's the axis classification
    # of its OWN theories (those in this session's nominal namespace, that
    # also live in proof axis)?
    sessions_of_interest = [
        "CBaseRefine", "CRefine", "CRefineSyscall", "InfoFlowCBase", "InfoFlowC",
        "InfoFlow", "Refine", "AInvs", "Access", "BaseRefine", "ASpec", "DPolicy",
        "SepDSpec", "DRefine", "DBaseRefine", "DSpec", "Bisim", "RefineOrphanage",
    ]

    per_session = defaultdict(lambda: defaultdict(lambda: {"count": 0, "wall": 0.0, "examples": []}))
    for sess in sessions_of_interest:
        # Use theories whose DAG node is labeled with this session
        for fqn, n in nodes.items():
            if n.get("session") != sess:
                continue
            wall = theory_origin_wall.get(fqn, 0)
            source = theory_pillar.get(fqn, "?")
            if source != "proof":
                continue
            cl = classify_proof_theory(theory_closure_pillars[fqn])
            bucket = per_session[sess][cl]
            bucket["count"] += 1
            bucket["wall"] += wall
            if len(bucket["examples"]) < 3:
                bucket["examples"].append((fqn, wall))

    # Build markdown
    md = []
    md.append("# Theory-Axis Analysis — Are Sessions Pillar-Alignable?\n")
    md.append("_For each PROOF-axis theory in the seL4 verification, compute its_")
    md.append("_transitive import closure and report which non-proof pillars it_")
    md.append("_touches (spec / haskell / c). The 4-pillar question per user_")
    md.append("_2026-05-14 reduces here to: how many proof theories are_")
    md.append("_single-axis (would survive in a pillar-aligned session) vs._")
    md.append("_cross-cutting (genuinely need both haskell and c heaps, so_")
    md.append("_must live in a CSTR-paying session no matter how we split)?_\n")
    md.append("Pillar source assignment from file path:")
    for p, key in PATH_PILLARS:
        md.append(f"- `{key}` → **{p}**")
    md.append("")

    md.append("## Overall classification of proof-axis theories\n")
    md.append("| Class | Count | Total wall (s) | Description |")
    md.append("|---|---:|---:|---|")
    labels = {
        "cross-cut-HC": "needs BOTH haskell + c content (forced CSTR)",
        "c-axis":       "needs c content only (could `+ CSpec` chain)",
        "haskell-axis": "needs haskell content only (could `+ Refine` chain)",
        "spec-only":    "needs spec content only (no haskell, no c)",
        "minimal":      "self-contained / lib only",
    }
    for cl in ("cross-cut-HC", "c-axis", "haskell-axis", "spec-only", "minimal"):
        md.append(f"| {cl} | {classify_counts[cl]} | {classify_walls[cl]:.0f} | {labels[cl]} |")
    md.append("")

    for cl in ("c-axis", "haskell-axis", "spec-only", "cross-cut-HC"):
        if classify_examples[cl]:
            md.append(f"### Examples of `{cl}`\n")
            for fqn, w in classify_examples[cl]:
                md.append(f"- `{fqn}` (wall {w:.1f}s)")
            md.append("")

    md.append("## Per-session distribution\n")
    md.append("For each major session, the axis-classification of its OWN")
    md.append("theories (those whose `.thy` files are in the session's nominal")
    md.append("directory). A session whose own theories all share ONE")
    md.append("classification is a strong candidate for pillar-alignment;")
    md.append("a session mixing classes is a candidate for SPLIT.\n")

    for sess in sessions_of_interest:
        if not per_session.get(sess):
            continue
        md.append(f"### `{sess}`\n")
        total_count = sum(d["count"] for d in per_session[sess].values())
        total_wall = sum(d["wall"] for d in per_session[sess].values())
        if total_count == 0:
            md.append("_(no proof-axis theories in DAG for this session)_\n")
            continue
        md.append(f"_total: {total_count} proof theories, {total_wall:.0f}s wall_\n")
        md.append("| Class | Count | Wall (s) | Sample theories |")
        md.append("|---|---:|---:|---|")
        for cl in ("c-axis", "haskell-axis", "spec-only", "cross-cut-HC", "minimal"):
            d = per_session[sess].get(cl, {"count": 0, "wall": 0.0, "examples": []})
            if d["count"] == 0:
                continue
            examples_str = ", ".join(f"`{f.split('.',1)[1] if '.' in f else f}`"
                                     for f, _ in d["examples"][:3])
            md.append(f"| {cl} | {d['count']} | {d['wall']:.0f} | {examples_str} |")
        md.append("")

    md.append("## Implications\n")
    cross_hc = classify_counts["cross-cut-HC"]
    c_only = classify_counts["c-axis"]
    h_only = classify_counts["haskell-axis"]
    total_proof = sum(classify_counts.values())
    if total_proof:
        md.append(f"- **{cross_hc / total_proof * 100:.0f}%** of proof theories are genuinely cross-cutting")
        md.append(f"  (need both haskell AND c content). These pay CSTR by their")
        md.append(f"  intrinsic nature — they assert relationships BETWEEN haskell")
        md.append(f"  and c representations. No structural change makes them mono-axis.")
        md.append(f"- **{c_only}** proof theories need c content only — these could")
        md.append(f"  in principle live in a C-axis-aligned session that `+`-merges")
        md.append(f"  CSpec without needing Refine.heap, avoiding CSTR.")
        md.append(f"- **{h_only}** proof theories need haskell content only — these")
        md.append(f"  could in principle live in a Haskell-axis session.")
    md.append("")

    md.append("## Methodology caveats\n")
    md.append("- The theory DAG covers `verification/l4v/` thy files; some")
    md.append("  sessions (Simpl-VCG, HOL, Pure) live outside and have 0 DAG")
    md.append("  nodes. Theories in those sessions can't be classified here.")
    md.append("- 'Closure pillars' uses the DAG's `imports` edges. Indirect")
    md.append("  imports via Eisbach methods, `crunch`-generated facts, or ML")
    md.append("  attribute lookups are NOT in the DAG; some 'haskell-axis' or")
    md.append("  'c-axis' classifications may actually be cross-cutting at runtime.")
    md.append("- Theories without a wall measurement (not in their origin's BLOB)")
    md.append("  show 0s; they still count by theory-count but not by wall.")

    OUT_MD.write_text("\n".join(md) + "\n")

    # JSON for downstream tools
    data = {
        "theory_count": len(nodes),
        "proof_theory_count": len(proof_theories),
        "classification_counts": dict(classify_counts),
        "classification_walls_s": {k: round(v, 1) for k, v in classify_walls.items()},
        "per_theory": {
            fqn: {
                "pillar": theory_pillar.get(fqn, "?"),
                "closure_pillars": sorted(theory_closure_pillars.get(fqn, set())),
                "wall_s": round(theory_origin_wall.get(fqn, 0), 1),
            }
            for fqn in nodes
        },
    }
    OUT_JSON.write_text(json.dumps(data, indent=2))
    print(f"\nWrote:\n  {OUT_MD}\n  {OUT_JSON}")


if __name__ == "__main__":
    main()
