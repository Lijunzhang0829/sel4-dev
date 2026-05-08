#!/usr/bin/env python3
"""
tools/critical_path/session_dedupe_scan.py
==========================================

P0.5 — Full session duplication scan.

WHAT THIS DOES
--------------
Reads heaps/db-archive/<session>.db for every session and extracts the
`theory_timings` BLOB (zstandard-compressed; field-separator \\x06 between
name/elapsed/cpu/gc).  Then computes, across all session pairs, which
theories appear in more than one session's timings record — i.e.,
theories that are processed during multiple sessions despite already being
present (proved) in an upstream session's heap.

The motivation is the P0 finding: CBaseRefine.db reports `Refine.Finalise_R`
at 424.3s, but Refine.db already reports it at 225.6s — meaning CBaseRefine
re-executes that work. P0 verified one direction (CBaseRefine vs Refine/AInvs);
this scan covers all 28 sessions, so the cost model used by the critical-path
report can attribute the *true* rebuild cost (sum across all sessions where
a theory appears) instead of underestimating.

OUTPUT
------
    reports/session-duplication-scan.md
    reports/session-duplication-scan.json   (machine-readable)

USAGE
-----
    python3 tools/critical_path/session_dedupe_scan.py

DEPENDENCIES
------------
    sqlite3   (stdlib)
    zstd      CLI from libzstd1 / zstd package (avoids needing `pip install zstandard`)
"""
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
DB_DIR = REPO / "heaps" / "db-archive"
OUT_MD = REPO / "reports" / "session-duplication-scan.md"
OUT_JSON = REPO / "reports" / "session-duplication-scan.json"

REC_RE = re.compile(
    r"name=([^\x06]+)\x06elapsed=([0-9.]+)\x06cpu=([0-9.]+)\x06gc=([0-9.]+)"
)


def extract_timings(db_path: Path) -> tuple[str | None, list[tuple[str, float, float, float]]]:
    """Return (session_name, [(theory, elapsed, cpu, gc), ...]). Empty list if blob missing."""
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT session_name, theory_timings FROM isabelle_session_info"
        ).fetchone()
    except sqlite3.OperationalError:
        return None, []
    if row is None or row[1] is None:
        return (row[0] if row else None), []
    name, blob = row
    with tempfile.NamedTemporaryFile(suffix=".zst", delete=False) as fz:
        fz.write(blob)
        fz_path = Path(fz.name)
    try:
        text = subprocess.check_output(["zstd", "-dcq", str(fz_path)]).decode("utf-8")
    finally:
        fz_path.unlink(missing_ok=True)
    recs = [
        (n, float(e), float(c), float(g)) for n, e, c, g in REC_RE.findall(text)
    ]
    return name, recs


def main() -> int:
    if not DB_DIR.is_dir():
        print(f"ERROR: {DB_DIR} not found", file=sys.stderr)
        return 2

    db_paths = sorted(DB_DIR.glob("*.db"))
    if not db_paths:
        print(f"ERROR: no .db files in {DB_DIR}", file=sys.stderr)
        return 2

    # session_name -> {theory_name -> (elapsed, cpu, gc)}
    by_session: dict[str, dict[str, tuple[float, float, float]]] = {}
    skipped = []
    for db in db_paths:
        sess, recs = extract_timings(db)
        if sess is None or not recs:
            skipped.append(db.stem)
            continue
        by_session[sess] = {n: (e, c, g) for n, e, c, g in recs}

    # theory -> [(session, elapsed, cpu, gc), ...]
    theory_in_sessions: dict[str, list[tuple[str, float, float, float]]] = defaultdict(list)
    for sess, thys in by_session.items():
        for thy, (e, c, g) in thys.items():
            theory_in_sessions[thy].append((sess, e, c, g))

    # Multi-session theories
    duplicated = {
        thy: rows for thy, rows in theory_in_sessions.items() if len(rows) > 1
    }

    # Per-session duplication footprint:
    #   "how many of session S's theories also appear elsewhere, and how much
    #    elapsed of S is spent on these duplicated theories?"
    session_dup_summary = []
    for sess, thys in by_session.items():
        own_total = sum(e for e, _, _ in thys.values())
        dup_thys = [thy for thy in thys if len(theory_in_sessions[thy]) > 1]
        dup_elapsed = sum(thys[thy][0] for thy in dup_thys)
        # For each duplicated theory in S, identify the OTHER sessions where it appears.
        coappear = defaultdict(float)  # other_session -> sum elapsed of overlapping theories in S
        for thy in dup_thys:
            for other_sess, _, _, _ in theory_in_sessions[thy]:
                if other_sess != sess:
                    coappear[other_sess] += thys[thy][0]
        session_dup_summary.append(
            {
                "session": sess,
                "n_theories": len(thys),
                "own_total_elapsed": round(own_total, 1),
                "n_duplicated_theories": len(dup_thys),
                "duplicated_elapsed_in_self": round(dup_elapsed, 1),
                "duplicated_pct": round(100 * dup_elapsed / max(own_total, 1e-9), 1),
                "co_appearing_sessions": dict(
                    sorted(
                        ((k, round(v, 1)) for k, v in coappear.items()),
                        key=lambda kv: -kv[1],
                    )
                ),
            }
        )
    session_dup_summary.sort(key=lambda r: -r["duplicated_elapsed_in_self"])

    # Top duplicated theories by total cost across all sessions
    theory_total_cost = []
    for thy, rows in duplicated.items():
        total_e = sum(e for _, e, _, _ in rows)
        total_c = sum(c for _, _, c, _ in rows)
        max_e = max(e for _, e, _, _ in rows)
        sessions_listed = sorted(rows, key=lambda r: -r[1])
        theory_total_cost.append(
            {
                "theory": thy,
                "n_sessions": len(rows),
                "total_elapsed_across_sessions": round(total_e, 1),
                "total_cpu_across_sessions": round(total_c, 1),
                "max_single_session_elapsed": round(max_e, 1),
                "duplication_overhead_estimate": round(total_e - max_e, 1),
                "per_session": [
                    {"session": s, "elapsed": round(e, 1), "cpu": round(c, 1), "gc": round(g, 1)}
                    for s, e, c, g in sessions_listed
                ],
            }
        )
    theory_total_cost.sort(key=lambda r: -r["total_elapsed_across_sessions"])

    # Aggregate stats
    grand_total_elapsed = sum(
        e for thys in by_session.values() for e, _, _ in thys.values()
    )
    grand_dup_overhead = sum(r["duplication_overhead_estimate"] for r in theory_total_cost)

    # ---- emit JSON ----
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(
            {
                "n_sessions_scanned": len(by_session),
                "skipped_sessions": skipped,
                "n_unique_theories": len(theory_in_sessions),
                "n_duplicated_theories": len(duplicated),
                "grand_total_elapsed_sum": round(grand_total_elapsed, 1),
                "grand_duplication_overhead_estimate": round(grand_dup_overhead, 1),
                "session_summary": session_dup_summary,
                "top_duplicated_theories": theory_total_cost[:50],
            },
            indent=2,
        )
        + "\n"
    )

    # ---- emit Markdown ----
    out = []
    out.append("# Session-level theory duplication scan")
    out.append("")
    out.append(
        "_Source: `heaps/db-archive/*.db` `theory_timings` BLOBs. Each BLOB lists "
        "every theory the session loaded/executed, with elapsed/cpu/gc. A theory "
        "appearing in multiple sessions' BLOBs is being **reprocessed** in each "
        "(empirically — see CBaseRefine vs Refine.Finalise_R: 225.6s → 424.3s with "
        "different cpu+gc, ruling out heap-load attribution)._"
    )
    out.append("")
    out.append("## Headline numbers")
    out.append("")
    out.append(f"- Sessions scanned: **{len(by_session)}**" + (f" (skipped {len(skipped)}: {', '.join(skipped)})" if skipped else ""))
    out.append(f"- Unique theory names across all sessions: **{len(theory_in_sessions)}**")
    out.append(f"- Theories duplicated across ≥2 sessions: **{len(duplicated)}**  ({100*len(duplicated)/max(len(theory_in_sessions),1):.1f}%)")
    out.append(f"- Sum of all per-theory elapsed across all sessions: **{grand_total_elapsed:.1f}s**")
    out.append(f"- Sum of duplication overhead (= sum_elapsed_across_sessions − max_single_session): **{grand_dup_overhead:.1f}s**")
    out.append("")
    out.append("> **Interpretation**: the duplication overhead is an upper-bound estimate of "
               "wall time potentially recoverable if each duplicated theory were processed "
               "exactly once across the session graph. Real recoverable wall is lower because "
               "of intra-session 8-thread parallelism (factor 3-6×) and because some "
               "re-execution may serve genuine purposes (locale re-interpretation in different "
               "ML contexts).")
    out.append("")

    # ---- per-session table ----
    out.append("## Per-session duplication footprint")
    out.append("")
    out.append("Sessions ranked by duplicated-elapsed (i.e., how much of THIS session's per-theory wall is on theories also processed elsewhere).")
    out.append("")
    out.append("| session | #theories | own Σelapsed | dup #thys | dup Σelapsed | dup % | top co-appearing sessions |")
    out.append("|---|---:|---:|---:|---:|---:|---|")
    for r in session_dup_summary:
        coapp = list(r["co_appearing_sessions"].items())[:3]
        coapp_str = "; ".join(f"{s}:{e:.0f}s" for s, e in coapp)
        out.append(
            f"| {r['session']} | {r['n_theories']} | {r['own_total_elapsed']:.1f} | "
            f"{r['n_duplicated_theories']} | {r['duplicated_elapsed_in_self']:.1f} | "
            f"{r['duplicated_pct']:.1f}% | {coapp_str} |"
        )
    out.append("")

    # ---- top duplicated theories ----
    out.append("## Top 30 theories by total cost across sessions")
    out.append("")
    out.append("| theory | #sessions | Σelapsed (all) | max single | overhead estimate | sessions (elapsed) |")
    out.append("|---|---:|---:|---:|---:|---|")
    for r in theory_total_cost[:30]:
        per = "; ".join(f"{p['session']}:{p['elapsed']:.0f}s" for p in r["per_session"])
        out.append(
            f"| `{r['theory']}` | {r['n_sessions']} | {r['total_elapsed_across_sessions']:.1f} | "
            f"{r['max_single_session_elapsed']:.1f} | {r['duplication_overhead_estimate']:.1f} | {per} |"
        )
    out.append("")
    out.append("## How the cost model uses this")
    out.append("")
    out.append(
        "For the four critical-path reports (`reports/critical-path-{proof,spec,haskell,c}.md`), "
        "each theory's weight is its **total elapsed across all sessions where it appears**, "
        "not its single-session elapsed. This avoids underestimating the wall-time impact of "
        "modifying a theory that is reprocessed by multiple downstream sessions due to the "
        "session inheritance pattern documented in [proof/ROOT:106-117]"
        "(../verification/l4v/proof/ROOT) (`session CBaseRefine = CSpec + sessions Refine ...`)."
    )

    OUT_MD.write_text("\n".join(out) + "\n")

    print(f"wrote {OUT_MD} and {OUT_JSON}")
    print(f"  sessions scanned: {len(by_session)} (skipped: {skipped})")
    print(f"  unique theories: {len(theory_in_sessions)}; duplicated: {len(duplicated)}")
    print(f"  grand total elapsed sum: {grand_total_elapsed:.1f}s")
    print(f"  duplication overhead estimate: {grand_dup_overhead:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
