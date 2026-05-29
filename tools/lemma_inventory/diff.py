"""Diff two lemma-inventory snapshots.

Usage:
    python3 tools/lemma_inventory/diff.py BEFORE.db AFTER.db [--out diff.md]

Reports five categories:
  removed_lemmas       — name in BEFORE missing in AFTER (matched by (session, name))
  added_lemmas         — name in AFTER not in BEFORE
  changed_statements   — same (session, name) but statement_sha256 differs
  new_sorry            — same (session, name) but went from has_sorry=0 → has_sorry=1
  resolved_sorry       — went from has_sorry=1 → has_sorry=0 (positive change, but reported)

Also reports:
  removed_theories     — theory file dropped
  added_theories       — theory file added
  session_set_changed  — sessions added/removed in ROOT files

Exit code:
  0  if no removed_lemmas / changed_statements / new_sorry
  1  otherwise (diff is "destructive" wrt inventory invariants)

The "(session, name)" key is the inventory's identity claim: the same logical
lemma keeps its name within its session even if the surrounding theory file is
restructured. (Anonymous lemmas key by (theory, line, kind) instead.)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path


def load(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT name FROM sessions")
    sessions = {r[0] for r in cur.fetchall()}

    cur.execute("SELECT path FROM theories")
    theories = {r[0] for r in cur.fetchall()}

    cur.execute(
        "SELECT session, name, theory, line, kind, statement_sha256, has_sorry, is_anonymous, attributes "
        "FROM lemmas"
    )
    rows = cur.fetchall()
    # Identity key:
    #   named lemma: (session, theory, name)  — Isabelle facts are theory-scoped;
    #     the same name may legally appear in two theories of the same session
    #     (e.g. `transferCaps_corres` in Ipc_R.thy AND Tcb_R.thy).
    #   anonymous:   (theory, line, kind)     — surrogate, will false-match across edits
    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for sess, name, theory, line, kind, sha, sorry, anon, attrs in rows:
        if anon:
            key = ("ANON", theory, line, kind)
        else:
            key = ("NAMED", sess, theory, name)
        by_key[key].append(dict(
            session=sess, name=name, theory=theory, line=line, kind=kind,
            sha=sha, sorry=bool(sorry), anon=bool(anon), attrs=attrs,
        ))

    conn.close()
    return dict(sessions=sessions, theories=theories, lemmas_by_key=by_key, total=len(rows))


def diff(before: dict, after: dict) -> dict:
    b = before["lemmas_by_key"]
    a = after["lemmas_by_key"]
    b_keys = set(b.keys())
    a_keys = set(a.keys())

    removed_keys = b_keys - a_keys
    added_keys = a_keys - b_keys
    common = b_keys & a_keys

    removed_lemmas = []
    for k in removed_keys:
        for r in b[k]:
            removed_lemmas.append(r)
    removed_lemmas.sort(key=lambda r: (r["session"] or "", r["theory"], r["line"]))

    added_lemmas = []
    for k in added_keys:
        for r in a[k]:
            added_lemmas.append(r)
    added_lemmas.sort(key=lambda r: (r["session"] or "", r["theory"], r["line"]))

    changed = []
    new_sorry = []
    resolved_sorry = []
    moved = []
    for k in common:
        b_rows = b[k]
        a_rows = a[k]
        # If a key has duplicates (same name appearing twice in same session),
        # compare element-wise using zip; surplus side is added/removed.
        for br, ar in zip(b_rows, a_rows):
            if br["sha"] != ar["sha"]:
                changed.append(dict(key=k, before=br, after=ar))
            if (not br["sorry"]) and ar["sorry"]:
                new_sorry.append(dict(key=k, before=br, after=ar))
            if br["sorry"] and (not ar["sorry"]):
                resolved_sorry.append(dict(key=k, before=br, after=ar))
            if br["theory"] != ar["theory"] or br["line"] != ar["line"]:
                moved.append(dict(key=k, before=br, after=ar))

    return dict(
        removed_lemmas=removed_lemmas,
        added_lemmas=added_lemmas,
        changed_statements=changed,
        new_sorry=new_sorry,
        resolved_sorry=resolved_sorry,
        moved=moved,
        removed_theories=sorted(before["theories"] - after["theories"]),
        added_theories=sorted(after["theories"] - before["theories"]),
        removed_sessions=sorted(before["sessions"] - after["sessions"]),
        added_sessions=sorted(after["sessions"] - before["sessions"]),
        total_before=before["total"],
        total_after=after["total"],
    )


def _summarise_moves(moves: list[dict]) -> list[tuple[str, int, int]]:
    """Group moves by (theory, line_shift). Returns list of (theory, shift, count)."""
    from collections import Counter
    c = Counter()
    for m in moves:
        # within-theory moves only — cross-theory moves are kept verbatim by caller
        if m["before"]["theory"] == m["after"]["theory"]:
            shift = m["after"]["line"] - m["before"]["line"]
            c[(m["before"]["theory"], shift)] += 1
    return sorted(c.items(), key=lambda kv: (-kv[1], kv[0][0]))


def render_markdown(d: dict, before: Path, after: Path, show_moves: bool = False) -> str:
    lines = []
    lines.append(f"# Inventory diff — `{before.name}` → `{after.name}`\n")
    lines.append(f"- total lemmas before: **{d['total_before']}**")
    lines.append(f"- total lemmas after:  **{d['total_after']}**")
    lines.append(f"- delta: **{d['total_after'] - d['total_before']:+d}**")
    lines.append("")

    def _section(title: str, rows: list, render):
        lines.append(f"## {title} ({len(rows)})")
        if not rows:
            lines.append("_(none)_\n")
            return
        for r in rows[:200]:
            lines.append(render(r))
        if len(rows) > 200:
            lines.append(f"\n_... and {len(rows)-200} more (truncated)_")
        lines.append("")

    if d["removed_sessions"] or d["added_sessions"]:
        lines.append("## Sessions")
        if d["removed_sessions"]:
            lines.append(f"- removed: {', '.join(d['removed_sessions'])}")
        if d["added_sessions"]:
            lines.append(f"- added: {', '.join(d['added_sessions'])}")
        lines.append("")

    if d["removed_theories"] or d["added_theories"]:
        lines.append("## Theories")
        if d["removed_theories"]:
            lines.append(f"- removed ({len(d['removed_theories'])}):")
            for t in d["removed_theories"][:50]:
                lines.append(f"  - `{t}`")
            if len(d["removed_theories"]) > 50:
                lines.append(f"  - _... +{len(d['removed_theories'])-50} more_")
        if d["added_theories"]:
            lines.append(f"- added ({len(d['added_theories'])}):")
            for t in d["added_theories"][:50]:
                lines.append(f"  - `{t}`")
            if len(d["added_theories"]) > 50:
                lines.append(f"  - _... +{len(d['added_theories'])-50} more_")
        lines.append("")

    _section(
        "Removed lemmas (BLOCKING)",
        d["removed_lemmas"],
        lambda r: f"- `{r['session']}::{r['name']}` was at `{r['theory']}:{r['line']}`",
    )
    _section(
        "New `sorry` / `oops` (BLOCKING)",
        d["new_sorry"],
        lambda r: f"- `{r['after']['session']}::{r['after']['name']}` at `{r['after']['theory']}:{r['after']['line']}`",
    )
    _section(
        "Statement changed (review carefully)",
        d["changed_statements"],
        lambda r: (
            f"- `{r['after']['session']}::{r['after']['name']}` at `{r['after']['theory']}:{r['after']['line']}`\n"
            f"   sha {r['before']['sha'][:12]} → {r['after']['sha'][:12]}"
        ),
    )
    _section(
        "Resolved sorry (informational)",
        d["resolved_sorry"],
        lambda r: f"- `{r['after']['session']}::{r['after']['name']}` at `{r['after']['theory']}:{r['after']['line']}`",
    )
    if show_moves:
        _section(
            "Moved (theory or line changed; sha equal)",
            d["moved"],
            lambda r: (
                f"- `{r['after']['session']}::{r['after']['name']}`: "
                f"`{r['before']['theory']}:{r['before']['line']}` → "
                f"`{r['after']['theory']}:{r['after']['line']}`"
            ),
        )
    else:
        within_theory = [m for m in d["moved"] if m["before"]["theory"] == m["after"]["theory"]]
        cross_theory = [m for m in d["moved"] if m["before"]["theory"] != m["after"]["theory"]]
        groups = _summarise_moves(within_theory)
        lines.append(f"## Moved within theory ({len(within_theory)}, summarised)")
        if not groups:
            lines.append("_(none)_\n")
        else:
            lines.append("(One upstream edit usually shifts every lemma below it. Pass `--show-moves` for the verbatim list.)")
            lines.append("| theory | line shift | lemmas |")
            lines.append("|---|---:|---:|")
            for (thy, shift), n in groups[:30]:
                lines.append(f"| `{thy}` | {shift:+d} | {n} |")
            if len(groups) > 30:
                lines.append(f"\n_... and {len(groups)-30} more (theory, shift) pairs_")
            lines.append("")
        _section(
            "Moved across theories",
            cross_theory,
            lambda r: (
                f"- `{r['after']['session']}::{r['after']['name']}`: "
                f"`{r['before']['theory']}:{r['before']['line']}` → "
                f"`{r['after']['theory']}:{r['after']['line']}`"
            ),
        )

    _section(
        "Added lemmas (informational)",
        d["added_lemmas"],
        lambda r: (
            f"- `{r['session']}::{r['name']}` at `{r['theory']}:{r['line']}`"
            + (" **[has sorry/oops]**" if r['sorry'] else "")
        ),
    )

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("before", type=Path)
    ap.add_argument("after", type=Path)
    ap.add_argument("--out", type=Path, default=None,
                    help="Write markdown report to this path (else stdout)")
    ap.add_argument("--json", type=Path, default=None,
                    help="Also write a JSON dump for machine consumption")
    ap.add_argument("--show-moves", action="store_true",
                    help="Include the 'Moved' section verbatim. By default moves are "
                         "summarised per (theory, line-shift) since one edit usually "
                         "shifts many lemmas in a file uniformly.")
    args = ap.parse_args()

    before = load(args.before)
    after = load(args.after)
    d = diff(before, after)

    md = render_markdown(d, args.before, args.after, show_moves=args.show_moves)
    if args.out:
        args.out.write_text(md)
        print(f"Wrote {args.out}")
    else:
        print(md)

    if args.json:
        # JSON cannot serialize tuple keys
        ser = {k: v for k, v in d.items()}
        args.json.write_text(json.dumps(ser, indent=2, default=str))
        print(f"Wrote {args.json}")

    blocking = (
        len(d["removed_lemmas"]) + len(d["new_sorry"]) + len(d["changed_statements"])
    )
    print(f"\nBlocking changes: removed={len(d['removed_lemmas'])} "
          f"new_sorry={len(d['new_sorry'])} changed_statements={len(d['changed_statements'])}")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
