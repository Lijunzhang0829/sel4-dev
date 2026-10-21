#!/usr/bin/env python3
"""Repo-level LLM proof-engineering metrics (the SCOREBOARD for harness changes).

Problem setting: LLM proof engineering for large proof repositories. The question
is not per-lemma pass-rate but how the machine REUSES / ORGANIZES / GENERALIZES
proofs vs the human corpus. This computes metrics 2/3/4 statically:

  2 generality      — downstream reuse count of each helper (higher = more general)
  3 compositionality— per-proof local fan-out + support-lemmas-per-target
  4 dedup           — helpers that re-invent an existing import vs net-new-reusable

A harness change is only real if it moves these toward the human distribution,
not just the green count. Recompute after any change that touches proposal/plan.

Usage:
  python3 regen/legacy/mode_r/repo_metrics.py <locked.json> <human_theory.thy> [--arch ARM]

Caveat: human reuse is counted WITHIN the one theory → under-counts (human lemmas
are reused downstream); the true machine/human gap is LARGER than reported here.
"""

# Allow both python -m and direct script invocation.
if __package__ in {None, ""}:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import json, re, sys, os, statistics, argparse

TOP = re.compile(r'^(lemma|definition|defs|abbreviation|crunch|crunches|lemmas|'
                 r'method|instantiation|context|end|text|section|subsection|instance)\b')
LEM = re.compile(r'^lemma\s+([A-Za-z0-9_\']+)')
TOKEN = re.compile(r'\b([A-Za-z][A-Za-z0-9_\']+)\b')


def reuse_count(name, corpus, self_name):
    pat = re.compile(r'\b' + re.escape(name) + r'\b')
    return sum(1 for nm, t in corpus if nm != self_name and pat.search(t))


def parse_human(path):
    src = open(path).read().split('\n')
    starts = [(i, LEM.match(l).group(1)) for i, l in enumerate(src) if LEM.match(l)]

    def block(i):
        j = i + 1
        while j < len(src) and not TOP.match(src[j]):
            j += 1
        return '\n'.join(src[i:j])
    return {n: block(i) for i, n in starts}


def dist(d):
    vs = sorted(d.values())
    if not vs:
        return dict(n=0)
    one = sum(1 for v in vs if v == 0)
    return dict(n=len(vs), median=statistics.median(vs), mean=statistics.mean(vs),
                mx=max(vs), oneoff_pct=one / len(vs) * 100)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("locked")
    ap.add_argument("theory")
    ap.add_argument("--arch", default=os.environ.get("L4V_ARCH", "ARM"))
    args = ap.parse_args()
    os.environ.setdefault("L4V_ARCH", args.arch)

    locked = json.load(open(args.locked))
    items = [(e['item'].get('name'), e['item'].get('kind'), e['text']) for e in locked]
    mtexts = [(n, t) for n, k, t in items if n]
    mhelp = [(n, t) for n, k, t in items if k == 'helper' and n]
    mfixed = [n for n, k, t in items if k == 'fixed']
    local_names = {n for n, k, t in items if n}

    human = parse_human(args.theory)
    hnames = set(human)
    hcorres = [n for n in human if n.endswith('_corres')]
    hsupport = [n for n in human if not n.endswith('_corres')]
    htexts = [(n, human[n]) for n in human]

    # import index for dedup (name collision with an existing import = re-invention)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from regen.legacy.mode_r.regen_incr import build_import_index
    idx, consts = build_import_index(args.theory)
    IMP = set(idx) | set(consts)

    # --- metric 2: generality (reuse) ---
    mreuse = {n: reuse_count(n, mtexts, n) for n, t in mhelp}
    hreuse = {n: reuse_count(n, htexts, n) for n in hsupport}

    # --- metric 3: compositionality (local fan-out) ---
    def fanout(text, self_n, names):
        return len({m for m in TOKEN.findall(text) if m in names and m != self_n})
    m_fan = [fanout(t, n, local_names) for n, k, t in items if k == 'fixed']
    h_fan = [fanout(human[n], n, hnames) for n in hcorres]

    # --- metric 4: dedup ---
    mhn = [n for n, t in mhelp]
    shadow = [n for n in mhn if n in IMP]
    netnew = [n for n in mhn if n not in IMP and mreuse[n] >= 1]
    oneoff = [n for n in mhn if n not in IMP and mreuse[n] == 0]

    print(f"# repo-metrics  machine={args.locked}  human={args.theory}")
    print("\n[2] GENERALITY  (helper downstream reuse; higher=more general)")
    print(f"    machine helper : {dist(mreuse)}")
    print(f"    human  support : {dist(hreuse)}")
    print("\n[3] COMPOSITIONALITY  (per-proof local fan-out; higher=more decomposed)")
    print(f"    machine fixed fan-out: median={statistics.median(m_fan):.1f} "
          f"mean={statistics.mean(m_fan):.2f} max={max(m_fan)}")
    print(f"    human  corres fan-out: median={statistics.median(h_fan):.1f} "
          f"mean={statistics.mean(h_fan):.2f} max={max(h_fan)}")
    print(f"    support/target: machine {len(mhelp)}/{len(mfixed)}="
          f"{len(mhelp)/max(1,len(mfixed)):.2f}  "
          f"human {len(hsupport)}/{len(hcorres)}={len(hsupport)/max(1,len(hcorres)):.2f}")
    print("\n[4] DEDUP  (re-invention vs net-new-reusable)")
    print(f"    machine helpers: {len(mhn)}")
    print(f"    re-invent import (name collision): {len(shadow)}  {shadow[:6]}")
    print(f"    net-new-reusable (non-collision, reuse>=1): {len(netnew)}  {netnew}")
    print(f"    one-off scaffolding: {len(oneoff)}")
    verdict = "NEGATIVE (re-invent > net-new)" if len(shadow) > len(netnew) else "positive"
    print(f"    net reuse: {verdict}")
    print("\n  caveat: human reuse counted WITHIN this theory only → gap is UNDER-stated.")


if __name__ == "__main__":
    main()
