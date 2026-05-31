#!/usr/bin/env python3
r"""
tools/critical_path/rank_candidates.py — produce dated candidates file.

Orchestrates spec_strengthen_scan.py output across a tree, applies
ROI weighting per the Taxonomy in isabelle_prover_spec SKILL §4, and
emits a ranked markdown file grouped by Tier.

Patterns covered:
  Tier 1 Logical:     C (auto), G (manual), D (manual), B (auto)
  Tier 2 Automation:  E (manual; see spec_coverage_matrix.py if available)
  Tier 3 Packaging:   A (auto; high false-positive rate)

ROI formula (SKILL §4):
  roi_score = consumer_lines × tier_weight

Tier weights (hardcoded constants; move to JSON if tuning is needed):
  C = 5,  G = 4,  D = 3,  B = 3,  E = 2,  A = 1

Output sort key (within each pattern):
  (done, -roi_score, -consumer_lines)

Past `reports/spec-strengthen/AInvs-*.md` logs are scanned for `[done]`
markers — matched lemma names get demoted in the ranking.

Usage:
  rank_candidates.py [--scan-root DIR] [--tree DIR] [--logs-dir DIR]
                     [--limit N] [--out FILE]
                     [--target spec|ainvs|refine|all]

Session mapping (matches SKILL parent's session-mapping table):
  spec/abstract/             → ASpec
  proof/invariant-abstract/  → AInvs
  proof/refine/              → Refine
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
    parse_thy_lemmas, detect_A, detect_B, detect_C,
)


# ---------- Taxonomy: weights, tiers, detector status ------------------------

WEIGHTS = {
    "C": 5,   # Tier 1 — premise weakening
    "G": 4,   # Tier 1 — frame preservation
    "D": 3,   # Tier 1 — loose bound → exact
    "B": 3,   # Tier 1 — functional postcond
    "E": 2,   # Tier 2 — compound _invs
    "A": 1,   # Tier 3 — packaging
}

TIER = {
    "C": "T1", "G": "T1", "D": "T1", "B": "T1",
    "E": "T2",
    "A": "T3",
}

DETECTOR = {
    "C": "auto",      # spec_strengthen_scan.py
    "G": "manual",    # future: G detector
    "D": "manual",    # semantic; not auto-detected
    "B": "auto",      # spec_strengthen_scan.py
    "E": "future",    # spec_coverage_matrix.py (separate tool)
    "A": "auto",      # spec_strengthen_scan.py
}

PATTERN_ORDER = ["C", "G", "D", "B", "E", "A"]  # T1 → T2 → T3 within tier


# ---------- Session mapping --------------------------------------------------

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

TARGET_SCAN_ROOTS = {
    "spec":   "verification/l4v/spec/abstract",
    "ainvs":  "verification/l4v/proof/invariant-abstract",
    "refine": "verification/l4v/proof/refine",
    "all":    "verification/l4v",
}


def session_for_path(file_path: str) -> str:
    """Return session name based on path prefix; '?' if no match."""
    for prefix, session in SESSION_BY_PATH_PREFIX:
        if prefix in file_path:
            return session
    return "?"


# ---------- Tier 2 consumer count -------------------------------------------

def grep_consumers(name: str, tree: Path,
                   self_file: Path | None = None,
                   self_line: int = 0) -> tuple[int, int]:
    r"""Return (line_hits, file_count) for `\bname\b` across `tree`.

    Excludes the candidate lemma's own definition line when self_file
    and self_line are supplied. Note: still an UPPER BOUND because
    name collisions across files aren't disambiguated.
    """
    if not tree.exists():
        return (0, 0)
    try:
        out = subprocess.run(
            ["grep", "-rn", "-E", rf"\b{re.escape(name)}\b", str(tree)],
            check=False, capture_output=True, text=True, timeout=60,
        )
        lines = [ln for ln in out.stdout.splitlines() if ln]
        if self_file is not None:
            sf = str(self_file)
            filtered = []
            self_prefix = f"{sf}:"
            for ln in lines:
                if ln.startswith(self_prefix):
                    parts = ln.split(":", 2)
                    if len(parts) >= 2 and parts[1].isdigit() \
                       and int(parts[1]) == self_line:
                        continue  # this is the def line; skip
                filtered.append(ln)
            lines = filtered
        line_count = len(lines)
        file_count = len({ln.split(":", 1)[0] for ln in lines})
        return (line_count, file_count)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return (0, 0)


# ---------- "Done" detection from past logs ---------------------------------

DONE_CONTEXT_RE = re.compile(
    r"(?:applied|verdict|tier\s*[1-3]|t[1-3]\b|"
    r"premise-weaken|monotone-strengthen|postcond-strengthen|additive|OK)",
    re.IGNORECASE,
)


def find_done_lemmas(logs_dir: Path) -> set[str]:
    """Scan past strengthen logs for lemma names marked as applied/done.

    Match is gated on the line containing a verdict / applied / tier /
    OK context word — avoids false positives like every backtick-name
    in any Pattern-letter row.
    """
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

def iter_thy_files(scan_root: Path):
    if scan_root.is_file():
        yield scan_root
    elif scan_root.is_dir():
        for thy in sorted(scan_root.rglob("*.thy")):
            yield thy


def run_auto_detectors(all_lemmas: list) -> dict[str, list]:
    """Run spec_strengthen_scan.py detectors that exist today."""
    return {
        "C": detect_C(all_lemmas),
        "B": detect_B(all_lemmas),
        "A": detect_A(all_lemmas),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
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
    args = ap.parse_args()

    scan_root = args.scan_root or Path(TARGET_SCAN_ROOTS[args.target])
    if not scan_root.exists():
        print(f"ERROR: scan-root not found: {scan_root}", file=sys.stderr)
        return 2

    # Parse lemmas
    all_lemmas = []
    for thy in iter_thy_files(scan_root):
        try:
            all_lemmas.extend(parse_thy_lemmas(thy))
        except Exception as e:
            print(f"warn: parse {thy}: {e}", file=sys.stderr)
    print(f"scanned {len(all_lemmas)} Hoare-triple lemmas under {scan_root}",
          file=sys.stderr)

    # Auto detectors (A/B/C)
    findings = run_auto_detectors(all_lemmas)
    done = find_done_lemmas(args.logs_dir)

    # Rank each pattern's auto-detected findings
    ranked: dict[str, list[dict]] = {}
    for pat in ("C", "B", "A"):
        rows = []
        for f in findings.get(pat, []):
            self_file = Path(f.file)
            line_hits, file_hits = grep_consumers(
                f.name, args.tree,
                self_file=self_file, self_line=f.line,
            )
            session = session_for_path(str(self_file))
            is_done = f.name in done
            weight = WEIGHTS[pat]
            roi_score = line_hits * weight
            rows.append({
                "name": f.name,
                "file": self_file.name,
                "file_path": str(self_file),
                "line": f.line,
                "session": session,
                "note": f.note,
                "pre": f.pre,
                "post": f.post,
                "consumers_lines": line_hits,
                "consumers_files": file_hits,
                "tier": TIER[pat],
                "tier_weight": weight,
                "roi_score": roi_score,
                "detector": DETECTOR[pat],
                "done": is_done,
            })
        rows.sort(key=lambda r: (r["done"], -r["roi_score"], -r["consumers_lines"]))
        ranked[pat] = rows[: args.limit]

    # G / D / E placeholders (no auto detector yet)
    placeholders = {
        "G": "manual — inspect each op's definition vs missing accessor frames; "
             "see references/spec-strengthen-patterns.md Pattern G",
        "D": "manual — search spec/abstract/**/Decode_A.thy etc. for postcond "
             "with `\\<le>` / `\\<subseteq>` that's actually `=`",
        "E": "run `python3 tools/critical_path/spec_coverage_matrix.py` "
             "(when available) to find ops missing compound `_invs`",
    }

    # Render markdown
    today = dt.date.today().isoformat()
    out_lines = [
        f"# Spec strengthening — ranked candidates ({today})",
        "",
        f"Generated by `tools/critical_path/rank_candidates.py "
        f"--target {args.target}`.",
        "",
        "**Ranking key**: ROI = consumer_lines × tier_weight, then "
        "consumer_lines.",
        "",
        "**Tier weights**: " + ", ".join(
            f"{p}={WEIGHTS[p]}" for p in PATTERN_ORDER
        ) + ".",
        "",
        f"**Past logs**: `{args.logs_dir}/AInvs-*.md` — lemma names "
        "appearing there with applied/verdict context are marked `[done]`.",
        "",
        f"**Pattern order** (Tier 1 → Tier 2 → Tier 3): "
        + " → ".join(PATTERN_ORDER) + ".",
        "",
        "---",
        "",
    ]

    for pat in PATTERN_ORDER:
        tier_label = TIER[pat]
        detector_label = DETECTOR[pat]
        weight = WEIGHTS[pat]
        out_lines.append(
            f"## {tier_label} · Pattern {pat} "
            f"(weight={weight}, detector={detector_label})"
        )
        out_lines.append("")

        if pat in ranked:
            rows = ranked[pat]
            not_done = sum(1 for r in rows if not r["done"])
            out_lines.append(f"_{not_done} un-done candidates_")
            out_lines.append("")
            if not rows:
                out_lines.append("(no candidates found by auto-detector)")
            else:
                out_lines.append(
                    "| Lemma | Session | File:Line "
                    "| Consumers (lines × files) | ROI | Status |"
                )
                out_lines.append(
                    "|---|---|---|---:|---:|---|"
                )
                for r in rows:
                    status = "[done]" if r["done"] else ""
                    out_lines.append(
                        f"| `{r['name']}` | {r['session']} "
                        f"| `{r['file']}:{r['line']}` "
                        f"| {r['consumers_lines']} × {r['consumers_files']} "
                        f"| {r['roi_score']} | {status} |"
                    )
        else:
            out_lines.append(f"**Detector status**: {detector_label}.")
            out_lines.append(f"**How to find candidates**: "
                             f"{placeholders.get(pat, 'manual')}")
        out_lines.append("")

    out_text = "\n".join(out_lines)
    if args.out:
        args.out.write_text(out_text, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(out_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
