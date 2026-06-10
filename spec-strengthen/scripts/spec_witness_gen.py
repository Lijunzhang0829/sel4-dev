#!/usr/bin/env python3
"""
tools/spec_strengthen/spec_witness_gen.py

Optional Step 2 helper: given a patch + the unmodified theory file,
emit the `_old` witness lemma the SKILL contract requires for a
shape-1 (modify) patch. Codifies the witness-rules-by-triple-shape
table from references/spec-strengthen-playbook.md so the agent
does not pick rules by name-matching alone.

Usage:
  python3 spec_witness_gen.py <patch> <theory.thy>
    [--emit {snippet,patch-fragment}]   # default: snippet
    [--no-warn-weakening]               # silence weakening warning

Patch format is the check-theory.sh range-replace format (same as
spec_impact.py consumes): `<start> <end>\n<replacement>\n---\n...`.

Exit codes:
  0  shape-1 (modify) — witness emitted to stdout
  1  shape-2 (additive) — no witness needed; stdout explains
  2  shape-3 (delete) — refactor, NOT a strengthening; stdout
     explains required action (grep / compatibility alias)
  3  weakening-direction detected — patch should be rejected
  4  parse error / ambiguous shape — manual review required

Design constraints:
  - Falls back to "TODO" skeleton if it can't auto-pick the rule
    (e.g. unrecognised triple shape, multi-hunk with different
    shapes). Never emits a confidently-wrong witness.
  - All discharge clauses default to `simp` per playbook table;
    if the spec change has real semantic content beyond
    monotonicity the agent reads the comment and extends.
"""

from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path

# Reuse parsers from sibling tools — internal library code.
from spec_impact import parse_patch, apply_patch_in_memory, PatchHunk
from spec_strengthen_scan import (
    Lemma, parse_thy_lemmas, extract_predicate_body,
)
from spec_impact import split_conjuncts


# ---------------- Witness-rules-by-triple-shape table -----------------------
#
# Cross-checked against verification/l4v/lib/Monads/ on 2026-06-02.
# Mirrors `references/spec-strengthen-playbook.md` §"Witness rules
# by Hoare triple shape". If you change this, change the playbook too.

# Triple shape detected from the OLD lemma text:
#   "valid"     — `\<lbrace>P\<rbrace> f \<lbrace>Q\<rbrace>`
#   "validE"    — `\<lbrace>P\<rbrace> f \<lbrace>Q\<rbrace>,\<lbrace>E\<rbrace>`
#   "validE_R"  — `\<lbrace>P\<rbrace> f \<lbrace>Q\<rbrace>,-`
#   "validE_E"  — `\<lbrace>P\<rbrace> f -,\<lbrace>E\<rbrace>`

WITNESS_RULES = {
    # (shape, direction) -> rule
    ("valid",    "pre-weaken"):       "hoare_weaken_pre",
    ("valid",    "post-strengthen"):  "hoare_strengthen_post",
    ("validE",   "pre-weaken"):       "hoare_pre",
    ("validE",   "post-strengthen"):  "hoare_post_impE",
    ("validE_R", "pre-weaken"):       "hoare_pre",
    ("validE_R", "post-strengthen"):  "hoare_strengthen_postE_R",
    ("validE_E", "pre-weaken"):       "hoare_pre",
    ("validE_E", "post-strengthen"):  "hoare_strengthen_postE_E",
}


# Triple shape regexes — applied to the lemma's STATEMENT text only.
# We look for the trailing exception-clause after the success
# postcondition `\<lbrace>...\<rbrace>`.
SHAPE_VALID_E_R = re.compile(r"\\<rbrace>\s*,\s*-(?:\s*$|\s*\n)")
SHAPE_VALID_E_E = re.compile(r"\\<rbrace>\s*-\s*,\s*\\<lbrace>")
SHAPE_VALID_E   = re.compile(r"\\<rbrace>\s*,\s*\\<lbrace>")


def detect_triple_shape(stmt: str) -> str | None:
    """Return one of {valid, validE, validE_R, validE_E, None}."""
    if SHAPE_VALID_E_R.search(stmt):
        return "validE_R"
    if SHAPE_VALID_E_E.search(stmt):
        return "validE_E"
    if SHAPE_VALID_E.search(stmt):
        return "validE"
    if "\\<lbrace>" in stmt and "\\<rbrace>" in stmt:
        return "valid"
    return None


# ---------------- Diff direction detection ----------------------------------

def direction_from_pair(old: Lemma, new: Lemma) -> str:
    """Return one of: pre-weaken, post-strengthen, weakening, noop, mixed.

    The decision is structural — counts top-level conjuncts in pre/post.
    'weakening' fires when pre adds more than it drops (pre got HARDER)
    or post drops conjuncts (post got WEAKER) — caller should reject.
    """
    old_pre = set(split_conjuncts(extract_predicate_body(old.pre)))
    new_pre = set(split_conjuncts(extract_predicate_body(new.pre)))
    old_post = set(split_conjuncts(extract_predicate_body(old.post)))
    new_post = set(split_conjuncts(extract_predicate_body(new.post)))

    pre_dropped = len(old_pre - new_pre)
    pre_added   = len(new_pre - old_pre)
    post_dropped = len(old_post - new_post)
    post_added   = len(new_post - old_post)

    pre_weakened   = pre_dropped > pre_added
    pre_hardened   = pre_added > pre_dropped
    post_strengthened = post_added > post_dropped
    post_weakened     = post_dropped > post_added

    # Weakening = made the lemma less useful
    if pre_hardened or post_weakened:
        return "weakening"
    if pre_weakened and not post_strengthened:
        return "pre-weaken"
    if post_strengthened and not pre_weakened:
        return "post-strengthen"
    if pre_weakened and post_strengthened:
        # Both — prefer post-strengthen rule which preserves the
        # weaker pre via hoare_pre/hoare_weaken_pre composition.
        # Agent gets a comment to maybe split the patch.
        return "mixed"
    if pre_dropped == 0 and pre_added == 0 and post_dropped == 0 and post_added == 0:
        return "noop"
    return "mixed"


# ---------------- Per-hunk lemma extraction ---------------------------------

def lemmas_in_range(lemmas: list[Lemma], lo: int, hi: int) -> list[Lemma]:
    """Lemmas whose header line is within [lo, hi] inclusive."""
    return [l for l in lemmas if lo <= l.line <= hi]


def lemma_by_name(lemmas: list[Lemma], name: str) -> Lemma | None:
    for l in lemmas:
        if l.name == name:
            return l
    return None


# ---------------- Witness emission ------------------------------------------

def render_witness(name: str, old_stmt: str, rule: str,
                   discharge: str = "simp") -> str:
    """Render the witness lemma snippet."""
    return (
        f"lemma {name}_old:\n"
        f"  \"{old_stmt}\"\n"
        f"  by (rule {rule}[OF {name}]) {discharge}\n"
    )


def render_todo_witness(name: str, old_stmt: str, reason: str) -> str:
    return (
        f"lemma {name}_old:\n"
        f"  \"{old_stmt}\"\n"
        f"  (* TODO: pick witness rule manually — {reason}.\n"
        f"     See references/spec-strengthen-playbook.md §Witness rules\n"
        f"     by Hoare triple shape. Expected: 1-line `by (rule\n"
        f"     <hoare-monotonicity>[OF {name}]) simp` *)\n"
        f"  sorry\n"
    )


def reconstruct_stmt_text(l: Lemma) -> str:
    """Best-effort: assemble the Hoare-triple statement string from a
    parsed Lemma. Loses some triple-shape suffix info (validE comma /
    validE_R `,-`) — caller should regex-detect shape on the ORIGINAL
    text window, not on this reconstruction."""
    return f"\\<lbrace>{l.pre}\\<rbrace> {l.body} \\<lbrace>{l.post}\\<rbrace>"


# ---------------- Triple-shape detection on raw text ------------------------

def raw_stmt_window(theory_text: str, lemma_line: int) -> str:
    """Read the lemma's statement window (from header to first proof
    keyword) verbatim from theory_text. lemma_line is 1-based."""
    lines = theory_text.splitlines(keepends=True)
    start_idx = lemma_line - 1
    chunk = []
    for idx in range(start_idx, min(start_idx + 40, len(lines))):
        chunk.append(lines[idx])
        if re.match(r"^\s*(?:apply|by|proof|done|qed|sorry|oops)\b",
                    lines[idx]):
            break
    return "".join(chunk)


# ---------------- Main shape classification --------------------------------

class Decision:
    def __init__(self, shape: str, exit_code: int, message: str,
                 witness: str = ""):
        self.shape = shape          # "1-modify", "2-additive", "3-delete",
                                    # "weakening", "ambiguous"
        self.exit_code = exit_code
        self.message = message
        self.witness = witness


def classify_and_emit(patch_path: Path, theory_path: Path,
                      emit_mode: str = "snippet",
                      warn_weakening: bool = True) -> Decision:
    """Inspect the patch, decide patch shape, emit witness or
    explanation.

    Algorithm (line-agnostic — robust to whole-hunk vs per-line
    matching):
      1. Parse OLD lemma set from theory file.
      2. Apply patch in memory and parse NEW lemma set.
      3. Set arithmetic:
           added    = NEW_names - OLD_names  (sans `_old` siblings)
           removed  = OLD_names - NEW_names
           common   = OLD_names ∩ NEW_names
         For each name in `common`, compare structurally — that's the
         shape-1 candidates with their direction.
      4. Single shape rollup; emit witnesses for modify list.
    """

    # ---- Parse inputs ----
    theory_text = theory_path.read_text(encoding="utf-8", errors="replace")
    hunks = parse_patch(patch_path)
    if not hunks:
        return Decision("ambiguous", 4, "patch has no hunks")

    # ---- Old / new lemma sets ----
    old_lemmas = parse_thy_lemmas(theory_path)
    new_text = apply_patch_in_memory(theory_text, hunks)
    import tempfile, os
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".thy", delete=False, encoding="utf-8"
    ) as tf:
        tf.write(new_text)
        new_path = Path(tf.name)
    try:
        new_lemmas = parse_thy_lemmas(new_path)
    finally:
        os.unlink(new_path)

    old_by_name = {l.name: l for l in old_lemmas}
    new_by_name = {l.name: l for l in new_lemmas}
    old_names = set(old_by_name)
    new_names = set(new_by_name)

    # `<X>_old` lemmas in NEW that aren't in OLD: those are witnesses
    # for sibling modifications — exclude from the "purely added" set.
    raw_added   = new_names - old_names
    added       = {n for n in raw_added
                   if not (n.endswith("_old")
                           and n[:-4] in old_names and n[:-4] in new_names)}
    removed     = old_names - new_names
    common      = old_names & new_names

    decisions: list[tuple[str, str]] = []
    witnesses: list[str] = []

    # ---- Classify each common-name pair ----
    for name in sorted(common):
        old_l = old_by_name[name]
        new_l = new_by_name[name]
        direction = direction_from_pair(old_l, new_l)
        if direction == "noop":
            continue
        if direction == "weakening":
            decisions.append(("weakening", name))
            continue

        # Direction in {pre-weaken, post-strengthen, mixed}
        old_raw = raw_stmt_window(theory_text, old_l.line)
        shape = detect_triple_shape(old_raw)
        old_stmt = reconstruct_stmt_text(old_l)

        if direction == "mixed":
            witnesses.append(render_todo_witness(
                name, old_stmt,
                f"mixed direction (both pre weakened and post "
                f"strengthened); shape detected as {shape}"
            ))
            decisions.append(("1-modify", name))
            continue

        if shape is None:
            witnesses.append(render_todo_witness(
                name, old_stmt,
                "could not detect triple shape from raw text"
            ))
            decisions.append(("1-modify", name))
            continue

        rule = WITNESS_RULES.get((shape, direction))
        if rule is None:
            witnesses.append(render_todo_witness(
                name, old_stmt,
                f"no rule mapping for ({shape}, {direction})"
            ))
            decisions.append(("1-modify", name))
            continue

        witnesses.append(render_witness(name, old_stmt, rule))
        decisions.append(("1-modify", name))

    # ---- Purely-added (shape 2) and removed (shape 3) ----
    for name in sorted(added):
        decisions.append(("2-additive", name))
    for name in sorted(removed):
        decisions.append(("3-delete", name))

    # ---- Roll up to a single shape decision ----
    shapes = {d[0] for d in decisions}
    if "weakening" in shapes:
        names = ", ".join(n for s, n in decisions if s == "weakening")
        msg = (
            f"WEAKENING DETECTED in lemma(s): {names}\n"
            f"The patch makes the lemma less useful (pre hardened or "
            f"post weakened). This is not a spec strengthening; "
            f"reject or rework. No witness emitted."
        )
        return Decision("weakening", 3, msg)

    if "3-delete" in shapes and "1-modify" not in shapes \
            and "2-additive" not in shapes:
        names = ", ".join(n for s, n in decisions if s == "3-delete")
        msg = (
            f"SHAPE 3 (deletion) — lemma(s) {names} removed.\n"
            f"This is a refactor, not a strengthening. Required:\n"
            f"  (a) `grep -rn '\\b{names}\\b' verification/l4v` "
            f"returns no consumer site, OR\n"
            f"  (b) the same patch adds `lemmas {names} = "
            f"<new-or-replacement>` to keep the name resolvable.\n"
            f"No `_old` witness; see SKILL Step 2."
        )
        return Decision("3-delete", 2, msg)

    if "1-modify" in shapes:
        msg = "# Shape 1 (modify) — witness lemma(s) below:\n\n"
        msg += "\n".join(witnesses)
        if emit_mode == "patch-fragment":
            msg = (
                "# Append these lines to your patch immediately after "
                "the modified lemma(s)\n# (inside the same hunk so "
                "check-theory.sh --patch verifies them together):\n\n"
            ) + msg
        return Decision("1-modify", 0, msg, witness="\n".join(witnesses))

    if "2-additive" in shapes:
        names = ", ".join(n for s, n in decisions if s == "2-additive")
        return Decision(
            "2-additive", 1,
            f"SHAPE 2 (additive) — new lemma(s) {names} added; no old\n"
            f"form to derive. No witness required. Run `check-theory.sh\n"
            f"--patch` to verify; impact verdict should be `additive`."
        )

    return Decision(
        "ambiguous", 4,
        "Could not classify patch (no shape detected). Inspect manually."
    )


# ---------------- CLI -------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Emit `_old` witness lemma for a spec-strengthening patch"
    )
    ap.add_argument("patch", type=Path,
                    help="Patch file (check-theory.sh range-replace format)")
    ap.add_argument("theory", type=Path,
                    help="Unmodified theory file the patch applies to")
    ap.add_argument("--emit", choices=["snippet", "patch-fragment"],
                    default="snippet",
                    help="Output mode (default: snippet)")
    ap.add_argument("--no-warn-weakening", action="store_true",
                    help="Suppress weakening warning text (still exits 3)")
    args = ap.parse_args()

    if not args.patch.exists():
        print(f"patch not found: {args.patch}", file=sys.stderr)
        return 4
    if not args.theory.exists():
        print(f"theory not found: {args.theory}", file=sys.stderr)
        return 4

    d = classify_and_emit(
        args.patch, args.theory,
        emit_mode=args.emit,
        warn_weakening=not args.no_warn_weakening,
    )
    print(d.message)
    return d.exit_code


if __name__ == "__main__":
    sys.exit(main())
