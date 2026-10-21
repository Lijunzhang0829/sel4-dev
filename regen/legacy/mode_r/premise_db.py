#!/usr/bin/env python3
"""Premise database for regen — a proven-lemma library with recall.

Each record = {name, stmt, theory, file, kind}. Built from the ANCESTOR import
closure of a target theory (the scope of facts ACTUALLY available when proving
it — same scope as build_import_index, but here we ALSO keep the file and, unlike
build_import_index's `setdefault`, KEEP EVERY same-named entry so collisions are
visible, not silently dropped).

Recall:
  - by NAME (fast, exact + fuzzy) — the reuse/dedup gate: before the model invents
    a helper, check whether a lemma of that (or a near) name already exists. Catches
    re-invention (14/65 helpers re-created imports), hallucinated names
    (tcbQueueAppend_corres), and misnamed/shadow lemmas (a real name reused with a
    DIVERGENT statement — the tcbQueueHead_iff_tcbQueueEnd / ready_qs_runnable_cross
    cases). The statement disambiguates; the file resolves same-name collisions.
  - by GOAL (const/token overlap over statements) — premise retrieval: surface the
    library lemmas most relevant to a goal, to be fed to metis/fastforce.

Usage:
  python3 regen/legacy/mode_r/premise_db.py build <target.thy>     # build + write JSON + stats
  python3 regen/legacy/mode_r/premise_db.py name  <target.thy> <lemma_name>
  python3 regen/legacy/mode_r/premise_db.py goal  <target.thy> "<goal fragment>"
"""

# Allow both python -m and direct script invocation.
if __package__ in {None, ""}:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import os, re, sys, json, difflib
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DB_DIR = REPO / "reports/regen"


def _l4v():
    return REPO / "verification/l4v"


def _closure_files(target_path):
    """BFS the ancestor import closure of target_path; return reachable .thy files
    (arch-isolated, downstream excluded) — mirrors build_import_index."""
    l4v = _l4v()
    arch = os.environ.get("L4V_ARCH", "ARM")
    other = {"ARM", "ARM_HYP", "RISCV64", "X64", "AARCH64"} - {arch}

    def wrong_arch(root):
        return any(f"/{a}/" in root + "/" or root.endswith(f"/{a}") for a in other)

    name2path = {}
    for root, _, files in os.walk(l4v):
        if any(seg in root for seg in ("/.git", "/autocorres/tests", "/tools")):
            continue
        if wrong_arch(root):
            continue
        for f in files:
            if f.endswith(".thy"):
                cur = name2path.get(f[:-4])
                if cur is None or (f"/{arch}/" in os.path.join(root, f)
                                   and f"/{arch}/" not in cur):
                    name2path[f[:-4]] = os.path.join(root, f)

    def imports_of(path):
        try:
            src = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            return []
        m = re.search(r'imports(.*?)begin', src, re.S)
        return [t.split(".")[-1] for t in
                re.findall(r'"?([A-Za-z][A-Za-z0-9_./]*)"?', m.group(1))] if m else []

    seen, queue, reachable = set(), [Path(target_path).stem], []
    while queue and len(reachable) < 2000:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        p = name2path.get(name.split("/")[-1])
        if not p:
            continue
        reachable.append(p)
        for imp in imports_of(p):
            if imp not in seen:
                queue.append(imp)
    return [p for p in reachable
            if os.path.abspath(p) != os.path.abspath(target_path)]


def build(target_path, write=True):
    """Return (records, meta). records: list of {name, stmt, theory, file, kind}."""

    from regen.preparation.regen_extract import slice_decls
    records = []
    for p in _closure_files(target_path):
        rel = os.path.relpath(p, REPO)
        theory = Path(p).stem
        src = open(p, encoding="utf-8", errors="ignore").read()
        for d in slice_decls(src):
            if d["name"] and d["kind"] in ("lemma", "theorem", "corollary", "lemmas"):
                records.append({
                    "name": d["name"],
                    "stmt": " ".join(d["stmt"].split())[:300],
                    "theory": theory,
                    "file": rel,
                    "kind": d["kind"],
                })
    # collision report: same name across >1 file
    from collections import defaultdict
    byname = defaultdict(list)
    for r in records:
        byname[r["name"]].append(r)
    collisions = {n: rs for n, rs in byname.items() if len({r["file"] for r in rs}) > 1}
    meta = {"target": os.path.relpath(target_path, REPO),
            "arch": os.environ.get("L4V_ARCH", "ARM"),
            "n_records": len(records),
            "n_unique_names": len(byname),
            "n_collision_names": len(collisions)}
    if write:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        out = DB_DIR / f"premise_db-{Path(target_path).stem}.json"
        json.dump({"meta": meta, "records": records}, open(out, "w"),
                  ensure_ascii=False, indent=1)
        meta["path"] = str(out)
    return records, meta, collisions


def index_by_name(records):
    from collections import defaultdict
    idx = defaultdict(list)
    for r in records:
        idx[r["name"]].append(r)
    return idx


# ---- recall -----------------------------------------------------------------
def recall_name(records, name, k=6, cutoff=0.72):
    """Reuse/dedup gate. Returns {exact:[...], fuzzy:[...]}.
    exact = library lemmas with THIS name (>1 ⇒ collision, file disambiguates).
    fuzzy = near-named lemmas (catches misname / re-invention / hallucination)."""
    idx = index_by_name(records)
    exact = idx.get(name, [])
    names = [n for n in idx if n != name]
    near = difflib.get_close_matches(name, names, n=k, cutoff=cutoff)
    fuzzy = [idx[n][0] for n in near]
    return {"exact": exact, "fuzzy": fuzzy}


_TOK = re.compile(r"[A-Za-z_][A-Za-z0-9_']{3,}")


def recall_goal(records, goal, k=12):
    """Premise retrieval by statement relevance (token overlap, name weighted 2x)."""
    toks = set(_TOK.findall(goal))
    scored = []
    for r in records:
        s = (2 * sum(1 for t in toks if t in r["name"])
             + sum(1 for t in toks if t in r["stmt"]))
        if s:
            scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:k]]


def _main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    tp = sys.argv[2]
    if not os.path.isabs(tp) and not os.path.exists(tp):
        tp = str(REPO / tp)

    records, meta, collisions = build(tp, write=(cmd == "build"))
    if cmd == "build":
        print(json.dumps(meta, ensure_ascii=False, indent=1))
        print(f"\n同名跨文件冲突(file 字段解决的正是这个): {len(collisions)} 组")
        for n, rs in list(collisions.items())[:8]:
            print(f"  {n}: " + " | ".join(sorted({r['theory'] for r in rs})))
    elif cmd == "name":
        res = recall_name(records, sys.argv[3])
        print(f"=== 精确命中 {len(res['exact'])} ===")
        for r in res["exact"]:
            print(f"  [{r['theory']}] {r['name']}: {r['stmt'][:120]}")
        print(f"=== 近名(fuzzy)===")
        for r in res["fuzzy"]:
            print(f"  [{r['theory']}] {r['name']}: {r['stmt'][:90]}")
    elif cmd == "goal":
        for r in recall_goal(records, sys.argv[3]):
            print(f"  [{r['theory']}] {r['name']}: {r['stmt'][:110]}")


if __name__ == "__main__":
    _main()
