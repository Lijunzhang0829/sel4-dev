#!/usr/bin/env python3
"""Intra-tactic CPU profiler for one slow seL4 lemma — runs INSIDE sel4-l4v.

Wraps the lemma's hottest tactic command in `time_profile ‹...›` (PolyML
ProfileTime), builds the theory with `isabelle process` (mirroring check-theory.sh
but KEEPING full stdout, which the script discards on success), and pipes the
@@PROF sample table to parse_profile.py for the search/simp/setup split.

This is the ground truth that validates v2's textual class: it measures where the
CPU time inside the tactic actually goes.

Usage (in container):
  python3 profile_lemma.py --lemma empty_slot_pas_refined [--session Access]
                           [--line N] [--keep] [--timeout 1800]
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, tempfile, shutil

ROOT = "/workspace/tools/seL4-proof-search/Isa-Repl"
L4V = os.environ.get("L4V_DIR", "/workspace/verification/l4v")
ISA = os.environ.get("ISABELLE_HOME", "/workspace/verification/isabelle")
CLASSIFIED = f"{ROOT}/runs/db_candidates_classified_v2.json"
SNIPPET = open(f"{ROOT}/profiler/time_profile.snippet.thy").read()

DIR_SESSION = [
    ("proof/access", "Access"), ("proof/infoflow", "InfoFlow"),
    ("proof/invariant-abstract", "AInvs"), ("proof/refine", "Refine"),
    ("proof/crefine", "CRefine"), ("proof/drefine", "DRefine"),
    ("proof/sep-capDL", "SepDSpec"), ("proof/capDL-api", "DSpecProofs"),
    ("proof/bisim", "Bisim"), ("proof/asmrefine", "AsmRefine"),
]


def session_for(path: str) -> str | None:
    for frag, sess in DIR_SESSION:
        if frag in path:
            return sess
    return None


def find_row(lemma: str) -> dict:
    rows = json.load(open(CLASSIFIED))
    hits = [r for r in rows if lemma in r["name_at_line"]]
    if not hits:
        sys.exit(f"lemma {lemma!r} not in {CLASSIFIED}")
    return hits[0]


def proof_span(lines: list[str], start: int) -> tuple[int, int]:
    """1-based [start,end] of the command beginning at `start`, balancing parens."""
    txt = lines[start - 1]
    depth = txt.count("(") - txt.count(")")
    end = start
    while depth > 0 and end < len(lines):
        end += 1
        depth += lines[end - 1].count("(") - lines[end - 1].count(")")
    return start, end


WRAP_RE = re.compile(r"^(\s*(?:subgoal\s+)?(?:by|apply)\s+)(.*)$", re.DOTALL)

STMT_RE = re.compile(r"^\s*(lemma|theorem|corollary|schematic_goal)\b")
# a line that begins a proof script after a statement
PROOF_START_RE = re.compile(r"^\s*(apply|by|proof|unfolding|using|supply|subgoal|done|sorry|oops|\.\.|\.)\b")


def sorry_prefix(lines, upto_line):
    """Replace each lemma/theorem PROOF script in lines[0:upto_line-1] with `sorry`
    so the prefix builds instantly (statements/decls + their asserted facts are
    kept; only the proof WORK is dropped). Leaves crunch/locale/interpretation/
    definition setup intact. Returns the new line list (same indices outside the
    replaced proof spans are NOT preserved — caller must recompute target offset:
    we only call this for the whole-file faithful build where the target proof is
    re-inserted separately). Heuristic, tuned for l4v apply-scripts."""
    out = []
    i = 0
    n = len(lines)
    limit = upto_line - 1  # 0-based exclusive boundary for the target lemma
    while i < n:
        if i >= limit:
            out.append(lines[i]); i += 1; continue
        if STMT_RE.match(lines[i]):
            # emit the statement lines until the proof starts
            out.append(lines[i]); i += 1
            while i < n and not PROOF_START_RE.match(lines[i]):
                # still inside the statement (goal string / fixes / assumes / shows)
                if STMT_RE.match(lines[i]):  # safety: new stmt w/o proof (lemmas list)
                    break
                out.append(lines[i]); i += 1
            if i >= n or not PROOF_START_RE.match(lines[i]):
                continue
            # consume the whole proof and replace it with `sorry`. Depth model:
            #   openers (proof / subgoal) raise depth; `done`/`qed` lower it. A
            #   `done`/`qed` at depth 0 (or a `by`/`.`/`..` at depth 0) is the
            #   script TERMINAL. Parens must balance (a `by (...)` spans lines).
            indent = re.match(r"^(\s*)", lines[i]).group(1)
            depth = 0
            paren = 0
            while i < n:
                line = lines[i]
                s = line.strip()
                paren += line.count("(") - line.count(")")
                i += 1
                if paren > 0:
                    continue  # inside a multi-line method/term — keep going
                paren = 0
                depth += len(re.findall(r"(?<![\w.])(?:proof|subgoal)\b", s))
                closers = len(re.findall(r"\bdone\b", s)) + len(re.findall(r"\bqed\b", s))
                terminal = False
                while closers > 0 and depth > 0:
                    depth -= 1; closers -= 1
                if closers > 0:            # a done/qed beyond open blocks ends script
                    terminal = True
                elif depth == 0 and re.match(r"^(by|sorry|oops|\.\.|\.)\b", s):
                    terminal = True        # by-terminal; bare `by` keeps paren open
                if terminal:
                    # `by (m1) (m2...)`: another method group on the next line(s)
                    nxt = lines[i].strip() if i < n else ""
                    if nxt.startswith("(") and not nxt.startswith("(*"):
                        continue
                    break
                # safety: implicit end at a new statement/decl boundary
                if depth == 0 and i < n and (STMT_RE.match(lines[i]) or re.match(
                        r"^(definition|fun|lemma|lemmas|crunch|crunches|locale|context|"
                        r"interpretation|sublocale|end|declare|text|section|subsection|"
                        r"named_theorems|abbreviation|primrec|method|notation)\b",
                        lines[i].strip())):
                    break
            out.append(indent + "sorry")
        else:
            out.append(lines[i]); i += 1
    return out


def wrap_proof(span_text: str) -> str:
    m = WRAP_RE.match(span_text)
    if not m:
        sys.exit(f"could not parse proof keyword in:\n{span_text!r}")
    # use the ASCII cartouche form \<open>..\<close> (this Isabelle setup rejects
    # literal U+2039/U+203A in theory source; the ASCII form is known-good)
    return f"{m.group(1)}(time_profile \\<open>{m.group(2)}\\<close>)"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lemma", required=True, help="label for output files; also json key unless --file given")
    ap.add_argument("--file", help="theory path (container); bypass json lookup")
    ap.add_argument("--session")
    ap.add_argument("--line", type=int, help="proof line to wrap (hottest cmd)")
    ap.add_argument("--lemma-line", type=int, dest="lemma_line",
                    help="line to insert method_setup before (the `lemma` keyword)")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--keep", action="store_true", help="keep the patched temp theory")
    ap.add_argument("--ends", type=int, default=0,
                    help="truncate file after the proof, append N `end` (close locale/theory)")
    ap.add_argument("--no-profile", action="store_true",
                    help="CONTROL: build truncated file with ORIGINAL proof (no wrap/snippet)")
    ap.add_argument("--write-only", action="store_true",
                    help="write the patched theory and print 'WRITTEN tmpd tmpname session L4V', then stop")
    ap.add_argument("--sorry-prefix", action="store_true",
                    help="replace every proof before the target lemma with `sorry` (cheap reach)")
    args = ap.parse_args()

    if args.file:
        thy = args.file
        if not (args.line and args.lemma_line):
            sys.exit("--file mode needs --line and --lemma-line")
        lemma_line = args.lemma_line
        hot_line = args.line
    else:
        row = find_row(args.lemma)
        thy = row["file"]
        lemma_line = int(row["lemma_line"])
        hot_line = args.line or int(row["hot_cmds"][0]["line"])
    session = args.session or session_for(thy)
    if not session:
        sys.exit(f"can't infer session for {thy}; pass --session")

    src = open(thy, encoding="utf-8").read().split("\n")
    start, end = proof_span(src, hot_line)
    span_text = "\n".join(src[start - 1:end])
    wrapped = wrap_proof(span_text)

    print(f"[profile] lemma={args.lemma} session={session} "
          f"{'CONTROL(no-profile)' if args.no_profile else 'PROFILE'}", file=sys.stderr)
    print(f"[profile] file={thy}", file=sys.stderr)
    print(f"[profile] proof lines {start}-{end}:", file=sys.stderr)
    print(f"    ORIG: {span_text[:200]}", file=sys.stderr)
    if not args.no_profile:
        print(f"    WRAP: {wrapped[:200]}", file=sys.stderr)

    # 1. wrap the target proof (or keep original for --no-profile), on full src.
    if args.no_profile:
        wsrc = list(src)
    else:
        wsrc = src[:start - 1] + [wrapped] + src[end:]
    # 2. optional sorry-prefix: collapse every proof BEFORE the target lemma to
    #    `sorry` so reaching the target is near-instant while its 1093-line context
    #    (statements, decls, asserted facts) is preserved. lemma_line is unchanged
    #    by step 1 (it precedes the wrap), so it's the right boundary here.
    if args.sorry_prefix:
        wsrc = sorry_prefix(wsrc, lemma_line)
    # 3. --ends>0: truncate after the (already-wrapped) target and close N blocks.
    if args.ends:
        # find the wrapped target line, keep through it, then close
        tgt = next((idx for idx, l in enumerate(wsrc) if "time_profile" in l), None)
        if tgt is not None:
            wsrc = wsrc[:tgt + 1] + [""] + ["end"] * args.ends
    # 4. inject method_setup at THEORY TOP LEVEL (right after `begin`) so
    #    `time_profile` is a global method (method_setup inside a locale block
    #    produced "Malformed theory").
    if args.no_profile:
        patched = wsrc
    else:
        begin_line = next((i + 1 for i, l in enumerate(wsrc) if l.strip() == "begin"), None)
        if begin_line is None:
            sys.exit("could not find theory `begin` to inject method_setup")
        patched = wsrc[:begin_line] + SNIPPET.split("\n") + wsrc[begin_line:]
    body = "\n".join(patched)

    base = os.path.basename(thy)[:-4]
    tmpd = tempfile.mkdtemp(prefix="prof_")
    tmpname = "Tmp_prof_" + os.urandom(6).hex()
    tmpfile = os.path.join(tmpd, tmpname + ".thy")
    body = re.sub(rf"^theory {re.escape(base)}\b", f"theory {tmpname}", body, count=1, flags=re.M)

    # qualify bare imports with session (mirror check-theory.sh)
    mm = re.search(r"(imports\s*\n?)(.*?)(begin)", body, re.DOTALL)
    if mm:
        imp = mm.group(2)
        out, i = [], 0
        while i < len(imp):
            ch = imp[i]
            if ch == '"':
                j = imp.index('"', i + 1) + 1
                out.append(imp[i:j]); i = j
            elif ch.isspace():
                out.append(ch); i += 1
            else:
                j = i
                while j < len(imp) and not imp[j].isspace():
                    j += 1
                tok = imp[i:j]
                out.append(tok if "." in tok else f"{session}.{tok}")
                i = j
        body = body[:mm.start(2)] + "".join(out) + body[mm.end(2):]

    open(tmpfile, "w", encoding="utf-8").write(body)
    print(f"[profile] patched theory: {tmpfile}", file=sys.stderr)

    if args.write_only:
        # emit run coordinates for the external poller (run_profile.sh) and stop
        print(f"WRITTEN\t{tmpd}\t{tmpname}\t{session}\t{L4V}")
        return

    env = os.environ.copy()
    env["L4V_ARCH"] = env.get("L4V_ARCH", "ARM")
    # serialize on the session heap (same lock check-theory.sh uses).
    # time_profile makes `isabelle process` HANG after the theory completes, so we
    # use `timeout -s KILL` and redirect output straight to a FILE — the profile is
    # flushed BEFORE the hang, so a KILL still leaves the data on disk (a python
    # PIPE capture would lose it on timeout).
    lock = f"/tmp/isabelle-session-{session}.lock"
    rawpath = f"{ROOT}/profiler/raw-{args.lemma}.out"
    cmd = ["flock", lock, "timeout", "-s", "KILL", str(args.timeout),
           f"{ISA}/bin/isabelle", "process", "-l", session, "-d", L4V,
           "-T", os.path.join(tmpd, tmpname)]
    print(f"[profile] running (KILL timeout {args.timeout}s): isabelle process -l {session} -T {tmpname}",
          file=sys.stderr)
    with open(rawpath, "w") as rf:
        p = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL,
                           stdout=rf, stderr=subprocess.STDOUT)
    print(f"[profile] isabelle exit={p.returncode} (137=KILLed after hang, output still captured)",
          file=sys.stderr)
    output = open(rawpath, encoding="utf-8", errors="replace").read()
    print(f"[profile] raw output -> {rawpath}  ({output.count('@@PROF')} @@PROF lines)", file=sys.stderr)

    if not args.keep:
        shutil.rmtree(tmpd, ignore_errors=True)

    # parse
    jsonpath = f"{ROOT}/profiler/result-{args.lemma}.json"
    pp = subprocess.run(["python3", f"{ROOT}/profiler/parse_profile.py", rawpath,
                         "--json", jsonpath], capture_output=True, text=True)
    print(pp.stdout)
    if pp.returncode != 0:
        print(pp.stderr, file=sys.stderr)
        # surface build errors if no profile data
        errs = [l for l in output.splitlines() if l.startswith("***")][:15]
        if errs:
            print("\n[profile] build *** lines:", file=sys.stderr)
            print("\n".join(errs), file=sys.stderr)


if __name__ == "__main__":
    main()
