"""Classify slow lemmas — v2, honest about what TEXT can and cannot resolve.

Input:  runs/db_candidates.json
Output: runs/db_candidates_classified_v2.json

Why v2 (see audit findings):
  v1 classed every `auto`/`fastforce`/`force` line with no `simp:` token as
  "search-dominated". But auto/fastforce/force ALWAYS run the full default
  simpset internally — "no simp: arg" does NOT mean "no simp work". v1's entire
  search-dominated bucket (15/15) was these simp-bearing methods; not one was a
  pure blast/metis. So v1's one discriminating bucket was systematically wrong.

The core honesty principle here:
  TEXT can reliably tell apart only the EXTREMES:
    - pure classical (blast/fast/safe/metis/meson/clarify, rule-chains)  -> search
    - pure rewriting (simp/simp_all, simp-only-args)                     -> simp
  TEXT *cannot* resolve auto/fastforce/force/clarsimp — these run BOTH the
  simpset and the classical reasoner. Their split is goal-dependent. So v2 puts
  them in an explicit AMBIGUOUS class with needs_profiling=True and only a *lean*
  (from dest:/elim:/intro: vs simp:/split: args), never a confident verdict.

Buckets:
  search-pure           genuinely classical, no simpset method
  simp-pure             genuinely rewriting, no classical method
  ambiguous-search-lean auto/fastforce/force, args lean search (dest/elim/intro, no simp:)  [needs profiling]
  ambiguous-simp-lean   auto/fastforce/force/clarsimp, args lean simp (simp:/split:)         [needs profiling]
  ambiguous-unknown     auto/fastforce/force bare, no discriminating args                    [needs profiling]
  wp-vcg                wp/wpsimp/ctac/cinit/vcg etc. dominated
  context-setup         locale/interpretation/datatype/instantiation elaboration
  structured            multi-family alternation/sequence — wholesale attribution invalid
"""

from __future__ import annotations
import json, re
from collections import defaultdict
from typing import Iterable

ROOT = "/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl"
IN_JSON = f"{ROOT}/runs/db_candidates.json"
OUT_JSON = f"{ROOT}/runs/db_candidates_classified_v2.json"

# --- method families (the actual subsystem each exercises) ---
CLASSICAL_PURE = ("blast", "fast", "safe", "clarify", "metis", "meson", "iprover")
SIMP_BEARING   = ("auto", "fastforce", "force")        # simpset + classical => AMBIGUOUS by text
PURE_SIMP      = ("simp", "simp_all", "simp_all_tac", "asm_full_simp", "asm_simp_tac")
CLARSIMP       = ("clarsimp",)                          # clarify + simp (simp-leaning)
WP_VCG         = ("wpsimp", "wp", "wpc", "wps", "vcg", "ctac", "cinit", "csymbr",
                  "ceqv", "sep_wp", "sep_cancel")
SETUP_HEAD_RE  = re.compile(
    r"^\s*(locale|interpretation|sublocale|context|instantiation|instance|datatype|"
    r"record|primrec|fun|function|termination|named_theorems|bundle|notation|no_notation)\b")

SIMP_ARG_RE    = re.compile(r"\b(simp:|simp add:|add:|split:|split del:|split_del:|cong:|del:)")
SEARCH_ARG_RE  = re.compile(r"\b(dest:|dest!:|elim:|elim!:|intro:|intro!:|rule:)")

def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()

def _word(tok: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(tok) + r"(?![A-Za-z0-9_])")

def _families_in(text: str) -> set[str]:
    """Which method families appear anywhere in the command text."""
    fams = set()
    for fam, toks in (("classical", CLASSICAL_PURE), ("simp_bearing", SIMP_BEARING),
                      ("simp", PURE_SIMP), ("clarsimp", CLARSIMP), ("wp_vcg", WP_VCG)):
        if any(_word(t).search(text) for t in toks):
            fams.add(fam)
    return fams

def _n_alternatives(text: str) -> int:
    """Crude: number of top-ish-level | alternatives (combinator method)."""
    return text.count("|")

def _classify_cmd(name: str, text: str) -> tuple[str, str]:
    """Return (bucket, lean_note) for ONE command, by text only."""
    t = _norm(text)
    if SETUP_HEAD_RE.match(t):
        return "context-setup", "setup-head"

    fams = _families_in(t)
    has_simp_arg = bool(SIMP_ARG_RE.search(t))
    has_search_arg = bool(SEARCH_ARG_RE.search(t))
    n_alt = _n_alternatives(t)

    # multi-family alternation/sequence: wholesale attribution is invalid -> structured
    if n_alt >= 2 and len(fams) >= 2:
        return "structured", f"alternation x{n_alt}, families={sorted(fams)}"

    # wp/vcg dominated (only if no heavier search/simp method present)
    if "wp_vcg" in fams and not (fams & {"classical", "simp_bearing", "simp", "clarsimp"}):
        return "wp-vcg", "wp/vcg method"

    # pure classical, NO simpset method at all -> the ONLY confident search bucket
    if "classical" in fams and not (fams & {"simp_bearing", "simp", "clarsimp"}):
        return "search-pure", "classical-only (blast/metis/fast/safe)"

    # pure simp, NO classical method at all
    if (fams & {"simp", "clarsimp"}) and not (fams & {"classical", "simp_bearing"}):
        # clarsimp has a small classical clarify component but is simp-leaning
        return "simp-pure", "simp/clarsimp-only"

    # simp-bearing classical (auto/fastforce/force): TEXT CANNOT RESOLVE -> ambiguous
    if "simp_bearing" in fams:
        if has_simp_arg and not has_search_arg:
            return "ambiguous-simp-lean", "auto/ff/force + simp:/split: args"
        if has_search_arg and not has_simp_arg:
            return "ambiguous-search-lean", "auto/ff/force + dest:/elim:/intro: args"
        if has_search_arg and has_simp_arg:
            return "ambiguous-unknown", "auto/ff/force + both arg kinds"
        return "ambiguous-unknown", "auto/ff/force bare (no discriminating args)"

    # classical method co-occurs with simp method but not via auto/ff/force
    if "classical" in fams and (fams & {"simp", "clarsimp"}):
        return "structured", "classical + simp methods combined"

    return "other", "no recognized method"

def _classify_lemma(total_s: float, by_bucket: dict[str, float]) -> tuple[str, float, str, bool]:
    if total_s <= 0:
        return "empty", 0.0, "empty-total", False
    # dominant bucket by elapsed
    dom, dom_s = max(by_bucket.items(), key=lambda kv: kv[1])
    share = dom_s / total_s
    needs_prof = dom.startswith("ambiguous")
    return dom, round(share, 3), f"dominant={dom} share={share:.2f}", needs_prof

def main() -> None:
    rows = json.load(open(IN_JSON))
    out = []
    summary = defaultdict(lambda: {"count": 0, "total_s": 0.0})
    needs_prof_total = {"count": 0, "total_s": 0.0}

    for row in rows:
        total_s = float(row.get("total_s", 0.0))
        by_bucket: dict[str, float] = defaultdict(float)
        parsed = []
        for line, name, elapsed_s, text in row.get("cmds", []):
            bucket, note = _classify_cmd(str(name), str(text))
            by_bucket[bucket] += float(elapsed_s)
            parsed.append({"line": int(line), "name": str(name),
                           "elapsed_s": round(float(elapsed_s), 3),
                           "bucket": bucket, "note": note,
                           "text": _norm(str(text))[:200]})

        klass, share, reason, needs_prof = _classify_lemma(total_s, dict(by_bucket))
        enriched = {k: row[k] for k in ("file", "lemma_line", "name_at_line",
                                         "total_s", "search_frac") if k in row}
        enriched["class_v2"] = klass
        enriched["dominant_share"] = share
        enriched["needs_profiling"] = needs_prof
        enriched["reason"] = reason
        enriched["bucket_s"] = {k: round(v, 3) for k, v in sorted(by_bucket.items(), key=lambda kv: -kv[1])}
        enriched["hot_cmds"] = sorted(parsed, key=lambda c: -c["elapsed_s"])[:3]
        out.append(enriched)

        summary[klass]["count"] += 1
        summary[klass]["total_s"] += total_s
        if needs_prof:
            needs_prof_total["count"] += 1
            needs_prof_total["total_s"] += total_s

    out.sort(key=lambda r: (r["class_v2"], -float(r.get("total_s", 0.0))))
    json.dump(out, open(OUT_JSON, "w"), ensure_ascii=False, indent=1)

    print(f"wrote {OUT_JSON}  ({len(out)} lemmas)\n")
    print(f"{'class':24s} {'count':>5s} {'total_s':>9s}")
    for k in sorted(summary):
        print(f"{k:24s} {summary[k]['count']:5d} {summary[k]['total_s']:9.1f}")
    print(f"\n** needs_profiling (ambiguous) : count={needs_prof_total['count']}  "
          f"total_s={needs_prof_total['total_s']:.1f} **")
    print("   ^ these are the lemmas TEXT cannot resolve search-vs-simp; the real target pool.")

if __name__ == "__main__":
    main()
