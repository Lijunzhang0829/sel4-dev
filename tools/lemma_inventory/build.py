"""Build a baseline inventory of all lemmas in l4v.

Output: SQLite DB + summary markdown.

Schema (SQLite):
  sessions(name TEXT PRIMARY KEY, parent TEXT, session_dir TEXT,
           imported_sessions TEXT, root_file TEXT)
  theories(path TEXT PRIMARY KEY, session TEXT, total_lemmas INT, total_sorry INT)
  lemmas(theory TEXT, line INT, kind TEXT, name TEXT, statement_sha256 TEXT,
         attributes TEXT, has_sorry INT, is_anonymous INT, session TEXT,
         PRIMARY KEY (theory, name, line))
  meta(key TEXT PRIMARY KEY, value TEXT)

Scope filter: by default we walk every .thy under <l4v_root> but exclude:
  - non-ARM arch directories: any path component in {AARCH64, X64, RISCV64}
  - test / spec_test / tutorial dirs flagged via --exclude
The "scope" is reported in meta and the summary markdown.

Usage:
  python3 tools/lemma_inventory/build.py \
      --l4v verification/l4v \
      --out reports/inventory/baseline.db \
      --summary reports/inventory/summary.md
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from extract_lemmas import extract_lemmas
from parse_roots import assign_session_for_thy, parse_all_roots


EXCLUDED_ARCH_PARTS = {"AARCH64", "X64", "RISCV64", "ARM_HYP"}
# Subtrees that are not part of the ARM build pipeline
EXCLUDED_DIR_NAMES = {
    "camkes",         # CAmkES proofs, not part of base ARM build
    "tutorial",
    "EVTutorial",
    "doc",
    ".git",
}


def should_skip(thy_path: Path, l4v_root: Path) -> tuple[bool, str]:
    rel = thy_path.relative_to(l4v_root)
    parts = rel.parts
    for p in parts:
        if p in EXCLUDED_ARCH_PARTS:
            return True, f"excluded arch dir {p}"
        if p in EXCLUDED_DIR_NAMES:
            return True, f"excluded subtree {p}"
    # Auto-generated theory files under spec/cspec/c/build/*/generated/ — emitted by
    # the C parser on every build, not source. Owned by root inside the docker
    # build, often unreadable by the host user.
    if "generated" in parts and "build" in parts:
        return True, "auto-generated build artifact"
    return False, ""


def init_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
          name TEXT PRIMARY KEY,
          parent TEXT,
          session_dir TEXT,
          imported_sessions TEXT,
          root_file TEXT
        );
        CREATE TABLE IF NOT EXISTS theories (
          path TEXT PRIMARY KEY,
          session TEXT,
          total_lemmas INT,
          total_sorry INT
        );
        CREATE TABLE IF NOT EXISTS lemmas (
          theory TEXT,
          line INT,
          kind TEXT,
          name TEXT,
          statement_sha256 TEXT,
          statement_norm TEXT,
          attributes TEXT,
          has_sorry INT,
          is_anonymous INT,
          session TEXT,
          PRIMARY KEY (theory, name, line)
        );
        CREATE INDEX IF NOT EXISTS lemmas_name_idx ON lemmas(name);
        CREATE INDEX IF NOT EXISTS lemmas_session_idx ON lemmas(session);
        CREATE INDEX IF NOT EXISTS lemmas_sha256_idx ON lemmas(statement_sha256);
        CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          value TEXT
        );
        """
    )


def store_sessions(conn: sqlite3.Connection, parsed: dict) -> None:
    cur = conn.cursor()
    for name, sess in parsed["sessions"].items():
        cur.execute(
            "INSERT OR REPLACE INTO sessions(name, parent, session_dir, imported_sessions, root_file) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                name,
                sess["parent"],
                sess["session_dir"],
                json.dumps(sess["imported_sessions"]),
                sess["root_file"],
            ),
        )


def store_lemmas(conn: sqlite3.Connection, theory_rel: str, session: str | None,
                 lemmas: list[dict]) -> None:
    cur = conn.cursor()
    total_sorry = sum(1 for r in lemmas if r["has_sorry"])
    cur.execute(
        "INSERT OR REPLACE INTO theories(path, session, total_lemmas, total_sorry) VALUES (?, ?, ?, ?)",
        (theory_rel, session, len(lemmas), total_sorry),
    )
    rows = [
        (
            theory_rel,
            r["line"],
            r["kind"],
            r["name"],
            r["statement_sha256"],
            r["statement_norm"],
            json.dumps(r["attributes"]),
            int(r["has_sorry"]),
            int(r["is_anonymous"]),
            session,
        )
        for r in lemmas
    ]
    cur.executemany(
        "INSERT OR REPLACE INTO lemmas("
        "theory, line, kind, name, statement_sha256, statement_norm, attributes,"
        " has_sorry, is_anonymous, session) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def write_summary(conn: sqlite3.Connection, summary_path: Path, meta: dict) -> None:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), SUM(has_sorry) FROM lemmas")
    total_lemmas, total_sorry = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM theories")
    total_theories = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM sessions")
    total_sessions = cur.fetchone()[0]

    cur.execute(
        "SELECT session, COUNT(*) AS n_lemmas, SUM(has_sorry) AS n_sorry, COUNT(DISTINCT theory) AS n_theories "
        "FROM lemmas WHERE session IS NOT NULL GROUP BY session ORDER BY n_lemmas DESC"
    )
    per_session = cur.fetchall()

    cur.execute("SELECT kind, COUNT(*) FROM lemmas GROUP BY kind ORDER BY 2 DESC")
    per_kind = cur.fetchall()

    cur.execute(
        "SELECT path, total_lemmas FROM theories ORDER BY total_lemmas DESC LIMIT 20"
    )
    top_theories = cur.fetchall()

    cur.execute(
        "SELECT name, COUNT(*) AS n FROM lemmas WHERE is_anonymous = 0 GROUP BY name HAVING n > 1 ORDER BY n DESC LIMIT 20"
    )
    duplicate_names = cur.fetchall()

    cur.execute("SELECT COUNT(*) FROM lemmas WHERE is_anonymous = 1")
    anon_count = cur.fetchone()[0]

    md = []
    md.append("# Lemma inventory — baseline summary\n")
    md.append(f"_Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}_\n")
    md.append("")
    md.append("## Scope")
    md.append(f"- l4v root: `{meta['l4v_root']}`")
    md.append(f"- arch: `{meta['arch']}`")
    md.append(f"- excluded arch dirs: `{', '.join(sorted(EXCLUDED_ARCH_PARTS))}`")
    md.append(f"- excluded subtrees: `{', '.join(sorted(EXCLUDED_DIR_NAMES))}`")
    md.append(f"- DB: `{meta['db_path']}`")
    md.append("")
    md.append("## Totals")
    md.append(f"- sessions: **{total_sessions}**")
    md.append(f"- theories scanned: **{total_theories}**")
    md.append(f"- lemma-like declarations: **{total_lemmas}**")
    md.append(f"- with `sorry`/`oops`/`sledgehammer` in body: **{total_sorry}**")
    md.append(f"- anonymous lemmas: **{anon_count}**")
    md.append(f"- skipped (excluded): **{meta['skipped_count']}**")
    md.append(f"- unassigned (no owning session): **{meta['unassigned_count']}**")
    md.append("")
    md.append("## Lemmas per session (top 30)")
    md.append("| session | theories | lemmas | sorry/oops |")
    md.append("|---|---:|---:|---:|")
    for sess, n_lemmas, n_sorry, n_theories in per_session[:30]:
        md.append(f"| {sess} | {n_theories} | {n_lemmas} | {n_sorry or 0} |")
    md.append("")
    md.append("## Lemma-kind distribution")
    md.append("| kind | count |")
    md.append("|---|---:|")
    for kind, n in per_kind:
        md.append(f"| {kind} | {n} |")
    md.append("")
    md.append("## Top 20 theories by lemma count")
    md.append("| theory | lemmas |")
    md.append("|---|---:|")
    for thy, n in top_theories:
        md.append(f"| `{thy}` | {n} |")
    md.append("")
    if duplicate_names:
        md.append("## Duplicate lemma names across theories (top 20)")
        md.append("(Same name in multiple theories — check whether intentional or a hide_fact target.)")
        md.append("| name | occurrences |")
        md.append("|---|---:|")
        for name, n in duplicate_names:
            md.append(f"| `{name}` | {n} |")
    summary_path.write_text("\n".join(md) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--l4v", required=True, type=Path,
                    help="Path to l4v source root (e.g. verification/l4v)")
    ap.add_argument("--out", required=True, type=Path,
                    help="Path to SQLite DB output")
    ap.add_argument("--summary", required=True, type=Path,
                    help="Path to summary markdown output")
    ap.add_argument("--arch", default="ARM")
    args = ap.parse_args()

    l4v = args.l4v.resolve()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        args.out.unlink()

    print(f"[1/4] Parsing ROOT files under {l4v} ...")
    parsed = parse_all_roots(l4v, arch=args.arch)
    print(f"      Found {len(parsed['sessions'])} sessions.")

    print(f"[2/4] Walking .thy files ...")
    all_thy = sorted(l4v.rglob("*.thy"))
    print(f"      Found {len(all_thy)} .thy files; applying scope filter ...")

    conn = sqlite3.connect(args.out)
    init_schema(conn)
    store_sessions(conn, parsed)

    skipped = 0
    unassigned = 0
    processed = 0
    t0 = time.time()
    for thy in all_thy:
        skip, reason = should_skip(thy, l4v)
        if skip:
            skipped += 1
            continue
        try:
            lemmas = extract_lemmas(thy)
        except Exception as e:
            print(f"[warn] failed to extract {thy}: {e}")
            continue
        session = assign_session_for_thy(thy, parsed)
        if session is None:
            unassigned += 1
        rel = str(thy.relative_to(l4v))
        store_lemmas(conn, rel, session, lemmas)
        processed += 1
        if processed % 200 == 0:
            print(f"      ... {processed} theories processed ({time.time()-t0:.1f}s)")
    conn.commit()

    print(f"[3/4] Wrote {processed} theories ({skipped} skipped, {unassigned} unassigned).")

    meta = dict(
        l4v_root=str(l4v),
        arch=args.arch,
        db_path=str(args.out.resolve()),
        skipped_count=skipped,
        unassigned_count=unassigned,
        generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    cur = conn.cursor()
    for k, v in meta.items():
        cur.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (k, str(v)))
    conn.commit()

    print(f"[4/4] Writing summary to {args.summary} ...")
    write_summary(conn, args.summary, meta)
    conn.close()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
