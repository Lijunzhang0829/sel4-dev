#!/usr/bin/env python3
"""adjudicate.py — build-oracle adjudication of localizer verdicts (host, B).

For a seed case: reconstruct the historical broken state (current tree with
the human proof-fix REVERSE-applied, in an isolated git worktree), then ask
check-theory (AARCH64 home) whether each ground-truth proof file is RED.

  RED   -> the break was compiler-forced; an empty/"survives" prediction is WRONG
  GREEN -> the human edit was semantic-tracking only; "survives" is DEFENSIBLE

Runs baseline (unmodified file) first as sanity. Serial (session heap lock).
Usage: python3 adjudicate.py <case-hash> [...]
"""
import json, os, subprocess, sys

REPO = "/data/zljj/sel4-dev"
L4V = REPO + "/verification/l4v"
WT = "/tmp/adj-worktree"
ENV = ["-e", "L4V_ARCH=AARCH64", "-e", "ISABELLE_HOME_USER=/root/.isabelle-aarch64",
       "-e", "L4V_DIR=/sel4-project/verification/l4v"]
CT = "/workspace/.claude/skills/isabelle_prover/scripts-container/check-theory.sh"


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def check(container_file, patch=None, timeout=3600):
    cmd = ["docker", "compose", "exec", "-T"] + ENV + \
          ["l4v", "bash", CT, container_file, "AInvs"]
    if patch:
        cmd += ["--patch", patch]
    r = sh(cmd, cwd=REPO, timeout=timeout)
    ok = ("OK" in r.stdout.split("\n")[-2:][0] or "\nOK" in r.stdout or
          r.returncode == 0)
    return ("GREEN" if ok else "RED"), r.stdout[-1500:] + r.stderr[-500:]


def main():
    results = {}
    for c in sys.argv[1:]:
        case = os.path.join(REPO, "coevolve/cases", c)
        gt = json.load(open(os.path.join(case, "ground_truth.json")))
        # isolated worktree with the human fix reverse-applied
        sh(["git", "-C", L4V, "worktree", "remove", "--force", WT])
        r = sh(["git", "-C", L4V, "worktree", "add", "--detach", WT, "HEAD"])
        assert r.returncode == 0, r.stderr
        r = sh(["git", "-C", WT, "apply", "-R",
                os.path.join(case, "delta_proof.diff")])
        assert r.returncode == 0, "reverse-apply failed: " + r.stderr

        for i, pf in enumerate(sorted(gt.keys())):
            cur = open(os.path.join(L4V, pf)).read().split("\n")
            rev = open(os.path.join(WT, pf)).read()
            pdir = os.path.join(REPO, "logs")
            os.makedirs(pdir, exist_ok=True)
            ppath = os.path.join(pdir, "adj-%s-%d.patch" % (c, i))
            with open(ppath, "w") as f:
                f.write("1 %d\n%s" % (len(cur), rev))
            cfile = "/sel4-project/verification/l4v/" + pf
            cpatch = "/workspace/logs/" + os.path.basename(ppath)

            base_v, base_log = check(cfile)
            brk_v, brk_log = ("SKIP", "") if base_v != "GREEN" else \
                check(cfile, patch=cpatch)
            verdict = {"file": pf, "baseline": base_v, "broken_state": brk_v}
            if base_v != "GREEN":
                verdict["note"] = "baseline not green — heap/env problem, see log"
            results.setdefault(c, []).append(verdict)
            print("%s %s baseline=%s broken=%s" % (c, pf, base_v, brk_v))
            with open(os.path.join(case, "adjudication-%d.log" % i), "w") as f:
                f.write("== baseline ==\n%s\n== broken ==\n%s\n" %
                        (base_log, brk_log))
        json.dump(results[c], open(os.path.join(case, "adjudication.json"), "w"),
                  indent=1)
    sh(["git", "-C", L4V, "worktree", "remove", "--force", WT])
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
