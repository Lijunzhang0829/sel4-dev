#!/usr/bin/env python3
"""tools/scan_topN_global.py — global top-N sorry-cost scan.

Differs from .claude/skills/.../scan-slow-proofs.sh + proof-timing.sh in:

  Per-file (skill)   : for EACH .thy in dir, measure top-10 lemmas BY SIZE.
                       Every file pays 1 baseline + 10 sorry = 11 builds, even
                       when its biggest lemma is tiny in the global picture.
  Global top-N (this): enumerate ALL lemmas in the dir, sort by body_size
                       descending, take top N (default 100). Group by file.
                       Each involved file pays 1 baseline + (N_in_this_file)
                       sorry builds. Files with no top-N lemma do 0 builds.

For Access (28 files, 1167 lemmas), global top-100 covers 17 files instead of
all 28; the smaller files skipped were paying 11 builds for almost no signal.

Build cost per file: roughly the same as baseline since `quick_and_dirty=true`
and sorry-substitution only short-circuits ONE proof — the other N-1 proofs
in the file still run, dominating wall.

Output:
  - JSON dump of all per-lemma results
  - Markdown report mirroring `reports/slow-proofs-<name>.md` shape so it can
    be diffed against the per-file scan output

Must run inside the sel4-l4v container; see tools/scan_topN_global_run.sh.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

# Reuse parser + measurement primitives from tools/proof_cost_scan.py.
sys.path.insert(0, str(Path(__file__).parent))
from proof_cost_scan import (
    DEFAULT_INVENTORY_DB,
    build_temp_session,
    parse_lemmas_with_body_lines,
    session_for_thy,
)


EXCLUDED_ARCH = {"AARCH64", "X64", "RISCV64", "ARM_HYP"}


def main() -> int:
    ap = argparse.ArgumentParser()
    # --session is now OPTIONAL — used as the fallback when inventory has no
    # entry for a file (rare, e.g. orphan .thy files outside any ROOT).
    # By default each file's session is looked up per-file from the inventory.
    ap.add_argument("--session", default=None,
                    help="Fallback Isabelle session if inventory has no entry "
                         "for a particular .thy. If omitted and a file has no "
                         "inventory entry, that file is skipped with a warning.")
    ap.add_argument("--scan-dir", required=True, type=Path,
                    help="Directory under l4v to enumerate .thy files (e.g. "
                         "/workspace/verification/l4v/proof/access-control)")
    ap.add_argument("--top-n", type=int, default=100)
    ap.add_argument("--l4v-dir", default=os.environ.get("L4V_DIR", "/sel4-project/verification/l4v"))
    ap.add_argument("--inventory", default=DEFAULT_INVENTORY_DB,
                    help="Path to inventory SQLite DB built by tools/lemma_inventory/build.py")
    ap.add_argument("--out-json", required=True, type=Path)
    ap.add_argument("--out-md", required=True, type=Path)
    args = ap.parse_args()

    print(f"[1/3] Enumerating lemmas under {args.scan_dir} ...", flush=True)
    all_thy = sorted(args.scan_dir.rglob("*.thy"))
    all_thy = [p for p in all_thy if not any(part in EXCLUDED_ARCH for part in p.parts)]
    print(f"      {len(all_thy)} .thy files in scope", flush=True)

    # Per-file session lookup. The inventory tells us each .thy's owning
    # session — necessary for correct heap selection when scan-dir spans
    # multiple sessions (e.g. proof/infoflow/ contains InfoFlow / InfoFlowC /
    # InfoFlowCBase content). Files with no inventory entry fall back to
    # --session if provided, else are skipped with a warning.
    file_session: dict[Path, str | None] = {}
    unmatched: list[Path] = []
    for thy in all_thy:
        sess = session_for_thy(thy, args.l4v_dir, args.inventory, fallback=args.session)
        file_session[thy] = sess
        if sess is None:
            unmatched.append(thy)
    if unmatched:
        print(f"      [warn] {len(unmatched)} files have no session (skip): "
              f"{', '.join(p.name for p in unmatched[:5])}"
              f"{' ...' if len(unmatched) > 5 else ''}", flush=True)
    sess_summary: dict[str, int] = defaultdict(int)
    for s in file_session.values():
        if s:
            sess_summary[s] += 1
    print(f"      session distribution: {dict(sess_summary)}", flush=True)

    candidates = []  # list of (Path, lemma_dict, session)
    for thy in all_thy:
        sess = file_session[thy]
        if sess is None:
            continue
        try:
            for lemma in parse_lemmas_with_body_lines(thy):
                candidates.append((thy, lemma, sess))
        except Exception as e:
            print(f"      [warn] parse failed for {thy}: {e}", flush=True)
    candidates.sort(key=lambda kv: kv[1]["body_size_lines"], reverse=True)
    print(f"      {len(candidates)} total lemmas (after session filter)", flush=True)

    top = candidates[: args.top_n]
    if top:
        print(f"      top {args.top_n}: body size {top[0][1]['body_size_lines']}L → "
              f"{top[-1][1]['body_size_lines']}L", flush=True)

    # Group by file (each file's session is consistent — inventory maps
    # path → single session).
    by_file: dict[Path, tuple[str, list[dict]]] = {}
    for thy, lemma, sess in top:
        if thy not in by_file:
            by_file[thy] = (sess, [])
        by_file[thy][1].append(lemma)
    files_n = len(by_file)
    print(f"      {files_n} files involved (out of {len(all_thy)})", flush=True)

    print(f"\n[2/3] Measuring ...", flush=True)
    results = []
    file_idx = 0
    t_chain = time.monotonic()
    for thy, (sess, lemmas) in by_file.items():
        file_idx += 1
        # Baseline once per file. Use this file's actual session (per inventory).
        t_b0 = time.monotonic()
        base_ms, base_err, base_log = build_temp_session(thy, sess, args.l4v_dir)
        b_wall = time.monotonic() - t_b0
        base_status = "ERR" if base_err else "OK"
        print(f"\n[{file_idx}/{files_n}] {thy.name}  session={sess}  "
              f"baseline={base_ms}ms [{base_status}]  ({b_wall:.0f}s wall, {len(lemmas)} lemmas)", flush=True)
        if base_err:
            print(f"      baseline failure tail: {base_log[-300:]}", flush=True)
            for lemma in lemmas:
                results.append({
                    "file": str(thy), "session": sess, "name": lemma["name"],
                    "body_size": lemma["body_size_lines"], "lemma_line": lemma["lemma_line"],
                    "baseline_ms": base_ms, "sorry_ms": None, "cost_ms": None,
                    "baseline_err": True, "sorry_err": None,
                })
            continue
        # Per-lemma sorry.
        for lemma in lemmas:
            t_s0 = time.monotonic()
            sorry_ms, sorry_err, sorry_log = build_temp_session(
                thy, sess, args.l4v_dir,
                sorry_lines=(lemma["body_start_line"], lemma["body_end_line"]),
            )
            s_wall = time.monotonic() - t_s0
            cost = base_ms - sorry_ms
            stat = "ERR" if sorry_err else "OK"
            print(f"    {lemma['name']:50s}  {lemma['body_size_lines']:3d}L  "
                  f"cost={cost:7d}ms  [{stat}]  ({s_wall:.0f}s)", flush=True)
            if sorry_err:
                print(f"      sorry stderr tail: {sorry_log[:300]}", flush=True)
            results.append({
                "file": str(thy), "session": sess, "name": lemma["name"],
                "body_size": lemma["body_size_lines"], "lemma_line": lemma["lemma_line"],
                "baseline_ms": base_ms, "sorry_ms": sorry_ms, "cost_ms": cost,
                "baseline_err": False, "sorry_err": sorry_err,
            })

    chain_wall = time.monotonic() - t_chain
    print(f"\n[3/3] Writing reports ({chain_wall:.0f}s wall total) ...", flush=True)
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps({
        "session_fallback": args.session,
        "session_distribution": dict(sess_summary),
        "scan_dir": str(args.scan_dir),
        "top_n": args.top_n,
        "files_in_scope": len(all_thy),
        "files_unmatched": len(unmatched),
        "lemmas_in_scope": len(candidates),
        "files_measured": files_n,
        "chain_wall_s": int(chain_wall),
        "results": results,
    }, indent=2))

    # Markdown summary
    md = []
    md.append("# Slow Proofs Report — global top-N\n")
    md.append(f"- **Scan dir**: {args.scan_dir}")
    md.append(f"- **Sessions covered (per file)**: {dict(sess_summary)}")
    if args.session:
        md.append(f"- **Fallback session for unmatched files**: {args.session}")
    md.append(f"- **Top N**: {args.top_n}")
    md.append(f"- **Files in scope**: {len(all_thy)}")
    if unmatched:
        md.append(f"- **Files skipped (no inventory match)**: {len(unmatched)}")
    md.append(f"- **Lemmas in scope**: {len(candidates)}")
    md.append(f"- **Files measured**: {files_n}")
    md.append(f"- **Wall**: {int(chain_wall)}s ({chain_wall/3600:.1f} h)")
    md.append(f"- **Date**: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}\n")

    ok = [r for r in results if not r["baseline_err"] and not r["sorry_err"]]
    ok.sort(key=lambda r: r["cost_ms"], reverse=True)

    md.append("## Top 30 by sorry-cost\n")
    md.append("| rank | cost ms | size L | session | lemma | file |")
    md.append("|---:|---:|---:|---|---|---|")
    for i, r in enumerate(ok[:30], 1):
        md.append(f"| {i} | {r['cost_ms']} | {r['body_size']} | "
                  f"{r.get('session','?')} | `{r['name']}` | "
                  f"`{Path(r['file']).name}:{r['lemma_line']}` |")

    md.append("\n## Slow proofs (cost > 3000 ms)\n")
    slow = [r for r in ok if r["cost_ms"] > 3000]
    md.append(f"Total: **{len(slow)}** out of {len(ok)} measured\n")
    md.append("| cost ms | size L | session | lemma | file:line |")
    md.append("|---:|---:|---|---|---|")
    for r in slow:
        md.append(f"| {r['cost_ms']} | {r['body_size']} | "
                  f"{r.get('session','?')} | `{r['name']}` | "
                  f"`{Path(r['file']).name}:{r['lemma_line']}` |")

    args.out_md.write_text("\n".join(md) + "\n")
    print(f"      wrote {args.out_json}")
    print(f"      wrote {args.out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
