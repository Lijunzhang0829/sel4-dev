"""Host-side LLM proposer service (rides Claude Max via the bundled CLI).

Watches a shared bridge dir for req-*.json (written by the in-container agent),
calls `claude -p --model haiku` on the HOST (uses ~/.claude Max OAuth — no API
key, no creds copied into the container), writes resp-*.json with proposed
static tactics. Credentials never leave the host; only goal text + tactics cross
the /workspace bind mount.

Run on the host (background):  python3 proposer_host.py
Stop:  touch <bridge>/STOP
"""
import os, glob, json, subprocess, time, re

BRIDGE="/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/bridge"
def _find_claude():
    """Locate the claude CLI robustly — the vscode-server extension version bumps
    (2.1.160 -> 2.1.170 -> ...) and breaks any hard-coded path, which silently makes
    every proposer call return empty in 0.0s. Glob for the newest installed version."""
    env=os.environ.get("CLAUDE_BIN")
    if env and os.path.exists(env): return env
    cands=sorted(glob.glob("/home/lijun/.vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude"))
    return cands[-1] if cands else (env or "claude")
CLAUDE=_find_claude()
MODEL=os.environ.get("PROPOSER_MODEL","sonnet")

# Transport-level drops between the claude CLI and the API ("The socket connection was
# closed unexpectedly", transient rate/overload) are NOT quota errors — a plain retry
# usually succeeds. Without one, a single drop wastes a whole react step (empty stdout ->
# no proposal -> fallback). Retry with exponential backoff on empty/transient output.
ATTEMPTS=int(os.environ.get("CLAUDE_RETRIES","3"))
PER_TIMEOUT=float(os.environ.get("CLAUDE_TIMEOUT","180"))   # per-attempt; cold calls spike ~120-160s
# Budget the WHOLE call (incl retries) to stay inside react_agent's resp-wait window
# (llm_react waits min(300,time_left)); overrunning it makes react abandon the request
# and fall back to bad menu picks -> spurious NO-PATH. Keep total < that window.
BUDGET=float(os.environ.get("CLAUDE_BUDGET","290"))
_TRANSIENT=re.compile(r"socket connection.*closed|closed unexpectedly|rate limit|temporarily|overloaded|"
                      r"ECONNRESET|ETIMEDOUT|EOF|connection reset|connection error|service unavailable|"
                      r"\b50[0-9]\b|too many requests|"
                      # the claude CLI's own mid-stream drop message, printed to stdout -> reconnect
                      r"connection closed|mid-response|response above may be incomplete|api error", re.I)
def _claude_stdout(prompt):
    """Call `claude -p` (MCP disabled, 25x faster). Retry ONLY fast transport drops
    (socket-closed / rate / 5xx) — NOT a full timeout, which is cold-start slowness and
    retrying it just overruns react's wait window. Budget-aware. Returns stdout or ""."""
    t0=time.monotonic(); last=""
    for attempt in range(ATTEMPTS):
        remaining=BUDGET-(time.monotonic()-t0)
        if remaining < 30: break                       # no time for a meaningful attempt
        try:
            p=subprocess.run([CLAUDE,"-p",prompt,"--model",MODEL,
                              "--strict-mcp-config","--mcp-config",'{"mcpServers":{}}',"--max-turns","1"],
                             capture_output=True,text=True,timeout=min(PER_TIMEOUT,remaining),cwd="/tmp")
            out=(p.stdout or "").strip(); err=(p.stderr or "")
            if out and not _TRANSIENT.search(out):
                return out                              # success
            last=(out or err or f"rc={p.returncode}")[:160]
            if not _TRANSIENT.search(last): break       # non-transient empty/err: don't burn retries
        except subprocess.TimeoutExpired:
            last="timeout"; break                       # slow, not a drop -> do NOT retry
        except Exception as e:
            last=str(e)[:160]
        if attempt < ATTEMPTS-1 and BUDGET-(time.monotonic()-t0) > 30:
            wait=min(2*(2**attempt), 8)                 # 2s, 4s — drops fail fast, keep backoff short
            print(f"[proposer] transient drop (try {attempt+1}/{ATTEMPTS}): {last!r} -> retry in {wait}s",flush=True)
            time.sleep(wait)
        else: break
    if last: print(f"[proposer] claude no usable output: {last!r}",flush=True)
    return ""

PROMPT="""You are proposing the next Isabelle/HOL apply-style tactic(s) to make progress on ONE proof subgoal, using ONLY deterministic STATIC tactics (no search/automation).

ALLOWED building blocks: rule/erule/drule/frule/intro/elim <named lemma>, subst/unfold <named>, hypsubst, assumption, (erule conjE), (erule notE, assumption), rule conjI/notI/impI/exI/allI, and `simp only: <explicitly named rules>`.
FORBIDDEN — never propose: auto, bare simp, simp add:, simp_all, blast, fastforce, force, clarsimp, metis, smt, meson, presburger, arith, linarith.

Hints:
- The original was `blast/auto/force/fastforce`, often with `intro: / dest: / elim: X` hints — those name the rules it chained. Replace by applying them directly: `rule X` (intro), `erule X` (elim), `drule X` (dest), then `assumption` / `rule refl`. This is usually the whole proof.
- An iff/equality fact F (`F: A = B`) used with drule/frule needs `F[THEN iffD1]` (or iffD2).
- A local equality hypothesis (e.g. `y = slot`) is consumed with `hypsubst`.
- Two contradictory hyps (`x = a` and `x \\<noteq> a`) close with `(erule notE, assumption)`.
- A conjunction goal `P \\<and> Q` → `rule conjI`; a `\\<noteq>`/`\\<not>` goal → `rule notI`; `\\<exists>` → `rule exI`; `\\<forall>`/`\\<And>` → `rule allI`; `P \\<longrightarrow> Q` → `rule impI`.
- For a set/logic goal you may also `unfold <def>` then apply the rules. `simp only: <named rules>` is allowed (name the full rule set); bare `simp`/`auto` are NOT.

Candidate lemma facts available: {facts}

Current subgoal (assumptions in \\<lbrakk>...\\<rbrakk>, conclusion after \\<Longrightarrow>):
{goal}

Propose the 1-3 most promising next tactics, BEST FIRST. Output ONLY tactic lines, each EXACTLY of the form:
apply (rule foo)
No prose, no markdown, no numbering."""

REACT_PROMPT="""You are proving ONE Isabelle/HOL goal by an interactive ReAct loop: at each step you see the current goal, the available facts, the target, and the HISTORY of tactics you already tried with their observations (including failures). Reason briefly, then output the single best next apply-tactic.

Use ONLY deterministic STATIC tactics. ALLOWED: rule/erule/drule/frule <named>, intro/elim <named>, subst/unfold <named>, hypsubst, assumption, (erule conjE), (erule notE, assumption), rule conjI/notI/impI/exI/allI, simp only: <explicitly named rules>.
FORBIDDEN: auto, bare simp, simp add:, simp_all, blast, fastforce, force, clarsimp, metis, smt, meson, presburger, arith, linarith.

LEARN FROM FAILURES in the history:
- "erule X" failed and the premise is a conjunction `A \\<and> B` -> first split it: (erule conjE).
- A def-like fact `foo_def` that failed as `rule`/`erule` is a rewrite rule -> use `unfold foo_def` or `simp only: foo_def`.
- An iff/eq fact F used with drule/frule needs `F[THEN iffD1]` (or iffD2).
- Goal `P \\<and> Q` -> rule conjI; `\\<noteq>`/`\\<not>` -> rule notI; `\\<exists>` -> rule exI; `\\<forall>`/`\\<And>` -> rule allI; `P \\<longrightarrow> Q` -> rule impI.
- After unfolding a def you usually need: split the goal conjunction (rule conjI), split a conjunctive assumption (erule conjE), then apply the elim/intro rules and `assumption`.
- If a tactic already FAILED on this same goal in the history, do NOT repeat it — try something different.

Facts available: {facts}
Target: {target}

Current goal:
{goal}

History (most recent last):
{history}

Output EXACTLY:
THOUGHT: <one short line of reasoning about the next move given the observations>
apply (<tactic>)
You may add up to 2 more `apply (...)` alternatives on following lines, best first. No other prose, no markdown."""

SELECT_PROMPT="""You are proving an Isabelle/HOL goal step by step. You are given the current goal, the history of tactics already tried with their observations, and a MENU of candidate next tactics (all audit-safe — derived from the goal structure and sledgehammer's relevant facts).

Pick the 1-3 candidates FROM THE MENU most likely to make progress. Reason from the goal and ESPECIALLY the failure history:
- Don't repeat a candidate that already FAILED on this same goal in the history.
- If "erule X" failed because the premise is a conjunction A \\<and> B -> pick (erule conjE) first.
- A def fact `foo_def` that failed as rule/erule is a rewrite -> pick `unfold foo_def` / `simp only: foo_def`.
- Goal `P \\<and> Q` -> rule conjI; `\\<exists>` -> rule exI; `\\<forall>`/`\\<And>` -> rule allI; `P \\<longrightarrow> Q` -> rule impI; a boolean equality `A = B` -> rule iffI.
- After unfolding/splitting, use the elim/intro facts then `assumption`.
- ORDER MATTERS. If the goal's conclusion head is a DEFINED predicate and a `_def`
  REWRITE hint names it, your FIRST pick MUST be `apply (unfold <that>_def)` (or
  `simp only:`) to expose its structure — do NOT apply conjI/rule/erule before the
  predicate is unfolded, they will fail on the still-folded predicate.
{roles}
You MUST copy chosen lines VERBATIM from the MENU — do not invent tactics not in the menu.

Target B (the EXACT proof state you must reach — match it, do not over- or under-prove):
{target}

Subgoal-count delta: {delta}
- If target has MORE subgoals than now, the next move is a SPLIT/intro that grows goals
  (e.g. `rule conjI` turns a `P ∧ Q` goal into 2). Pick that.
- If target has FEWER (or 0), the next move ELIMINATES/closes a goal (elim rule, assumption,
  rule refl). Do NOT pick a splitting intro that would overshoot the target count.

Current goal A:
{goal}

History (most recent last):
{history}

MENU (choose from these only):
{menu}

Output EXACTLY:
THOUGHT: <one short line of reasoning>
<a line copied verbatim from the MENU>
You may add up to 2 more menu lines, best first. No other prose."""

_FACT=re.compile(r"[A-Za-z_][A-Za-z0-9_'.]*")
_ROLE_BAD={"intro","dest","elim","simp","only","add","del","split","cong","by","apply",
           "auto","fastforce","force","blast","clarsimp","metis","fast","safe","rule","wp","OF","THEN"}
_ROLE_MAP={"dest":"a DEST rule -> pick `apply (drule {f})` or `apply (drule (1) {f})` or `apply (frule {f})` (use `drule`, NOT `erule`/`rule`)",
           "elim":"an ELIM rule -> pick `apply (erule {f})` (use `erule`, NOT `drule`/`rule`)",
           "intro":"an INTRO rule -> pick `apply (rule {f})` (use `rule`, NOT `erule`/`drule`)",
           "simp":"a REWRITE/def rule -> pick `apply (unfold {f})` or `apply (simp only: {f})` (NOT rule/erule/drule)"}
def parse_roles(orig):
    """The original automation's dest:/elim:/intro:/simp: hints ARE each fact's role.
    Turn them into explicit guidance so the model picks the right menu position instead
    of guessing among rule/erule/drule/frule for each fact."""
    if not orig: return ""
    parts=re.split(r"\b(intro|dest|elim|simp(?:\s+only)?|del|cong|split|add)!?:", orig)
    lines=[]; i=1
    while i < len(parts)-1:
        marker=parts[i].split()[0]; group=parts[i+1]; i+=2
        if marker not in _ROLE_MAP: continue
        for f in _FACT.findall(group):
            if f in _ROLE_BAD or f[0].isdigit(): continue
            lines.append("  - `%s` is %s" % (f, _ROLE_MAP[marker].format(f=f)))
    if not lines: return ""
    return ("\nThe ORIGINAL automation that proved this goal used these hints — each names a\n"
            "fact's ROLE, so pick the MATCHING menu line (do not guess the position):\n"
            + "\n".join(lines) + "\n")

def call_claude_react(goal, facts, target, history, menu=None, orig="", delta=""):
    hist=""
    for h in history:
        mark="ok" if h.get("ok") else "FAIL"
        hist+=f"  [{mark}] {h.get('action')}  =>  {h.get('obs','')}\n"
    hist=hist or "  (none yet)"
    if menu:
        prompt=SELECT_PROMPT.format(target=target or "close the goal", goal=goal,
                                    history=hist, menu="\n".join(menu), roles=parse_roles(orig),
                                    delta=delta or "(unknown)")
    else:
        factstr=("\n  - "+"\n  - ".join(facts)) if facts else "(none beyond background)"
        prompt=REACT_PROMPT.format(facts=factstr,
                                   target=target or "close the goal", goal=goal, history=hist)
    out=_claude_stdout(prompt)
    if not out:
        return [], "", "claude transient failure (retries exhausted)"
    tacs=[]; thought=""
    for ln in out.splitlines():
        ln=ln.strip().strip("`")
        if ln.upper().startswith("THOUGHT:"): thought=ln[8:].strip()
        elif ln.startswith("apply ") and ln not in tacs: tacs.append(ln)
    return tacs[:3], thought, out.strip()[:300]

def call_claude(goal, facts):
    prompt=PROMPT.format(facts=", ".join(facts) if facts else "(none beyond background)", goal=goal)
    # neutral cwd + MCP disabled (25x faster) + transient-drop retry, all in _claude_stdout
    out=_claude_stdout(prompt)
    if not out:
        return [], "claude transient failure (retries exhausted)"
    tacs=[]
    for ln in out.splitlines():
        ln=ln.strip().strip("`")
        if ln.startswith("apply ") and ln not in tacs:
            tacs.append(ln)
    return tacs[:4], out.strip()[:200]

def main():
    os.makedirs(BRIDGE,exist_ok=True)
    print(f"[proposer] watching {BRIDGE} (model={MODEL})",flush=True)
    seen=set()
    while True:
        if os.path.exists(os.path.join(BRIDGE,"STOP")):
            print("[proposer] STOP",flush=True); break
        for req in sorted(glob.glob(os.path.join(BRIDGE,"req-*.json"))):
            rid=os.path.basename(req)[4:-5]
            resp=os.path.join(BRIDGE,f"resp-{rid}.json")
            if rid in seen or os.path.exists(resp): continue
            try: r=json.load(open(req))
            except Exception: continue
            t0=time.time()
            if r.get("mode")=="react":
                tacs,thought,raw=call_claude_react(r["goal"], r.get("facts",[]),
                                                   r.get("target",""), r.get("history",[]),
                                                   r.get("menu"), r.get("orig",""), r.get("delta",""))
                out={"tactics":tacs,"thought":thought,"raw":raw,"secs":round(time.time()-t0,1)}
            else:
                tacs,raw=call_claude(r["goal"], r.get("facts",[]))
                out={"tactics":tacs,"raw":raw,"secs":round(time.time()-t0,1)}
            json.dump(out, open(resp+".tmp","w")); os.replace(resp+".tmp",resp)
            seen.add(rid)
            print(f"[proposer] {rid}: {len(tacs)} tactics in {time.time()-t0:.1f}s -> {tacs}",flush=True)
        time.sleep(0.4)

main()
