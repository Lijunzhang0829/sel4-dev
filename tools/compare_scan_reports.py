#!/usr/bin/env python3
"""tools/compare_scan_reports.py — diff global top-N scan vs per-file scan.

Inputs:
  --topn-json  JSON output of tools/scan_topN_global.py
  --perfile-md Markdown produced by .claude/skills/.../scan-slow-proofs.sh

Outputs (markdown to --out or stdout):
  - both     : lemmas measured by both, with cost from each side
  - only-topn: lemmas the global scan caught that per-file did not measure
  - only-perfile: lemmas per-file caught that global skipped
  - cost agreement: scatter-style table for shared lemmas
  - file coverage: which files each tool actually measured
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def load_perfile(md_path: Path) -> list[dict]:
    """Parse the skill's slow-proofs markdown into a list of lemma records."""
    text = md_path.read_text()
    out = []
    # Sections look like:
    # ## <basename>
    # - **File**: <path>
    # - **Baseline**: <ms>ms
    # ```
    # ── Sorry-substitution (top 10 proofs) ──
    #   <name>  <NL>  sorry=<ms>ms  cost=<ms>ms  [OK]
    # ...
    # ```
    sections = re.findall(
        r"## (\S+)\s*\n.*?\*\*File\*\*: (\S+).*?\*\*Baseline\*\*: (\d+)ms.*?```(.*?)```",
        text, re.DOTALL,
    )
    for base, file_path, baseline, body in sections:
        for line in body.split("\n"):
            m = re.match(
                r"\s+(\S+)\s+(\d+)L\s+sorry=\s*(\d+)ms\s+cost=\s*(-?\d+)ms\s+\[(\w+)\]",
                line,
            )
            if m:
                name, sz, sm, cm, st = m.groups()
                out.append({
                    "name": name, "file": file_path, "size": int(sz),
                    "baseline_ms": int(baseline), "sorry_ms": int(sm),
                    "cost_ms": int(cm), "status": st,
                })
    return out


def load_topn(json_path: Path) -> list[dict]:
    data = json.loads(json_path.read_text())
    out = []
    for r in data["results"]:
        if r.get("baseline_err") or r.get("sorry_err"):
            continue
        if r["sorry_ms"] is None:
            continue
        out.append({
            "name": r["name"], "file": r["file"], "size": r["body_size"],
            "baseline_ms": r["baseline_ms"], "sorry_ms": r["sorry_ms"],
            "cost_ms": r["cost_ms"], "status": "OK",
            "lemma_line": r.get("lemma_line"),
        })
    out_extras = {
        "session": data.get("session"), "scan_dir": data.get("scan_dir"),
        "top_n": data.get("top_n"), "files_in_scope": data.get("files_in_scope"),
        "lemmas_in_scope": data.get("lemmas_in_scope"),
        "files_measured": data.get("files_measured"),
        "chain_wall_s": data.get("chain_wall_s"),
    }
    return out, out_extras


def render(topn: list[dict], topn_meta: dict, perfile: list[dict]) -> str:
    # Index lemmas by (basename, name) to match across reports
    def key(r):
        return (Path(r["file"]).name, r["name"])
    topn_by = {key(r): r for r in topn}
    perf_by = {key(r): r for r in perfile}
    both = sorted(set(topn_by) & set(perf_by))
    only_t = sorted(set(topn_by) - set(perf_by))
    only_p = sorted(set(perf_by) - set(topn_by))

    md = []
    md.append("# Scan-method comparison: global top-N vs per-file top-10\n")
    md.append(f"- session: **{topn_meta['session']}**")
    md.append(f"- topn: N={topn_meta['top_n']} (covered {topn_meta['files_measured']} of {topn_meta['files_in_scope']} files; "
              f"wall {topn_meta['chain_wall_s']/3600:.1f} h)")
    md.append(f"- topn lemmas measured (no err): **{len(topn)}**")
    md.append(f"- per-file lemmas measured (no err): **{len(perfile)}**")
    md.append(f"- in BOTH: **{len(both)}**")
    md.append(f"- only in top-N: **{len(only_t)}**")
    md.append(f"- only in per-file: **{len(only_p)}**\n")

    md.append("## Files measured by each method\n")
    files_t = sorted({Path(r["file"]).name for r in topn})
    files_p = sorted({Path(r["file"]).name for r in perfile})
    common_f = sorted(set(files_t) & set(files_p))
    only_t_f = sorted(set(files_t) - set(files_p))
    only_p_f = sorted(set(files_p) - set(files_t))
    md.append(f"- top-N measured **{len(files_t)}** files; per-file measured **{len(files_p)}** files")
    md.append(f"- common: {len(common_f)}; only-topN: {len(only_t_f)}; only-perfile: **{len(only_p_f)}**")
    if only_p_f:
        md.append(f"- files measured ONLY by per-file (top-N skipped because none of their lemmas hit global cutoff):")
        for fn in only_p_f:
            md.append(f"  - `{fn}`")
    md.append("")

    # Slow proofs (cost > 3000) per method
    slow_t = [r for r in topn if r["cost_ms"] > 3000]
    slow_p = [r for r in perfile if r["cost_ms"] > 3000]
    slow_t_keys = {key(r) for r in slow_t}
    slow_p_keys = {key(r) for r in slow_p}
    md.append("## Slow-proof comparison (cost > 3000 ms)\n")
    md.append(f"- top-N found: **{len(slow_t)}**")
    md.append(f"- per-file found: **{len(slow_p)}**")
    md.append(f"- in BOTH slow lists: **{len(slow_t_keys & slow_p_keys)}**")
    md.append(f"- slow only in top-N: **{len(slow_t_keys - slow_p_keys)}**")
    md.append(f"- slow only in per-file: **{len(slow_p_keys - slow_t_keys)}**\n")

    # Newly discovered slow proofs (only in topN — i.e. caught by going wider in big files)
    newly = sorted(slow_t_keys - slow_p_keys, key=lambda k: -topn_by[k]["cost_ms"])
    if newly:
        md.append("### Newly discovered slow proofs (only in top-N)\n")
        md.append("| cost ms | size L | lemma | file |")
        md.append("|---:|---:|---|---|")
        for k in newly:
            r = topn_by[k]
            md.append(f"| {r['cost_ms']} | {r['size']} | `{r['name']}` | `{Path(r['file']).name}` |")
        md.append("")

    # Slow lost from per-file (small files top-N didn't sample)
    lost = sorted(slow_p_keys - slow_t_keys, key=lambda k: -perf_by[k]["cost_ms"])
    if lost:
        md.append("### Slow proofs only in per-file (top-N missed because their file was small)\n")
        md.append("| cost ms | size L | lemma | file |")
        md.append("|---:|---:|---|---|")
        for k in lost:
            r = perf_by[k]
            md.append(f"| {r['cost_ms']} | {r['size']} | `{r['name']}` | `{Path(r['file']).name}` |")
        md.append("")

    # Cost agreement on shared lemmas
    md.append("## Cost agreement on lemmas measured by both\n")
    md.append("Each row: same lemma, different runs. Δ% measures variance.\n")
    md.append("| lemma | file | cost_topN | cost_perfile | Δ ms | Δ % |")
    md.append("|---|---|---:|---:|---:|---:|")
    rows = []
    for k in both:
        rt, rp = topn_by[k], perf_by[k]
        d = rt["cost_ms"] - rp["cost_ms"]
        denom = max(abs(rp["cost_ms"]), 1)
        pct = d * 100.0 / denom
        rows.append((rp["cost_ms"], rt["cost_ms"], d, pct, rt["name"], Path(rt["file"]).name))
    rows.sort(key=lambda x: -max(x[0], x[1]))
    for cp, ct, d, pct, name, fn in rows[:50]:
        md.append(f"| `{name}` | `{fn}` | {ct} | {cp} | {d:+d} | {pct:+.1f}% |")
    if len(rows) > 50:
        md.append(f"\n_(truncated to top 50 by max cost; total {len(rows)} shared)_")
    md.append("")
    return "\n".join(md) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topn-json", required=True, type=Path)
    ap.add_argument("--perfile-md", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    topn, topn_meta = load_topn(args.topn_json)
    perfile = load_perfile(args.perfile_md)
    md = render(topn, topn_meta, perfile)
    if args.out:
        args.out.write_text(md)
        print(f"wrote {args.out}")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
