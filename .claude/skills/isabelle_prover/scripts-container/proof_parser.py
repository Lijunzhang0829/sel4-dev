"""Proof parser for Isabelle .thy files.

Identifies:
  1. Top-level lemma/theorem proofs (proof_start .. proof_end inclusive).
  2. Optional sub-proofs inside Isar `proof ... qed` blocks
     (`have NAME: "..." by TAC` single-line claims).

Terminator-aware: tracks `proof ... qed` nesting depth and recognizes
`by` / `done` / `sorry` / `oops` at depth 0 as the explicit proof terminator.
This is more robust than the previous "next top-level command" heuristic alone,
which mishandled:

  - One-liners `lemma foo: "..." by simp` (proof keyword on the lemma line).
    The previous code only scanned from `lemma_line + 1`, so a one-liner had
    no proof_start detected, and the parser would then "skip ahead" to the
    NEXT lemma's `apply`/`by` line and treat that as the one-liner's proof,
    silently swallowing the next lemma entirely.
  - Nested `proof - ... proof - ... qed ... qed` blocks where the inner
    `qed` was naively treated as the outer's closer.
  - `by (...,\n ...)` continuations (paren-balanced over multiple lines).

Output record:
    {
      'name': str,              # 'lemma_name' or 'lemma_name::sub_name'
      'kind': str,              # 'lemma' | 'theorem' | 'have' | 'show' | ...
      'proof_start': int,       # 0-indexed line of first proof keyword
      'proof_end': int,         # 0-indexed terminator line (inclusive)
      'size': int,              # proof_end - proof_start + 1
      'is_subproof': bool,
      'parent': str | None,
      'top_tactic': str,        # 'by' | 'apply' | 'proof' | 'using' | ...
      'search_pressure': str,   # 'high' | 'medium' | 'low'
    }

`top_tactic` and `search_pressure` support Phase C decision rules:
unhinted `auto / force / fastforce / blast / metis` is `high` — these are
the lemmas worth attacking with sledgehammer linearisation, Isar
decomposition, or explicit-hint rewrites. The principle is: reduce the
proof's search space by giving precise direction.
"""
import re

LEMMA_KIND_RE = re.compile(
    r"^\s*(?P<kind>lemma|theorem|corollary|proposition|schematic_goal)\b"
)
NAME_RE = re.compile(
    r"\s*(?:\(\s*in\s+[A-Za-z_][\w'\s,]*?\s*\)\s*)?"
    r"(?:\[[^\]]*\]\s*)?"
    r"(?P<name>[A-Za-z_][\w']*)"
)
PROOF_KEYWORDS = (
    "apply", "by", "proof", "using", "unfolding", "supply",
    "subgoal", "show", "thus", "hence", "have", "moreover",
    "obtain", "fix", "assume", "next", "qed", "done",
    "oops", "sorry", "including", "sledgehammer",
)
PROOF_START_RE = re.compile(r"^\s*(" + "|".join(PROOF_KEYWORDS) + r")\b")

# When checking the lemma line itself for an inline proof, we only accept
# the keywords that legally introduce the proof (not the in-block keywords
# like `have`, `show`, `next`, `qed`, etc.).
INLINE_PROOF_KW_RE = re.compile(r"\b(apply|by|using|unfolding|supply|proof)\b")

TERMINATOR_TOKENS = ("by", "done", "sorry", "oops")

TOP_KEYWORDS = (
    "lemma", "theorem", "corollary", "proposition", "schematic_goal", "lemmas",
    "definition", "fun", "function", "primrec", "abbreviation", "notation",
    "no_notation", "declare", "axiomatization", "consts", "locale", "sublocale",
    "context", "interpretation", "instance", "instantiation", "class", "datatype",
    "record", "type_synonym", "code_datatype", "codatatype", "end", "begin",
    "ML", "ML_file", "ML_command", "ML_val", "setup", "local_setup",
    "attribute_setup", "method_setup", "syntax", "no_syntax", "translations",
    "term", "value", "thm", "find_theorems", "find_consts", "named_theorems",
    "partial_function", "termination", "defs", "overloading", "bundle",
    "unbundle", "lift_definition", "free_constructors", "oracle", "section",
    "subsection", "subsubsection", "chapter", "paragraph", "text", "txt",
    "crunch", "crunches", "crunch_ignore", "requalify_consts", "requalify_facts",
    "requalify_types", "global_naming", "qualified_consts",
)
NEXT_TOP_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(k) for k in TOP_KEYWORDS) + r")\b"
)

SEARCH_HEAVY_TACTICS = (
    "auto", "force", "fastforce", "blast", "metis", "smt", "sledgehammer",
)

SUBPROOF_RE = re.compile(
    r"^\s*(?P<kind>have|show|hence|thus|moreover|ultimately|also|finally)\b"
    r"\s*(?:(?P<name>[A-Za-z_][\w']*)\s*:)?"
)

# Sub-proof "claim head" keywords; encountering any of them at depth 0 inside
# an Isar block means the previous sub-claim's proof has ended.
SUBPROOF_HEAD_TOKENS = (
    "have", "show", "hence", "thus", "moreover", "ultimately", "also",
    "finally", "next",
)


# ─── helpers ─────────────────────────────────────────────────────────────

def _strip_comments(src):
    """Strip nested (* ... *) preserving newlines and string-literal contents."""
    out, i, depth, n, in_str = [], 0, 0, len(src), False
    while i < n:
        if not in_str and src.startswith("(*", i):
            depth, end = 1, i + 2
            while end < n and depth > 0:
                if src.startswith("(*", end):
                    depth += 1; end += 2
                elif src.startswith("*)", end):
                    depth -= 1; end += 2
                elif src[end] == "\n":
                    out.append("\n"); end += 1
                else:
                    end += 1
            i = end
            continue
        if not in_str and src[i] == '"':
            in_str = True
        elif in_str and src[i] == '"':
            in_str = False
        out.append(src[i])
        i += 1
    return "".join(out)


def _strip_strings(line):
    """Replace `"..."` with same-length spaces to mask string contents."""
    return re.sub(r'"[^"]*"', lambda m: ' ' * len(m.group(0)), line)


def _first_token(line):
    stripped = line.lstrip()
    if not stripped:
        return ""
    m = re.match(r"(\w+)", stripped)
    return m.group(1) if m else ""


def _consume_multiline_by(start, lines):
    """A `by (rule foo,\n    simp)` spans multiple lines because of an
    unbalanced paren. Return the line at which parens balance out."""
    line = lines[start]
    opens = line.count("(") - line.count(")")
    if opens <= 0:
        return start
    j = start + 1
    n = len(lines)
    while j < n and opens > 0:
        opens += lines[j].count("(") - lines[j].count(")")
        if opens <= 0:
            return j
        j += 1
    return min(j, n - 1)


def _classify_search_pressure(tactic_text):
    """Pressure for a SINGLE tactic invocation (used for sub-proofs that
    are one-line `by TAC`). Body-level pressure for whole proofs uses
    `_classify_body_pressure` below."""
    s = tactic_text.strip()
    has_hint = bool(re.search(r"\b(add|dest|intro|rule|elim|simp|del|cong):", s))
    for tac in SEARCH_HEAVY_TACTICS:
        if re.search(rf"\b{tac}\b", s):
            return "medium" if has_hint else "high"
    return "low"


def _classify_body_pressure(proof_start, proof_end, lines):
    """Scan the FULL proof body for search-heavy tactics. Returns 'high' if
    any unhinted `auto/force/fastforce/blast/metis/smt` invocation exists,
    'medium' if all such invocations carry hints (`add:`/`dest:`/etc.),
    'low' if no search-heavy tactics are used at all.

    Rationale: a lemma `using assms\n  apply auto\n  done` has top_tactic
    `using` (which is low-pressure on its own line) but its slowness is
    actually driven by the unhinted `auto` deeper in the body. Reducing the
    search space there is the lever. The pressure metric must reflect the
    body, not just the first proof keyword."""
    text = "\n".join(lines[proof_start:proof_end + 1])
    sanitized = _strip_strings(text)
    has_unhinted = False
    has_hinted = False
    for tac in SEARCH_HEAVY_TACTICS:
        for m in re.finditer(rf"\b{tac}\b", sanitized):
            # Peek at the tactic's invocation tail: up to the next `)`, `;`,
            # `|`, newline, or `apply`/`by`/`done`. Look for hint markers
            # within that tail.
            tail = sanitized[m.end():m.end() + 200]
            tail_until_break = re.split(r"[\n\)\|;]|\bapply\b|\bdone\b|\bby\b",
                                        tail, maxsplit=1)[0]
            if re.search(r"\b(add|dest|intro|rule|elim|simp|del|cong):",
                         tail_until_break):
                has_hinted = True
            else:
                has_unhinted = True
    if has_unhinted:
        return "high"
    if has_hinted:
        return "medium"
    return "low"


# ─── proof-end search (depth-tracking, terminator-aware) ─────────────────

def _find_proof_end(scan_from, lines, *, initial_depth=0):
    """Scan from `scan_from` forward; return inclusive last line of the proof.

    Args:
        scan_from: line index to start scanning. The CALLER decides whether
            to include scan_from itself by passing the correct initial_depth
            and choosing scan_from accordingly:
              - lemma's `by` is at line L on its own row → call (L, depth=0).
                Returns L (or end of multi-line `by (...)`).
              - lemma uses `proof - ...` at line L → call (L+1, depth=1).
              - same-line `lemma foo: "..." using assms apply simp` at line L
                with continuation → call (L+1, depth=0).
        initial_depth: Isar `proof` nesting depth at start of scan.

    Rules:
        - `proof` increments depth; `qed` decrements (when depth > 0).
        - At depth 0, any token in TERMINATOR_TOKENS ends the proof.
        - At depth 0, hitting a TOP_KEYWORD line means the proof was
          implicitly closed (malformed source or our depth model failed);
          bail with j-1 to keep the parser robust.
    """
    n = len(lines)
    depth = initial_depth
    j = scan_from
    # When called with scan_from on its own first-keyword row, check that row first.
    if j < n and lines[j].strip():
        tok = _first_token(lines[j])
        if depth == 0 and tok in TERMINATOR_TOKENS:
            return _consume_multiline_by(j, lines)
        if tok == "proof":
            depth += 1
        elif tok == "qed" and depth > 0:
            depth -= 1
            if depth == 0:
                return j
        j += 1
    while j < n:
        line = lines[j]
        if not line.strip():
            j += 1; continue
        tok = _first_token(line)

        if tok == "proof":
            depth += 1
        elif tok == "qed":
            if depth > 0:
                depth -= 1
                if depth == 0:
                    return j
        elif depth == 0:
            if tok in TERMINATOR_TOKENS:
                return _consume_multiline_by(j, lines)
            if NEXT_TOP_RE.match(line):
                return max(scan_from, j - 1)
        j += 1
    return n - 1


# ─── inline proof on lemma row ───────────────────────────────────────────

def _detect_inline_proof(lemma_line, lines, after_name_offset):
    """If the lemma row itself contains a proof keyword AFTER the name
    (outside any quoted string), return (kw, proof_end). Else (None, None).

    Same-line forms supported:
        lemma foo: "stmt" by simp                       → by, end=lemma_line
        lemma foo: "stmt" using assms by simp           → by, end=lemma_line
        lemma foo: "stmt" apply simp by auto            → apply, end=lemma_line
        lemma foo: "stmt"\n  by tac                     → None (not inline)
    """
    line = lines[lemma_line]
    sanitized = _strip_strings(line)
    # Search for ANY proof-starting keyword in the part after the name.
    m = INLINE_PROOF_KW_RE.search(sanitized, after_name_offset)
    if not m:
        return (None, None)
    kw = m.group(1)

    # Determine proof_end based on the LAST proof keyword on the line.
    # (For `using assms by simp`, last is `by`; the proof completes on this line.)
    # Find the rightmost terminator-class keyword on the line.
    last_terminator = None
    for tm in INLINE_PROOF_KW_RE.finditer(sanitized, after_name_offset):
        if tm.group(1) in ("by",):
            last_terminator = tm
    if last_terminator is not None:
        # `by` on same line → proof ends on this line (or extends if `by (...)`).
        return ("by", _consume_multiline_by(lemma_line, lines))
    if kw == "proof":
        # `lemma foo: ... proof -` starts an Isar block; scan for matching qed.
        return ("proof", _find_proof_end(lemma_line + 1, lines, initial_depth=1))
    # using / apply / unfolding / supply — scan forward for terminator.
    return (kw, _find_proof_end(lemma_line + 1, lines, initial_depth=0))


# ─── sub-proof extraction inside Isar blocks ─────────────────────────────

def _find_subproof_end(have_line, outer_end, lines):
    """For a `have NAME: "..."` line at `have_line`, find the inclusive last
    line of its proof. Returns `have_line` if the `by ...` is on the same
    row; otherwise scans forward up to `outer_end` for the appropriate
    terminator (`by`, `done`, `sorry`, `oops`, or matching `qed`).

    Returns None if the sub-claim has no proof within the outer block (e.g.,
    a `have NAME: "..."` followed immediately by another `have` — malformed)."""
    BY_INLINE_RE = re.compile(r"\bby\s+")
    if BY_INLINE_RE.search(_strip_strings(lines[have_line])):
        return _consume_multiline_by(have_line, lines)

    n = len(lines)
    depth = 0
    j = have_line + 1
    while j <= outer_end and j < n:
        line = lines[j]
        if not line.strip():
            j += 1; continue
        tok = _first_token(line)

        if tok == "proof":
            depth += 1
        elif tok == "qed":
            if depth > 0:
                depth -= 1
                if depth == 0:
                    return j
        elif depth == 0:
            if tok in TERMINATOR_TOKENS:
                return _consume_multiline_by(j, lines)
            # A new sub-claim head at depth 0 means this one had no explicit
            # terminator — the prior sub-proof is malformed. Skip it.
            if tok in SUBPROOF_HEAD_TOKENS:
                return None
        j += 1
    return None


def _extract_subproofs(proof_start, proof_end, lines, parent_name):
    """Find named `have NAME: "..."` / `show NAME: "..."` sub-proofs inside
    an Isar block (`proof ... qed`).

    The block may start at `proof_start` itself, or after a `using/supply/
    unfolding` preamble. Both same-line `by tac` and multi-line forms are
    supported. Anonymous `have "..."` (no name) is skipped because there is
    no stable identifier to surface in reports / sorry substitution.
    """
    subs = []
    isar_start = None
    for j in range(proof_start, min(proof_end + 1, len(lines))):
        if _first_token(lines[j]) == "proof":
            isar_start = j
            break
    if isar_start is None:
        return subs

    BY_INLINE_RE = re.compile(r"\bby\s+")
    j = isar_start + 1
    while j < proof_end:
        line = lines[j]
        m = SUBPROOF_RE.match(line)
        if not m:
            j += 1; continue
        kind = m.group("kind")
        name = m.group("name")
        if not name or kind in ("moreover", "ultimately", "also", "finally"):
            # Glue keywords without a binding name — skip.
            j += 1; continue
        end_j = _find_subproof_end(j, proof_end, lines)
        if end_j is None:
            j += 1; continue
        # Tactic text for pressure classification: same-line `by` or the
        # proof-body line we landed on.
        by_m = BY_INLINE_RE.search(line)
        if by_m:
            tac_text = line[by_m.end():]
        elif end_j < len(lines):
            tac_text = lines[end_j]
        else:
            tac_text = ""
        subs.append({
            'name': f"{parent_name}::{name}",
            'kind': kind,
            'proof_start': j,
            'proof_end': end_j,
            'size': end_j - j + 1,
            'is_subproof': True,
            'parent': parent_name,
            'top_tactic': 'by' if by_m else _first_token(lines[end_j]),
            'search_pressure': _classify_search_pressure(tac_text),
        })
        j = end_j + 1
    return subs


# ─── main entry ──────────────────────────────────────────────────────────

def parse_proofs(orig_lines, *, extract_subproofs=True):
    """Parse .thy lines into a list of proof entries.

    Args:
        orig_lines: list of strings (with trailing newlines).
        extract_subproofs: emit `have NAME: ... by TAC` sub-proofs too.

    Returns: list of dicts sorted by size descending.
    """
    cleaned = _strip_comments("".join(orig_lines)).split("\n")
    while len(cleaned) < len(orig_lines):
        cleaned.append("")

    proofs = []
    n = len(cleaned)
    i = 0
    while i < n:
        lemma_m = LEMMA_KIND_RE.match(cleaned[i])
        if not lemma_m:
            i += 1; continue
        kind = lemma_m.group("kind")
        name_m = NAME_RE.match(cleaned[i][lemma_m.end():])
        if not name_m:
            i += 1; continue
        name = name_m.group("name")
        lemma_line = i
        after_name_offset = lemma_m.end() + name_m.end()

        # 1. Same-line proof on lemma row?
        inline_kw, inline_end = _detect_inline_proof(lemma_line, cleaned, after_name_offset)
        if inline_kw is not None:
            proof_start = lemma_line
            proof_end = inline_end
            top_tactic = inline_kw
            # Tactic text: portion AFTER the keyword on the same line
            sanitized = _strip_strings(cleaned[lemma_line])
            kw_match = re.search(rf"\b{inline_kw}\b", sanitized[after_name_offset:])
            tac_text = ""
            if kw_match:
                tac_text = cleaned[lemma_line][
                    after_name_offset + kw_match.end():
                ]
            # tac_text is only used as fallback for one-liner same-row proofs.
            # For the parent lemma's pressure we use body-scan below.
            pressure = _classify_search_pressure(tac_text)
        else:
            # 2. Scan next lines for proof keyword start.
            proof_start = None
            for j in range(lemma_line + 1, n):
                if cleaned[j].strip() and PROOF_START_RE.match(cleaned[j]):
                    proof_start = j
                    break
            if proof_start is None:
                i += 1; continue
            proof_end = _find_proof_end(proof_start, cleaned)
            top_tactic = _first_token(cleaned[proof_start])
            # Tactic text from the proof_start line
            if top_tactic in ("by", "apply"):
                stripped = cleaned[proof_start].lstrip()
                tac_text = stripped[len(top_tactic):]
            else:
                tac_text = cleaned[proof_start]
            pressure = _classify_search_pressure(tac_text)

        # Trim trailing blanks
        while proof_end > proof_start and not cleaned[proof_end].strip():
            proof_end -= 1

        # Body-level pressure (whole-proof scan, more accurate than first-line).
        body_pressure = _classify_body_pressure(proof_start, proof_end, cleaned)

        proofs.append({
            'name': name,
            'kind': kind,
            'proof_start': proof_start,
            'proof_end': proof_end,
            'size': proof_end - proof_start + 1,
            'is_subproof': False,
            'parent': None,
            'top_tactic': top_tactic,
            'search_pressure': body_pressure,
        })

        if extract_subproofs:
            proofs.extend(
                _extract_subproofs(proof_start, proof_end, cleaned, name)
            )

        i = proof_end + 1

    proofs.sort(key=lambda p: p['size'], reverse=True)
    return proofs
