"""Extract lemma records from a single Isabelle .thy file.

Heuristic regex-based — does NOT use Isabelle's outer syntax parser. Goal is to
build a stable inventory key for each named lemma so that "did this lemma still
exist after my edits, and did its statement / proof status change" can be
answered by diffing two snapshots.

For each lemma-like declaration we capture:
  kind         lemma | theorem | corollary | proposition | schematic_goal | lemmas
  name         identifier following the keyword (None for anonymous lemmas)
  attributes   list of strings inside [ ... ] adjacent to the name (e.g. ["simp"])
  line         1-indexed line in the original source
  statement    text from after the colon up to the start of the proof script,
               normalised (whitespace collapsed, comments stripped) for hashing
  statement_sha256
  body         text of the proof script, used only to detect sorry/oops; not hashed
  has_sorry    True if `sorry`, `oops`, or `sledgehammer` appears in the body

The extractor is tolerant: if it can't make sense of a region it skips it.
Anonymous lemmas (no name) are recorded with a synthetic name `<anon@line>` so
they still appear in counts but won't false-match across edits.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

LEMMA_KINDS = ("lemma", "theorem", "corollary", "proposition", "schematic_goal", "lemmas")

# Top-level commands that terminate a lemma block when seen at start of a logical line.
# Used to bound the "tail" of an unterminated lemma. We intentionally include common
# theory-level commands; the list does not need to be exhaustive — lemma proofs
# almost always end with done/qed/by/oops/sorry.
TERMINATING_COMMANDS = (
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

# Stripped-comments scanning is done first; therefore the regex below operates on
# clean text (no (* ... *) regions).
_LEMMA_START_RE = re.compile(
    r"""
    (^|\n)\s*
    (?P<kind>lemma|theorem|corollary|proposition|schematic_goal|lemmas)
    \b
    """,
    re.VERBOSE,
)

# Match: optional [attrs1] then NAME then optional [attrs2] then ":" or "="
# NAME is an Isabelle identifier; we allow a few extra characters that show up
# in the wild (notably ' and ?) even though Isabelle itself is stricter.
_NAME_AND_HEAD_RE = re.compile(
    r"""
    \s*
    (?:\(\s*in\s+(?P<locale>[A-Za-z_][\w'\s,]*?)\s*\)\s*)?   # optional `(in locale[, locale...])`
    (?:\[(?P<attrs1>[^\]]*)\]\s*)?
    (?P<name>[A-Za-z_][\w']*)
    \s*
    (?:\[(?P<attrs2>[^\]]*)\]\s*)?
    (?P<sep>[:=])
    """,
    re.VERBOSE,
)

# Keywords that signal the start of a proof script when seen at the *beginning of
# a logical line* (after stripping leading whitespace). These end the "statement"
# region of a lemma.
_PROOF_START_KEYWORDS = ("apply", "by", "proof", "using", "unfolding",
                         "supply", "subgoal", "show", "thus", "hence",
                         "have", "moreover", "obtain", "fix", "assume",
                         "next", "qed", "done", "oops", "sorry",
                         "including", "sledgehammer", "ML_val", "ML")
_PROOF_START_RE = re.compile(
    r"^\s*(" + "|".join(_PROOF_START_KEYWORDS) + r")\b"
)

# Match any of the terminating top-level commands at the start of a line.
_NEXT_TOPLEVEL_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(k) for k in TERMINATING_COMMANDS) + r")\b",
    re.MULTILINE,
)

_SORRY_RE = re.compile(r"\b(sorry|oops|sledgehammer)\b")


def _strip_isabelle_comments(src: str) -> str:
    """Strip nested (* ... *) comments. Preserves string literals."""
    out = []
    i = 0
    depth = 0
    n = len(src)
    in_str = False
    str_quote = ""
    while i < n:
        if not in_str:
            # toggle strings: " or `\<open>...\<close>` — leave inner-syntax alone
            if depth == 0 and src[i] == '"':
                in_str = True
                str_quote = '"'
                out.append(src[i])
                i += 1
                continue
            if src.startswith("(*", i):
                depth += 1
                # replace with a single space to preserve byte/line ratios is overkill;
                # keep newlines so line numbers don't drift.
                end = i + 2
                while end < n and depth > 0:
                    if src.startswith("(*", end):
                        depth += 1
                        end += 2
                    elif src.startswith("*)", end):
                        depth -= 1
                        end += 2
                    elif src[end] == "\n":
                        out.append("\n")
                        end += 1
                    else:
                        end += 1
                i = end
                continue
            out.append(src[i])
            i += 1
        else:
            out.append(src[i])
            if src[i] == str_quote:
                in_str = False
            i += 1
    return "".join(out)


# Statement normalization: collapse all runs of whitespace to single space, strip.
_WS_RE = re.compile(r"\s+")


def _normalise_statement(s: str) -> str:
    return _WS_RE.sub(" ", s).strip()


def _line_of_offset(src: str, offset: int, line_index: list[int] | None = None) -> int:
    """Return 1-indexed line containing offset."""
    if line_index is None:
        line_index = [i for i, c in enumerate(src) if c == "\n"]
    # Binary search wouldn't matter — but keep it simple
    import bisect
    return bisect.bisect_left(line_index, offset) + 1


def _find_proof_or_statement_end(clean: str, search_from: int) -> tuple[int, str]:
    """Find the position where the lemma's statement ends (i.e. proof begins) AND
    where the lemma block ends (terminator). Returns (statement_end, body_text).

    Strategy:
      - From `search_from`, scan line by line. The statement extends until we see
        a line whose first non-whitespace token is a proof-script keyword.
      - The body extends from there until we hit either:
          * the closing of the proof: a line whose first token is `done` or `qed`,
            or the keyword `by` `apply` etc. that ends inline like `by simp`;
          * a `sorry` or `oops`;
          * the start of another top-level command.
        We use the next-toplevel regex as the outer bound; the body may end earlier
        on `done`/`qed`/`oops`/`sorry`, which we detect with a quick scan.
    """
    n = len(clean)
    statement_end = n
    # find statement end: walk lines, look for first line starting with a proof keyword
    pos = search_from
    while pos < n:
        # advance to start of next non-empty line
        line_start = pos
        # find end of line
        nl = clean.find("\n", pos)
        line_end = n if nl == -1 else nl
        line = clean[line_start:line_end]
        if line.strip() and _PROOF_START_RE.match(line):
            statement_end = line_start
            break
        pos = line_end + 1
    # Body extends from statement_end until next top-level command.
    next_top = _NEXT_TOPLEVEL_RE.search(clean, statement_end + 1)
    body_end = next_top.start() if next_top else n
    body_text = clean[statement_end:body_end]
    return statement_end, body_text


def _parse_attrs(s: str | None) -> list[str]:
    if not s:
        return []
    # split on commas at top level (don't split inside nested brackets)
    out, depth, cur = [], 0, []
    for ch in s:
        if ch == "[":
            depth += 1
            cur.append(ch)
        elif ch == "]":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            tok = "".join(cur).strip()
            if tok:
                out.append(tok)
            cur = []
        else:
            cur.append(ch)
    tok = "".join(cur).strip()
    if tok:
        out.append(tok)
    return out


def extract_lemmas(thy_path: Path) -> list[dict]:
    raw = thy_path.read_text(errors="replace")
    clean = _strip_isabelle_comments(raw)
    line_index = [i for i, c in enumerate(clean) if c == "\n"]
    out = []
    for m in _LEMMA_START_RE.finditer(clean):
        kind = m.group("kind")
        head_start = m.end()
        head_match = _NAME_AND_HEAD_RE.match(clean, head_start)
        line = _line_of_offset(clean, m.start("kind"), line_index)

        if not head_match:
            # Could be anonymous: `lemma "stmt" by simp`
            # Try to detect a string literal right after.
            tail = clean[head_start:].lstrip()
            if tail.startswith('"'):
                # Anonymous lemma with statement only
                stmt_start = clean.index('"', head_start)
                # statement-end + body
                statement_end, body_text = _find_proof_or_statement_end(clean, stmt_start)
                statement = clean[stmt_start:statement_end]
                norm = _normalise_statement(statement)
                out.append(dict(
                    kind=kind,
                    name=f"<anon@{line}>",
                    attributes=[],
                    line=line,
                    statement=statement.strip(),
                    statement_norm=norm,
                    statement_sha256=hashlib.sha256(norm.encode()).hexdigest(),
                    body=body_text.strip(),
                    has_sorry=bool(_SORRY_RE.search(body_text)),
                    is_anonymous=True,
                ))
            continue

        name = head_match.group("name")
        attrs = _parse_attrs(head_match.group("attrs1")) + _parse_attrs(head_match.group("attrs2"))
        locale = head_match.group("locale")
        if locale:
            attrs.append(f"in:{re.sub(r'\\s+', '', locale)}")
        sep = head_match.group("sep")
        after_sep = head_match.end()

        if kind == "lemmas":
            # `lemmas` is a fact bundle: `lemmas name [attrs] = expr`
            # The "statement" is the right-hand-side expression. Body is empty.
            # Bound the rhs by the next top-level command.
            next_top = _NEXT_TOPLEVEL_RE.search(clean, after_sep + 1)
            rhs_end = next_top.start() if next_top else len(clean)
            rhs = clean[after_sep:rhs_end]
            norm = _normalise_statement(rhs)
            out.append(dict(
                kind=kind,
                name=name,
                attributes=attrs,
                line=line,
                statement=rhs.strip(),
                statement_norm=norm,
                statement_sha256=hashlib.sha256(norm.encode()).hexdigest(),
                body="",
                has_sorry=False,
                is_anonymous=False,
            ))
            continue

        # Regular lemma/theorem/etc.
        statement_end, body_text = _find_proof_or_statement_end(clean, after_sep)
        statement = clean[after_sep:statement_end]
        norm = _normalise_statement(statement)
        out.append(dict(
            kind=kind,
            name=name,
            attributes=attrs,
            line=line,
            statement=statement.strip(),
            statement_norm=norm,
            statement_sha256=hashlib.sha256(norm.encode()).hexdigest(),
            body=body_text.strip(),
            has_sorry=bool(_SORRY_RE.search(body_text)),
            is_anonymous=False,
        ))
    return out


if __name__ == "__main__":
    import json
    import sys

    p = Path(sys.argv[1])
    rows = extract_lemmas(p)
    print(f"# {p}: {len(rows)} lemma-like declarations")
    for r in rows[:30]:
        print(f"  L{r['line']:5d} {r['kind']:15s} {r['name']:40s} sorry={r['has_sorry']} attrs={r['attributes']}")
    if len(rows) > 30:
        print(f"  ... and {len(rows)-30} more")
