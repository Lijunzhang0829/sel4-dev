#!/usr/bin/env python3
"""
Extract per-command timing from an Isabelle session's heap-log DB
(/root/.isabelle/heaps/.../log/<SESSION>.db, command_timings BLOB) and
produce a slow-proofs report.

For each slow command (apply/by), map its byte offset back to:
  - the .thy source line
  - the surrounding `lemma`/`theorem` definition
  - the actual tactic source

Outputs a markdown report sorted by elapsed time desc.

Usage: extract_session_command_timings.py <session_db> <repo_root> <out.md>
"""
import sqlite3, sys, re, os, bisect

try:
    import zstandard as zstd
except ImportError:
    sys.exit("zstandard module required (pip install zstandard)")

if len(sys.argv) != 4:
    print(__doc__); sys.exit(2)

db_path, repo_root, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
con = sqlite3.connect(db_path)
sess, ct = con.execute(
    "SELECT session_name, command_timings FROM isabelle_session_info"
).fetchone()
if ct is None:
    sys.exit(f"no command_timings BLOB for session {sess}")

text = zstd.ZstdDecompressor().decompress(ct).decode("utf-8", errors="replace")

pat = re.compile(
    r"name=([\w_-]+)[\x00-\x1f]*"
    r"offset=([0-9]+)[\x00-\x1f]*"
    r"file=([^\x00-\x1f]+)[\x00-\x1f]*"
    r"elapsed=([0-9.]+)"
)
recs = [
    (m.group(1), int(m.group(2)), m.group(3), float(m.group(4)))
    for m in pat.finditer(text)
]

def translate(p):
    if p.startswith("/sel4-project/"):
        rel = p[len("/sel4-project/"):]
        return os.path.join(repo_root, rel)
    return p

file_cache = {}
def get_index(path):
    if path in file_cache:
        return file_cache[path]
    if not os.path.exists(path):
        file_cache[path] = (None, None, None)
        return None, None, None
    with open(path, "rb") as f:
        data = f.read()
    line_starts = [0]
    for i, b in enumerate(data):
        if b == 0x0a:
            line_starts.append(i + 1)
    text_str = data.decode("utf-8", errors="replace")
    lemma_pat = re.compile(
        r"^(?:lemma|theorem|corollary|definition|function|primrec)\s+([\w'_]+)",
        re.MULTILINE,
    )
    lemma_offsets = []
    cum = 0
    for line in text_str.split("\n"):
        m = lemma_pat.match(line)
        if m:
            lemma_offsets.append((cum, m.group(1)))
        cum += len(line.encode("utf-8")) + 1
    lemma_offsets.sort()
    file_cache[path] = (data, line_starts, lemma_offsets)
    return data, line_starts, lemma_offsets

def offset_to_line(line_starts, off):
    if line_starts is None:
        return None
    i = bisect.bisect_right(line_starts, off) - 1
    return i + 1

def offset_to_lemma(lemma_offsets, off):
    if not lemma_offsets:
        return None
    keys = [x[0] for x in lemma_offsets]
    i = bisect.bisect_right(keys, off) - 1
    if i < 0:
        return None
    return lemma_offsets[i][1]

def line_text(data, line_starts, line):
    if data is None or line is None:
        return ""
    if line - 1 >= len(line_starts):
        return ""
    start = line_starts[line - 1]
    end = line_starts[line] if line < len(line_starts) else len(data)
    return data[start:end].decode("utf-8", errors="replace").rstrip()

out = []
out.append(f"# Slow commands report — {sess} session\n")
out.append(f"_Source: heap-log `command_timings` BLOB. Already collected during the original build — no re-measurement done._\n")
out.append(f"\n- Total commands: **{len(recs)}**")
from collections import Counter
type_counts = Counter(r[0] for r in recs)
out.append("- Command-type distribution: " +
           ", ".join(f"{n}={c}" for n, c in type_counts.most_common(8)))

# Aggregate: time per lemma (sum elapsed of all commands within each lemma)
lemma_time = {}
for name, off, f, e in recs:
    full_path = translate(f)
    _, _, lemma_offsets = get_index(full_path)
    lemma = offset_to_lemma(lemma_offsets, off)
    key = (f, lemma)
    lemma_time[key] = lemma_time.get(key, 0.0) + e

out.append("\n## Top 30 lemmas by aggregate elapsed (sum of contained commands)\n")
out.append("| rank | elapsed (s) | lemma | file |\n|---:|---:|---|---|")
sorted_lemmas = sorted(lemma_time.items(), key=lambda x: -x[1])[:30]
for i, ((f, lemma), t) in enumerate(sorted_lemmas, 1):
    short_f = f.replace("/sel4-project/verification/l4v/", "")
    out.append(f"| {i} | {t:.1f} | `{lemma or '<top-level>'}` | {short_f} |")

out.append("\n## Top 50 individual slow commands\n")
out.append("| rank | elapsed (s) | type | file:line | lemma | source |\n|---:|---:|---|---|---|---|")
for i, (name, off, f, e) in enumerate(sorted(recs, key=lambda r: -r[3])[:50], 1):
    full = translate(f)
    data, ls, lo = get_index(full)
    line = offset_to_line(ls, off)
    lemma = offset_to_lemma(lo, off)
    src = line_text(data, ls, line) if data else ""
    src_clean = src.replace("|", "\\|")[:100]
    short_f = f.replace("/sel4-project/verification/l4v/", "")
    out.append(f"| {i} | {e:.2f} | {name} | {short_f}:{line} | `{lemma or '?'}` | `{src_clean}` |")

with open(out_path, "w") as f:
    f.write("\n".join(out) + "\n")

print(f"wrote {out_path}: {len(recs)} commands across {len(lemma_time)} lemma scopes")
