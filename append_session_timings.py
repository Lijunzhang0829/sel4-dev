#!/usr/bin/env python3
"""
Decode an Isabelle session's `theory_timings` BLOB (zstd-compressed) from its
.db file and append a per-theory + TOTAL block to a target build log file in
the format already used by heaps/build_log.txt.

Usage: append_session_timings.py <session_db_path> <build_log_path>
"""
import sqlite3, sys, re

try:
    import zstandard as zstd
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "zstandard"])
    import zstandard as zstd

if len(sys.argv) != 3:
    print(__doc__); sys.exit(2)

db_path, log_path = sys.argv[1], sys.argv[2]
con = sqlite3.connect(db_path)
row = con.execute(
    "SELECT session_name, theory_timings FROM isabelle_session_info"
).fetchone()
if row is None:
    sys.exit("no isabelle_session_info row")
sess_name, blob = row
text = zstd.ZstdDecompressor().decompress(blob).decode("utf-8")

# Records look like: ::name=NAMEelapsed=Xcpu=Ygc=Z separated by leading "::".
recs = []
for chunk in text.split("::"):
    chunk = chunk.strip()
    if not chunk: continue
    m = re.match(r"name=(.+?)elapsed=([0-9.]+)cpu=([0-9.]+)gc=([0-9.]+)$", chunk)
    if not m:
        continue
    recs.append((m.group(1), float(m.group(2)),
                 float(m.group(3)), float(m.group(4))))

if not recs:
    sys.exit("no theory_timings parsed")

tot_e = sum(r[1] for r in recs)
tot_c = sum(r[2] for r in recs)
tot_g = sum(r[3] for r in recs)

# Format matches existing build_log.txt: theory col 50-char wide, then 3 right-
# aligned numeric cols, 3 decimals.
NAME_W = 50
NUM_W  = 10
def row(name, e, c, g):
    return f"{name:<{NAME_W}}{e:>{NUM_W}.3f}{c:>{NUM_W}.3f}{g:>{NUM_W}.3f}"

out = []
out.append("")
out.append(f"--- {sess_name}  ({len(recs)} theories) ---")
out.append("theory" + " "*(NAME_W-6) + "    elapsed(s)     cpu(s)     gc(s)")
out.append("-"*80)
for r in recs:
    out.append(row(*r))
out.append("-"*80)
out.append(row("TOTAL", tot_e, tot_c, tot_g))
out.append("")

with open(log_path, "a") as f:
    f.write("\n".join(out) + "\n")

print(f"appended {sess_name}: {len(recs)} theories, "
      f"TOTAL elapsed={tot_e:.1f}s cpu={tot_c:.1f}s gc={tot_g:.1f}s")
