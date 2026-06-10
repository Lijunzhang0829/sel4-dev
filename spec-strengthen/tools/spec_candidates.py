#!/usr/bin/env python3
r"""
tools/spec_strengthen/spec_candidates.py

Per-pattern candidate generator for Pattern A or Pattern C. Pattern B
was dropped 2026-06-08 per the revised strict definition of spec
strengthening. Pattern G uses a separate detector (`spec_frame_gap.py`)
because its candidates are mechanically preflightable; Pattern D is
manual-only and has no detector.

This tool emits candidates for **one pattern at a time** — `--pattern`
is required. There is intentionally no shared ranking pipeline that
mixes A and C; the two have different evidence qualities and different
downstream verification paths, so mixing them in one table is
misleading.

Within a single pattern, candidates carry a `suspicion_score` (NOT
"ranking" — the name was changed to avoid suggesting these are
"highest probability of success"; they're "highest suspicion / impact
if true"). For Pattern C, suspicion is also gated by a probe at
execute time; the scanner is hypothesis-only.

Output:
  - JSON (--json): list of candidate records (for shell consumption)
  - Markdown (default): human-readable table

Usage:
  spec_candidates.py --pattern A|C
                     [--target spec|ainvs|refine|all]
                     [--scan-root DIR]
                     [--tree DIR]
                     [--logs-dir DIR]
                     [--limit N]
                     [--out FILE]
                     [--json]

Candidate fields (JSON):
  name           — lemma name
  file           — source file (basename)
  file_path      — absolute path
  line           — 1-based source line
  session        — derived from file path
  consumers_lines/files — Tier-2 grep upper-bound counts
  suspicion_score — pattern-internal heuristic priority
  suggested_move — natural-language hint
  kind           — "paired-chain" (A) or "unused-premise" (C)
  evidence       — "heuristic+manual" (A) or "heuristic" (C)
  done           — true if covered in past strengthen-log

For unfamiliar shapes or FP risks, consult
`references/spec-strengthen-playbook.md`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
from spec_strengthen_scan import (  # noqa: E402
    parse_thy_lemmas, iter_thy_files,
    detect_paired_chain, detect_unused_premise,
    Finding,
)


# Per-pattern config: detector callable + evidence type + internal
# suspicion weight (used only WITHIN the pattern's own ranking — no
# cross-pattern comparison). The shell's survey layer is responsible
# for tier-based presentation; this tool stays single-pattern.
PATTERN_CONFIG = {
    "A": {
        "kind":      "paired-chain",
        "detector":  detect_paired_chain,
        "evidence":  "heuristic+manual",
        # suspicion_score = consumer_lines × weight (weight is 1 here
        # because A's high consumer count often correlates with cross-file
        # consumer breakage risk; we don't want to over-promote it).
        "weight":    1,
        "tier":      3,
    },
    "C": {
        "kind":      "unused-premise",
        "detector":  detect_unused_premise,
        "evidence":  "heuristic",
        # weight 5: C with high consumer count is high-impact-if-true,
        # but probe is the actual filter at execute time.
        "weight":    5,
        "tier":      2,
    },
}

TARGET_SCAN_ROOTS = {
    "spec":   "verification/l4v/spec/abstract",
    "ainvs":  "verification/l4v/proof/invariant-abstract",
    "refine": "verification/l4v/proof/refine",
    "all":    "verification/l4v",
}

SESSION_BY_PATH_PREFIX = (
    ("spec/abstract/",            "ASpec"),
    ("spec/cspec/",               "CSpec"),
    ("proof/invariant-abstract/", "AInvs"),
    ("proof/refine/",             "Refine"),
    ("proof/crefine/",            "CRefine"),
    ("proof/access-control/",     "Access"),
    ("proof/infoflow/",           "InfoFlow"),
    ("proof/drefine/",            "DRefine"),
    ("proof/bisim/",              "Bisim"),
)


def session_for_path(file_path: str) -> str:
    for prefix, session in SESSION_BY_PATH_PREFIX:
        if prefix in file_path:
            return session
    return "?"


def grep_consumers(name: str, tree: Path,
                   self_file: Path | None = None,
                   self_line: int = 0) -> tuple[int, int]:
    r"""(line_hits, file_count) for `\bname\b` across `tree`.

    Filters out the candidate lemma's own definition line.
    Upper bound — cross-file name collisions not disambiguated.
    """
    if not tree.exists():
        return (0, 0)
    try:
        out = subprocess.run(
            ["grep", "-rn", "-E", rf"\b{re.escape(name)}\b", str(tree)],
            check=False, capture_output=True, text=True, timeout=60,
        )
        raw = [ln for ln in out.stdout.splitlines() if ln]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return (0, 0)

    if self_file is None or not self_line:
        return (len(raw), len({ln.split(":", 1)[0] for ln in raw}))

    sf = str(self_file)
    self_prefix = f"{sf}:"
    def_keyword_re = re.compile(r"\b(?:lemma|theorem|corollary)s?\b")
    kept: list[str] = []
    for ln in raw:
        if ln.startswith(self_prefix):
            parts = ln.split(":", 2)
            if len(parts) >= 3 and parts[1].isdigit() \
               and int(parts[1]) == self_line \
               and def_keyword_re.search(parts[2]):
                continue
        kept.append(ln)
    return (len(kept), len({ln.split(":", 1)[0] for ln in kept}))


# ---------- "Done" detection from past logs ---------------------------------

DONE_CONTEXT_RE = re.compile(
    r"(?:applied|verdict|tier\s*[1-3]|t[1-3]\b|"
    r"premise-weaken|monotone-strengthen|postcond-strengthen|additive|OK)",
    re.IGNORECASE,
)


def find_done_lemmas(logs_dir: Path) -> set[str]:
    done: set[str] = set()
    if not logs_dir.exists():
        return done
    for log_path in sorted(logs_dir.glob("AInvs-*.md")):
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for ln in text.splitlines():
            if not DONE_CONTEXT_RE.search(ln):
                continue
            for m in re.finditer(r"`([A-Za-z_][A-Za-z_0-9']*)`", ln):
                done.add(m.group(1))
    return done


# ---------- Driver -----------------------------------------------------------

def run_detectors(all_lemmas: list, pattern: str) -> list[Finding]:
    """Run the single detector for the chosen pattern.

    No cross-pattern aggregation — callers ask for one pattern at a
    time. The shell's survey layer is responsible for combining
    patterns into the tier-based view.
    """
    cfg = PATTERN_CONFIG.get(pattern)
    if cfg is None:
        raise ValueError(f"unknown pattern: {pattern} (allowed: A, C)")
    return cfg["detector"](all_lemmas)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pattern", required=True, choices=["A", "C"],
                    help="Which pattern's detector to run. A = paired-chain "
                         "(heuristic+manual, Tier 3). C = unused-premise "
                         "(heuristic, probe-confirmable, Tier 2). Required — "
                         "this tool emits one pattern at a time, no shared "
                         "ranking pipeline.")
    ap.add_argument("--target", choices=list(TARGET_SCAN_ROOTS),
                    default="ainvs",
                    help="Which session to scan; sets --scan-root default.")
    ap.add_argument("--scan-root", type=Path, default=None,
                    help="Override scan root (else derived from --target).")
    ap.add_argument("--tree", type=Path,
                    default=Path("verification/l4v/proof"),
                    help="Tree for consumer-count grep.")
    ap.add_argument("--logs-dir", type=Path,
                    default=Path("reports/spec-strengthen"))
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--json", action="store_true",
                    help="Emit candidate records as a JSON array on stdout "
                         "(for tool consumption by spec_strengthen_run.sh).")
    args = ap.parse_args()

    cfg = PATTERN_CONFIG[args.pattern]

    scan_root = args.scan_root or Path(TARGET_SCAN_ROOTS[args.target])
    if not scan_root.exists():
        print(f"ERROR: scan-root not found: {scan_root}", file=sys.stderr)
        return 2

    all_lemmas = []
    for thy in iter_thy_files(scan_root):
        try:
            all_lemmas.extend(parse_thy_lemmas(thy))
        except Exception as e:
            print(f"warn: parse {thy}: {e}", file=sys.stderr)
    print(f"scanned {len(all_lemmas)} Hoare-triple lemmas under {scan_root} "
          f"(pattern {args.pattern})",
          file=sys.stderr)

    findings = run_detectors(all_lemmas, args.pattern)
    done = find_done_lemmas(args.logs_dir)

    # Build rows. Within a single pattern, suspicion_score =
    # consumer_lines × cfg['weight']. NOT a cross-pattern ROI.
    by_name: dict[tuple[str, int, str], dict] = {}
    for f in findings:
        self_file = Path(f.file)
        line_hits, file_hits = grep_consumers(
            f.name, args.tree,
            self_file=self_file, self_line=f.line,
        )
        suspicion_score = line_hits * cfg["weight"]
        row = {
            "name": f.name,
            "file": self_file.name,
            "file_path": str(self_file),
            "line": f.line,
            "session": session_for_path(str(self_file)),
            "consumers_lines": line_hits,
            "consumers_files": file_hits,
            "suspicion_score": suspicion_score,
            "suggested_move": f.suggested_move or f.note,
            "kind": f.kind,
            "pattern": args.pattern,
            "evidence": cfg["evidence"],
            "tier": cfg["tier"],
            "done": f.name in done,
        }
        key = (str(self_file), f.line, f.name)
        existing = by_name.get(key)
        if existing is None or row["suspicion_score"] > existing["suspicion_score"]:
            by_name[key] = row

    rows = list(by_name.values())
    rows.sort(key=lambda r: (r["done"], -r["suspicion_score"], -r["consumers_lines"]))
    rows = rows[: args.limit]

    if args.json:
        import json
        print(json.dumps(rows, ensure_ascii=False))
        return 0

    today = dt.date.today().isoformat()
    out_lines = [
        f"# Pattern {args.pattern} candidates ({today})",
        "",
        f"Source: `{scan_root}`",
        f"Evidence type: `{cfg['evidence']}` — survey-layer Tier {cfg['tier']}.",
        "",
        "Suspicion score = consumer_lines × pattern weight. **NOT** a "
        "cross-pattern ROI. Higher means \"this candidate has wider downstream "
        "surface if the heuristic is right\" — not \"more likely to succeed\".",
        "Probe (Pattern C) or full check-theory.sh (Pattern A) is the actual "
        "verification gate at execute time.",
        "",
        "| Lemma | Session | File:Line | Consumers | Suspicion | Suggested move | Status |",
        "|---|---|---|---:|---:|---|---|",
    ]
    for r in rows:
        status = "[done]" if r["done"] else ""
        sm = r["suggested_move"][:100]
        if len(r["suggested_move"]) > 100:
            sm += "…"
        out_lines.append(
            f"| `{r['name']}` | {r['session']} "
            f"| `{r['file']}:{r['line']}` "
            f"| {r['consumers_lines']} "
            f"| {r['suspicion_score']} "
            f"| {sm} | {status} |"
        )

    out_text = "\n".join(out_lines) + "\n"
    if args.out:
        args.out.write_text(out_text, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(out_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
