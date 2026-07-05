#!/usr/bin/env python3
"""repair_driver.py v2 — Step-2 repair experiment (A drives, B oracle).

v2: prompt via STDIN (argv 128KB limit); error-centric window for big files;
CUMULATIVE repair (keep progress across rounds); per-call wall logging.
Canonical copy lives in B:coevolve/scripts/ — A's /tmp scratchpad is volatile.
"""
import difflib, json, os, re, subprocess, sys, time

SSH = ["ssh", "-o", "BatchMode=yes", "zljj@114.212.82.216"]
B_REPO = "/data/zljj/sel4-dev"
CT = "/workspace/.claude/skills/isabelle_prover/scripts-container/check-theory.sh"
ENVS = ["-e", "L4V_ARCH=AARCH64", "-e", "L4V_DIR=/sel4-project/verification/l4v"]
CLAUDE = os.environ.get("CLAUDE_BIN", "/home/lijun/.local/bin/claude")
MAX_ROUNDS = 3
WINDOW = 260
BIG = 900
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repair-results")

BLOCK_RE = re.compile(r"<<<<SEARCH\n(.*?)\n====\n(.*?)>>>>", re.S)


def ssh(cmd, stdin=None, timeout=1800):
    r = subprocess.run(SSH + [cmd], input=stdin, capture_output=True,
                       text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def b_read(path):
    rc, out, err = ssh("cat %s" % path)
    assert rc == 0, err[:200]
    return out


def b_write(path, content):
    rc, _, err = ssh("cat > %s" % path, stdin=content)
    assert rc == 0, err[:200]


def check(gt_file, patch_container_path):
    # harness item #9: this file's failure buries the *** block under a
    # >1200-line goal dump — tailing stdout starves the agent of the error.
    # Capture FULL output to a file on B, then grep the *** section from it.
    inner = ("docker compose exec -T %s l4v bash %s "
             "/sel4-project/verification/l4v/%s AInvs --patch %s"
             % (" ".join(ENVS), CT, gt_file, patch_container_path))
    cmd = ("cd %s && %s > /tmp/ct-full.out 2>&1; rc=$?; "
           "tail -4 /tmp/ct-full.out; echo __ERRSEC__; "
           "grep -m1 -A80 '^\*\*\*' /tmp/ct-full.out | head -100; exit $rc"
           % (B_REPO, inner))
    rc, out, err = ssh(cmd, timeout=2400)
    head, _, errsec = out.partition("__ERRSEC__")
    green = bool(re.search(r"^OK\b", head, re.M))
    return ("GREEN" if green else "RED"), (errsec.strip() or head)


def extract_err(out):
    # keep the *** block AND the goal-state dump that follows it (the ***
    # header can sit hundreds of lines above the tail — grab from first ***)
    idx = out.find("***")
    if idx >= 0:
        return out[idx:idx + 3500]
    return out[-1500:]


def err_line_no(err):
    m = re.search(r"line (\d+) of", err)
    return int(m.group(1)) if m else None


def make_view(content, err):
    lines = content.split("\n")
    if len(lines) <= BIG:
        return content, "the complete file"
    ln = err_line_no(err) or len(lines) // 2
    lo, hi = max(0, ln - 1 - WINDOW), min(len(lines), ln - 1 + WINDOW)
    view = "\n".join(lines[:50]) + \
        "\n\n(... %d lines omitted ...)\n\n" % (lo - 50 if lo > 50 else 0) + \
        "\n".join(lines[lo:hi]) + \
        "\n\n(... %d lines omitted to end of file ...)" % (len(lines) - hi)
    return view, ("a WINDOW around the failing region (lines %d-%d of %d; "
                  "anchor edits ONLY on text visible here)" % (lo + 1, hi, len(lines)))


def make_patch(orig, new):
    """check-theory patch: minimal hunks (whole-file payload trips the
    parser when file content contains a literal '---' line)."""
    ol, nl = orig.split("\n"), new.split("\n")
    sm = difflib.SequenceMatcher(None, ol, nl, autojunk=False)
    hunks = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        repl = nl[j1:j2]
        if i1 == i2:            # pure insert: anchor on previous line
            if i1 > 0:
                start, end, repl = i1, i1, [ol[i1 - 1]] + repl
            else:
                start, end, repl = 1, 1, repl + [ol[0]]
        elif not repl:          # pure delete: rewrite prev line to keep block non-empty
            if i1 > 0:
                start, end, repl = i1, i2, [ol[i1 - 1]]
            else:
                start, end, repl = 1, i2, []
        else:
            start, end = i1 + 1, i2
        assert all(l.strip() != "---" for l in repl), "replacement contains ---"
        hunks.append("%d %d\n%s" % (start, end, "\n".join(repl)))
    return "\n---\n".join(hunks) + "\n"


def anchor_hint(content, search):
    first = search.split("\n")[0].strip()
    lines = content.split("\n")
    m = difflib.get_close_matches(first, [l.strip() for l in lines], n=1, cutoff=0.5)
    if not m:
        return "(no similar line found)"
    idx = [l.strip() for l in lines].index(m[0])
    return "\n".join(lines[max(0, idx - 2):idx + 6])


PROMPT = """You are repairing seL4/l4v proofs broken by an upstream artifact \
change (proof co-evolution). The artifact change is ALREADY applied to the \
spec; the proof file below no longer builds.

## Upstream artifact change (already applied to the spec side)
```diff
{adiff}
```
{prior}
## Current Isabelle error
```
{error}
```

## Proof file `{path}` — {viewdesc}
```isabelle
{view}
```

## Task
Produce MINIMAL edits that make this file build.
Allowed: re-prove bodies; EVOLVE lemma statements to track the new semantics; \
add helper lemmas; MOVE a lemma later in the file when it needs facts declared \
below it (delete at old site + re-insert after the needed declarations); \
update crunches lists; delete a lemma ONLY if its statement refers to \
something that no longer exists AND nothing in this file uses it.
Forbidden: sorry / oops / axiomatization; weakening a statement to triviality.

Output ONLY edit blocks (nothing else). Each SEARCH must be an exact, \
contiguous, UNIQUE substring of the current file text — COPY it \
character-for-character from the file shown above; do NOT retype, reflow, \
or reconstruct it from memory (any mismatch aborts the edit):
<<<<SEARCH
(exact text)
====
(replacement; empty for deletion)
>>>>
"""


def run_claude(prompt, log_path):
    cmd = [CLAUDE, "-p", "--model", "sonnet", "--tools", ""]
    t0 = time.time()
    for attempt in (1, 2):
        try:
            r = subprocess.run(cmd, input=prompt, capture_output=True,
                               text=True, timeout=1500)
            break
        except subprocess.TimeoutExpired:
            if attempt == 2:
                with open(log_path, "w") as f:
                    f.write(prompt[:3000] + "\n=== TIMEOUT x2 ===\n")
                return "", int(time.time() - t0)
    dt = int(time.time() - t0)
    with open(log_path, "w") as f:
        f.write("call_wall_s=%d prompt_chars=%d\n" % (dt, len(prompt))
                + prompt[:3000] + "\n=== RAW ===\n" + r.stdout
                + "\n=== ERR ===\n" + r.stderr[:1000])
    return r.stdout, dt


def main():
    os.makedirs(OUT, exist_ok=True)
    summary = {}
    for c in sys.argv[1:]:
      try:
        t0 = time.time()
        case = "%s/coevolve/cases/%s" % (B_REPO, c)
        gt = json.loads(b_read(case + "/ground_truth.json"))
        gt_file = sorted(gt.keys())[0]
        adiff = b_read(case + "/delta_artifact.diff")
        broken_patch = b_read("%s/logs/adj-%s-0.patch" % (B_REPO, c))
        head, broken = broken_patch.split("\n", 1)
        nline = head.split()[1]
        ssh("mkdir -p %s/repair" % case)

        verdict, out = check(gt_file, "/workspace/logs/adj-%s-0.patch" % c)
        err = extract_err(out)
        print("[%s] broken-state check: %s" % (c, verdict), flush=True)
        if verdict == "GREEN":
            summary[c] = {"verdict": "SKIP(not red)"}
            continue

        current = broken
        applied_log = []
        result = {"rounds": 0, "verdict": "RED"}
        for k in range(1, MAX_ROUNDS + 1):
            view, viewdesc = make_view(current, err)
            prior = ("\n## Edits already applied in earlier rounds "
                     "(kept — build on them)\n%s\n" % "\n".join(applied_log)) \
                    if applied_log else ""
            prompt = PROMPT.format(adiff=adiff[:20000], prior=prior, error=err,
                                   path=gt_file, view=view, viewdesc=viewdesc)
            raw, dt = run_claude(prompt, os.path.join(OUT, "%s-r%d.txt" % (c, k)))
            blocks = BLOCK_RE.findall(raw)
            print("[%s] round %d: claude %ds, %d blocks" % (c, k, dt, len(blocks)),
                  flush=True)
            if not blocks:
                err = err + "\n(previous round: no edit blocks parsed)"
                continue
            new_content, fails = current, []
            for i, (s, rpl) in enumerate(blocks):
                n = new_content.count(s)
                if n != 1:
                    hint = anchor_hint(new_content, s) if n == 0 else ""
                    fails.append("block %d: SEARCH occurs %d times%s" % (i, n,
                        ("; closest ACTUAL text in file:\n" + hint) if hint else ""))
                else:
                    new_content = new_content.replace(s, rpl)
            if fails:
                err = err + "\nEDIT-APPLY FAILURES: " + "; ".join(fails)
                continue
            if re.search(r"\b(sorry|oops|axiomatization)\b", new_content) and \
               not re.search(r"\b(sorry|oops|axiomatization)\b", broken):
                err = err + "\nREJECTED: edit introduces sorry/oops/axiomatization"
                continue
            # patch coordinates MUST be in CURRENT-file space (check-theory
            # applies to the original file) — computing them against `broken`
            # silently lands edits on the wrong lemma (83ddb4def retraction).
            current_file = b_read("%s/verification/l4v/%s" % (B_REPO, gt_file))
            patch = make_patch(current_file, new_content)
            b_write("%s/logs/repair-%s-r%d.patch" % (B_REPO, c, k), patch)
            verdict, out = check(gt_file, "/workspace/logs/repair-%s-r%d.patch"
                                 % (c, k))
            err = extract_err(out)
            print("[%s] round %d: %s (%d edits)" % (c, k, verdict, len(blocks)),
                  flush=True)
            b_write(case + "/repair/attempt-r%d.edits.txt" % k,
                    raw + "\n\n=== VERDICT: %s ===\n%s" % (verdict, out[-1500:]))
            result = {"rounds": k, "verdict": verdict, "n_edits": len(blocks)}
            current = new_content
            applied_log += ["r%d: %d edits applied (build %s)"
                            % (k, len(blocks), verdict)]
            if verdict == "GREEN":
                b_write(case + "/repair/final.patch", patch)
                break
        result["wall_s"] = int(time.time() - t0)
        summary[c] = result
        b_write(case + "/repair/summary.json", json.dumps(result, indent=1))
        print("[%s] DONE %s" % (c, json.dumps(result)), flush=True)
      except Exception as e:
        summary[c] = {"verdict": "DRIVER-ERROR", "error": str(e)[:300]}
        print("[%s] DRIVER-ERROR %s" % (c, str(e)[:200]), flush=True)
    print("=== SUMMARY ===\n" + json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
