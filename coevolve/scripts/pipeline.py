#!/usr/bin/env python3
"""pipeline.py — coevolve tool: detect -> repair -> validate -> report.

Deployment shape (not the benchmark scaffold): an upstream artifact change is
on the tree; the build oracle DISCOVERS which downstream files fail (Isabelle
stops at the first failing theory per session), we repair that file, rebuild
to surface the next failure, iterate to a green session (multi-file fixpoint),
then assemble a PR-style report with autonomy accounting (M6).

Seed mode (`--seed <hash>`) reconstructs a historical broken tree from a case
bundle so detect/repair/validate can be validated against a known answer;
detect is NOT told which file — it discovers it from the build.

Sound oracle = tree-apply + `isabelle build <SESSION>` (AARCH64). check-theory
--patch is unsound for arch_global_naming theories (README #13).
"""
import argparse, difflib, json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from repair_driver_tree import (            # noqa: E402  reuse validated helpers
    ssh, b_read, b_write, run_claude, make_view, extract_err,
    BLOCK_RE, anchor_hint, B_REPO, PROMPT)

ENV1 = "-e L4V_ARCH=AARCH64"
CONTAINER_L4V = "/sel4-project/verification/l4v"
MAX_ROUNDS = 5          # effective (build-reaching) rounds per file
MAX_ITERS = 12
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline-runs")

AT_CMD = re.compile(
    r'line (\d+) of "(?:/sel4-project/verification/l4v/)?([^"]+\.thy)"')


def build_session(session, tag):
    """Build the session on the current tree (persists). Returns
    (green, err_block, src_file_repo_rel, line, build_wall_s)."""
    inner = ("docker compose exec -T %s l4v bash -c "
             "'export L4V_ARCH=AARCH64; isabelle build -b -d %s %s'"
             % (ENV1, CONTAINER_L4V, session))
    cmd = ("cd %s && %s > /tmp/pipe-%s.out 2>&1; rc=$?; "
           "tail -3 /tmp/pipe-%s.out; echo __SEC__; "
           "grep -m1 -A90 '^\\*\\*\\*' /tmp/pipe-%s.out | head -120; "
           "echo __AT__; grep 'line [0-9]* of' /tmp/pipe-%s.out | tail -3; "
           "exit $rc" % (B_REPO, inner, tag, tag, tag, tag))
    t0 = time.time()
    rc, out, _ = ssh(cmd, timeout=3600)
    dt = int(time.time() - t0)
    green = (rc == 0)
    if green:
        return True, "", None, None, dt
    _, _, rest = out.partition("__SEC__")
    errsec, _, atsec = rest.partition("__AT__")
    m = None
    for m in AT_CMD.finditer(atsec):        # last = the failing command
        pass
    src = m.group(2) if m else None
    line = int(m.group(1)) if m else None
    return False, errsec.strip(), src, line, dt


def repair_file(session, src_file, adiff, run_dir, initial_err,
                escalate_budget=MAX_ROUNDS):
    """Repair one failing file to session-green, starting from the caller's
    already-known build error (no redundant detect build). Content persists on
    the tree on success; on exhaustion the file is reverted and escalated."""
    cur = b_read("%s/verification/l4v/%s" % (B_REPO, src_file))
    rec = {"file": src_file, "verdict": "RED", "rounds": 0, "iters": 0,
           "escalated": False, "human_decisions": 0, "llm": [],
           "build_wall_s": 0, "transcripts": []}
    err = extract_err(initial_err)
    current = cur
    eff = it = 0
    applied = []
    while eff < escalate_budget and it < MAX_ITERS:
        it += 1
        view, viewdesc = make_view(current, err)
        prior = ("\n## Edits already applied this file (kept)\n%s\n"
                 % "\n".join(applied)) if applied else ""
        prompt = PROMPT.format(adiff=adiff[:20000], prior=prior, error=err,
                               path=src_file, view=view, viewdesc=viewdesc)
        tag = "%s-%s-i%02d" % (os.path.basename(run_dir),
                               src_file.replace("/", "_"), it)
        local_log = os.path.join(OUT, tag + ".log")       # on A (this host)
        b_arch = os.path.join(run_dir, "claude-" + tag)   # on B (durable)
        raw, _, meta = run_claude(prompt, local_log, b_archive=b_arch)
        rec["llm"].append(meta)
        rec["transcripts"].append(b_arch + ".txt")
        blocks = BLOCK_RE.findall(raw)
        if not blocks:
            err += "\n(prev round: no edit blocks parsed)"
            continue
        nc, fails = current, []
        for i, (s, rp) in enumerate(blocks):
            n = nc.count(s)
            if n != 1:
                h = anchor_hint(nc, s) if n == 0 else ""
                fails.append("block %d occurs %d times%s" % (i, n,
                             ("; closest ACTUAL:\n" + h) if h else ""))
            else:
                nc = nc.replace(s, rp)
        if fails:
            err += "\nEDIT-APPLY FAILURES: " + "; ".join(fails)
            continue
        if re.search(r"\b(sorry|oops|axiomatization)\b", nc) and \
           not re.search(r"\b(sorry|oops|axiomatization)\b", cur):
            err += "\nREJECTED: introduces sorry/oops/axiomatization"
            continue
        # write candidate to tree, build the session
        b_write("/tmp/cand.thy", nc)
        ssh("cp /tmp/cand.thy %s/verification/l4v/%s" % (B_REPO, src_file))
        green, e2, fsrc, _, bdt = build_session(session, "r%d" % it)
        eff += 1
        rec.update(rounds=eff, iters=it, build_wall_s=rec["build_wall_s"] + bdt)
        applied.append("i%d: %d edits (build %s)"
                       % (it, len(blocks), "GREEN" if green else "RED"))
        current = nc
        if green or (fsrc and fsrc != src_file):
            # session green, OR the failure moved to a DIFFERENT file =>
            # THIS file is repaired; leave content on tree.
            rec["verdict"] = "GREEN"
            b_write(os.path.join(run_dir, "fix-%s.thy"
                                 % src_file.replace("/", "_")), nc)
            return rec
        err = extract_err(e2)
    # exhausted: revert this file, escalate
    ssh("git -C %s/verification/l4v checkout -- %s" % (B_REPO, src_file))
    rec["escalated"] = True
    rec["human_decisions"] = 1
    return rec


def affected_sessions(artifact_files):
    """DAG: artifact theory files -> proof sessions whose theories depend on
    them, nearest-layer first."""
    order = ["AInvs", "Refine", "CRefine", "Access", "InfoFlow", "DRefine"]
    seen = set()
    for f in artifact_files:
        base = os.path.splitext(os.path.basename(f))[0]
        # canonical name guess: ASpec.<base> for spec/abstract, else scan
        for canon in ("ASpec.%s" % base, "ExecSpec.%s" % base):
            r = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "zljj@114.212.82.216",
                 "cd %s && L4V_ARCH=AARCH64 python3 tools/theory_dag/dag_query.py "
                 "dependents %s --by-session" % (B_REPO, canon)],
                capture_output=True, text=True)
            for ln in r.stdout.split("\n"):
                mm = re.match(r"(\w+)\s+\d+ theories", ln)
                if mm:
                    seen.add(mm.group(1))
    return [s for s in order if s in seen] or ["AInvs"]


def reconstruct_seed(seed):
    """Seed mode: put the historical broken proof content on the tree (spec
    already new in tree). Returns (session, adiff, [touched_files])."""
    case = "%s/coevolve/cases/%s" % (B_REPO, seed)
    gt = json.loads(b_read(case + "/ground_truth.json"))
    session = list(gt.values())[0]["session"]
    src = sorted(gt.keys())[0]
    adiff = b_read(case + "/delta_artifact.diff")
    broken = b_read("%s/logs/adj-%s-0.patch" % (B_REPO, seed)).split("\n", 1)[1]
    b_write("/tmp/cand.thy", broken)
    ssh("cp /tmp/cand.thy %s/verification/l4v/%s" % (B_REPO, src))
    return session, adiff, [src]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", help="reconstruct broken tree from a case bundle")
    ap.add_argument("--session", help="override target session")
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()
    run_id = args.run_id or (args.seed or "run")
    run_dir = "%s/coevolve/pipeline-runs/%s" % (B_REPO, run_id)
    ssh("mkdir -p %s" % run_dir)
    os.makedirs(OUT, exist_ok=True)                        # local A-side logs

    t0 = time.time()
    if args.seed:
        session, adiff, touched = reconstruct_seed(args.seed)
    else:
        sys.exit("artifact-diff mode: wire real Δ application here")
    session = args.session or session

    record = {"run_id": run_id, "seed": args.seed, "session": session,
              "files": [], "touched": list(touched)}

    # multi-file fixpoint: build -> discover failing file -> repair -> rebuild
    for cycle in range(1, 20):
        green, err, src, line, dt = build_session(session, "cycle%d" % cycle)
        if green:
            record["final"] = "SESSION-GREEN"
            break
        if src is None:
            record["final"] = "RED-unparsed"
            break
        if src not in record["touched"]:
            record["touched"].append(src)
        print("[pipe] cycle %d: RED at %s:%s (%ds) -> repair"
              % (cycle, src, line, dt), flush=True)
        fr = repair_file(session, src, adiff, run_dir, err)
        record["files"].append(fr)
        print("[pipe]   %s -> %s (rounds=%d, escalated=%s)"
              % (src, fr["verdict"], fr["rounds"], fr["escalated"]), flush=True)
        if fr["escalated"]:
            record["final"] = "ESCALATED"
            break
    else:
        record["final"] = "MAX-CYCLES"

    # accounting (M6)
    files = record["files"]
    n_auto = sum(1 for f in files if not f["escalated"] and f["human_decisions"] == 0)
    record["accounting"] = {
        "files_repaired": sum(1 for f in files if f["verdict"] == "GREEN"),
        "files_escalated": sum(1 for f in files if f["escalated"]),
        "autonomy_rate": round(n_auto / len(files), 3) if files else None,
        "human_review_queue": [f["file"] for f in files if f["escalated"]],
        "total_llm_calls": sum(len(f["llm"]) for f in files),
        "total_cost_usd": round(sum((m.get("cost_usd") or 0)
                                     for f in files for m in f["llm"]), 4),
        "wall_s": int(time.time() - t0),
    }
    b_write(run_dir + "/run-record.json", json.dumps(record, indent=1))
    # revert tree (dry-run default)
    for f in record["touched"]:
        ssh("git -C %s/verification/l4v checkout -- %s" % (B_REPO, f))
    print("[pipe] DONE final=%s accounting=%s"
          % (record["final"], json.dumps(record["accounting"])))
    print("[pipe] record: %s/run-record.json" % run_dir)


if __name__ == "__main__":
    main()
