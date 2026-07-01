"""Decisive check: for each timed command the build DB stores BOTH `elapsed` and
`cpu` (and `gc`). scan_db_timings.py used ELAPSED only. If a "147s" command has
tiny CPU, its elapsed is a PARALLEL-BUILD SCHEDULING ARTIFACT, not real proof work
— which would invalidate the elapsed-ranked candidate selection.

This decodes the Access session DB, prints elapsed/cpu/gc for commands around the
empty_slot fastforce (CNode_AC ~line 1094-1119), and the whole-file totals.
Run in container.
"""
import glob, os, sqlite3, sys

HEAPS = glob.glob("/root/.isabelle/heaps/*/log")[0]
TARGET_FILE_SUBSTR = "CNode_AC.thy"
TARGET_LINES = range(1088, 1120)


def decompress(b):
    b = bytes(b)
    try:
        import zstandard as zstd
        return zstd.ZstdDecompressor().decompress(b)
    except Exception:
        import subprocess
        return subprocess.run(["zstd", "-dc"], input=b, capture_output=True).stdout


def parse_full(data):
    """Yield {name, offset, file, elapsed, cpu, gc}. A record is delimited by the
    set of keys; we flush on whichever of elapsed/cpu/gc completes the trio, but to
    be robust we flush when we see a key we've already got (start of next record)."""
    txt = data.decode("utf-8", "replace")
    cur = {}
    for tok in txt.split("\x06"):
        tok = tok.replace("\x05", "")
        if "=" not in tok:
            continue
        k, _, v = tok.partition("=")
        k = k.strip()
        if k not in ("name", "offset", "file", "elapsed", "cpu", "gc"):
            continue
        if k in cur:  # new record begins
            yield cur
            cur = {}
        cur[k] = v
    if cur:
        yield cur


def symbol_off2line(path):
    src = open(path, encoding="utf-8", errors="replace").read()
    line_starts = [0]
    sym = 0; i = 0; n = len(src)
    while i < n:
        ch = src[i]
        if ch == "\\" and i + 1 < n and src[i + 1] == "<":
            j = src.find(">", i)
            if j != -1:
                i = j + 1; sym += 1; continue
        if ch == "\n":
            line_starts.append(sym + 1)
        sym += 1; i += 1

    def off2line(off):
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= off:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1
    return off2line


def main():
    recs = []
    for db in sorted(glob.glob(os.path.join(HEAPS, "*.db"))):
        if "SCALA_ISABELLE_TEMP" in db:
            continue
        try:
            con = sqlite3.connect(db)
            row = con.execute("SELECT command_timings FROM isabelle_session_info").fetchone()
        except Exception:
            continue
        if not row or row[0] is None:
            continue
        sess = os.path.basename(db)[:-3]
        for e in parse_full(decompress(row[0])):
            if "file" in e and TARGET_FILE_SUBSTR in e.get("file", ""):
                e["_sess"] = sess
                recs.append(e)

    if not recs:
        print("no CNode_AC records found"); return
    path = recs[0]["file"]
    off2line = symbol_off2line(path)

    def f(e, k):
        try:
            return float(e.get(k, "nan"))
        except Exception:
            return float("nan")

    # whole-file totals (dedup by offset, max elapsed)
    best = {}
    for e in recs:
        off = int(e["offset"])
        if off not in best or f(e, "elapsed") > f(best[off], "elapsed"):
            best[off] = e
    tot_el = sum(f(e, "elapsed") for e in best.values())
    tot_cpu = sum(f(e, "cpu") for e in best.values())
    tot_gc = sum(f(e, "gc") for e in best.values())
    print(f"=== {os.path.basename(path)} whole-file (dedup {len(best)} cmds) ===")
    print(f"  sum elapsed = {tot_el:8.1f}s   sum cpu = {tot_cpu:8.1f}s   sum gc = {tot_gc:7.1f}s")
    print(f"  session(s): {sorted({e['_sess'] for e in recs})}\n")

    print(f"=== commands at lines {TARGET_LINES.start}-{TARGET_LINES.stop-1} (empty_slot region) ===")
    print(f"{'line':>5} {'elapsed':>9} {'cpu':>8} {'gc':>7}  name")
    rows = sorted(best.values(), key=lambda e: int(e["offset"]))
    for e in rows:
        ln = off2line(int(e["offset"]))
        if ln in TARGET_LINES:
            print(f"{ln:>5} {f(e,'elapsed'):>9.2f} {f(e,'cpu'):>8.2f} {f(e,'gc'):>7.2f}  {e.get('name','')}")

    # also the single biggest-elapsed command in the file
    big = max(best.values(), key=lambda e: f(e, "elapsed"))
    print(f"\n=== biggest-elapsed command in file ===")
    print(f"  line {off2line(int(big['offset']))}: elapsed={f(big,'elapsed'):.1f}s "
          f"cpu={f(big,'cpu'):.1f}s gc={f(big,'gc'):.1f}s name={big.get('name','')}")


main()
