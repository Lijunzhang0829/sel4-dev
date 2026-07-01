#!/usr/bin/env python3
"""anon_scan.py — scan CRefine simp tent-poles, record anon% + dominant named rule.

Evidence for "critical-path simp is irreducible": for each slow simp step, scoped simp_trace gives
the fraction of rewrites done by ANONYMOUS def-unfolding rules (anon%) vs by NAMED rules. High anon%
(or a dominant named rule whose ablation we know saves ~noise) => the time is genuine def-unfolding
work, structurally not removable by simp del:/simp only:.

Runs INSIDE the container. Writes incrementally so a mid-scan death keeps partial results.
"""
import sys, json
sys.path.insert(0, "/workspace/tools/seL4-proof-search/Isa-Repl")
import goal_aware_reduce as G

ARM = "/sel4-project/verification/l4v/proof/crefine/ARM/"
OUT = "/workspace/tools/seL4-proof-search/Isa-Repl/runs/anon_scan.json"

# already profiled (do not re-run) — fold into the final table
DONE = [
    {"file": "CSpace_C.thy", "line": 2408, "elapsed": 147.9, "anon_pct": 93.8, "n_rewrites": 2853,
     "top_named": [["cap_frame_cap_lift_def", 26], ["option.case_2", 24]]},
    {"file": "Invoke_C.thy", "line": 1134, "elapsed": 67.5, "anon_pct": 43.4, "n_rewrites": 3031,
     "top_named": [["Nat.plus_nat.add_Suc", 624], ["add_0_left", 230]]},
]

# simp / simp_all tent-poles to scan (simp_trace applies)
TARGETS = [
    ("Fastpath_C.thy", 2052, 408.4),
    ("CSpace_C.thy",   2945, 103.2),
    ("CSpace_C.thy",    797,  72.1),
    ("Retype_C.thy",   7071,  53.0),
    ("CSpace_C.thy",   3009,  88.0),
    ("CSpace_C.thy",   2998,  56.6),
]

results = list(DONE)
json.dump(results, open(OUT, "w"), indent=1)
for f, ln, el in TARGETS:
    print(f"[scan] profiling {f}:{ln} ({el}s) ...", flush=True)
    try:
        p = G.profile(ARM + f, "CRefine", ln)
        row = {"file": f, "line": ln, "elapsed": el, "anon_pct": p.get("anon_pct"),
               "n_rewrites": p.get("n_rewrites"), "top_named": p.get("top_rules", [])[:3]}
    except Exception as e:
        row = {"file": f, "line": ln, "elapsed": el, "error": str(e)[:120]}
    results.append(row)
    print(f"[scan] {f}:{ln}  anon={row.get('anon_pct')}%  n={row.get('n_rewrites')}  top={row.get('top_named')}", flush=True)
    json.dump(results, open(OUT, "w"), indent=1)   # incremental

print("\n=== anon% DISTRIBUTION (irreducibility evidence) ===", flush=True)
for r in sorted(results, key=lambda x: -(x.get("anon_pct") or 0)):
    print(f"  {r['file']+':'+str(r['line']):<22} {str(r.get('elapsed'))+'s':>8}  anon={r.get('anon_pct')}%  "
          f"dom={(r.get('top_named') or [['-',0]])[0]}", flush=True)
