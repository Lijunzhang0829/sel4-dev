#!/usr/bin/env python3
r"""
tools/critical_path/spec_strengthen_scan.py

Scan one or more .thy files (typically under
verification/l4v/proof/invariant-abstract/) for candidate spec
strengthenings. Emits patterns A/B/C as a ranked list.

Patterns:
  A — Paired weak/strong postcondition: same operation in Hoare body,
      one postcondition implies the other via a known table
      (real_cte_at→cte_at, invs→valid_objs, valid_pspace→valid_objs,
      valid_cap→wellformed_cap, etc.). Weak form may be redundant.

  B — set_*/update_* with no functional postcondition: an op of the form
      set_X v / update_X f has only preservation-shaped lemmas
      (⟨P⟩ set_X _ ⟨λ_. P⟩), no functional shape
      (⟨⊤⟩ set_X v ⟨λ_ s. X s = v⟩).

  C — Possibly-unused premise: precondition mentions valid_objs / invs /
      valid_pspace but postcondition is purely structural (cte_at /
      obj_at / type_at / is_*_cap). Premise may be droppable.

Pattern D (loose bound `≤` → `=`) and E (compound `_invs`) are NOT
detected here — D requires semantic understanding of the operation,
E requires per-component coverage analysis (see
tools/critical_path/spec_coverage_matrix.py if available).

Output: stdout — markdown table per pattern, ordered by file:line.
Use --json for structured output. Use --pattern A|B|C to limit to
one pattern.

Usage:
  spec_strengthen_scan.py <file_or_dir> [--pattern A|B|C|all] [--json]
                          [--limit N] [--out FILE]

Limitations:
- Regex-based; cannot follow imports / type-check.
- Cross-file Pattern A pairing has ~70% false-positive rate
  (incomparable preconditions). Use per-file scans for highest signal.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

# Predicate-implication table for Pattern A. Each pair (strong, weak)
# means strong ⟹ weak. Sourced from l4v's named implication lemmas
# (real_cte_at_cte, invs_valid_objs, valid_pspace_def [valid_objs is
# a conjunct], valid_cap_def2, etc.).
IMPLICATION_TABLE = [
    ("real_cte_at", "cte_at"),
    ("invs", "valid_objs"),
    ("invs", "valid_pspace"),
    ("invs", "valid_mdb"),
    ("invs", "valid_idle"),
    ("invs", "cur_tcb"),
    ("invs", "valid_irq_node"),
    ("invs", "valid_irq_states"),
    ("valid_pspace", "valid_objs"),
    ("valid_pspace", "pspace_aligned"),
    ("valid_pspace", "pspace_distinct"),
    ("valid_cap", "wellformed_cap"),
    ("valid_cap", "cap_aligned"),
    ("real_cte_at'", "cte_at'"),
    ("invs'", "valid_objs'"),
    ("invs'", "valid_pspace'"),
]

# Structural-postcondition predicates that don't usually need
# invariant-level preconditions to discharge. Used by Pattern C.
STRUCTURAL_POSTCOND = {
    "cte_at", "real_cte_at", "obj_at", "typ_at",
    "ko_at", "ep_at", "ntfn_at", "tcb_at",
    "is_cnode_cap", "is_thread_cap", "is_ep_cap",
    "is_ntfn_cap", "is_reply_cap",
}

# Tokens that are typically local variables in Hoare bodies; exclude
# from op-extraction to avoid confusing `s`, `rv` etc. with the op name.
LOCAL_TOKENS = {
    "s", "s'", "rv", "rv'", "x", "y", "z", "p", "p'",
    "t", "t'", "r", "r'", "P", "Q", "f", "g", "F", "G",
    "obj", "ko", "cap", "cte", "ptr", "src", "dest",
    "args", "v", "w", "i", "j", "k", "n", "m", "b", "c",
    "do", "od", "let", "in", "if", "then", "else",
    "case", "of", "True", "False",
}


@dataclass
class Lemma:
    file: str
    line: int  # 1-based
    name: str
    pre: str
    body: str
    post: str
    op: str

    def as_dict(self):
        return asdict(self)


@dataclass
class Finding:
    pattern: str  # "A" | "B" | "C"
    file: str
    line: int
    name: str
    note: str
    severity: str = "med"
    companion: dict | None = None  # for Pattern A
    pre: str = ""
    post: str = ""

    def as_dict(self):
        d = asdict(self)
        if d["companion"] is None:
            d.pop("companion")
        return d


# ------ Parsing ------------------------------------------------------------

# Match `lemma NAME` or `lemma NAME [attrs]:` — capture name.
LEMMA_HEADER_RE = re.compile(
    r"^\s*(?:lemma|theorem|corollary)s?\s+"
    r"(?P<name>[A-Za-z_][A-Za-z_0-9']*)",
    re.MULTILINE,
)

# Match a Hoare triple body inside a lemma statement (single-line or
# multi-line). Captures pre / body / post. The body MUST be terminated
# by `\<rbrace>` followed by optional `,` / `-` for the validE variants.
HOARE_TRIPLE_RE = re.compile(
    r"""\\<lbrace>             # opening pre brace
        (?P<pre>.*?)           # precondition (non-greedy)
        \\<rbrace>             # closing pre brace
        (?P<body>.*?)          # operation body (non-greedy)
        \\<lbrace>             # opening post brace
        (?P<post>.*?)          # postcondition
        \\<rbrace>             # closing post brace
    """,
    re.VERBOSE | re.DOTALL,
)


def _line_at(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _extract_op(body: str) -> str:
    """Pick the first 'kernel-op-shaped' identifier in the Hoare body.

    Skips short locals (≤2 chars) and the hard-coded LOCAL_TOKENS set.
    Prefers tokens with underscore (most l4v ops are snake_case).
    """
    tokens = re.findall(r"[A-Za-z_][A-Za-z_0-9']*", body)
    for t in tokens:
        if t in LOCAL_TOKENS:
            continue
        if len(t) <= 2 and "_" not in t:
            continue
        if "_" in t or len(t) >= 5:
            return t
    # Fallback: any non-local token
    for t in tokens:
        if t not in LOCAL_TOKENS:
            return t
    return ""


def parse_thy_lemmas(thy_path: Path) -> list[Lemma]:
    text = thy_path.read_text(encoding="utf-8", errors="replace")
    lemmas: list[Lemma] = []
    # Iterate over lemma headers, then look for the first Hoare triple
    # in the statement window (up to the next `apply` / `by` / next
    # lemma header).
    headers = list(LEMMA_HEADER_RE.finditer(text))
    for i, m in enumerate(headers):
        name = m.group("name")
        start = m.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        window = text[start:end]
        # Truncate window at first proof tactic keyword (lemma statement
        # ends before the proof body).
        proof_kw = re.search(
            r"^\s*(?:apply|by|proof|done|qed|sorry|oops)\b",
            window, re.MULTILINE,
        )
        if proof_kw:
            window = window[: proof_kw.start()]
        htm = HOARE_TRIPLE_RE.search(window)
        if not htm:
            continue
        pre = htm.group("pre").strip()
        body = htm.group("body").strip()
        post = htm.group("post").strip()
        line = _line_at(text, m.start())
        lemmas.append(Lemma(
            file=str(thy_path),
            line=line,
            name=name,
            pre=pre,
            body=body,
            post=post,
            op=_extract_op(body),
        ))
    return lemmas


# ------ Pattern detectors --------------------------------------------------

def detect_A(lemmas: list[Lemma]) -> list[Finding]:
    """Paired weak/strong via implication table."""
    findings: list[Finding] = []
    # Group by (op, op-args head fingerprint)
    by_op: dict[str, list[Lemma]] = {}
    for l in lemmas:
        if not l.op:
            continue
        by_op.setdefault(l.op, []).append(l)
    for op, group in by_op.items():
        if len(group) < 2:
            continue
        # For each pair (a, b) check if a.post strengthens b.post
        for a in group:
            for b in group:
                if a is b:
                    continue
                for strong, weak in IMPLICATION_TABLE:
                    if (re.search(rf"\b{re.escape(strong)}\b", a.post)
                            and re.search(rf"\b{re.escape(weak)}\b", b.post)
                            and not re.search(rf"\b{re.escape(strong)}\b", b.post)):
                        # b is the weak (Pattern A candidate)
                        findings.append(Finding(
                            pattern="A",
                            file=b.file, line=b.line, name=b.name,
                            note=(
                                f"`{weak}` postcondition is implied by "
                                f"`{strong}` (companion at line {a.line})."
                            ),
                            severity="med",
                            companion={
                                "name": a.name, "line": a.line, "post": a.post[:80],
                            },
                            pre=b.pre[:120], post=b.post[:120],
                        ))
                        break
    # Dedup by (file, line, name)
    seen: set[tuple[str, int, str]] = set()
    out: list[Finding] = []
    for f in findings:
        k = (f.file, f.line, f.name)
        if k in seen:
            continue
        seen.add(k)
        out.append(f)
    return out


# Functional-postcondition shape: post mentions equality and references
# the body op's argument. Detect absence by checking ops that match
# set_*/update_* and have no lemma with `<...>= ...` in postcondition.
B_OP_PREFIX_RE = re.compile(r"^(?:set|update|modify|do)_")

def detect_B(lemmas: list[Lemma]) -> list[Finding]:
    """set_*/update_* missing functional postcondition."""
    findings: list[Finding] = []
    by_op: dict[str, list[Lemma]] = {}
    for l in lemmas:
        if not l.op or not B_OP_PREFIX_RE.match(l.op):
            continue
        by_op.setdefault(l.op, []).append(l)
    for op, group in by_op.items():
        # Has any lemma with a functional postcondition?
        has_functional = False
        for l in group:
            # Functional shape heuristics: post contains "= " (in a
            # \<lambda>_ s. … s = …) and references the body argument.
            if re.search(r"\\<lambda>\s*_\s+s\.\s+.*=", l.post):
                has_functional = True
                break
            if "= v" in l.post or "= t" in l.post or "= x" in l.post:
                has_functional = True
                break
        if has_functional:
            continue
        # Emit one finding pointing at the FIRST preservation lemma
        l = group[0]
        findings.append(Finding(
            pattern="B",
            file=l.file, line=l.line, name=l.name,
            note=(
                f"`{op}` has preservation lemmas but no functional "
                f"postcondition. Consider adding "
                f"`\\<lbrace>\\<top>\\<rbrace> {op} v "
                f"\\<lbrace>\\<lambda>_ s. <accessor> s = v\\<rbrace>`."
            ),
            severity="low",
            pre=l.pre[:120], post=l.post[:120],
        ))
    return findings


PREMISE_INV_RE = re.compile(r"\b(?:valid_objs|invs|valid_pspace|invs')\b")

def detect_C(lemmas: list[Lemma]) -> list[Finding]:
    """Possibly-unused premise: invariant-level pre but structural post."""
    findings: list[Finding] = []
    for l in lemmas:
        if not PREMISE_INV_RE.search(l.pre):
            continue
        # Check whether postcondition is purely structural.
        # Look for any non-structural predicate first (cheap negative).
        post_tokens = set(re.findall(r"[A-Za-z_][A-Za-z_0-9']*", l.post))
        # Postcondition uses structural-only predicates?
        has_invariant_in_post = bool(
            re.search(r"\b(?:invs|valid_objs|valid_pspace|valid_mdb|"
                      r"valid_idle|valid_cap|valid_state)\b", l.post)
        )
        if has_invariant_in_post:
            # Premise IS plausibly used (post needs it too)
            continue
        is_structural = any(p in post_tokens for p in STRUCTURAL_POSTCOND)
        if not is_structural:
            continue
        # Emit candidate
        findings.append(Finding(
            pattern="C",
            file=l.file, line=l.line, name=l.name,
            note=(
                f"Premise contains invariant-level conjuncts "
                f"(`{PREMISE_INV_RE.search(l.pre).group(0)}`) but "
                f"postcondition is structural. Try dropping; "
                f"check-theory.sh --patch settles in ~60s."
            ),
            severity="med",
            pre=l.pre[:200], post=l.post[:120],
        ))
    return findings


# ------ Driver -------------------------------------------------------------

def iter_thy_files(path: Path) -> Iterable[Path]:
    if path.is_file() and path.suffix == ".thy":
        yield path
        return
    if path.is_dir():
        for thy in sorted(path.rglob("*.thy")):
            yield thy


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Scan .thy files for spec-strengthening candidates.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("path", type=Path, help="A .thy file or directory.")
    ap.add_argument("--pattern", choices=["A", "B", "C", "all"], default="all")
    ap.add_argument("--limit", type=int, default=50,
                    help="Max findings per pattern.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    all_lemmas: list[Lemma] = []
    for thy in iter_thy_files(args.path):
        try:
            all_lemmas.extend(parse_thy_lemmas(thy))
        except Exception as e:
            print(f"warn: failed to parse {thy}: {e}", file=sys.stderr)

    if not all_lemmas:
        print(f"No Hoare-triple lemmas found in {args.path}", file=sys.stderr)
        return 0

    findings_by_pattern: dict[str, list[Finding]] = {}
    patterns = ["A", "B", "C"] if args.pattern == "all" else [args.pattern]
    for p in patterns:
        if p == "A":
            findings_by_pattern[p] = detect_A(all_lemmas)
        elif p == "B":
            findings_by_pattern[p] = detect_B(all_lemmas)
        elif p == "C":
            findings_by_pattern[p] = detect_C(all_lemmas)
    # Apply limit
    for p in findings_by_pattern:
        findings_by_pattern[p] = findings_by_pattern[p][: args.limit]

    if args.json:
        out_text = json.dumps(
            {p: [f.as_dict() for f in fs]
             for p, fs in findings_by_pattern.items()},
            indent=2,
        )
    else:
        lines: list[str] = []
        lines.append(f"# Scan of {args.path}")
        lines.append(f"# {sum(len(v) for v in findings_by_pattern.values())} findings, {len(all_lemmas)} lemmas examined")
        lines.append("")
        for p, fs in findings_by_pattern.items():
            lines.append(f"## Pattern {p} — {len(fs)} candidates")
            lines.append("")
            if not fs:
                lines.append("  (no findings)")
                lines.append("")
                continue
            lines.append("| File:Line | Lemma | Note |")
            lines.append("|---|---|---|")
            for f in fs:
                rel = Path(f.file).name
                lines.append(f"| `{rel}:{f.line}` | `{f.name}` | {f.note} |")
            lines.append("")
        out_text = "\n".join(lines)

    if args.out:
        args.out.write_text(out_text, encoding="utf-8")
    else:
        print(out_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
