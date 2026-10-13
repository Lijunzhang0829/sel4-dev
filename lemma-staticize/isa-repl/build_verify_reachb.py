"""Build-verify every REACHED-B / PATH-FOUND result in a genstat/DFS experiment dir.

The in-REPL reach-B signature is only a fast PRE-FILTER and CAN false-positive (it collapsed a
wrapped goal to its first line on in_whileLoop_corres). Ground truth = splice the static path into
the real theory and run an Isabelle build. Only build-green AND faster counts as a real acceleration.

For each <method>/<lemma>-L<line>/result.json with a reach-B verdict and a path, run
gate.build_check and emit a verdict: REAL-ACCEL / REAL-SAME-SPEED / FALSE-POSITIVE(build reject).

Run INSIDE the l4v container (needs gate + a build).  Usage: python3 build_verify_reachb.py <OUT_dir>
"""
import sys, os, json, glob
import gate

OUT = sys.argv[1]
L4V = os.environ.get("L4V_DIR", "/sel4-project/verification/l4v")
SET = "/workspace/lemma-staticize/experiment-candidates/high_value_candidates.json"
cand = {(c["lemma"], c["proof_line"]): c for c in json.load(open(SET))["cases"]}

reachb = []
for rj in glob.glob(os.path.join(OUT, "*", "*-L*", "result.json")):
    try:
        d = json.load(open(rj))
    except Exception:
        continue
    if d.get("verdict") in ("REACHED-B", "CLOSED", "PATH-FOUND", "FASTER") and d.get("path"):
        method = rj.split("/")[-3]
        # derive (lemma, line) from the DIR name `<lemma>-L<line>` — reduce result.json may
        # omit proof_line, and the dir is authoritative.
        base = rj.split("/")[-2]; lem_d, _, ln_d = base.rpartition("-L")
        reachb.append((method, lem_d, int(ln_d) if ln_d.isdigit() else None, d))

print(f"=== build-verifying {len(reachb)} reach-B result(s) in {OUT} ===")
out = []
for method, lem, ln, d in reachb:
    c = cand.get((lem, ln), {})
    thy = os.path.join(L4V, c.get("thy", ""))
    sess = c.get("session")
    path = d["path"]
    print(f"\n--- {method} {lem}:L{ln} ({sess}) path={path}")
    # The variant passed in-REPL reach-B: applying these EXACT lines from A reaches the SAME
    # state B the original command left. So splice them AS-IS in place of the original command
    # span [start,end] (keep everything before/after) — do NOT add or strip `done`.
    try:
        start, end = gate.find_proof_span(thy, ln)
        repl = "\n".join("  " + t for t in path)
        ok, build_ms, msg = gate.check_theory_builds(thy, sess, start, end, repl)
        r = {"builds_ok": ok, "build_ms": build_ms, "build_msg": msg or ""}
        builds = ok
    except Exception as e:
        r = {"builds_ok": None, "err": str(e)[:120]}; builds = None
    orig = d.get("orig_ms"); stat = d.get("variant_ms") or d.get("static_ms")
    faster = (orig and stat and stat < orig * 0.9)
    verdict = ("REAL-ACCEL" if (builds and faster) else
               "REAL-SAME/SLOWER" if builds else
               "FALSE-POSITIVE" if builds is False else "BUILD-ERROR")
    print(f"    builds_ok={builds}  orig_ms={orig} static_ms={stat}  -> {verdict}")
    out.append({"method": method, "lemma": lem, "line": ln, "session": sess,
                "builds_ok": builds, "orig_ms": orig, "static_ms": stat,
                "saved_ms": d.get("saved_ms"), "verdict": verdict, "path": path,
                "build_msg": r.get("build_msg", "")[:200]})

json.dump(out, open(os.path.join(OUT, "build_verify.json"), "w"), ensure_ascii=False, indent=1)
real = [x for x in out if x["verdict"] == "REAL-ACCEL"]
print(f"\n=== SUMMARY: {len(reachb)} reach-B -> {len(real)} REAL-ACCEL (build-green + faster), "
      f"{sum(1 for x in out if x['verdict']=='FALSE-POSITIVE')} false-positive ===")
for x in real:
    print(f"  REAL-ACCEL: {x['method']} {x['lemma']}:L{x['line']}  "
          f"{x['orig_ms']:.0f}->{x['static_ms']:.1f}ms  saved={x['saved_ms']}ms")
print(f"-> {OUT}/build_verify.json")
