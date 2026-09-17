#!/usr/bin/env python3
"""regen_agent.py — the regeneration subagent (claude -p, transcript-recorded).

Given a task_spec (theory name + imports + the KEPT statements that form the
oracle/contract surface), the agent produces a COMPLETE Isabelle theory that:
  - contains every kept statement VERBATIM,
  - proves all of them (no sorry/oops/axiomatization),
  - invents whatever PRIVATE helper lemmas/defs it needs.

Feedback loop: in --repair mode it additionally receives the previous attempt
and the Isabelle `***` error, and returns a corrected full theory. The full
`claude -p` process is streamed and the raw stream-json is saved as the audit
transcript (the evidence that the proof was regenerated, not recalled).

Usage:
  regen_agent.py --task task_spec.json --out attempt.thy --raw-out raw.ndjson
                 [--repair prev.thy --error err.txt] [--model sonnet]
                 [--timeout 1800] [--effort high]
"""
import argparse, json, os, re, subprocess, sys, time

# reuse the proven claude -p invocation from the spec-strengthen harness
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "spec-strengthen", "scripts"))
from spec_agent import find_claude, run_claude_streaming  # noqa: E402


def build_prompt(spec):
    to_prove = spec["to_prove"]
    verbatim = spec.get("verbatim", [])
    prove_txt = "\n\n".join(k["statement"] for k in to_prove)
    verb_txt = "\n\n".join(k["text"] for k in verbatim) or "(none)"
    return f"""You are regenerating the proof engineering of an seL4 Isabelle/HOL
refinement theory from scratch. The two endpoint artifacts (the abstract spec
and the Haskell executable spec) are FIXED and available through the imported
sessions. Your job is to REBUILD the proof corpus that connects them.

theory: {spec['theory_name']}
imports: {spec['imports']}

Two kinds of FIXED GROUND TRUTH follow. Your output theory MUST contain both,
and prove everything.

(A) {len(to_prove)} LEMMA STATEMENTS to reproduce VERBATIM and PROVE (these are
the refinement contract surface + the theory's exported interface). You MAY —
and generally must — introduce your OWN private helper lemmas, definitions and
`crunch`/`wp`/`simp` machinery to prove them; you are re-deriving the whole
interior, not copying the original.

(B) {len(verbatim)} DECLARATIONS to reproduce VERBATIM (definitions, `lemmas`
aliases, interpretations that pin names/facts the statements in (A) depend on).

HARD RULES:
- No `sorry`, `oops`, or `axiomatization`. Every lemma fully proved.
- Do NOT weaken, rename, or restate any fixed statement or declaration below.
- Output ONLY the complete theory source, from `theory {spec['theory_name']}`
  to the final `end`, inside a single ```isabelle code fence. No prose.

=== (A) STATEMENTS TO PROVE (bodies are yours to reconstruct) ===

{prove_txt}

=== (B) DECLARATIONS TO REPRODUCE VERBATIM ===

{verb_txt}
"""


def build_repair_prompt(spec, prev, error):
    base = build_prompt(spec)
    return base + f"""

--- YOUR PREVIOUS ATTEMPT FAILED ---
Isabelle reported (first errors):

{error[:6000]}

Your previous theory source was:

```isabelle
{prev[:20000]}
```

Fix the errors and return the COMPLETE corrected theory (same rules, same fixed
statements verbatim). Output ONLY the theory source in one ```isabelle fence.
"""


def extract_theory(text, spec):
    """Take the model's output and wrap its BODY with the KNOWN theory header
    (name + imports) and a trailing `end`. The header is fixed ground truth —
    never let the model hallucinate it (rounds died on a missing/garbled
    `theory ... begin`). We strip any header/end the model did emit, then wrap."""
    name, imports = spec["theory_name"], spec["imports"]
    m = re.search(r'```(?:isabelle|isar|thy)?\s*\n(.*?)```', text, re.S)
    body = (m.group(1) if m else text).strip("\n")
    # strip any leading license comment + `theory ... imports ... begin` header
    # the model emitted. The model often prefixes a copyright comment BEFORE
    # `theory`, so keying on "body starts with theory" misses it and we double-
    # wrap -> two `theory` commands ("Bad context for command theory"). Instead:
    # find the first standalone `begin` line; if a `theory` line precedes it,
    # drop everything through that begin (comment + header included). A
    # `context begin` line won't match `^begin$`, so this only eats the header.
    bm = re.search(r'^\s*begin\s*$', body, re.M)
    if bm and re.search(r'^\s*theory\s+', body[:bm.start()], re.M):
        body = body[bm.end():]
    # strip a single trailing top-level `end`
    body = re.sub(r'\n\s*end\s*$', '', body.rstrip()).strip("\n")
    return f"theory {name}\nimports {imports}\nbegin\n\n{body}\n\nend\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--raw-out", default=None)
    ap.add_argument("--repair", default=None, help="previous attempt .thy")
    ap.add_argument("--error", default=None, help="trial error text file")
    ap.add_argument("--model", default=os.environ.get("REGEN_MODEL", "sonnet"))
    ap.add_argument("--timeout", type=int, default=int(os.environ.get("REGEN_TIMEOUT", "1800")))
    ap.add_argument("--effort", default=os.environ.get("REGEN_EFFORT", "high"))
    args = ap.parse_args()

    spec = json.load(open(args.task))
    if args.repair:
        prev = open(args.repair, encoding="utf-8", errors="ignore").read()
        err = open(args.error, encoding="utf-8", errors="ignore").read() if args.error else "(no error captured)"
        prompt = build_repair_prompt(spec, prev, err)
        print(f"[regen_agent] REPAIR mode for theory {spec['theory_name']}", file=sys.stderr)
    else:
        prompt = build_prompt(spec)
        print(f"[regen_agent] GENERATE theory {spec['theory_name']} "
              f"({len(spec['contracts'])} contracts, {len(spec['to_prove'])} lemmas, "
              f"{len(spec.get('verbatim', []))} verbatim)",
              file=sys.stderr)

    claude = find_claude()
    max_turns = os.environ.get("REGEN_MAX_TURNS", "3")
    # `--tools ""` => ZERO tools (verified tools=0). MANDATORY for memorization
    # integrity: with Bash/Read/WebFetch the agent would just read the on-disk
    # original l4v theory and echo it — that is `cat`, not regeneration. Tool-
    # less forces reconstruction from weights only; the perturbation / oracle-
    # thinning / divergence controls then bound residual recall.
    argv = [claude, "-p", prompt, "--model", args.model,
            "--max-turns", max_turns,
            "--tools", "",
            "--output-format", "stream-json", "--verbose"]
    print(f"[regen_agent] model={args.model} effort={args.effort} "
          f"timeout={args.timeout}s claude={claude}", file=sys.stderr)

    # A transient API failure (529 Overloaded, rate limit, dropped connection)
    # comes back AS the result text — if wrapped into a theory it wastes a whole
    # round on a fake 8-line file. Detect it and RETRY with backoff instead of
    # counting it as a real (failed) attempt.
    def is_transient(raw):
        if not raw or not raw.strip():
            return True
        low = raw.lower()
        markers = ("api error", "overloaded", "529", "rate limit",
                   "connection error", "connection closed", "connection reset",
                   "service unavailable", "internal server error", "timed out")
        has_marker = any(m in low for m in markers)
        has_content = ("lemma" in low) or ("```" in raw) or ("\\<" in raw)
        return has_marker and not has_content

    max_tries = int(os.environ.get("REGEN_API_TRIES", "6"))
    out = ""
    for attempt in range(1, max_tries + 1):
        try:
            out = run_claude_streaming(argv, args.timeout, raw_path=args.raw_out)
        except subprocess.TimeoutExpired:
            out = ""
            print(f"[regen_agent] agent timed out (attempt {attempt}/{max_tries})", file=sys.stderr)
        if not is_transient(out):
            break
        back = min(180, 15 * attempt)
        print(f"[regen_agent] TRANSIENT API failure (attempt {attempt}/{max_tries}); "
              f"backing off {back}s", file=sys.stderr)
        time.sleep(back)
    else:
        # exhausted retries: write EMPTY so the driver skips this round cleanly
        # (keeps the repair chain, does not fabricate a fake attempt).
        open(args.out, "w").close()
        print(f"[regen_agent] gave up after {max_tries} transient failures; empty attempt", file=sys.stderr)
        return

    thy = extract_theory(out, spec)
    with open(args.out, "w") as f:
        f.write(thy)
    print(f"[regen_agent] wrote {args.out} ({thy.count(chr(10))+1} lines)", file=sys.stderr)


if __name__ == "__main__":
    main()
