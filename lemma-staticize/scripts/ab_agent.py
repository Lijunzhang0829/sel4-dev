"""A->B reconstruction agent (v1.5) on Isa-REPL.

Replaces sledgehammer->metis/meson: hammer supplies CANDIDATE FACTS only; this
agent searches a DETERMINISTIC, audit-passing tactic path between the state
before an automation tactic (A) and the state it produces (B), using only
allowed static tactics. clone_tls/focus_tls = cheap backtracking.

v1.5 adds, over v0/v1:
- per-node lemma DISCOVERY via _find_theorems (intro/elim/dest [+solves]) so the
  agent finds the lemmas it needs for split subgoals (e.g. `y != slot`);
- node + wall BUDGET so unsolvable goals fail fast instead of exploding;
- durable step-by-step LOGGING (each found path replayed, every intermediate
  state recorded) -> runs/ab-<LEMMA>.{json,md} for full-process analysis.

Run inside the sel4-l4v container. Env: THY, LEMMA, SESSION, PORT, DEPTH,
TARGETS (e.g. "6,10"), NODE_BUDGET, TIME_CAP, SOLVES (1 to enable find_theorems solves).
"""
import os, re, subprocess, time, json, glob
from py4j.java_gateway import JavaGateway, GatewayParameters

def _java():
    """Resolve the JVM robustly — a restarted container may have only
    isabelle/bin on PATH (no JDK), so a bare `java` fails with FileNotFoundError
    and the search dies before [init ok]. Glob Isabelle's bundled jdk."""
    for pat in (os.path.join(os.environ.get("ISABELLE_HOME","/workspace/verification/isabelle"),"contrib/jdk-*/x86_64-linux/bin/java"),
                "/root/.isabelle/contrib/jdk-*/x86_64-linux/bin/java"):
        for p in sorted(glob.glob(pat)):
            if os.path.exists(p): return p
    return "java"

ISABELLE_HOME="/workspace/verification/isabelle"
JAR="/workspace/tools/seL4-proof-search/Isa-Repl/target/IsaREPL.jar"
# Heap fingerprints were built with sources at $L4V_DIR (/sel4-project/... per
# docker-compose). Initializing the REPL with a DIFFERENT path (/workspace) makes
# Isabelle see "different" sources and trigger a full ASpec->...->Refine rebuild,
# which hangs init. Honour L4V_DIR so freshness checks pass against the heaps.
L4V=os.environ.get("L4V_DIR","/sel4-project/verification/l4v")
THY=os.environ.get("THY", os.path.join(L4V,"proof/refine/ARM/Finalise_R.thy"))
LEMMA=os.environ.get("LEMMA","n_tranclD")
SESSION=os.environ.get("SESSION","Refine")
PORT=int(os.environ.get("PORT","25570")); SEP="<\\SEP>"
DEPTH=int(os.environ.get("DEPTH","5"))
TARGETS_ENV=os.environ.get("TARGETS","")
# Focus search on ONE proof line: only the classical step whose whitespace-stripped
# text contains this substring becomes a search target (others just execute). Lets
# the loop hit only the DB-identified slow line instead of every classical line in
# the proof — so a slow-LLM search finishes within budget.
TARGET_SUBSTR=os.environ.get("TARGET_SUBSTR","")
_norm=lambda s: re.sub(r"\s+","",s)
NODE_BUDGET=int(os.environ.get("NODE_BUDGET","250"))
ATTEMPT_CAP=int(os.environ.get("ATTEMPT_CAP","10"))   # max tactic-tries per search line
# Divergence guard: B is the original automation's output (for `by (fastforce ...)`
# that CLOSES the goal, so B has 0 subgoals). A branch whose subgoal count exceeds
# len(B)+MAX_GROWTH is expanding away from B (e.g. an elim rule applied to its own
# output) — prune it so the budget explores convergent siblings instead.
MAX_GROWTH=int(os.environ.get("MAX_GROWTH","4"))
TIME_CAP=float(os.environ.get("TIME_CAP","240"))
SOLVES=os.environ.get("SOLVES","1")=="1"
RUNS=os.path.join(os.path.dirname(THY) if False else "/workspace/tools/seL4-proof-search/Isa-Repl/runs")

def sr(r):
    p=r.split(SEP,1); return (p[0]=="True",p[1]) if len(p)==2 else (None,r)
KW=["apply","supply","subgoal","using","unfolding","proof","qed","done","{","}","next","note","let","write","fix","assume","then","have","show","from","with","also","finally","moreover","ultimately","presume","define","consider","obtain","case","typ","term","prop","thm","print_statement","apply_end","defer","prefer","back","oops","hence","thus",".","..","and","include","including","is","interpret","by"]
def ft(s): return re.split(r"[ ()\n]+", s.strip())[0] if s.strip() else ""
def isc(c):s=c.strip();return s.startswith("(*") and s.endswith("*)")
def rbs(cmds):
    d=0;np=False;npd=-1;o=[];i=0;n=len(cmds)
    while i<n:
        if cmds[i].endswith("begin"):d+=1
        if cmds[i].strip()=="end":
            if d==npd and np:np=False
            d-=1
        if cmds[i].startswith("notepad"):np=not np;npd=d
        if not np:
            if ft(cmds[i]) in KW:
                while i<n and ft(cmds[i]) in KW:i+=1
                o.append("sorry")
            else:o.append(cmds[i]);i+=1
        else:o.append(cmds[i]);i+=1
    return o
def signature(msg):
    return tuple(re.sub(r"\s+"," ",l.strip()) for l in msg.split("\n") if re.match(r"\s*\d+\.",l))
_SCHEM_RE=re.compile(r"\?[A-Za-z][A-Za-z0-9_'.]*")
def canon_sig(sig):
    """Dedup key for the `seen` prune: rename schematic vars canonically by order of
    first appearance, so goal states that differ ONLY in Isabelle's fresh ?Var
    numbering (e.g. `?P13 ?t13` vs `?P17 ?t17`, produced by repeated elim/intro)
    collapse to ONE key. Without this the prune never fires and the search burns its
    whole budget descending a single non-converging spine instead of exploring
    siblings. Used ONLY for dedup — B-matching still compares the RAW signature, so
    this can only ADD pruning, never change what counts as reaching B."""
    text="".join(sig); mapping={}
    def repl(m):
        v=m.group(0)
        if v not in mapping: mapping[v]=f"?V{len(mapping)}"
        return mapping[v]
    return _SCHEM_RE.sub(repl, text)
def first_goal(msg):
    for l in msg.split("\n"):
        if re.match(r"\s*1\.",l): return re.sub(r"\s+"," ",l.strip())[:160]
    return ("No subgoals" if "No subgoals" in msg else msg.strip().split("\n")[0][:80])
def sig_list(sig, cap=2000):
    """Full goal signature as a list, each subgoal capped only at a generous 2000
    chars (flagged if hit) — kept for faithful per-attempt state capture."""
    return [(x[:cap]+"…TRUNC" if len(x)>cap else x) for x in sig]

FACT_RE=re.compile(r"[A-Za-z_][A-Za-z0-9_'.]*(?:\(\d+\))?")
def parse_hammer_facts(hammer_msg, orig_tac):
    facts=set()
    for m in re.finditer(r"\((?:meson|metis|smt[^ ]*|blast|fastforce|auto|force|fast)\s+([^()]*(?:\([^()]*\)[^()]*)*)\)", hammer_msg):
        for t in FACT_RE.findall(m.group(1)): facts.add(t)
    m=re.search(r"using\s+(.+?)\s+(?:apply|by|\.)", hammer_msg)
    if m:
        for t in FACT_RE.findall(m.group(1)): facts.add(t)
    # split on modifier markers so a marker never swallows the NEXT marker's name —
    # the old greedy `[^):]*` capture dropped elim!:/simp: fact groups (e.g. it lost
    # equiv_asids_trivial and states_equiv_for_def from an elim!:/simp: tactic).
    for grp in re.split(r"\b(?:intro|dest|elim|simp(?:\s+only)?|del|cong|split|add)!?:", orig_tac)[1:]:
        for t in FACT_RE.findall(grp): facts.add(t)
    bad={"intro","dest","elim","simp","only","add","del","split","cong","if_split_asm",
         "meson","metis","smt","blast","fastforce","auto","force","fast","apply","by","using"}
    return [f for f in facts if f not in bad and not f.isdigit()]

# Core logical intro/elim + set/eq primitives. Every proof term references these,
# but they are exactly what the agent's structural menu (build_menu / propose) already
# generates by the goal's connective — so as AP3 facts they are pure noise. Strip them
# so AP3 contributes ONLY DOMAIN lemmas (the simp/library rules an auto/simp used that
# the menu can't invent). A `by blast` proof legitimately yields only these -> AP3
# correctly contributes nothing for it; a `simp add:`/`auto` proof yields real domain
# lemmas that survive this filter.
LOGIC_PRIMS={
    "subst","ssubst","sym","refl","trans","arg_cong","cong","fun_cong","ext","meta_eq_to_obj_eq",
    "impI","impE","impCE","mp","rev_mp","rev_iffD1","rev_iffD2","contrapos_nn","contrapos_pp",
    "conjI","conjE","conjunct1","conjunct2","disjI1","disjI2","disjE","disjCI",
    "iffI","iffD1","iffD2","iffE","notI","notE","classical","ccontr","FalseE","TrueI","excluded_middle",
    "allI","allE","spec","exI","exE","ex1I","ex1E","the_equality","someI",
    "subsetI","subsetD","equalityI","equalityE","CollectI","CollectD","ballI","bspec","rev_bexI",
    "mp","imp_refl","eq_reflection","Eq_TrueI","Eq_FalseI",
}
def filter_deps(deps_str, sep="<\\SEP>"):
    """AP3: turn _extract_thm_deps output into DOMAIN candidate facts. Drops the
    Pure/HOL proof-infrastructure noise (protect*, arity_type*, eq_reflection, *.super,
    classrel*) and the core logic primitives (LOGIC_PRIMS) the structural menu already
    covers; keeps only real library/domain lemmas."""
    out=[]; seen=set()
    for d in (deps_str or "").split(sep):
        d=d.strip()
        if not d or d in seen: continue
        if d.startswith("Pure."): continue
        tail=d.split(".")[-1]
        if tail in LOGIC_PRIMS: continue
        if "arity_type" in d or "classrel" in d or "protect" in d.lower() or tail in ("super","simp_thms","TrueI"):
            continue
        seen.add(d); out.append(d)
    return out

# --- SWAP mode (idea 2): don't reconstruct a static path (intractable for long auto);
# swap the WHOLE method for a cheaper one that reaches the SAME state B. Keeps the
# original fact modifiers (simp:/dest:/intro:/elim:), substitutes only the leading
# method token. Invalid combos (e.g. `simp` with `dest:`) just fail the B_sig check
# and are dropped. Known wins: fastforce->clarsimp/force/auto, metis->meson.
SWAP_ALT={
    "auto":["clarsimp","force","fastforce","blast","simp"],
    "fastforce":["clarsimp","force","auto","blast","simp"],
    "force":["clarsimp","fastforce","auto","blast","simp"],
    "clarsimp":["simp","auto","force","fastforce"],
    "simp":["clarsimp","auto","force","fastforce"],
    "simp_all":["clarsimp","auto","simp"],
    "blast":["fast","fastforce","force","auto","meson","metis"],
    "fast":["blast","fastforce","force","auto","meson"],
    "metis":["meson","blast","fastforce","force"],
    "meson":["metis","blast","fastforce"],
    "clarify":["clarsimp","safe","auto"],
    "safe":["clarsimp","clarify","auto"],
}
SWAP_RE=re.compile(r"^(\s*(?:apply|by)\s*\(?\s*)([a-zA-Z_][a-zA-Z0-9_']*)(.*)$", re.S)
def swap_menu(line, facts=None):
    """Expanded candidate set for reaching the same state B by a (hopefully cheaper)
    whole tactic: (a) method substitution keeping the original modifiers; (b) hammer-
    fact-driven metis/meson/fastforce (what Sledgehammer would suggest as a fast
    closer); (c) pre-normalize-then-close combos that factor the expensive simp work
    out of the classical step. Invalid combos just fail the B_sig check downstream."""
    facts=facts or []
    m=SWAP_RE.match(line.strip())
    if not m: return []
    pre,meth,rest=m.group(1),m.group(2),m.group(3)
    kw="apply" if pre.strip().startswith("apply") else "by"
    cands=[pre+a+rest for a in SWAP_ALT.get(meth,[])]            # (a)
    if facts:                                                    # (b)
        fs=" ".join(facts[:12])
        cands += [f"{kw} (metis {fs})", f"{kw} (meson {fs})",
                  f"{kw} (fastforce simp: {fs})", f"{kw} (force simp: {fs})",
                  f"{kw} (auto simp: {fs})"]
    for combo in ["clarsimp; blast","simp; force","clarsimp; force","simp; fastforce",   # (c)
                  "safe; fastforce","clarsimp; metis","simp; blast","clarsimp; auto"]:
        cands.append(f"{kw} ({combo})")
    seen=set(); out=[]
    for c in cands:
        c=c.rstrip()
        if c==line.strip() or c in seen: continue
        seen.add(c); out.append(c)
    return out

LOCALE_RE=re.compile(r"^\s*(?:locale|context|sublocale|experiment)\s+([A-Za-z_][A-Za-z0-9_']*)")
def locale_names(prefix_steps):
    """Names of locales/contexts opened in the theory prefix. _extract_thm_deps uses
    Global_Theory.get_thms (global namespace) so a locale-local lemma `foo` is invisible
    under its bare name — it is registered as `Locale.foo`. We try these prefixes,
    innermost (latest-declared) first, mirroring the JAR's own get_dependent_theorems
    locale fallback (which is NOT exposed via the gateway)."""
    ns=[]
    for s in prefix_steps:
        m=LOCALE_RE.match(s)
        if m and m.group(1) not in ("begin",): ns.append(m.group(1))
    return list(dict.fromkeys(reversed(ns)))

NAME_RE=re.compile(r"^\s*([A-Za-z][A-Za-z0-9_'.]+)(?:\s*\(\d+\))?:", re.M)
def parse_thm_names(out, cap=10):
    names=[]
    for m in NAME_RE.finditer(out or ""):
        n=m.group(1)
        if n not in ("found","theorem","theorems") and n not in names:
            names.append(n)
        if len(names)>=cap: break
    return names

# v2: goal-structure-driven planner. Instead of a fact × tactic cross-product
# (which floods the search with spurious-progress `drule conjI`-style junk), we
# read the FIRST subgoal's conclusion and propose a small, ORDERED candidate set:
# structural rule for the top connective first, then assumption, then class-
# specific fact moves, with frule/drule (facts only) LAST.
TRANCL_FAMILY=["r_into_trancl","trancl_into_trancl","trancl_into_trancl2","r_r_into_trancl",
               "transitive_closure_trans(1)","trancl_trans","trancl.intros"]
# small structural library always available to `rule` (e.g. line 4 needs domI)
BACKGROUND=["domI","conjI","exI","refl","r_into_trancl","trancl_into_trancl",
            "trancl_into_trancl2","r_r_into_trancl"]
def concl_of(sg):
    s=sg
    if "\\<Longrightarrow>" in s: s=s.split("\\<Longrightarrow>")[-1]
    return s.strip()
def propose(sg, facts):
    """Goal-classifier + fact search → ordered, audit-safe candidate tactics.
    Recipe components verified on n_tranclD line 6 (conjunction + contradiction):
    iff facts need [THEN iffD1]; local equalities need hypsubst; conjunction
    hyps need erule conjE; contradictory hyps close with the COMBINED backtracking
    method `(erule notE, assumption)` (a separate `erule notE` commits to the wrong ¬)."""
    concl=concl_of(sg); c=[]
    pool=list(dict.fromkeys(list(facts)+BACKGROUND))   # facts + small structural library
    # (1) goal classifier — structural rule for the top connective, FIRST
    if "\\<and>" in concl: c.append("apply (rule conjI)")
    if "\\<noteq>" in concl or "\\<not>" in concl: c.append("apply (rule notI)")
    if "\\<longrightarrow>" in concl: c.append("apply (rule impI)")
    if "\\<exists>" in concl: c.append("apply (rule exI)")
    if "\\<forall>" in concl and "\\<in>" in concl: c.append("apply (rule ballI)")  # bounded ∀x∈S
    if "\\<forall>" in concl: c.append("apply (rule allI)")
    if "\\<subseteq>" in concl: c.append("apply (rule subsetI)")                    # set inclusion
    # standard bounded/set dest idioms (consume one matching premise)
    c += ["apply (drule (1) bspec)","apply (drule (1) subsetD)","apply (erule (1) ballE)"]
    # (2) assumption — cheap, closes leaves
    c.append("apply assumption")
    # (2b) standard elims — split an assumption of any shape (needed once the goal is
    #      decomposed, e.g. after iffI/impI). Plus discharge a contradictory assumption:
    #      `(erule notE, rule refl)` closes via a `t \<noteq> t` hyp (erule notE -> goal
    #      `t = t` -> refl); `(erule notE, assumption)` closes via `x=a` + `x\<noteq>a`.
    c += ["apply (erule conjE)","apply (erule disjE)","apply (erule exE)","apply (erule impE)",
          "apply (erule notE, assumption)","apply (erule notE, rule refl)"]
    # (3) contradiction micro-pattern, GATED to a `False` goal (after notI). The
    #     conjE / combined-notE / iffD1 moves live ONLY here — emitting them on
    #     every goal floods trancl goals with spurious progress.
    if concl.strip()=="False" or concl.endswith("False"):
        c.append("apply hypsubst")
        for f in facts:
            c.append(f"apply (drule {f}[THEN iffD1])")
            c.append(f"apply (frule {f}[THEN iffD1])")
        c.append("apply (erule conjE)")
        c.append("apply (erule notE, assumption)")
        for f in facts: c.append(f"apply (drule {f})")
    # (4) equality goal — iffI splits a boolean equality. NO ∧/≠ exclusions: parency's
    #     RHS `(p ≠ slot ∧ ...)` contains both, which previously hid iffI. `=` only ever
    #     matches a real HOL `=` (\<noteq>/\<longrightarrow>/… tokens carry no literal '='),
    #     and iffI fails harmlessly on a non-bool `=` (fail_memo records it).
    if "=" in concl:
        c.append("apply (rule iffI)")
        c.append("apply (rule refl)")
        for f in facts: c.append(f"apply (subst {f})")
    # (5) trancl/reachability goal — the trancl rule family + hammer facts
    if "\\<leadsto>" in concl:
        for f in TRANCL_FAMILY+facts: c.append(f"apply (rule {f})")
        for f in facts: c.append(f"apply (erule {f})")
    # (5b) ELIM-rule application + definitional unfold. These search-dominated proofs
    #      are elim:/elim!:-driven and usually need a def unfolded first; the proposer
    #      emitted NEITHER before, so the key moves were unreachable. `erule` applies a
    #      fact as an elimination rule to a matching premise; unfold / `simp only:`
    #      open a definition (both audit-safe — not bare simp/auto/blast).
    for f in facts:
        if f.endswith("_def"):
            c.append(f"apply (unfold {f})")
            c.append(f"apply (simp only: {f})")
    for f in facts: c.append(f"apply (erule {f})")
    for f in facts: c.append(f"apply (erule {f}, assumption)")
    # (6) generic fact+background rule, then iff-elim / frule / drule (FACTS only)
    for f in pool: c.append(f"apply (rule {f})")
    for f in facts: c.append(f"apply (drule {f}[THEN iffD1])")
    for f in facts: c.append(f"apply (frule {f})")
    for f in facts: c.append(f"apply (drule {f})")
    seen=set(); out=[]
    for x in c:
        if x not in seen: seen.add(x); out.append(x)
    return out

BANNED=re.compile(r"\b(auto|blast|fastforce|force|clarsimp|eval|presburger|sos|arith|linarith|metis|smt|meson)\b")
def audit(path):
    text=" ; ".join(path)
    if BANNED.search(text): return False
    for m in re.finditer(r"\bsimp(_all)?\b", text):
        seg=text[max(0,m.start()-6):m.start()+12]
        if "simp only:" not in seg: return False
    return True

# --- LLM proposer (priority #4): file-bridge to the host-side claude CLI ---
# Rides Claude Max via proposer_host.py on the host; no API key, no creds in the
# container. propose_llm() writes a request to the shared bridge dir and polls
# for the response, then audit-filters whatever the model proposed.
PROPOSER=os.environ.get("PROPOSER","heuristic")
BRIDGE="/workspace/tools/seL4-proof-search/Isa-Repl/bridge"
LLM_CALL_CAP=int(os.environ.get("LLM_CALL_CAP","60"))
_llm={"seq":0,"calls":0,"cache":{}}
def _audit_ok_tac(t):
    if BANNED.search(t): return False
    for m in re.finditer(r"\bsimp(_all)?\b", t):
        if "simp only:" not in t[max(0,m.start()-6):m.start()+12]: return False
    return True
def llm_rank(goal, menu, history=None):
    """Menu-select graft onto the DFS: send the heuristic MENU + the FAILURE HISTORY
    accumulated so far to the host LLM (proposer_host SELECT mode) and return its ranked
    subset, RESTRICTED to the menu. The history lets the model reason WITH feedback
    ("X already failed → try Y") instead of one-shot. search() puts the picks first and
    keeps the rest as fallback. Cached per goal; capped by LLM_CALL_CAP."""
    if not menu or _llm["calls"]>=LLM_CALL_CAP: return [], ""
    if goal in _llm["cache"]: return _llm["cache"][goal]
    _llm["calls"]+=1; _llm["seq"]+=1; rid=f"{_llm['seq']:04d}"
    os.makedirs(BRIDGE,exist_ok=True)
    req=os.path.join(BRIDGE,f"req-{rid}.json"); resp=os.path.join(BRIDGE,f"resp-{rid}.json")
    json.dump({"mode":"react","goal":goal,"menu":menu,"facts":[],"history":(history or [])[-12:],
               "orig":_llm.get("orig","")},   # original automation tactic -> fact ROLE hints
              open(req+".tmp","w")); os.replace(req+".tmp",req)
    t0=time.monotonic(); tacs=[]; thought=""
    while time.monotonic()-t0 < 210:
        if os.path.exists(resp):
            try:
                d=json.load(open(resp)); tacs=d.get("tactics",[]); thought=d.get("thought","")
            except Exception: tacs=[]
            break
        time.sleep(0.3)
    picks=[t for t in tacs if t in menu]   # must be chosen from the menu
    _llm["cache"][goal]=(picks, thought)   # keep the model's REASONING, not just its picks
    return picks, thought

def main():
    os.makedirs(RUNS, exist_ok=True)
    env=os.environ.copy();env["ISABELLE_HOME"]=ISABELLE_HOME
    proc=subprocess.Popen([_java(),"-Xmx8g","-jar",JAR,str(PORT)],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT,text=True)
    time.sleep(12)
    isa=None
    try:
        _evf=None
        gw=JavaGateway(gateway_parameters=GatewayParameters(port=PORT,auto_convert=True,read_timeout=900));isa=gw.entry_point
        # init is intermittently flaky under CPU contention even when the heap exists —
        # retry a few times before giving up (a transient failure is NOT "no path").
        ok=False
        for _it in range(int(os.environ.get("INIT_RETRY","4"))):
            try: ok,_=sr(isa._initializeRepl(THY,L4V,SESSION,[L4V]))
            except Exception as _e: ok=False; print(f"[init try {_it+1} exc: {str(_e)[:60]}]",flush=True)
            if ok: break
            print(f"[init retry {_it+1}/4]",flush=True); time.sleep(8)
        assert ok, "init failed after retries"
        print("[init ok]",flush=True)
        steps=[c for c in isa._parse_to_steps(open(THY,encoding="utf-8").read()).split(SEP) if not isc(c)]
        # exact lemma-name match (`LEMMA:` / `LEMMA [attrs]`), NOT substring — else
        # "parency" wrongly matches "parency_n"/"parency_m" declared earlier.
        idx=next(i for i,s in enumerate(steps)
                 if ft(s) in ("lemma","theorem","corollary")
                 and re.search(r"(?<![\w'])"+re.escape(LEMMA)+r"\s*[:\[]", s))   # word-bounded: "eq" != "Un_eq"
        u=""
        for s in rbs(steps[:idx])[1:]:
            r=isa._step_without_timeout(u+s); u=u+s+"\n" if "False"+SEP in r else ""
        sr(isa._step(steps[idx])); print(f"[reached {LEMMA}]",flush=True)
        idx2=next(i for i in range(idx+1,len(steps)) if ft(steps[i]) in ("lemma","lemmas","definition","theorem","corollary"))
        proof=steps[idx+1:idx2]
        # AP3 (env-gated): proof-dependency facts. The original proof exists and works,
        # so replay it to `done` on a throwaway clone and ask _extract_thm_deps(LEMMA)
        # which library lemmas its automation actually used — the facts a `by auto`/`simp`
        # needs but never names. Restore focus afterwards so the targets loop is unaffected.
        # AP3=0 (default) skips this entirely (exact baseline).
        ap3_facts=[]
        if os.environ.get("AP3","0")=="1":
            try:
                isa._clone_tls("AP3PRE")                 # save post-statement state
                isa._clone_tls("AP3RUN"); isa._focus_tls("AP3RUN")
                u3=""; last_ok=None
                for st in proof:
                    r=isa._step_without_timeout(u3+st); last_ok=not ("False"+SEP in r)
                    u3=u3+st+"\n" if "False"+SEP in r else ""
                # bare name first, then locale-qualified candidates (locale lemmas are
                # registered as Locale.name and invisible to the global-lookup extract).
                cand=[LEMMA]+[f"{loc}.{LEMMA}" for loc in locale_names(steps[:idx])]
                okd=False; deps=""; used=None
                for nm in cand:
                    okd,deps=sr(isa._extract_thm_deps(nm))
                    if okd and deps.strip(): used=nm; break
                raw=[d for d in (deps or "").split(SEP) if d.strip()] if okd else []
                ap3_facts=filter_deps(deps) if okd else []
                isa._focus_tls("AP3PRE")                 # restore for the targets loop
                print(f"[AP3] {len(ap3_facts)} facts for {LEMMA} "
                      f"(name={used} replay_last_ok={last_ok} extract_ok={okd} "
                      f"raw_deps={len(raw)} tried={len(cand)}): {ap3_facts[:12]}",flush=True)
            except Exception as e:
                print(f"[AP3] extract failed ({str(e)[:80]}); contributing nothing",flush=True)
                try: isa._focus_tls("AP3PRE")
                except Exception: pass
                ap3_facts=[]
        AUTO=re.compile(r"\b(auto|force|fastforce|blast|clarsimp)\b")
        node_seq=[0]
        class ServerDead(Exception): pass
        def step_safe(tac):
            try: return sr(isa._step_with_30s_timeout(tac))
            except Exception as e: raise ServerDead(str(e)[:80])

        # ---- capture targets (A_sig via `apply -`, B_sig via original) ----
        # source-line tracking: commands are processed in order, so a forward cursor
        # over the raw theory maps each command to its source line (for the gate).
        src_raw=open(THY,encoding="utf-8").read().splitlines()
        _cur=[0]
        for k,ll in enumerate(src_raw):
            if re.search(r"(?<![\w'])"+re.escape(LEMMA)+r"\s*[:\[]", ll): _cur[0]=k; break
        def src_line_of(cmd):
            nn=_norm(cmd)[:25]
            for k in range(_cur[0], len(src_raw)):
                if nn and nn in _norm(src_raw[k]): _cur[0]=k+1; return k+1
            return None
        targets=[]; u=""
        for i,s in enumerate(proof):
            cp=f"N{i}"; isa._clone_tls(cp)
            is_target = ft(s) in("apply","by") and AUTO.search(s)
            if TARGET_SUBSTR and is_target and _norm(TARGET_SUBSTR) not in _norm(s):
                is_target = False   # not the DB-flagged slow line -> just execute
            if is_target:
                isa._focus_tls(cp); A_goal=isa._extract_goal()
                isa._focus_tls(cp); _,m0=sr(isa._step_without_timeout("apply -")); A_sig=signature(m0)
                isa._focus_tls(cp); t0=time.monotonic()
                _,mb=sr(isa._step_without_timeout(s)); dt=(time.monotonic()-t0)*1000
                sl=src_line_of(s); B_sig=signature(mb)
                targets.append((i,s,cp,A_goal,A_sig,B_sig,dt,sl))
            else:
                src_line_of(s)   # advance cursor past non-target commands too
                r=isa._step_without_timeout(u+s); u=u+s+"\n" if "False"+SEP in r else ""
        # MULTI-LINE selection: keep the M slowest targets above MIN_LINE_MS. The rough
        # in-JVM wall (`dt`) is fine for RANKING which lines to attack; the credible
        # before/after numbers come from the external gate. Then restore proof order
        # so the lines are searched/rewritten LINEARLY top-to-bottom.
        MAX_LINES=int(os.environ.get("MAX_LINES","3")); MIN_LINE_MS=float(os.environ.get("MIN_LINE_MS","50"))
        if not TARGET_SUBSTR:   # multi-line AUTO mode: keep the M slowest above threshold.
            targets=[t for t in targets if (t[6] or 0)>=MIN_LINE_MS]   # (when TARGET_SUBSTR is
            targets.sort(key=lambda t:-(t[6] or 0)); targets=targets[:MAX_LINES]  # set, the line
            targets.sort(key=lambda t:t[0])                            # is already chosen — no time filter)
        print(f"[targets] {len(targets)} lines: {[(t[7], round(t[6] or 0)) for t in targets]}\n",flush=True)

        disc_cache={}
        def discover(node_sig):
            if node_sig in disc_cache: return disc_cache[node_sig]
            crits=[["intro"],["elim"],["dest"]] + ([["solves"]] if SOLVES else [])
            res=[]
            for c in crits:
                try: out=isa._find_theorems(c, 6, True)
                except Exception: out=""
                for n in parse_thm_names(out, 6):
                    if n not in res: res.append(n)
            disc_cache[node_sig]=res; return res

        state={"budget":NODE_BUDGET,"deadline":0.0,"cur_line":None,"fail_memo":set()}
        # --- full-fidelity instrumentation (added; does NOT change search logic) ---
        # Every attempt — fail OR progress — is recorded with: the checkpoint it
        # fired from (`cp`), the child checkpoint it spawned (`child`), the FULL
        # untruncated prover message on failure (`fail_full`), and the FULL resulting
        # goal signature (`goal_sig`) not just the subgoal count. Each is also
        # streamed line-by-line to runs/ab-<LEMMA>.events.jsonl so stdout and the
        # structured record can be cross-checked, and the search tree + every
        # internal state transition can be replayed even when NO path is found.
        EVENTS_PATH=os.path.join(RUNS,f"ab-{LEMMA}.events.jsonl")
        _evf=open(EVENTS_PATH,"w",encoding="utf-8"); _evseq=[0]
        def emit(rec, count=True):
            if count: state["attempts"].append(rec)   # count=False: log to events only, free of ATTEMPT_CAP
            _evseq[0]+=1
            ev=dict(rec); ev["seq"]=_evseq[0]; ev["line"]=state.get("cur_line")
            _evf.write(json.dumps(ev,ensure_ascii=False)+"\n"); _evf.flush()
        def search(cp, depth, B_sig, base_facts, node_sig, seen):
            if state["budget"]<=0 or time.monotonic()>state["deadline"]: return None
            state["budget"]-=1
            facts=base_facts
            d=DEPTH-depth
            sg0=node_sig[0] if node_sig else ""
            menu0 = propose(sg0, facts)                      # heuristic audit-safe menu
            cand = list(menu0); picks = []; thought = ""; fail_hist = []
            if PROPOSER=="llm":                              # LLM ranks the menu; rest = fallback
                # failure feedback: the tactics that already failed earlier in THIS search,
                # most recent last — handed to the model so it can avoid/learn from them.
                fail_hist = [{"action": a.get("tac"), "ok": False,
                              "obs": (a.get("fail_full") or a.get("note") or "failed").split(chr(10))[0][:90]}
                             for a in state["attempts"] if a.get("kind") == "fail"][-12:]
                picks, thought = llm_rank(sg0, cand, fail_hist)
                if picks: cand = picks + [c for c in cand if c not in picks]
            # per-node event: what the agent SAW (facts), the candidate MENU, the FAILURES
            # shown to the model, which items it ranked, and its reasoning. attempt events
            # below carry the same `cp` so they join back to this node.
            emit({"kind":"node","cp":cp,"d":DEPTH-depth,"goal":sg0[:300],
                  "facts":facts,"menu":menu0,"failures_shown":[h["action"] for h in fail_hist],
                  "llm_ranked":picks,"llm_thought":thought}, count=False)
            node_key=canon_sig(node_sig)   # failure-memo key for THIS canonical state
            for tac in cand:
                if len(state["attempts"]) >= ATTEMPT_CAP: return None   # per-line attempt cap
                if state["budget"]<=0 or time.monotonic()>state["deadline"]: return None
                # FAILURE FEEDBACK: Isabelle is deterministic, so a (state, tactic) that
                # failed once fails forever. Skip it WITHOUT executing or spending an
                # attempt — this kills the cross-node re-tries of the same doomed
                # rule/fact fan-out on canonically-identical sibling goals.
                if (node_key,tac) in state["fail_memo"]:
                    emit({"cp":cp,"d":d,"tac":tac,"kind":"memo-skip(prior-fail)"}, count=False)
                    continue
                isa._focus_tls(cp)
                try: ok,m=step_safe(tac)
                except ServerDead: raise
                if not ok:
                    state["fail_memo"].add((node_key,tac))
                    emit({"cp":cp,"d":d,"tac":tac,"kind":"fail",
                          "note":m.strip().split(chr(10))[0][:48],
                          "fail_full":m.strip()})
                    continue
                sig=signature(m)
                if sig==B_sig:
                    emit({"cp":cp,"d":d,"tac":tac,"kind":"CLOSES (==B)",
                          "ngoals":len(sig),"goal_sig":sig_list(sig),"first_goal":first_goal(m)})
                    return [tac]
                if len(sig) > len(B_sig) + MAX_GROWTH:   # diverging away from B — prune
                    emit({"cp":cp,"d":d,"tac":tac,"kind":"pruned(growth)",
                          "ngoals":len(sig),"goal_sig":sig_list(sig),"first_goal":first_goal(m)})
                    continue
                if depth<=1:
                    emit({"cp":cp,"d":d,"tac":tac,"kind":"progress@maxdepth",
                          "ngoals":len(sig),"goal_sig":sig_list(sig),"first_goal":first_goal(m)})
                    continue
                csig=canon_sig(sig)   # schematic-invariant dedup key (breaks ?Var loops)
                if csig in seen:
                    emit({"cp":cp,"d":d,"tac":tac,"kind":"seen(pruned)",
                          "ngoals":len(sig),"goal_sig":sig_list(sig),"first_goal":first_goal(m)})
                    continue
                seen.add(csig)
                node_seq[0]+=1; child=f"S{node_seq[0]}"
                emit({"cp":cp,"d":d,"tac":tac,"kind":"progress","child":child,
                      "ngoals":len(sig),"goal_sig":sig_list(sig),"first_goal":first_goal(m)})
                isa._clone_tls(child)
                sub=search(child, depth-1, B_sig, base_facts, sig, seen)
                if sub is not None: return [tac]+sub
            return None

        def replay(cp, path):
            """Replay a found path from A, recording each step's resulting state."""
            isa._focus_tls(cp); trace=[]
            for tac in path:
                _,m=sr(isa._step_without_timeout(tac))
                trace.append({"tac":tac,"goal_after":first_goal(m),
                              "nsubgoals":len(signature(m)),"goal_sig":sig_list(signature(m))})
            return trace

        def time_run(cp, steps, reps=4):
            """Median(min) wall (ms) of running `steps` from checkpoint cp — used to
            check whether removing the search actually SPEEDS UP the line."""
            ts=[]
            for _ in range(reps):
                isa._focus_tls(cp); t=time.monotonic()
                for st in steps: isa._step_without_timeout(st)
                ts.append((time.monotonic()-t)*1000)
            return min(ts)

        def time_spread(cp, steps, reps=7):
            """(min, median, max) wall ms over reps — exposes the noise/IPC floor so a
            sub-floor 'speedup' (e.g. 8ms->7ms) is visibly noise, not a real win."""
            ts=[]
            for _ in range(reps):
                isa._focus_tls(cp); t=time.monotonic()
                for st in steps: isa._step_without_timeout(st)
                ts.append((time.monotonic()-t)*1000)
            ts.sort(); return ts[0], ts[len(ts)//2], ts[-1]

        only={int(x) for x in TARGETS_ENV.split(",") if x.strip()} if TARGETS_ENV else None
        results=[]
        for (i,s,cp,A_goal,A_sig,B_sig,dt,sl) in targets:
            if only is not None and i not in only: continue
            # hammer is nondeterministic + sometimes drops an essential fact;
            # union over a couple of calls to stabilise the candidate set.
            hset=set()
            for _ in range(int(os.environ.get("HAMMER_N","2"))):
                isa._focus_tls(cp)
                try: hset |= set(parse_hammer_facts(isa._prove_by_hammer(), s))
                except Exception: pass
            hfacts=list(hset)
            print(f"== line {i}: {s.strip()[:60]}",flush=True)
            print(f"   A goal: {A_goal.replace(SEP,' ').strip()[:120]}",flush=True)
            print(f"   hammer facts: {hfacts}",flush=True)
            state["budget"]=NODE_BUDGET; state["deadline"]=time.monotonic()+TIME_CAP; state["attempts"]=[]
            state["fail_memo"]=set()   # reset failure feedback per target line
            state["cur_line"]=i
            _llm["orig"]=s             # original automation tactic for this line (role hints)
            # SWAP mode (idea 2): instead of static reconstruction, find a faster WHOLE
            # method reaching the same B. Tiny menu, empirically validated by B_sig match.
            if os.environ.get("SWAP","0")=="1":
                isa._focus_tls(cp); hf=[]
                try: hf=parse_hammer_facts(isa._prove_by_hammer(), s)
                except Exception: pass
                isa._focus_tls(cp); o_min,o_med,o_max=time_spread(cp,[s])
                valid=[]; diag=[]
                for c in swap_menu(s, hf):
                    isa._focus_tls(cp)
                    try: ok,m=step_safe(c)
                    except ServerDead: raise
                    if not ok: diag.append((c,"fail",None)); continue
                    sig=signature(m)
                    if sig==B_sig:
                        _,md,_=time_spread(cp,[c]); valid.append((c,round(md,1))); diag.append((c,"==B",round(md,1)))
                    else: diag.append((c,f"d{len(sig)}sg",None))
                valid.sort(key=lambda x:x[1])
                best=valid[0] if valid else None
                spd=round(100*(o_med-best[1])/o_med,1) if best else None
                # REAL speedup: orig above the ~IPC noise floor (>=30ms) AND >=10ms saved AND >10%.
                real=best is not None and o_med>=30 and (o_med-best[1])>=10 and best[1]<o_med*0.90
                print(f"   [SWAP] orig med={round(o_med,1)}ms (spread {round(o_min,1)}-{round(o_max,1)}) "
                      f"best={best} speedup={spd}% REAL={real} valid={len(valid)}/{len(diag)}",flush=True)
                for c,o,t in diag:
                    print(f"      {o:<7}{(' %.1fms'%t) if t is not None else '':>9}  {c.strip()[:64]}",flush=True)
                results.append({"line":i,"orig":s.strip(),"orig_ms":round(o_med,1),
                                "orig_min":round(o_min,1),"orig_max":round(o_max,1),
                                "swap_valid":valid,"best_swap":best[0].strip() if best else None,
                                "best_ms":best[1] if best else None,"speedup_pct":spd,
                                "sped_up":real,"n_cands":len(diag)})
                continue
            # AP1 (env-gated for the ablation): wire the find_theorems-based lemma
            # discovery that discover() implements but search() never called. At the
            # A-state, query intro/elim/dest[/solves] rules relevant to the goal and
            # union them into the candidate fact set. AP1=0 (default) reproduces the
            # exact baseline (hammer-only) so the ablation arms differ by this flag alone.
            disc=[]
            if os.environ.get("AP1","0")=="1":
                isa._focus_tls(cp); disc=discover(A_sig)
                print(f"   discovered (find_theorems): {disc}",flush=True)
            base_facts=list(dict.fromkeys(hfacts+disc+ap3_facts))   # dedup: hammer, AP1, AP3
            try:
                path=search(cp, DEPTH, B_sig, base_facts, A_sig, set())
            except ServerDead as e:
                print(f"   [server died: {e}] — aborting\n",flush=True)
                results.append({"line":i,"orig":s.strip(),"hammer_facts":hfacts,"path":None,"audit":False,"server_died":True}); break
            okp = path is not None and audit(path)
            trace = replay(cp, path) if okp else None
            used=NODE_BUDGET-state["budget"]
            print(f"   PATH: {path}  audit={'OK' if okp else 'FAIL/none'}  nodes={used}",flush=True)
            if trace:
                for t in trace: print(f"      -> {t['tac']}   [{t['nsubgoals']} sg] {t['goal_after']}",flush=True)
            # TIMING is DEFERRED to the external reliable gate (gate.py: in-prover
            # `isar timing` + `check-theory.sh` build) — run by the loop driver
            # AFTER this JVM exits, so the 12GB poly server never coexists with the
            # 8GB JVM heap. The old in-JVM `time_run` (Isa-REPL `time.monotonic`)
            # baked ~6-8ms IPC into every step and biased multi-step static paths as
            # "slower", which is exactly what made the old verdicts untrustworthy.
            orig_ms=static_ms=delta_pct=None; sped_up=None  # filled by gate.py later
            print(flush=True)
            results.append({"line":i,"src_line":sl,"orig_ms_est":round(dt) if dt else None,
                            "orig":s.strip(),"A_goal":A_goal.replace(SEP," ").strip(),
                            "B_sig":list(B_sig),"hammer_facts":hfacts,"discovered":disc_cache.get(A_sig,[]),
                            "ap3_facts":ap3_facts,
                            "path":path,"audit":okp,"nodes_used":used,"trace":trace,
                            "orig_ms":orig_ms,"static_ms":static_ms,"delta_pct":delta_pct,"sped_up":sped_up,
                            "attempts":list(state["attempts"])})
        # ---- durable records ----
        rec={"lemma":LEMMA,"thy":THY,"session":SESSION,"depth":DEPTH,
             "node_budget":NODE_BUDGET,"solves":SOLVES,"results":results}
        json.dump(rec, open(os.path.join(RUNS,f"ab-{LEMMA}.json"),"w"), indent=1, ensure_ascii=False)
        with open(os.path.join(RUNS,f"ab-{LEMMA}.md"),"w") as f:
            f.write(f"# A->B reconstruction run — {LEMMA} ({THY}, {SESSION})\n\n")
            f.write(f"depth={DEPTH} node_budget={NODE_BUDGET} solves={SOLVES}\n\n")
            for r in results:
                f.write(f"## line {r['line']} — `{r['orig']}`\n")
                if "speedup_pct" in r:   # SWAP-mode record
                    f.write(f"- orig: {r.get('orig_ms')}ms  best_swap: `{r.get('best_swap')}` "
                            f"({r.get('best_ms')}ms, {r.get('speedup_pct')}% faster, sped_up={r.get('sped_up')})\n")
                    f.write(f"- valid alternatives: {r.get('swap_valid',[])}\n\n")
                    continue
                f.write(f"- A goal: `{r.get('A_goal','')[:200]}`\n")
                f.write(f"- hammer facts: {r.get('hammer_facts',[])}\n")
                f.write(f"- discovered (find_theorems): {r.get('discovered',[])}\n")
                f.write(f"- nodes used: {r.get('nodes_used')}\n")
                if r.get('path') and r.get('audit'):
                    f.write(f"- **static path (audit OK):**\n")
                    for t in (r.get('trace') or []):
                        f.write(f"  - `{t['tac']}` -> [{t['nsubgoals']} sg] {t['goal_after']}\n")
                else:
                    f.write(f"- **no audit-passing path found** (work-bound or beyond search) -> pivot\n")
                f.write("\n")
        print("[saved]", os.path.join(RUNS,f"ab-{LEMMA}.{{json,md}}"),flush=True)
        print("[summary]", json.dumps([{"line":r["line"],"audit":r["audit"],"path":r["path"]} for r in results], ensure_ascii=False),flush=True)
    finally:
        try: _evf.close()
        except: pass
        try: isa._exit()
        except: pass
        proc.terminate(); proc.wait()
main()
