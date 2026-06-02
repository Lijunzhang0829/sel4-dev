#!/usr/bin/env python3
r"""
tools/spec_strengthen/spec_impact.py — spec-strengthen impact metric.

Required by `isabelle_prover_spec` SKILL Step 4.

What this script does (and does NOT do):

- DOES: structural-diff a patch's lemma deltas; classify each into a
  verdict (premise-weaken / monotone-strengthen / postcond-strengthen /
  additive / removal / rewrite / weakening / noop); compute a Semantic
  Strength Score (SSS); check wall-time regression vs baseline;
  detect whether the patch contains an `<name>_old` witness lemma.
- DOES NOT: prove A_old derivable from A_new. That is check-theory.sh's
  job — the witness lemma is verified by check-theory.sh in the same
  pass as the strengthened lemma.

Acceptance gates (exit code 0 iff all pass):
  1. No `weakening` verdict on any delta.
  2. At least one delta in
     {premise-weaken, monotone-strengthen, postcond-strengthen,
      additive}.
  3. trial_wall_ms ≤ baseline_wall_ms × 1.30 (when both supplied).
  Witness presence is reported but advisory (real check at Step 3).

Usage:
  spec_impact.py <patch.txt> <theory.thy>
                 [--baseline-wall MS] [--trial-wall MS]
                 [--tree DIR]
                 [--append-to FILE] [--json]
                 [--measurement-out FILE]

`--measurement-out FILE` writes a simplified JSON suited to
`reports/experiments/<NNNN>/measurement.json` (the per-PR audit
bundle). The simplified schema is a subset of the verbose `--json`
output, with fields named after rule-5's template.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
from spec_strengthen_scan import parse_thy_lemmas, Lemma  # noqa: E402


VERDICT_PREMISE_WEAKEN     = "premise-weaken"
VERDICT_MONOTONE_STRENGTHEN = "monotone-strengthen"
VERDICT_POSTCOND_STRENGTHEN = "postcond-strengthen"
VERDICT_ADDITIVE            = "additive"
VERDICT_REMOVAL             = "removal"
VERDICT_REWRITE             = "rewrite"
VERDICT_WEAKENING           = "weakening"
VERDICT_NOOP                = "noop"

ACCEPTABLE_VERDICTS = {
    VERDICT_PREMISE_WEAKEN,
    VERDICT_MONOTONE_STRENGTHEN,
    VERDICT_POSTCOND_STRENGTHEN,
    VERDICT_ADDITIVE,
}

# Verdicts that should be accompanied by an `<name>_old` witness lemma
# in the same patch (strengthening of an existing lemma — the witness
# is the soundness proof). `additive` is not Tier 1 (no `_old` form
# to derive); witness is omitted.
TIER1_VERDICTS = {
    VERDICT_PREMISE_WEAKEN,
    VERDICT_MONOTONE_STRENGTHEN,
    VERDICT_POSTCOND_STRENGTHEN,
}

WALL_REGRESSION_THRESHOLD_PCT = 30.0  # trial may exceed baseline by ≤ 30%


# ---------- Patch parsing ----------------------------------------------------

@dataclass
class PatchHunk:
    start: int  # 1-based, inclusive
    end: int    # 1-based, inclusive
    text: str


def parse_patch(patch_path: Path) -> list[PatchHunk]:
    raw = patch_path.read_text(encoding="utf-8")
    blocks = [b.strip("\n") for b in raw.split("\n---\n") if b.strip()]
    hunks: list[PatchHunk] = []
    for blk in blocks:
        if not blk.strip():
            continue
        m = re.match(r"^(\d+)\s+(\d+)\s*\n(.*)", blk, re.DOTALL)
        if not m:
            raise ValueError(f"Bad patch hunk header: {blk[:60]!r}")
        hunks.append(PatchHunk(
            start=int(m.group(1)),
            end=int(m.group(2)),
            text=m.group(3).rstrip("\n"),
        ))
    return hunks


def apply_patch_in_memory(theory_text: str, hunks: list[PatchHunk]) -> str:
    lines = theory_text.splitlines(keepends=True)
    for h in sorted(hunks, key=lambda x: -x.start):
        replacement = h.text + ("\n" if not h.text.endswith("\n") else "")
        lines[h.start - 1: h.end] = [replacement]
    return "".join(lines)


# ---------- Structural diff (primary verdict driver) -------------------------

def split_conjuncts(predicate: str) -> list[str]:
    r"""Split a predicate text on top-level ` and ` / `\<and>`.
    Returns normalized conjunct strings.

    "Top-level" approximated by balance counter: only split when paren
    depth is 0. This avoids splitting inside nested predicates.
    """
    if not predicate.strip():
        return []
    s = predicate.strip()
    # Strip a single layer of outer parens if matching
    if s.startswith("(") and s.endswith(")"):
        depth = 0
        outer = True
        for i, c in enumerate(s):
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0 and i < len(s) - 1:
                    outer = False
                    break
        if outer:
            s = s[1:-1].strip()

    # Walk and split on top-level " and " or \<and>
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    i = 0
    while i < len(s):
        if s[i] == "(":
            depth += 1
            buf.append(s[i]); i += 1
        elif s[i] == ")":
            depth -= 1
            buf.append(s[i]); i += 1
        elif depth == 0 and s[i:i+5] == " and ":
            parts.append("".join(buf))
            buf = []
            i += 5
        elif depth == 0 and s[i:i+6] == "\\<and>":
            parts.append("".join(buf))
            buf = []
            i += 6
        else:
            buf.append(s[i]); i += 1
    if buf:
        parts.append("".join(buf))

    # Normalize: collapse whitespace; strip surrounding parens once if matched
    out = []
    for p in parts:
        p = " ".join(p.split())
        if p.startswith("(") and p.endswith(")"):
            depth = 0
            matched = True
            for j, c in enumerate(p):
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0 and j < len(p) - 1:
                        matched = False
                        break
            if matched:
                p = p[1:-1].strip()
        if p:
            out.append(p)
    return out


def extract_predicate_body(pred: str) -> str:
    r"""Strip lambda binders from a Hoare-triple predicate (pre or post).

    Examples:
      `\<lambda>_ s. body`  →  `body`
      `\<lambda>rv. body`   →  `body`
      `\<lambda>_. P`        →  `P`
      `\<lambda>s. P s`      →  `P s`
      `invs and valid_objs`  →  `invs and valid_objs`  (no binder)
    """
    m = re.match(r"\s*\\<lambda>[^.]*\.\s*(.*)", pred, re.DOTALL)
    if m:
        return m.group(1).strip()
    return pred.strip()


# Backwards-compat alias (old name)
extract_post_body = extract_predicate_body


FRAME_CONJUNCT_RE = re.compile(
    r"\bP\s*\(\s*\w+\s+s'?\s*\)"            # P (accessor s)
    r"|=\s*\w+\s+s'?\s*$"                   # ... = accessor s (preservation tail)
    r"|\w+\s+s'?\s*=\s*\w+\s+s'?"           # acc s = acc s'  (frame eq form)
    r"|\\<lambda>[^.]*s'?\s*\.\s*\w+\s+s'?\s*="  # \<lambda>_ s. acc s = ...
)
# Note: still heuristic — designed to catch common l4v frame shapes.
# `acc s` accessor patterns include both bare and primed (s, s') variants.
LE_RE = re.compile(r"(?:\\<le>|≤|<=)")
EQ_RE = re.compile(r"(?<![<>=!])=(?!=)")


@dataclass
class StructuralDiff:
    removed_premises: int   # conjuncts in old.pre not in new.pre
    added_premises: int     # conjuncts in new.pre not in old.pre
    removed_post_facts: int # conjuncts in old.post not in new.post
    added_post_facts: int   # conjuncts in new.post not in old.post
    added_frame_facts: int  # subset of added_post_facts matching frame shape
    swap_le_to_eq: int      # ≤ in old.post converted to = in new.post


def structural_diff(b: Lemma, a: Lemma) -> StructuralDiff:
    # Strip lambda binders from BOTH pre and post before splitting.
    # Predicates of form `\<lambda>s. P1 \<and> P2` must have the binder
    # removed; otherwise the body conjuncts get hidden inside one
    # opaque lambda blob.
    pre_b = set(split_conjuncts(extract_predicate_body(b.pre)))
    pre_a = set(split_conjuncts(extract_predicate_body(a.pre)))
    post_b = set(split_conjuncts(extract_predicate_body(b.post)))
    post_a = set(split_conjuncts(extract_predicate_body(a.post)))

    removed_pre = pre_b - pre_a
    added_pre = pre_a - pre_b
    removed_post = post_b - post_a
    added_post = post_a - post_b

    added_frame = sum(1 for c in added_post if FRAME_CONJUNCT_RE.search(c))

    # ≤ → = swap: rough — count ≤ in old.post that turned into = in new.post
    swaps = 0
    if LE_RE.search(b.post) and EQ_RE.search(a.post) and not LE_RE.search(a.post):
        swaps = 1

    return StructuralDiff(
        removed_premises=len(removed_pre),
        added_premises=len(added_pre),
        removed_post_facts=len(removed_post),
        added_post_facts=len(added_post),
        added_frame_facts=added_frame,
        swap_le_to_eq=swaps,
    )


# ---------- Token-based heuristic (notes only, no verdict) -------------------

def count_tokens(s: str) -> int:
    return len(re.findall(r"[A-Za-z_][A-Za-z_0-9']*", s))


def count_premise_conjuncts_heuristic(pre: str) -> int:
    """Cheap proxy; structural_diff is authoritative."""
    return len(split_conjuncts(pre))


def has_state_reference(post: str) -> bool:
    m = re.search(r"\\<lambda>\s*[^.]*?\bs\b[^.]*?\.\s*(.*)", post,
                  re.DOTALL)
    if m:
        return bool(re.search(r"\bs\b", m.group(1)))
    return bool(re.search(r"\bs\b", post))


# ---------- LemmaMetrics + delta --------------------------------------------

@dataclass
class LemmaMetrics:
    name: str
    line: int
    pre_tokens: int
    post_tokens: int
    premise_count: int
    state_dependent: bool

    def as_dict(self):
        return asdict(self)


def make_metrics(l: Lemma) -> LemmaMetrics:
    return LemmaMetrics(
        name=l.name,
        line=l.line,
        pre_tokens=count_tokens(l.pre),
        post_tokens=count_tokens(l.post),
        premise_count=count_premise_conjuncts_heuristic(l.pre),
        state_dependent=has_state_reference(l.post),
    )


@dataclass
class LemmaDelta:
    name: str
    classification: str  # added | removed | modified
    before: LemmaMetrics | None
    after: LemmaMetrics | None
    before_lemma: Lemma | None = None
    after_lemma: Lemma | None = None
    diff: StructuralDiff | None = None
    notes: list[str] = field(default_factory=list)
    verdict: str = ""
    strength_score: float = 0.0
    score_breakdown: dict = field(default_factory=dict)
    file: str = ""

    def as_dict(self):
        return {
            "name": self.name,
            "classification": self.classification,
            "before": self.before.as_dict() if self.before else None,
            "after": self.after.as_dict() if self.after else None,
            "diff": asdict(self.diff) if self.diff else None,
            "verdict": self.verdict,
            "strength_score": self.strength_score,
            "score_breakdown": dict(self.score_breakdown),
            "notes": list(self.notes),
            "file": self.file,
        }


# ---------- Verdict classifier ----------------------------------------------

def classify_delta(d: LemmaDelta) -> tuple[str, list[str]]:
    """Returns (verdict, extra_notes).

    For added Hoare-triple lemmas we emit `additive` unconditionally.
    The skill's contract is that purely-additive lemmas don't need a
    witness; auxiliary aux lemmas without strengthening intent are
    not distinguished here (the PR reviewer judges intent — soundness
    is already covered by check-theory.sh).
    """
    notes: list[str] = []

    if d.classification == "added":
        return VERDICT_ADDITIVE, notes

    if d.classification == "removed":
        notes.append("removal — requires `<name>_old` witness in same "
                     "patch (delete-style cleanup) or rejection")
        return VERDICT_REMOVAL, notes

    # Modified — use structural diff
    sd = d.diff
    if not sd:
        return VERDICT_REWRITE, notes

    # WEAKENING checks: post lost conjunct OR pre gained conjunct strictly.
    # (We require BOTH: net post loss AND net pre gain that's not balanced.)
    if sd.removed_post_facts > sd.added_post_facts:
        notes.append(f"postcondition lost {sd.removed_post_facts - sd.added_post_facts} "
                     "more conjuncts than it gained → WEAKENING")
        return VERDICT_WEAKENING, notes
    if sd.added_premises > sd.removed_premises:
        notes.append(f"precondition gained {sd.added_premises - sd.removed_premises} "
                     "more conjuncts than it dropped → WEAKENING")
        return VERDICT_WEAKENING, notes

    premise_strictly_weaker = sd.removed_premises > 0 and sd.added_premises == 0
    post_strictly_stronger = (
        (sd.added_post_facts > 0 or sd.added_frame_facts > 0 or sd.swap_le_to_eq > 0)
        and sd.removed_post_facts == 0
    )

    if premise_strictly_weaker and post_strictly_stronger:
        notes.append("premise weakened AND postcondition strengthened")
        return VERDICT_MONOTONE_STRENGTHEN, notes
    if premise_strictly_weaker:
        notes.append(f"removed {sd.removed_premises} premise conjunct(s)")
        return VERDICT_PREMISE_WEAKEN, notes
    if post_strictly_stronger:
        if sd.swap_le_to_eq:
            notes.append("≤ → = swap detected in postcondition")
        if sd.added_frame_facts:
            notes.append(f"added {sd.added_frame_facts} frame fact(s)")
        if sd.added_post_facts:
            notes.append(f"added {sd.added_post_facts} postcondition conjunct(s)")
        return VERDICT_POSTCOND_STRENGTHEN, notes

    # Pre+post both touched but no clear direction
    if sd.removed_premises > 0 or sd.added_premises > 0 or \
       sd.removed_post_facts > 0 or sd.added_post_facts > 0:
        notes.append("pre/post both shifted; not a clean strengthening — "
                     "manual review")
        return VERDICT_REWRITE, notes

    return VERDICT_NOOP, notes


def compute_strength_score(sd: StructuralDiff) -> tuple[float, dict]:
    """SSS = #removed_premises + #added_post_facts + 0.5 × #added_frame_facts
    + 1.0 × swap_le_to_eq.  Per SKILL §6."""
    score = (
        sd.removed_premises
        + sd.added_post_facts
        + 0.5 * sd.added_frame_facts
        + 1.0 * sd.swap_le_to_eq
    )
    breakdown = {
        "removed_premises": sd.removed_premises,
        "added_post_facts": sd.added_post_facts,
        "added_frame_facts_half": 0.5 * sd.added_frame_facts,
        "swap_le_to_eq": sd.swap_le_to_eq,
    }
    return score, breakdown


# ---------- Derivability witness detection ----------------------------------

DERIVABILITY_RULES = (
    "hoare_pre", "hoare_weaken_pre",
    "hoare_strengthen_post", "hoare_strengthen_postE_R",
    "hoare_post_imp", "hoare_post_imp_R",
)


def detect_derivability_witness(patch_text: str) -> dict:
    """Look for `lemma <X>_old:` constructs in the patch whose body uses
    one of the derivability monotonicity rules. Returns:

      { "present": bool,
        "witnesses": [ { "name": str, "tactic_rule": str } ... ] }
    """
    witnesses: list[dict] = []
    # Match: lemma NAME_old: ...   ... by/apply (rule <derivability_rule>...
    pat = re.compile(
        r"^\s*lemma\s+(\w+_old)\s*\[?[^]]*?\]?\s*:"
        r"[\s\S]{0,2000}?"
        r"(?:by|apply)\s*\(?\s*rule\s+(" +
        "|".join(re.escape(r) for r in DERIVABILITY_RULES) +
        r")\b",
        re.MULTILINE,
    )
    for m in pat.finditer(patch_text):
        witnesses.append({
            "name": m.group(1),
            "tactic_rule": m.group(2),
        })
    return {"present": bool(witnesses), "witnesses": witnesses}


# ---------- Tier 2 consumer count (heuristic, upper bound) ------------------

_DEF_KEYWORD_RE = re.compile(r"\b(?:lemma|theorem|corollary)s?\b")


def grep_consumers(name: str, tree: Path,
                   self_file: Path | None = None,
                   self_line: int = 0) -> tuple[int, int]:
    r"""Return (line_count, file_count) of `\bname\b` matches under tree.

    UPPER BOUND. When (self_file, self_line) supplied, the lemma's own
    definition line (at <self_file>:<self_line>: containing a
    `lemma`/`theorem`/`corollary` keyword) is filtered out — same logic
    as rank_candidates.grep_consumers, so the two tools agree on
    consumer counts.

    Previously this used a list-comp with reversed conditional logic
    that kept the def line instead of dropping it (see review
    2026-06-02). Rewritten to an explicit `for/continue` loop matching
    the rank_candidates version exactly.
    """
    if not tree.exists():
        return (0, 0)
    try:
        out = subprocess.run(
            ["grep", "-rn", "-E", rf"\b{re.escape(name)}\b", str(tree)],
            check=False, capture_output=True, text=True, timeout=60,
        )
        raw = [ln for ln in out.stdout.splitlines() if ln]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return (0, 0)

    if self_file is None or not self_line:
        line_count = len(raw)
        file_count = len({ln.split(":", 1)[0] for ln in raw})
        return (line_count, file_count)

    sf = str(self_file)
    self_prefix = f"{sf}:"
    kept: list[str] = []
    for ln in raw:
        if ln.startswith(self_prefix):
            parts = ln.split(":", 2)
            if len(parts) >= 3 and parts[1].isdigit() \
               and int(parts[1]) == self_line \
               and _DEF_KEYWORD_RE.search(parts[2]):
                # Def line — drop.
                continue
        kept.append(ln)
    line_count = len(kept)
    file_count = len({ln.split(":", 1)[0] for ln in kept})
    return (line_count, file_count)


# ---------- Main -------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("patch", type=Path)
    ap.add_argument("theory", type=Path)
    ap.add_argument("--baseline-wall", type=int, default=None)
    ap.add_argument("--trial-wall", type=int, default=None)
    ap.add_argument("--tree", type=Path,
                    default=Path("verification/l4v/proof"))
    ap.add_argument("--append-to", type=Path, default=None,
                    help="Append markdown report to this file.")
    ap.add_argument("--json", action="store_true",
                    help="Emit verbose JSON to stdout (full report).")
    ap.add_argument("--measurement-out", type=Path, default=None,
                    help="Write simplified measurement.json (rule-5 audit "
                         "bundle schema) to this path. Compatible with "
                         "reports/experiments/_template/measurement.json.")
    args = ap.parse_args()

    if not args.theory.exists():
        print(f"ERROR: theory file not found: {args.theory}", file=sys.stderr)
        return 2
    if not args.patch.exists():
        print(f"ERROR: patch file not found: {args.patch}", file=sys.stderr)
        return 2

    theory_text = args.theory.read_text(encoding="utf-8", errors="replace")
    patch_text  = args.patch.read_text(encoding="utf-8", errors="replace")
    hunks = parse_patch(args.patch)
    after_text = apply_patch_in_memory(theory_text, hunks)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".thy", delete=False, encoding="utf-8",
    ) as tf:
        tf.write(after_text)
        after_path = Path(tf.name)

    try:
        before_lemmas = {l.name: l for l in parse_thy_lemmas(args.theory)}
        after_lemmas  = {l.name: l for l in parse_thy_lemmas(after_path)}
    finally:
        after_path.unlink(missing_ok=True)

    deltas: list[LemmaDelta] = []
    for name in sorted(set(before_lemmas) | set(after_lemmas)):
        b = before_lemmas.get(name)
        a = after_lemmas.get(name)
        if b and not a:
            deltas.append(LemmaDelta(
                name=name, classification="removed",
                before=make_metrics(b), after=None,
                before_lemma=b, after_lemma=None,
                file=str(args.theory),
            ))
        elif a and not b:
            deltas.append(LemmaDelta(
                name=name, classification="added",
                before=None, after=make_metrics(a),
                before_lemma=None, after_lemma=a,
                file=str(args.theory),
            ))
        else:
            assert a and b
            if (a.pre, a.post, a.body) == (b.pre, b.post, b.body):
                continue
            sd = structural_diff(b, a)
            deltas.append(LemmaDelta(
                name=name, classification="modified",
                before=make_metrics(b), after=make_metrics(a),
                before_lemma=b, after_lemma=a,
                diff=sd, file=str(args.theory),
            ))

    # Classify + score each delta
    for d in deltas:
        verdict, vnotes = classify_delta(d)
        d.verdict = verdict
        d.notes.extend(vnotes)
        if d.diff:
            score, breakdown = compute_strength_score(d.diff)
            d.strength_score = score
            d.score_breakdown = breakdown
        # Hint notes (token-level heuristics — DEMOTED to informational)
        if d.before and d.after:
            if d.before.post_tokens != d.after.post_tokens:
                d.notes.append(
                    f"[heuristic] postcond tokens {d.before.post_tokens} → "
                    f"{d.after.post_tokens}"
                )
            if d.before.state_dependent != d.after.state_dependent:
                d.notes.append(
                    f"state-dep {d.before.state_dependent} → {d.after.state_dependent}"
                )

    # Tier 2: consumer counts (upper bound; excludes lemma's own def line)
    consumer_counts: dict[str, dict] = {}
    for d in deltas:
        if d.classification == "removed":
            continue
        lemma_ref = d.after_lemma or d.before_lemma
        line_no = lemma_ref.line if lemma_ref else 0
        lc, fc = grep_consumers(d.name, args.tree,
                                 self_file=args.theory, self_line=line_no)
        consumer_counts[d.name] = {
            "lines": lc, "files": fc,
            "note": "upper bound; lemma's own definition line filtered",
        }

    # Derivability witness detection
    deriv = detect_derivability_witness(patch_text)

    # Wall gate
    wall_pct: float | None = None
    wall_gate_pass = True
    if args.baseline_wall and args.trial_wall:
        wall_pct = (args.trial_wall - args.baseline_wall) * 100.0 / args.baseline_wall
        if wall_pct > WALL_REGRESSION_THRESHOLD_PCT:
            wall_gate_pass = False

    # Aggregate gates
    has_weakening = any(d.verdict == VERDICT_WEAKENING for d in deltas)
    has_acceptable = any(d.verdict in ACCEPTABLE_VERDICTS for d in deltas)
    tier1_deltas = [d for d in deltas if d.verdict in TIER1_VERDICTS]
    derivability_needed = bool(tier1_deltas)
    derivability_advisory_pass = (not derivability_needed) or deriv["present"]

    gate_pass = (
        (not has_weakening)
        and has_acceptable
        and wall_gate_pass
    )

    total_sss = sum(d.strength_score for d in deltas)

    report: dict = {
        "patch": str(args.patch),
        "theory": str(args.theory),
        "baseline_wall_ms": args.baseline_wall,
        "trial_wall_ms": args.trial_wall,
        "wall_pct": round(wall_pct, 2) if wall_pct is not None else None,
        "wall_gate_pass": wall_gate_pass,
        "wall_regression_threshold_pct": WALL_REGRESSION_THRESHOLD_PCT,
        "gate_pass": gate_pass,
        "has_weakening": has_weakening,
        "derivability": {
            **deriv,
            "needed_for_tier1": derivability_needed,
            "advisory_pass": derivability_advisory_pass,
        },
        "strength_score": total_sss,
        "deltas": [d.as_dict() for d in deltas],
        "consumers": consumer_counts,
    }

    if args.json:
        out_text = json.dumps(report, indent=2)
    else:
        lines: list[str] = []
        lines.append(f"# Spec impact — {args.patch.name}")
        lines.append(f"# Target: {args.theory}")
        lines.append(f"# Gate: {'PASS ✓' if gate_pass else 'FAIL ✗'}")
        if wall_pct is not None:
            wall_emoji = "✓" if wall_gate_pass else "✗"
            lines.append(f"# File wall: {args.baseline_wall} ms → {args.trial_wall} ms "
                         f"({wall_pct:+.1f}%) {wall_emoji} "
                         f"(threshold ≤ +{WALL_REGRESSION_THRESHOLD_PCT}%)")
        lines.append(f"# Total SSS: {total_sss:.1f}")
        if derivability_needed:
            d_emoji = "✓" if deriv["present"] else "⚠"
            n_wit = len(deriv["witnesses"])
            lines.append(
                f"# Derivability witness ({n_wit} found): {d_emoji} "
                f"{'present' if deriv['present'] else 'ABSENT — Step 4.5 incomplete'}"
            )
        lines.append("")
        lines.append("## Lemma deltas")
        lines.append("| Lemma | Classification | Verdict | SSS | Notes |")
        lines.append("|---|---|---|---:|---|")
        for d in deltas:
            notes = "; ".join(d.notes) if d.notes else ""
            lines.append(
                f"| `{d.name}` | {d.classification} | **{d.verdict}** "
                f"| {d.strength_score:.1f} | {notes} |"
            )
        if any(d.diff for d in deltas):
            lines.append("")
            lines.append("## Structural diffs (authoritative — verdict driver)")
            lines.append("| Lemma | Pre conj −/+ | Post conj −/+ | Frame+ | ≤→= |")
            lines.append("|---|---:|---:|---:|---:|")
            for d in deltas:
                if not d.diff:
                    continue
                lines.append(
                    f"| `{d.name}` "
                    f"| -{d.diff.removed_premises} / +{d.diff.added_premises} "
                    f"| -{d.diff.removed_post_facts} / +{d.diff.added_post_facts} "
                    f"| {d.diff.added_frame_facts} "
                    f"| {d.diff.swap_le_to_eq} |"
                )
        if consumer_counts:
            lines.append("")
            lines.append("## Consumer counts (Tier 2, UPPER BOUND)")
            lines.append("| Lemma | Lines | Files |")
            lines.append("|---|---:|---:|")
            for name, c in consumer_counts.items():
                lines.append(f"| `{name}` | {c['lines']} | {c['files']} |")
        out_text = "\n".join(lines) + "\n"

    if args.append_to:
        with open(args.append_to, "a", encoding="utf-8") as f:
            f.write("\n" + out_text)
    print(out_text)

    # ----- simplified measurement.json (rule-5 audit bundle) ------------
    if args.measurement_out:
        # Pick a representative verdict for the patch as a whole.
        # Priority: monotone-strengthen > premise-weaken / postcond-strengthen
        # > additive > weakening / rewrite / removal / noop.
        priority = [
            VERDICT_MONOTONE_STRENGTHEN, VERDICT_PREMISE_WEAKEN,
            VERDICT_POSTCOND_STRENGTHEN, VERDICT_ADDITIVE,
            VERDICT_WEAKENING, VERDICT_REWRITE, VERDICT_REMOVAL,
            VERDICT_NOOP,
        ]
        delta_verdicts = [d.verdict for d in deltas]
        representative_verdict = next(
            (v for v in priority if v in delta_verdicts),
            "noop",
        )
        # Total consumer count (sum of unique consumers across deltas).
        total_consumer_lines = sum(
            c.get("lines", 0) for c in consumer_counts.values()
        )
        total_consumer_files = sum(
            c.get("files", 0) for c in consumer_counts.values()
        )
        # Derive session from theory file path if recognizable.
        session = "?"
        for prefix, sess in (
            ("spec/abstract/",            "ASpec"),
            ("spec/cspec/",               "CSpec"),
            ("proof/invariant-abstract/", "AInvs"),
            ("proof/refine/",             "Refine"),
            ("proof/crefine/",            "CRefine"),
            ("proof/access-control/",     "Access"),
            ("proof/infoflow/",           "InfoFlow"),
            ("proof/drefine/",            "DRefine"),
            ("proof/bisim/",              "Bisim"),
        ):
            if prefix in str(args.theory):
                session = sess
                break

        delta_pct = wall_pct  # alias for template compatibility

        measurement = {
            "session": session,
            "baseline_wall_ms": args.baseline_wall,
            "trial_wall_ms": args.trial_wall,
            "delta_pct": round(delta_pct, 2) if delta_pct is not None else None,
            "wall_gate_pass": wall_gate_pass,
            "baseline_ref": f"reports/golden-baseline/walls.json#{session}",
            "session_rebuild_done": False,
            "consumers_lines": total_consumer_lines,
            "consumers_files": total_consumer_files,
            "impact_verdict": representative_verdict,
            "strength_score": total_sss,
            "witness_present": bool(deriv["present"]),
            "witness_advisory_pass": derivability_advisory_pass,
            "gate_pass": gate_pass,
            "has_weakening": has_weakening,
        }
        args.measurement_out.write_text(
            json.dumps(measurement, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[measurement.json written to {args.measurement_out}]",
              file=sys.stderr)

    return 0 if gate_pass else 1


if __name__ == "__main__":
    sys.exit(main())
