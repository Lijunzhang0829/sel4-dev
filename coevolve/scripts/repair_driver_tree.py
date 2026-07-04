#!/usr/bin/env python3
"""repair_driver_tree.py v4 — tree-apply oracle (harness item #13 fix).

check-theory --patch is UNSOUND for arch_global_naming theories (probe-proven
false RED). The sound oracle: write the candidate file into the real tree,
run the incremental AInvs session build (AARCH64), read the verdict, revert.
~17-23 min per check; content written directly (no patch format → the whole
coordinate/format bug class is gone by construction).

Justified deviation from "never isabelle build directly": the documented
explicit-session-build path, used because the normal gate is proven unsound
here (coevolve/README #13).
"""
import difflib, json, os, re, subprocess, sys, time

SSH = ["ssh", "-o", "BatchMode=yes", "zljj@114.212.82.216"]
B_REPO = "/data/zljj/sel4-dev"
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


def tree_check(gt_file, content, tag):
    """Sound oracle: candidate content -> real tree -> AInvs build -> revert."""
    b_write("/tmp/cand.thy", content)
    cmd = (
        "cd {R} && cp /tmp/cand.thy verification/l4v/{F} && "
        "docker compose exec -T -e L4V_ARCH=AARCH64 l4v bash -c "
        "'export L4V_ARCH=AARCH64; "
        "isabelle build -b -d /sel4-project/verification/l4v AInvs' "
        "> /tmp/build-{T}.out 2>&1; rc=$?; "
        "git -C verification/l4v checkout -- {F}; "
        "tail -3 /tmp/build-{T}.out; echo __ERRSEC__; "
        "grep -m1 -A60 '^\\*\\*\\*' /tmp/build-{T}.out | head -80; "
        "exit $rc"
    ).format(R=B_REPO, F=gt_file, T=tag)
    t0 = time.time()
    rc, out, err = ssh(cmd, timeout=3000)
    dt = int(time.time() - t0)
    head, _, errsec = out.partition("__ERRSEC__")
    green = (rc == 0)
    return ("GREEN" if green else "RED"), (errsec.strip() or head.strip()), dt


def extract_err(txt):
    idx = txt.find("***")
    return txt[idx:idx + 3500] if idx >= 0 else txt[-1500:]


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


def anchor_hint(content, search):
    first = search.split("\n")[0].strip()
    lines = content.split("\n")
    m = difflib.get_close_matches(first, [l.strip() for l in lines], n=1,
                                  cutoff=0.5)
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
## Current Isabelle error (from a full AInvs session build — line numbers \
refer to the REAL file)
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
below it; update crunches lists; delete a lemma ONLY if its statement refers \
to something that no longer exists AND nothing in this file uses it.
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
    c = sys.argv[1]
    t0 = time.time()
    case = "%s/coevolve/cases/%s" % (B_REPO, c)
    gt = json.loads(b_read(case + "/ground_truth.json"))
    gt_file = sorted(gt.keys())[0]
    adiff = b_read(case + "/delta_artifact.diff")
    broken = b_read("%s/logs/adj-%s-0.patch" % (B_REPO, c)).split("\n", 1)[1]
    ssh("mkdir -p %s/repair" % case)

    # tree must be clean at the target file before we start
    rc, st, _ = ssh("git -C %s/verification/l4v status --short -- %s"
                    % (B_REPO, gt_file))
    assert not st.strip(), "tree not clean at %s: %s" % (gt_file, st)

    # baseline guard: clean tree, incremental build should be green/no-op
    cur = b_read("%s/verification/l4v/%s" % (B_REPO, gt_file))
    v, e, dt = tree_check(gt_file, cur, "baseline")
    print("[%s] tree-oracle baseline: %s (%ds)" % (c, v, dt), flush=True)
    if v != "GREEN":
        print("BASELINE-RED — aborting: %s" % e[:300])
        return

    v, e, dt = tree_check(gt_file, broken, "broken")
    err = extract_err(e)
    print("[%s] broken-state: %s (%ds)\n%s" % (c, v, dt, err[:400]), flush=True)
    if v == "GREEN":
        print("[%s] broken state builds GREEN — no forced break; seed is "
              "choice-set only. Recording." % c)
        b_write(case + "/repair/tree-oracle-verdict.json",
                json.dumps({"broken_state": "GREEN",
                            "meaning": "no compiler-forced break"}))
        return

    current = broken
    applied_log = []
    result = {"rounds": 0, "verdict": "RED"}
    for k in range(1, MAX_ROUNDS + 1):
        view, viewdesc = make_view(current, err)
        prior = ("\n## Edits already applied in earlier rounds (kept)\n%s\n"
                 % "\n".join(applied_log)) if applied_log else ""
        prompt = PROMPT.format(adiff=adiff[:20000], prior=prior, error=err,
                               path=gt_file, view=view, viewdesc=viewdesc)
        raw, cdt = run_claude(prompt, os.path.join(OUT, "%s-tree-r%d.txt" % (c, k)))
        blocks = BLOCK_RE.findall(raw)
        print("[%s] round %d: claude %ds, %d blocks" % (c, k, cdt, len(blocks)),
              flush=True)
        if not blocks:
            err += "\n(previous round: no edit blocks parsed)"
            continue
        new_content, fails = current, []
        for i, (s, rp) in enumerate(blocks):
            n = new_content.count(s)
            if n != 1:
                hint = anchor_hint(new_content, s) if n == 0 else ""
                fails.append("block %d: SEARCH occurs %d times%s" % (i, n,
                    ("; closest ACTUAL text:\n" + hint) if hint else ""))
            else:
                new_content = new_content.replace(s, rp)
        if fails:
            err += "\nEDIT-APPLY FAILURES: " + "; ".join(fails)
            continue
        if re.search(r"\b(sorry|oops|axiomatization)\b", new_content) and \
           not re.search(r"\b(sorry|oops|axiomatization)\b", broken):
            err += "\nREJECTED: introduces sorry/oops/axiomatization"
            continue
        v, e, dt = tree_check(gt_file, new_content, "r%d" % k)
        err = extract_err(e)
        print("[%s] round %d: %s (%d edits, build %ds)" % (c, k, v, len(blocks), dt),
              flush=True)
        b_write(case + "/repair/tree-attempt-r%d.edits.txt" % k,
                raw + "\n\n=== VERDICT: %s ===\n%s" % (v, e[-1500:]))
        result = {"rounds": k, "verdict": v, "n_edits": len(blocks)}
        current = new_content
        applied_log += ["r%d: %d edits (build %s)" % (k, len(blocks), v)]
        if v == "GREEN":
            b_write(case + "/repair/tree-final-content.thy", new_content)
            break
    result["wall_s"] = int(time.time() - t0)
    b_write(case + "/repair/tree-summary.json", json.dumps(result, indent=1))
    print("[%s] DONE %s" % (c, json.dumps(result)))


if __name__ == "__main__":
    main()
