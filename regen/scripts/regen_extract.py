#!/usr/bin/env python3
"""regen_extract.py — contract-surface / interior extractor for a regen scope.

READ-ONLY. Given a set of Refine theory files (one subsystem scope) and the
whole Refine directory, partition every top-level declaration into three
buckets and emit a keepset:

  CONTRACT  top-level corres/ccorres refinement obligation  -> KEEP statement, regen body  (the oracle)
  EXPORTED  cited by NAME outside the scope, OR globally attributed [wp]/[simp]/...
                                                             -> KEEP statement, regen body  (interface)
  PRIVATE   used only within the scope                       -> DELETE entirely, regen      (interior)

Reference-closure: any definition referenced by a KEPT statement is itself KEPT.

Output: keepset.json (machine) + a human summary on stderr. Never writes to the
l4v source tree.

Usage:
  regen_extract.py --refine <ARM dir> --out keepset.json <theory.thy> [<theory2.thy> ...]
"""
import argparse, glob, json, os, re, sys

# Top-level Isabelle declaration keywords we care to slice on (column 0).
DECL_KW = (r'lemma|theorem|corollary|lemmas|definition|defs|abbreviation|primrec|'
           r'fun|function|inductive|inductive_set|coinductive|coinductive_set|'
           r'interpretation|global_interpretation|sublocale|'
           r'instantiation|instance|schematic_goal|named_theorems|declare|'
           r'crunch|crunches|record|datatype|type_synonym|axiomatization')
DECL_RE = re.compile(r'^(' + DECL_KW + r')\b(.*)$')
# name capture for the named kinds. `defs NAME_def:` writes the fact name on the
# `defs` line before a colon — capture it so the def can be reference-closed/kept.
NAME_RE = re.compile(r'^(lemma|theorem|corollary|lemmas|definition|defs|abbreviation|'
                     r'primrec|fun|function|inductive|inductive_set|coinductive|'
                     r'coinductive_set|schematic_goal|record|datatype|type_synonym)\s+'
                     r'([A-Za-z0-9_\']+)\s*(\[[^\]]*\])?')
PROOF_RE = re.compile(r'^\s*(by|apply|using|unfolding|proof|supply|done|qed|\.\.|\.|sorry|oops)\b')
ATTR_RE = re.compile(r'\b(wp|simp|corres|corresK|intro|dest|elim|cong|split|iff|code)\b')
# A contract = statement mentions corres/ccorres as an obligation, but NOT merely
# the corres_underlying/corres_inst plumbing predicates.
CORRES_RE = re.compile(r'\b(corres|ccorres|corresK)\b')
PLUMBING_RE = re.compile(r'\bcorres_(underlying|inst|rrel|cross|noop|split|rel_imp)\b')
# Ground-truth contracts follow the operation-name-first convention. Generic
# proof rules such as `corres_split` and `corres_cap_fault` are human proof
# decomposition and must not leak into the fixed oracle merely because their
# statements mention corres.
OP_CONTRACT_NAME_RE = re.compile(r'_(?:corres|ccorres|corresK)\'?$')


def slice_decls(src):
    """Return list of dicts: {kind,name,attr,start,end,stmt} (0-based line span)."""
    lines = src.splitlines()
    starts = []
    for i, ln in enumerate(lines):
        m = DECL_RE.match(ln)
        if m:
            starts.append(i)
    decls = []
    for idx, s in enumerate(starts):
        e = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        head = "\n".join(lines[s:e])
        km = DECL_RE.match(lines[s])
        kind = km.group(1)
        nm = NAME_RE.match(lines[s])
        name = nm.group(2) if nm else None
        attr = (nm.group(3) if nm else "") or ""
        # defs often write the name on a later line: `definition\n "NAME \<equiv> ..."`
        # or `definition NAME :: ty where`. Recover it so reference-closure works.
        if name is None and kind in ("definition", "abbreviation", "fun",
                                     "primrec", "function", "inductive",
                                     "inductive_set", "coinductive", "coinductive_set",
                                     "record", "datatype", "type_synonym"):
            dm = (re.search(r'\b(?:definition|abbreviation|fun|primrec|function|record|'
                                r'datatype|type_synonym|inductive|inductive_set|'
                                r'coinductive|coinductive_set)\s+'
                                r'([A-Za-z][A-Za-z0-9_\']*)'
                                r'(?=\s|::|=|\[)', head)
                  or re.search(
                      r'"\s*([A-Za-z][A-Za-z0-9_\']*)'
                      r'(?=\s|::|=|\\<equiv>)',
                      head,
                  ))
            if dm:
                name = dm.group(1)
        # statement text = decl start until first proof-command line
        stmt = []
        for ln in lines[s:e]:
            if PROOF_RE.match(ln):
                break
            stmt.append(ln)
        decls.append(dict(kind=kind, name=name, attr=attr,
                          start=s, end=e, stmt="\n".join(stmt)))
    return decls


def is_contract(d):
    # only a real goal-bearing lemma/theorem can be a refinement obligation;
    # `lemmas`/`declare` aliases are plumbing, never contracts.
    if d["kind"] not in ("lemma", "theorem", "corollary"):
        return False
    name = d.get("name") or ""
    if not OP_CONTRACT_NAME_RE.search(name) or name.startswith(("corres_", "ccorres_")):
        return False
    st = d["stmt"]
    if not CORRES_RE.search(st):
        return False
    # drop pure plumbing lemmas about the corres framework itself
    if PLUMBING_RE.search(st) and not re.search(r'\bcorres\s+\S', st):
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refine", required=True, help="Refine ARM dir (for outside-scope citation)")
    ap.add_argument("--out", required=True)
    ap.add_argument("theories", nargs="+")
    args = ap.parse_args()

    scope_abs = {os.path.abspath(t) for t in args.theories}
    all_thys = glob.glob(os.path.join(args.refine, "*.thy"))
    outside = "\n".join(open(p, encoding="utf-8", errors="ignore").read()
                        for p in all_thys if os.path.abspath(p) not in scope_abs)

    scope = {}
    for t in args.theories:
        scope[os.path.abspath(t)] = open(t, encoding="utf-8", errors="ignore").read()

    # collect all names & defs in scope for reference-closure
    per_theory = {}
    all_scope_defs = {}   # name -> theory abspath (definition/abbreviation)
    for path, src in scope.items():
        decls = slice_decls(src)
        per_theory[path] = decls
        for d in decls:
            if d["name"] and d["kind"] in ("definition", "abbreviation", "fun",
                                           "primrec", "function"):
                all_scope_defs[d["name"]] = path

    # first pass: classify contract / exported / private (definitions default private)
    def classify(d):
        if is_contract(d):
            return "CONTRACT"
        nm = d["name"]
        attr = d["attr"]
        exported = bool(ATTR_RE.search(attr))
        if nm and re.search(r'\b' + re.escape(nm) + r'\b', outside):
            exported = True
        return "EXPORTED" if exported else "PRIVATE"

    buckets = {}
    for path, decls in per_theory.items():
        for d in decls:
            d["bucket"] = classify(d)

    # reference-closure: any scope-def referenced by a KEPT statement -> KEEP (EXPORTED)
    kept_stmt_text = "\n".join(
        d["stmt"] for decls in per_theory.values() for d in decls
        if d["bucket"] in ("CONTRACT", "EXPORTED"))
    for path, decls in per_theory.items():
        for d in decls:
            if d["bucket"] == "PRIVATE" and d["name"] in all_scope_defs \
               and d["kind"] in ("definition", "abbreviation", "fun", "primrec", "function") \
               and re.search(r'\b' + re.escape(d["name"]) + r'\b', kept_stmt_text):
                d["bucket"] = "KEPT_DEF"
            # `defs NAME_def: "c s ≡ ..."` gives the DEFINITIONAL body of an
            # already-declared (design-spec `consts'`) constant. It is a definition,
            # not a provable obligation — the machine cannot regenerate it, and a
            # kept concrete op that mentions the constant is un-dischargeable without
            # it (measured: setCurThread asserts `idleThreadNotQueued`, whose
            # `defs idleThreadNotQueued_def` was deleted → the proof hits "Undefined
            # fact" / a fastforce search-explosion trying to reason about an opaque
            # predicate). Keep every `defs` verbatim, like the code it defines.
            elif d["kind"] == "defs":
                d["bucket"] = "KEPT_DEF"

    # emit
    result = {"scope": [os.path.relpath(t) for t in args.theories],
              "theories": {}}
    tally = dict(CONTRACT=0, EXPORTED=0, KEPT_DEF=0, PRIVATE=0)
    loc = dict(CONTRACT=0, EXPORTED=0, KEPT_DEF=0, PRIVATE=0)
    for path, decls in per_theory.items():
        rel = os.path.relpath(path)
        out_decls = []
        for d in decls:
            b = d["bucket"]
            tally[b] = tally.get(b, 0) + 1
            loc[b] = loc.get(b, 0) + (d["end"] - d["start"])
            out_decls.append(dict(kind=d["kind"], name=d["name"], attr=d["attr"],
                                  start=d["start"] + 1, end=d["end"],
                                  bucket=b,
                                  stmt_lines=d["stmt"].count("\n") + 1))
        result["theories"][rel] = out_decls
    result["tally"] = tally
    result["loc"] = loc
    kept = tally["CONTRACT"] + tally["EXPORTED"] + tally["KEPT_DEF"]
    regen_struct = tally["PRIVATE"]
    interior_pct = loc["PRIVATE"] / max(1, sum(loc.values()))
    result["summary"] = dict(kept_decls=kept, regen_private_decls=regen_struct,
                             contracts=tally["CONTRACT"],
                             interior_loc_pct=round(interior_pct, 3))

    with open(args.out, "w") as f:
        json.dump(result, f, indent=2)

    print(f"[regen_extract] scope = {result['scope']}", file=sys.stderr)
    print(f"  CONTRACT {tally['CONTRACT']:4d} ({loc['CONTRACT']:5d} loc)  <- oracle surface (keep stmt)",
          file=sys.stderr)
    print(f"  EXPORTED {tally['EXPORTED']:4d} ({loc['EXPORTED']:5d} loc)  <- interface (keep stmt)",
          file=sys.stderr)
    print(f"  KEPT_DEF {tally['KEPT_DEF']:4d} ({loc['KEPT_DEF']:5d} loc)  <- defs referenced by kept stmts",
          file=sys.stderr)
    print(f"  PRIVATE  {tally['PRIVATE']:4d} ({loc['PRIVATE']:5d} loc)  <- INTERIOR (delete+regen structure)",
          file=sys.stderr)
    print(f"  interior structure = {interior_pct:.0%} LOC ; oracle = {tally['CONTRACT']} contracts",
          file=sys.stderr)
    print(f"  -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
