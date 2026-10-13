#!/usr/bin/env python3
r"""
tools/critical_path/rank_candidates.py

Orchestrate spec_strengthen_scan.py across a tree, add Tier 2 consumer
counts, filter out already-done lemmas (by scanning past strengthen
log files), and emit a ranked candidates file in the format expected
by the spec sub-skill's targeting step.

Pattern order (highest empirical ROI first, per
isabelle_prover_spec/SKILL.md): C → B → A.
Pattern D and E are not yet detected automatically.

Within each pattern, candidates are sorted by **consumer count** (line
hits across the proof tree) descending — that's the downstream-surface
proxy for downstream ROI.

Past strengthening logs at reports/spec-strengthen/AInvs-*.md are
parsed for lemma names; matches are marked `[done]` and demoted.

Usage:
  rank_candidates.py [--tree DIR] [--scan-root DIR]
                     [--limit N] [--out FILE]
                     [--logs-glob GLOB]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
from spec_strengthen_scan import (  # noqa: E402
    parse_thy_lemmas, detect_A, detect_B, detect_C,
)


def grep_consumers(name: str, tree: Path) -> tuple[int, int]:
    """Return (line_hits, file_count) for `\\bname\\b` across tree."""
    if not tree.exists():
        return (0, 0)
    try:
        files_out = subprocess.run(
            ["grep", "-rln", "-E", rf"\b{re.escape(name)}\b", str(tree)],
            check=False, capture_output=True, text=True, timeout=60,
        )
        file_count = sum(1 for ln in files_out.stdout.splitlines() if ln)
        lines_out = subprocess.run(
            ["grep", "-rn", "-E", rf"\b{re.escape(name)}\b", str(tree)],
            check=False, capture_output=True, text=True, timeout=60,
        )
        line_count = sum(1 for ln in lines_out.stdout.splitlines() if ln)
        return (line_count, file_count)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return (0, 0)


def find_done_lemmas(logs_glob_dir: Path, pattern: str) -> set[str]:
    """Extract lemma names already strengthened, from dated logs.

    A lemma is considered done if:
      - its name appears in a line that also references its Pattern
        letter (e.g. row of a summary table containing both)
      - OR the log has an explicit `applied` marker for it
    """
    done: set[str] = set()
    if not logs_glob_dir.exists():
        return done
    for log_path in sorted(logs_glob_dir.glob("AInvs-*.md")):
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        # Heuristic: any backtick-quoted identifier in the same line as
        # the pattern letter or in an "applied" / "OK" context.
        for ln in text.splitlines():
            if pattern in ("A", "B", "C", "D", "E") and \
               re.search(rf"\b{pattern}\b", ln) and \
               re.search(r"applied|OK|monotone-strengthen|premise-weaken|"
                         r"postcond-strengthen|additive", ln, re.I):
                # extract backtick names from this line
                for m in re.finditer(r"`([A-Za-z_][A-Za-z_0-9']*)`", ln):
                    done.add(m.group(1))
        # Catch explicit summary-table rows with lemma in column 3 etc.
        for m in re.finditer(
            r"\|\s*[A-Z]\s*\|\s*`?([A-Za-z_][A-Za-z_0-9']*)`?\s*\|",
            text,
        ):
            done.add(m.group(1))
    return done


def iter_thy_files(scan_root: Path):
    if scan_root.is_file():
        yield scan_root
        return
    for thy in sorted(scan_root.rglob("*.thy")):
        yield thy


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scan-root", type=Path,
                    default=Path("verification/l4v/proof/invariant-abstract"))
    ap.add_argument("--tree", type=Path,
                    default=Path("verification/l4v/proof"),
                    help="Tree for consumer-count grep.")
    ap.add_argument("--logs-dir", type=Path,
                    default=Path("reports/spec-strengthen"),
                    help="Where to look for prior strengthen logs to dedup.")
    ap.add_argument("--limit", type=int, default=30,
                    help="Max candidates per pattern.")
    ap.add_argument("--out", type=Path, default=None,
                    help="Write markdown report to this file (default stdout).")
    args = ap.parse_args()

    if not args.scan_root.exists():
        print(f"ERROR: scan-root not found: {args.scan_root}", file=sys.stderr)
        return 2

    # Aggregate lemmas
    all_lemmas = []
    for thy in iter_thy_files(args.scan_root):
        try:
            all_lemmas.extend(parse_thy_lemmas(thy))
        except Exception as e:
            print(f"warn: parse failed {thy}: {e}", file=sys.stderr)

    print(f"scanned {len(all_lemmas)} Hoare-triple lemmas under {args.scan_root}",
          file=sys.stderr)

    # Run detectors
    findings_by_pattern: dict[str, list] = {
        "C": detect_C(all_lemmas),
        "B": detect_B(all_lemmas),
        "A": detect_A(all_lemmas),
    }

    # Done sets (per pattern)
    done_by_pattern: dict[str, set[str]] = {
        p: find_done_lemmas(args.logs_dir, p) for p in findings_by_pattern
    }

    # Rank: for each finding compute consumer counts + done flag
    ranked: dict[str, list] = {}
    for pat, fs in findings_by_pattern.items():
        rows = []
        for f in fs:
            line_hits, file_hits = grep_consumers(f.name, args.tree)
            is_done = f.name in done_by_pattern[pat]
            rows.append({
                "name": f.name,
                "file": Path(f.file).name,
                "line": f.line,
                "note": f.note,
                "pre": f.pre,
                "post": f.post,
                "consumers_lines": line_hits,
                "consumers_files": file_hits,
                "done": is_done,
            })
        # Sort: done last, then by consumer count desc
        rows.sort(key=lambda r: (r["done"], -r["consumers_lines"]))
        ranked[pat] = rows[: args.limit]

    # Emit markdown
    today = dt.date.today().isoformat()
    lines = [
        f"# Spec strengthening — ranked candidates ({today})",
        "",
        "Generated by `tools/critical_path/rank_candidates.py`. Re-run after applying patches to refresh.",
        "",
        f"Ranking key: **consumer_lines** (downstream surface = ROI proxy). Higher = more proofs potentially benefit.",
        "",
        f"Past strengthening logs at `{args.logs_dir}/`; lemmas already handled there are marked `[done]` and demoted.",
        "",
        "Patterns covered: A (paired weak/strong), B (missing functional postcond), C (possibly-unused premise). D and E require manual analysis.",
        "",
        "---",
        "",
    ]
    for pat in ["C", "B", "A"]:
        rows = ranked.get(pat, [])
        not_done = sum(1 for r in rows if not r["done"])
        lines.append(f"## Pattern {pat} — {not_done} un-done candidates")
        lines.append("")
        if not rows:
            lines.append("  (no candidates)")
            lines.append("")
            continue
        lines.append("| Lemma | File:Line | Consumers (lines × files) | Pre | Status |")
        lines.append("|---|---|---:|---|---|")
        for r in rows:
            status = "[done]" if r["done"] else ""
            pre = (r["pre"] or "")[:60]
            lines.append(
                f"| `{r['name']}` | `{r['file']}:{r['line']}` "
                f"| {r['consumers_lines']} × {r['consumers_files']} "
                f"| `{pre}` | {status} |"
            )
        lines.append("")

    out_text = "\n".join(lines)
    if args.out:
        args.out.write_text(out_text, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(out_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
