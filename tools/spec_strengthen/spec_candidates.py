#!/usr/bin/env python3
r"""
tools/spec_strengthen/spec_candidates.py

Discover candidate spec strengthenings in a target session and emit a
ranked flat list of (lemma, file:line, consumers, suggested move).

The skill's Workflow Step 1 entry point. The Pattern A/B/C taxonomy
that used to drive the output is now an internal detector
implementation detail — the user-facing output is a single ranked
table with natural-language suggestions.

Usage:
  spec_candidates.py [--target spec|ainvs|refine|all]
                     [--scan-root DIR]
                     [--tree DIR]
                     [--logs-dir DIR]
                     [--limit N]
                     [--out FILE]

Ranking: consumer_lines × internal_weight (descending). Lemmas
already covered in `reports/spec-strengthen/AInvs-*.md` are marked
`[done]` and demoted to the bottom.

Session is auto-derived from the file path:
  spec/abstract/             → ASpec
  proof/invariant-abstract/  → AInvs
  proof/refine/              → Refine
  ...

For unfamiliar candidate shapes or false-positive risks, consult
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
    detect_paired_chain, detect_missing_functional, detect_unused_premise,
    Finding,
)


# Internal weights — bias ranking toward shapes with higher empirical
# ROI (premise removal > functional postcond > paired-chain cleanup).
# Hidden from user-facing output by design.
KIND_WEIGHT = {
    "unused-premise":     5,
    "missing-functional": 3,
    "paired-chain":       1,
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

def run_detectors(all_lemmas: list) -> list[Finding]:
    """Run every detector once and concatenate results.

    A single lemma may appear in multiple detector outputs (e.g. a
    paired-chain candidate that also has an unused premise). Dedup
    by (file, line, name) post-rank, preferring the higher-weighted
    finding.
    """
    out: list[Finding] = []
    out.extend(detect_unused_premise(all_lemmas))
    out.extend(detect_missing_functional(all_lemmas))
    out.extend(detect_paired_chain(all_lemmas))
    return out


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

    all_lemmas = []
    for thy in iter_thy_files(scan_root):
        try:
            all_lemmas.extend(parse_thy_lemmas(thy))
        except Exception as e:
            print(f"warn: parse {thy}: {e}", file=sys.stderr)
    print(f"scanned {len(all_lemmas)} Hoare-triple lemmas under {scan_root}",
          file=sys.stderr)

    findings = run_detectors(all_lemmas)
    done = find_done_lemmas(args.logs_dir)

    # Build rows (with consumer counts + dedup by name keeping max weight)
    by_name: dict[tuple[str, int, str], dict] = {}
    for f in findings:
        self_file = Path(f.file)
        line_hits, file_hits = grep_consumers(
            f.name, args.tree,
            self_file=self_file, self_line=f.line,
        )
        weight = KIND_WEIGHT.get(f.kind, 1)
        roi_score = line_hits * weight
        row = {
            "name": f.name,
            "file": self_file.name,
            "file_path": str(self_file),
            "line": f.line,
            "session": session_for_path(str(self_file)),
            "consumers_lines": line_hits,
            "consumers_files": file_hits,
            "_internal_weight": weight,
            "roi_score": roi_score,
            "suggested_move": f.suggested_move or f.note,
            "kind": f.kind,
            "done": f.name in done,
        }
        key = (str(self_file), f.line, f.name)
        existing = by_name.get(key)
        if existing is None or row["roi_score"] > existing["roi_score"]:
            by_name[key] = row

    rows = list(by_name.values())
    rows.sort(key=lambda r: (r["done"], -r["roi_score"], -r["consumers_lines"]))
    rows = rows[: args.limit]

    today = dt.date.today().isoformat()
    out_lines = [
        f"# Spec strengthening — candidates ({today})",
        "",
        f"Source: `{scan_root}`",
        "",
        "Single ranked list. Higher rows have higher downstream-surface ROI.",
        "Already-done lemmas (matched against past strengthen logs) are at",
        "the bottom with `[done]`. For unfamiliar shapes or false-positive",
        "risks, consult `references/spec-strengthen-playbook.md`.",
        "",
        "| Lemma | Session | File:Line | Consumers | Suggested move | Status |",
        "|---|---|---|---:|---|---|",
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
