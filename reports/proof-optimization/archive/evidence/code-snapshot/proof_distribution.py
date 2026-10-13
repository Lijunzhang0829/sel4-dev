"""Proof-tactic distribution: of the build's `by`/`apply` time, how much goes to
each TACTIC FAMILY (simp / clarsimp / auto-fastforce-force / blast-metis /
wp-wpsimp / rule-erule-deterministic / other). Reads source text per command so it
classifies the actual method, not just the `by`/`apply` keyword.

Scope = proof commands the build recorded (elapsed > ~0.1s threshold) across ALL
sessions. Carries the DB-elapsed-under-parallel-scheduling caveat (absolute s are
upper-ish bounds, not pure CPU); use for RELATIVE shape, not exact wall.
Run in container.
"""
import glob, os, sqlite3, re
from collections import defaultdict

HEAPS = glob.glob("/root/.isabelle/heaps/*/log")[0]
L4V = "/workspace/verification/l4v"
PROOF_KW = {"by", "apply", "proof", "unfolding", "supply", "subgoal", "using"}

# tactic families, checked in order; first hit wins on the dominant method token
FAMILIES = [
    ("blast/metis(pure-search)", re.compile(r"\b(blast|metis|meson|fast|iprover)\b")),
    ("auto/fastforce/force(simp+search)", re.compile(r"\b(auto|fastforce|force)\b")),
    ("clarsimp", re.compile(r"\bclarsimp\b")),
    ("simp", re.compile(r"\b(simp|simp_all)\b")),
    ("wp/wpsimp", re.compile(r"\b(wpsimp|wp|wpc|wps)\b")),
    ("ctac/vcg/ccorres", re.compile(r"\b(ctac|cinit|csymbr|ceqv|vcg|sep_wp)\b")),
    ("rule/erule/cases(deterministic)",
     re.compile(r"\b(rule|erule|drule|frule|intro|elim|cases|case_tac|induct|"
                r"subst|unfold|assumption|rename_tac|clarify|safe)\b")),
]


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


def off2line(path):
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
    lines = src.split("\n")

    def o2l(off):
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= off: lo = mid
            else: hi = mid - 1
        return lo + 1

    def text_at(line):
        buf = lines[line - 1] if line - 1 < len(lines) else ""
        depth = buf.count("(") - buf.count(")")
        k = line
        while depth > 0 and k < len(lines) and k - line < 8:
            buf += " " + lines[k]
            depth += lines[k].count("(") - lines[k].count(")")
            k += 1
        return buf
    return o2l, text_at


def family(text):
    for name, rx in FAMILIES:
        if rx.search(text):
            return name
    return "other/mixed"


def main():
    cmds = {}
    for db in sorted(glob.glob(os.path.join(HEAPS, "*.db"))):
        if "SCALA_ISABELLE_TEMP" in db: continue
        try:
            con = sqlite3.connect(db)
            row = con.execute("SELECT command_timings FROM isabelle_session_info").fetchone()
        except Exception:
            continue
        if not row or row[0] is None: continue
        for e in parse(decompress(row[0])):
            if not e["file"].startswith(L4V): continue
            if e["name"] not in PROOF_KW: continue
            k = (e["file"], e["offset"])
            if k not in cmds or e["elapsed"] > cmds[k]["elapsed"]:
                cmds[k] = e

    by_fam = defaultdict(float); n_fam = defaultdict(int)
    top = defaultdict(list)
    o2l_cache = {}
    total = 0.0
    for (f, off), e in cmds.items():
        if not os.path.exists(f): continue
        if f not in o2l_cache:
            o2l_cache[f] = off2line(f)
        o2l, text_at = o2l_cache[f]
        ln = o2l(off)
        fam = family(text_at(ln))
        by_fam[fam] += e["elapsed"]; n_fam[fam] += 1; total += e["elapsed"]
        top[fam].append((e["elapsed"], f.split("l4v/")[-1], ln))

    print(f"[db] {len(cmds)} timed by/apply commands (>~0.1s), sum = {total:.0f}s")
    print("    (DB-elapsed under parallel scheduling — RELATIVE shape, not exact CPU)\n")
    print(f"{'tactic family':36} {'sum_s':>8} {'share':>7} {'count':>7}")
    print("-" * 62)
    for fam, s in sorted(by_fam.items(), key=lambda kv: -kv[1]):
        print(f"{fam:36} {s:8.0f} {100*s/total:6.1f}% {n_fam[fam]:7d}")
    print("\n=== top 3 single commands per family ===")
    for fam, _ in sorted(by_fam.items(), key=lambda kv: -kv[1]):
        rows = sorted(top[fam], reverse=True)[:3]
        print(f"\n{fam}:")
        for el, rel, ln in rows:
            print(f"  {el:7.1f}s  {rel}:{ln}")


main()
