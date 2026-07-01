#!/usr/bin/env python3
r"""
tools/critical_path/spec_impact.py

Post-patch impact metric. Verifies that a spec-strengthening patch is
genuinely a strengthening (no `weakening` verdict), measures Tier 1
statement deltas and Tier 2 consumer counts, and emits a verdict gate.

REQUIRED by isabelle_prover_spec SKILL.md Step 4 (MANDATORY).

Workflow contract:
  1. Run `check-theory.sh --patch <patch>` on the target file FIRST.
     If that fails, no point running this tool.
  2. Run this tool against (patch, theory_file).
     Verdict must be one of: premise-weaken / monotone-strengthen /
     postcond-strengthen / additive. `weakening` = HARD STOP.
  3. Then proceed to apply.

Usage:
  spec_impact.py <patch.txt> <theory.thy>
                 [--baseline-wall MS] [--trial-wall MS]
                 [--tree DIR]
                 [--append-to FILE] [--json]

The patch file uses check-theory.sh format:
  <start_line> <end_line>
  <replacement text spanning one or more lines>
  ---
  <start_line> <end_line>
  <replacement text>
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path

# Reuse the lemma extractor from spec_strengthen_scan.py to keep parsing
# semantics consistent.
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
from spec_strengthen_scan import (   # noqa: E402
    parse_thy_lemmas, Lemma,
)


VERDICT_PREMISE_WEAKEN     = "premise-weaken"
VERDICT_MONOTONE_STRENGTHEN = "monotone-strengthen"
VERDICT_POSTCOND_STRENGTHEN = "postcond-strengthen"
VERDICT_ADDITIVE            = "additive"
VERDICT_REMOVAL             = "removal"
VERDICT_REWRITE             = "rewrite"
VERDICT_WEAKENING           = "weakening"
VERDICT_NOOP                = "noop"

# Acceptable verdicts (Step 4 gate of the spec sub-skill).
ACCEPTABLE_VERDICTS = {
    VERDICT_PREMISE_WEAKEN,
    VERDICT_MONOTONE_STRENGTHEN,
    VERDICT_POSTCOND_STRENGTHEN,
    VERDICT_ADDITIVE,
}


# ---------- Patch parsing ----------------------------------------------------

@dataclass
class PatchHunk:
    start: int  # 1-based, inclusive
    end: int    # 1-based, inclusive
    text: str   # replacement text (no trailing newline)


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
    # Apply in reverse line order so earlier hunks' line numbers stay valid
    for h in sorted(hunks, key=lambda x: -x.start):
        # Replace lines[start-1:end] (inclusive) with h.text + "\n"
        replacement = h.text
        if not replacement.endswith("\n"):
            replacement += "\n"
        lines[h.start - 1: h.end] = [replacement]
    return "".join(lines)


# ---------- Tier 1 metrics ---------------------------------------------------

def count_tokens(s: str) -> int:
    return len(re.findall(r"[A-Za-z_][A-Za-z_0-9']*", s))


def count_premise_conjuncts(pre: str) -> int:
    """Estimate premise conjunct count by splitting on `and` / `\\<and>`."""
    if not pre.strip():
        return 0
    # Top-level conjunction separators in l4v's Hoare-triple precondition
    # notation: " and " (predicate combinator), `\<and>` (logical and).
    # We split conservatively; the count is a proxy.
    splits = re.split(r"\s+and\s+|\\<and>", pre)
    return sum(1 for s in splits if s.strip())


def has_state_reference(post: str) -> bool:
    r"""True if postcondition references state — \<lambda> ... s. <body
    containing s>. Distinguish from \<lambda> _ s. <body NOT containing s>
    which is technically a state-free predicate wrapped in λ-binder."""
    # Match a lambda binder grabbing an `s` argument
    m = re.search(r"\\<lambda>\s*[^.]*?\bs\b[^.]*?\.\s*(.*)", post,
                  re.DOTALL)
    if m:
        body = m.group(1)
        return bool(re.search(r"\bs\b", body))
    # No lambda — check if `s` appears free
    if re.search(r"\bs\b", post):
        return True
    return False


def proof_body_line_count(theory_text: str, lemma_name: str) -> int:
    """Count lines of proof body for a lemma. Crude — from the lemma
    header to the next `done`/`qed` or the next lemma header."""
    text = theory_text
    m = re.search(rf"^\s*(?:lemma|theorem|corollary)s?\s+"
                  rf"{re.escape(lemma_name)}\b",
                  text, re.MULTILINE)
    if not m:
        return 0
    start = m.end()
    # Find proof end: `done`, `qed`, or sole `by ... )` line
    rest = text[start:]
    end_m = re.search(r"^\s*(?:done|qed)\s*$|^\s*by\b.*[^\\]\s*$",
                      rest, re.MULTILINE)
    if not end_m:
        return 0
    chunk = rest[: end_m.end()]
    # Count proof lines (apply/by/...)
    return sum(1 for ln in chunk.splitlines()
               if re.match(r"^\s*(?:apply|by|using|supply|unfolding|"
                           r"subgoal|have|show|then|from|with|note|moreover|"
                           r"ultimately|done|qed|proof)\b", ln))


@dataclass
class LemmaMetrics:
    name: str
    line: int
    pre_tokens: int
    post_tokens: int
    premise_count: int
    state_dependent: bool
    proof_body_lines: int

    def as_dict(self):
        return asdict(self)


@dataclass
class LemmaDelta:
    name: str
    classification: str  # added | removed | modified
    before: LemmaMetrics | None
    after: LemmaMetrics | None
    notes: list[str] = field(default_factory=list)
    verdict: str = ""
    file: str = ""

    def as_dict(self):
        return {
            "name": self.name,
            "classification": self.classification,
            "before": self.before.as_dict() if self.before else None,
            "after": self.after.as_dict() if self.after else None,
            "notes": list(self.notes),
            "verdict": self.verdict,
            "file": self.file,
        }


def make_metrics(theory_text: str, l: Lemma) -> LemmaMetrics:
    return LemmaMetrics(
        name=l.name,
        line=l.line,
        pre_tokens=count_tokens(l.pre),
        post_tokens=count_tokens(l.post),
        premise_count=count_premise_conjuncts(l.pre),
        state_dependent=has_state_reference(l.post),
        proof_body_lines=proof_body_line_count(theory_text, l.name),
    )


# ---------- Verdict classifier ----------------------------------------------

def classify_delta(d: LemmaDelta) -> str:
    if d.classification == "added":
        return VERDICT_ADDITIVE
    if d.classification == "removed":
        return VERDICT_REMOVAL
    # Modified — compare before/after
    b, a = d.before, d.after
    if not b or not a:
        return VERDICT_REWRITE
    premise_dropped = b.premise_count > a.premise_count
    premise_grew = b.premise_count < a.premise_count
    postcond_grew = b.post_tokens < a.post_tokens
    postcond_shrunk = b.post_tokens > a.post_tokens
    gained_state = (not b.state_dependent) and a.state_dependent
    lost_state = b.state_dependent and (not a.state_dependent)

    # WEAKENING: premise grew without postcond grow OR postcond shrunk
    if premise_grew and not postcond_grew:
        return VERDICT_WEAKENING
    if postcond_shrunk and not premise_dropped:
        return VERDICT_WEAKENING
    if lost_state and not premise_dropped and not postcond_grew:
        return VERDICT_WEAKENING

    if premise_dropped and (postcond_grew or gained_state):
        return VERDICT_MONOTONE_STRENGTHEN
    if premise_dropped:
        return VERDICT_PREMISE_WEAKEN
    if postcond_grew or gained_state:
        return VERDICT_POSTCOND_STRENGTHEN

    # Same statement shape but proof body changed
    if b.proof_body_lines != a.proof_body_lines:
        return VERDICT_REWRITE
    return VERDICT_NOOP


# ---------- Tier 2: consumer count ------------------------------------------

def grep_consumers(name: str, tree: Path) -> tuple[int, int]:
    """Return (line_count, file_count) for `\\bname\\b` matches under tree.

    Uses `grep -rln` for files and `grep -rc` for line totals. Safe:
    only reads filesystem, no network.
    """
    if not tree.exists():
        return (0, 0)
    try:
        files_out = subprocess.run(
            ["grep", "-rln", "-E", rf"\b{re.escape(name)}\b", str(tree)],
            check=False, capture_output=True, text=True, timeout=60,
        )
        file_count = sum(1 for ln in files_out.stdout.splitlines() if ln)
        lines_out = subprocess.run(
            ["grep", "-rn", "-E", rf"\b{re.escape(name)}\b", str(tree)],
            check=False, capture_output=True, text=True, timeout=60,
        )
        line_count = sum(1 for ln in lines_out.stdout.splitlines() if ln)
        return (line_count, file_count)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return (0, 0)


# ---------- Main -------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Spec-impact metric (Step 4 MANDATORY for spec sub-skill).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("patch", type=Path)
    ap.add_argument("theory", type=Path)
    ap.add_argument("--baseline-wall", type=int, default=None,
                    help="Baseline wall (ms) from check-theory.sh.")
    ap.add_argument("--trial-wall", type=int, default=None,
                    help="Trial wall (ms) from check-theory.sh --patch.")
    ap.add_argument("--tree", type=Path,
                    default=Path("verification/l4v/proof"),
                    help="Tree to grep for consumer counts.")
    ap.add_argument("--append-to", type=Path, default=None,
                    help="Append report to this file (mode: a).")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not args.theory.exists():
        print(f"ERROR: theory file not found: {args.theory}", file=sys.stderr)
        return 2
    if not args.patch.exists():
        print(f"ERROR: patch file not found: {args.patch}", file=sys.stderr)
        return 2

    theory_text = args.theory.read_text(encoding="utf-8", errors="replace")
    hunks = parse_patch(args.patch)
    after_text = apply_patch_in_memory(theory_text, hunks)

    # Write the patched file to a temp path so parse_thy_lemmas can read it
    # (parse_thy_lemmas takes a Path).
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
    all_names = set(before_lemmas) | set(after_lemmas)
    for name in sorted(all_names):
        b = before_lemmas.get(name)
        a = after_lemmas.get(name)
        if b and not a:
            deltas.append(LemmaDelta(
                name=name, classification="removed",
                before=make_metrics(theory_text, b), after=None,
                file=str(args.theory),
            ))
        elif a and not b:
            deltas.append(LemmaDelta(
                name=name, classification="added",
                before=None, after=make_metrics(after_text, a),
                file=str(args.theory),
            ))
        else:
            assert a and b
            if (a.pre, a.post, a.body) == (b.pre, b.post, b.body):
                continue  # no change to this lemma
            deltas.append(LemmaDelta(
                name=name, classification="modified",
                before=make_metrics(theory_text, b),
                after=make_metrics(after_text, a),
                file=str(args.theory),
            ))

    for d in deltas:
        d.verdict = classify_delta(d)
        if d.before and d.after:
            if d.before.premise_count != d.after.premise_count:
                d.notes.append(
                    f"premise count {d.before.premise_count} → {d.after.premise_count}"
                )
            if d.before.post_tokens != d.after.post_tokens:
                d.notes.append(
                    f"postcond tokens {d.before.post_tokens} → {d.after.post_tokens}"
                )
            if d.before.state_dependent != d.after.state_dependent:
                d.notes.append(
                    f"state-dep {d.before.state_dependent} → {d.after.state_dependent}"
                )

    # Tier 2: consumer counts (just for added/modified — skip removed)
    consumer_counts: dict[str, tuple[int, int]] = {}
    for d in deltas:
        if d.classification in ("added", "modified"):
            consumer_counts[d.name] = grep_consumers(d.name, args.tree)

    # Gate check
    has_weakening = any(d.verdict == VERDICT_WEAKENING for d in deltas)
    has_acceptable = any(d.verdict in ACCEPTABLE_VERDICTS for d in deltas)
    gate_pass = (not has_weakening) and has_acceptable

    # Wall delta
    wall_pct = None
    if args.baseline_wall and args.trial_wall:
        wall_pct = (args.trial_wall - args.baseline_wall) * 100.0 / args.baseline_wall

    # Output
    report: dict = {
        "patch": str(args.patch),
        "theory": str(args.theory),
        "baseline_wall_ms": args.baseline_wall,
        "trial_wall_ms": args.trial_wall,
        "wall_pct": wall_pct,
        "gate_pass": gate_pass,
        "has_weakening": has_weakening,
        "deltas": [d.as_dict() for d in deltas],
        "consumers": {
            name: {"lines": lc, "files": fc}
            for name, (lc, fc) in consumer_counts.items()
        },
    }

    if args.json:
        out_text = json.dumps(report, indent=2)
    else:
        lines: list[str] = []
        lines.append(f"# Spec impact — {args.patch.name}")
        lines.append(f"# Target: {args.theory}")
        lines.append(f"# Gate: {'PASS ✓' if gate_pass else 'FAIL ✗'}")
        if wall_pct is not None:
            lines.append(f"# File wall: {args.baseline_wall} ms → {args.trial_wall} ms ({wall_pct:+.1f}%)")
        lines.append("")
        lines.append("## Lemma deltas")
        lines.append("| Lemma | Classification | Verdict | Notes |")
        lines.append("|---|---|---|---|")
        for d in deltas:
            notes = "; ".join(d.notes) if d.notes else ""
            lines.append(f"| `{d.name}` | {d.classification} | **{d.verdict}** | {notes} |")
        if consumer_counts:
            lines.append("")
            lines.append("## Consumer counts (Tier 2 surface)")
            lines.append("| Lemma | Lines | Files |")
            lines.append("|---|---:|---:|")
            for name, (lc, fc) in consumer_counts.items():
                lines.append(f"| `{name}` | {lc} | {fc} |")
        out_text = "\n".join(lines) + "\n"

    if args.append_to:
        with open(args.append_to, "a", encoding="utf-8") as f:
            f.write("\n" + out_text)
    print(out_text)

    return 0 if gate_pass else 1


if __name__ == "__main__":
    sys.exit(main())
