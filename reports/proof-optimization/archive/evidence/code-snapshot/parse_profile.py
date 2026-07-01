#!/usr/bin/env python3
"""Parse PolyML ProfileTime output (Markup.ML_profiling) from an `isabelle process`
run of a `time_profile ‹m›` proof, and bucket ML functions into subsystems.

Input  : stdin or a file, the raw process output (may contain YXML control chars).
Output : per-subsystem CPU-sample share + top raw functions (auditable).

ProfileTime `count` = CPU sampler ticks per ML function (∝ CPU time). The headline
number is the SHARE each subsystem holds of the profiled tactic's CPU time. This
is the ground truth that validates v2's textual class for a lemma:
  - high `classical` share + low `simp`  -> genuinely search-dominated
  - high `simp` share                    -> simp/work-dominated (static-ization useless)
  - high `locale_setup`/`kernel`         -> setup/elaboration cost
"""
from __future__ import annotations
import re, sys, json

# function-name -> subsystem. Ordered: first matching rule wins. Substring match
# on the (possibly qualified) ML function name. Kept explicit + auditable; any
# unmapped function is reported under 'other' with its raw name so the map can grow.
RULES = [
    ("simp", ["Raw_Simplifier", "Simplifier", "Simpset", "simp_", "_simp",
               "rewrite", "bottomc", "Conv.", "Simproc", "mk_simp", "Cong",
               "Thm.eq_assumption", "norm_hhf"]),
    ("classical", ["Classical", "Clasimp", "Blast", "biresolution", "biresolve",
                    "bicompose", "Tactic.", "Tactical", "resolve_tac", "eresolve",
                    "dresolve", "forward_tac", "assume_tac", "search", "depth_tac",
                    "step_tac", "safe_step", "slow_step", "ares", "Goal_Display",
                    "RANGE", "biresolution"]),
    ("arith_decproc", ["Fast_Lin_Arith", "Lin_Arith", "Base_Order_Tac", "order_tac",
                        "Arith", "Groebner", "Presburger", "Cooper", "Numeral",
                        "Semiring", "Order_Tac", "Sat", "Argo"]),
    ("unify", ["Pattern", "Unify", "Envir", "unify", "_match", "Type.unif"]),
    ("split_datatype", ["Splitter", "Induct", "Old_Datatype", "split_tac",
                         "case_tac", "Case_", "Record.", "Datatype"]),
    ("type_infer", ["Sorts", "Type_Infer", "Type.", "Consts.", "Sign.", "Typedef"]),
    ("locale_setup", ["Locale", "Element", "Expression", "Morphism", "Named_Target",
                       "Generic_Target", "Proof_Context", "Context.", "Variable.",
                       "Assumption", "Interpretation"]),
    ("kernel", ["Thm.", "Proofterm", "Logic.", "Term.", "Drule.", "More_Thm",
                 "Theory.", "Name_Space", "Envir.norm", "Goal.", "Library.",
                 "Table()", "Net.", "Item_Net", "Ord_List"]),
    ("runtime_gc", ["GARBAGE COLLECTION", "Garbage", "GC", "RunCall", "PolyML.",
                     "Thread", "Signal", "Future", "Lazy.", "Multithreading"]),
]
# samples that carry no proof-work signal — excluded from the search/simp ratio
NONATTRIB = {"runtime_gc", "other"}


def classify_fn(name: str) -> str:
    for bucket, needles in RULES:
        for n in needles:
            if n in name:
                return bucket
    return "other"


# ML_Profiling.profile_time emits `tracing (YXML.string_of msg)` where each
# function is an XML element Markup.ML_profiling_entry {name, count}. In YXML the
# start tag is  \x05\x06 elem \x06 name=<FN> \x06 count=<N> \x05 ...  so the
# attribute pair is unambiguously delimited (robust vs the aligned text body).
X, Y = "\x05", "\x06"
YXML_ENTRY_RE = re.compile(Y + r"name=(.*?)" + Y + r"count=(\d+)" + X, re.DOTALL)
# fallback: the human text body line "   <count> <name>" after stripping ctrl
TEXT_ENTRY_RE = re.compile(r"^\s*(\d+)\s+([A-Za-z_<][\w.'()\[\]<> -]*?)\s*$")


def strip_ctrl(s: str) -> str:
    return "".join(c for c in s if c == "\n" or c == "\t" or ord(c) >= 32)


def parse_blocks(text: str):
    """Split the output into separate profile_time blocks (one per time_profile
    invocation, in execution order) and parse each independently."""
    # each block: from a `profile_time:` header to the next one (or EOF)
    parts = re.split(r"profile_time:", text)
    blocks = []
    for part in parts[1:]:  # skip preamble before first header
        ents = {}
        for m in YXML_ENTRY_RE.finditer(part):
            name = m.group(1).strip()
            if name and name != "TOTAL":
                ents[name] = ents.get(name, 0) + int(m.group(2))
        if ents:
            blocks.append((ents, sum(ents.values())))
    return blocks


def parse(text: str):
    entries: dict[str, int] = {}
    # primary: YXML markup attributes
    for m in YXML_ENTRY_RE.finditer(text):
        name = m.group(1).strip()
        if name and name != "TOTAL":
            entries[name] = entries.get(name, 0) + int(m.group(2))
    if entries:
        return entries, sum(entries.values())
    # fallback: parse the aligned text body within the profile_time block
    in_block = False
    for line in strip_ctrl(text).splitlines():
        if "profile_time:" in line:
            in_block = True
            continue
        if not in_block:
            continue
        mm = TEXT_ENTRY_RE.match(line)
        if not mm:
            continue
        if mm.group(2).strip() == "TOTAL":
            in_block = False
            continue
        entries[mm.group(2).strip()] = entries.get(mm.group(2).strip(), 0) + int(mm.group(1))
    return entries, sum(entries.values())


def report(entries, total, label=""):
    buckets: dict[str, int] = {}
    for name, c in entries.items():
        buckets[classify_fn(name)] = buckets.get(classify_fn(name), 0) + c
    order = ["classical", "simp", "arith_decproc", "unify", "split_datatype",
             "type_infer", "locale_setup", "kernel", "runtime_gc", "other"]
    hdr = f"total samples: {total}"
    if label:
        hdr = f"[{label}] " + hdr
    print(f"\n{hdr}\n{'subsystem':16s} {'samples':>9s} {'share':>7s}")
    print("-" * 36)
    for b in order:
        if b in buckets:
            print(f"{b:16s} {buckets[b]:9d} {100*buckets[b]/total:6.1f}%")
    attrib = sum(v for k, v in buckets.items() if k not in NONATTRIB) or 1
    simp_work = buckets.get("simp", 0) + buckets.get("arith_decproc", 0)
    cls = buckets.get("classical", 0) + buckets.get("unify", 0)
    setup = buckets.get("locale_setup", 0) + buckets.get("type_infer", 0)
    simp_r, cls_r, setup_r = simp_work / attrib, cls / attrib, setup / attrib
    print(f"(attributable: {attrib}/{total} = {100*attrib/total:.0f}%; GC+other excluded)")
    if cls_r >= 0.45 and cls_r >= 2 * simp_r:
        verdict = "SEARCH/CLASSICAL-DOMINATED"
    elif simp_r >= 0.45 and simp_r >= 2 * cls_r:
        verdict = "SIMP/WORK-DOMINATED"
    elif setup_r >= 0.40:
        verdict = "SETUP/LOCALE-DOMINATED"
    else:
        verdict = "MIXED (no single subsystem dominates)"
    print(f"VERDICT: {verdict}   classical+unify={100*cls_r:.1f}%  "
          f"simp+arith={100*simp_r:.1f}%  setup={100*setup_r:.1f}%")
    print("top 12 raw functions:")
    for name, c in sorted(entries.items(), key=lambda kv: -kv[1])[:12]:
        print(f"  {100*c/total:5.1f}%  [{classify_fn(name):14s}] {name}")
    return {"total": total, "buckets": buckets, "verdict": verdict,
            "top": sorted(entries.items(), key=lambda kv: -kv[1])[:30]}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    raw = open(args[0]).read() if args else sys.stdin.read()
    if "--per-block" in sys.argv:
        blocks = parse_blocks(raw)
        if not blocks:
            print("NO PROFILE DATA FOUND.", file=sys.stderr); sys.exit(2)
        print(f"=== {len(blocks)} profile_time block(s), in execution order ===")
        outs = [report(e, t, f"block {i+1}") for i, (e, t) in enumerate(blocks)]
        if "--json" in sys.argv:
            p = sys.argv[sys.argv.index("--json") + 1]
            json.dump(outs, open(p, "w"), indent=1); print(f"\nwrote {p}")
        return
    entries, total_reported = parse(raw)
    total = sum(entries.values())
    if total == 0:
        print("NO PROFILE DATA FOUND.", file=sys.stderr)
        print("(check the tactic ran under time_profile and tracing was captured)",
              file=sys.stderr)
        sys.exit(2)
    out = report(entries, total)
    if "--json" in sys.argv:
        path = sys.argv[sys.argv.index("--json") + 1]
        json.dump(out, open(path, "w"), indent=1)
        print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
