"""Wasted-classical ablation + file-wall measurement (channel-free).
For one candidate: swap the leading search tactic (auto/fastforce/force) on its proof
line -> clarsimp (drop the wasted classical backtracking, keep clarify). Build BOTH the
baseline (original line) and the clarsimp variant via check-theory, alternating REPS
times, and report median whole-file wall + whether clarsimp still builds.

Env: LEMMA THY SESSION PROOF_LINE TACTIC  [REPS=3]   (run inside the l4v container)
"""
import os, re, json, time
import gate

THY = os.environ["THY"]; SESSION = os.environ["SESSION"]
PROOF_LINE = int(os.environ["PROOF_LINE"]); TACTIC = os.environ["TACTIC"]
LEMMA = os.environ.get("LEMMA", "?"); REPS = int(os.environ.get("REPS", "3"))


def med(xs):
    xs = sorted(xs); return xs[len(xs) // 2] if xs else None


def main():
    lines = gate._read_lines(THY)
    start, end = gate.find_proof_span(THY, PROOF_LINE)
    orig = "\n".join(lines[start - 1:end])
    # clarsimp variant: swap only the LEADING search tactic name, keep the rest (simp: ...)
    clar = re.sub(r"\b(auto|fastforce|force)\b", "clarsimp", orig, count=1)
    print(f"[ablate] {LEMMA} {THY.split('l4v/')[-1]}:{start}-{end}  {TACTIC}->clarsimp", flush=True)
    print(f"  orig:   {orig.strip()[:90]}", flush=True)
    print(f"  clar:   {clar.strip()[:90]}", flush=True)

    # CHEAP REJECT FIRST: does clarsimp even build? (1 build; most candidates die here)
    ok_c0, ms_c0, msg_c = gate.check_theory_builds(THY, SESSION, start, end, clar)
    if not ok_c0:
        print(f"  clarsimp BUILD-FAIL -> {(' '.join((msg_c or '').split()))[:150]}", flush=True)
        res = {"lemma": LEMMA, "thy": THY.split("l4v/")[-1], "session": SESSION, "tactic": TACTIC,
               "clar_builds": False, "base_ms_med": None, "clar_ms_med": None}
        print("[result] " + json.dumps(res, ensure_ascii=False), flush=True); return
    # it builds -> now time baseline vs clarsimp (A/B, REPS reps, non-overlapping check)
    base_ms, clar_ms = [], [ms_c0]
    for r in range(REPS):
        _, ms_b, _ = gate.check_theory_builds(THY, SESSION, start, end, orig)
        _, ms_c, _ = gate.check_theory_builds(THY, SESSION, start, end, clar)
        if ms_b: base_ms.append(ms_b)
        if ms_c: clar_ms.append(ms_c)
        print(f"  rep {r}: base={ms_b}ms  clar={ms_c}ms", flush=True)
    res = {"lemma": LEMMA, "thy": THY.split("l4v/")[-1], "session": SESSION,
           "tactic": TACTIC, "clar_builds": True,
           "base_ms_med": med(base_ms), "clar_ms_med": med(clar_ms),
           "base_ms": base_ms, "clar_ms": clar_ms,
           "base_ms_min": min(base_ms) if base_ms else None,
           "clar_ms_max": max(clar_ms) if clar_ms else None}
    if res["base_ms_med"] and res["clar_ms_med"]:
        res["delta_ms"] = round(res["base_ms_med"] - res["clar_ms_med"], 1)
        res["speedup"] = round(res["base_ms_med"] / res["clar_ms_med"], 3)
        # the criterion that matters: fraction of FILE wall this rewrite saves
        res["frac_saved"] = round((res["base_ms_med"] - res["clar_ms_med"]) / res["base_ms_med"], 3)
        # non-overlapping = worst clarsimp build still beats best baseline build
        res["non_overlap"] = bool(res.get("base_ms_min") and res.get("clar_ms_max")
                                  and res["base_ms_min"] > res["clar_ms_max"])
    print("[result] " + json.dumps(res, ensure_ascii=False), flush=True)


main()
