#!/usr/bin/env python3
"""
tools/critical_path/theory_axis_2d.py

Theory-level 2D classification for the seL4 verification:

  Dimension 1 — SOURCE PILLAR
      Where the theory's .thy file physically lives. Determines which
      pillar's source change directly invalidates this theory.

      Values: spec | haskell | c | proof | lib
        spec    = /spec/abstract/, /spec/cspec/ (Isabelle wrappers), /spec/
        haskell = /spec/haskell/ (source), /spec/design/ (generated)
        c       = /spec/cspec/c/build/ (generated), /seL4/src/
        proof   = /proof/ (any subdir, including invariant-abstract)
        lib     = /lib/, /tools/, Isabelle distribution

  Dimension 2 — DEP PILLAR SET
      The set of pillars touched by the theory's transitive `imports`
      closure, excluding the universal `lib` (which everyone depends on).
      Determines which pillars' changes transitively invalidate this theory.

      The 5 meaningful classes (re-named to user's clearer convention):
        needs-H-AND-C    closure touches BOTH haskell and c → cross-cut
        needs-H-NOT-C    closure touches haskell, NOT c    → Haskell-side
        needs-C-NOT-H    closure touches c, NOT haskell    → C-side
        needs-spec-only  closure touches spec only, not h/c
        lib-only         closure ⊆ {lib, proof}             → self-contained

The 2D matrix exposes both directions of (mis)alignment:

  - source=proof × dep=needs-H-AND-C → genuine cross-cut work (must pay CSTR)
  - source=proof × dep=needs-H-NOT-C → Haskell-side proof; should live in
    Refine/Access/InfoFlow/DRefine
  - source=proof × dep=needs-C-NOT-H → C-side proof (would be a surprise
    in seL4 since every C proof is ccorres)
  - source=proof × dep=lib-only → minimal helper, no pillar dep

Theories in BLOB but missing from the theory-DAG (Simpl-VCG, HOL, Pure,
UmmTypes — Isabelle distribution sessions) are classified by namespace
heuristic: dep_set = lib-only, source = lib.
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
OUT_MD = REPO / "reports" / "theory-axis-2d.md"
OUT_JSON = REPO / "reports" / "theory-axis-2d.json"


# ─── Source pillar from path ─────────────────────────────────────────────

PATH_PILLARS = [
    ("c",       "/spec/cspec/c/build/"),
    ("c",       "verification/seL4/src/"),
    ("c",       "verification/seL4/include/"),
    ("spec",    "/spec/cspec/"),            # Isabelle-side cspec wrappers (rest)
    ("haskell", "/spec/design/"),
    ("haskell", "/spec/haskell/"),
    ("spec",    "/spec/abstract/"),
    ("spec",    "/spec/"),                  # other specs (Bisim_Spec etc.)
    ("proof",   "/proof/"),
    ("lib",     "/lib/"),
    ("lib",     "/tools/"),
    ("lib",     "verification/isabelle/"),
]

# For BLOB theories not in DAG: source-pillar fallback by namespace prefix.
# HOL/Pure/Simpl-VCG live in Isabelle distribution; classify as lib.
NAMESPACE_FALLBACK = {
    "HOL":         "lib",
    "Pure":        "lib",
    "Simpl-VCG":   "lib",
    "UmmTypes":    "lib",
    "Word_Lib":    "lib",
    "Lib":         "lib",
    "Monads":      "lib",
    "Basics":      "lib",
    "ML_Utils":    "lib",
    "Eisbach_Tools": "lib",
    "SepTactics":  "lib",
    "Sep_Algebra": "lib",
    "AutoCorres":  "lib",
    "AsmRefine":   "lib",
    "CorresK":     "lib",
    "ASpec":       "spec",
    "ExecSpec":    "haskell",
    "CSpec":       "spec",  # Isabelle wrappers
    "CKernel":     "c",
    "CParser":     "lib",
    "AInvs":       "proof",
    "Refine":      "proof",
    "BaseRefine":  "proof",
    "Access":      "proof",
    "InfoFlow":    "proof",
    "InfoFlowCBase": "proof",
    "InfoFlowC":   "proof",
    "CBaseRefine": "proof",
    "CRefine":     "proof",
    "CRefineSyscall": "proof",
    "DSpec":       "spec",
    "DRefine":     "proof",
    "DBaseRefine": "proof",
    "DPolicy":     "proof",
    "DSpecProofs": "proof",
    "SepDSpec":    "proof",
    "Bisim":       "proof",
    "RefineOrphanage": "proof",
    "SimplExport": "proof",
    "SimplExportAndRefine": "proof",
    # CAMKES — component framework above seL4, peripheral to kernel verif.
    # All bucketed as lib (they're upstream-of-nothing for kernel proofs).
    "CamkesGlueSpec":   "lib",
    "CamkesAdlSpec":    "lib",
    "CamkesGlueProofs": "lib",
    "CamkesCdlRefine":  "lib",
    "SysInit":          "lib",
    "SysInitExamples":  "lib",
    # Isabelle distribution sub-libraries
    "HOL-Library":      "lib",
    "HOL-Statespace":   "lib",
    "HOL-Eisbach":      "lib",
    "HOL-Combinatorics": "lib",
    "Complex_Main":     "lib",
    "Main":             "lib",
    "ML_Bootstrap":     "lib",
    "Tools":            "lib",
    "Docs":             "lib",
}


def source_pillar(fqn, path=None):
    if path:
        for p, key in PATH_PILLARS:
            if key in path:
                return p
    prefix = fqn.split(".", 1)[0] if "." in fqn else fqn
    return NAMESPACE_FALLBACK.get(prefix, "?")


# ─── Dep pillar set from import closure ──────────────────────────────────

def transitive_closure(start, imports_of):
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


def dep_pillar_set(closure_theories, theory_source_pillar):
    """Set of pillars touched by closure (excluding the starting theory)."""
    pillars = set()
    for t in closure_theories:
        p = theory_source_pillar.get(t, "?")
        if p != "?":
            pillars.add(p)
    return frozenset(pillars)


def classify_dep(dep_set):
    """Re-named per user convention 2026-05-14: distinguish ONLY on
    haskell-vs-c since spec/lib are universal foundations."""
    non_universal = dep_set - {"lib"}
    has_h = "haskell" in non_universal
    has_c = "c" in non_universal
    has_s = "spec" in non_universal
    if has_h and has_c:
        return "needs-H-AND-C"
    if has_h:
        return "needs-H-NOT-C"
    if has_c:
        return "needs-C-NOT-H"
    if has_s:
        return "needs-spec-only"
    return "lib-only"


# ─── Loaders ─────────────────────────────────────────────────────────────

REC_RE = re.compile(
    r"name=([^\x06]+)\x06elapsed=([0-9.]+)\x06cpu=([0-9.]+)\x06gc=([0-9.]+)"
)

def load_blobs():
    """session -> {theory_fqn -> elapsed_s}"""
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


def load_dag():
    raw = json.loads(DAG_PATH.read_text())
    nodes = {n["name"]: n for n in raw["nodes"]}
    imports_of = defaultdict(set)
    for src, dst in raw["edges"]:
        imports_of[src].add(dst)
    return nodes, dict(imports_of)


# ─── Main ────────────────────────────────────────────────────────────────

def main():
    print("Loading DAG and BLOBs...")
    dag_nodes, imports_of = load_dag()
    blobs = load_blobs()

    # Collect ALL theories that ever appear: DAG nodes ∪ BLOB theories
    all_theories = set(dag_nodes.keys())
    for sess, recs in blobs.items():
        all_theories.update(recs.keys())
    print(f"Theory universe: {len(all_theories)} unique theories")
    print(f"  in DAG: {len(dag_nodes)}")
    print(f"  in BLOB (canonical ARM build): {sum(1 for _ in {t for s in blobs.values() for t in s})}")
    print(f"  in BLOB but NOT in DAG: {len(all_theories) - len(set(dag_nodes) & all_theories)}")

    # === Dimension 1: source pillar for every theory ===
    print("\nAssigning source pillars...")
    theory_source = {}
    for t in all_theories:
        path = dag_nodes.get(t, {}).get("path")
        theory_source[t] = source_pillar(t, path)
    src_dist = Counter(theory_source.values())
    print(f"  Source pillar distribution:")
    for p, n in src_dist.most_common():
        print(f"    {p:10s}: {n}")

    # === Dimension 2: dep pillar set for every theory ===
    print("\nComputing closures...")
    theory_dep_set = {}
    theory_dep_class = {}
    for t in all_theories:
        if t in imports_of or t in dag_nodes:
            cl = transitive_closure(t, imports_of)
            cl.discard(t)
            ds = dep_pillar_set(cl, theory_source)
        else:
            # No DAG record for this theory; fall back to lib-only assumption
            # for Isabelle distribution theories (HOL/Pure/Simpl-VCG etc.).
            ds = frozenset({"lib"})
        theory_dep_set[t] = ds
        theory_dep_class[t] = classify_dep(ds)
    dep_dist = Counter(theory_dep_class.values())
    print(f"  Dep classification distribution:")
    for c, n in dep_dist.most_common():
        print(f"    {c:20s}: {n}")

    # === Origin wall per theory (for weighting by wall not just count) ===
    theory_wall = {}
    for sess, recs in blobs.items():
        for thy, w in recs.items():
            prefix = thy.split(".", 1)[0] if "." in thy else thy
            if prefix == sess:
                if thy not in theory_wall or w < theory_wall[thy]:
                    theory_wall[thy] = w
    # For theories not in their nominal origin session's BLOB, pick min from any BLOB
    for t in all_theories:
        if t in theory_wall:
            continue
        for sess, recs in blobs.items():
            if t in recs:
                if t not in theory_wall or recs[t] < theory_wall[t]:
                    theory_wall[t] = recs[t]

    # === 2D matrix: (source pillar) × (dep class) ===
    matrix = defaultdict(lambda: defaultdict(lambda: {"count": 0, "wall": 0.0, "examples": []}))
    for t in all_theories:
        src = theory_source[t]
        dc = theory_dep_class[t]
        wall = theory_wall.get(t, 0.0)
        cell = matrix[src][dc]
        cell["count"] += 1
        cell["wall"] += wall
        if len(cell["examples"]) < 4:
            cell["examples"].append((t, wall))

    # === Build markdown ===
    md = []
    md.append("# Theory Axis Analysis — 2D Classification\n")
    md.append("_Every theory in the canonical ARM build is labeled along two axes:_\n")
    md.append("  - **source pillar** = which pillar's *files* invalidate this theory directly")
    md.append("    (`spec` / `haskell` / `c` / `proof` / `lib`)")
    md.append("  - **dep pillar class** = which pillars' *changes* transitively reach this theory")
    md.append("    (`needs-H-AND-C` / `needs-H-NOT-C` / `needs-C-NOT-H` /")
    md.append("    `needs-spec-only` / `lib-only`)")
    md.append("")
    md.append("`spec` and `lib` are foundational — almost every proof imports them —")
    md.append("so the dep classification *distinguishes* only on the haskell-vs-c axis.")
    md.append("A theory labeled `needs-H-NOT-C` still typically imports spec and lib,")
    md.append("but does NOT import any c-pillar content.\n")

    md.append("## Theory universe (canonical ARM build)\n")
    n_in_dag = sum(1 for t in all_theories if t in dag_nodes)
    n_blob = len({t for s in blobs.values() for t in s})
    md.append(f"- {len(all_theories)} unique theories total")
    md.append(f"- {n_in_dag} in theory-DAG (have file path + import edges)")
    md.append(f"- {n_blob} actually executed in some BLOB during the canonical build")
    md.append(f"- {len(all_theories) - n_in_dag} have no DAG record (mostly Isabelle distribution: HOL, Pure, Simpl-VCG, UmmTypes)")
    md.append("")

    md.append("## Source pillar distribution\n")
    md.append("| Source pillar | Theory count | Sum wall (s) | Description |")
    md.append("|---|---:|---:|---|")
    src_labels = {
        "spec":    "abstract spec + cspec Isabelle wrappers + other specs",
        "haskell": "spec/design/ generated from spec/haskell/",
        "c":       "spec/cspec/c/build/ generated from seL4/src/",
        "proof":   "all proof/* subdirs (refine/crefine/access/infoflow/invariant-abstract/...)",
        "lib":     "lib/ + tools/ + Isabelle distribution (HOL/Pure/Simpl-VCG/...)",
        "?":       "unclassified (should be 0)",
    }
    for p in ("spec", "haskell", "c", "proof", "lib", "?"):
        total_count = src_dist.get(p, 0)
        if total_count == 0: continue
        total_wall = sum(theory_wall.get(t, 0) for t in all_theories if theory_source[t] == p)
        md.append(f"| `{p}` | {total_count} | {total_wall:.0f} | {src_labels[p]} |")
    md.append("")

    md.append("## 2D matrix: source × dep class\n")
    md.append("_Counts shown as `count theories / wall_s`. Empty cells = 0._\n")
    classes = ["needs-H-AND-C", "needs-H-NOT-C", "needs-C-NOT-H", "needs-spec-only", "lib-only"]
    md.append("| Source ↓ \\ Dep → | " + " | ".join(classes) + " |")
    md.append("|" + "---|" * (len(classes) + 1))
    for src in ("spec", "haskell", "c", "proof", "lib"):
        cells = [f"`{src}`"]
        for dc in classes:
            d = matrix[src].get(dc)
            if not d or d["count"] == 0:
                cells.append("—")
            else:
                cells.append(f"{d['count']} / {d['wall']:.0f}s")
        md.append("| " + " | ".join(cells) + " |")
    md.append("")

    # === Per-cell sample examples for the interesting cells ===
    md.append("## Sample theories per cell (selected)\n")
    interesting_cells = [
        ("proof", "needs-H-AND-C", "the cross-cut refinement work — irreducible CSTR"),
        ("proof", "needs-H-NOT-C", "Haskell-side proofs; belong in Refine/Access/InfoFlow"),
        ("proof", "needs-C-NOT-H", "if any — would be surprise (seL4's C proofs are ccorres)"),
        ("proof", "needs-spec-only", "proof depends only on abstract spec, not h or c"),
        ("proof", "lib-only", "self-contained helper theories"),
        ("spec",  "needs-H-AND-C", "spec material that transitively needs c — odd"),
        ("c",     "needs-H-NOT-C", "c-source theories transitively needing haskell — odd"),
    ]
    for src, dc, note in interesting_cells:
        cell = matrix[src].get(dc)
        if not cell or cell["count"] == 0:
            md.append(f"### `source={src}` × `dep={dc}` — {note}\n")
            md.append("_(empty)_\n")
            continue
        md.append(f"### `source={src}` × `dep={dc}` — {note}\n")
        md.append(f"_{cell['count']} theories, {cell['wall']:.0f}s wall_\n")
        for fqn, w in cell["examples"]:
            md.append(f"- `{fqn}` ({w:.1f}s)")
        md.append("")

    # === Misalignments: cells that suggest a session-move opportunity ===
    md.append("## Misalignment candidates\n")
    md.append("Theories whose **source dir** sits in a cross-cutting session")
    md.append("but whose **dep class** is `needs-H-NOT-C` — these are Haskell-side")
    md.append("proofs that ended up living in cross-cut directories. They could")
    md.append("be moved to a Haskell-axis session without paying CSTR amplification.\n")
    sess_of_node = {n: dag_nodes[n].get("session", "?") for n in dag_nodes}
    cross_cut_sessions = {"CBaseRefine", "CRefine", "CRefineSyscall", "InfoFlowC",
                          "AutoCorresCRefine", "SimplExportAndRefine"}
    misaligned = []
    for t in all_theories:
        if theory_source[t] != "proof":
            continue
        if theory_dep_class[t] != "needs-H-NOT-C":
            continue
        sess = sess_of_node.get(t)
        if sess in cross_cut_sessions:
            misaligned.append((t, sess, theory_wall.get(t, 0),
                               dag_nodes.get(t, {}).get("path", "")))
    misaligned.sort(key=lambda x: -x[2])
    if misaligned:
        md.append(f"_Found {len(misaligned)} candidates totaling "
                  f"{sum(w for _, _, w, _ in misaligned):.0f}s wall:_\n")
        md.append("| Theory | Current session | Wall (s) | File |")
        md.append("|---|---|---:|---|")
        for fqn, sess, wall, path in misaligned:
            short_path = path.replace(str(REPO) + "/", "") if path else "—"
            md.append(f"| `{fqn}` | `{sess}` | {wall:.1f} | `{short_path}` |")
    else:
        md.append("_(none found)_")
    md.append("")

    md.append("## Methodology notes\n")
    md.append("- **Per-arch view**: theory-DAG was generated with `L4V_ARCH=ARM`,")
    md.append("  so the per-pillar counts reflect what ARM build pulls in. Other")
    md.append("  archs (X64/ARM_HYP/AARCH64/RISCV64) have their own arch-specific")
    md.append("  variants that would multiply some counts ~5×.")
    md.append("- **DAG completeness**: theories not in DAG (HOL/Pure/Simpl-VCG/")
    md.append("  UmmTypes — ~140 of the 846 BLOB total) are assigned `source=lib,")
    md.append("  dep=lib-only` by namespace fallback. Their actual imports are")
    md.append("  inside the Isabelle distribution and not relevant for proof-axis")
    md.append("  restructuring.")
    md.append("- **Wall accounting**: `theory_wall` is the elapsed time from the")
    md.append("  theory's origin-session BLOB (the lighter, non-CSTR-amplified")
    md.append("  measurement). The CSTR-amplified cost in downstream sessions is")
    md.append("  separate and tracked in [session-duplication-scan](session-duplication-scan.md).")

    OUT_MD.write_text("\n".join(md) + "\n")

    # JSON
    OUT_JSON.write_text(json.dumps({
        "n_theories": len(all_theories),
        "source_pillar_counts": dict(src_dist),
        "dep_class_counts": dict(dep_dist),
        "matrix": {
            src: {dc: {"count": d["count"], "wall_s": round(d["wall"], 1)}
                  for dc, d in src_data.items()}
            for src, src_data in matrix.items()
        },
        "misalignment_candidates": [
            {"theory": t, "session": s, "wall_s": round(w, 1), "path": p}
            for t, s, w, p in misaligned
        ],
        "per_theory": {
            t: {
                "source_pillar": theory_source[t],
                "dep_class": theory_dep_class[t],
                "dep_pillar_set": sorted(theory_dep_set[t]),
                "wall_s": round(theory_wall.get(t, 0), 1),
            }
            for t in sorted(all_theories)
        },
    }, indent=2))

    print(f"\nWrote:\n  {OUT_MD}\n  {OUT_JSON}")
    print(f"\nTotal theories classified: {len(all_theories)}")
    print(f"Misalignment candidates: {len(misaligned)}")


if __name__ == "__main__":
    main()
