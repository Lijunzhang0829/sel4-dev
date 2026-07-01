"""GenStat-STATEFUL: LLM segment-rewrite WITH the real intermediate proof state.

Contrast with rewrite_agent.py (GenStat-STATELESS): that one hands the LLM only the
lemma STATEMENT + the original tactic, regenerates a whole apply-script, and
build-verifies. It has no handle on the goal entering a mid-proof search line.

This variant uses the REPL exactly like DFS/react_agent: it sorry-replaces the
preceding proofs, steps to the located (cost-aware) search line, and extracts
  A = the REAL goal entering that line   (isa._extract_goal)
  B = the subgoal signature the ORIGINAL tactic leaves   (apply orig, signature)
then hands BOTH to the LLM and asks for a STATIC apply-script that takes A -> B.
Verification is IN-REPL: apply the script from the A checkpoint and check the
resulting signature == B (same success test DFS uses). On failure the ACTUAL
resulting goal state is fed back for self-correction.

So: DFS's stateful, B-targeted harness, but the fixed menu+backtrack search is
replaced by one LLM that proposes the whole segment (open vocabulary, multi-line).
This is the "give the LLM the context state and let it decide" design.

Env: THY, LEMMA, SESSION, PORT, L4V_DIR, TARGET_SUBSTR, MAX_ROUNDS, REWRITE_MODEL,
     CLAUDE_TIMEOUT.
"""
import os, re, json, time, subprocess
from py4j.java_gateway import JavaGateway, GatewayParameters
import react_agent as RA   # import-safe now (main() is __main__-guarded)

L4V = os.environ.get("L4V_DIR", "/sel4-project/verification/l4v")
THY = os.environ.get("THY", os.path.join(L4V, "proof/access-control/CNode_AC.thy"))
LEMMA = os.environ.get("LEMMA", "empty_slot_pas_refined")
SESSION = os.environ.get("SESSION", "Access")
PORT = int(os.environ.get("PORT", "25600")); SEP = RA.SEP
TARGET_SUBSTR = os.environ.get("TARGET_SUBSTR", "")
_TAG = os.environ.get("RESULT_TAG", "")  # unique output filename suffix for parallel runs
import base64 as _b64
_tb=os.environ.get("TARGET_SUBSTR_B64","")
if _tb: TARGET_SUBSTR=_b64.b64decode(_tb).decode("utf-8")   # exact per-line pin (avoids env quote issues)
MAX_STEPS = int(os.environ.get("MAX_STEPS", "20"))   # total budget (claude calls/lemma)
STUCK_CAP = int(os.environ.get("STUCK_CAP", "12"))   # consecutive error-retries before giving up (10-20)
SHOW_STATE = os.environ.get("SHOW_STATE", "1") == "1"  # 0 = blind/one-shot "stateless" arm, SAME reach-B verify
CLAUDE_TIMEOUT = float(os.environ.get("CLAUDE_TIMEOUT", "600"))
RUNS = RA.RUNS
BRIDGE = RA.BRIDGE   # shared dir; proposer_host.py (HOST) services mode=genstat
AUTO = re.compile(r"\b(auto|force|fastforce|blast|clarsimp|safe)\b")
_seq = [0]


def claude(prompt):
    """Get a static apply-script from claude via the HOST bridge (the container has no
    `claude` binary). Writes req-*.json (mode=genstat), polls resp-*.json. REQUIRES
    proposer_host.py running on the host."""
    _seq[0] += 1; rid = f"G{PORT}_{os.getpid()}_{_seq[0]:04d}"   # PORT+PID -> unique across cases AND runs (no stale-resp replay)
    os.makedirs(BRIDGE, exist_ok=True)
    req = os.path.join(BRIDGE, f"req-{rid}.json"); resp = os.path.join(BRIDGE, f"resp-{rid}.json")
    json.dump({"mode": "genstat", "prompt": prompt}, open(req + ".tmp", "w")); os.replace(req + ".tmp", req)
    t0 = time.monotonic()
    while time.monotonic() - t0 < CLAUDE_TIMEOUT:
        if os.path.exists(resp):
            try: d = json.load(open(resp))
            except Exception: d = {}
            out = (d.get("script") or "").strip()
            csec = d.get("secs")   # proposer-side REAL claude -p duration (vs our bridge wait)
            _record(rid, prompt, out, round(time.monotonic() - t0, 1), csec, d.get("err", ""))
            return out, csec
        time.sleep(0.5)
    _record(rid, prompt, "", CLAUDE_TIMEOUT, None, "bridge-timeout")
    print(f"  [claude] bridge timeout ({CLAUDE_TIMEOUT:.0f}s) — is proposer_host running on host?", flush=True)
    return "", None


def _record(rid, prompt, response, bridge_secs, claude_secs, err=""):
    """Full claude -p interaction transcript for audit. bridge_secs = our round-trip
    wait; claude_secs = proposer-side real claude -p time; the gap is bridge/queue overhead."""
    try:
        rec = {"rid": rid, "lemma": LEMMA, "bridge_secs": bridge_secs, "claude_secs": claude_secs,
               "err": err, "prompt": prompt, "response": response}
        open(os.path.join(RUNS, f"genstat-transcript-{LEMMA}{_TAG}.jsonl"), "a").write(
            json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def parse_script(out):
    """Pull the apply-script (apply/by/unfolding/using lines)."""
    path = []
    for ln in out.splitlines():
        ln = ln.strip().strip("`")
        if ln.startswith("apply ") or ln == "apply -" or re.match(r"^(by|unfolding|using|done)\b", ln):
            if ln not in path or ln == "done":
                path.append(ln)
    return path


# canonicalise away schematic variable NAMES (?x42 -> ?) so two states that differ
# only by Isabelle's fresh-var numbering compare equal (react_agent does the same).
canon = lambda sig: tuple(re.sub(r"\?[A-Za-z0-9_'.]+", "?", s) for s in sig)


def norm_tac(t):
    """Normalise a tactic for repeat-detection: `unfold X` and `simp only: X` of the
    same fact are the same WORK, so churn that swaps one for the other is still churn."""
    t = re.sub(r"\(\s*(?:unfold|simp only:)\s+", "(DEF ", t)
    return re.sub(r"\s+", " ", t).strip()

FEWSHOT = r"""Worked examples (current goal -> next static step(s)):
- goal `\<forall>x\<in>S'. Q x` with hyps `\<forall>x\<in>S. P x`, `S'\<subseteq>S`, `\<And>x. P x\<Longrightarrow>Q x`
    -> apply (rule ballI)   (then later: apply (drule (1) subsetD)  apply (drule (1) bspec)  apply assumption)
- goal `A \<and> B \<and> C` whose conjuncts are all hypotheses -> apply (intro conjI; assumption)
- goal `\<not> is_transferable_cap (UntypedCap ...)` (impossible constructor)
    -> apply (rule notI)   (then: apply (erule is_transferable.cases)  apply (simp only: cap.distinct option.distinct)+)
- goal `x = f x'` where `x = f x'` is a hypothesis -> apply assumption   (NOT rule refl)"""

STEP_PROMPT = r"""You are statically reconstructing ONE search tactic of an Isabelle proof, STEP BY STEP.
Each turn: look at the CURRENT goal and propose the NEXT 1-3 static apply-lines that move it
toward the TARGET. You will see the resulting goal next turn (or the error if it failed).

ALLOWED: unfold, simp only:<named>, rule/erule/drule/frule/intro/elim <named>, rule_tac,
assumption, hypsubst, rule conjI/impI/allI/exI/notI/refl/iffI/ballI, drule (1) bspec,
drule (1) subsetD, erule (1) ballE, case_tac. Combinators `;` and `+` allowed.
FORBIDDEN: auto, bare simp, simp add:, simp_all, blast, fastforce, force, clarsimp, metis, smt, meson.
Map the original tactic's hints: simp:/_def -> unfold or simp only:; dest: -> drule (add (1) if it
has a side premise); elim: -> erule; intro: -> rule.
- If TARGET is "close the goal", drive to 0 subgoals (finish with the closing step / `done`).
- If TARGET lists subgoals, stop once you've reached EXACTLY them — do NOT over-close.
- A hypothesis equality `x = f x'` closes by `apply assumption`, never `rule refl`.
- USE ONLY names in the goal, the original tactic's hints, or standard library.
{fewshot}
{lessons}
{hints}
Original tactic being replaced: {orig}
TARGET (state B): {target}
Steps applied so far (already verified in the prover): {applied}

CURRENT GOAL:
{goal}
{feedback}
OUTPUT ONLY the next 1-3 apply-lines, one per line. No prose."""

ONESHOT_PROMPT = r"""Rewrite a SEARCH-BASED Isabelle tactic into a DETERMINISTIC static apply-script
proving the SAME lemma to the SAME state the original tactic reaches. You do NOT get the
intermediate proof goal — reason from the lemma statement and the original tactic alone.

ALLOWED: unfold, simp only:<named>, rule/erule/drule/frule/intro/elim <named>, rule_tac,
assumption, hypsubst, rule conjI/impI/allI/exI/notI/refl/iffI/ballI, drule (1) bspec,
drule (1) subsetD, erule (1) ballE, case_tac. Combinators `;` and `+` allowed.
FORBIDDEN: auto, bare simp, simp add:, simp_all, blast, fastforce, force, clarsimp, metis, smt, meson.
Map the original tactic's hints: simp:/_def -> unfold or simp only:; dest: -> drule (add (1) if it
has a side premise); elim: -> erule; intro: -> rule.
- If TARGET is "close the goal", finish all goals (end with `done`).
- If TARGET lists subgoals, leave EXACTLY those — do NOT over-close.
{fewshot}
{lessons}
{hints}
Lemma: {stmt}
Original tactic being replaced: {orig}
TARGET (state B): {target}
{feedback}
OUTPUT ONLY the full apply-script (apply-lines, then `done` if closing). No prose."""

LESSONS_FILE = os.path.join(RUNS, "genstat_lessons.jsonl")


def load_lessons(n=8):
    """Compact cross-lemma memory: prior winning step-patterns + failure notes."""
    if not os.path.exists(LESSONS_FILE):
        return ""
    out = []
    try:
        rows = [json.loads(l) for l in open(LESSONS_FILE) if l.strip()][-n:]
    except Exception:
        return ""
    for r in rows:
        if r.get("verdict") == "REACHED-B" and r.get("path"):
            out.append(f"  [WORKED] {r.get('btype','')}: {' '.join(r['path'])[:160]}")
        elif r.get("fail"):
            out.append(f"  [FAILED] {r.get('btype','')}: {r['fail'][:120]}")
    return ("Lessons from earlier lemmas (learn from these):\n" + "\n".join(out) + "\n") if out else ""


def save_lesson(rec, applied, last_fail, btype):
    try:
        row = {"lemma": rec["lemma"], "btype": btype, "verdict": rec["verdict"],
               "path": applied if rec["verdict"] == "REACHED-B" else None,
               "fail": None if rec["verdict"] == "REACHED-B" else (last_fail or "")[:160]}
        open(LESSONS_FILE, "a").write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def state_sig(isa, cp):
    """Numbered-goal signature at checkpoint cp (probe with no-op `apply -`)."""
    pn = cp + "p"
    isa._focus_tls(cp); isa._clone_tls(pn); isa._focus_tls(pn)
    ok, m = RA.sr(isa._step_without_timeout("apply -"))
    isa._focus_tls(cp)
    return RA.signature(m) if ok else ()


def clean_goal(isa, cp):
    isa._focus_tls(cp); _, g = RA.sr(isa._extract_goal()); return g


def apply_chunk(isa, base_cp, tacs, tag):
    """Apply tacs from a fresh clone of base_cp. Return (ok, post_cp, post_sig, fail_diag).
    A named checkpoint snapshots the state AT CLONE TIME, so re-focusing the work checkpoint
    later reloads the PRE-chunk goal. We therefore clone a SECOND checkpoint AFTER stepping,
    capturing the live POST-chunk state, and return that — so clean_goal(post_cp) shows the
    real residual goal (not stale A). post_sig still comes from the last step's return msg."""
    work = f"S{tag}"; isa._focus_tls(base_cp); isa._clone_tls(work); isa._focus_tls(work)
    last_m = ""
    for tac in tacs:
        ok, m = RA.sr(isa._step_without_timeout(tac))
        if ok is False or "*** " in (m or ""):
            return False, None, None, f"`{tac}` failed: {(m or '').strip()[:280]}"
        last_m = m
    post = f"S{tag}d"; isa._clone_tls(post)   # snapshot the live POST-chunk state
    return True, post, RA.signature(last_m), ""


def main():
    print(f"[genstat-stateful] {LEMMA} @ {THY.split('l4v/')[-1]}", flush=True)
    env = os.environ.copy(); env["ISABELLE_HOME"] = RA.ISABELLE_HOME   # JVM needs this to start
    proc = subprocess.Popen([RA._java(), "-Xmx8g", "-jar", RA.JAR, str(PORT)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, text=True)
    isa = None; A_cp = None; orig_ms = None
    rec = {"lemma": LEMMA, "thy": THY, "session": SESSION, "mode": "genstat-stateful",
           "verdict": "NO-PATH", "rounds": []}
    try:
        # poll the gateway until the JVM binds (cold/loaded host can need >12s)
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

        # ---- cost-aware locate (same as react_agent) ----
        hint = TARGET_SUBSTR
        if not hint:
            cl = [(i, s) for i, s in enumerate(proof) if RA.ft(s) in ("apply", "by", "subgoal") and AUTO.search(s)]
            tmap = RA.db_classical_elapsed(THY) if len(cl) > 1 else {}
            if tmap:
                _el = lambda s: max((el for k, el in tmap.items() if k and k in RA._norm(s)), default=0.0)
                sc = sorted(((_el(s), i, s) for i, s in cl), key=lambda x: -x[0])
                if sc and sc[0][0] > 0:
                    hint = sc[0][2]
                    print(f"[cost-target] hottest of {len(cl)} classical lines: step{sc[0][1]} = {sc[0][0]:.1f}s DB", flush=True)
        u = ""; target = None
        for i, s in enumerate(proof):
            cp = f"N{i}"; isa._clone_tls(cp)
            # `subgoal by (<auto>)` closes the first goal -> reach-B target = outer (N-1)-goal state
            is_t = RA.ft(s) in ("apply", "by", "subgoal") and AUTO.search(s)
            if hint and is_t and not RA._hint_in(hint, s): is_t = False
            if is_t:
                # `subgoal by (m)` = bare `subgoal`(focus) + `by (m)`. Target the pair as a UNIT
                # from OUTER A (before `subgoal`) so reach-B = outer (N-1)-goal state (see react_agent).
                pre = cp; st = s
                if i > 0 and RA.ft(proof[i - 1]) == "subgoal":
                    pre = f"N{i - 1}"; st = proof[i - 1] + "\n" + s
                # strip the "True<SEP>" REPL ok-flag/protocol prefix — passing the RAW
                # response made claude read the goal as literal `True` and hallucinate
                # `apply TrueI`. Hand the LLM the CLEAN goal term only.
                isa._focus_tls(pre); _, A_goal = RA.sr(isa._extract_goal())
                isa._focus_tls(pre); _t0 = time.monotonic(); _, mb = RA.sr(isa._step_without_timeout(st)); _om = (time.monotonic() - _t0) * 1000; B_sig = RA.signature(mb)
                target = (i, st, pre, A_goal, B_sig, _om); break
            else:
                r = isa._step_without_timeout(u + s); u = u + s + "\n" if "False" + SEP in r else ""
        if not target:
            print("[no target classical line]", flush=True); rec["verdict"] = "NO-TARGET"; return
        i, orig, A_cp, A_goal, B_sig, orig_ms = target
        facts = []
        try: facts = sorted(set(RA.parse_hammer_facts(isa._prove_by_hammer(), orig)))
        except Exception: pass
        target_desc = ("close the goal (0 subgoals)" if not B_sig
                       else f"reach EXACTLY this {len(B_sig)}-subgoal state:\n" + "\n".join(B_sig)[:600])
        rec.update(target=orig.strip(), B_sig=list(B_sig), A_goal=A_goal[:600], facts=facts)
        print(f"== target: {orig.strip()[:70]}\n   A_goal: {A_goal[:120]}\n   B: {target_desc[:80]}", flush=True)

        hints = (f"Hint lemmas available: {', '.join(facts[:12])}\n" if facts else "")
        lessons = load_lessons()
        btype = "close" if not B_sig else f"{len(B_sig)}sub"

        # ---- BLIND / ONE-SHOT "stateless" arm (SHOW_STATE=0): the LLM sees only the
        #      lemma statement (NOT the live goal A), generates the WHOLE script, and is
        #      verified by reach-B in the REPL — the SAME criterion as DFS / the stateful
        #      arm. This makes stateful-vs-stateless a clean A/B on "does seeing the live
        #      state help", with IDENTICAL verification (改写成功且改写前后状态一致).
        if not SHOW_STATE:
            stmt = re.sub(r"\s+", " ", steps[idx]).strip()
            feedback = ""; empties = 0; last_fail = ""
            for rnd in range(STUCK_CAP):
                prompt = ONESHOT_PROMPT.format(fewshot=FEWSHOT, lessons=lessons, hints=hints,
                                               stmt=stmt[:700], orig=orig.strip(),
                                               target=target_desc, feedback=feedback)
                t0 = time.time(); raw, csec = claude(prompt); gen_secs = round(time.time() - t0, 1)
                TM = {"gen_secs": gen_secs, "claude_secs": csec,
                      "bridge_wait": round(gen_secs - (csec or 0), 1), "verify_secs": 0}
                script = parse_script(raw)
                print(f"  round {rnd}: gen={gen_secs}s (claude {csec}s) -> {script}", flush=True)
                if not script:
                    empties += 1; rec["rounds"].append({"round": rnd, "script": [], "outcome": "NO-OUTPUT", **TM})
                    if empties >= 3: rec["verdict"] = "CHANNEL-FAIL"; break
                    continue
                empties = 0
                tv = time.time()
                ok, ncp, sig, diag = apply_chunk(isa, A_cp, script, rnd)
                TM["verify_secs"] = round(time.time() - tv, 1)
                if ok and canon(sig) == canon(B_sig):
                    rec["verdict"] = "REACHED-B"; rec["path"] = script
                    rec["rounds"].append({"round": rnd, "script": script, "outcome": "REACHED-B", **TM})
                    print(f"  round {rnd}: REACHED-B", flush=True); break
                if not ok:
                    last_fail = diag
                    feedback = (f"\nYour script FAILED: {diag}\nRewrite the WHOLE script differently "
                                f"(you do NOT get the intermediate goal; reason from the lemma).\n")
                    outc = "BUILD-FAIL"
                else:
                    last_fail = f"wrong state ({len(sig)} subgoals)"
                    feedback = (f"\nYour script applied but reached the WRONG state ({len(sig)} subgoals); "
                                f"TARGET wants {btype}. Rewrite it.\n")
                    outc = "WRONG-STATE"
                rec["rounds"].append({"round": rnd, "script": script, "outcome": outc,
                                      "diag": (diag or last_fail)[:300], **TM})
                print(f"  round {rnd}: {outc} {(diag or last_fail)[:80]}", flush=True)
            if rec["verdict"] not in ("REACHED-B", "CHANNEL-FAIL"):
                rec["verdict"] = "NO-PATH"
            save_lesson(rec, rec.get("path", []), last_fail, btype)
            return

        # ---- STEP-BY-STEP: claude proposes the next chunk from the CURRENT goal; the
        #      REPL applies it and the new goal (or error) feeds the next turn. One bad
        #      tactic no longer wastes a whole attempt; the LLM sees real intermediate
        #      states. (Costs up to MAX_STEPS claude calls per lemma.)
        cur_cp = A_cp; cur_sig = state_sig(isa, A_cp)   # fresh-A probe works (react does the same)
        applied = []; feedback = ""; empties = 0; stuck = 0; last_fail = ""; tried = set()
        visited = {canon(cur_sig)}; applied_norm = set()   # anti-churn: seen states + done work
        CLOSE_HINT = ("If a hypothesis is an EQUALITY (x = ...), rewrite with `apply (erule subst)` "
                      "or `apply hypsubst` then close with `apply assumption`.")
        for step in range(MAX_STEPS):
            if canon(cur_sig) == canon(B_sig):
                rec["verdict"] = "REACHED-B"; rec["path"] = applied; break
            goal = clean_goal(isa, cur_cp)
            prompt = STEP_PROMPT.format(fewshot=FEWSHOT, lessons=lessons, hints=hints,
                                        orig=orig.strip(), target=target_desc,
                                        applied=(" ".join(applied) or "(none yet)"),
                                        goal=goal[:1200], feedback=feedback)
            t0 = time.time(); raw, csec = claude(prompt); gen_secs = round(time.time() - t0, 1)
            # split timing: gen_secs = our bridge round-trip; claude_secs = proposer-side real
            # claude -p; bridge_wait = gap (process spawn + queue); verify_secs = REPL apply time.
            TM = {"gen_secs": gen_secs, "claude_secs": csec,
                  "bridge_wait": round(gen_secs - (csec or 0), 1), "verify_secs": 0}
            nexts = parse_script(raw)
            print(f"  step {step}: gen={gen_secs}s (claude {csec}s) -> {nexts}", flush=True)
            if not nexts:
                empties += 1
                rec["rounds"].append({"step": step, "tacs": [], "outcome": "NO-OUTPUT", **TM})
                if empties >= 3: rec["verdict"] = "CHANNEL-FAIL"; break
                continue
            empties = 0
            # anti-churn pre-check: the WHOLE proposed chunk is work already done (re-proposing
            # the opening, swapping unfold<->simp only) -> reject before applying, steer to closing.
            if applied_norm and all(norm_tac(t) in applied_norm for t in nexts):
                last_fail = "re-proposed already-applied work"; stuck += 1; tried.add(" ".join(nexts))
                feedback = (f"\nYou re-proposed {nexts} — that work is ALREADY DONE. Do NOT repeat the opening; "
                            f"propose the NEXT step toward closing. {CLOSE_HINT}\n")
                rec["rounds"].append({"step": step, "tacs": nexts, "outcome": "REPEAT-CHUNK", **TM})
                print(f"  step {step}: REPEAT-CHUNK (work already done)", flush=True)
                if stuck >= STUCK_CAP: break
                continue
            tv = time.time()
            ok, ncp, new_sig, diag = apply_chunk(isa, cur_cp, nexts, step)
            TM["verify_secs"] = round(time.time() - tv, 1)
            if not ok:
                last_fail = diag; stuck += 1; tried.add(" ".join(nexts))
                feedback = (f"\nThat step FAILED: {diag}\nThe CURRENT goal above is unchanged; "
                            f"try DIFFERENT tactics (do not repeat: {sorted(tried)[:6]}).\n")
                rec["rounds"].append({"step": step, "tacs": nexts, "outcome": "FAILED", "diag": diag[:300], **TM})
                print(f"  step {step}: FAILED {diag[:80]}", flush=True)
                if stuck >= STUCK_CAP: break
                continue
            if not new_sig and B_sig:                       # over-closed
                last_fail = "over-closed"; stuck += 1; tried.add(" ".join(nexts))
                feedback = f"\nThat step CLOSED all goals, but TARGET wants {len(B_sig)} subgoal(s) left. Do less.\n"
                rec["rounds"].append({"step": step, "tacs": nexts, "outcome": "OVER-CLOSED", **TM})
                if stuck >= STUCK_CAP: break
                continue
            if canon(new_sig) == canon(cur_sig) or canon(new_sig) in visited:   # no progress / churn revisit
                why = "did NOT change the goal" if canon(new_sig) == canon(cur_sig) else "led back to a state already seen (churn)"
                last_fail = "churn/no-progress"; stuck += 1; tried.add(" ".join(nexts))
                feedback = (f"\nThat step {why}. The opening is done; find the NEXT step toward closing. "
                            f"{CLOSE_HINT} Do NOT repeat: {sorted(tried)[:6]}.\n")
                rec["rounds"].append({"step": step, "tacs": nexts, "outcome": "CHURN", **TM})
                if stuck >= STUCK_CAP: break
                continue
            cur_cp = ncp; cur_sig = new_sig; applied += nexts; stuck = 0; feedback = ""   # advance
            visited.add(canon(new_sig)); applied_norm.update(norm_tac(t) for t in nexts)
            rec["rounds"].append({"step": step, "tacs": nexts, "outcome": "OK", "subgoals": len(new_sig), **TM})
            print(f"  step {step}: OK -> {len(new_sig)} subgoal(s); applied={applied}", flush=True)
        if rec["verdict"] not in ("REACHED-B", "CHANNEL-FAIL"):
            rec["verdict"] = "REACHED-B" if canon(cur_sig) == canon(B_sig) else "NO-PATH"
            if rec["verdict"] == "REACHED-B": rec["path"] = applied
        save_lesson(rec, applied, last_fail, btype)
    except Exception as e:
        rec["verdict"] = "ERROR"; rec["error"] = str(e)[:200]; print("[error]", e, flush=True)
    finally:
        # speed: time the original tactic (captured at B_sig) vs the static PATH from A
        if rec.get("verdict") == "REACHED-B" and A_cp is not None and rec.get("path") and orig_ms:
            try:
                static_ms, ipc_ms = RA.time_static_path(isa, A_cp, rec["path"])
                rec.update(orig_ms=round(orig_ms, 1), static_ms=static_ms, ipc_ms=ipc_ms,
                           saved_ms=round(orig_ms - static_ms, 1),
                           frac_saved=(round((orig_ms - static_ms) / orig_ms, 3) if orig_ms > 0.5 else None),
                           accelerated=bool(orig_ms > 0.5 and static_ms < orig_ms * 0.9))
                print(f"[speed] orig={orig_ms:.0f}ms static={static_ms:.0f}ms saved={orig_ms-static_ms:.0f}ms accel={rec['accelerated']}", flush=True)
            except Exception as e:
                rec["time_err"] = str(e)[:100]
        out = os.path.join(RUNS, f"genstat-stateful-{LEMMA}{_TAG}.json")
        json.dump(rec, open(out, "w"), indent=1, ensure_ascii=False)
        print(f"[saved] {out}  verdict={rec['verdict']}", flush=True)
        try: isa._exit()
        except Exception: pass
        proc.terminate(); proc.wait()


if __name__ == "__main__":
    main()
