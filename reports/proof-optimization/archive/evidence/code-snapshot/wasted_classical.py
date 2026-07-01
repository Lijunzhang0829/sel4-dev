"""Select the simp-set-reduction sweet spot the two agents pointed to:
`auto/fastforce/force simp: <list>` where the classical machinery is likely WASTED
(no split:, no dest:/elim:/intro: => no real case-work or rule-chaining; the simp
does the work and auto only adds trivial safe-step/closing overhead), in files
SMALL enough to baseline cleanly under check-theory (the big CRefine/Refine files
time out or break imports).

NOT ranked by DB-elapsed (proven unreliable). Selection = text form + file size +
a MODERATE elapsed band (enough to matter, not load-bearing case-explosion).
The agent then ABLATES (auto->simp/clarsimp) to confirm the classical is wasted.
Run in container.
"""
import glob, os, sqlite3, re
HEAPS = glob.glob("/root/.isabelle/heaps/*/log")[0]; L4V="/workspace/verification/l4v"
AFF   = re.compile(r"\b(auto|fastforce|force)\b")
SIMPA = re.compile(r"\bsimp:")                      # has an explicit simp: list
SPLIT = re.compile(r"\bsplit:")
CLASR = re.compile(r"\b(dest!?:|elim!?:|intro!?:)")
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
    L=src.split("\n"); nlines=len(L)
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
    return o2l,txt,nlines
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
    if not (2.0 <= e["elapsed"] <= 60.0): continue        # moderate band
    if not os.path.exists(f): continue
    if f not in cache: cache[f]=mk(f)
    o2l,txt,nlines=cache[f]
    if nlines > 1600: continue                            # baseline-able size
    ln=o2l(off); t=re.sub(r"\s+"," ",txt(ln))
    if not (AFF.search(t) and SIMPA.search(t)): continue
    if SPLIT.search(t) or CLASR.search(t): continue       # those = real case/chain work
    n_simp = t.count(" ") if False else len(re.findall(r"\b\w+_def\b|\b\w+_simps?\b|\b\w+\b", t.split("simp:",1)[1])) if "simp:" in t else 0
    cand.append((nlines, e["elapsed"], f.split("l4v/")[-1], ln, t[:100]))
# sort: smaller file first (easier baseline), then higher elapsed
cand.sort(key=lambda c:(c[0], -c[1]))
print(f"WASTED-CLASSICAL candidates (auto/ff/force + simp:, NO split/dest/elim/intro, file<=1600L, 2-60s): {len(cand)}")
print(f"{'flines':>6} {'elapsed':>8}  file:line")
for nlines,el,rel,ln,t in cand[:25]:
    print(f"{nlines:6d} {el:8.1f}  {rel}:{ln}\n          {t}")
