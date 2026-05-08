#!/usr/bin/env python3
"""
tools/assemble_build_log.py
===========================

Stage 2 of the canonical rebuild pipeline.

WHAT IT DOES
------------
Merges two artifacts produced by tools/rebuild_canonical.sh into the final,
human-readable timing record at heaps/build_log.txt:

    heaps/build_log.tuned.txt      session-level Timing lines + canonical
                                   config header (from `isabelle build -v`
                                   stdout, captured per-session)
    heaps/db-archive/<sess>.db     session sqlite databases containing the
                                   theory_timings blob (zstandard-compressed,
                                   one row per theory file with elapsed/cpu/gc)

The merged output mirrors the historical heaps/build_log.txt format:

    [canonical config header — copied verbatim from .tuned.txt]
    SESSION OVERVIEW table   — one row per session: elapsed, cpu, gc, factor,
                                #theories, sum-of-theory elapsed/cpu
    PER-THEORY TIMINGS       — one block per session, theories sorted by
                                elapsed desc, with totals line

WHY TWO STAGES
--------------
Session-level Timing lines are emitted on stdout by `isabelle build -v` and
are easy to grep; per-theory timings live inside the session .db file as a
zstandard-compressed BLOB column on isabelle_session_info.theory_timings,
encoded as a record stream with \\x06 field separators (Isabelle 2024
internal format). Decoding that requires sqlite + zstandard, so we keep it
out of the bash rebuild driver and do it here in Python.

INPUTS / OUTPUTS
----------------
    in:   heaps/build_log.tuned.txt       (produced by rebuild_canonical.sh)
          heaps/db-archive/<sess>.db      (one per session, except UmmTypes)
    out:  heaps/build_log.txt             (overwritten)

DEPENDENCIES
------------
    sqlite3   (stdlib)
    zstandard (auto-installed via pip on first run if missing)

SPECIAL CASES
-------------
    UmmTypes — phantom session built by mk_umm_types.py; no .db is archived
    and no per-theory data is available. Shown in SESSION OVERVIEW with
    n=1, theory_elapsed=0.0; absent from the PER-THEORY TIMINGS block.

USAGE
-----
    python3 tools/assemble_build_log.py
"""
import sqlite3, sys, re, os
from pathlib import Path

try:
    import zstandard as zstd
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "zstandard"])
    import zstandard as zstd

REPO = Path(__file__).resolve().parent.parent
TUNED_LOG = REPO / "heaps" / "build_log.tuned.txt"
DB_DIR    = REPO / "heaps" / "db-archive"
OUT_LOG   = REPO / "heaps" / "build_log.txt"

# --- Parse Timing lines from tuned log ---
sessions = []  # list of (name, threads, elapsed, cpu, gc)
header_lines = []
in_header = True
with open(TUNED_LOG) as f:
    for line in f:
        if line.startswith("Timing "):
            in_header = False
            m = re.match(r"Timing (\S+) \((\d+) threads, ([\d.]+)s elapsed time, "
                         r"([\d.]+)s cpu time, ([\d.]+)s GC time", line)
            if m:
                sessions.append((m.group(1), int(m.group(2)),
                                 float(m.group(3)), float(m.group(4)), float(m.group(5))))
        elif in_header:
            header_lines.append(line.rstrip())

# --- Read per-theory timings from each .db ---
thy_data = {}  # session -> [(theory, elapsed, cpu, gc), ...]
for sess, *_ in sessions:
    db = DB_DIR / f"{sess}.db"
    if not db.exists():
        # UmmTypes builds via mk_umm_types.py in temp dir; .db not archived.
        continue
    try:
        con = sqlite3.connect(db)
        row = con.execute(
            "SELECT session_name, theory_timings FROM isabelle_session_info"
        ).fetchone()
    except sqlite3.OperationalError:
        continue
    if row is None or row[1] is None:
        continue
    text = zstd.ZstdDecompressor().decompress(row[1]).decode("utf-8")
    # Isabelle 2024 emits records with \x06 as internal field separator.
    matches = re.findall(
        r"name=([^\x06]+)\x06elapsed=([0-9.]+)\x06cpu=([0-9.]+)\x06gc=([0-9.]+)",
        text)
    recs = [(n, float(e), float(c), float(g)) for n, e, c, g in matches]
    if recs:
        thy_data[sess] = recs

# --- Compose output ---
out = []
# Re-use the canonical config header from build_log.tuned.txt verbatim.
out.extend(header_lines)
out.append("")
out.append("=" * 100)
out.append(f"SESSION OVERVIEW ({len(sessions)} sessions)")
out.append("=" * 100)
out.append("")
hdr = (f"{'Session':<22}{'elapsed':>10}{'cpu':>11}{'gc':>9}{'factor':>8}"
       f"{'theories':>10}{'thy_elapsed':>13}{'thy_cpu':>11}")
out.append(hdr)
out.append("-" * len(hdr))

total_e = total_c = total_g = 0.0
total_n = total_te = total_tc = 0
total_n_count = 0
for name, threads, e, c, g in sessions:
    factor = c / max(e, 0.001)
    if name in thy_data:
        n = len(thy_data[name])
        te = sum(r[1] for r in thy_data[name])
        tc = sum(r[2] for r in thy_data[name])
    else:
        n = 1 if name == "UmmTypes" else 0
        te = tc = 0.0
    out.append(f"{name:<22}{e:>10.1f}{c:>11.1f}{g:>9.1f}{factor:>8.2f}"
               f"{n:>10}{te:>13.1f}{tc:>11.1f}")
    total_e += e; total_c += c; total_g += g
    total_n += n; total_te += te; total_tc += tc
out.append("-" * len(hdr))
out.append(f"{'TOTAL':<22}{total_e:>10.1f}{total_c:>11.1f}{total_g:>9.1f}"
           f"{'':>8}{total_n:>10}{total_te:>13.1f}{total_tc:>11.1f}")
out.append("")
out.append("=" * 100)
out.append("PER-THEORY TIMINGS  (sorted by elapsed desc within each session)")
out.append("=" * 100)

NAME_W, NUM_W = 50, 10
for name, *_ in sessions:
    if name not in thy_data:
        continue
    recs = sorted(thy_data[name], key=lambda r: -r[1])
    te = sum(r[1] for r in recs); tc = sum(r[2] for r in recs); tg = sum(r[3] for r in recs)
    out.append("")
    out.append(f"--- {name}  ({len(recs)} theories) ---")
    out.append("theory" + " " * (NAME_W - 6) + "    elapsed(s)     cpu(s)     gc(s)")
    out.append("-" * 80)
    for n, e, c, g in recs:
        out.append(f"{n:<{NAME_W}}{e:>{NUM_W}.3f}{c:>{NUM_W}.3f}{g:>{NUM_W}.3f}")
    out.append("-" * 80)
    out.append(f"{'TOTAL':<{NAME_W}}{te:>{NUM_W}.3f}{tc:>{NUM_W}.3f}{tg:>{NUM_W}.3f}")

OUT_LOG.write_text("\n".join(out) + "\n")
print(f"wrote {OUT_LOG} ({sum(1 for _ in open(OUT_LOG))} lines)")
print(f"  sessions: {len(sessions)}; with per-theory data: {len(thy_data)}")
print(f"  TOTAL elapsed={total_e:.1f}s cpu={total_c:.1f}s gc={total_g:.1f}s")
