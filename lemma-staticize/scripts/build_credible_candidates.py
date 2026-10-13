#!/usr/bin/env python3
"""Build a CREDIBLE lemma-optimization candidate set.

Credibility rules (the whole point):
  - timing  : ONLY golden-build per-lemma elapsed from ranking.json, and ONLY for
              theories with coverage >= 0.9 (so the per-theory picture is complete).
              Low-coverage big files (TcbAcc_R cov=0.34, CNodeInv_R cov=0.02, ...)
              are EXCLUDED — their timing is partial and not trustworthy.
  - session : walls.json golden session cpu_s (coherent single build 2026-05-29).
  - search  : MEASURED search_frac from db_candidates_classified_v2 where available;
              otherwise a transparent heuristic proxy from the tactic + proof text,
              flagged via `sf_basis`.
  - pattern : flags the lemma against the PROVEN-closeable shapes we have ACCEPTed
              (def-unfold like sep_heap/rel_terminate; rule-based like ball_subsetE)
              vs simp-bound (which our tool has never closed).
Every row carries provenance + coverage so nothing is taken on faith.
"""
import json, re, os
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CAND = os.path.join(ROOT, "lemma-staticize", "candidates")
rank = json.load(open(os.path.join(CAND, "ranking.json")))
walls = json.load(open(os.path.join(ROOT, "reports/golden-baseline/walls.json")))["per_session"]
classified = {(c["file"].split("/l4v/")[-1], c.get("lemma_line")): c
              for c in json.load(open(os.path.join(CAND, "db_candidates_classified_v2.json")))}

WEIGHT = {"metis":1.0,"blast":1.0,"fast":1.0,"force":0.55,"fastforce":0.55,
          "auto":0.30,"safe":0.30,"clarsimp":0.20}

def profile(tac, proof):
    p = proof or ""
    defs = re.findall(r"\b\w+_def\b", p)
    nsimp = len(re.findall(r"simp(?:_all)?\s*(?:only)?\s*:", p))
    has_simp = nsimp > 0
    has_rule = bool(re.search(r"\b(dest|elim|intro)!?:", p))
    if 1 <= len(defs) <= 2 and tac in ("force","fastforce","auto") and nsimp <= 1:
        pat = "def-unfold(proven)"
    elif has_rule and not has_simp:
        pat = "rule-based(proven)"
    elif has_simp and len(defs) >= 3:
        pat = "simp-bound(hard)"
    else:
        pat = "mixed"
    w = WEIGHT.get((tac or "").strip(), 0.0)
    w = max(0.1, w - 0.05*nsimp)
    return pat, round(w, 2)

cands = []
for r in rank:
    if (r.get("coverage") or 0) < 0.9: continue
    if not r.get("matched"): continue
    el = r.get("elapsed_ms") or 0
    if el < 100: continue
    tac = (r.get("tactic") or "").strip()
    if tac not in WEIGHT: continue
    pat, w = profile(tac, r.get("proof"))
    meas = classified.get((r["thy"], r["line"]))
    sf = meas["search_frac"] if meas else None
    eff = sf if sf is not None else w
    cands.append({
        "thy": r["thy"], "lemma": r["lemma"], "session": r["session"], "line": r["line"],
        "tactic": tac, "proof": (r.get("proof") or "")[:90],
        "elapsed_ms": el, "search_frac": sf, "search_weight_proxy": w,
        "sf_basis": "measured" if meas else "proxy(heuristic)",
        "pattern": pat, "recoverable_ms": round(el*eff, 1),
        "coverage": r.get("coverage"), "session_cpu_s": walls.get(r["session"], {}).get("cpu_s"),
        "provenance": "golden-20260529 ranking.json(cov>=0.9) + walls.json",
    })
cands.sort(key=lambda c: -c["recoverable_ms"])
json.dump(cands, open(os.path.join(CAND, "credible_candidates.json"), "w"), indent=1, ensure_ascii=False)

print(f"credible candidate set: {len(cands)} lemmas (coverage>=0.9, elapsed>=100ms)")
print("pattern:", dict(Counter(c["pattern"] for c in cands)))
print("sf_basis:", dict(Counter(c["sf_basis"] for c in cands)))
hdr = f"{'elapsed':>8} {'recov':>7} {'sf':>6} {'sess':9} {'pattern':19} lemma"
print("\nTOP 24 by recoverable (= elapsed x search_ratio):")
print(hdr)
for c in cands[:24]:
    sf = (f"{c['search_frac']:.2f}" if c["search_frac"] is not None else f"~{c['search_weight_proxy']:.2f}")
    print(f"  {c['elapsed_ms']:6.0f}ms {c['recoverable_ms']:6.0f} {sf:>6} {c['session']:9} {c['pattern']:19} "
          f"{c['lemma'][:28]} ({c['thy'].split('/')[-1]})")
