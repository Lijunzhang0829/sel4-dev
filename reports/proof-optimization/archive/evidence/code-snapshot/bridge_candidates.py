"""auto/fastforce/force commands that LEAN search (have dest:/elim:/intro: AND no
simp:/split: modifier) — the lines where a `by (rule bridge)` is most likely to
collapse a real combinatorial search. Ranked by DB-elapsed (caveat: parallel)."""
import glob, os, sqlite3, re
from collections import defaultdict
HEAPS = glob.glob("/root/.isabelle/heaps/*/log")[0]; L4V="/workspace/verification/l4v"
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
    L=src.split("\n")
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
    return o2l,txt
AFF=re.compile(r"\b(auto|fastforce|force)\b"); SEARCHARG=re.compile(r"\b(dest!?:|elim!?:|intro!?:)"); SIMPARG=re.compile(r"\b(simp:|simp add:|add:|split:|cong:)")
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
cand=[]; cache={}
for (f,off),e in cmds.items():
    if not os.path.exists(f): continue
    if f not in cache: cache[f]=mk(f)
    o2l,txt=cache[f]; ln=o2l(off); t=txt(ln)
    if AFF.search(t) and SEARCHARG.search(t) and not SIMPARG.search(t):
        cand.append((e["elapsed"],f.split("l4v/")[-1],ln,re.sub(r"\s+"," ",t)[:95]))
cand.sort(reverse=True)
print(f"search-leaning auto/fastforce/force (dest/elim/intro, NO simp:): {len(cand)} lines, sum={sum(c[0] for c in cand):.0f}s")
print(f"{'elapsed':>8}  file:line   text")
for el,rel,ln,t in cand[:20]:
    print(f"{el:8.1f}  {rel}:{ln}\n          {t}")
