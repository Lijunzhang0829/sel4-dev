#!/usr/bin/env python3
"""theory_simpdel_test.py — test the GLOBAL (b) hypothesis at theory scope, cheaply.

Instead of removing a wasted-trial [simp] rule on ONE line, inject `declare RULE[simp del]` right after
`begin` so the rule leaves the simpset for the WHOLE own-session theory. Rebuild (parent=CRefine heap
untouched) and compare theory-total command_timings + green vs orig. This asks: if we removed the global
[simp] attribute, would this theory (a) stay green, and (b) get faster in aggregate?

  green=False  => the rule IS load-bearing somewhere in the theory -> global removal not free.
  green=True + faster => the wasted trials aggregate into removable wall.

Run INSIDE container:  theory_simpdel_test.py <thy_abs> <session> <begin_line> <rule> [reps]
"""
import sys, os, time, subprocess, shutil, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goal_aware_reduce as G


def build_read(thy, session, patch):
    tmp, name, dst = G._mk(thy, session, patch=patch)
    t0 = time.time()
    r = subprocess.run(["timeout", "--kill-after=30s", str(G.ABS_CAP), G.ISA, "build",
                        "-d", G.L4V, "-d", tmp, f"{name}_S"], capture_output=True, text=True)
    wall = round(time.time() - t0, 1)
    tm = G._timings(name, dst) if r.returncode == 0 else {}
    shutil.rmtree(tmp, ignore_errors=True)
    return {"green": r.returncode == 0, "wall": wall,
            "theory_ms": round(sum(tm.values()), 1) if tm else None, "n_cmds": len(tm),
            "err": "" if r.returncode == 0 else (r.stdout + r.stderr)[-600:]}


if __name__ == "__main__":
    thy, session, begin, rule = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
    reps = int(sys.argv[5]) if len(sys.argv) > 5 else 1
    src_begin = open(thy, encoding="utf-8").read().split("\n")[begin - 1]
    patch = {begin: src_begin + "\ndeclare " + rule + "[simp del]"}
    print(f"[inject after L{begin}] declare {rule}[simp del]", flush=True)
    res = {"rule": rule, "orig": [], "variant": []}
    for k in range(reps):
        o = build_read(thy, session, None);   print(f"[orig r{k}] {json.dumps(o)}", flush=True);   res["orig"].append(o)
        v = build_read(thy, session, patch);  print(f"[variant r{k}] {json.dumps(v)}", flush=True); res["variant"].append(v)
    print("[SUMMARY] " + json.dumps(res), flush=True)
