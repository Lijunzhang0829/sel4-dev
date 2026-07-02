#!/usr/bin/env python3
"""localize_agent.py — fork-① harness, agent half (localizer).

Input:  a case dir (delta_artifact.diff, meta.json) + the l4v repo.
        The agent sees ONLY the artifact change — never delta_proof.diff.
Output: work-list JSON {"work_list":[{"file":..,"lemmas":[{"name":..,
        "statement_must_evolve":bool,"reason":..}]}]} + full transcript.

Deterministic context assembly (identifiers + grep candidates) feeds a
claude -p call. Python 3.8 compatible.
"""
import argparse, json, os, re, subprocess, sys

DEF_RE = re.compile(
    r"^[-+]\s*(?:definition|abbreviation|fun|primrec|function|datatype|record|"
    r"type_synonym|consts|crunch(?:es)?)\b(?:\s*\(\s*input\s*\))?\s+\"?"
    r"([A-Za-z_][A-Za-z0-9_']*)")
QUOTED_DEF_RE = re.compile(r"^[-+]\s*\"([A-Za-z_][A-Za-z0-9_']*)\s")


def git(repo, *args, ok_fail=False):
    r = subprocess.run(["git", "-C", repo] + list(args),
                       capture_output=True, text=True)
    if r.returncode != 0 and not ok_fail:
        raise RuntimeError(r.stderr[:300])
    return r.stdout


HUNK_CTX_RE = re.compile(
    r"^@@[^@]*@@\s+(?:(?:definition|abbreviation|fun|primrec|function|"
    r"lemma|theorem)\s+(?:\(\s*input\s*\)\s+)?)?\"?([A-Za-z_][A-Za-z0-9_']*)")


def changed_identifiers(diff_text):
    """Definition-header names on +/- lines, PLUS the enclosing definition
    from each hunk context header (catches edits inside a definition body)."""
    ids = []

    STOP = {"where", "and", "shows", "assumes", "fixes", "lemmas", "lemma",
            "definition", "abbreviation", "text", "section", "subsection",
            "end", "context", "locale", "theory", "imports", "begin"}

    def add(name):
        if name and name not in STOP and name not in ids:
            ids.append(name)

    for ln in diff_text.split("\n"):
        if ln.startswith("+++") or ln.startswith("---"):
            continue
        if ln.startswith("@@"):
            m = HUNK_CTX_RE.match(ln)
            if m:
                add(m.group(1))
            continue
        m = DEF_RE.match(ln) or QUOTED_DEF_RE.match(ln)
        if m:
            add(m.group(1))
    return ids


ARCH_DIRS = ("AARCH64", "ARM", "ARM_HYP", "RISCV64", "X64")


def arches_of(paths):
    s = set()
    for p in paths:
        for a in ARCH_DIRS:
            if "/%s/" % a in p:
                s.add(a)
    return s


def grep_candidates(repo, rev, idents, artifact_files, limit=40):
    """proof/ files at rev referencing any changed identifier.
    Deterministic arch filter: if the artifact change is arch-specific,
    drop candidates living under OTHER arch dirs (they cannot be affected)."""
    changed_arches = arches_of(artifact_files)
    hits = {}
    for ident in idents:
        out = git(repo, "grep", "-l", "-w", ident, rev, "--", "proof/",
                  ok_fail=True)
        for line in out.split("\n"):
            if ":" not in line:
                continue
            path = line.split(":", 1)[1]
            if changed_arches:
                cand_arches = arches_of([path])
                if cand_arches and not (cand_arches & changed_arches):
                    continue  # other-arch file — cannot be affected
            hits.setdefault(path, set()).add(ident)
    ranked = sorted(hits.items(), key=lambda kv: -len(kv[1]))[:limit]
    return [{"file": f, "mentions": sorted(s)} for f, s in ranked]


PROMPT = """You are a seL4/l4v proof-maintenance analyst. An upstream ARTIFACT \
change (spec/machine/haskell) was applied; the proofs have NOT been updated yet. \
Predict the repair work-list.

## The artifact change (unified diff)
```diff
{diff}
```

## Identifiers whose definitions changed (mechanically extracted)
{idents}

## Candidate proof files that mention these identifiers (grep at pre-change rev)
{cands}

## Your task
Predict the work-list a seL4 PROOF MAINTAINER would carry out for this \
change — which proof files need edits, which lemma blocks, and for each \
lemma whether its STATEMENT must evolve versus a body-only re-proof.

Rules:
- **Maintainer-aligned, not merely compiler-forced.** If the spec's semantics \
shifted (e.g. a boundary guard), lemmas whose statements express the OLD \
semantics must be evolved to track the new one, EVEN IF the old proof might \
still compile. Predict what the maintainer changes, not only what fails.
- **work_list contains ONLY files that need edits.** A file you examined and \
judged fine must NOT appear in work_list (mention it in a separate top-level \
key "cleared": [{{"file":.., "why":..}}]).
- **Breakage stops at unchanged lemma statements.** A downstream file needs \
no edit if it only uses lemmas whose statements survive.
- STAY WITHIN the candidate list unless you have strong structural evidence \
(crunch/locale fallout) — and NEVER pick a file merely because its name \
resembles the changed spec file (e.g. Decode_A -> Decode_AI): breakage \
follows the REFERENCES (the grep evidence), not file naming.
- Base every claim on the diff and the files you inspect; cite the evidence.
- **Budget: inspect at most 6 of the most promising candidates.** Rank by \
mention density and session proximity to the changed layer; judge the rest \
from the grep evidence alone. Answer within the budget rather than \
exhaustively verifying everything.

Respond with ONLY a JSON object, no markdown fence:
{{"work_list": [{{"file": "<path>", "lemmas": [{{"name": "<lemma name>", \
"statement_must_evolve": true/false, "reason": "<one line>"}}], \
"file_reason": "<one line>"}}]}}
If a file needs only mechanical edits outside lemmas (e.g. a crunches list), \
use lemma name "<crunch-or-setup>".
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--l4v", required=True)
    ap.add_argument("--case", required=True, dest="case_dir")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    meta = json.load(open(os.path.join(args.case_dir, "meta.json")))
    diff = open(os.path.join(args.case_dir, "delta_artifact.diff")).read()
    idents = changed_identifiers(diff)
    cands = grep_candidates(args.l4v, meta["parent"], idents,
                            meta["artifact_files"])

    prompt = PROMPT.format(
        diff=diff[:60000], idents=json.dumps(idents),
        cands="\n".join("- %s  (mentions: %s)" % (c["file"], ", ".join(c["mentions"]))
                        for c in cands) or "(none found)")

    ctx = {"identifiers": idents, "candidates": cands}
    json.dump(ctx, open(os.path.join(args.case_dir, "localizer_context.json"), "w"),
              indent=1)

    import shutil
    claude_bin = (os.environ.get("CLAUDE_BIN") or shutil.which("claude")
                  or next((p for p in ("/home/lijun/.local/bin/claude",
                                       "/usr/local/bin/claude")
                           if os.path.exists(p)), "claude"))
    r = subprocess.run(
        [claude_bin, "-p", prompt, "--model", args.model],
        capture_output=True, text=True, timeout=args.timeout)
    raw = r.stdout.strip()
    with open(os.path.join(args.case_dir, "localizer_transcript.txt"), "w") as f:
        f.write("PROMPT (%d chars, diff truncated at 60k)\n%s\n\n=== RAW REPLY ===\n%s\n=== STDERR ===\n%s\n"
                % (len(prompt), prompt[:2000], raw, r.stderr[:2000]))

    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        print("AGENT-FAIL: no JSON in reply (see localizer_transcript.txt)")
        sys.exit(3)
    # Isabelle syntax (\<lambda> etc.) inside JSON strings is an invalid
    # escape — repair any backslash not starting a legal JSON escape.
    txt = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", m.group(0))
    pred = json.loads(txt)
    json.dump(pred, open(os.path.join(args.case_dir, "localizer_pred.json"), "w"),
              indent=1)
    print("OK localizer: %d files predicted, identifiers=%s"
          % (len(pred.get("work_list", [])), ",".join(idents)))


if __name__ == "__main__":
    main()
