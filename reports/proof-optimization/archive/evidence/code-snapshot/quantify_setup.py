"""Quantify where build-command time goes across ALL l4v sessions, by command
CATEGORY — to size the SETUP axis (crunch + locale interpretation + datatype/fun)
vs proof tactics. Motivated by the finding that reaching a mid-file lemma is
setup-bound (crunch/interp > 23 min synchronous), not tactic-bound.

Reads each session build DB's `command_timings` (elapsed per command; the build's
own numbers — carries the parallel-scheduling caveat, but it is the only
whole-build signal at scale). Buckets every command by its outer keyword and:
  - sums elapsed per category (setup / proof / statement / other),
  - ranks the single most expensive setup commands (file:line).

Run in container.
"""
import glob, os, sqlite3, sys
from collections import defaultdict

HEAPS = glob.glob("/root/.isabelle/heaps/*/log")[0]
L4V = "/workspace/verification/l4v"

SETUP = {"crunch", "crunches", "interpretation", "interpret", "sublocale",
         "instantiation", "instance", "locale", "datatype", "record", "fun",
         "function", "primrec", "termination", "definition", "named_theorems",
         "type_synonym", "context", "bundle", "global_interpretation"}
PROOF = {"by", "apply", "proof", "qed", "done", "subgoal", "unfolding", "using",
         "supply", "applyS", "apply_end", "also", "finally", "moreover", "next",
         "..", ".", "sorry", "oops", "show", "have", "obtain", "fix", "assume",
         "then", "thus", "hence", "with", "let", "case"}
STMT = {"lemma", "theorem", "corollary", "schematic_goal", "lemmas", "theorems"}


def decompress(b):
    b = bytes(b)
    try:
        import zstandard as zstd
        return zstd.ZstdDecompressor().decompress(b)
    except Exception:
        import subprocess
        return subprocess.run(["zstd", "-dc"], input=b, capture_output=True).stdout


def parse(data):
    txt = data.decode("utf-8", "replace")
    cur = {}
    for tok in txt.split("\x06"):
        tok = tok.replace("\x05", "")
        if "=" not in tok:
            continue
        k, _, v = tok.partition("=")
        k = k.strip()
        if k in ("name", "offset", "file", "elapsed"):
            cur[k] = v
            if k == "elapsed" and "file" in cur and "offset" in cur:
                yield {"name": cur.get("name", ""), "offset": int(cur["offset"]),
                       "file": cur["file"], "elapsed": float(cur["elapsed"])}
                cur = {}


def off2line_factory(path):
    src = open(path, encoding="utf-8", errors="replace").read()
    starts = [0]; sym = 0; i = 0; n = len(src)
    while i < n:
        c = src[i]
        if c == "\\" and i + 1 < n and src[i + 1] == "<":
            j = src.find(">", i)
            if j != -1:
                i = j + 1; sym += 1; continue
        if c == "\n":
            starts.append(sym + 1)
        sym += 1; i += 1

    def o2l(off):
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= off:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1
    return o2l


def cat(name):
    if name in SETUP:
        return "setup"
    if name in STMT:
        return "statement"
    if name in PROOF:
        return "proof"
    return "other"


def main():
    # dedup by (file, offset), keep max elapsed
    cmds = {}
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
        for e in parse(decompress(row[0])):
            if not e["file"].startswith(L4V):
                continue
            k = (e["file"], e["offset"])
            if k not in cmds or e["elapsed"] > cmds[k]["elapsed"]:
                cmds[k] = e

    by_cat = defaultdict(float)
    by_name = defaultdict(float)
    by_name_n = defaultdict(int)
    setup_cmds = []
    total = 0.0
    for (f, off), e in cmds.items():
        el = e["elapsed"]; nm = e["name"]; c = cat(nm)
        by_cat[c] += el
        by_name[nm] += el
        by_name_n[nm] += 1
        total += el
        if c == "setup":
            setup_cmds.append((el, nm, f, off))

    print(f"[db] {len(cmds)} distinct timed commands, sum elapsed = {total:.0f}s "
          f"(NOTE: build-DB elapsed under parallel scheduling)\n")
    print(f"{'category':12} {'sum_s':>9} {'share':>7}")
    print("-" * 32)
    for c in ("setup", "proof", "statement", "other"):
        print(f"{c:12} {by_cat[c]:9.0f} {100*by_cat[c]/total:6.1f}%")

    print(f"\n=== elapsed by command keyword (top 18) ===")
    print(f"{'name':16} {'sum_s':>8} {'count':>6} {'cat':>10}")
    for nm, s in sorted(by_name.items(), key=lambda kv: -kv[1])[:18]:
        print(f"{nm:16} {s:8.0f} {by_name_n[nm]:6d} {cat(nm):>10}")

    print(f"\n=== top 25 single most expensive SETUP commands ===")
    print(f"{'elapsed':>8}  {'name':14} file:line")
    o2l_cache = {}
    for el, nm, f, off in sorted(setup_cmds, reverse=True)[:25]:
        if f not in o2l_cache and os.path.exists(f):
            o2l_cache[f] = off2line_factory(f)
        ln = o2l_cache[f](off) if f in o2l_cache else "?"
        rel = f.split("l4v/")[-1]
        print(f"{el:8.1f}  {nm:14} {rel}:{ln}")


main()
