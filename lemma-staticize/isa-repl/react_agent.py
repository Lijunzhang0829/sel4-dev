"""LLM-guided DFS A->B reconstruction agent on Isa-REPL.

A backtracking DFS where the host LLM ORDERS each node's candidate menu (claude-picks
first, then the full structural+fact menu systematically), so a single wrong/timed-out
LLM pick no longer dead-ends the whole search:

  at each node the model sees the current goal + facts + history and ranks the menu;
  we try its picks first, fall through to the rest of the menu, and BACKTRACK to the
  parent on a dead end. Each candidate is applied on a CLONE so the parent checkpoint
  stays intact for backtracking. Visited proof states (schematic-canonicalised) are
  pruned to avoid cycles; states that blow up past |B|+MAX_GROWTH subgoals are pruned.

This combines claude's guidance (speed) with menu+backtrack (robustness) — it fixes the
old linear loop's variance, where one bad LLM pick at a critical node -> spurious NO-PATH.
Only audit-passing STATIC tactics enter the menu/path (same contract as ab_agent.py).
Talks to the host `claude` CLI via the shared bridge dir (proposer_host.py, mode=react/
SELECT). Run inside the sel4-l4v container.

Env: THY, LEMMA, SESSION, PORT, TARGET_SUBSTR, MAX_STEPS (max path depth), TIME_CAP,
     NODE_CAP (total applications), MAX_GROWTH (subgoal blow-up prune).
"""
import os, re, subprocess, time, json, glob
from py4j.java_gateway import JavaGateway, GatewayParameters

def _java():
    """Resolve the JVM robustly — a restarted container may have only isabelle/bin on
    PATH (no JDK); a bare `java` then dies (FileNotFoundError) and the loop never starts."""
    for pat in ("/workspace/verification/isabelle/contrib/jdk-*/x86_64-linux/bin/java",
                "/root/.isabelle/contrib/jdk-*/x86_64-linux/bin/java"):
        for p in sorted(glob.glob(pat)):
            if os.path.exists(p): return p
    return "java"

ISABELLE_HOME="/workspace/verification/isabelle"
JAR="/workspace/tools/seL4-proof-search/Isa-Repl/target/IsaREPL.jar"
# Heaps are fingerprinted to /sel4-project (docker-compose overlay; same files as
# /workspace but THAT path was used at build time). Initing with /workspace makes
# scala-isabelle see "different" sources and REBUILD the session (SCALA_ISABELLE_TEMP),
# which churns heaps -> init flakiness / connection-refused. Honour the build path.
L4V=os.environ.get("L4V_DIR","/sel4-project/verification/l4v")
THY=os.environ.get("THY", os.path.join(L4V,"proof/infoflow/InfoFlow_IF.thy"))
LEMMA=os.environ.get("LEMMA","sameFor_sym")
SESSION=os.environ.get("SESSION","InfoFlow")
PORT=int(os.environ.get("PORT","25580")); SEP="<\\SEP>"
TARGET_SUBSTR=os.environ.get("TARGET_SUBSTR","")
_TAG=os.environ.get("RESULT_TAG","")  # unique output filename suffix for parallel runs
import base64 as _b64
_tb=os.environ.get("TARGET_SUBSTR_B64","")
if _tb: TARGET_SUBSTR=_b64.b64decode(_tb).decode("utf-8")   # exact per-line pin (avoids env quote issues)
MAX_STEPS=int(os.environ.get("MAX_STEPS","12"))     # max accepted-progress steps
MAX_FAILS=int(os.environ.get("MAX_FAILS","4"))      # consecutive no-progress rounds before giving up
TIME_CAP=float(os.environ.get("TIME_CAP","600"))
BRIDGE="/workspace/tools/seL4-proof-search/Isa-Repl/bridge"
RUNS="/workspace/tools/seL4-proof-search/Isa-Repl/runs"
_norm=lambda s: re.sub(r"\s+","",s)
def _hint_in(hint,s):
    """robust hint->step match: `subgoal by (m)` parses as bare `subgoal` + `by (m)`, so strip a
    leading `subgoal` from the hint; and the extractor truncates hot_line to 100 chars, so a
    truncated hint is a PREFIX of the real step -> use containment (hint in step)."""
    hn=_norm(hint)
    if hn.startswith("subgoal"): hn=hn[len("subgoal"):]
    return hn in _norm(s)

def sr(r):
    p=r.split(SEP,1); return (p[0]=="True",p[1]) if len(p)==2 else (None,r)

def _decompress(b):
    b=bytes(b)
    try:
        import zstandard as z; return z.ZstdDecompressor().decompress(b)
    except Exception:
        return subprocess.run(["zstd","-dc"],input=b,capture_output=True).stdout

def db_classical_elapsed(thy):
    """{norm(source-line-text): max DB elapsed} for classical apply/by commands in `thy`,
    decoded from heap command_timings. The cost oracle for 'which line is most expensive'
    (per-command elapsed, NOT source order). Empty on any failure -> first-line fallback."""
    import sqlite3
    rel=thy.split("l4v/")[-1]
    try:
        heaps=glob.glob("/root/.isabelle/heaps/*/log")[0]
        src=open(thy,encoding="utf-8",errors="replace").read()
    except Exception:
        return {}
    AUTO=re.compile(r"\b(auto|force|fastforce|blast|clarsimp|safe)\b")
    starts=[0]; sym=0; i=0; n=len(src)               # symbol-offset -> line (\<..> = 1 symbol)
    while i<n:
        c=src[i]
        if c=="\\" and i+1<n and src[i+1]=="<":
            j=src.find(">",i)
            if j!=-1: i=j+1; sym+=1; continue
        if c=="\n": starts.append(sym+1)
        sym+=1; i+=1
    def o2l(o):
        lo,hi=0,len(starts)-1
        while lo<hi:
            m=(lo+hi+1)//2
            if starts[m]<=o: lo=m
            else: hi=m-1
        return lo
    L=src.split("\n"); out={}
    for db in glob.glob(os.path.join(heaps,"*.db")):
        if "SCALA_ISABELLE_TEMP" in db: continue
        try:
            row=sqlite3.connect(db).execute("SELECT command_timings FROM isabelle_session_info").fetchone()
        except Exception: continue
        if not row or row[0] is None: continue
        cur={}
        for tok in _decompress(row[0]).decode("utf-8","replace").split("\x06"):
            tok=tok.replace("\x05","")
            if "=" not in tok: continue
            k,_,v=tok.partition("="); k=k.strip()
            if k in ("name","offset","file","elapsed"):
                cur[k]=v
                if k=="elapsed" and "file" in cur and "offset" in cur:
                    if cur["file"].endswith(rel) and cur.get("name") in ("by","apply"):
                        line=L[o2l(int(cur["offset"]))] if o2l(int(cur["offset"]))<len(L) else ""
                        if AUTO.search(line):
                            key=_norm(line); el=float(cur["elapsed"])
                            if key and el>out.get(key,0): out[key]=el
                    cur={}
    return out
KW=["apply","supply","subgoal","using","unfolding","proof","qed","done","{","}","next","note","let","write","fix","assume","then","have","show","from","with","also","finally","moreover","ultimately","presume","define","consider","obtain","case","typ","term","prop","thm","print_statement","apply_end","defer","prefer","back","oops","hence","thus",".","..","and","include","including","is","interpret","by"]
def ft(s): return re.split(r"[ ()\n]+", s.strip())[0] if s.strip() else ""
def isc(c): s=c.strip(); return s.startswith("(*") and s.endswith("*)")
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
    # capture the FULL text of each subgoal (join wrapped continuation lines), not just the first
    # printed line. The old version kept only `^\d+.` lines, so a long goal wrapped across output
    # lines collapsed to its first line -> two different goals sharing that prefix gave the SAME
    # signature (a reach-B FALSE POSITIVE, observed on in_whileLoop_corres). Real check-theory
    # build remains the ground-truth gate; this just makes the in-REPL pre-filter far less lossy.
    goals=[]; cur=None
    for l in msg.split("\n"):
        if re.match(r"\s*\d+\.",l):
            if cur is not None: goals.append(cur)
            cur=l.strip()
        elif cur is not None:
            if l.strip()=="": goals.append(cur); cur=None
            else: cur+=" "+l.strip()
    if cur is not None: goals.append(cur)
    return tuple(re.sub(r"\s+"," ",g) for g in goals)
def first_goal(msg):
    for l in msg.split("\n"):
        if re.match(r"\s*1\.",l): return re.sub(r"\s+"," ",l.strip())[:200]
    return ("No subgoals" if "No subgoals" in msg else msg.strip().split("\n")[0][:80])
def goal_text(msg, cap=1200):
    """All subgoals, whitespace-normalised, capped — what the model reasons over."""
    ls=[re.sub(r"\s+"," ",l.strip()) for l in msg.split("\n") if re.match(r"\s*\d+\.",l)]
    if not ls: return "No subgoals" if "No subgoals" in msg else msg.strip()[:cap]
    t="\n".join(ls)
    return t[:cap]+" …TRUNC" if len(t)>cap else t

def concl_of(sg):
    s=sg
    if "\\<Longrightarrow>" in s: s=s.split("\\<Longrightarrow>")[-1]
    return s.strip()
def build_menu(first_sg, facts):
    """Restricted, audit-safe candidate menu for the LLM to SELECT from (vs free
    generation, which wastes budget inventing invalid tactics). Structural intro/elim
    by the goal's top connective + every hammer fact in every usable position."""
    concl=concl_of(first_sg); m=[]
    if "\\<and>" in concl: m.append("apply (rule conjI)")
    if "\\<longrightarrow>" in concl: m.append("apply (rule impI)")
    if "\\<forall>" in concl: m.append("apply (rule allI)")
    if "\\<exists>" in concl: m.append("apply (rule exI)")
    if "\\<noteq>" in concl or "\\<not>" in concl: m.append("apply (rule notI)")
    if "=" in concl and "\\<and>" not in concl and "\\<noteq>" not in concl:
        m += ["apply (rule iffI)","apply (rule refl)"]
    # standard elims always available (an assumption may have any shape); plus close moves
    m += ["apply (erule conjE)","apply (erule disjE)","apply (erule exE)","apply (erule impE)",
          "apply (erule notE)","apply (erule allE)","apply assumption","apply hypsubst"]
    for f in facts:
        m += [f"apply (rule {f})",f"apply (erule {f})",f"apply (drule {f})",f"apply (frule {f})"]
        if f.endswith("_def"): m += [f"apply (unfold {f})",f"apply (simp only: {f})"]
        else: m.append(f"apply (subst {f})")
    seen=set(); return [x for x in m if not (x in seen or seen.add(x))]

FACT_RE=re.compile(r"[A-Za-z_][A-Za-z0-9_'.]*(?:\(\d+\))?")
def parse_hammer_facts(hammer_msg, orig_tac):
    facts=set()
    for m in re.finditer(r"\((?:meson|metis|smt[^ ]*|blast|fastforce|auto|force|fast)\s+([^()]*(?:\([^()]*\)[^()]*)*)\)", hammer_msg):
        for t in FACT_RE.findall(m.group(1)): facts.add(t)
    for grp in re.split(r"\b(?:intro|dest|elim|simp(?:\s+only)?|del|cong|split|add)!?:", orig_tac)[1:]:
        for t in FACT_RE.findall(grp): facts.add(t)
    bad={"intro","dest","elim","simp","only","add","del","split","cong","if_split_asm",
         "meson","metis","smt","blast","fastforce","auto","force","fast","apply","by","using","rule","safe"}
    return [f for f in facts if f not in bad and not f.isdigit()]

BANNED=re.compile(r"\b(auto|blast|fastforce|force|clarsimp|eval|presburger|sos|arith|linarith|metis|smt|meson)\b")
def audit_ok(tac):
    if BANNED.search(tac): return False
    for m in re.finditer(r"\bsimp(_all)?\b", tac):
        if "simp only:" not in tac[max(0,m.start()-6):m.start()+12]: return False
    return True

# --- bridge to host LLM (proposer_host.py, mode=react) ---
_seq=[0]
def llm_react(goal, facts, target, history, time_left, menu=None, delta=""):
    _seq[0]+=1; rid=f"R{_seq[0]:04d}"
    os.makedirs(BRIDGE,exist_ok=True)
    req=os.path.join(BRIDGE,f"req-{rid}.json"); resp=os.path.join(BRIDGE,f"resp-{rid}.json")
    payload={"mode":"react","goal":goal,"facts":facts,"target":target,
             "history":history[-12:],   # last dozen observations is plenty of context
             "delta":delta}             # |A| -> |B| subgoal-count hint (split vs close)
    if menu: payload["menu"]=menu       # constrained SELECT mode
    json.dump(payload, open(req+".tmp","w")); os.replace(req+".tmp",req)
    t0=time.monotonic()
    # must exceed proposer_host's BUDGET (~290s incl retries) or react abandons a still-
    # pending request and falls back to bad menu picks -> spurious NO-PATH.
    while time.monotonic()-t0 < min(300, time_left):
        if os.path.exists(resp):
            try: d=json.load(open(resp))
            except Exception: d={}
            return [t for t in d.get("tactics",[]) if t.startswith("apply ") and audit_ok(t)], d.get("thought","")
        time.sleep(0.3)
    return [], "(llm timeout)"

def time_static_path(isa, A_cp, path, reps=3):
    """In-REPL wall of the static PATH from checkpoint A, IPC-adjusted (subtract
    len(path) x per-step IPC). Returns (static_ms, ipc_ms). Shared by react + genstat;
    orig_ms is captured separately at the (already-run) B_sig step."""
    def run(cmds):
        isa._focus_tls(A_cp); isa._clone_tls("TMR"); isa._focus_tls("TMR")
        t0 = time.monotonic()
        for c in cmds:
            try: isa._step_without_timeout(c)
            except Exception: pass
        isa._focus_tls(A_cp)
        return (time.monotonic() - t0) * 1000.0
    md = lambda xs: (sorted(xs)[len(xs) // 2] if xs else 0.0)
    ipc = md([run(["apply -"]) for _ in range(reps)])
    stat = md([run(path) for _ in range(reps)]) if path else 0.0
    return round(max(0.0, stat - len(path) * ipc), 1), round(ipc, 1)

def main():
    os.makedirs(RUNS, exist_ok=True)
    env=os.environ.copy(); env["ISABELLE_HOME"]=ISABELLE_HOME
    proc=subprocess.Popen([_java(),"-Xmx8g","-jar",JAR,str(PORT)],env=env,
                          stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT,text=True)
    time.sleep(12)
    isa=None; _evf=None
    rec={"lemma":LEMMA,"thy":THY,"session":SESSION,"mode":"react",
         "target":None,"B_sig":None,"facts":[],"steps":[],"path":None,"audit":False,"verdict":"NO-PATH"}
    try:
        gw=JavaGateway(gateway_parameters=GatewayParameters(port=PORT,auto_convert=True,read_timeout=900)); isa=gw.entry_point
        ok,_=sr(isa._initializeRepl(THY,L4V,SESSION,[L4V])); assert ok; print("[init ok]",flush=True)
        steps=[c for c in isa._parse_to_steps(open(THY,encoding="utf-8").read()).split(SEP) if not isc(c)]
        # exact lemma-name match: `LEMMA:` or `LEMMA [attrs]` — NOT a substring, else
        # "parency" wrongly matches "parency_n"/"parency_m" declared earlier.
        idx=next(i for i,s in enumerate(steps)
                 if ft(s) in ("lemma","theorem","corollary") and re.search(re.escape(LEMMA)+r"\s*[:\[]", s))
        u=""
        for s in rbs(steps[:idx])[1:]:
            r=isa._step_without_timeout(u+s); u=u+s+"\n" if "False"+SEP in r else ""
        sr(isa._step(steps[idx])); print(f"[reached {LEMMA}]",flush=True)
        idx2=next(i for i in range(idx+1,len(steps)) if ft(steps[i]) in ("lemma","lemmas","definition","theorem","corollary"))
        proof=steps[idx+1:idx2]
        AUTO=re.compile(r"\b(auto|force|fastforce|blast|clarsimp|safe)\b")
        # COST-AWARE TARGETING: among the lemma's classical lines, pin the one with the
        # highest DB elapsed (not the first in source order). Manual TARGET_SUBSTR still
        # overrides; single-classical-line or no-DB-timing -> first-classical fallback.
        hint=TARGET_SUBSTR
        if not hint:
            cl=[(i,s) for i,s in enumerate(proof) if ft(s) in ("apply","by","subgoal") and AUTO.search(s)]
            tmap=db_classical_elapsed(THY) if len(cl)>1 else {}
            if tmap:
                def _el(s):
                    ns=_norm(s); return max((el for k,el in tmap.items() if k and k in ns), default=0.0)
                scored=sorted(((_el(s),i,s) for i,s in cl), key=lambda x:-x[0])
                if scored and scored[0][0]>0:
                    hint=scored[0][2]
                    print(f"[cost-target] hottest of {len(cl)} classical lines: step{scored[0][1]} "
                          f"= {scored[0][0]:.1f}s DB; others={[round(e,1) for e,_,_ in scored[1:4]]}",flush=True)
        # locate the target classical line; checkpoint A before it, capture B from the original
        u=""; target=None
        for i,s in enumerate(proof):
            cp=f"N{i}"; isa._clone_tls(cp)
            # `subgoal by (<auto>)` closes the first goal -> reach-B target = outer (N-1)-goal state
            is_t = ft(s) in ("apply","by","subgoal") and AUTO.search(s)
            if hint and is_t and not _hint_in(hint,s): is_t=False
            if is_t:
                # `subgoal by (m)` parses as bare `subgoal`(focus) + `by (m)`. Targeting the inner
                # `by` would make A focused but B the OUTER (N-1)-goal state (focus exits) -> raw
                # tactics can't cross that boundary. Instead target the subgoal+by as a UNIT from
                # OUTER A (before `subgoal`): then a static `apply (m)` closing goal 1 reaches B.
                pre=cp; st=s
                if i>0 and ft(proof[i-1])=="subgoal":
                    pre=f"N{i-1}"; st=proof[i-1]+"\n"+s
                isa._focus_tls(pre); A_goal=isa._extract_goal()
                isa._focus_tls(pre); _t0=time.monotonic(); _,mb=sr(isa._step_without_timeout(st)); _om=(time.monotonic()-_t0)*1000; B_sig=signature(mb)
                target=(i,st,pre,A_goal,B_sig,_om); break
            else:
                r=isa._step_without_timeout(u+s); u=u+s+"\n" if "False"+SEP in r else ""
        if not target:
            print("[no target classical line]",flush=True); rec["verdict"]="NO-TARGET"; return
        i,s,A_cp,A_goal,B_sig,orig_ms=target
        hset=set()
        try: hset|=set(parse_hammer_facts(isa._prove_by_hammer(),s))
        except Exception: pass
        facts=sorted(hset)
        target_desc=("close the goal (0 subgoals)" if not B_sig
                     else f"reach this {len(B_sig)}-subgoal state:\n"+"\n".join(B_sig)[:600])
        rec.update(target=s.strip(), B_sig=list(B_sig), facts=facts)
        print(f"== target line {i}: {s.strip()[:70]}",flush=True)
        print(f"   facts: {facts}",flush=True)

        EVENTS=os.path.join(RUNS,f"react-{LEMMA}{_TAG}.events.jsonl"); _evf=open(EVENTS,"w",encoding="utf-8")
        def ev(d): _evf.write(json.dumps(d,ensure_ascii=False)+"\n"); _evf.flush()

        class ServerDead(Exception): pass
        def step_safe(tac):
            try: return sr(isa._step_with_30s_timeout(tac))
            except Exception as e: raise ServerDead(str(e)[:80])

        # numbered proof-state probe: `apply -` (no-op method) on a clone re-displays
        # the goals as "1. ... 2. ..." so signature() parses them. _extract_goal()
        # returns a bare term with no "N." prefix -> signature ()==B_sig -> false close.
        pseq=[0]
        def probe(cp):
            pseq[0]+=1; pn=f"P{pseq[0]}"
            isa._focus_tls(cp); isa._clone_tls(pn); isa._focus_tls(pn)
            ok,m=sr(isa._step_without_timeout("apply -"))
            isa._focus_tls(cp)
            return (signature(m), m) if ok else ((), "No subgoals")

        # ---- DFS loop (claude orders each node, systematic menu fallback, BACKTRACK) ----
        # The old loop was linear/no-backtrack: one wrong LLM pick at a node -> dead end ->
        # give up (spurious NO-PATH, the variance failure). Now we keep claude's picks
        # FIRST but fall through to the full menu and BACKTRACK on dead ends, so a single
        # LLM whiff no longer kills the search. claude still guides the order (= speed);
        # the menu+backtrack guarantee coverage (= robustness).
        deadline=time.monotonic()+TIME_CAP
        NODE_CAP=int(os.environ.get("NODE_CAP","60"))   # total tactic applications budget
        MAX_GROWTH=int(os.environ.get("MAX_GROWTH","4"))# prune states with >|B|+G subgoals
        path=[]; history=[]; closed=False; nb=[0]; sid=[0]; visited=set()
        canon=lambda sig: tuple(re.sub(r"\?[A-Za-z0-9_']+","?",s) for s in sig)

        def order_node(cp, depth):
            """One LLM call: claude ranks this node's menu; return claude-picks-first then
            the rest of the menu (systematic). Logs the think event. Applies nothing."""
            gsig,gmsg=probe(cp)
            menu=build_menu(gsig[0] if gsig else "", facts)
            nB=len(B_sig)
            delta=(f"{len(gsig)} subgoal(s) now -> target {nB} "
                   + ("(close ALL remaining subgoals)" if nB==0
                      else "MORE (you must SPLIT/intro to grow goals)" if nB>len(gsig)
                      else "FEWER (close/eliminate goals)" if nB<len(gsig)
                      else "SAME count (transform in place)"))
            if os.environ.get("NO_CLAUDE"):                   # ablation: pure DFS, no LLM
                acts,thought=[],"(NO_CLAUDE: pure DFS, structural menu order)"
            else:
                acts,thought=llm_react(goal_text(gmsg), facts, target_desc, history,
                                       deadline-time.monotonic(), menu=menu, delta=delta)
            acts=[a for a in acts if a in menu]
            ordered=acts+[m for m in menu if m not in acts]   # claude-first, full menu after
            sid[0]+=1
            ev({"kind":"think","step":sid[0],"thought":thought,"proposed":acts,"goal":goal_text(gmsg,400)})
            print(f"  [think d{depth}] {thought[:80]}  -> {acts[:3]}",flush=True)
            return ordered

        visited.add(canon(probe(A_cp)[0]))
        stack=[{"cp":A_cp,"cands":order_node(A_cp,0),"idx":0,"path":[]}]
        while stack and not closed:
            if time.monotonic()>deadline: print("[time cap]",flush=True); break
            if nb[0]>=NODE_CAP: print("[node cap]",flush=True); break
            fr=stack[-1]
            if fr["idx"]>=len(fr["cands"]) or len(fr["path"])>=MAX_STEPS:
                stack.pop()                                   # node exhausted -> BACKTRACK
                if stack:
                    sid[0]+=1; ev({"kind":"act","step":sid[0],"action":"(backtrack)","ok":False,"obs":"node exhausted"})
                    print("    ↩ backtrack",flush=True)
                continue
            tac=fr["cands"][fr["idx"]]; fr["idx"]+=1; nb[0]+=1; sid[0]+=1
            trial=f"D{nb[0]}"                                  # clone first: keep parent intact
            isa._focus_tls(fr["cp"]); isa._clone_tls(trial); isa._focus_tls(trial)
            try: ok,m=step_safe(tac)
            except ServerDead: print("[server died]",flush=True); rec["verdict"]="SERVER-DIED"; raise
            if not ok:
                obs="FAILED: "+m.strip().split(chr(10))[1][:100] if chr(10) in m.strip() else m.strip()[:100]
                history.append({"action":tac,"ok":False,"obs":obs})
                ev({"kind":"act","step":sid[0],"action":tac,"ok":False,"obs":m.strip()[:300]})
                continue
            nsig=signature(m)
            if nsig==B_sig:                                   # reached target state B -> done
                path=fr["path"]+[tac]; closed=True
                history.append({"action":tac,"ok":True,"obs":"CLOSES (==target)"})
                ev({"kind":"act","step":sid[0],"action":tac,"ok":True,"closes":True})
                print(f"    ✓✓ {tac}  -> CLOSES",flush=True); break
            cn=canon(nsig)
            if cn in visited or len(nsig)>len(B_sig)+MAX_GROWTH:  # cycle / blow-up -> prune
                ev({"kind":"act","step":sid[0],"action":tac,"ok":True,"ngoals":len(nsig),"obs":"pruned"})
                continue
            visited.add(cn)
            history.append({"action":tac,"ok":True,"obs":f"[{len(nsig)}sg] "+first_goal(m)})
            ev({"kind":"act","step":sid[0],"action":tac,"ok":True,"ngoals":len(nsig),"goal_after":first_goal(m)})
            print(f"    → {tac}  [{len(nsig)}sg] {first_goal(m)[:55]}",flush=True)
            stack.append({"cp":trial,"cands":order_node(trial,len(fr["path"])+1),"idx":0,"path":fr["path"]+[tac]})
        # ---- verdict ----
        okp = closed and audit_ok(" ; ".join(path)) and len(path)>0
        rec.update(path=path, audit=okp, steps=history,
                   verdict=("PATH-FOUND" if okp else ("CLOSED-BUT-AUDIT-FAIL" if closed else "NO-PATH")))
        rec["orig_ms"]=round(orig_ms,1)
        if closed and path:                                   # speed: orig tactic vs static path from A
            try:
                static_ms, ipc_ms = time_static_path(isa, A_cp, path)
                rec.update(static_ms=static_ms, ipc_ms=ipc_ms, saved_ms=round(orig_ms-static_ms,1),
                           frac_saved=(round((orig_ms-static_ms)/orig_ms,3) if orig_ms>0.5 else None),
                           accelerated=bool(orig_ms>0.5 and static_ms < orig_ms*0.9))
                print(f"[speed] orig={orig_ms:.0f}ms static={static_ms:.0f}ms saved={orig_ms-static_ms:.0f}ms accel={rec['accelerated']}",flush=True)
            except Exception as e: rec["time_err"]=str(e)[:100]
        print(f"\n[result] verdict={rec['verdict']}  path={path}",flush=True)
    except Exception as e:
        rec["error"]=str(e)[:200]; print(f"[error] {e}",flush=True)
    finally:
        json.dump(rec, open(os.path.join(RUNS,f"react-{LEMMA}{_TAG}.json"),"w"), indent=1, ensure_ascii=False)
        print("[saved]", os.path.join(RUNS,f"react-{LEMMA}{_TAG}.json"),flush=True)
        try: _evf.close()
        except: pass
        try: isa._exit()
        except: pass
        proc.terminate(); proc.wait()

if __name__ == "__main__":
    main()
