"""reduce_agent.py — Direction A: search-space REDUCTION via LLM (claude -p).

genstat/DFS tried to ELIMINATE the search tactic with a STATIC script and FAILED on every
valuable line: the goals diverge because the tactic's power is integrated backtracking search
that does not decompose into a no-backtrack static sequence.

This agent does the opposite. It KEEPS the automation but asks the LLM to make it do LESS
work and reach the SAME state B, FASTER:
  - prepend `clarsimp` / `simp only: <targeted>` to normalise the goal BEFORE the search tactic
  - `auto`/`fastforce`/`simp` with `simp del: <expensive looping rule>` to drop a hot rewrite
  - a TARGETED `simp:`/`fastforce simp:` set instead of the whole default simpset
  - split into `clarsimp` (cheap) + a small close
The variant MAY use auto/fastforce/blast/simp — it just must search LESS.

Two gates:
  (1) reach-B (in-REPL): the variant from checkpoint A reaches the SAME signature B the
      ORIGINAL tactic leaves. Fast pre-filter; CAN false-positive on long wrapped goals.
  (2) FASTER (in-REPL): variant_ms < orig_ms*(1-MARGIN), timed at A (median of reps).
Any winner MUST then pass ground-truth `build_verify_reachb.py` (real check-theory build).

claude -p full transcript is recorded by proposer_host's _record (mode=genstat passthrough).

Env: THY, LEMMA, SESSION, PORT, L4V_DIR, TARGET_SUBSTR(_B64), MAX_ROUNDS(=8),
     CLAUDE_TIMEOUT(=600), MARGIN(=0.1), RESULT_TAG.
"""
import os, re, json, time, base64, subprocess
from py4j.java_gateway import JavaGateway, GatewayParameters
import react_agent as RA
import genstat_stateful as GS   # reuse apply_chunk / parse_script / canon / clean_goal (all standalone)

L4V = os.environ.get("L4V_DIR", "/sel4-project/verification/l4v")
THY = os.environ.get("THY", os.path.join(L4V, "proof/access-control/CNode_AC.thy"))
LEMMA = os.environ.get("LEMMA", "empty_slot_pas_refined")
SESSION = os.environ.get("SESSION", "Access")
PORT = int(os.environ.get("PORT", "25600")); SEP = RA.SEP
_TAG = os.environ.get("RESULT_TAG", "")
TARGET_SUBSTR = os.environ.get("TARGET_SUBSTR", "")
_tb = os.environ.get("TARGET_SUBSTR_B64", "")
if _tb: TARGET_SUBSTR = base64.b64decode(_tb).decode("utf-8")
MAX_ROUNDS = int(os.environ.get("MAX_ROUNDS", "8"))
CLAUDE_TIMEOUT = float(os.environ.get("CLAUDE_TIMEOUT", "600"))
MARGIN = float(os.environ.get("MARGIN", "0.1"))   # require variant <= orig*(1-MARGIN)
RUNS = RA.RUNS; BRIDGE = RA.BRIDGE
AUTO = re.compile(r"\b(auto|force|fastforce|blast|clarsimp|safe)\b")
_seq = [0]


def claude(prompt):
    """Whole-prompt passthrough via the HOST bridge (mode=genstat); proposer_host runs
    `claude -p <prompt>` and returns the raw apply-script. REQUIRES proposer_host on the host."""
    _seq[0] += 1; rid = f"D{PORT}_{os.getpid()}_{_seq[0]:04d}"
    os.makedirs(BRIDGE, exist_ok=True)
    req = os.path.join(BRIDGE, f"req-{rid}.json"); resp = os.path.join(BRIDGE, f"resp-{rid}.json")
    json.dump({"mode": "genstat", "prompt": prompt}, open(req + ".tmp", "w")); os.replace(req + ".tmp", req)
    t0 = time.monotonic()
    while time.monotonic() - t0 < CLAUDE_TIMEOUT:
        if os.path.exists(resp):
            try: d = json.load(open(resp))
            except Exception: d = {}
            out = (d.get("script") or "").strip(); csec = d.get("secs")
            _record(rid, prompt, out, round(time.monotonic() - t0, 1), csec, d.get("err", ""))
            return out, csec
        time.sleep(0.5)
    _record(rid, prompt, "", CLAUDE_TIMEOUT, None, "bridge-timeout")
    return "", None


def _save(rec):
    """Persist result INCREMENTALLY so a TLIMIT kill mid-loop still keeps a found winner
    (the loop continues after a win to try to beat it; without this, timeout loses it)."""
    try:
        json.dump(rec, open(os.path.join(RUNS, f"reduce-{LEMMA}{_TAG}.json"), "w"), ensure_ascii=False, indent=1)
    except Exception:
        pass


def _record(rid, prompt, response, bridge_secs, claude_secs, err=""):
    try:
        open(os.path.join(RUNS, f"reduce-transcript-{LEMMA}{_TAG}.jsonl"), "a").write(json.dumps(
            {"rid": rid, "lemma": LEMMA, "bridge_secs": bridge_secs, "claude_secs": claude_secs,
             "err": err, "prompt": prompt, "response": response}, ensure_ascii=False) + "\n")
    except Exception:
        pass


PROMPT = """You are OPTIMISING one slow Isabelle proof step. Keep it CORRECT but make it FASTER.

The original step is a SEARCH tactic (auto/fastforce/force/blast/simp) doing a lot of backtracking
search. Do NOT replace it with a static rule script (that fails — the search is needed). Instead
make the SAME tactic do LESS work and reach the SAME state, faster. YOU decide how; the five
techniques below are a menu, not a fixed recipe.

Five reduction techniques:
  1. NORMALISE first: prepend `apply clarsimp` or `apply (simp only: <few targeted defs>)` so the
     following search tactic has far less to explore.
  2. DROP a looping/expensive rewrite: `... simp del: <rule>` (e.g. a rule that rewrites forever).
  3. TARGET the simpset: `fastforce simp: <only the defs actually needed>` instead of a big bundle.
  4. SPLIT: `apply clarsimp` then a small close.
  5. REORDER: cheap structural rules (conjI/clarify) before the heavy search.

Examples:
  # technique 3 (targeted simpset) — narrow a huge bundle to the 1-2 defs the goal needs:
  orig:   by (fastforce simp: obj_at'_def projectKOs invs'_def valid_state'_def st_tcb_at'_def)
  faster: by (clarsimp simp: obj_at'_def projectKOs, fastforce simp: st_tcb_at'_def)
  # technique 1 (normalise then search) — clarsimp clears the easy structure first:
  orig:   apply fastforce
  faster: apply (clarsimp simp: split_def) apply fastforce
  # technique 2 (drop a looping rewrite) — remove one rule that explodes the term:
  orig:   apply (auto simp: cap_lift_def cap_tag_defs word_bw_assocs)
  faster: apply (auto simp: cap_lift_def cap_tag_defs simp del: word_bw_assocs)

Lemma:
{stmt}

Original (slow) step being optimised:
{orig}

TARGET state to reach (MUST match exactly): {target}
{hints}{feedback}
Output ONLY the apply-script (apply-lines, end with `done` if it closes). No prose."""


def main():
    rec = {"lemma": LEMMA, "thy": THY, "session": SESSION, "mode": "reduce",
           "verdict": "NO-PATH", "orig_ms": None, "rounds": []}
    isa = None; A_cp = None; orig_ms = None
    # launch the REPL JVM for this PORT (same as react/genstat), then poll the gateway
    env = os.environ.copy(); env["ISABELLE_HOME"] = RA.ISABELLE_HOME
    subprocess.Popen([RA._java(), "-Xmx8g", "-jar", RA.JAR, str(PORT)], env=env,
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
        assert gw is not None, "JVM gateway never came up"
        isa = gw.entry_point
        ok, _ = RA.sr(isa._initializeRepl(THY, L4V, SESSION, [L4V])); assert ok
        print("[init ok]", flush=True)
        steps = [c for c in isa._parse_to_steps(open(THY, encoding="utf-8").read()).split(SEP) if not RA.isc(c)]
        idx = next(i for i, s in enumerate(steps)
                   if RA.ft(s) in ("lemma", "theorem", "corollary") and re.search(re.escape(LEMMA) + r"\s*[:\[]", s))
        u = ""
        for s in RA.rbs(steps[:idx])[1:]:
            r = isa._step_without_timeout(u + s); u = u + s + "\n" if "False" + SEP in r else ""
        RA.sr(isa._step(steps[idx])); print(f"[reached {LEMMA}]", flush=True)
        idx2 = next(i for i in range(idx + 1, len(steps))
                    if RA.ft(steps[i]) in ("lemma", "lemmas", "definition", "theorem", "corollary"))
        proof = steps[idx + 1:idx2]
        # locate the cost-target search line (same as genstat/react)
        hint = TARGET_SUBSTR
        if not hint:
            cl = [(i, s) for i, s in enumerate(proof) if RA.ft(s) in ("apply", "by", "subgoal") and AUTO.search(s)]
            tmap = RA.db_classical_elapsed(THY) if len(cl) > 1 else {}
            if tmap:
                _el = lambda s: max((el for k, el in tmap.items() if k and k in RA._norm(s)), default=0.0)
                sc = sorted(((_el(s), i, s) for i, s in cl), key=lambda x: -x[0])
                if sc and sc[0][0] > 0: hint = sc[0][2]
        u = ""; target = None
        for i, s in enumerate(proof):
            cp = f"N{i}"; isa._clone_tls(cp)
            is_t = RA.ft(s) in ("apply", "by", "subgoal") and AUTO.search(s)
            if hint and is_t and not RA._hint_in(hint, s): is_t = False
            if is_t:
                pre = cp; st = s
                if i > 0 and RA.ft(proof[i - 1]) == "subgoal":
                    pre = f"N{i - 1}"; st = proof[i - 1] + "\n" + s
                isa._focus_tls(pre); _, A_goal = RA.sr(isa._extract_goal())
                isa._focus_tls(pre); _t0 = time.monotonic(); _, mb = RA.sr(isa._step_without_timeout(st))
                _om = (time.monotonic() - _t0) * 1000; B_sig = RA.signature(mb)
                target = (i, st, pre, A_goal, B_sig, _om); break
            else:
                r = isa._step_without_timeout(u + s); u = u + s + "\n" if "False" + SEP in r else ""
        if not target:
            print("[no target classical line]", flush=True); rec["verdict"] = "NO-TARGET"; return
        i, orig, A_cp, A_goal, B_sig, orig_ms = target
        # ADAPTIVE timing reps: a slow original (e.g. 261s) must NOT be timed 3x (13min/measure).
        # Measure once; if cheap (<4s) re-measure at reps=3 for stability, else stay reps=1.
        try:
            orig_ms, _ipc = RA.time_static_path(isa, A_cp, [orig], reps=1)
            REPS = 3 if orig_ms < 4000 else 1
            if REPS == 3:
                orig_ms, _ipc = RA.time_static_path(isa, A_cp, [orig], reps=3)
        except Exception:
            REPS = 1
        facts = []
        try: facts = sorted(set(RA.parse_hammer_facts(isa._prove_by_hammer(), orig)))
        except Exception: pass
        target_desc = ("close the goal (0 subgoals)" if not B_sig
                       else f"leave EXACTLY these {len(B_sig)} subgoal(s):\n" + "\n".join(B_sig)[:500])
        stmt = re.sub(r"\s+", " ", steps[idx]).strip()[:700]
        hints = (f"Lemmas you may cite: {', '.join(facts[:14])}\n" if facts else "")
        rec.update(target=orig.strip(), orig_ms=round(orig_ms, 1), B_sig=list(B_sig), facts=facts)
        _save(rec)   # preserve target+orig_ms even if a later round times out
        print(f"== target: {orig.strip()[:70]}\n   orig_ms={orig_ms:.0f}  B: {target_desc[:70]}", flush=True)

        # REMEASURE mode: rigorous A/B with noise floor. Given a fixed VARIANT, time the ORIGINAL
        # K times (sample A), the ORIGINAL again K times (sample A' = the measurement noise floor),
        # and the VARIANT K times — all from the same checkpoint A in this one REPL. A win is real
        # iff (orig_med - var_med) clears the A-vs-A' noise. (isar build-context timing is broken
        # — missing-json — so this controlled in-REPL A/B is the rigorous fallback.)
        if os.environ.get("VARIANT_B64"):
            import statistics as _st
            variant = GS.parse_script(base64.b64decode(os.environ["VARIANT_B64"]).decode())
            K = 4 if orig_ms > 30000 else 7   # take K, DISCARD the first (cold-start warmup) -> median of rest
            def smp(p):
                xs = [RA.time_static_path(isa, A_cp, p, reps=1)[0] for _ in range(K)]
                return xs, [x for x in xs[1:] if x and x > 0]   # raw, warm(non-cold, non-zero)
            oa_raw, oa = smp([orig]); oa2_raw, oa2 = smp([orig]); vb_raw, vb = smp(variant)
            om = _st.median(oa) if oa else 0.0
            rec.update(verdict="REMEASURE", variant=variant, K=K,
                       orig_A_raw=[round(x, 1) for x in oa_raw], orig_A2_raw=[round(x, 1) for x in oa2_raw],
                       variant_raw=[round(x, 1) for x in vb_raw],
                       orig_med=round(om, 1), orig_med2=round(_st.median(oa2), 1) if oa2 else 0.0,
                       var_med=round(_st.median(vb), 1) if vb else 0.0,
                       noise_pct=round(100 * abs(_st.median(oa) - _st.median(oa2)) / om, 1) if (om and oa2) else None,
                       saved_pct=round(100 * (om - _st.median(vb)) / om, 1) if (om and vb) else None)
            print(f"[remeasure K={K} drop-1] orig_A(warm)={[round(x,0) for x in oa]}\n  orig_A'={[round(x,0) for x in oa2]}\n  variant={[round(x,0) for x in vb]}", flush=True)
            print(f"  orig_med={rec['orig_med']} orig'_med={rec['orig_med2']} var_med={rec['var_med']} "
                  f"| noise={rec['noise_pct']}% saved={rec['saved_pct']}%", flush=True)
            _save(rec); return

        best = None; feedback = ""; empties = 0
        for rnd in range(MAX_ROUNDS):
            prompt = PROMPT.format(stmt=stmt, orig=orig.strip(), target=target_desc,
                                   hints=hints, feedback=feedback)
            t0 = time.time(); raw, csec = claude(prompt); gen = round(time.time() - t0, 1)
            variant = GS.parse_script(raw)
            print(f"  round {rnd}: gen={gen}s (claude {csec}s) -> {variant}", flush=True)
            if not variant:
                empties += 1
                if empties >= 3: rec["verdict"] = "CHANNEL-FAIL"; break
                continue
            empties = 0
            ok, ncp, sig, diag = GS.apply_chunk(isa, A_cp, variant, rnd)
            if not ok:
                feedback = f"\nYour variant FAILED: {diag}\nTry a different reduction (keep the search tactic, normalise less aggressively).\n"
                rec["rounds"].append({"round": rnd, "variant": variant, "outcome": "FAILED", "diag": diag[:200], "gen_secs": gen, "claude_secs": csec})
                print(f"  round {rnd}: FAILED {diag[:70]}", flush=True); continue
            if GS.canon(sig) != GS.canon(B_sig):
                feedback = f"\nYour variant reached the WRONG state ({len(sig)} subgoals); TARGET needs {('close' if not B_sig else str(len(B_sig))+' subgoals')}. Adjust.\n"
                rec["rounds"].append({"round": rnd, "variant": variant, "outcome": "WRONG-STATE", "subgoals": len(sig), "gen_secs": gen, "claude_secs": csec})
                print(f"  round {rnd}: WRONG-STATE ({len(sig)} subgoals)", flush=True); continue
            # reach-B OK -> time the variant; require FASTER
            try: vms, _ipc = RA.time_static_path(isa, A_cp, variant, reps=REPS)
            except Exception as e: vms = None
            faster = bool(vms is not None and orig_ms > 0.5 and vms < orig_ms * (1 - MARGIN))
            saved = round(orig_ms - vms, 1) if vms is not None else None
            rec["rounds"].append({"round": rnd, "variant": variant, "outcome": "REACH-B",
                                  "variant_ms": vms, "saved_ms": saved, "faster": faster,
                                  "gen_secs": gen, "claude_secs": csec})
            print(f"  round {rnd}: REACH-B variant_ms={vms:.0f} orig_ms={orig_ms:.0f} saved={saved} faster={faster}", flush=True)
            if faster and (best is None or vms < best["variant_ms"]):
                best = {"variant": variant, "variant_ms": round(vms, 1), "saved_ms": saved,
                        "frac_saved": round(saved / orig_ms, 3)}
                # persist the win NOW (a later round + TLIMIT kill must not lose it)
                rec.update(verdict="FASTER", path=best["variant"], variant_ms=best["variant_ms"],
                           saved_ms=best["saved_ms"], frac_saved=best["frac_saved"], accelerated=True)
                _save(rec)
            if faster:
                # keep going to try to beat it, but with a "beat X ms" nudge
                feedback = f"\nGood: reached the target in {vms:.0f}ms (orig {orig_ms:.0f}ms). Try to go EVEN faster (drop more rules / normalise harder), same target.\n"
            else:
                feedback = f"\nYour variant is CORRECT but NOT faster ({vms:.0f}ms vs orig {orig_ms:.0f}ms). Reduce the search MORE: prepend a stronger `clarsimp`/`simp only:` normaliser, or `simp del:` the expensive rule, then a lighter close.\n"
        if best:
            rec.update(verdict="FASTER", path=best["variant"], variant_ms=best["variant_ms"],
                       saved_ms=best["saved_ms"], frac_saved=best["frac_saved"], accelerated=True)
            print(f"[WIN] {orig_ms:.0f}ms -> {best['variant_ms']:.0f}ms  saved={best['saved_ms']}ms ({best['frac_saved']*100:.0f}%)", flush=True)
        elif rec["verdict"] == "NO-PATH" and any(r["outcome"] == "REACH-B" for r in rec["rounds"]):
            rec["verdict"] = "REACHED-B-NOT-FASTER"; rec["accelerated"] = False
    except Exception as e:
        rec["verdict"] = "ERROR"; rec["error"] = str(e)[:200]; print("[error]", e, flush=True)
    finally:
        try:
            json.dump(rec, open(os.path.join(RUNS, f"reduce-{LEMMA}{_TAG}.json"), "w"), ensure_ascii=False, indent=1)
            print(f"[saved] {RUNS}/reduce-{LEMMA}{_TAG}.json  verdict={rec['verdict']}", flush=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
