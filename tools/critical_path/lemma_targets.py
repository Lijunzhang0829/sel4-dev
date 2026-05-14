#!/usr/bin/env python3
"""
tools/critical_path/lemma_targets.py

Rank individual LEMMAS across the proof codebase by their optimization
potential — bridging Phase A (cost data per theory) with Phase C (lemma-
level rewrites).

For each top-N theory by post-swap wall, parse the .thy file via
proof_parser.parse_proofs(), then rank its lemmas/sub-proofs by:

  combined_score = log(1 + theory_wall_s) * size_lines * pressure_weight

where pressure_weight ∈ {high: 3, medium: 1, low: 0.3}. This biases
toward:
  - lemmas in expensive theories (theory_wall acts as a multiplier)
  - large lemmas (more decomposition surface, more sledgehammer leverage)
  - high search pressure (the user's principle: "give precise direction
    to reduce search path is the root of lemma speedup")

For each top lemma, suggest an optimization PATH (sledgehammer / Isar
decompose / hint addition) based on its tactic shape, applying the
decision tree from the patched proof-timing.sh phase C plan.

Inputs:
  reports/session-duplication-scan.json  (per-session own_total)
  heaps/db-archive/*.db                  (per-theory wall)
  verification/l4v/proof/**/*.thy        (lemma source)

Output:
  reports/lemma-optimization-targets.md
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
DB_DIR = REPO / "heaps" / "db-archive"
L4V_PROOF = REPO / "verification" / "l4v" / "proof"
L4V_ARCH = os.environ.get("L4V_ARCH", "ARM")
OUT_MD = REPO / "reports" / "lemma-optimization-targets.md"
PARSER_DIR = REPO / ".claude" / "skills" / "isabelle_prover" / "scripts-container"

sys.path.insert(0, str(PARSER_DIR))
from proof_parser import parse_proofs  # noqa: E402

REC_RE = re.compile(
    r"name=([^\x06]+)\x06elapsed=([0-9.]+)\x06cpu=([0-9.]+)\x06gc=([0-9.]+)"
)


def load_theory_walls():
    """Map theory_fqn -> elapsed_in_origin_session.

    `origin session` = the session whose namespace prefix matches the theory's
    FQN. This is the wall we want, because it represents the theory's
    INTRINSIC cost (before any CSTR amplification). For a theory that appears
    in multiple sessions, we use the min (closest to the "honest" cost).
    """
    by_fqn = defaultdict(list)  # fqn -> [(elapsed, session), ...]
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
        for n, e, _, _ in REC_RE.findall(text):
            by_fqn[n].append((float(e), name))
    walls = {}
    for fqn, entries in by_fqn.items():
        prefix = fqn.split(".", 1)[0] if "." in fqn else fqn
        matching = [(e, s) for e, s in entries if s == prefix]
        if matching:
            walls[fqn] = matching[0][0]
        else:
            walls[fqn] = min(e for e, _ in entries)
    return walls


def map_theory_to_file(fqn):
    """Map a theory FQN to its .thy file path (ARM arch). Returns None if
    not found in the standard proof tree."""
    if "." not in fqn:
        return None
    sess, name = fqn.split(".", 1)
    # The session-to-dir mapping is encoded in the ROOT files. Use a simple
    # convention check; fall back to brute glob.
    candidates = [
        L4V_PROOF / "refine" / L4V_ARCH / f"{name}.thy",
        L4V_PROOF / "crefine" / L4V_ARCH / f"{name}.thy",
        L4V_PROOF / "invariant-abstract" / L4V_ARCH / f"{name}.thy",
        L4V_PROOF / "access-control" / L4V_ARCH / f"{name}.thy",
        L4V_PROOF / "infoflow" / L4V_ARCH / f"{name}.thy",
        L4V_PROOF / "refine" / f"{name}.thy",
        L4V_PROOF / "crefine" / f"{name}.thy",
        L4V_PROOF / "invariant-abstract" / f"{name}.thy",
        L4V_PROOF / "access-control" / f"{name}.thy",
        L4V_PROOF / "infoflow" / f"{name}.thy",
    ]
    for c in candidates:
        if c.exists():
            return c
    # Brute-force find as last resort (slow; limit to proof tree)
    matches = list(L4V_PROOF.rglob(f"{name}.thy"))
    matches = [m for m in matches if f"/{L4V_ARCH}/" in str(m)
               or m.parent.name in ("refine", "crefine", "invariant-abstract",
                                    "access-control", "infoflow")]
    return matches[0] if matches else None


def suggest_path(lemma, theory_wall_s):
    """Decision tree for optimization PATH based on tactic shape + cost."""
    top = lemma["top_tactic"]
    pressure = lemma["search_pressure"]
    size = lemma["size"]
    has_subs = bool(lemma.get("is_subproof"))  # only meaningful for sub-proofs

    if theory_wall_s < 20:
        return "DEFER", "theory wall < 20s — fixed-cost overhead dominates; skip"
    if pressure == "high" and top in ("apply", "by"):
        if size <= 5:
            return ("HAMMER",
                    f"short {top} with unhinted search — sledgehammer single-shot"
                    f" to find `by (metis ...)` reconstruction")
        return ("HAMMER+ISAR",
                f"long {top} chain with unhinted search — sledgehammer the slow"
                f" step OR Isar-decompose into named `have` blocks to bound each"
                f" tactic's search space")
    if pressure == "medium":
        return ("HINT-ADD",
                f"hinted search-heavy ({top}) — review whether `add:`/`dest:` set"
                f" is minimal; trim redundant rules to shrink search")
    if top == "proof" and size > 50:
        return ("SUBPROOF-DRILL",
                f"long Isar proof — run scan-slow-proofs to find which inner `have`"
                f" is the bottleneck; address that specifically")
    if top == "by" and pressure == "low":
        return ("LOW-PRIORITY",
                f"already low-pressure `by`; unlikely to yield much")
    return ("INSPECT", f"top={top} pressure={pressure} size={size} — needs case-by-case look")


def main():
    walls = load_theory_walls()

    # Pick top-N theories by wall (excluding pure-ML SimplExport* and
    # spec-derived Kernel_C/Substitute that have no lemmas to optimize)
    excluded_prefixes = ("SimplExportAndRefine.", "SimplExport.")
    excluded_thys = {"CKernel.Kernel_C", "CSpec.Substitute"}
    ranked = sorted(
        ((wall, fqn) for fqn, wall in walls.items()
         if wall >= 30
         and not any(fqn.startswith(p) for p in excluded_prefixes)
         and fqn not in excluded_thys),
        reverse=True
    )[:25]

    print(f"Analyzing top {len(ranked)} theories by origin wall...")

    results = []
    skipped_no_file = []
    for wall, fqn in ranked:
        path = map_theory_to_file(fqn)
        if path is None:
            skipped_no_file.append(fqn)
            continue
        try:
            with open(path) as f:
                lines = f.readlines()
        except Exception:
            continue
        proofs = parse_proofs(lines, extract_subproofs=True)
        # Annotate each proof with theory wall + combined score
        pressure_weight = {"high": 3.0, "medium": 1.0, "low": 0.3}
        for p in proofs:
            score = (math.log1p(wall) * p["size"]
                     * pressure_weight.get(p["search_pressure"], 0.5))
            p["score"] = score
            p["theory_wall"] = wall
            p["theory_fqn"] = fqn
            p["file"] = str(path.relative_to(REPO))
            path_suggest, path_rationale = suggest_path(p, wall)
            p["path"] = path_suggest
            p["path_rationale"] = path_rationale
        results.extend(proofs)

    # Sort all lemmas across files by combined score
    results.sort(key=lambda r: -r["score"])

    # Build markdown
    md = []
    md.append("# Lemma Optimization Targets — Ranked by score\n")
    md.append("_For each top-cost theory in the post-swap canonical build, rank_")
    md.append("_individual lemmas by `log(1+theory_wall) × size × pressure_weight`._")
    md.append("_The score biases toward: lemmas in expensive theories, large_")
    md.append("_lemmas, and unhinted search-heavy tactics (the leverage points_")
    md.append("_per the principle: \"reduce search path / give precise direction_")
    md.append("_to shorten lemma time\")._\n")
    md.append("Pressure weights: high=3.0, medium=1.0, low=0.3.\n")
    md.append("Optimization paths:")
    md.append("- **HAMMER**: short search-heavy tactic — sledgehammer for metis reconstruction")
    md.append("- **HAMMER+ISAR**: long search-heavy chain — sledgehammer specific steps + Isar decompose")
    md.append("- **HINT-ADD**: hinted tactic — minimize rule set to shrink search")
    md.append("- **SUBPROOF-DRILL**: long Isar proof — locate slow inner `have` via proof-timing")
    md.append("- **DEFER**: theory wall too low for measurable wins")
    md.append("- **LOW-PRIORITY**: low-pressure `by`; unlikely to move")
    md.append("- **INSPECT**: needs case-by-case look\n")

    md.append("## Top 30 lemma targets (overall)\n")
    md.append("| # | Lemma | Theory (wall) | Size (L) | Top | Pressure | Score | Path |")
    md.append("|---:|---|---|---:|---|---|---:|---|")
    seen_files = set()
    for i, r in enumerate(results[:30], 1):
        thy_short = r["theory_fqn"].split(".", 1)[1] if "." in r["theory_fqn"] else r["theory_fqn"]
        md.append(
            f"| {i} | `{r['name']}` | `{thy_short}` ({r['theory_wall']:.0f}s) | "
            f"{r['size']} | {r['top_tactic']} | {r['search_pressure']} | "
            f"{r['score']:.0f} | {r['path']} |"
        )
        seen_files.add(r["theory_fqn"])
    md.append("")

    # Per-path summary
    md.append("## Distribution by recommended path\n")
    path_counts = defaultdict(int)
    for r in results:
        path_counts[r["path"]] += 1
    for p in ("HAMMER", "HAMMER+ISAR", "HINT-ADD", "SUBPROOF-DRILL",
              "INSPECT", "LOW-PRIORITY", "DEFER"):
        md.append(f"- **{p}**: {path_counts[p]} lemmas")
    md.append("")

    # Top-10 HAMMER+ISAR (high-leverage path)
    hammer_isar = [r for r in results if r["path"] == "HAMMER+ISAR"][:10]
    if hammer_isar:
        md.append("## Top HAMMER+ISAR targets (the high-leverage path)\n")
        md.append("| Lemma | Theory | Size | Pressure | Top tactic | Rationale |")
        md.append("|---|---|---:|---|---|---|")
        for r in hammer_isar:
            thy_short = r["theory_fqn"].split(".", 1)[1] if "." in r["theory_fqn"] else r["theory_fqn"]
            md.append(
                f"| `{r['name']}` | `{thy_short}` | {r['size']} | "
                f"{r['search_pressure']} | {r['top_tactic']} | {r['path_rationale']} |"
            )

    # Notes
    md.append("\n## Methodology notes\n")
    md.append("- **theory_wall** is the elapsed in the theory's ORIGIN session")
    md.append("  (where it's first compiled), not the amplified cost in CSTR-")
    md.append("  duplicating downstream sessions. Optimizing a lemma reduces")
    md.append("  its origin cost AND (proportionally) the cost in every CSTR")
    md.append("  consumer — the multiplier kicks in for free.")
    md.append("- **size** is the line count of the proof body, from `proof_parser`.")
    md.append("  It correlates with optimization surface, but is NOT a direct")
    md.append("  cost proxy. A 5-line `by metis (...)` can be slower than a")
    md.append("  500-line structured Isar proof. Run `scan-slow-proofs.sh` to")
    md.append("  get sorry-cost (the gold standard) before committing budget.")
    md.append("- **search_pressure** is a body-scan: high if any unhinted")
    md.append("  `auto/force/fastforce/blast/metis/smt` appears in the proof body,")
    md.append("  medium if hinted, low otherwise.")
    md.append("- This report is PRE-EMPIRICAL: it ranks candidates before any")
    md.append("  Isabelle measurement. Use `scan-slow-proofs.sh` to refine the")
    md.append("  top-N down to the lemmas with actual high sorry-cost before")
    md.append(f"  spending the skill's $100/session budget. Excluded {len(excluded_thys)}")
    md.append("  pure-ML theories (Kernel_C / Substitute) and SimplExport* —")
    md.append("  those have no lemmas to optimize.")

    if skipped_no_file:
        md.append(f"\n_Skipped {len(skipped_no_file)} theories where .thy file was not found "
                  f"(likely tools/library)._")

    OUT_MD.write_text("\n".join(md) + "\n")
    print(f"Wrote {OUT_MD}")
    print(f"  Analyzed {len(results)} lemmas across {len(seen_files)} theories")
    for p in ("HAMMER", "HAMMER+ISAR", "HINT-ADD", "SUBPROOF-DRILL"):
        print(f"  {p:18s}: {path_counts[p]} lemmas")


if __name__ == "__main__":
    main()
