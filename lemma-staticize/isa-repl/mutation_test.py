#!/usr/bin/env python3
"""mutation_test.py — decisive TIME test for direction (b): does removing a wasted-trial ambient [simp]
rule from a hot step change the own-session command_timings elapsed, while staying build-green?

Counts (trial_probe) show ctes_of_not_0 (= valid_mdbD3': ctes_of s p = Some cte & valid_mdb' s ==> p!=0)
is TRIED-and-FAILS ~96-107x on the CRefine tent-poles. But counts != time. This builds the target theory
own-session (parent=CRefine, single theory) for ORIG and for a VARIANT that `del:`s the rule on the hot
line, reads per-command elapsed from command_timings for both, and reports green + delta.

Run INSIDE container:  mutation_test.py <thy_abs> <session> <line> "<extra simp modifier>"
"""
import sys, os, time, subprocess, shutil, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goal_aware_reduce as G


def build_read(thy, session, line, patch):
    tmp, name, dst = G._mk(thy, session, patch=patch)
    t0 = time.time()
    r = subprocess.run(["timeout", "--kill-after=30s", str(G.ABS_CAP), G.ISA, "build",
                        "-d", G.L4V, "-d", tmp, f"{name}_S"], capture_output=True, text=True)
    wall = round(time.time() - t0, 1)
    tm = G._timings(name, dst) if r.returncode == 0 else {}
    shutil.rmtree(tmp, ignore_errors=True)
    return {"green": r.returncode == 0, "wall": wall, "line_ms": tm.get(line),
            "theory_ms": round(sum(tm.values()), 1) if tm else None,
            "err": "" if r.returncode == 0 else (r.stdout + r.stderr)[-500:]}


if __name__ == "__main__":
    thy, session, line, mod = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
    src_line = open(thy, encoding="utf-8").read().split("\n")[line - 1]
    # insert the modifier before the final ')' of the method
    idx = src_line.rstrip().rfind(")")
    variant_line = src_line.rstrip()[:idx] + " " + mod + src_line.rstrip()[idx:]
    print(f"[orig]    {src_line.strip()}", flush=True)
    print(f"[variant] {variant_line.strip()}", flush=True)
    orig = build_read(thy, session, line, None)
    print("[orig result] " + json.dumps(orig), flush=True)
    var = build_read(thy, session, line, {line: variant_line})
    print("[variant result] " + json.dumps(var), flush=True)
    out = {"line": line, "modifier": mod, "orig": orig, "variant": var}
    if orig.get("line_ms") and var.get("green") and var.get("line_ms"):
        out["line_speedup_pct"] = round(100 * (orig["line_ms"] - var["line_ms"]) / orig["line_ms"], 1)
    print("[SUMMARY] " + json.dumps(out), flush=True)
