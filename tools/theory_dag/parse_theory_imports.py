#!/usr/bin/env python3
"""
tools/critical_path/parse_theory_imports.py
===========================================

Step 1 of the DAG critical-path pipeline.

WHAT IT DOES
------------
Walks every .thy file under verification/l4v/, extracts the
"theory NAME imports A B C ..." header, and maps each .thy to its
owning session via tools/lemma_inventory/parse_roots.py. Resolves
each import to its canonical Session.Theory name (matching the
form used in heaps/db-archive/*.db theory_timings BLOBs and in
reports/session-duplication-scan.json).

OUTPUT
------
    reports/theory-dag.json
      {
        "stats": {...},
        "nodes": [{"name": "Refine.Finalise_R", "session": "Refine",
                   "path": "<abs>", "n_imports_resolved": int,
                   "n_imports_unresolved": int}, ...],
        "edges": [["Refine.Finalise_R", "Refine.Invariants_H"], ...],
        "unresolved_imports": [{"from": "Refine.Finalise_R",
                                "raw": "../base/SomeName"}, ...]
      }

DEPENDENCIES
------------
    tools/lemma_inventory/parse_roots.py  (sibling)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tools" / "lemma_inventory"))
import parse_roots  # noqa: E402

L4V = REPO / "verification" / "l4v"
import os as _os
ARCH = _os.environ.get("L4V_ARCH", "ARM")          # override: L4V_ARCH=AARCH64
OUT_JSON = REPO / "reports" / f"theory-dag-{ARCH}.json"

# theory NAME imports ...  <terminator>  where terminator = keywords|abbrevs|begin
HEADER_RE = re.compile(
    r"\btheory\s+(?:\"([^\"]+)\"|(\S+))\s+imports\s+(.*?)\s+(?:keywords\b|abbrevs\b|begin\b)",
    re.DOTALL,
)
# tokens inside imports: quoted-string OR bare-id (allow ./ ../ . - in identifiers)
_TOKEN_RE = re.compile(r'"([^"]+)"|(\$?[A-Za-z_][\w./\-]*)')


def parse_thy(path: Path) -> tuple[str, list[str]] | None:
    """Return (theory_basename, [raw_import, ...]) or None if no header found / unreadable."""
    try:
        raw = path.read_text(errors="replace")
    except (PermissionError, OSError):
        return None
    clean = parse_roots.strip_isabelle_comments(raw)
    m = HEADER_RE.search(clean)
    if not m:
        return None
    name = m.group(1) or m.group(2)
    name = name.strip().strip('"')
    # If quoted name has a path/extension, take basename without .thy
    name = Path(name).name
    if name.endswith(".thy"):
        name = name[:-4]
    body = m.group(3)
    tokens: list[str] = []
    for tm in _TOKEN_RE.finditer(body):
        t = tm.group(1) or tm.group(2)
        if t and t not in ("imports",):
            tokens.append(t)
    return name, tokens


def build_theory_index(parsed_roots: dict) -> tuple[dict[str, dict], list[Path]]:
    """Walk all .thy files; assign owner session; return (index, list-of-thy-paths).
    index: full canonical name 'Session.Theory' → {path, session, basename}.
    """
    index: dict[str, dict] = {}
    paths: list[Path] = []
    seen_paths: set[Path] = set()
    for thy in sorted(L4V.rglob("*.thy")):
        if not thy.is_file():
            continue
        # Skip hidden / generated dirs we know are not part of the regular session DAG
        rel = thy.resolve()
        if rel in seen_paths:
            continue
        seen_paths.add(rel)
        sess = parse_roots.assign_session_for_thy(thy, parsed_roots)
        if sess is None:
            # Try parents until something matches a session_dir
            continue
        basename = thy.stem
        canon = f"{sess}.{basename}"
        # If duplicate canonical name appears (rare — different ARCHes own same name),
        # prefer the L4V_ARCH=ARM one heuristically: the path that contains "/ARM/" or no arch dir.
        if canon in index:
            existing = Path(index[canon]["path"])
            new_score = (f"/{ARCH}/" in str(thy)) - (f"/{ARCH}/" in str(existing))
            if new_score <= 0:
                continue
        index[canon] = {
            "path": str(thy.resolve()),
            "session": sess,
            "basename": basename,
        }
        paths.append(thy)
    return index, paths


def resolve_import(
    raw: str,
    owner_session: str,
    owner_thy_dir: Path,
    sessions: dict,
    theory_index: dict[str, dict],
    basename_to_canon: dict[str, list[str]],
) -> str | None:
    """Resolve a raw import string to a canonical Session.Theory name. None if unresolvable."""

    # Case 1: contains a path separator → relative path resolution
    if "/" in raw:
        try:
            cand_path = (owner_thy_dir / raw).resolve()
            for p in (cand_path.with_suffix(".thy"), cand_path):
                if p.exists() and p.suffix == ".thy":
                    abs_p = str(p)
                    for canon, info in theory_index.items():
                        if info["path"] == abs_p:
                            return canon
        except Exception:
            pass
        # fallthrough: try basename matching
        raw = Path(raw).name

    # Case 2: session-qualified "Session.Theory"
    if "." in raw:
        if raw in theory_index:
            return raw
        # Sometimes it's "Path.With.Dots.Theory" — try last segment as basename
        last = raw.rsplit(".", 1)[-1]
        cands = basename_to_canon.get(last, [])
        # prefer one whose session matches owner's parent chain or imports
        # (caller will iterate; for now, prefer owner session, then imports, then any)
        own = f"{owner_session}.{last}"
        if own in theory_index:
            return own
        for s in sessions.get(owner_session, {}).get("imported_sessions", []):
            c = f"{s}.{last}"
            if c in theory_index:
                return c
        # parent chain
        p = sessions.get(owner_session, {}).get("parent")
        while p:
            c = f"{p}.{last}"
            if c in theory_index:
                return c
            p = sessions.get(p, {}).get("parent")
        # any single hit
        if len(cands) == 1:
            return cands[0]
        return None

    # Case 3: bare identifier — same logic as above's last segment
    cand = f"{owner_session}.{raw}"
    if cand in theory_index:
        return cand
    for s in sessions.get(owner_session, {}).get("imported_sessions", []):
        c = f"{s}.{raw}"
        if c in theory_index:
            return c
    p = sessions.get(owner_session, {}).get("parent")
    while p:
        c = f"{p}.{raw}"
        if c in theory_index:
            return c
        p = sessions.get(p, {}).get("parent")
    cands = basename_to_canon.get(raw, [])
    if len(cands) == 1:
        return cands[0]
    return None


def main() -> int:
    print(f"parsing ROOTs under {L4V} ...", file=sys.stderr)
    parsed = parse_roots.parse_all_roots(L4V.resolve(), arch=ARCH)
    print(f"L4V_ARCH={ARCH}", file=sys.stderr)
    sessions = parsed["sessions"]

    print(f"sessions found: {len(sessions)}", file=sys.stderr)
    print("building theory index by walking *.thy ...", file=sys.stderr)
    index, paths = build_theory_index(parsed)
    print(f"theory_index size: {len(index)}; .thy files in walk: {len(paths)}", file=sys.stderr)

    # basename → list of canonical names (for fallback resolution)
    basename_to_canon: dict[str, list[str]] = {}
    for canon, info in index.items():
        basename_to_canon.setdefault(info["basename"], []).append(canon)

    # Now parse each thy and build edges
    edges: list[tuple[str, str]] = []
    unresolved: list[dict] = []
    per_node_stats: dict[str, dict[str, int]] = {}
    no_header: list[str] = []

    for thy in paths:
        parsed_h = parse_thy(thy)
        owner = parse_roots.assign_session_for_thy(thy, parsed)
        if owner is None:
            continue
        if parsed_h is None:
            no_header.append(str(thy))
            continue
        basename, raw_imports = parsed_h
        canon_self = f"{owner}.{basename}"
        if canon_self not in index:
            # filename-vs-declared-name mismatch (rare but possible); record under filename canon
            continue
        thy_dir = thy.parent
        n_res, n_unres = 0, 0
        for raw in raw_imports:
            target = resolve_import(
                raw, owner, thy_dir, sessions, index, basename_to_canon
            )
            if target is None:
                n_unres += 1
                unresolved.append({"from": canon_self, "raw": raw})
            else:
                if target != canon_self:
                    edges.append((canon_self, target))
                n_res += 1
        per_node_stats[canon_self] = {
            "n_imports_resolved": n_res,
            "n_imports_unresolved": n_unres,
        }

    # Deduplicate edges
    edges_set = set(edges)
    nodes = []
    for canon in sorted(index):
        info = index[canon]
        st = per_node_stats.get(canon, {"n_imports_resolved": 0, "n_imports_unresolved": 0})
        nodes.append(
            {
                "name": canon,
                "session": info["session"],
                "path": info["path"],
                **st,
            }
        )

    # Sanity stats
    isolated = [n["name"] for n in nodes if not any(e[0] == n["name"] or e[1] == n["name"] for e in edges_set)]
    # Cheaper: compute incident counts
    incoming = {n["name"]: 0 for n in nodes}
    outgoing = {n["name"]: 0 for n in nodes}
    for f, t in edges_set:
        outgoing[f] = outgoing.get(f, 0) + 1
        incoming[t] = incoming.get(t, 0) + 1
    isolated_set = [n["name"] for n in nodes if incoming[n["name"]] == 0 and outgoing[n["name"]] == 0]

    # Top unresolved tokens (often library/Isabelle-stdlib like HOL, Pure)
    unresolved_freq: dict[str, int] = {}
    for u in unresolved:
        unresolved_freq[u["raw"]] = unresolved_freq.get(u["raw"], 0) + 1
    top_unresolved = sorted(unresolved_freq.items(), key=lambda kv: -kv[1])[:30]

    stats = {
        "n_sessions_found": len(sessions),
        "n_theory_nodes": len(nodes),
        "n_thy_files_walked": len(paths),
        "n_thy_files_no_header": len(no_header),
        "n_edges": len(edges_set),
        "n_isolated_nodes": len(isolated_set),
        "n_total_unresolved_imports": len(unresolved),
        "n_unique_unresolved_tokens": len(unresolved_freq),
        "top_unresolved_tokens": [{"token": k, "count": v} for k, v in top_unresolved],
        "isolated_node_examples": isolated_set[:20],
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(
            {
                "stats": stats,
                "nodes": nodes,
                "edges": [list(e) for e in sorted(edges_set)],
                "unresolved_imports_sample": unresolved[:200],
            },
            indent=2,
        )
        + "\n"
    )

    # Print sanity to stderr/stdout
    print(f"\n=== sanity ===")
    print(f"  sessions:                 {stats['n_sessions_found']}")
    print(f"  theory nodes:             {stats['n_theory_nodes']}")
    print(f"  .thy files walked:        {stats['n_thy_files_walked']}")
    print(f"  .thy files no-header:     {stats['n_thy_files_no_header']}")
    print(f"  edges:                    {stats['n_edges']}")
    print(f"  isolated nodes:           {stats['n_isolated_nodes']}")
    print(f"  unresolved imports total: {stats['n_total_unresolved_imports']}")
    print(f"  unique unresolved tokens: {stats['n_unique_unresolved_tokens']}")
    print(f"  top 10 unresolved tokens:")
    for k, v in top_unresolved[:10]:
        print(f"    {v:5d}× {k}")
    print(f"\nwrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
