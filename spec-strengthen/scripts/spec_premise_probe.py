#!/usr/bin/env python3
"""
tools/spec_strengthen/spec_premise_probe.py

Optional Step 1.5 helper: TRIAL-based premise-usage probe via
Isa-REPL. Ground truth (not heuristic) — synthesizes a copy of
the target lemma with the suspect premise removed from the
precondition, then runs the ORIGINAL proof body against it
inside a hot Isa-REPL session.

  - If the proof closes        → premise is **unused** in the
                                  proof; safe to drop.
  - If any proof step fails    → premise is **load-bearing**;
                                  the proof depended on it.

This is the same correctness signal as `check-theory.sh --patch`,
just driven through Isa-REPL so multiple probes on the same
session can reuse the heap-loaded JVM (future work — current v1
spins one JVM per invocation; per-probe wall ≈ 40-60s).

v0 (substring goal-state heuristic) was rejected: differential
test showed it could not distinguish unused from load-bearing
premises because Isabelle keeps the full precondition visible in
intermediate subgoals — see `reports/experiments/0011-*`.

Verdicts:
  likely-unused      — synthesized lemma's proof closes; premise
                       can be dropped (CONFIRM with full
                       check-theory.sh on the resulting patch).
  load-bearing       — synthesized lemma's proof FAILS at some
                       step; premise is consumed by tactic.
  parse-error        — could not extract conjunct from precondition.
  premise-not-found  — suspect premise NAME isn't in the precondition
                       (candidate is stale).
  uncertain          — Isa-REPL infrastructure error / unexpected
                       failure mode; fall back to check-theory.sh.

Invocation (inside sel4-l4v container):
  python3 /workspace/tools/spec_strengthen/spec_premise_probe.py \\
    --theory <abs-path>.thy --lemma <name> --premise <suspect>

Or via the host wrapper:
  bash tools/spec_strengthen/spec_premise_probe.sh \\
    <relative-thy-path> <lemma_name> <suspect_premise>
"""

from __future__ import annotations
import argparse
import os
import re
import sys
import time
from pathlib import Path


# Session mapping mirrors parent SKILL.md
SESSION_MAP = [
    ("spec/abstract/",            "ASpec"),
    ("spec/cspec/",               "CSpec"),
    ("proof/invariant-abstract/", "AInvs"),
    ("proof/refine/",             "Refine"),
    ("proof/crefine/",            "CRefine"),
    ("proof/access-control/",     "Access"),
    ("proof/infoflow/",           "InfoFlow"),
    ("proof/drefine/",            "DRefine"),
    ("proof/bisim/",              "Bisim"),
]


def detect_session(theory_path: Path) -> str | None:
    p = str(theory_path)
    for prefix, sess in SESSION_MAP:
        if prefix in p:
            return sess
    return None


# ---- Lemma block extraction (raw text) -------------------------------------

LEMMA_HEADER_RE = re.compile(
    r"^(?P<indent>\s*)(?P<kw>lemma|theorem|corollary)s?\s+"
    r"(?P<name>[A-Za-z_][A-Za-z_0-9']*)\s*(?P<attrs>\[[^\]]*\])?\s*:",
    re.MULTILINE,
)

PROOF_END_RE = re.compile(r"^\s*(?:done|qed|sorry|oops)\b", re.MULTILINE)


def find_lemma_block(theory_text: str, lemma_name: str) -> dict | None:
    """Locate the target lemma's text block and split it into
    (header_text, statement_text, proof_text). Returns None if
    structure can't be parsed."""
    for m in LEMMA_HEADER_RE.finditer(theory_text):
        if m.group("name") != lemma_name:
            continue
        header_start = m.start()
        stmt_start = m.end()  # right after the colon
        # Statement is everything from after the colon up to the
        # first proof keyword
        proof_kw = re.search(
            r"^\s*(?:apply|by|proof|using|including)\b",
            theory_text[stmt_start:], re.MULTILINE,
        )
        if not proof_kw:
            return None
        proof_start = stmt_start + proof_kw.start()
        # Proof body is from proof_start to first end token
        proof_end_m = PROOF_END_RE.search(theory_text, proof_start)
        if not proof_end_m:
            return None
        # Include the end-token's full line
        end_of_line = theory_text.find("\n", proof_end_m.end())
        if end_of_line == -1:
            end_of_line = len(theory_text)
        return {
            "indent":   m.group("indent"),
            "attrs":    m.group("attrs") or "",
            "header_start": header_start,
            "stmt_start":   stmt_start,
            "proof_start":  proof_start,
            "block_end":    end_of_line,
            "statement":    theory_text[stmt_start:proof_start].strip(),
            "proof":        theory_text[proof_start:end_of_line],
        }
    return None


# ---- Statement transformer: drop a conjunct from pre ----------------------

# A statement looks like (after stripping quotes):
#   "\<lbrace>P\<rbrace> body \<lbrace>Q\<rbrace>"
# Pre/post may have lambda binders: `\<lambda>s. ...`. The conjunct
# token to drop is matched by NAME boundary anywhere inside the
# pre — and the surrounding " \<and> " / " and " is collapsed.

PRE_RE = re.compile(
    r'^(?P<lq>")\s*\\<lbrace>(?P<pre>.*?)\\<rbrace>(?P<rest>.*?)(?P<rq>")\s*$',
    re.DOTALL,
)


def normalize_quotes(stmt: str) -> str:
    """Strip whitespace and ensure the statement is one " "-quoted
    string. l4v lemmas use the quote form `"..."` (no triple)."""
    return stmt.strip()


def drop_conjunct_from_pre(stmt: str, premise: str) -> tuple[str, str]:
    """Return (new_stmt, status). status is "ok" if a conjunct
    mentioning `premise` was dropped, "not-found" if no conjunct
    matched, "ambiguous" if multiple conjuncts matched."""
    stmt_norm = normalize_quotes(stmt)
    m = PRE_RE.match(stmt_norm)
    if not m:
        return stmt, "parse-error"

    pre_text = m.group("pre")
    # Detect leading lambda binder; preserve it.
    binder_m = re.match(r'\s*(\\<lambda>[^.]+\.\s*)(.*)$',
                        pre_text, re.DOTALL)
    if binder_m:
        binder, body = binder_m.group(1), binder_m.group(2)
    else:
        binder, body = "", pre_text

    # Split on top-level `\<and>` / ` and ` conjunctions.
    # Naive: assume conjunctions are not nested under quantifiers.
    # (Good enough for the common spec-strengthening case; if
    # complex, fall back to "parse-error".)
    parts = re.split(r'\s*\\<and>\s*|\s+and\s+', body)
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return stmt, "parse-error"

    # Find parts mentioning `premise` as a whole word.
    pat = re.compile(rf'\b{re.escape(premise)}\b')
    hits = [i for i, p in enumerate(parts) if pat.search(p)]
    if not hits:
        return stmt, "not-found"
    if len(hits) > 1:
        # Multiple conjuncts match — ambiguous; refuse to guess.
        return stmt, "ambiguous"

    drop_idx = hits[0]
    kept = [p for i, p in enumerate(parts) if i != drop_idx]
    if not kept:
        # Dropping would leave an empty precondition — replace with
        # `\<top>` (l4v idiom for "no precondition").
        new_pre_body = "\\<top>"
        binder = ""  # \<top> doesn't take a state binder
    else:
        new_pre_body = " \\<and> ".join(kept)
    new_pre = binder + new_pre_body
    new_stmt = (
        f'{m.group("lq")}\\<lbrace>{new_pre}\\<rbrace>'
        f'{m.group("rest")}{m.group("rq")}'
    )
    return new_stmt, "ok"


# ---- Trial driver ---------------------------------------------------------

def run_trial(theory_path: Path, lemma_name: str,
              suspect_premise: str,
              port: int = 25559,
              startup_wait: float = 14.0) -> dict:
    """Synthesize a probe lemma + run its proof. Verdict from
    proof success/failure."""

    sys.path.insert(0, "/workspace/tools/seL4-proof-search/Isa-Repl")
    from isarepl_client import IsaRepl  # noqa: E402

    session = detect_session(theory_path)
    if session is None:
        return {
            "verdict": "uncertain",
            "reason": f"could not derive session from path {theory_path}",
        }

    theory_text = theory_path.read_text(encoding="utf-8", errors="replace")
    block = find_lemma_block(theory_text, lemma_name)
    if block is None:
        return {
            "verdict": "uncertain",
            "reason": f"lemma `{lemma_name}` not found or "
                      f"structure unparseable",
        }

    # Synthesize the weakened statement.
    new_stmt, status = drop_conjunct_from_pre(
        block["statement"], suspect_premise
    )
    if status == "not-found":
        return {
            "verdict": "premise-not-found",
            "reason": f"premise `{suspect_premise}` not in any "
                      f"top-level conjunct of lemma's precondition "
                      f"(candidate may be stale)",
        }
    if status == "ambiguous":
        return {
            "verdict": "uncertain",
            "reason": f"premise `{suspect_premise}` appears in "
                      f"multiple conjuncts — refusing to guess "
                      f"which to drop",
        }
    if status != "ok":
        return {
            "verdict": "parse-error",
            "reason": f"could not split precondition (status="
                      f"{status})",
        }

    # Synthesized probe lemma command.
    probe_header = (
        f"lemma {lemma_name}_probe:\n"
        f"  {new_stmt}\n"
    )

    # ---- Drive Isa-REPL ----
    real_thy_path = str(theory_path)
    t0 = time.monotonic()
    try:
        with IsaRepl(session=session, port=port,
                     startup_wait=startup_wait) as r:
            ok, msg = r.init(real_thy_path)
            if not ok:
                return {
                    "verdict": "uncertain",
                    "reason": f"Isa-REPL init failed: {msg[:200]}",
                }
            init_secs = time.monotonic() - t0

            try:
                steps = r.steps_of(real_thy_path)
            except Exception as e:
                return {
                    "verdict": "uncertain",
                    "reason": f"steps_of failed: {e!r}",
                }
            steps = [s for s in steps if s and s not in ("True", "False")]

            # Find target lemma's step.
            target_idx = None
            for i, s in enumerate(steps):
                if re.search(
                    rf"\b(?:lemma|theorem|corollary)s?\s+"
                    rf"{re.escape(lemma_name)}\b", s,
                ):
                    target_idx = i
                    break
            if target_idx is None:
                return {
                    "verdict": "uncertain",
                    "reason": "target lemma step not found in parsed steps",
                }

            # Step the preamble (everything BEFORE the target lemma).
            preamble_failures = 0
            for s in steps[:target_idx]:
                ok, _ = r.step(s)
                if not ok:
                    preamble_failures += 1
            preamble_secs = time.monotonic() - t0

            # Now in the right context. Step the synthesized
            # probe lemma header — should enter proof mode.
            ok, st = r.step(probe_header)
            if not ok:
                return {
                    "verdict": "load-bearing",
                    "reason": (
                        f"synthesized lemma `{lemma_name}_probe` "
                        f"with `{suspect_premise}` dropped failed "
                        f"to TYPE-CHECK; the premise is part of the "
                        f"type signature's well-formedness. "
                        f"Isa-REPL msg: {st[:160]}"
                    ),
                    "preamble_failures": preamble_failures,
                    "init_secs": round(init_secs, 1),
                    "preamble_secs": round(preamble_secs, 1),
                    "total_secs": round(time.monotonic() - t0, 1),
                }

            # Step the original proof body, one tactic at a time.
            proof_steps = _split_proof_body(block["proof"])
            failure_step = None
            for i, s in enumerate(proof_steps):
                ok, st = r.step(s)
                if not ok:
                    failure_step = (i, s.splitlines()[0][:120], st[:200])
                    break

            total_secs = time.monotonic() - t0
            base_meta = {
                "preamble_failures": preamble_failures,
                "preamble_steps": target_idx,
                "proof_steps": len(proof_steps),
                "init_secs": round(init_secs, 1),
                "preamble_secs": round(preamble_secs, 1),
                "total_secs": round(total_secs, 1),
                "synthesized_pre_preview": new_stmt[:200],
            }

            if failure_step is None:
                return {
                    "verdict": "likely-unused",
                    "reason": (
                        f"synthesized lemma's proof closed without "
                        f"the `{suspect_premise}` premise — premise "
                        f"is NOT load-bearing. Confirm with "
                        f"check-theory.sh --patch."
                    ),
                    **base_meta,
                }
            return {
                "verdict": "load-bearing",
                "reason": (
                    f"proof failed at step {failure_step[0]+1}/"
                    f"{len(proof_steps)} (`{failure_step[1]}`) "
                    f"after dropping `{suspect_premise}` — "
                    f"premise is consumed by this tactic."
                ),
                "failure_msg": failure_step[2],
                **base_meta,
            }
    except Exception as e:
        return {
            "verdict": "uncertain",
            "reason": f"Isa-REPL session error: {e!r}",
        }


def _split_proof_body(proof_text: str) -> list[str]:
    """Split a proof script into individual tactic commands.
    Naive: split on lines starting with a top-level Isar keyword."""
    lines = proof_text.splitlines()
    steps: list[list[str]] = []
    cur: list[str] = []
    for ln in lines:
        if re.match(r"^\s*(?:apply|by|proof|qed|done|sorry|oops|"
                    r"using|including|moreover|ultimately|next|"
                    r"fix|assume|then|hence|thus|show)\b", ln):
            if cur:
                steps.append(cur)
            cur = [ln]
        else:
            if cur:
                cur.append(ln)
    if cur:
        steps.append(cur)
    return ["\n".join(s).rstrip() for s in steps if any(l.strip() for l in s)]


# ---- CLI -------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="TRIAL-based premise-usage probe: drop a "
                    "suspect premise from the lemma's pre, run the "
                    "ORIGINAL proof, see if it still closes."
    )
    ap.add_argument("--theory", type=Path, required=True,
                    help="Absolute path to .thy file (in-container path)")
    ap.add_argument("--lemma", required=True,
                    help="Target lemma name")
    ap.add_argument("--premise", required=True,
                    help="Suspect premise NAME (e.g. invs, valid_objs)")
    ap.add_argument("--port", type=int, default=25559)
    ap.add_argument("--startup-wait", type=float, default=14.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not args.theory.exists():
        print(f"theory not found: {args.theory}", file=sys.stderr)
        return 4

    result = run_trial(
        args.theory, args.lemma, args.premise,
        port=args.port,
        startup_wait=args.startup_wait,
    )

    if args.json:
        import json
        print(json.dumps(result, indent=2))
    else:
        print(f"# spec_premise_probe (TRIAL) — {args.lemma} / {args.premise}")
        print(f"# Theory: {args.theory}")
        for k, v in result.items():
            print(f"{k}: {v}")

    v = result["verdict"]
    return {
        "likely-unused":   0,
        "load-bearing":    1,
        "premise-not-found": 2,
        "parse-error":     3,
        "uncertain":       4,
    }.get(v, 4)


if __name__ == "__main__":
    sys.exit(main())
