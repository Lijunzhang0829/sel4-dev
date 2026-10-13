"""A/B time the ORIGINAL search tactic vs the REACHED-B static path, from checkpoint A,
in-REPL. Reports median over REPS, with an IPC-per-step baseline subtracted (the static
path has more steps -> more py4j round-trips, which would otherwise bias against it).
Env: LEMMA THY SESSION PORT L4V_DIR  [REPS=9]  ; reads /workspace/tools/seL4-proof-search/Isa-Repl/runs/reachb_paths.json
"""
import os, re, json, time, subprocess
from py4j.java_gateway import JavaGateway, GatewayParameters
import react_agent as RA

LEMMA = os.environ["LEMMA"]; THY = os.environ["THY"]; SESSION = os.environ["SESSION"]
PORT = int(os.environ["PORT"]); L4V = os.environ["L4V_DIR"]; REPS = int(os.environ.get("REPS", "9"))
SEP = RA.SEP
D = json.load(open("/workspace/tools/seL4-proof-search/Isa-Repl/runs/reachb_paths.json"))[LEMMA]
ORIG = D["orig"]; PATH = D["path"]
AUTO = re.compile(r"\b(auto|force|fastforce|blast|clarsimp|safe|simp_all)\b")


def med(xs): xs = sorted(xs); return xs[len(xs) // 2]


def run_from(isa, A_cp, cmds):
    """clone A, run cmds, return elapsed ms (incl py4j IPC for len(cmds) steps)."""
    isa._focus_tls(A_cp); isa._clone_tls("TT"); isa._focus_tls("TT")
    t0 = time.monotonic()
    for c in cmds:
        isa._step_without_timeout(c)
    return (time.monotonic() - t0) * 1000.0


def main():
    env = os.environ.copy(); env["ISABELLE_HOME"] = RA.ISABELLE_HOME
    proc = subprocess.Popen([RA._java(), "-Xmx8g", "-jar", RA.JAR, str(PORT)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, text=True)
    try:
        gw = None
        for _ in range(30):
            time.sleep(3)
            try:
                gw = JavaGateway(gateway_parameters=GatewayParameters(port=PORT, auto_convert=True, read_timeout=900))
                gw.entry_point.toString(); break
            except Exception:
                gw = None
        isa = gw.entry_point
        ok, _ = RA.sr(isa._initializeRepl(THY, L4V, SESSION, [L4V])); assert ok
        steps = [c for c in isa._parse_to_steps(open(THY, encoding="utf-8").read()).split(SEP) if not RA.isc(c)]
        idx = next(i for i, s in enumerate(steps)
                   if RA.ft(s) in ("lemma", "theorem", "corollary") and re.search(re.escape(LEMMA) + r"\s*[:\[]", s))
        u = ""
        for s in RA.rbs(steps[:idx])[1:]:
            r = isa._step_without_timeout(u + s); u = u + s + "\n" if "False" + SEP in r else ""
        RA.sr(isa._step(steps[idx]))
        idx2 = next(i for i in range(idx + 1, len(steps)) if RA.ft(steps[i]) in ("lemma", "lemmas", "definition", "theorem", "corollary"))
        proof = steps[idx + 1:idx2]
        # step proof prefix to the search line; checkpoint A just before it
        A_cp = None; u = ""
        for s in proof:
            if RA.ft(s) in ("apply", "by") and AUTO.search(s):
                isa._clone_tls("A"); A_cp = "A"; break
            isa._step_without_timeout(u + s)
        assert A_cp, "no search line"
        # NOISE DISCIPLINE (per measurement-tools.md): drop the warm-up run (first run is
        # 2-4.5x inflated: JIT/simpset cache/lazy load), take median of >=REPS reps, and
        # measure an A/A noise floor (time ORIG twice). This is a ROUGH in-REPL SCREEN ONLY
        # — never a reported speed number; a positive ceiling here must be confirmed by a
        # stock command_timings build, and correctness by check_theory_selfqual --patch.
        def batch(cmds):
            run_from(isa, A_cp, cmds)                                  # warm-up, DISCARDED
            return med([run_from(isa, A_cp, cmds) for _ in range(REPS)])
        ipc = batch(["apply -"])
        oa = batch([ORIG]); ob = batch([ORIG]); op = batch(PATH)       # A/A: ORIG twice
        adj_orig = max(0.0, med([oa, ob]) - 1 * ipc)
        adj_path = max(0.0, op - len(PATH) * ipc)
        aa_spread = abs(oa - ob)                                       # noise floor
        ceiling = adj_orig - adj_path                                 # search-acceleration ceiling C_i
        significant = bool(ceiling > 0 and ceiling > 2 * aa_spread)   # only claim if > 2x A/A
        print(json.dumps({
            "lemma": LEMMA, "ipc_ms": round(ipc, 1), "orig": ORIG[:40], "static_steps": len(PATH),
            "orig_ms_adj": round(adj_orig, 1), "static_ms_adj": round(adj_path, 1),
            "aa_spread_ms": round(aa_spread, 1), "ceiling_ms": round(ceiling, 1),
            "significant": significant,
            "note": "in-REPL SCREEN only; confirm winners with stock command_timings",
        }, ensure_ascii=False), flush=True)
    finally:
        try: isa._exit()
        except Exception: pass
        proc.terminate(); proc.wait()


main()
