#!/usr/bin/env bash
# Emit per-theory baseline timings for an Isabelle session, sourced directly
# from the heap-log database already written by a prior `isabelle build`.
# No additional build work is triggered.
#
# Usage: emit-session-baselines.sh <SESSION_NAME> [<OUT_MD_PATH>]
#   If OUT_MD_PATH is omitted, the markdown is printed to stdout.
set -euo pipefail

SESSION="${1:?Usage: $0 <session> [out_md_path]}"
OUT_MD="${2:-}"
L4V_ARCH="${L4V_ARCH:-ARM}"

HEAPS_DIR="$(isabelle getenv -b ISABELLE_HEAPS)"
DB="${HEAPS_DIR}/polyml-5.9.1_x86_64_32-linux/log/${SESSION}.db"

if [ ! -f "$DB" ]; then
  echo "no heap log DB at $DB — session must be built at least once first" >&2
  exit 1
fi

exec env DB="$DB" SESSION="$SESSION" OUT_MD="$OUT_MD" python3 - <<'PY'
import sys, sqlite3, glob, io, re, os, datetime

# pip-installed zstandard lives under the user's pip prefix
for p in glob.glob("/workspace/.home/.local/lib/python*/site-packages"):
    if p not in sys.path: sys.path.insert(0, p)
try:
    import zstandard
except ImportError:
    print("zstandard module missing — run: pip3 install --user --break-system-packages zstandard", file=sys.stderr)
    sys.exit(2)

db, session, out_md = os.environ["DB"], os.environ["SESSION"], os.environ.get("OUT_MD", "")

con = sqlite3.connect(db)
row = con.execute("SELECT theory_timings FROM isabelle_session_info").fetchone()
if not row or not row[0]:
    print(f"no theory_timings recorded for {session}", file=sys.stderr)
    sys.exit(3)

raw = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(row[0])).read()

# Records are delimited by 0x05 / fields by 0x06 inside Isabelle's YXML-ish format:
#   ::name=<theory>\x06elapsed=<s>\x06cpu=<s>\x06gc=<s>
pattern = rb"name=([^\x06]+)\x06elapsed=([\d.]+)\x06cpu=([\d.]+)\x06gc=([\d.]+)"
entries = [(m.group(1).decode(), float(m.group(2)), float(m.group(3)), float(m.group(4)))
           for m in re.finditer(pattern, raw)]
entries.sort(key=lambda r: r[1], reverse=True)

# Only keep theories belonging to the session itself (skip imports from other sessions)
own = [e for e in entries if e[0].startswith(session + ".")]

ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
lines = [
    f"# Session Baseline — {session}",
    "",
    f"- Generated: {ts}",
    f"- Source: `{db}` (`theory_timings` BLOB, zstd-compressed)",
    f"- Own theories: {len(own)} / {len(entries)} total (including deps)",
    "",
    "Times are from the last `isabelle build` of this session — no extra build",
    "was triggered by generating this report.",
    "",
    "| Theory | elapsed_ms | cpu_ms | gc_ms |",
    "|---|---:|---:|---:|",
]
for name, el, cpu, gc in own:
    lines.append(f"| {name} | {int(el*1000)} | {int(cpu*1000)} | {int(gc*1000)} |")

text = "\n".join(lines) + "\n"
if out_md:
    os.makedirs(os.path.dirname(out_md) or ".", exist_ok=True)
    with open(out_md, "w") as f:
        f.write(text)
    print(f"wrote {out_md}: {len(own)} theories (of {len(entries)} total)")
else:
    print(text)
PY
