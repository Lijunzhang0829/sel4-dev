#!/usr/bin/env python3
r"""
tools/critical_path/spec_impact.py — spec-strengthen impact metric.

Required by `isabelle_prover_spec` SKILL Step 4 (MANDATORY).

What this script does (and does NOT do):

- DOES: structural-diff a patch's lemma deltas, classify each into a
  verdict (premise-weaken / monotone-strengthen / postcond-strengthen /
  additive / removal / rewrite / weakening / noop), compute a Semantic
  Strength Score (SSS), check wall-time regression vs baseline, and
  detect whether the patch contains a Step-4.5 derivability witness.
- DOES NOT: prove A_old derivable from A_new. That is check-theory.sh's
  job (the `<name>_old` aux lemma must be inside the patch; verifying
  it is the Step-3 check-theory.sh run).

Acceptance gate (Tier 1 lemmas):
  1. No `weakening` verdict on any delta.
  2. At least one delta with verdict in
     {premise-weaken, monotone-strengthen, postcond-strengthen, additive}.
  3. trial_wall_ms ≤ baseline_wall_ms × 1.30 (when both supplied).
  4. Derivability witness present in patch (warning only — actual
     verification happens at check-theory.sh).

Exit code 0 iff gates 1-3 pass. Gate 4 is advisory.

Usage:
  spec_impact.py <patch.txt> <theory.thy>
                 [--baseline-wall MS] [--trial-wall MS]
                 [--tree DIR]
                 [--append-to FILE] [--json]
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
VERDICT_UNCLASSIFIED_ADD    = "unclassified-add"  # added but not B/G/E shape

ACCEPTABLE_VERDICTS = {
    VERDICT_PREMISE_WEAKEN,
    VERDICT_MONOTONE_STRENGTHEN,
    VERDICT_POSTCOND_STRENGTHEN,
    VERDICT_ADDITIVE,
}

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
    r"\bP\s*\(\s*\w+\s+s'?\s*\)"           # P (accessor s)
    r"|=\s*\w+\s+s\d*"                      # = accessor s (preservation form)
)
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


# ---------- Pattern shape detection for added lemmas -------------------------

def detect_added_pattern(after: Lemma) -> str | None:
    """Classify a newly-added Hoare-triple lemma as Pattern B / G / E.

    Returns 'B', 'G', 'E', or None.
    """
    if not after or not after.post:
        return None
    op = after.op or ""

    # Pattern E: compound _invs.
    if after.name.endswith("_invs") or after.name.endswith("_invs_minor"):
        post_body = extract_post_body(after.post)
        if re.search(r"\binvs\b", post_body):
            return "E"

    # Pattern B: functional postcond — set_X / update_X with `accessor s = <arg>`.
    if re.match(r"(?:set|update|modify)_", op):
        post_body = extract_post_body(after.post)
        if re.search(r"\w+\s+s\s*=", post_body):
            return "B"

    # Pattern G: frame — pre and post both `P (accessor s)`.
    pre_body = after.pre
    post_body = extract_post_body(after.post)
    if FRAME_CONJUNCT_RE.search(pre_body) and FRAME_CONJUNCT_RE.search(post_body):
        return "G"

    return None


# ---------- Verdict classifier ----------------------------------------------

def classify_delta(d: LemmaDelta) -> tuple[str, list[str]]:
    """Returns (verdict, extra_notes)."""
    notes: list[str] = []

    if d.classification == "added":
        pat = detect_added_pattern(d.after_lemma) if d.after_lemma else None
        if pat:
            notes.append(f"added lemma matches Pattern {pat} shape")
            return VERDICT_ADDITIVE, notes
        notes.append("added lemma does NOT match any of Pattern B/G/E shape — "
                     "not a strengthening; treat as auxiliary")
        return VERDICT_UNCLASSIFIED_ADD, notes

    if d.classification == "removed":
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

def grep_consumers(name: str, tree: Path,
                   self_file: Path | None = None,
                   self_line: int = 0) -> tuple[int, int]:
    """Return (line_count, file_count) of `\\bname\\b` matches under tree.

    Note: this is an UPPER BOUND. Includes the lemma's own definition line
    when self_file is None. To exclude self, pass (self_file, self_line).
    """
    if not tree.exists():
        return (0, 0)
    try:
        out = subprocess.run(
            ["grep", "-rn", "-E", rf"\b{re.escape(name)}\b", str(tree)],
            check=False, capture_output=True, text=True, timeout=60,
        )
        lines = [ln for ln in out.stdout.splitlines() if ln]
        # Filter out lemma's own definition line(s)
        if self_file is not None:
            sf = str(self_file)
            lines = [
                ln for ln in lines
                if not ln.startswith(f"{sf}:")
                or not re.match(rf"^{re.escape(sf)}:{self_line}:",
                                ln) is None and "lemma " in ln
            ]
        line_count = len(lines)
        file_count = len({ln.split(":", 1)[0] for ln in lines})
        return (line_count, file_count)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return (0, 0)


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
    ap.add_argument("--append-to", type=Path, default=None)
    ap.add_argument("--json", action="store_true")
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

    return 0 if gate_pass else 1


if __name__ == "__main__":
    sys.exit(main())
