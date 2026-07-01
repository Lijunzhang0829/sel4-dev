"""Scan all l4v session build DBs for per-command timing (no re-running).

Each session `.db` (under heaps/.../log/) stores `command_timings` in
`isabelle_session_info` as a zstd-compressed Isabelle YXML properties list; each
entry is `name=<cmd> offset=<symbol-offset> file=<thy> elapsed=<seconds>`.

We decode every session DB, dedup commands by (file, offset), and group them into
lemmas (a `lemma`/`theorem`/... statement + the proof commands until the next
statement) to compute, per lemma:
  total_s        — sum of its commands' elapsed
  search_s       — sum of elapsed of commands whose source is a CLASSICAL tactic
  search_frac    — search_s / total_s
Then filter to total_s > MIN_TOTAL and search_frac > MIN_FRAC.
"""
import os, re, glob, sqlite3, json, sys

HEAPS = glob.glob("/root/.isabelle/heaps/*/log")[0]
L4V = os.environ.get("L4V_DIR", "/sel4-project/verification/l4v")
MIN_TOTAL = float(os.environ.get("MIN_TOTAL", "10"))
MIN_FRAC = float(os.environ.get("MIN_FRAC", "0.30"))

CLASS = re.compile(r"\b(auto|blast|fastforce|force|metis|fast|safe)\b")
# command keywords that START a new lemma group
STMT = ("lemma", "theorem", "corollary", "schematic_goal", "lemmas")
# non-statement, non-proof commands that END a group (decls between lemmas)
DECL = ("definition", "fun", "function", "primrec", "datatype", "record",
        "instantiation", "instance", "locale", "context", "end", "declare",
        "abbreviation", "type_synonym", "axiomatization", "termination",
        "crunch", "crunches", "ML", "text", "section", "subsection",
        "interpretation", "sublocale", "notation", "no_notation", "method")


def decompress(b):
    b = bytes(b)
    try:
        import zstandard as zstd
        return zstd.ZstdDecompressor().decompress(b)
    except Exception:
        import subprocess
        return subprocess.run(["zstd", "-dc"], input=b, capture_output=True).stdout


def parse_timings(data):
    """Yield dicts {name, offset, file, elapsed} from the decoded YXML bytes."""
    txt = data.decode("utf-8", "replace")
    # fields are separated by \x06; a record ends at its `elapsed=` token.
    cur = {}
    for tok in txt.split("\x06"):
        # strip YXML control chars
        tok = tok.replace("\x05", "")
        if "=" not in tok:
            continue
        k, _, v = tok.partition("=")
        k = k.strip()
        if k in ("name", "offset", "file", "elapsed", "cpu", "gc"):
            cur[k] = v
            if k == "elapsed":
                if "file" in cur and "offset" in cur:
                    yield {"name": cur.get("name", ""), "offset": int(cur["offset"]),
                           "file": cur["file"], "elapsed": float(cur["elapsed"])}
                cur = {}


def symbol_line_index(path):
    """Return a function offset(symbols)->1-based line, accounting for Isabelle
    symbols (\\<foo> counts as ONE symbol). Build a prefix map of symbol-count at
    each line start."""
    src = open(path, encoding="utf-8", errors="replace").read()
    # per char, symbol advances by 1 except inside a \<...> run which is 1 symbol.
    line_starts_sym = [0]  # symbol index at start of each line (line i -> index)
    sym = 0
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        if ch == "\\" and i + 1 < n and src[i + 1] == "<":
            j = src.find(">", i)
            if j != -1:
                i = j + 1
                sym += 1
                continue
        if ch == "\n":
            line_starts_sym.append(sym + 1)
        sym += 1
        i += 1

    def off2line(off):
        # binary search: largest line whose start <= off
        lo, hi = 0, len(line_starts_sym) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts_sym[mid] <= off:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1
    return off2line, src, line_starts_sym


def main():
    # 1) collect + dedup all command timings across sessions
    cmds = {}  # (file, offset) -> {name, elapsed}
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
        for e in parse_timings(decompress(row[0])):
            if not e["file"].startswith(L4V):
                continue
            k = (e["file"], e["offset"])
            # keep the max elapsed seen for a command (robust to noise)
            if k not in cmds or e["elapsed"] > cmds[k]["elapsed"]:
                cmds[k] = {"name": e["name"], "elapsed": e["elapsed"]}
    print(f"[db] {len(cmds)} distinct timed commands across all sessions", file=sys.stderr)

    # 2) per-file, sort by offset, group into lemmas, read source text per command
    byfile = {}
    for (f, off), v in cmds.items():
        byfile.setdefault(f, []).append((off, v["name"], v["elapsed"]))

    lemmas = []  # {file, lemma_line, name, total_s, search_s, search_frac, slow_cmds}
    for f, items in byfile.items():
        if not os.path.exists(f):
            continue
        items.sort()
        off2line, src, _ = symbol_line_index(f)
        # to read a command's source text, map symbol-offset back to char index
        # (approx: find the char index whose symbol-count == offset)
        srclines = src.split("\n")

        def cmd_text(line):
            """Full multi-line text of the proof command starting at `line`
            (until parens balance and the bracket/step closes, max 8 lines) so a
            `simp:`/`dest:` on a continuation line is not missed."""
            buf = srclines[line - 1] if line - 1 < len(srclines) else ""
            depth = buf.count("(") - buf.count(")")
            k = line
            while depth > 0 and k < len(srclines) and k - line < 8:
                buf += " " + srclines[k]
                depth += srclines[k].count("(") - srclines[k].count(")")
                k += 1
            return buf.strip()

        cur = None
        for off, name, el in items:
            line = off2line(off)
            txt = cmd_text(line)[:200]
            is_classical = bool(CLASS.search(name)) or bool(CLASS.search(txt))
            if name in STMT:
                if cur:
                    lemmas.append(cur)
                cur = {"file": f, "lemma_line": line, "name_at_line": txt[:50],
                       "total_s": el, "search_s": 0.0, "cmds": [(line, name, round(el, 3), txt)]}
            elif name in DECL:
                if cur:
                    lemmas.append(cur)
                cur = None
            elif cur is not None:
                cur["total_s"] += el
                if is_classical:
                    cur["search_s"] += el
                cur["cmds"].append((line, name, round(el, 3), txt))
            else:
                # orphan proof command (e.g. statement was sub-threshold) — treat
                # as its own single-command lemma so we don't lose big classical lines
                cur = {"file": f, "lemma_line": line, "name_at_line": txt[:50],
                       "total_s": el, "search_s": el if is_classical else 0.0,
                       "cmds": [(line, name, round(el, 3), txt)]}
                lemmas.append(cur)
                cur = None
        if cur:
            lemmas.append(cur)

    for lm in lemmas:
        lm["search_frac"] = round(lm["search_s"] / lm["total_s"], 3) if lm["total_s"] else 0.0
        lm["total_s"] = round(lm["total_s"], 3)
        lm["search_s"] = round(lm["search_s"], 3)

    hits = [lm for lm in lemmas if lm["total_s"] > MIN_TOTAL and lm["search_frac"] > MIN_FRAC]
    hits.sort(key=lambda x: -x["search_s"])
    out = "/workspace/tools/seL4-proof-search/Isa-Repl/runs/db_candidates.json"
    json.dump(hits, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"\n[hits] total_s>{MIN_TOTAL}s AND search_frac>{MIN_FRAC}: {len(hits)} lemmas -> {out}")
    print(f"{'total':>7} {'search':>7} {'frac':>5}  file:line  (slowest classical cmd)")
    for lm in hits[:40]:
        rel = lm["file"].split("l4v/")[-1]
        sc = max((c for c in lm["cmds"] if CLASS.search(c[1]) or CLASS.search(c[3])),
                 key=lambda c: c[2], default=(lm["lemma_line"], "", 0, ""))
        print(f"{lm['total_s']:>7.1f} {lm['search_s']:>7.1f} {lm['search_frac']:>5.2f}  "
              f"{rel}:{lm['lemma_line']}  [{sc[2]}s] {sc[3][:45]}")


main()
