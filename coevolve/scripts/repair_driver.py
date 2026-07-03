#!/usr/bin/env python3
"""repair_driver.py v2 — Step-2 repair experiment (A drives, B oracle).

v2: prompt via STDIN (argv 128KB limit); error-centric window for big files;
CUMULATIVE repair (keep progress across rounds); per-call wall logging.
Canonical copy lives in B:coevolve/scripts/ — A's /tmp scratchpad is volatile.
"""
import json, os, re, subprocess, sys, time

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
    cmd = ("cd %s && docker compose exec -T %s l4v bash %s "
           "/sel4-project/verification/l4v/%s AInvs --patch %s 2>&1 | tail -60"
           % (B_REPO, " ".join(ENVS), CT, gt_file, patch_container_path))
    rc, out, err = ssh(cmd, timeout=2400)
    green = bool(re.search(r"^OK\b", out, re.M)) or "\nOK" in out
    return ("GREEN" if green else "RED"), out


def extract_err(out):
    lines = [l for l in out.split("\n") if l.startswith("***")]
    return "\n".join(lines)[:2500] or out[-1500:]


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
contiguous, UNIQUE substring of the current file text:
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
                               text=True, timeout=1200)
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
                    fails.append("block %d: SEARCH occurs %d times" % (i, n))
                else:
                    new_content = new_content.replace(s, rpl)
            if fails:
                err = err + "\nEDIT-APPLY FAILURES: " + "; ".join(fails)
                continue
            if re.search(r"\b(sorry|oops|axiomatization)\b", new_content) and \
               not re.search(r"\b(sorry|oops|axiomatization)\b", broken):
                err = err + "\nREJECTED: edit introduces sorry/oops/axiomatization"
                continue
            patch = "1 %s\n%s" % (nline, new_content)
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
