"""Extract high-value optimization candidates from the build's MEASURED per-command timing.

Fully reproducible, parameter-driven — NO manual / experience-based picking. The data source
is the build's own `command_timings` (per-command elapsed) stored in each session heap DB;
we never invent costs.

A candidate is a proof command (apply/by/subgoal) that is BOTH:
  (1) VALUE:          measured elapsed >= MIN_CPU seconds        -> worth optimizing at all
  (2) REWRITE-TARGET: its source text contains a search/automation tactic in TACTICS
                      (auto/fastforce/force/blast/clarsimp/metis ...) -> something to static-ize

Each candidate is additionally TAGGED feasible/infeasible for the REPL rewrite methods:
  FEASIBLE := file_lines <= MAX_FILE_LINES AND session not in EXCLUDE_SESSIONS
  (bigger files / heavier sessions time out at REPL init — this is a TOOL limitation,
   reported separately, NOT a judgement about the lemma's value.)

Parameters (env, all overridable so you can re-run and verify):
  MIN_CPU=10                TACTICS=auto,fastforce,force,blast,clarsimp,metis
  MAX_FILE_LINES=2900       EXCLUDE_SESSIONS=CRefine,Refine,InfoFlowC
  L4V_DIR=/sel4-project/verification/l4v   OUT=.../runs/high_value_candidates.json

Run INSIDE the l4v container (needs the heap command_timings + the inventory DB).
"""
import os, re, glob, sqlite3, json

HEAPS = glob.glob("/root/.isabelle/heaps/*/log")[0]
L4V = os.environ.get("L4V_DIR", "/sel4-project/verification/l4v")
INV = "/workspace/reports/golden-baseline/lemma-inventory.db"
OUT = os.environ.get("OUT", "/workspace/lemma-staticize/experiment-candidates/high_value_candidates.json")
MIN_CPU = float(os.environ.get("MIN_CPU", "10"))
# default targets = genuine CLASSICAL-SEARCH tactics only. clarsimp/simp_all are EXCLUDED by
# default: clarsimp is already the light tactic (clarify+simp), its cost is simp-WORK not
# backtracking search, so it's not a search-elimination opportunity. (Override TACTICS to study them.)
TACTICS = os.environ.get("TACTICS", "auto,fastforce,force,blast").split(",")
MAX_FILE_LINES = int(os.environ.get("MAX_FILE_LINES", "2900"))
EXCLUDE_SESSIONS = set(os.environ.get("EXCLUDE_SESSIONS", "CRefine,Refine,InfoFlowC").split(","))
TAC = re.compile(r"\b(" + "|".join(re.escape(t) for t in TACTICS) + r")\b")
STMT = re.compile(r"^\s*(lemma|theorem|corollary|schematic_goal)\b\s*([A-Za-z0-9_'.\[]+)?")
# the FULL set of proof-owning goal commands, so a proof line is attributed to its REAL
# enclosing command (not the previous `lemma`). Only INIT_OK kinds can be reached by the REPL
# methods' lemma-name init path; others (distinct/crunch/instance/...) are method-infeasible
# (a TOOL limit, reported, not a value judgement).
ENC = re.compile(r"^\s*(lemma|theorem|corollary|schematic_goal|distinct|crunch|crunches|"
                 r"instance|interpretation|sublocale|instantiation|termination|function|"
                 r"lift_definition|primrec|fun|lemmas)\b\s*([A-Za-z0-9_'.\[]+)?")
INIT_OK = {"lemma", "theorem", "corollary", "schematic_goal"}
# work-suspect heuristic: `simp: <>=WORK_DEFS named defs>` with NO rule-chaining => the cost is
# likely simp-WORK (default-simpset rewriting / def unfolding), NOT eliminable search. Flagged,
# NOT excluded (the search-vs-work split can't be told reliably from text — the profiling wall).
CHAIN = re.compile(r"\b(dest!?:|elim!?:|intro!?:|split:|frule|drule|rule_tac|case_tac|erule)")
DEFSIMP = re.compile(r"\b\w+_(?:defs?|simps)\b")     # _def, _defs (bundle), _simps
DEFBUNDLE = re.compile(r"\b\w+_defs\b")              # a *_defs PLURAL bundle = strong simp-WORK signal
WORK_DEFS = int(os.environ.get("WORK_DEFS", "2"))


def decompress(b):
    b = bytes(b)
    try:
        import zstandard as z
        return z.ZstdDecompressor().decompress(b)
    except Exception:
        import subprocess
        return subprocess.run(["zstd", "-dc"], input=b, capture_output=True).stdout


def parse_timings(d):
    cur = {}
    for tok in d.decode("utf-8", "replace").split("\x06"):
        tok = tok.replace("\x05", "")
        if "=" not in tok:
            continue
        k, _, v = tok.partition("="); k = k.strip()
        if k in ("name", "offset", "file", "elapsed"):
            cur[k] = v
            if k == "elapsed" and "file" in cur and "offset" in cur:
                yield {"name": cur.get("name", ""), "offset": int(cur["offset"]),
                       "file": cur["file"], "elapsed": float(cur["elapsed"])}
                cur = {}


def make_index(path):
    """offset(symbols)->1-based line, command source text (paren-balanced), enclosing lemma."""
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

    def off2line(o):
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            m = (lo + hi + 1) // 2
            if starts[m] <= o: lo = m
            else: hi = m - 1
        return lo + 1

    def text(ln):
        b = lines[ln - 1] if ln - 1 < len(lines) else ""
        d = b.count("(") - b.count(")"); k = ln
        while d > 0 and k < len(lines) and k - ln < 8:
            b += " " + lines[k]; d += lines[k].count("(") - lines[k].count(")"); k += 1
        return b

    def enc_of(ln):
        """nearest enclosing goal command above ln -> (kind, name)."""
        for i in range(min(ln, len(lines)) - 1, -1, -1):
            m = ENC.match(lines[i])
            if m and m.group(2):
                return m.group(1), m.group(2).split("[")[0].rstrip(":")
        return None, None
    return off2line, text, enc_of, len(lines)


def main():
    sess = {p: s for p, s in sqlite3.connect(INV).execute("select path, session from theories")}
    # 1) collect + dedup the build's per-command timings (keep max elapsed per command)
    cmds = {}
    for db in sorted(glob.glob(os.path.join(HEAPS, "*.db"))):
        if "SCALA_ISABELLE_TEMP" in db:
            continue
        try:
            row = sqlite3.connect(db).execute("SELECT command_timings FROM isabelle_session_info").fetchone()
        except Exception:
            continue
        if not row or row[0] is None:
            continue
        for e in parse_timings(decompress(row[0])):
            if not e["file"].startswith(L4V) or e["name"] not in ("apply", "by", "subgoal"):
                continue
            k = (e["file"], e["offset"])
            if k not in cmds or e["elapsed"] > cmds[k]["elapsed"]:
                cmds[k] = e
    # 2) filter: VALUE (>=MIN_CPU) AND REWRITE-TARGET (tactic in TACTICS); tag feasibility
    cache = {}; out = []
    for (f, off), e in cmds.items():
        if e["elapsed"] < MIN_CPU or not os.path.exists(f):
            continue
        if f not in cache:
            cache[f] = make_index(f)
        off2line, text, enc_of, nlines = cache[f]
        ln = off2line(off); t = re.sub(r"\s+", " ", text(ln))
        m = TAC.search(t)
        if not m:
            continue
        rel = f.split("l4v/")[-1]; s = sess.get(rel, "?")
        enc_kind, lem = enc_of(ln)
        if not lem:
            continue
        ndef = len(set(DEFSIMP.findall(t)))
        # work-suspect: cost likely simp-WORK (not eliminable search). Either many named defs,
        # or a *_defs PLURAL bundle (e.g. s0_ptr_defs) — both unfold defs, not backtrack — AND
        # no rule-chaining. Flagged, not excluded (the split can't be told reliably from text).
        work_suspect = ("simp" in t and not CHAIN.search(t)
                        and (ndef >= WORK_DEFS or bool(DEFBUNDLE.search(t))))
        # method can only init lemma/theorem/corollary/schematic_goal by name; a proof line under
        # distinct/crunch/instance/... is method-infeasible (reported as a tool limit).
        init_ok = enc_kind in INIT_OK
        out.append({"lemma": lem, "thy": rel, "session": s, "proof_line": ln,
                    "tactic": m.group(1), "cpu_s": round(e["elapsed"], 2), "file_lines": nlines,
                    "enc_cmd": enc_kind, "init_able": init_ok,
                    "feasible": init_ok and nlines <= MAX_FILE_LINES and s not in EXCLUDE_SESSIONS,
                    "work_suspect": work_suspect, "n_simp_defs": ndef, "hot_line": t[:100]})
    # KEEP every search LINE (a lemma's proof may have several; we must attempt each, since
    # accelerating the lemma needs ALL its search lines handled unless some are shown
    # unnecessary). Annotate each line with its lemma's #search-lines + total search CPU so
    # results can be rolled up per lemma. (The rewrite methods are per-line.)
    grp = {}
    for c in out:
        grp.setdefault((c["lemma"], c["thy"]), []).append(c)
    for (lem, thy), v in grp.items():
        for c in v:
            c["lemma_search_lines"] = len(v)
            c["lemma_search_cpu"] = round(sum(x["cpu_s"] for x in v), 2)
    out = sorted(out, key=lambda c: (-c["lemma_search_cpu"], c["lemma"], c["proof_line"]))
    params = {"MIN_CPU": MIN_CPU, "TACTICS": TACTICS, "MAX_FILE_LINES": MAX_FILE_LINES,
              "EXCLUDE_SESSIONS": sorted(EXCLUDE_SESSIONS)}
    json.dump({"params": params, "source": "build command_timings (heap DBs)",
               "n_total": len(out), "n_feasible": sum(c["feasible"] for c in out), "cases": out},
              open(OUT, "w"), ensure_ascii=False, indent=1)
    feas = [c for c in out if c["feasible"]]
    fs = [c for c in feas if not c["work_suspect"]]   # search-suspect LINES (experiment target)
    fw = [c for c in feas if c["work_suspect"]]        # work-suspect LINES (cost likely simp-work)
    nlemmas = len({(c["lemma"], c["thy"]) for c in out})
    multiline = sum(1 for k, v in grp.items() if len(v) > 1)
    fs_lemmas = len({(c["lemma"], c["thy"]) for c in fs})
    print(f"params: {params}  WORK_DEFS={WORK_DEFS}")
    print(f"high-value SEARCH LINES (every line kept; rewrite is per-line): {len(out)}  "
          f"across {nlemmas} lemmas ({multiline} lemmas have >1 search line)")
    print(f"  feasible: {len(feas)} lines ({sum(c['cpu_s'] for c in feas):.0f}s)  | infeasible(tool limit): {sum(not c['feasible'] for c in out)}")
    print(f"    - search-suspect (EXPERIMENT TARGET): {len(fs)} lines / {fs_lemmas} lemmas  ({sum(c['cpu_s'] for c in fs):.0f}s)")
    print(f"    - work-suspect (simp-work, flagged):  {len(fw)} lines  ({sum(c['cpu_s'] for c in fw):.0f}s)")
    print(f"-> {OUT}\n")
    print("top feasible SEARCH-SUSPECT lines (the experiment set; L*=lemma's #search lines):")
    for c in fs[:18]:
        print(f"  {c['cpu_s']:7.1f}s {c['session']:11s} {c['tactic']:9s} "
              f"{c['lemma'][:28]:28s} {c['thy'].split('/')[-1]}:{c['proof_line']}  (lemma:{c['lemma_search_lines']}L/{c['lemma_search_cpu']:.0f}s)")


main()
