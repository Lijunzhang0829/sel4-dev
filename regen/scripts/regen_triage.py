#!/usr/bin/env python3
"""regen_triage.py — per-round self-introspection for the regen loop.

After every failed round, classify WHY it failed and decide what to do next,
instead of blindly feeding the same error to the proof agent again (which
burned 4 rounds on one structural bug before a human intervened). Distinguishes:

  HARNESS_STRUCTURAL  malformed theory (nested `theory`, outer/inner syntax,
                      "Bad context") -> the WRAPPER/extraction is wrong, not the
                      proof. Blind repair won't help; flag it loudly.
  FACT_DISCOVERY      "Undefined fact" -> the tool-less agent guessed a fact
                      name that doesn't exist. Isabelle SUGGESTS the real
                      (session-qualified) name in the error tail; extract it and
                      hand it back as a concrete fix.
  PROOF               genuine "Failed to finish/apply proof" -> normal repair.
  EMPTY               no attempt (agent timeout/crash).

Also computes an error SIGNATURE + attempt hash and compares to the previous
round to detect STALL (no progress) so the driver can break early / escalate.

Usage:
  regen_triage.py --attempt ATT.thy --check check.full --task task_spec.json
                  [--prev prev_triage.json] --out triage.json
Emits triage.json and prints a one-line verdict on stderr. The driver appends
`repair_hint` to the error text handed to the repair agent.
"""
import argparse, hashlib, json, os, re, sys


def norm_err_lines(text):
    """*** lines with volatile bits (temp paths, line numbers) stripped."""
    out = []
    for ln in text.splitlines():
        ln = re.sub(r'^\d+:', '', ln)              # driver's grep -n prefix
        if not ln.startswith('***'):
            continue
        ln = re.sub(r'\(line \d+ of "[^"]*"\)', '', ln)
        ln = re.sub(r'/tmp/[^\s"]+', '', ln)
        out.append(ln.strip())
    return out


def extract_undefined_facts(text):
    """Return {bad_name: [suggested qualified names]} from Undefined-fact errors.
    Isabelle flattens markup so the tail concatenates candidates; pull dotted
    qualified identifiers out of it."""
    facts = {}
    for m in re.finditer(r'Undefined fact:\s*"([^"]+)"(.*)', text):
        bad = m.group(1)
        tail = m.group(2)
        # drop the `(line N of "/tmp/...")` ref so its path isn't mistaken for a name
        tail = re.sub(r'\(line \d+ of "[^"]*"\)', '', tail)
        # Isabelle flattens the candidate markup, joining entries with the literal
        # tag `fact` and a leading count digit. Split on `fact`, strip the digit,
        # take the leading qualified name from each piece.
        cands = []
        for piece in re.split(r'fact', tail):
            piece = re.sub(r'^\s*\d+', '', piece.strip())
            m2 = re.match(r'([A-Za-z][A-Za-z0-9_\']*(?:\.[A-Za-z][A-Za-z0-9_\']*)+)', piece)
            if m2 and m2.group(1) != bad and m2.group(1) not in cands:
                cands.append(m2.group(1))
        facts[bad] = cands
    return facts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--attempt", required=True)
    ap.add_argument("--check", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--prev", default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    attempt = open(args.attempt, encoding="utf-8", errors="ignore").read() if os.path.exists(args.attempt) else ""
    check = open(args.check, encoding="utf-8", errors="ignore").read() if os.path.exists(args.check) else ""
    spec = json.load(open(args.task))

    err_lines = norm_err_lines(check)
    sig = hashlib.sha256("\n".join(sorted(set(err_lines))).encode()).hexdigest()[:12]
    att_sha = hashlib.sha256(attempt.encode()).hexdigest()[:12]

    structural = bool(re.search(r'Bad context for command|Outer syntax error|'
                                r'Inner syntax error|Inner lexical error|'
                                r'Malformed|command "theory" expected', check))
    n_theory = len(re.findall(r'(?m)^\s*theory\s+', attempt))
    if n_theory > 1:
        structural = True
    undefined = extract_undefined_facts(check)
    proof_fail = bool(re.search(r'Failed to finish proof|Failed to apply proof method|'
                                r'exception THM|no unifiers|No subgoals', check))

    if not attempt.strip():
        category = "EMPTY"
    elif structural:
        category = "HARNESS_STRUCTURAL"
    elif undefined:
        category = "FACT_DISCOVERY"
    elif proof_fail:
        category = "PROOF"
    else:
        category = "OTHER"

    # missing contract statements (anti-cheat / generation completeness)
    missing = [k["name"] for k in spec.get("contracts", [])
               if k.get("name") and not re.search(r'(?m)^\s*(lemma|theorem)\s+' + re.escape(k["name"]) + r'\b', attempt)]

    prev = json.load(open(args.prev)) if args.prev and os.path.exists(args.prev) else {}
    stalled = bool(prev) and (prev.get("error_sig") == sig or prev.get("attempt_sha") == att_sha)

    # build a concrete repair hint
    hint = []
    if category == "HARNESS_STRUCTURAL":
        hint.append("STRUCTURAL: the generated theory was malformed"
                    + (f" ({n_theory} `theory` headers)" if n_theory > 1 else "")
                    + ". Output ONLY the lemmas/defs BODY — do NOT emit a `theory ... begin` "
                      "header or a nested `theory` command; the harness adds them.")
    if undefined:
        for bad, cands in undefined.items():
            if cands:
                hint.append(f'Fact "{bad}" does NOT exist. Isabelle suggests: '
                            + ", ".join(cands[:4]) + ". Use one of these exact (session-qualified) names.")
            else:
                hint.append(f'Fact "{bad}" does NOT exist. Record accessors have no `_def`; '
                            "find the real (often session-qualified, e.g. ARM_H.*) lemma name.")
    if missing:
        hint.append("MISSING required statements (must appear verbatim and be proved): "
                    + ", ".join(missing))
    if stalled:
        hint.append("NO PROGRESS since last round — the same errors persist. Change STRATEGY: "
                    "try a different proof method / different helper lemmas, not the same script.")

    out = dict(category=category, error_sig=sig, attempt_sha=att_sha,
               stalled=stalled, structural=structural, n_theory=n_theory,
               undefined_facts=undefined, missing_contracts=missing,
               distinct_errors=len(set(err_lines)),
               repair_hint="\n".join(hint))
    json.dump(out, open(args.out, "w"), indent=2)
    print(f"[triage] category={category} stalled={stalled} "
          f"undefined={list(undefined)} missing={missing} sig={sig}", file=sys.stderr)


if __name__ == "__main__":
    main()
