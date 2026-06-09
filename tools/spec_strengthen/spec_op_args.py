#!/usr/bin/env python3
"""
tools/spec_strengthen/spec_op_args.py

Extract the formal argument names of a Pattern-G op from its
definition in verification/l4v/spec/abstract/. Used by
spec_strengthen_run.sh's execute_G template generation so the
new frame lemma uses the correct arg arity/names instead of the
hardcoded set_object-shape `p ko`.

Returns space-separated arg names on stdout, or exits non-zero
with a comment on stderr if the def can't be located/parsed.

Usage:
  python3 tools/spec_strengthen/spec_op_args.py <op_name>

Example:
  $ python3 tools/spec_strengthen/spec_op_args.py set_thread_state
  ref ts
"""
from __future__ import annotations
import sys
from pathlib import Path

# Reuse the def-block extraction from spec_frame_gap.
sys.path.insert(0, str(Path(__file__).parent))
from spec_frame_gap import _find_op_def_block  # noqa: E402

import re


def _type_arity(type_sig: str) -> int:
    """Count top-level `\\<Rightarrow>` arrows in an Isabelle type signature.

    Args are everything before the final return type, so the arity is
    the number of top-level arrows. Parenthesized sub-expressions are
    skipped (their internal arrows aren't formal args of the outer op).
    """
    depth = 0
    arrows = 0
    i = 0
    n = len(type_sig)
    arrow_lit = r"\<Rightarrow>"
    while i < n:
        ch = type_sig[i]
        if ch == "(":
            depth += 1
            i += 1
        elif ch == ")":
            depth -= 1
            i += 1
        elif depth == 0 and type_sig[i:i + len(arrow_lit)] == arrow_lit:
            arrows += 1
            i += len(arrow_lit)
        else:
            i += 1
    return arrows


def _pad_args(named: list[str], expected: int) -> list[str]:
    """Pad `named` to `expected` length with fresh single-letter binders.

    Skips letters that would shadow the Hoare-triple predicate var `P`
    or the state var `s`. Pad letters chosen lexicographically from
    {a, b, c, d, e, f, g, h, i, j, k, m, n, o, q, r, t, u, v, w, x, y, z}.
    """
    out = list(named)
    if len(out) >= expected:
        return out
    used = set(out) | {"P", "s"}
    for c in "abcdefghijkmnoqrtuvwxyz":
        if c not in used:
            out.append(c)
            used.add(c)
            if len(out) >= expected:
                return out
    return out


def extract_op_args(op: str, repo_root: Path) -> list[str] | None:
    """Return the list of formal arg names usable in a Hoare-triple
    invocation `<op> <args>`. Combines two sources:

      1. Bound names from `"<op> <names> \\<equiv> ..."` (works only for
         the prefix of args that aren't destructured by pattern-matching
         lambdas, e.g. `set_cap cap \\<equiv> \\<lambda>(o, c). ...` only
         binds `cap` at the equation level).
      2. Total arity from the type signature
         `<op> :: type1 \\<Rightarrow> type2 \\<Rightarrow> ... \\<Rightarrow> monad`.
         Used to pad (1)'s output to the full arity with fresh names.

    Returns None if no def block is found in spec/abstract/.
    """
    body = _find_op_def_block(op, repo_root)
    if body is None:
        return None

    # (1) named args from equation
    m_eqn = re.search(
        r'"\s*' + re.escape(op) + r'((?:\s+[a-zA-Z_][\w\']*)+)\s*\\<equiv>',
        body,
    )
    named: list[str] = m_eqn.group(1).strip().split() if m_eqn else []

    # (2) type-signature arity
    m_sig = re.search(
        re.escape(op) + r'\s*::\s*"((?:[^"\\]|\\.)+)"',
        body,
    )
    if m_sig:
        arity = _type_arity(m_sig.group(1))
        if arity > len(named):
            named = _pad_args(named, arity)

    return named or None


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: spec_op_args.py <op_name>", file=sys.stderr)
        return 2
    op = sys.argv[1]
    repo_root = Path.cwd()
    args = extract_op_args(op, repo_root)
    if args is None:
        print(f"# spec_op_args: could not extract args for op '{op}' "
              f"(no def block found, or non-standard equation form)",
              file=sys.stderr)
        return 4
    print(" ".join(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
