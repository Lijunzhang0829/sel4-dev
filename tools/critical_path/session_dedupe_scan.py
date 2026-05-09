#!/usr/bin/env python3
"""
tools/critical_path/session_dedupe_scan.py
==========================================

P0.5 — Full session duplication scan, with mechanism + case studies.

WHAT THIS DOES
--------------
Reads heaps/db-archive/<session>.db for every session and extracts the
`theory_timings` BLOB (zstandard-compressed; field-separator \\x06 between
name/elapsed/cpu/gc).  Then computes:

  - Pairwise theory overlap across all session pairs.
  - For each session, which `sessions Y` ROOT declaration is causing
    its duplications (cross-reference parse_roots output).
  - Concrete trigger imports: which .thy files in this session import
    "Y.something", reproducing the exact source-line pulling Y in.
  - Pattern classification: A (high-dup, structural fix candidate),
    B (clean / leaf), C (spec broadcast).
  - Top-5 case studies with full per-theory diff (elapsed/cpu/gc on
    each side) for the heaviest dup pairs.

The output bundles the empirical data with the narrative explanations
needed to act on it.

OUTPUT
------
    reports/session-duplication-scan.md
    reports/session-duplication-scan.json

USAGE
-----
    python3 tools/critical_path/session_dedupe_scan.py

DEPENDENCIES
------------
    sqlite3   (stdlib)
    zstd      CLI from libzstd1 / zstd package
    tools/lemma_inventory/parse_roots.py  (sibling)
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
sys.path.insert(0, str(REPO / "tools" / "lemma_inventory"))
import parse_roots  # noqa: E402

DB_DIR = REPO / "heaps" / "db-archive"
L4V = REPO / "verification" / "l4v"
OUT_MD = REPO / "reports" / "session-duplication-scan.md"
OUT_JSON = REPO / "reports" / "session-duplication-scan.json"

REC_RE = re.compile(
    r"name=([^\x06]+)\x06elapsed=([0-9.]+)\x06cpu=([0-9.]+)\x06gc=([0-9.]+)"
)


# ----------------------------------------------------------------------
# Narrative content (encoded once, ships with every regeneration)
# ----------------------------------------------------------------------

PROBLEM_STATEMENT = {
    "summary": (
        "Several l4v sessions reprocess theories that have already been "
        "proven and cached in upstream sessions' heap files. The "
        "theory_timings BLOB inside each session's .db records every "
        "theory the session executed during its build; cross-referencing "
        "these BLOBs reveals that some theories appear in multiple BLOBs "
        "with different (often higher) elapsed times — i.e., the same "
        ".thy source was typechecked + proved more than once across the "
        "project, in different ML contexts."
    ),
    "what_we_detect": (
        "For every theory T present in BLOB(S) for ≥2 sessions S, we "
        "treat the cross-session duplication overhead as "
        "Σ elapsed_in_each_session − max_elapsed_in_any_one_session. "
        "This is an upper-bound estimate of the wall time recoverable if "
        "T were processed exactly once across the project."
    ),
    "what_the_numbers_mean": {
        "own_total_elapsed":
            "Sum of per-theory elapsed in this session's own BLOB. "
            "Equals what you'd see in heaps/build_log.txt's per-theory "
            "block for this session.",
        "duplicated_elapsed_in_self":
            "How much of own_total_elapsed is on theories that ALSO "
            "appear in some other session's BLOB.",
        "co_appearing_sessions":
            "{other_session: elapsed_in_self_on_overlapping_theories}. "
            "Tells you which sessions this one is duplicating work with.",
        "n_sessions (per theory)":
            "How many distinct sessions' BLOBs list this theory. >1 "
            "implies cross-session reprocessing.",
        "duplication_overhead (per theory)":
            "Σ elapsed across sessions − max elapsed in any one session. "
            "Conservative wall-recovery upper bound.",
    },
}

MECHANISM = {
    "isabelle_session_model": (
        "An Isabelle session declares its parent via `+ X` (heap merge: "
        "X.heap is deserialized into the new session's ML state at start) "
        "and may declare additional sessions via `sessions Y, Z, ...` "
        "(namespace declaration only — makes Y.foo and Z.bar resolvable "
        "from `imports` clauses, but does NOT merge their heaps)."
    ),
    "plus_X_semantics": (
        "`= X +` causes Isabelle to load X.heap directly. Theories in X "
        "are present as compiled images in the running ML state; they "
        "are NOT re-executed and contribute milliseconds-level loading "
        "cost only."
    ),
    "sessions_X_semantics": (
        "`sessions X` only declares X for name resolution. When a "
        "theory in the current session writes `imports \"X.foo\"`, "
        "Isabelle locates X.foo's source file via X's session dir, then "
        "EXECUTES it in the current session's ML state (NOT loaded from "
        "X.heap). This is the source of the cross-session duplication "
        "observed in P0.5."
    ),
    "why_elapsed_differs_across_sessions": (
        "When the same .thy is executed in two different sessions, the "
        "ambient ML state differs: each session has its own active simp "
        "rules, locale interpretations, type class instances, etc., "
        "inherited from its `+` parent. Tactic search spaces grow with "
        "rule set size, so the same proof script can take significantly "
        "longer in a more populated ML state. Example: Refine.Finalise_R "
        "takes 226s in Refine session (BaseRefine.heap-derived state) "
        "vs 424s in CBaseRefine session (CSpec.heap-derived state) — "
        "+88% wall, +24% cpu, +77% gc."
    ),
    "single_session_invocation_does_NOT_run_a_theory_twice": (
        "Within ONE `isabelle build CBaseRefine` invocation, each "
        ".thy file is processed exactly ONCE and its result cached in "
        "ML memory for subsequent imports. The 'duplication' is across "
        "DIFFERENT session-build invocations: `isabelle build Refine` "
        "processes Finalise_R once, then `isabelle build CBaseRefine` "
        "processes it ONE MORE time independently, totaling 2× across "
        "the project (NOT 45× within CBaseRefine's build)."
    ),
}

PATTERNS = {
    "A_high_dup_structural_fix_candidates": {
        "definition": (
            "Sessions whose ≥80% of own_total_elapsed lies on theories "
            "duplicated elsewhere. Caused by `sessions Y` ROOT "
            "declarations whose Y has a meta-theory (e.g., Refine.Refine) "
            "that gets pulled in via a single `imports` line and "
            "re-executes Y wholesale in the current session's ML state."
        ),
        "fix_strategy": (
            "(1) Refactor ROOT to make Y a `+` parent if Isabelle's "
            "single-parent constraint allows. (2) If Y and the current "
            "`+` parent have ML state conflicts, introduce a JointBase "
            "session merging both via cascaded `+` so the cost is paid "
            "once. (3) Reduce what Y's meta-theory re-exports so the "
            "current session only pulls in the subset it actually needs."
        ),
    },
    "B_clean_independent_work": {
        "definition": (
            "Sessions with 0% duplication — their theories do not "
            "appear in any other session's BLOB. These represent "
            "genuinely independent work and serve as templates for "
            "what 'clean' session boundaries look like."
        ),
        "fix_strategy": (
            "No fix needed. Notable that several large sessions "
            "(SimplExportAndRefine 2264s, HOL 379s) achieve 0% — "
            "demonstrating clean boundaries are achievable in l4v."
        ),
    },
    "C_spec_broadcast": {
        "definition": (
            "Sessions like ASpec where every theory appears in many "
            "downstream sessions but each individual occurrence is tiny "
            "(usually < 5s). Wall accumulates via fan-out, not "
            "per-occurrence cost."
        ),
        "fix_strategy": (
            "Lower priority than Pattern A. Fix only if a specific "
            "downstream session imports more of the spec than it uses."
        ),
    },
}

REMEDIATION_HINTS = {
    "fix_root_inheritance_pattern_A": (
        "Edit verification/l4v/proof/ROOT to convert `sessions Y` into "
        "`+ Y` (or build a JointBase). Trial-fix CBaseRefine first: "
        "swap line 106 from `= CSpec +` to `= Refine +` (and add CSpec "
        "via `sessions`), then `isabelle build CBaseRefine` and inspect. "
        "If ML state conflicts, the error will pinpoint which simp/locale "
        "needs reconciliation. Upper bound: 5519s wall in CBaseRefine "
        "alone could be reclaimed (98.6% of 5598.7s)."
    ),
    "split_meta_imports_pattern_A": (
        "If full ROOT change is too risky, instead split `Refine.Refine` "
        "(meta-theory that re-exports all 48 Refine theories) so the "
        "current session imports only the subset it actually needs. "
        "Use reports/critical-path-all.json fanout data to identify "
        "which Refine theories CBaseRefine downstream actually uses."
    ),
    "skip_proofs_environment_workaround": (
        "l4v already provides this: `proof/ROOT:111` declares the "
        "duplicated theories under condition=SKIP_DUPLICATED_PROOFS "
        "with quick_and_dirty + skip_proofs, allowing CI/dev workflows "
        "to bypass re-execution at the cost of weaker checking. Suitable "
        "for incremental dev cycles, not release builds."
    ),
    "split_heavy_theory_into_base_heavy_pattern_A": (
        "For specific high-weight duplicated theories (Refine.Finalise_R "
        "650s total, Refine.Invariants_H 359s, etc.), split the .thy "
        "into Base (stable interface lemmas downstream needs) + Heavy "
        "(internal proof bodies only Refine itself uses). Reduces what "
        "the duplication actually re-executes."
    ),
}


# ----------------------------------------------------------------------
# Data extraction
# ----------------------------------------------------------------------

def extract_timings(db_path: Path) -> tuple[str | None, list[tuple[str, float, float, float]]]:
    """Return (session_name, [(theory, elapsed, cpu, gc), ...])."""
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
    return name, [(n, float(e), float(c), float(g)) for n, e, c, g in REC_RE.findall(text)]


def find_trigger_imports(session_dir: str, target_sessions: list[str]) -> list[dict]:
    """Walk .thy files under session_dir; find concrete `imports "TARGET.X"` lines."""
    triggers = []
    sd = Path(session_dir)
    if not sd.is_dir():
        return triggers
    header_re = re.compile(
        r"theory\s+\S+\s+imports\s+(.*?)\s+(?:keywords\b|abbrevs\b|begin\b)",
        re.DOTALL,
    )
    for thy in sorted(sd.rglob("*.thy")):
        if not thy.is_file():
            continue
        try:
            content = thy.read_text(errors="replace")
        except (PermissionError, OSError):
            continue
        m = header_re.search(content)
        if not m:
            continue
        body = m.group(1)
        for target in target_sessions:
            esc = re.escape(target)
            for imp_m in re.finditer(rf'"({esc}\.[^"]+)"', body):
                triggers.append(
                    {
                        "thy_file": str(thy.relative_to(REPO)),
                        "import_text": imp_m.group(1),
                        "target_session": target,
                    }
                )
    return triggers


# ----------------------------------------------------------------------
# Analysis
# ----------------------------------------------------------------------

def classify_pattern(dup_pct: float, n_theories: int) -> str:
    if dup_pct >= 80.0:
        return "A_high_dup_structural_fix_candidate"
    if dup_pct == 0.0:
        return "B_clean_independent_work"
    if 0.0 < dup_pct < 80.0:
        # Heuristic for C: many theories, small per-theory contribution
        return "C_spec_broadcast" if n_theories > 50 else "B_partial_independent"
    return "uncategorized"


def build_session_summary(
    by_session: dict[str, dict[str, tuple[float, float, float]]],
    theory_in_sessions: dict[str, list[tuple[str, float, float, float]]],
    parsed_roots: dict,
) -> list[dict]:
    out = []
    sessions_data = parsed_roots["sessions"]
    for sess, thys in by_session.items():
        own_total = sum(e for e, _, _ in thys.values())
        dup_thys = [thy for thy in thys if len(theory_in_sessions[thy]) > 1]
        dup_elapsed = sum(thys[thy][0] for thy in dup_thys)
        dup_pct = round(100 * dup_elapsed / max(own_total, 1e-9), 1)
        coappear: dict[str, float] = defaultdict(float)
        for thy in dup_thys:
            for other_sess, _, _, _ in theory_in_sessions[thy]:
                if other_sess != sess:
                    coappear[other_sess] += thys[thy][0]
        # Inheritance trigger analysis
        sd = sessions_data.get(sess, {})
        plus_parent = sd.get("parent")
        sessions_decl = sd.get("imported_sessions", [])
        trigger_breakdown = []
        for declared in sessions_decl:
            mine_from_declared = [t for t in thys if t.startswith(f"{declared}.")]
            if mine_from_declared:
                d_elapsed = sum(thys[t][0] for t in mine_from_declared)
                trigger_breakdown.append(
                    {
                        "declared_via_sessions": declared,
                        "n_theories_pulled_in": len(mine_from_declared),
                        "elapsed_in_self": round(d_elapsed, 1),
                    }
                )
        # Pattern classification
        pattern = classify_pattern(dup_pct, len(thys))
        out.append(
            {
                "session": sess,
                "n_theories": len(thys),
                "own_total_elapsed": round(own_total, 1),
                "n_duplicated_theories": len(dup_thys),
                "duplicated_elapsed_in_self": round(dup_elapsed, 1),
                "duplicated_pct": dup_pct,
                "co_appearing_sessions": dict(
                    sorted(((k, round(v, 1)) for k, v in coappear.items()), key=lambda kv: -kv[1])
                ),
                "root_plus_parent": plus_parent,
                "root_imported_sessions": sessions_decl,
                "inheritance_trigger_breakdown": trigger_breakdown,
                "pattern": pattern,
            }
        )
    out.sort(key=lambda r: -r["duplicated_elapsed_in_self"])
    return out


def build_top_duplicated_theories(
    theory_in_sessions: dict[str, list[tuple[str, float, float, float]]],
) -> list[dict]:
    out = []
    for thy, rows in theory_in_sessions.items():
        if len(rows) < 2:
            continue
        total_e = sum(e for _, e, _, _ in rows)
        max_row = max(rows, key=lambda r: r[1])
        max_e = max_row[1]
        max_session = max_row[0]
        sessions_listed = sorted(rows, key=lambda r: -r[1])
        # delta_pct between max session and the one immediately below
        if len(sessions_listed) >= 2:
            second_e = sessions_listed[1][1]
            delta_pct = round(100 * (max_e - second_e) / max(second_e, 1e-9), 1)
        else:
            delta_pct = 0.0
        out.append(
            {
                "theory": thy,
                "n_sessions": len(rows),
                "total_elapsed_across_sessions": round(total_e, 1),
                "max_single_session_elapsed": round(max_e, 1),
                "max_session": max_session,
                "duplication_overhead_estimate": round(total_e - max_e, 1),
                "delta_max_vs_second_pct": delta_pct,
                "per_session": [
                    {
                        "session": s,
                        "elapsed": round(e, 1),
                        "cpu": round(c, 1),
                        "gc": round(g, 1),
                    }
                    for s, e, c, g in sessions_listed
                ],
            }
        )
    out.sort(key=lambda r: -r["total_elapsed_across_sessions"])
    return out


def build_case_studies(
    session_summary: list[dict],
    top_thys: list[dict],
    parsed_roots: dict,
    by_session_for_case: dict,
    n_cases: int = 5,
) -> list[dict]:
    """Build narrative case studies for top N high-dup PAIRS, perpetrator-side only.

    Deduplicates by frozenset of pair members; for each pair, picks the side that
    declares the OTHER via `sessions Y` (the actual duplicator that pulls the
    other's content in via `imports "Y.something"`). If neither side declares
    the other (rare), keeps the side with higher dup_elapsed_in_self.
    """
    cases = []
    sessions_data = parsed_roots["sessions"]
    seen_pairs: set[frozenset[str]] = set()

    # Group sessions by sname for quick lookup
    summary_by_session = {s["session"]: s for s in session_summary}

    for s in session_summary:
        sname = s["session"]
        if s["duplicated_elapsed_in_self"] < 50.0:
            break  # tail-too-small
        sd = sessions_data.get(sname)
        if sd is None or not s["co_appearing_sessions"]:
            continue
        top_other = next(iter(s["co_appearing_sessions"].keys()))
        pair = frozenset({sname, top_other})
        if pair in seen_pairs:
            continue

        # Determine perpetrator: theory names tell us who owns what.
        # Theory `Refine.Finalise_R` is owned by session `Refine`. If CBaseRefine.db
        # contains `Refine.Finalise_R`, CBaseRefine RE-EXECUTED Refine's theory →
        # CBaseRefine is the perpetrator.
        a_thys = set(by_session_for_case[sname].keys())
        b_thys = set(by_session_for_case[top_other].keys())
        overlap = a_thys & b_thys
        a_owned = sum(1 for t in overlap if t.startswith(f"{sname}."))
        b_owned = sum(1 for t in overlap if t.startswith(f"{top_other}."))

        # If neither side owns any of the overlap, this is a SHARED 3rd-party
        # situation (e.g., both load Lib.*, Monads.*, etc.). Skip — it's not a
        # perpetrator/victim case; documented separately in 'shared_dependency
        # _overhead'.
        if a_owned == 0 and b_owned == 0:
            seen_pairs.add(pair)
            continue

        if b_owned > a_owned:
            perpetrator, victim = sname, top_other
        else:
            perpetrator, victim = top_other, sname

        seen_pairs.add(pair)
        # Re-read the perpetrator's side data
        perp_sd = sessions_data.get(perpetrator)
        perp_summary = summary_by_session.get(perpetrator, {})
        if perp_sd is None or not perp_summary:
            continue

        sname = perpetrator
        sd = perp_sd
        s = perp_summary
        top_other = victim
        # Find a representative theory from the heaviest pair
        top_thy_in_pair = None
        for t in top_thys:
            sessions_in_t = {p["session"] for p in t["per_session"]}
            if sname in sessions_in_t and top_other in sessions_in_t:
                top_thy_in_pair = t
                break
        # Find concrete trigger imports from this session's source
        trigger_imports = find_trigger_imports(sd["session_dir"], [top_other])
        cases.append(
            {
                "case_id": f"{sname}_re-executes_{top_other}",
                "perpetrator_session": sname,
                "victim_session": top_other,
                "elapsed_in_perpetrator_on_overlap": s["co_appearing_sessions"].get(top_other, 0.0),
                "root_declaration": (
                    f"session {sname} in \"{Path(sd['session_dir']).name}\" "
                    f"= {sd['parent']} +"
                    + (
                        "\n  sessions\n    " + "\n    ".join(sd["imported_sessions"])
                        if sd["imported_sessions"]
                        else ""
                    )
                ),
                "concrete_trigger_imports": trigger_imports[:5],
                "spotlight_theory": (
                    {
                        "name": top_thy_in_pair["theory"],
                        "comparison": top_thy_in_pair["per_session"],
                        "delta_max_vs_second_pct": top_thy_in_pair["delta_max_vs_second_pct"],
                        "interpretation": (
                            "Same .thy source; ran in two different ML contexts. "
                            f"In {top_thy_in_pair['per_session'][0]['session']} "
                            f"(this session) it took {top_thy_in_pair['per_session'][0]['elapsed']}s; "
                            f"in {top_thy_in_pair['per_session'][1]['session']} (originating session) "
                            f"it took {top_thy_in_pair['per_session'][1]['elapsed']}s. "
                            "Difference reflects the different sets of active simp rules / "
                            "locale interpretations / type class instances inherited from "
                            "each session's `+` parent heap."
                        ),
                    }
                    if top_thy_in_pair
                    else None
                ),
                "applicable_pattern": s["pattern"],
            }
        )
    return cases


# ----------------------------------------------------------------------
# Markdown rendering
# ----------------------------------------------------------------------

def render_md(data: dict) -> str:
    out: list[str] = []
    out.append("# Session-level theory duplication scan (P0.5)")
    out.append("")
    out.append(
        "_Empirical scan of `heaps/db-archive/*.db` `theory_timings` BLOBs to "
        "quantify cross-session re-execution of the same .thy source. "
        "See [`tools/critical_path/session_dedupe_scan.py`](../tools/critical_path/session_dedupe_scan.py) "
        "for the scanner._"
    )
    out.append("")

    # ---- Headline ----
    out.append("## Headline")
    out.append("")
    out.append(f"- Sessions scanned: **{data['n_sessions_scanned']}**" + (
        f" (skipped {len(data['skipped_sessions'])}: {', '.join(data['skipped_sessions'])})"
        if data["skipped_sessions"]
        else ""
    ))
    out.append(f"- Unique theory names: **{data['n_unique_theories']}**")
    out.append(
        f"- Theories duplicated across ≥2 sessions: **{data['n_duplicated_theories']}** "
        f"({100*data['n_duplicated_theories']/max(data['n_unique_theories'],1):.1f}%)"
    )
    out.append(f"- Σ per-theory elapsed across all sessions: **{data['grand_total_elapsed_sum']}s**")
    out.append(
        f"- **Duplication overhead estimate**: **{data['grand_duplication_overhead_estimate']}s** "
        f"({100*data['grand_duplication_overhead_estimate']/max(data['grand_total_elapsed_sum'],1):.1f}%)"
    )
    out.append("")

    # ---- Problem statement ----
    out.append("## Problem statement")
    out.append("")
    ps = data["problem_statement"]
    out.append(f"**What this scan diagnoses.** {ps['summary']}")
    out.append("")
    out.append(f"**How we detect it.** {ps['what_we_detect']}")
    out.append("")
    out.append("**Field semantics**:")
    for k, v in ps["what_the_numbers_mean"].items():
        out.append(f"- `{k}` — {v}")
    out.append("")

    # ---- Mechanism ----
    out.append("## Mechanism")
    out.append("")
    mc = data["mechanism"]
    out.append(f"**Isabelle session model.** {mc['isabelle_session_model']}")
    out.append("")
    out.append(f"**`+ X` (heap merge).** {mc['plus_X_semantics']}")
    out.append("")
    out.append(f"**`sessions X` (namespace only).** {mc['sessions_X_semantics']}")
    out.append("")
    out.append(f"**Why elapsed differs across sessions.** {mc['why_elapsed_differs_across_sessions']}")
    out.append("")
    out.append(
        "> **Important.** "
        + mc["single_session_invocation_does_NOT_run_a_theory_twice"]
    )
    out.append("")

    # ---- Patterns ----
    out.append("## Patterns")
    out.append("")
    for pid, pdata in data["patterns"].items():
        out.append(f"### Pattern {pid}")
        out.append("")
        out.append(f"**Definition.** {pdata['definition']}")
        out.append("")
        out.append(f"**Fix strategy.** {pdata['fix_strategy']}")
        out.append("")

    # ---- Per-session table ----
    out.append("## Per-session footprint")
    out.append("")
    out.append(
        "Sessions ranked by `duplicated_elapsed_in_self`. The "
        "`inheritance_trigger_breakdown` column attributes duplications to "
        "the specific `sessions Y` ROOT declaration that pulled Y in."
    )
    out.append("")
    out.append("| session | pattern | #thys | own Σelapsed | dup #thys | dup Σelapsed | dup % | trigger via `sessions` | parent (`+`) |")
    out.append("|---|---|---:|---:|---:|---:|---:|---|---|")
    for r in data["session_summary"]:
        triggers = "; ".join(
            f"`{t['declared_via_sessions']}`→{t['elapsed_in_self']:.0f}s"
            for t in r["inheritance_trigger_breakdown"]
        ) or "—"
        parent = f"`{r['root_plus_parent']}`" if r["root_plus_parent"] else "—"
        out.append(
            f"| `{r['session']}` | {r['pattern'].split('_', 1)[0]} | "
            f"{r['n_theories']} | {r['own_total_elapsed']:.1f} | "
            f"{r['n_duplicated_theories']} | {r['duplicated_elapsed_in_self']:.1f} | "
            f"{r['duplicated_pct']:.1f}% | {triggers} | {parent} |"
        )
    out.append("")

    # ---- Case studies ----
    out.append("## Case studies (top 5 by dup elapsed)")
    out.append("")
    for case in data["case_studies"]:
        out.append(f"### {case['case_id']}")
        out.append("")
        out.append(f"- **Perpetrator (does the re-execution)**: `{case['perpetrator_session']}`")
        out.append(f"- **Victim (its theories get re-executed)**: `{case['victim_session']}`")
        out.append(f"- **Elapsed in perpetrator on overlapping theories**: {case['elapsed_in_perpetrator_on_overlap']}s")
        out.append(f"- **Pattern**: `{case['applicable_pattern']}`")
        out.append("")
        out.append("**ROOT declaration**:")
        out.append("")
        out.append("```")
        out.append(case["root_declaration"])
        out.append("```")
        out.append("")
        if case["concrete_trigger_imports"]:
            out.append("**Concrete trigger imports** (source lines that pull in the duplicated session):")
            out.append("")
            for tr in case["concrete_trigger_imports"]:
                out.append(f"- `{tr['thy_file']}` → `imports \"{tr['import_text']}\"`")
            out.append("")
        st = case.get("spotlight_theory")
        if st:
            out.append(f"**Spotlight theory `{st['name']}`** — same .thy source, two ML contexts:")
            out.append("")
            out.append("| in session | elapsed (s) | cpu (s) | gc (s) |")
            out.append("|---|---:|---:|---:|")
            for ps_ in st["comparison"]:
                out.append(f"| `{ps_['session']}` | {ps_['elapsed']:.1f} | {ps_['cpu']:.1f} | {ps_['gc']:.1f} |")
            out.append("")
            out.append(f"_{st['interpretation']}_")
            out.append("")

    # ---- Shared 3rd-party dependency overhead ----
    out.append("## Shared third-party dependency overhead")
    out.append("")
    out.append(
        "These are theories owned by sessions NOT in the canonical 28-session "
        "build set (libraries like `Lib`, `Monads`, `Eisbach_Tools`, etc.) that "
        "appear in MULTIPLE canonical sessions' BLOBs. Each canonical session "
        "re-executes them independently because they're declared via `sessions Y` "
        "rather than heap-merged via `+ Y`. This is a different structural pattern "
        "from perpetrator/victim — there's no single 'duplicator' to fault. "
        "Remediation requires either making the third-party session a `+` parent "
        "of all consumers, or introducing a shared parent that already merges it."
    )
    out.append("")
    out.append("| 3rd-party session | #thys | in #canonical sessions | Σelapsed (all appearances) | avg/appearance |")
    out.append("|---|---:|---:|---:|---:|")
    for r in data.get("shared_dependency_overhead", [])[:20]:
        out.append(
            f"| `{r['third_party_session']}` | {r['n_theories']} | "
            f"{r['appearing_in_n_canonical_sessions']} | "
            f"{r['total_elapsed_across_appearances']:.1f} | "
            f"{r['avg_elapsed_per_appearance']:.2f}s |"
        )
    out.append("")

    # ---- Top duplicated theories ----
    out.append("## Top 30 duplicated theories by total cross-session cost")
    out.append("")
    out.append("| theory | #sess | Σelapsed (all) | max single | max session | overhead | Δmax/2nd % | per-session breakdown |")
    out.append("|---|---:|---:|---:|---|---:|---:|---|")
    for r in data["top_duplicated_theories"][:30]:
        per = "; ".join(f"`{p['session']}`:{p['elapsed']:.0f}s" for p in r["per_session"])
        out.append(
            f"| `{r['theory']}` | {r['n_sessions']} | "
            f"{r['total_elapsed_across_sessions']:.1f} | "
            f"{r['max_single_session_elapsed']:.1f} | `{r['max_session']}` | "
            f"{r['duplication_overhead_estimate']:.1f} | "
            f"{r['delta_max_vs_second_pct']:.1f}% | {per} |"
        )
    out.append("")

    # ---- Remediation hints ----
    out.append("## Remediation hints")
    out.append("")
    for k, v in data["remediation_hints"].items():
        out.append(f"### {k}")
        out.append("")
        out.append(v)
        out.append("")

    # ---- Footer ----
    out.append("## How downstream tools use this")
    out.append("")
    out.append(
        "`reports/critical-path-{proof,spec,haskell,c}.md` and `dag.py` use "
        "the `top_duplicated_theories[].total_elapsed_across_sessions` field "
        "as each theory's weight in the CPM cost model — capturing the "
        "true rebuild cost (vs the lower single-session elapsed). The "
        "`session_summary[].inheritance_trigger_breakdown` field directly "
        "identifies the ROOT lines a structural-fix branch would edit."
    )
    out.append("")

    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main() -> int:
    if not DB_DIR.is_dir():
        print(f"ERROR: {DB_DIR} not found", file=sys.stderr)
        return 2

    db_paths = sorted(DB_DIR.glob("*.db"))
    if not db_paths:
        print(f"ERROR: no .db files in {DB_DIR}", file=sys.stderr)
        return 2

    by_session: dict[str, dict[str, tuple[float, float, float]]] = {}
    skipped: list[str] = []
    for db in db_paths:
        sess, recs = extract_timings(db)
        if sess is None or not recs:
            skipped.append(db.stem)
            continue
        by_session[sess] = {n: (e, c, g) for n, e, c, g in recs}

    theory_in_sessions: dict[str, list[tuple[str, float, float, float]]] = defaultdict(list)
    for sess, thys in by_session.items():
        for thy, (e, c, g) in thys.items():
            theory_in_sessions[thy].append((sess, e, c, g))
    duplicated = {thy: rows for thy, rows in theory_in_sessions.items() if len(rows) > 1}

    print(f"loaded {len(by_session)} session BLOBs ({len(skipped)} skipped); "
          f"{len(theory_in_sessions)} unique theories, {len(duplicated)} duplicated",
          file=sys.stderr)

    print("parsing l4v ROOTs ...", file=sys.stderr)
    parsed_roots = parse_roots.parse_all_roots(L4V.resolve(), arch="ARM")

    session_summary = build_session_summary(by_session, theory_in_sessions, parsed_roots)
    top_thys = build_top_duplicated_theories(theory_in_sessions)
    case_studies = build_case_studies(session_summary, top_thys, parsed_roots, by_session, n_cases=5)

    # Shared third-party dependency overhead: theories owned by sessions NOT
    # in the canonical 28 (i.e., libraries like Lib, Monads, Eisbach_Tools,
    # Lib.* etc.) that appear in many canonical sessions' BLOBs because each
    # canonical session re-executes them on its own (no `+` heap merge).
    canonical_sessions = set(by_session.keys())
    third_party: dict[str, dict] = {}  # owner_session -> stats
    for thy, rows in theory_in_sessions.items():
        owner = thy.split(".", 1)[0] if "." in thy else thy
        if owner in canonical_sessions:
            continue  # owned by a canonical session; covered by case studies
        if len(rows) < 2:
            continue
        slot = third_party.setdefault(
            owner,
            {"theories": set(), "total_elapsed_across_sessions": 0.0,
             "appearing_in_sessions": set(), "n_appearances": 0},
        )
        slot["theories"].add(thy)
        for sess, e, _, _ in rows:
            slot["total_elapsed_across_sessions"] += e
            slot["appearing_in_sessions"].add(sess)
            slot["n_appearances"] += 1
    third_party_summary = sorted(
        [
            {
                "third_party_session": k,
                "n_theories": len(v["theories"]),
                "appearing_in_n_canonical_sessions": len(v["appearing_in_sessions"]),
                "appearing_sessions": sorted(v["appearing_in_sessions"]),
                "total_elapsed_across_appearances": round(v["total_elapsed_across_sessions"], 1),
                "avg_elapsed_per_appearance": round(
                    v["total_elapsed_across_sessions"] / max(v["n_appearances"], 1), 2
                ),
            }
            for k, v in third_party.items()
        ],
        key=lambda r: -r["total_elapsed_across_appearances"],
    )

    grand_total = sum(e for thys in by_session.values() for e, _, _ in thys.values())
    grand_overhead = sum(r["duplication_overhead_estimate"] for r in top_thys)

    data = {
        "scan_metadata": {
            "n_sessions_scanned": len(by_session),
            "skipped_sessions": skipped,
            "n_unique_theories": len(theory_in_sessions),
            "n_duplicated_theories": len(duplicated),
            "grand_total_elapsed_sum": round(grand_total, 1),
            "grand_duplication_overhead_estimate": round(grand_overhead, 1),
        },
        # Backward-compatible fields (consumed by dag.py)
        "n_sessions_scanned": len(by_session),
        "skipped_sessions": skipped,
        "n_unique_theories": len(theory_in_sessions),
        "n_duplicated_theories": len(duplicated),
        "grand_total_elapsed_sum": round(grand_total, 1),
        "grand_duplication_overhead_estimate": round(grand_overhead, 1),
        # New narrative sections
        "problem_statement": PROBLEM_STATEMENT,
        "mechanism": MECHANISM,
        "patterns": PATTERNS,
        "remediation_hints": REMEDIATION_HINTS,
        # Augmented data
        "session_summary": session_summary,
        "top_duplicated_theories": top_thys[:50],
        "case_studies": case_studies,
        "shared_dependency_overhead": third_party_summary,
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(data, indent=2) + "\n")
    OUT_MD.write_text(render_md(data))

    print(f"wrote {OUT_MD} ({sum(1 for _ in open(OUT_MD))} lines)")
    print(f"wrote {OUT_JSON} ({sum(1 for _ in open(OUT_JSON))} lines)")
    print(f"  case studies: {len(case_studies)}")
    print(f"  pattern A sessions: {sum(1 for s in session_summary if s['pattern'].startswith('A_'))}")
    print(f"  pattern B sessions: {sum(1 for s in session_summary if s['pattern'].startswith('B_'))}")
    print(f"  pattern C sessions: {sum(1 for s in session_summary if s['pattern'].startswith('C_'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
