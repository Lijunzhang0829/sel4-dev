#!/usr/bin/env python3
"""
tools/spec_strengthen/spec_strengthen_c_patchgen.py

Generate a Pattern C strengthening patch (drop unused premise +
inline `_old` witness) in the range-replace format that
`check-theory.sh --patch` consumes.

Workflow:
  1. Locate the lemma block in the theory file (`lemma <name>:`
     header through closing `done`/`qed`/`by`).
  2. Extract the Hoare-triple statement; drop the conjunct
     mentioning `<premise>` from the precondition via
     `spec_premise_probe.drop_conjunct_from_pre`.
  3. Detect the triple shape (valid / validE / validE_R / validE_E).
  4. Look up the witness rule via WITNESS_RULES (mirrors
     `spec_witness_gen.py`).
  5. Emit a range-replace patch:
       <start_line> <end_line>
       <lemma header (unchanged name)>
       <statement with the conjunct dropped from pre>
       <original proof script verbatim — the premise was unused, so
        the same proof closes>
       <blank line>
       <witness lemma <name>_old with ORIGINAL statement>
       <by (rule <witness_rule>[OF <name>]) simp>

Usage:
  spec_strengthen_c_patchgen.py --theory <file> --lemma <name> \
    --premise <p> [--out <patch>]

Output (on stdout when --out absent):
  the range-replace patch text.

Exit codes:
  0  patch written / printed
  4  bad input (file/lemma not found)
  5  premise not present in pre / multiple matches / parse error
  6  could not detect triple shape or pick witness rule

This tool is the missing piece that lets `spec_strengthen_run.sh
execute --candidate C:...` run end-to-end.  It assumes the probe
(spec_premise_probe.sh) has already confirmed the premise is
NOT load-bearing.  If the probe was skipped, the resulting
`check-theory.sh --patch` will simply fail at trial; no soundness
risk.
"""

from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path

# Reuse parsers from sibling tools.
sys.path.insert(0, str(Path(__file__).parent))
from spec_premise_probe import drop_conjunct_from_pre  # noqa: E402


# ----------------------------------------------------------------------------
# Witness-rule table (mirrors spec_witness_gen.py / playbook table).
# Pattern C is always "pre-weaken" (we drop a precondition).
# ----------------------------------------------------------------------------

WITNESS_RULE_BY_SHAPE = {
    "valid":    "hoare_weaken_pre",
    "validE":   "hoare_pre",
    "validE_R": "hoare_pre",
    "validE_E": "hoare_pre",
}


# Triple-shape regexes — same as spec_witness_gen.py
SHAPE_VALID_E_R = re.compile(r"\\<rbrace>\s*,\s*-(?:\s*$|\s*\n)")
SHAPE_VALID_E_E = re.compile(r"\\<rbrace>\s*-\s*,\s*\\<lbrace>")
SHAPE_VALID_E   = re.compile(r"\\<rbrace>\s*,\s*\\<lbrace>")


def detect_triple_shape(stmt: str) -> str | None:
    if SHAPE_VALID_E_R.search(stmt):
        return "validE_R"
    if SHAPE_VALID_E_E.search(stmt):
        return "validE_E"
    if SHAPE_VALID_E.search(stmt):
        return "validE"
    if "\\<lbrace>" in stmt and "\\<rbrace>" in stmt:
        return "valid"
    return None


# ----------------------------------------------------------------------------
# Lemma block extraction
# ----------------------------------------------------------------------------

LEMMA_HEADER_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<kw>lemma|theorem|corollary)s?[ \t]+"
    r"(?P<name>[A-Za-z_][A-Za-z_0-9']*)\s*(?P<attrs>\[[^\]]*\])?\s*:",
    re.MULTILINE,
)
PROOF_END_RE = re.compile(r"^\s*(?:done|qed|sorry|oops)\b", re.MULTILINE)


def locate_lemma_block(theory_text: str, lemma_name: str) -> dict | None:
    """Find the lemma block and return location + content slices."""
    for m in LEMMA_HEADER_RE.finditer(theory_text):
        if m.group("name") != lemma_name:
            continue
        header_start = m.start()
        end_m = PROOF_END_RE.search(theory_text, m.end())
        if not end_m:
            return None
        # End-of-block is the line containing `done`/`qed`
        block_end_line_end = theory_text.find("\n", end_m.end())
        if block_end_line_end == -1:
            block_end_line_end = len(theory_text)
        block_text = theory_text[header_start:block_end_line_end]
        # 1-based line numbers
        start_line = theory_text.count("\n", 0, header_start) + 1
        end_line = theory_text.count("\n", 0, block_end_line_end) + 1
        # end_line correction: if we landed exactly at a newline,
        # back off one — the `done` line itself.
        # The patch range needs to be [start_line, end_line] INCLUSIVE
        # where end_line is the line containing the `done`.
        done_line = theory_text.count("\n", 0, end_m.start()) + 1
        return {
            "indent": m.group("indent"),
            "header_start": header_start,
            "header_match": m,
            "block_text": block_text,
            "start_line": start_line,
            "end_line": done_line,
        }
    return None


# Hoare-triple extraction — find the `"<\\<lbrace>...\\<rbrace> body \\<lbrace>...\\<rbrace>[,-...]?"` string.
# Allow multi-line.
def extract_quoted_statement(block_text: str) -> tuple[str, int, int] | None:
    """Find the first `"..."` after the lemma header colon.
    Returns (statement_text_without_quotes, start_offset_in_block, end_offset_in_block).
    """
    # The header line ends with `:`; find first `"` after that.
    colon = block_text.find(":")
    if colon < 0:
        return None
    quote_start = block_text.find('"', colon)
    if quote_start < 0:
        return None
    # Find matching closing quote.  l4v lemmas don't usually escape
    # quotes inside the statement, so a naive look-ahead works.
    i = quote_start + 1
    while i < len(block_text):
        if block_text[i] == '"' and (i == 0 or block_text[i - 1] != '\\'):
            return (block_text[quote_start + 1:i], quote_start, i + 1)
        i += 1
    return None


def split_pre_body_post(stmt: str) -> dict | None:
    """Naive Hoare-triple split: pre / body / post / suffix (`,-` or
    `,\\<lbrace>E\\<rbrace>`).  Returns None if the shape isn't
    recognizable."""
    # Match `\\<lbrace>PRE\\<rbrace> BODY \\<lbrace>POST\\<rbrace>[SUFFIX]`
    m = re.match(
        r"\s*\\<lbrace>(?P<pre>.*?)\\<rbrace>"
        r"(?P<body>.*?)"
        r"\\<lbrace>(?P<post>.*?)\\<rbrace>"
        r"(?P<suffix>(?:\s*,\s*-|\s*,\s*\\<lbrace>.*?\\<rbrace>|))",
        stmt,
        re.DOTALL,
    )
    if not m:
        return None
    return {
        "pre":    m.group("pre"),
        "body":   m.group("body"),
        "post":   m.group("post"),
        "suffix": m.group("suffix") or "",
    }


# ----------------------------------------------------------------------------
# Patch construction
# ----------------------------------------------------------------------------

def build_patch(
    theory_path: Path,
    lemma_name: str,
    premise: str,
) -> tuple[int, str]:
    """Return (exit_code, patch_text).  exit_code 0 means success."""
    theory_text = theory_path.read_text(encoding="utf-8", errors="replace")

    block = locate_lemma_block(theory_text, lemma_name)
    if block is None:
        return 4, f"lemma `{lemma_name}` not found or has no proof end\n"

    stmt_extracted = extract_quoted_statement(block["block_text"])
    if stmt_extracted is None:
        return 5, "could not extract quoted statement from lemma block\n"
    original_stmt_inner, stmt_off_start, stmt_off_end = stmt_extracted
    original_stmt_quoted = f'"{original_stmt_inner}"'

    # Drop the premise from the precondition
    new_stmt_quoted, status = drop_conjunct_from_pre(
        original_stmt_quoted, premise
    )
    if status != "ok":
        return 5, f"drop_conjunct_from_pre status: {status}\n"

    # Detect triple shape on the ORIGINAL stmt
    shape = detect_triple_shape(original_stmt_inner)
    if shape is None:
        return 6, "could not detect triple shape\n"
    witness_rule = WITNESS_RULE_BY_SHAPE.get(shape)
    if witness_rule is None:
        return 6, f"no witness rule for shape {shape}\n"

    # Build the replacement text:
    #   <block_text with statement substring replaced by new_stmt_quoted>
    #   <blank>
    #   <witness lemma>
    new_block_text = (
        block["block_text"][:stmt_off_start]
        + new_stmt_quoted
        + block["block_text"][stmt_off_end:]
    )

    witness_text = (
        f"\n\n"
        f"lemma {lemma_name}_old:\n"
        f"  {original_stmt_quoted}\n"
        f"  by (rule {witness_rule}[OF {lemma_name}]) simp"
    )

    replacement = new_block_text + witness_text

    patch = f"{block['start_line']} {block['end_line']}\n{replacement}\n"
    return 0, patch


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate a Pattern C strengthening patch "
                    "(drop unused premise + inline _old witness).",
    )
    ap.add_argument("--theory", type=Path, required=True,
                    help="Path to .thy file")
    ap.add_argument("--lemma", required=True,
                    help="Target lemma name")
    ap.add_argument("--premise", required=True,
                    help="Premise to drop from pre (e.g. valid_objs)")
    ap.add_argument("--out", type=Path, default=None,
                    help="Output patch file (default: stdout)")
    args = ap.parse_args()

    if not args.theory.exists():
        print(f"theory not found: {args.theory}", file=sys.stderr)
        return 4

    code, text = build_patch(args.theory, args.lemma, args.premise)
    if code != 0:
        print(f"# spec_strengthen_c_patchgen: ERROR (code {code})",
              file=sys.stderr)
        print(text, file=sys.stderr)
        return code

    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"# patch written to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
