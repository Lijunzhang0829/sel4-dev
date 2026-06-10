r"""
tools/spec_strengthen/spec_strengthen_scan.py

INTERNAL library for the spec-strengthen sub-skill. NOT a CLI tool —
the user-facing entry point is `spec_candidates.py` (discovery) and
`spec_impact.py` (verdict). Both import from this module.

Exports:
- `Lemma`            — dataclass for one Hoare-triple lemma extracted
                       from a .thy file.
- `Finding`          — one detector hit on a `Lemma`.
- `parse_thy_lemmas(path) -> list[Lemma]`
                     — regex-based extractor, recognizes both full
                       `⟨P⟩ body ⟨Q⟩` and shorthand `body ⟨Q⟩` Hoare
                       triples, plus `lemma shows`-bundle syntax.
- `iter_thy_files(path)`
                     — iterator over .thy files under a path.
- Two detector functions returning `list[Finding]`:
    * `detect_paired_chain(lemmas)` — Pattern A.
        Same-op lemma pairs where one postcond implies the other.
        Heuristic + manual review required (~70% FP from incomparable
        preconditions). evidence: "heuristic+manual" — emit to Tier 3.
    * `detect_unused_premise(lemmas)` — Pattern C.
        Hoare-triple lemmas whose precondition mentions an
        invariant-level predicate (valid_objs/invs/valid_pspace)
        but whose postcondition is purely structural — premise may
        be droppable. Heuristic only; ground-truth via probe.
        evidence: "heuristic" — emit to Tier 2.

Pattern B's `detect_missing_functional` was removed 2026-06-08 per
the revised strict definition of spec strengthening (B is "interface
filling", not strict strengthening).

The detector layer is intentionally one-pattern-at-a-time. No shared
ranking pipeline. Callers should invoke individual detectors and
present results in tiers (mechanical / probe-confirmable / manual).

Limitations:
- Regex-based; cannot follow imports / type-check.
- Cross-file paired_chain matching has high false-positive rate
  (~70%) because preconditions are not compared. Use per-file
  scans for cleanest signal.
- Loose-bound (`≤ → =`) and compound `_invs` shapes are NOT
  detected here — they require semantic understanding the
  scanner doesn't have.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable


# ------ Constants ----------------------------------------------------------

# Predicate-implication table for paired-chain detection. Each pair
# (strong, weak) means strong ⟹ weak in l4v.
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

STRUCTURAL_POSTCOND = {
    "cte_at", "real_cte_at", "obj_at", "typ_at",
    "ko_at", "ep_at", "ntfn_at", "tcb_at",
    "is_cnode_cap", "is_thread_cap", "is_ep_cap",
    "is_ntfn_cap", "is_reply_cap",
}

LOCAL_TOKENS = {
    "s", "s'", "rv", "rv'", "x", "y", "z", "p", "p'",
    "t", "t'", "r", "r'", "P", "Q", "f", "g", "F", "G",
    "obj", "ko", "cap", "cte", "ptr", "src", "dest",
    "args", "v", "w", "i", "j", "k", "n", "m", "b", "c",
    "do", "od", "let", "in", "if", "then", "else",
    "case", "of", "True", "False",
}

PREMISE_INV_RE = re.compile(r"\b(?:valid_objs|invs|valid_pspace|invs')\b")


# ------ Dataclasses --------------------------------------------------------

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
    """One detector hit on a Lemma.

    `kind` is a descriptive string identifying which detector produced
    the hit:
      "paired-chain"   — paired weak/strong (Pattern A; evidence: heuristic+manual)
      "unused-premise" — invariant-level pre + structural post (Pattern C; evidence: heuristic, probe-confirmable)

    Pattern B's "missing-functional" detector was removed 2026-06-08.
    """
    kind: str
    file: str
    line: int
    name: str
    note: str
    suggested_move: str = ""
    companion: dict | None = None
    pre: str = ""
    post: str = ""

    def as_dict(self):
        d = asdict(self)
        if d["companion"] is None:
            d.pop("companion")
        return d


# ------ Parsing helpers ----------------------------------------------------

# `[ \t]*` (not `\s*`) — `\s` includes `\n`, so with MULTILINE the
# anchor `^` would silently match at the start of a preceding blank
# line, making `m.start()` point one line too early. Tracked down
# 2026-06-02 (consumer-count off-by-one in spec_impact).
LEMMA_HEADER_RE = re.compile(
    r"^[ \t]*(?:lemma|theorem|corollary)s?[ \t]+"
    r"(?P<name>[A-Za-z_][A-Za-z_0-9']*)",
    re.MULTILINE,
)

HOARE_TRIPLE_RE = re.compile(
    r"""\\<lbrace>
        (?P<pre>.*?)
        \\<rbrace>
        (?P<body>.*?)
        \\<lbrace>
        (?P<post>.*?)
        \\<rbrace>
    """,
    re.VERBOSE | re.DOTALL,
)

HOARE_FRAME_SHORT_RE = re.compile(
    r"^\s*(?P<body>[^\n]+?)\s*"
    r"\\<lbrace>(?P<post>.*?)\\<rbrace>\s*$",
    re.DOTALL,
)

ISAR_KEYWORD_NAMES = {
    "shows", "fixes", "assumes", "obtains", "defines", "where", "notes",
}

BUNDLE_ENTRY_RE = re.compile(
    r"""(?:^|\sand\s)\s*
        (?P<name>[A-Za-z_][A-Za-z_0-9']*)
        (?:\s*\[[^\]]*\])?\s*
        :\s*
        "(?P<stmt>(?:[^"\\]|\\.)*)"
    """,
    re.VERBOSE | re.DOTALL,
)


def _line_at(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _extract_op(body: str) -> str:
    """First 'kernel-op-shaped' identifier in a Hoare body — skipping
    short locals and LOCAL_TOKENS. Returns "" if nothing reasonable."""
    tokens = re.findall(r"[A-Za-z_][A-Za-z_0-9']*", body)
    for t in tokens:
        if t in LOCAL_TOKENS:
            continue
        if len(t) <= 2 and "_" not in t:
            continue
        if "_" in t or len(t) >= 5:
            return t
    for t in tokens:
        if t not in LOCAL_TOKENS:
            return t
    return ""


def _hoare_from_stmt(stmt: str) -> tuple[str, str, str] | None:
    """Extract (pre, body, post). None if no Hoare shape recognized."""
    htm = HOARE_TRIPLE_RE.search(stmt)
    if htm:
        return (htm.group("pre").strip(),
                htm.group("body").strip(),
                htm.group("post").strip())
    sm = HOARE_FRAME_SHORT_RE.search(stmt)
    if sm:
        body = sm.group("body").strip()
        post = sm.group("post").strip()
        if "\\<lbrace>" in body:
            return None
        return (post, body, post)  # shorthand frame: pre = post
    return None


def extract_predicate_body(pred: str) -> str:
    r"""Strip lambda binders from a Hoare-triple predicate.

    `\<lambda>_ s. body`  →  `body`
    `\<lambda>rv. body`   →  `body`
    No binder → return as-is.
    """
    m = re.match(r"\s*\\<lambda>[^.]*\.\s*(.*)", pred, re.DOTALL)
    if m:
        return m.group(1).strip()
    return pred.strip()


def parse_thy_lemmas(thy_path: Path) -> list[Lemma]:
    """Extract all Hoare-triple lemmas from a .thy file."""
    text = thy_path.read_text(encoding="utf-8", errors="replace")
    lemmas: list[Lemma] = []
    headers = list(LEMMA_HEADER_RE.finditer(text))
    for i, m in enumerate(headers):
        name = m.group("name")
        start = m.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        window = text[start:end]
        proof_kw = re.search(
            r"^\s*(?:apply|by|proof|done|qed|sorry|oops)\b",
            window, re.MULTILINE,
        )
        if proof_kw:
            window = window[: proof_kw.start()]

        # Isar bundle: `lemma shows` followed by N (name, stmt) entries
        if name == "shows":
            for bm in BUNDLE_ENTRY_RE.finditer(window):
                bname = bm.group("name")
                if bname in ISAR_KEYWORD_NAMES:
                    continue
                hp = _hoare_from_stmt(bm.group("stmt"))
                if not hp:
                    continue
                pre, body, post = hp
                entry_offset = start + bm.start("name")
                lemmas.append(Lemma(
                    file=str(thy_path),
                    line=_line_at(text, entry_offset),
                    name=bname,
                    pre=pre, body=body, post=post,
                    op=_extract_op(body),
                ))
            continue

        if name in ISAR_KEYWORD_NAMES:
            continue

        hp = _hoare_from_stmt(window)
        if not hp:
            continue
        pre, body, post = hp
        lemmas.append(Lemma(
            file=str(thy_path),
            line=_line_at(text, m.start()),
            name=name,
            pre=pre, body=body, post=post,
            op=_extract_op(body),
        ))
    return lemmas


def iter_thy_files(path: Path) -> Iterable[Path]:
    if path.is_file() and path.suffix == ".thy":
        yield path
        return
    if path.is_dir():
        for thy in sorted(path.rglob("*.thy")):
            yield thy


# ------ Detectors ----------------------------------------------------------

def detect_paired_chain(lemmas: list[Lemma]) -> list[Finding]:
    """Lemmas about the same op whose postconds form an implication
    chain via IMPLICATION_TABLE — the weaker postcond may be a
    redundant cleanup target (former Pattern A).

    HIGH false-positive rate (~70%): preconditions are not compared,
    so most surfaced pairs have incomparable preconditions and the
    weaker lemma is NOT actually redundant. Treat as a hint only.
    """
    findings: list[Finding] = []
    by_op: dict[str, list[Lemma]] = {}
    for l in lemmas:
        if not l.op:
            continue
        by_op.setdefault(l.op, []).append(l)
    for op, group in by_op.items():
        if len(group) < 2:
            continue
        for a in group:
            for b in group:
                if a is b:
                    continue
                for strong, weak in IMPLICATION_TABLE:
                    if (re.search(rf"\b{re.escape(strong)}\b", a.post)
                            and re.search(rf"\b{re.escape(weak)}\b", b.post)
                            and not re.search(rf"\b{re.escape(strong)}\b", b.post)):
                        findings.append(Finding(
                            kind="paired-chain",
                            file=b.file, line=b.line, name=b.name,
                            note=(
                                f"`{weak}` postcondition is implied by "
                                f"`{strong}` (companion at line {a.line})."
                            ),
                            suggested_move=(
                                f"candidate cleanup: weak postcond `{weak}` "
                                f"is implied by `{strong}` companion "
                                f"`{a.name}` (line {a.line}); consider "
                                f"weak-lemma deletion (NOTE: high "
                                f"false-positive rate — verify "
                                f"preconditions are comparable first)"
                            ),
                            companion={
                                "name": a.name, "line": a.line,
                                "post": a.post[:80],
                            },
                            pre=b.pre[:120], post=b.post[:120],
                        ))
                        break
    seen: set[tuple[str, int, str]] = set()
    out: list[Finding] = []
    for f in findings:
        k = (f.file, f.line, f.name)
        if k in seen:
            continue
        seen.add(k)
        out.append(f)
    return out


# NOTE: `detect_missing_functional` (Pattern B) was removed 2026-06-08.
# Per the revised strict definition of spec strengthening (new spec must
# strictly entail old spec AND old spec cannot derive new spec), B is
# "interface filling", not strengthening. The detector is gone; the
# `missing-functional` kind no longer appears in any output.


def detect_unused_premise(lemmas: list[Lemma]) -> list[Finding]:
    """Hoare-triple lemmas with invariant-level pre (`valid_objs` /
    `invs` / `valid_pspace`) but structural-only post — premise may
    be droppable (former Pattern C)."""
    findings: list[Finding] = []
    for l in lemmas:
        pre_m = PREMISE_INV_RE.search(l.pre)
        if not pre_m:
            continue
        post_tokens = set(re.findall(r"[A-Za-z_][A-Za-z_0-9']*", l.post))
        has_invariant_in_post = bool(
            re.search(r"\b(?:invs|valid_objs|valid_pspace|valid_mdb|"
                      r"valid_idle|valid_cap|valid_state)\b", l.post)
        )
        if has_invariant_in_post:
            continue
        is_structural = any(p in post_tokens for p in STRUCTURAL_POSTCOND)
        if not is_structural:
            continue
        found_token = pre_m.group(0)
        findings.append(Finding(
            kind="unused-premise",
            file=l.file, line=l.line, name=l.name,
            note=(
                f"Premise contains `{found_token}` but postcondition "
                f"is structural-only. Premise may be droppable."
            ),
            suggested_move=(
                f"try dropping `{found_token}` from precondition; "
                f"include `<name>_old` witness lemma in same patch; "
                f"`check-theory.sh --patch` settles in ~60s"
            ),
            pre=l.pre[:200], post=l.post[:120],
        ))
    return findings
