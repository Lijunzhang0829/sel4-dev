#!/usr/bin/env python3
"""goal_aware_reduce.py — CLAUDE-DRIVEN search-space reduction for HEAVY sessions (no Isa-REPL).

This is the goal-aware backend that run_reduce.sh dispatches to when the session is heavy
(CRefine/Refine/InfoFlowC or a >2900-line file) — the established conclusion is that the
Isa-REPL cannot init those, so the cheap ms reach-B loop is unavailable.

Difference from the in-REPL path: there is NO cheap feedback, so we make claude GOAL-AWARE
up front with RICH evidence, and claude (not a rule) decides the reduction:

  1. profile()  : own-session `isabelle process -T` with [[simp_trace]] on the target step ->
                  the proof GOAL at that step + a frequency table of the rewrite rules that
                  actually fire (the looping/most-frequent ones are evidence for `simp del:`;
                  the few that fire are evidence for `simp only:`).
  2. baseline() : own-session build (no patch) -> whole-theory wall + the target line's elapsed.
  3. claude     : the goal + the profiling table + the 5 techniques + few-shot are sent through
                  the SAME proposer_host bridge the in-REPL agent uses, so EVERY prompt/response
                  is recorded to reduce-transcript-<lemma>.jsonl (claude does the rewriting).
  4. verify()   : own-session build with the patch, capped at 1.2x baseline wall (an exploding
                  variant is killed fast, never burning hours). Accept iff: builds green AND
                  whole-theory wall < baseline AND target-line elapsed < baseline.

Own-session = rename the theory + qualify its imports against the heavy session's heap (read-only),
so the real session heap is never rebuilt/touched.

Env: THY, SESSION, TARGET_LINE, OUT_DIR, MAX_ROUNDS(=4), REL_TIMEOUT(=1.2), ABS_CAP(=3600),
     PROPOSER_BRIDGE(=./bridge).
"""
import os, re, json, time, secrets, shutil, subprocess, sqlite3, glob, bisect
from collections import Counter

ISA = os.environ.get("ISABELLE_HOME", "/workspace/verification/isabelle") + "/bin/isabelle"
L4V = os.environ.get("L4V_DIR", "/sel4-project/verification/l4v")
REL_TIMEOUT = float(os.environ.get("REL_TIMEOUT", "1.2"))
ABS_CAP = int(os.environ.get("ABS_CAP", "3600"))
BRIDGE = os.environ.get("PROPOSER_BRIDGE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge"))


# ---------------- own-session theory construction ----------------
def _mk(thy_abs, session, patch=None, trace_line=None, span_replace=None):
    base = os.path.basename(thy_abs)[:-4]
    tmp = "/tmp/gar_" + secrets.token_hex(4); os.makedirs(tmp)
    name = "Tmp_" + secrets.token_hex(6); dst = os.path.join(tmp, name + ".thy")
    src = open(thy_abs, encoding="utf-8").read().split("\n")
    if span_replace is not None:
        # replace a MULTI-LINE tactic span [start,end] (1-based, inclusive) with `text` (may be
        # multi-line) as a clean slice -> the variant occupies lines [start, start+text_lines-1].
        s, e, text = span_replace
        src[s - 1:e] = text.split("\n")
    if patch:
        for ln, new in patch.items(): src[int(ln) - 1] = new
    if trace_line is not None:
        # SCOPE the trace to JUST this step: wrap its method with `use [[simp_trace]] in <open>...<close>`
        # so the captured goal + rule frequencies belong to THIS line, not the whole theory.
        i = trace_line - 1
        mm = re.search(r'\b(?:by|apply)\s*\((.*)\)\s*$', src[i])
        if mm:
            meth = mm.group(1)
            _depth = os.environ.get("SIMP_TRACE_DEPTH", "1")   # 1 = legacy (fired rules only); >1 exposes condition-discharge
            src[i] = (src[i][:mm.start(1)]
                      + "use [[simp_trace, simp_trace_depth_limit=" + _depth + "]] in \\<open>" + meth + "\\<close>"
                      + src[i][mm.end(1):])
    src = "\n".join(src)
    src = re.sub(r'^theory\s+' + re.escape(base), 'theory ' + name, src, flags=re.M)
    src = re.sub(r'\b' + re.escape(base) + r'\.', name + '.', src)
    m = re.search(r'(imports\s*)(.*?)(\nbegin)', src, re.DOTALL)
    if m:
        def q(t):
            o = []; i = 0
            while i < len(t):
                c = t[i]
                if c == '"': j = t.index('"', i + 1) + 1; o.append(t[i:j]); i = j
                elif c.isspace(): o.append(c); i += 1
                else:
                    j = i
                    while j < len(t) and not t[j].isspace(): j += 1
                    tok = t[i:j]; o.append(tok if '.' in tok else session + '.' + tok); i = j
            return ''.join(o)
        src = src[:m.start(2)] + q(m.group(2)) + src[m.end(2):]
    open(dst, "w", encoding="utf-8").write(src)
    open(os.path.join(tmp, "ROOT"), "w").write(f"session {name}_S = {session} +\n  theories\n    {name}\n")
    return tmp, name, dst


def _o2l(path):
    s = open(path, encoding="utf-8", errors="replace").read(); st = [0]; sym = 0; i = 0; n = len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n and s[i + 1] == "<":
            j = s.find(">", i)
            if j != -1: i = j + 1; sym += 1; continue
        if c == "\n": st.append(sym + 1)
        sym += 1; i += 1
    return lambda o: bisect.bisect_right(st, o)


def _timings(name, dst):
    heaps = subprocess.run([ISA, "getenv", "-b", "ISABELLE_HEAPS"], capture_output=True, text=True).stdout.strip()
    dbp = None
    for d in glob.glob(heaps + "/**/" + name + "_S.db", recursive=True): dbp = d
    if not dbp: return {}
    row = sqlite3.connect(dbp).execute("SELECT command_timings FROM isabelle_session_info").fetchone()
    if not row: return {}
    import zstandard as z
    d = z.ZstdDecompressor().decompress(bytes(row[0]))
    f = _o2l(dst); best = {}; cur = {}
    for tok in d.decode("utf-8", "replace").split("\x06"):
        tok = tok.replace("\x05", "")
        if "=" not in tok: continue
        k, _, v = tok.partition("="); k = k.strip()
        if k in ("name", "offset", "file", "elapsed"):
            cur[k] = v
            if k == "elapsed" and "file" in cur and "offset" in cur:
                ln = f(int(cur["offset"])); best[ln] = max(best.get(ln, 0.0), float(cur["elapsed"])); cur = {}
    return best


# ---------------- the three goal-aware ops ----------------
def baseline(thy, session, line):
    tmp, name, dst = _mk(thy, session)
    t0 = time.time()
    r = subprocess.run(["timeout", "--kill-after=30s", str(ABS_CAP), ISA, "build", "-d", L4V, "-d", tmp, f"{name}_S"],
                       capture_output=True, text=True)
    wall = int(time.time() - t0); tm = _timings(name, dst) if r.returncode == 0 else {}
    shutil.rmtree(tmp, ignore_errors=True)
    return {"ok": r.returncode == 0, "wall": wall, "line_ms": tm.get(line),
            "err": "" if r.returncode == 0 else (r.stdout + r.stderr)[-600:]}


def profile(thy, session, line, top=12, cap_bytes=80_000_000):
    # simp_trace of a multi-100s simp can emit GBs; cap the captured trace so we never OOM.
    tmp, name, dst = _mk(thy, session, trace_line=line)
    tracef = os.path.join(tmp, "trace.txt")
    cmd = (f"timeout --kill-after=30s {ABS_CAP} {ISA} process -l {session} -d {L4V} "
           f"-T {os.path.join(tmp, name)} 2>&1 | head -c {cap_bytes} > {tracef}")
    subprocess.run(cmd, shell=True)
    out = open(tracef, encoding="utf-8", errors="replace").read() if os.path.exists(tracef) else ""
    rules = Counter(m.group(1) for m in re.finditer(r'rewrite rule "([^"]+)"', out))
    # grab the goal printed nearest the heaviest simp invocation (best-effort)
    goal = ""
    gm = re.search(r"SIMPLIFIER INVOKED ON THE FOLLOWING TERM:\s*\n(.*?)(?:\n\[|\Z)", out, re.DOTALL)
    if gm: goal = gm.group(1).strip()[:1200]
    shutil.rmtree(tmp, ignore_errors=True)
    total = sum(rules.values())
    anon = rules.pop("??.unknown", 0)        # anonymous rewrites (def-unfolding etc.) — not actionable
    # the `| head` pipe masks isabelle's exit code; "ok" = we captured the goal or named rules.
    return {"ok": bool(goal) or bool(rules), "goal": goal, "top_rules": rules.most_common(top),
            "n_rewrites": total, "anon_rewrites": anon,
            "anon_pct": round(100 * anon / total, 1) if total else 0}


def verify(thy, session, line, variant, base_wall):
    cap = min(int(REL_TIMEOUT * base_wall) + 30, ABS_CAP)
    tmp, name, dst = _mk(thy, session, patch={line: variant})
    t0 = time.time()
    r = subprocess.run(["timeout", "--kill-after=30s", str(cap), ISA, "build", "-d", L4V, "-d", tmp, f"{name}_S"],
                       capture_output=True, text=True)
    wall = int(time.time() - t0)
    if r.returncode in (124, 137):
        shutil.rmtree(tmp, ignore_errors=True)
        return {"verdict": "TIMEOUT", "wall": wall, "cap": cap}
    if r.returncode != 0:
        err = (r.stdout + r.stderr)[-600:]; shutil.rmtree(tmp, ignore_errors=True)
        return {"verdict": "BUILD-FAIL", "wall": wall, "err": err}
    tm = _timings(name, dst); shutil.rmtree(tmp, ignore_errors=True)
    return {"verdict": "OK", "wall": wall, "line_ms": tm.get(line)}


# ---------------- claude via the SAME bridge as in-REPL (recorded) ----------------
def claude(prompt, transcript, timeout=300):
    # proposer_host protocol: flat req-<rid>.json / resp-<rid>.json; genstat response under "script".
    os.makedirs(BRIDGE, exist_ok=True)
    rid = secrets.token_hex(8); req = os.path.join(BRIDGE, f"req-{rid}.json")
    json.dump({"mode": "genstat", "prompt": prompt}, open(req + ".tmp", "w")); os.replace(req + ".tmp", req)
    resp = os.path.join(BRIDGE, f"resp-{rid}.json"); t0 = time.time(); out = ""; err = ""
    while time.time() - t0 < timeout:
        if os.path.exists(resp):
            d = json.load(open(resp)); out = d.get("script", ""); err = d.get("err", ""); break
        time.sleep(2)
    else:
        err = "bridge-timeout"
    # record the FULL claude rewriting interaction (uniform with reduce_agent._record)
    open(transcript, "a").write(json.dumps({"rid": rid, "claude_secs": round(time.time() - t0, 1),
                                            "err": err, "prompt": prompt, "response": out}, ensure_ascii=False) + "\n")
    return out, err


PROMPT = """You are OPTIMISING one slow Isabelle proof step on the CRITICAL PATH. Keep it CORRECT, make it FASTER.
Do NOT swap to a different search tactic (auto->fastforce EXPLODES on these goals). Make the SAME
tactic do LESS, reaching the same state. There is NO cheap retry here — be precise the first time.

Five reduction techniques:
  1. NORMALISE first: prepend `clarsimp`/`simp only: <few defs>` so the search has less to explore.
  2. DROP a looping rewrite: `... simp del: <rule>` (use the evidence below).
  3. TARGET the simpset: `simp: <only the defs that actually fire>` instead of a big bundle.
  4. SPLIT: `clarsimp` then a small close.   5. REORDER: cheap structural rules before the search.

PROFILING EVIDENCE for this exact step:
GOAL the simp is fighting:
{goal}
Rewrite rules that fire (count x rule) — high-count ones are `simp del:` candidates; if only a few
fire, they are your `simp only:` set:
{rules}

Original (slow) step (line {line}):
{orig}
{feedback}
Output ONLY the replacement apply/by line (single line, same indentation). No prose."""


def run(thy, session, line, out_dir, rounds=4):
    os.makedirs(out_dir, exist_ok=True)
    lemma = os.path.basename(thy)[:-4]
    transcript = os.path.join(out_dir, f"reduce-transcript-{lemma}-L{line}.jsonl")
    orig = open(thy, encoding="utf-8").read().split("\n")[line - 1]
    rec = {"thy": thy, "session": session, "line": line, "orig": orig, "method": "goal-aware",
           "rel_timeout": REL_TIMEOUT, "rounds": []}
    log = lambda m: print(m, flush=True)

    log("[baseline] own-session build ...")
    base = baseline(thy, session, line); rec["baseline"] = base
    if not base["ok"]:
        rec["verdict"] = "BASELINE-FAIL"; json.dump(rec, open(out_dir + "/result.json", "w"), indent=1); return rec
    bw, bl = base["wall"], base["line_ms"]
    log(f"[baseline] wall={bw}s line{line}={bl}s  cap={REL_TIMEOUT}x={int(REL_TIMEOUT*bw)}s")

    log("[profile] simp_trace (scoped to this step) ...")
    prof = profile(thy, session, line); rec["profile"] = prof
    named = "\n".join(f"  {n:>6} x  {r}" for r, n in prof["top_rules"]) or "  (no NAMED rule fired prominently)"
    rules_txt = (f"{named}\n  ({prof.get('anon_rewrites',0)} anonymous rewrites = {prof.get('anon_pct',0)}% "
                 f"of all work — if this is HIGH, the simp is doing real def-unfolding, not a named loop, "
                 f"so `simp del:` won't help; try `simp only:` with exactly the defs the GOAL needs, or report it is irreducible)")
    goal_txt = prof["goal"] or "  (goal not captured)"
    log(f"[profile] {prof['n_rewrites']} rewrites, {prof.get('anon_pct',0)}% anon; top named: {prof['top_rules'][:2]}")

    # anon%-based PRE-SCREEN: if the simp is dominated by anonymous def-unfolding rewrites, there is
    # no named looping rule to drop -> reduction is structurally hopeless. Skip the (expensive) claude
    # rounds + verify builds and report it up front, so we spend compute only where a win is possible.
    ANON_CAP = float(os.environ.get("ANON_CAP", "85"))
    if prof.get("anon_pct", 0) >= ANON_CAP:
        rec["verdict"] = "PROFILE-IRREDUCIBLE"
        rec["note"] = (f"{prof.get('anon_pct')}% anonymous rewrites >= {ANON_CAP}% -> the simp is real "
                       f"def-unfolding work (no named looping rule to `simp del:`); claude rounds skipped.")
        log(f"[early-exit] anon={prof.get('anon_pct')}% >= {ANON_CAP}% -> PROFILE-IRREDUCIBLE (no wasted builds)")
        json.dump(rec, open(out_dir + "/result.json", "w"), indent=1, ensure_ascii=False)
        return rec

    feedback = ""
    for rnd in range(rounds):
        prompt = PROMPT.format(goal=goal_txt, rules=rules_txt, line=line, orig=orig, feedback=feedback)
        variant, err = claude(prompt, transcript)
        variant = variant.strip().splitlines()[0].strip() if variant.strip() else ""
        log(f"[round {rnd}] claude -> {variant!r} (err={err!r})")
        if not variant:
            rec["rounds"].append({"round": rnd, "verdict": "NO-PROPOSAL", "err": err}); continue
        v = verify(thy, session, line, variant, bw); v["round"] = rnd; v["variant"] = variant
        rec["rounds"].append(v)
        log(f"[round {rnd}] {v['verdict']} wall={v.get('wall')} line={v.get('line_ms')}")
        if v["verdict"] == "TIMEOUT":
            feedback = f"\nYour variant EXPLODED (>{REL_TIMEOUT}x baseline build). Be far more conservative: `simp del:` ONE high-count rule above, keep the tactic class.\n"
        elif v["verdict"] == "BUILD-FAIL":
            feedback = f"\nFailed to build:\n{v['err'][-300:]}\nIt must reach the SAME state. Smaller change.\n"
        else:
            vw, vl = v["wall"], v.get("line_ms")
            if vw < bw and (vl is None or bl is None or vl < bl):
                rec.update(verdict="FASTER", accepted=variant,
                           wall_delta_pct=round(100 * (bw - vw) / bw, 1),
                           line_delta_pct=(round(100 * (bl - vl) / bl, 1) if (bl and vl) else None))
                log(f"[ACCEPT] wall {bw}->{vw}s ({rec['wall_delta_pct']}%) line {bl}->{vl}s")
                break
            feedback = f"\nCORRECT but NOT faster (line {bl}->{vl}s, wall {bw}->{vw}s). Reduce MORE per the evidence.\n"
    rec.setdefault("verdict", "NO-FASTER-VARIANT")
    json.dump(rec, open(out_dir + "/result.json", "w"), indent=1, ensure_ascii=False)
    log(f"VERDICT: {rec['verdict']}")
    return rec


if __name__ == "__main__":
    run(os.environ["THY"], os.environ["SESSION"], int(os.environ["TARGET_LINE"]),
        os.environ["OUT_DIR"], int(os.environ.get("MAX_ROUNDS", "4")))
