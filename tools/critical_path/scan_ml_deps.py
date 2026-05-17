#!/usr/bin/env python3
"""
tools/critical_path/scan_ml_deps.py

Scan every .thy file's ML cartouches (`ML \<open> ... \<close>`,
`setup ... \<close>`, `local_setup ... \<close>`, etc.) for references
that imply a cross-pillar dependency NOT visible via Isabelle's
`imports` clause.

Motivation: SEL4GraphRefine.thy imports only `CSpec.Substitute`,
`SimplExport.SEL4SimplExport`, etc. (all spec/proof pillar at the
import level), but its ML body reads `../c/build/$L4V_ARCH/kernel_all.c_pp`
directly to drive graph-refinement proofs. The theory-DAG can't see this,
so the closure analysis mis-classifies SEL4GraphRefine as needs-H-NOT-C
when it actually needs C content.

This scanner produces a supplementary map:

    reports/ml-pillar-deps.json
    {
      "Session.Theory": ["c", "haskell", "spec"],   # union of pillars touched via ML
      ...
    }

which `theory_axis_2d.py` unions into each theory's `dep_pillar_set`
before classification.

Detection patterns (heuristic, seL4-specific):

  C pillar  — referenced via:
    "c/build/" or "kernel_all.c_pp" string literals;
    CalculateState.{get_csenv,get_globals_rcd,*};
    ParseGraph.{funs,mkdir_relative,*};
    AutoCorresModifiesProofs.*;
    ProveSimplToGraphGoals.*.

  Haskell — referenced via:
    "/spec/design/" or "/spec/haskell/" string literals;
    {SkelLib,DesignSpec}.* APIs (if encountered).

  Spec    — referenced via:
    "/spec/abstract/" string literals.

The patterns are conservative — they fire on clear evidence. A theory
that mentions C only in a comment string won't trigger (because we
restrict to ML cartouches).
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
L4V = REPO / "verification" / "l4v"
OUT_JSON = REPO / "reports" / "ml-pillar-deps.json"

sys.path.insert(0, str(REPO / "tools" / "lemma_inventory"))
import parse_roots  # noqa: E402

# ML-block-introducing keywords. Each is followed by `\<open> ... \<close>`.
ML_INTRO_TOKENS = (
    "ML", "ML_command", "ML_val", "ML_prf",
    "setup", "local_setup",
    "attribute_setup", "method_setup",
    "lift_definition",  # body can include ML morphisms
    "translate_ML",
)
# `ML_file "path"` is a different form — direct file load.
ML_FILE_RE = re.compile(r'\bML_file\s+"([^"]+)"')

# Cartouche pattern. Match the introducing keyword followed by \<open>,
# then capture body up to matching \<close> (with nesting).
ML_INTRO_RE = re.compile(
    r'\b(' + '|'.join(ML_INTRO_TOKENS) + r')\b\s*\\<open>'
)

# Heuristics — patterns that imply a pillar dependency. (regex, pillar)
PILLAR_PATTERNS = [
    # C pillar — file references
    (re.compile(r'kernel_all\.c_pp'),                "c"),
    (re.compile(r'c/build/'),                        "c"),
    (re.compile(r'spec/cspec/c/build'),              "c"),
    (re.compile(r'seL4/src/'),                       "c"),
    # C pillar — well-known seL4 ML APIs that operate on C semantics
    (re.compile(r'\bCalculateState\.'),              "c"),
    (re.compile(r'\bParseGraph\.'),                  "c"),
    (re.compile(r'\bAutoCorresModifiesProofs\.'),    "c"),
    (re.compile(r'\bProveSimplToGraphGoals\.'),      "c"),
    (re.compile(r'\bCFunDump_filename\b'),           "c"),
    # Haskell pillar — file references
    (re.compile(r'spec/design/'),                    "haskell"),
    (re.compile(r'spec/haskell/'),                   "haskell"),
    # Spec pillar — file references
    (re.compile(r'spec/abstract/'),                  "spec"),
]


def extract_ml_blocks(text: str) -> list[str]:
    """Extract ML block bodies from a .thy file, handling nested cartouches."""
    blocks = []
    for m in ML_INTRO_RE.finditer(text):
        start = m.end()
        # Walk forward, tracking \<open> / \<close> nesting depth.
        depth = 1
        i = start
        n = len(text)
        while i < n and depth > 0:
            if text.startswith(r'\<open>', i):
                depth += 1; i += len(r'\<open>')
            elif text.startswith(r'\<close>', i):
                depth -= 1
                if depth == 0:
                    blocks.append(text[start:i])
                    i += len(r'\<close>')
                    break
                i += len(r'\<close>')
            else:
                i += 1
    return blocks


def scan_thy(path: Path) -> set[str]:
    """Return set of pillars referenced via ML in this .thy file."""
    try:
        raw = path.read_text(errors="replace")
    except OSError:
        return set()
    # Strip Isabelle comments first (mirrors theory-DAG behavior).
    text = parse_roots.strip_isabelle_comments(raw)
    pillars = set()
    # ML_file "path" — direct file load
    for m in ML_FILE_RE.finditer(text):
        path_str = m.group(1)
        for pat, p in PILLAR_PATTERNS:
            if pat.search(path_str):
                pillars.add(p)
    # ML cartouches
    for block in extract_ml_blocks(text):
        for pat, p in PILLAR_PATTERNS:
            if pat.search(block):
                pillars.add(p)
    return pillars


def main() -> int:
    parsed = parse_roots.parse_all_roots(L4V, arch="ARM")
    sessions = parsed["sessions"]

    # Walk every .thy, assign owner session via parse_roots, run scan.
    result: dict[str, list[str]] = {}
    n_scanned = 0
    n_with_deps = 0
    by_pillar = defaultdict(int)

    for thy in sorted(L4V.rglob("*.thy")):
        if not thy.is_file():
            continue
        sess = parse_roots.assign_session_for_thy(thy, parsed)
        if sess is None:
            continue
        n_scanned += 1
        pillars = scan_thy(thy)
        if not pillars:
            continue
        n_with_deps += 1
        for p in pillars:
            by_pillar[p] += 1
        fqn = f"{sess}.{thy.stem}"
        result[fqn] = sorted(pillars)

    OUT_JSON.write_text(json.dumps(result, indent=2, sort_keys=True))
    print(f"Scanned {n_scanned} .thy files")
    print(f"  with ML pillar deps: {n_with_deps}")
    for p, n in sorted(by_pillar.items()):
        print(f"    {p}: {n} files")
    print(f"\nWrote {OUT_JSON}")

    # Show a few interesting ones
    print("\nExamples of ML-derived c-pillar deps (top 10 by name):")
    c_deps = [k for k, v in result.items() if "c" in v]
    for k in sorted(c_deps)[:10]:
        print(f"  {k}: {result[k]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
