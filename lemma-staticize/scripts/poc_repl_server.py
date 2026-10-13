"""PoC REPL server for claude-driven lemma rewriting.

Holds ONE long-lived Isa-REPL (gateway + poly), reaches the target lemma's automation
line ONCE (the expensive part), captures checkpoint A + target signature B_sig, then
serves single-tactic "try" requests over a file bridge so an external agent (claude -p,
via the thin isa_tool.py CLI) can propose tactics and observe results without touching
py4j. Each `try` runs on a CLONE of A, so A is never consumed — the agent can try many
tactics against the same state. `commit` advances A (for building a multi-step path).

Env: THY, LEMMA, SESSION, TARGET_SUBSTR (substring of the automation line to attack),
     PORT, BRIDGE (dir for poc-req.json/poc-resp.json). Run in-container.
"""
import os, re, subprocess, time, json, glob
from py4j.java_gateway import JavaGateway, GatewayParameters

def _java():
    """Resolve the JVM robustly — a restarted container often has only isabelle/bin
    on PATH (no JDK), so a bare `java` dies with FileNotFoundError before the server
    is READY. Glob Isabelle's bundled x86_64-linux jdk."""
    for pat in ("/workspace/verification/isabelle/contrib/jdk-*/x86_64-linux/bin/java",
                "/root/.isabelle/contrib/jdk-*/x86_64-linux/bin/java"):
        for p in sorted(glob.glob(pat)):
            if os.path.exists(p): return p
    return "java"

ISA="/workspace/verification/isabelle"
JAR="/workspace/tools/seL4-proof-search/Isa-Repl/target/IsaREPL.jar"
L4V=os.environ.get("L4V_DIR","/sel4-project/verification/l4v")
THY=os.environ["THY"]; LEMMA=os.environ["LEMMA"]; SESSION=os.environ["SESSION"]
TARGET=os.environ.get("TARGET_SUBSTR","")
PORT=int(os.environ.get("PORT","25970")); SEP="<\\SEP>"
BRIDGE=os.environ.get("BRIDGE","/workspace/tools/seL4-proof-search/Isa-Repl/poc-bridge")
_norm=lambda s: re.sub(r"\s+","",s)
def sr(r): p=r.split(SEP,1); return (p[0]=="True",p[1]) if len(p)==2 else (None,r)
KW=["apply","supply","subgoal","using","unfolding","proof","qed","done","{","}","next","note","let","write","fix","assume","then","have","show","from","with","also","finally","moreover","ultimately","presume","define","consider","obtain","case","oops","hence","thus",".","..","and","by"]
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
        if not np and ft(cmds[i]) in KW:
            while i<n and ft(cmds[i]) in KW:i+=1
            o.append("sorry")
        else:o.append(cmds[i]);i+=1
    return o
def signature(msg): return tuple(re.sub(r"\s+"," ",l.strip()) for l in msg.split("\n") if re.match(r"\s*\d+\.",l))
def goaltext(msg,cap=1500):
    ls=[re.sub(r"\s+"," ",l.strip()) for l in msg.split("\n") if re.match(r"\s*\d+\.",l)]
    if not ls: return "No subgoals!" if "No subgoals" in msg else msg.strip()[:cap]
    t="\n".join(ls); return t[:cap]+" ...TRUNC" if len(t)>cap else t

def main():
    os.makedirs(BRIDGE,exist_ok=True)
    env=os.environ.copy(); env["ISABELLE_HOME"]=ISA
    # memory guard: the JVM needs ~8GB. If a leftover 12GB gate poly/ml_server is still
    # hogging RAM, the JVM dies at init and py4j reports "Answer from Java side is empty"
    # / socket closed. Surface it instead of failing opaquely.
    try:
        avail=int(next(l.split()[1] for l in open("/proc/meminfo") if l.startswith("MemAvailable")))//1024//1024
        if avail<10: print(f"[poc] WARN: only {avail}GB free (<10) — JVM may die (socket-closed). Kill leftover poly/ml_server.",flush=True)
    except Exception: pass
    JVMLOG=os.path.join(BRIDGE,"jvm.log")
    proc=subprocess.Popen([_java(),"-Xmx8g","-jar",JAR,str(PORT)],env=env,
                          stdout=open(JVMLOG,"w"),stderr=subprocess.STDOUT)
    time.sleep(12)
    gw=JavaGateway(gateway_parameters=GatewayParameters(port=PORT,auto_convert=True,read_timeout=900)); isa=gw.entry_point
    ok=False; last=""
    for _ in range(int(os.environ.get("INIT_RETRY","4"))):
        if proc.poll() is not None:   # JVM already exited -> the real socket-closed cause is in jvm.log
            print(f"[poc] JVM exited (code {proc.returncode}) — jvm.log tail:\n"+("".join(open(JVMLOG).readlines()[-6:])),flush=True); break
        try: ok,_=sr(isa._initializeRepl(THY,L4V,SESSION,[L4V]))
        except Exception as e: ok=False; last=str(e)[:120]
        if ok: break
        time.sleep(8)
    if not ok:
        print(f"[poc] init FAILED (last={last}). jvm.log tail:\n"+("".join(open(JVMLOG).readlines()[-6:]) if os.path.exists(JVMLOG) else "(no jvm.log)"),flush=True)
    assert ok,"init failed"; print("[poc] init ok",flush=True)
    steps=[c for c in isa._parse_to_steps(open(THY,encoding="utf-8").read()).split(SEP) if not isc(c)]
    idx=next(i for i,s in enumerate(steps) if ft(s) in ("lemma","theorem","corollary")
             and re.search(r"(?<![\w'])"+re.escape(LEMMA)+r"\s*[:\[]",s))
    u=""
    for s in rbs(steps[:idx])[1:]:
        r=isa._step_without_timeout(u+s); u=u+s+"\n" if "False"+SEP in r else ""
    sr(isa._step(steps[idx])); print(f"[poc] reached {LEMMA}",flush=True)
    idx2=next(i for i in range(idx+1,len(steps)) if ft(steps[i]) in ("lemma","lemmas","definition","theorem","corollary"))
    proof=steps[idx+1:idx2]
    AUTO=re.compile(r"\b(auto|force|fastforce|blast|clarsimp|simp|metis)\b")
    # walk to the target automation line; capture A (before it) + B_sig (after original)
    u=""; A_goal=None; B_sig=None
    for i,s in enumerate(proof):
        isa._clone_tls("A")            # A = state BEFORE the target tactic (pre-tactic)
        hit = ft(s) in ("apply","by") and AUTO.search(s) and (not TARGET or _norm(TARGET) in _norm(s))
        if hit:
            # A_goal: _extract_goal returns "ok<SEP>goal" — sr-parse so the goal is clean
            # (not "True<\SEP>..."). target_line captured for B_sig + context.
            isa._focus_tls("A"); A_goal=" ".join(sr(isa._extract_goal())[1].split())[:700]
            # capture B_sig on a CLONE so A stays pre-tactic (else `try` steps from a closed goal)
            isa._focus_tls("A"); isa._clone_tls("Bcap"); isa._focus_tls("Bcap")
            _,mb=sr(isa._step_without_timeout(s)); B_sig=signature(mb)
            isa._focus_tls("A")        # leave focus on the pristine pre-tactic A
            print(f"[poc] target line {i}: {s.strip()[:70]}",flush=True); break
        else:
            r=isa._step_without_timeout(u+s); u=u+s+"\n" if "False"+SEP in r else ""
    assert B_sig is not None,"no target automation line found"
    target=proof[i].strip(); statement=steps[idx].strip()
    # named facts in the original tactic (e.g. equiv_forE from `by (blast elim: equiv_forE)`)
    KWSET={"intro","dest","elim","simp","only","add","del","cong","split","where","OF","of","THEN","rule_format","simplified"}
    named=[t for grp in re.split(r"\b(?:intro|dest|elim|simp(?:\s+only)?|del|cong|split|add)!?:",target)[1:]
           for t in re.findall(r"[A-Za-z_][A-Za-z0-9_'.]*",grp) if t not in KWSET]
    # candidate constants whose `<c>_def` the original automation likely unfolds (markup
    # \<lbrakk> etc. stripped). We do NOT fetch def bodies via the REPL: `thm`/find_theorems
    # diagnostic output isn't returned by _step (same channel issue as simp_trace), so it
    # yields garbage. Instead we hand the agent the constant names + a grep hint — fetching
    # a definition from the l4v source is reliable and cheap.
    clean=re.sub(r"\\<[^>]*>"," ",statement+" "+A_goal)
    SKIP={"lemma","theorem","apply","and","the","def","equiv"}
    const_hints=[c for c in dict.fromkeys(re.findall(r"[a-z][a-z0-9_']{3,}",clean))
                 if not c.endswith("_def") and c not in SKIP][:8]
    state={"lemma":LEMMA,"statement":statement,"target":target,"A_goal":A_goal,
           "B_sig":list(B_sig),"B_desc":("close goal (0 subgoals)" if not B_sig else f"{len(B_sig)}-subgoal state:\n"+"\n".join(B_sig)[:800]),
           "named_facts_in_original":named,"const_hints":const_hints,
           "hints":["tactics must be full proof steps: `apply (<m> ...)` or `by (<m> ...)`",
                    "to unfold a definition use `simp only: <const>_def` (static); grep the l4v source for `definition <const>` if you need its body"]}
    json.dump(state, open(os.path.join(BRIDGE,"poc-state.json"),"w"), indent=1)
    print(f"[poc] READY. target={state['target'][:60]} B={state['B_desc'][:50]} named={state['named_facts_in_original']} consts={state['const_hints']}",flush=True)
    print(f"[poc] bridge={BRIDGE}",flush=True)

    BANNED=re.compile(r"\b(auto|blast|fastforce|force|clarsimp|eval|presburger|arith|linarith)\b")
    req=os.path.join(BRIDGE,"poc-req.json"); resp=os.path.join(BRIDGE,"poc-resp.json")
    nseq=[0]
    while True:
        if not os.path.exists(req): time.sleep(0.2); continue
        try: d=json.load(open(req))
        except Exception: os.remove(req); continue
        os.remove(req); cmd=d.get("cmd"); tac=(d.get("tactic") or "").strip()
        out={"cmd":cmd}
        if cmd=="stop": json.dump({"ok":True,"stopped":True},open(resp+".t","w")); os.replace(resp+".t",resp); break
        try:
            if cmd in ("goal","facts","state"):
                out.update(state)
            elif cmd=="record":           # machine-readable final result — RE-VALIDATED
                if not re.match(r"\s*(apply|by)\b",tac):
                    tac=("apply "+tac) if tac.startswith("(") else f"apply ({tac})"
                isa._focus_tls("A"); nseq[0]+=1; t=f"T{nseq[0]}"; isa._clone_tls(t); isa._focus_tls(t)
                try: rok,rm=sr(isa._step_with_30s_timeout(tac))
                except Exception as e: rok=False; rm=str(e)[:120]
                reached=rok and signature(rm)==tuple(state["B_sig"])
                static=not bool(BANNED.search(tac))
                json.dump({"lemma":LEMMA,"target":target,"final_rewrite":tac,
                           "verified_reached_B":reached,"verified_static":static},
                          open(os.path.join(BRIDGE,"poc-result.json"),"w"), ensure_ascii=False)
                out.update({"ok":True,"recorded":tac,"verified_reached_B":reached,"verified_static":static,
                            "warning":(None if reached else "this tactic did NOT reach B — not a valid rewrite")})
            elif cmd in ("try","commit"):
                # tolerate bare methods: wrap into a full proof step
                if not re.match(r"\s*(apply|by)\b",tac):
                    tac=("apply "+tac) if tac.startswith("(") else f"apply ({tac})"
                # ALWAYS work on a fresh clone t (so `t` is defined for both try and commit,
                # and `try` never consumes A). commit then promotes t to be the new A.
                isa._focus_tls("A"); nseq[0]+=1; t=f"T{nseq[0]}"; isa._clone_tls(t); isa._focus_tls(t)
                try: ok,m=sr(isa._step_with_30s_timeout(tac))
                except Exception as e: ok=False; m=f"server error: {str(e)[:120]}"
                if ok:
                    sig=signature(m); reached=(sig==tuple(state["B_sig"]))
                    out.update({"ok":True,"normalized_tactic":tac,"reached_B":reached,
                                "audit_static":not bool(BANNED.search(tac)),"ngoals":len(sig),"goal":goaltext(m)})
                    if cmd=="commit": isa._focus_tls(t); isa._clone_tls("A")  # advance A := result of t
                else:
                    out.update({"ok":False,"normalized_tactic":tac,
                                "error":m.strip().split(chr(10))[1][:160] if chr(10) in m.strip() else m.strip()[:160]})
            else: out.update({"ok":False,"error":f"unknown cmd {cmd}"})
        except Exception as e:
            out.update({"ok":False,"error":f"server exception: {str(e)[:120]}"})
        json.dump(out, open(resp+".t","w")); os.replace(resp+".t",resp)
    try: isa._exit()
    except Exception: pass
    proc.terminate(); proc.wait(); print("[poc] stopped",flush=True)

if __name__=="__main__": main()
