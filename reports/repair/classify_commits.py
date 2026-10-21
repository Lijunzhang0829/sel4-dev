#!/usr/bin/env python3
import sys, re
from collections import Counter, defaultdict

# path -> (category, is_proof, is_artifact, session_label)
def bucket(path):
    p = path
    if p.startswith('spec/abstract/'):   return ('ART:abstract', False, True, None)
    if p.startswith('spec/design/'):     return ('ART:design(hs->isa)', False, True, None)
    if p.startswith('spec/haskell/'):    return ('ART:haskell', False, True, None)
    if p.startswith('spec/cspec/'):      return ('ART:cspec(C)', False, True, None)
    if p.startswith('spec/machine/'):    return ('ART:machine', False, True, None)
    if p.startswith('spec/'):            return ('ART:other-spec', False, True, None)
    if p.startswith('proof/invariant-abstract/'): return ('PRF:AInvs', True, False, 'AInvs')
    if p.startswith('proof/refine/'):    return ('PRF:Refine', True, False, 'Refine')
    if p.startswith('proof/crefine/'):   return ('PRF:CRefine', True, False, 'CRefine')
    if p.startswith('proof/access-control/'): return ('PRF:Access', True, False, 'Access')
    if p.startswith('proof/infoflow/'):  return ('PRF:InfoFlow', True, False, 'InfoFlow')
    if p.startswith('proof/drefine/'):   return ('PRF:DRefine', True, False, 'DRefine')
    if p.startswith('proof/'):           return ('PRF:other', True, False, 'proof-other')
    if p.startswith('lib/'):             return ('LIB', False, False, None)
    return ('OTHER', False, False, None)

commits = []  # (hash, date, subj, [files])
cur = None
for line in open(sys.argv[1], encoding='utf-8', errors='replace'):
    line = line.rstrip('\n')
    if line.startswith('@@C@@\t'):
        if cur: commits.append(cur)
        _, h, d, s = line.split('\t', 3)
        cur = [h, d[:10], s, []]
    elif line.strip() and cur is not None:
        cur[3].append(line.strip())
if cur: commits.append(cur)

with_files = [c for c in commits if c[3]]
N = len(with_files)
print(f"commits analyzed (with file lists): {N}  (total records {len(commits)})")
if N: print(f"date span: {min(c[1] for c in with_files)} .. {max(c[1] for c in with_files)}")
print()

touches_proof = 0
touches_art = 0
proof_only = 0
art_only = 0
co_change = 0          # ART & PROOF same commit  == in-commit repair
cross_session = Counter()   # #distinct proof sessions among proof-touching commits
trigger_when_cochange = Counter()  # which artifact layers present in co-change commits
proof_session_hits = Counter()
fanout_files = []      # #proof files per proof-touching commit
fanout_sessions = []   # #proof sessions per proof-touching commit
msg_kw = Counter()
KW = ['follow','sync','port','update proof','fix proof','fixup','sorry','proof update','re-prove','reprove','adapt','broken']

for h,d,s,files in with_files:
    cats = [bucket(f) for f in files]
    proof_sess = set(c[3] for c in cats if c[1])
    arts = set(c[0] for c in cats if c[2])
    has_proof = len(proof_sess) > 0
    has_art = len(arts) > 0
    if has_proof: touches_proof += 1
    if has_art: touches_art += 1
    if has_proof and not has_art: proof_only += 1
    if has_art and not has_proof: art_only += 1
    if has_proof and has_art:
        co_change += 1
        for a in arts: trigger_when_cochange[a]+=1
    if has_proof:
        cross_session[len(proof_sess)] += 1
        for ps in proof_sess: proof_session_hits[ps]+=1
        fanout_files.append(sum(1 for c in cats if c[1]))
        fanout_sessions.append(len(proof_sess))
        sl = s.lower()
        for k in KW:
            if k in sl: msg_kw[k]+=1

def pct(x): return f"{100*x/N:5.1f}%"
print("=== TOP-LEVEL ===")
print(f"touch PROOF:        {touches_proof:5d}  {pct(touches_proof)}")
print(f"touch ARTIFACT:     {touches_art:5d}  {pct(touches_art)}")
print(f"proof-only:         {proof_only:5d}  {pct(proof_only)}   <- separate-commit repair candidates")
print(f"artifact-only:      {art_only:5d}  {pct(art_only)}   <- upstream change w/o same-commit proof fix")
print(f"CO-CHANGE (art+prf):{co_change:5d}  {pct(co_change)}   <- in-commit repair")
print()
print("=== among PROOF-touching commits: cross-session fan-out ===")
tp = touches_proof or 1
for k in sorted(cross_session):
    lbl = {1:'1 session (single-layer)'}.get(k, f'{k} sessions (CROSS-LAYER)')
    print(f"  {lbl:28s}: {cross_session[k]:5d}  {100*cross_session[k]/tp:5.1f}%")
print()
print("=== proof session hit counts (a commit may hit several) ===")
for s,c in proof_session_hits.most_common():
    print(f"  {s:16s}: {c:5d}")
print()
print("=== when co-change: which artifact layer changed ===")
for a,c in trigger_when_cochange.most_common():
    print(f"  {a:22s}: {c:5d}  {100*c/(co_change or 1):5.1f}% of co-change")
print()
print("=== fan-out (#proof files per proof-touching commit) ===")
import statistics as st
if fanout_files:
    fo=sorted(fanout_files)
    print(f"  min {fo[0]}  median {st.median(fo)}  p90 {fo[int(0.9*len(fo))-1]}  p99 {fo[int(0.99*len(fo))-1]}  max {fo[-1]}")
    buckets=Counter()
    for x in fanout_files:
        b = '1' if x==1 else '2-3' if x<=3 else '4-10' if x<=10 else '11-30' if x<=30 else '31+'
        buckets[b]+=1
    for b in ['1','2-3','4-10','11-30','31+']:
        print(f"    {b:6s}: {buckets[b]:5d}  {100*buckets[b]/tp:5.1f}%")
print()
print("=== commit-message keywords (proof-touching commits) ===")
for k,c in msg_kw.most_common():
    print(f"  {k:14s}: {c}")
