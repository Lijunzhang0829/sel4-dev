"""Round-2 heuristics for proof-wall local wins, beyond the exhausted
'auto/ff/force + simp:, no split/classical-args' pool.

H1 (split-bearing): auto|fastforce|force WITH split: (and maybe simp:) but NO
   dest/elim/intro. The split: is the real case-work; fastforce's classical SEARCH
   on top may be wasted. Recipe: replace the keyword with `clarsimp` (keep
   split:/simp:). NEW pool — split: lines were excluded in round 1.
H2 (clarify-wasted): clarsimp simp: <list>, NO dest/elim/intro/split. If the goal
   has no premise structure to clarify, `simp` alone suffices and clarify is waste.
   Recipe: clarsimp simp: X -> simp add: X.

File<=1600L (baseline-able), elapsed 4-100s. NOT ranked by DB-elapsed precision.
"""
import glob, os, sqlite3, re
HEAPS=glob.glob("/root/.isabelle/heaps/*/log")[0]; L4V="/workspace/verification/l4v"
AFF=re.compile(r"\b(auto|fastforce|force)\b"); CLAR=re.compile(r"\bclarsimp\b")
SPLIT=re.compile(r"\bsplit:"); SIMPA=re.compile(r"\bsimp:")
CLASR=re.compile(r"\b(dest!?:|elim!?:|intro!?:)")
def decompress(b):
    b=bytes(b)
    try:
        import zstandard as z; return z.ZstdDecompressor().decompress(b)
    except Exception:
        import subprocess; return subprocess.run(["zstd","-dc"],input=b,capture_output=True).stdout
def parse(d):
    cur={}
    for tok in d.decode("utf-8","replace").split("\x06"):
        tok=tok.replace("\x05","")
        if "=" not in tok: continue
        k,_,v=tok.partition("="); k=k.strip()
        if k in ("name","offset","file","elapsed"):
            cur[k]=v
            if k=="elapsed" and "file" in cur and "offset" in cur:
                yield {"name":cur.get("name",""),"offset":int(cur["offset"]),"file":cur["file"],"elapsed":float(cur["elapsed"])}; cur={}
def mk(path):
    src=open(path,encoding="utf-8",errors="replace").read(); st=[0]; sym=0; i=0; n=len(src)
    while i<n:
        c=src[i]
        if c=="\\" and i+1<n and src[i+1]=="<":
            j=src.find(">",i)
            if j!=-1: i=j+1; sym+=1; continue
        if c=="\n": st.append(sym+1)
        sym+=1; i+=1
    L=src.split("\n"); nl=len(L)
    def o2l(o):
        lo,hi=0,len(st)-1
        while lo<hi:
            m=(lo+hi+1)//2
            if st[m]<=o: lo=m
            else: hi=m-1
        return lo+1
    def txt(ln):
        b=L[ln-1] if ln-1<len(L) else ""; d=b.count("(")-b.count(")"); k=ln
        while d>0 and k<len(L) and k-ln<8: b+=" "+L[k]; d+=L[k].count("(")-L[k].count(")"); k+=1
        return b
    return o2l,txt,nl
cmds={}
for db in sorted(glob.glob(os.path.join(HEAPS,"*.db"))):
    if "SCALA_ISABELLE_TEMP" in db: continue
    try:
        con=sqlite3.connect(db); row=con.execute("SELECT command_timings FROM isabelle_session_info").fetchone()
    except Exception: continue
    if not row or row[0] is None: continue
    for e in parse(decompress(row[0])):
        if not e["file"].startswith(L4V) or e["name"] not in ("by","apply"): continue
        k=(e["file"],e["offset"])
        if k not in cmds or e["elapsed"]>cmds[k]["elapsed"]: cmds[k]=e
H1=[]; H2=[]; cache={}
for (f,off),e in cmds.items():
    if not (4.0<=e["elapsed"]<=100.0) or not os.path.exists(f): continue
    if f not in cache: cache[f]=mk(f)
    o2l,txt,nl=cache[f]
    if nl>1600: continue
    ln=o2l(off); t=re.sub(r"\s+"," ",txt(ln))
    if CLASR.search(t): continue                       # both pools exclude dest/elim/intro
    if AFF.search(t) and SPLIT.search(t):              # H1
        H1.append((nl,e["elapsed"],f.split("l4v/")[-1],ln,t[:105]))
    elif CLAR.search(t) and SIMPA.search(t) and not SPLIT.search(t) and not AFF.search(t):  # H2
        H2.append((nl,e["elapsed"],f.split("l4v/")[-1],ln,t[:105]))
for label,pool in [("H1 split-bearing (ff/auto/force + split:, -> clarsimp)",H1),
                   ("H2 clarify-wasted (clarsimp simp: -> simp add:)",H2)]:
    pool.sort(key=lambda c:(c[0],-c[1]))
    print(f"\n==== {label}: {len(pool)} ====")
    for nl,el,rel,ln,t in pool[:18]:
        print(f"{nl:5d}L {el:6.1f}s  {rel}:{ln}\n           {t}")
