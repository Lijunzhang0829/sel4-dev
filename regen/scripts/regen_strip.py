#!/usr/bin/env python3
"""regen_strip.py — turn a keepset + original theory into the regeneration
task spec (the kept skeleton the machine must reproduce and prove).

READ-ONLY on the l4v source. Emits, under the experiment dir:
  kept_skeleton.thy   the theory header + imports + KEPT statements verbatim
                      (CONTRACT + EXPORTED + KEPT_DEF), bodies removed,
                      PRIVATE interior removed. This is the *contract* the
                      machine output must satisfy (same statements present).
  task_spec.json      structured: theory name, imports, ordered kept decls
                      (name, kind, bucket, statement text), contracts list.

The machine (regen_agent.py) is asked to produce a COMPLETE theory that
contains every kept statement verbatim and proves it, inventing whatever
PRIVATE helpers it needs. kept_skeleton.thy is both the anti-cheat reference
(every CONTRACT/EXPORTED statement must reappear unchanged) and the human
comparison anchor.

Usage:
  regen_strip.py --keepset keepset.json --theory <orig.thy> --outdir <dir>
"""
import argparse, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from regen_extract import slice_decls  # reuse the exact slicer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keepset", required=True)
    ap.add_argument("--theory", required=True)
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()

    keep = json.load(open(args.keepset))
    src = open(args.theory, encoding="utf-8", errors="ignore").read()
    lines = src.splitlines()
    decls = slice_decls(src)

    rel = os.path.relpath(args.theory)
    ks_decls = keep["theories"].get(rel) or next(iter(keep["theories"].values()))
    bucket_by_start = {d["start"]: d["bucket"] for d in ks_decls}  # start is 1-based in keepset

    # theory header: everything before the first declaration (comments, theory
    # name, imports, begin)
    first_start = decls[0]["start"] if decls else len(lines)
    header = "\n".join(lines[:first_start])
    m = re.search(r'^theory\s+([A-Za-z0-9_\']+)', header, re.M)
    theory_name = m.group(1) if m else "UNKNOWN"
    mi = re.search(r'imports(.*?)begin', header, re.S)
    imports = " ".join(mi.group(1).split()) if mi else ""

    BODY_KINDS = ("lemma", "theorem", "corollary")
    to_prove, verbatim = [], []
    skeleton_parts = [header]
    for d in decls:
        b = bucket_by_start.get(d["start"] + 1)  # slicer 0-based, keepset 1-based
        if b not in ("CONTRACT", "EXPORTED", "KEPT_DEF"):
            continue
        full = "\n".join(lines[d["start"]:d["end"]]).rstrip()
        if d["kind"] in BODY_KINDS:
            stmt = d["stmt"].rstrip()
            to_prove.append(dict(name=d["name"], kind=d["kind"], bucket=b,
                                 statement=stmt))
            skeleton_parts += ["", stmt, "  sorry (* REGEN: body to reconstruct *)"]
        else:
            # definition / lemmas / declare / interpretation / ... : reproduce
            # the ENTIRE declaration verbatim (including any `by ...` proof).
            verbatim.append(dict(name=d["name"], kind=d["kind"], bucket=b,
                                 text=full))
            skeleton_parts += ["", full]
    skeleton_parts += ["", "end"]

    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, "kept_skeleton.thy"), "w") as f:
        f.write("\n".join(skeleton_parts) + "\n")

    spec = dict(theory_name=theory_name, imports=imports,
                orig_rel=rel,
                orig_lines=len(lines),
                to_prove=to_prove,
                verbatim=verbatim,
                contracts=[k for k in to_prove if k["bucket"] == "CONTRACT"],
                n_private_deleted=keep["tally"].get("PRIVATE", 0))
    with open(os.path.join(args.outdir, "task_spec.json"), "w") as f:
        json.dump(spec, f, indent=2)

    print(f"[regen_strip] theory={theory_name} imports=({imports})", file=sys.stderr)
    print(f"  kept: {len(spec['contracts'])} contracts, {len(to_prove)} lemmas to prove, "
          f"{len(verbatim)} verbatim decls ; deleted {spec['n_private_deleted']} private decls",
          file=sys.stderr)
    print(f"  -> {args.outdir}/kept_skeleton.thy , task_spec.json", file=sys.stderr)


if __name__ == "__main__":
    main()
