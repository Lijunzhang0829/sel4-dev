#!/usr/bin/env python3
"""measure_cases.py — clean per-lemma CPU re-measurement of the reduce FASTER cases.

For each case: own-session single-theory build of the ORIGINAL theory (no patch) and of the
PATCHED theory (LLM variant) -> read the target line's command_timings elapsed (threads=1-ish,
single theory => clean CPU proxy, no in-REPL proof-cache / cold-start pollution). Compare.

Unlike goal_aware.verify, NO 1.2x cap: we want to MEASURE even a slower variant (some in-REPL
FASTER verdicts were false positives), not kill it.

Usage: measure_cases.py [only_lemma]   (reads /tmp/cases.json, writes runs/clean_cpu_remeasure.json)
"""
import sys, json, time, subprocess, shutil
sys.path.insert(0, "/workspace/tools/seL4-proof-search/Isa-Repl")
import goal_aware_reduce as G
import gate  # find_proof_span: the orig tactic may be MULTI-LINE (by (... ) spanning N lines)

OUT = "/workspace/tools/seL4-proof-search/Isa-Repl/runs/clean_cpu_remeasure.json"


def measure(thy, session, span_replace=None):
    tmp, name, dst = G._mk(thy, session, span_replace=span_replace)
    t0 = time.time()
    r = subprocess.run(["timeout", "--kill-after=30s", str(G.ABS_CAP), G.ISA, "build",
                        "-d", G.L4V, "-d", tmp, f"{name}_S"], capture_output=True, text=True)
    wall = int(time.time() - t0)
    tm = G._timings(name, dst) if r.returncode == 0 else {}
    shutil.rmtree(tmp, ignore_errors=True)
    return {"ok": r.returncode == 0, "wall": wall, "tm": tm,
            "err": "" if r.returncode == 0 else (r.stdout + r.stderr)[-400:]}


cases = [c for c in json.load(open("/tmp/cases.json")) if c.get("line")]
only = sys.argv[1] if len(sys.argv) > 1 else None
results = []
for c in cases:
    if only and c["lemma"] != only:
        continue
    thy = "/sel4-project/verification/l4v/" + c["thy_rel"]
    ln, sess, var = c["line"], c["session"], c["variant"]
    print(f"[measure] {c['lemma']} {sess} {c['thy_rel']}:{ln}", flush=True)
    start, end = gate.find_proof_span(thy, ln)       # full multi-line tactic span
    o = measure(thy, sess)                            # orig theory, no patch
    orig_ms = o["tm"].get(start)                      # the orig (multi-line) command is timed at its start line
    print(f"  orig:    ok={o['ok']} cmd@{start}={orig_ms}s wall={o['wall']}s  span={start}-{end}", flush=True)
    row = {"lemma": c["lemma"], "session": sess, "thy": c["thy_rel"], "line": ln, "span": [start, end],
           "orig_ok": o["ok"], "orig_ms": orig_ms}
    if o["ok"]:
        v = measure(thy, sess, span_replace=(start, end, var))
        # the variant may be several apply-commands -> sum the elapsed over the lines it occupies
        vlines = range(start, start + var.count("\n") + 1)
        var_ms = round(sum(v["tm"].get(l, 0.0) for l in vlines), 3) if v["ok"] else None
        print(f"  variant: ok={v['ok']} sum(cmd@{start}..)={var_ms}s wall={v['wall']}s {v.get('err','')[:70]}", flush=True)
        row.update(variant_ok=v["ok"], variant_ms=var_ms, variant_err=v.get("err", "")[:200])
        if orig_ms and var_ms is not None:
            row["clean_delta_pct"] = round(100 * (orig_ms - var_ms) / orig_ms, 1)
    results.append(row)
    json.dump(results, open(OUT, "w"), indent=1)
    print(f"  => clean CPU delta = {row.get('clean_delta_pct')}%", flush=True)

print("\n=== CLEAN per-lemma CPU re-measurement (own-session, threads=1-ish) ===", flush=True)
for r in sorted(results, key=lambda x: -(x.get("clean_delta_pct") or -999)):
    print(f"  {r['lemma']:<32} {str(r['session'])[:8]:<8} "
          f"orig={r.get('orig_ms')}s -> var={r.get('variant_ms')}s  = {r.get('clean_delta_pct')}%", flush=True)
