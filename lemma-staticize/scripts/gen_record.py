"""Render one bench candidate dir into the two human/machine timing artifacts:
  record.md    — one-page audit view: before/after code, diff, search tree (with the
                 LLM's per-node reasoning), per-line timing.
  timing.json  — before/after total + per-line timing (verbatim from the gate profiles).

Reads result.json (gate verdict, with original_lemma/modified_lemma bundles) and
attempts.jsonl (node + attempt event stream) from the candidate dir. Usage:
    python3 gen_record.py <candidate_dir>
"""
import json, os, sys, difflib

cd = sys.argv[1]
r = json.load(open(os.path.join(cd, "result.json")))
ol = r.get("original_lemma") or {}
ml = r.get("modified_lemma") or {}

# ---- timing.json (before + after, total + per-line) ----
json.dump(
    {"before": {"total_ms": ol.get("total_ms"), "time_status": ol.get("time_status"),
                "line_timings": ol.get("line_timings", [])},
     "after":  {"total_ms": ml.get("total_ms"), "time_status": ml.get("time_status"),
                "line_timings": ml.get("line_timings", [])}},
    open(os.path.join(cd, "timing.json"), "w"), indent=1, ensure_ascii=False)

# ---- read the event stream ----
events = []
ap = os.path.join(cd, "attempts.jsonl")
if os.path.exists(ap):
    for line in open(ap):
        try: events.append(json.loads(line))
        except Exception: pass

def fmt(v): return "—" if v is None else f"{v:g}" if isinstance(v, float) else str(v)

L = []
L.append(f"# {r.get('lemma')} — {r.get('verdict','?')}")
L.append("")
L.append(f"- thy `{r.get('thy')}`  ·  session **{r.get('session')}**  ·  proof span {r.get('proof_span')}")
L.append(f"- lines modified: **{r.get('modified_line_count', 0)}**")
ot, st, dp = ol.get("total_ms"), ml.get("total_ms"), r.get("delta_pct")
tline = f"- total timing: **{fmt(ot)}ms → {fmt(st)}ms**"
if dp is not None: tline += f"  ({dp}%)"
if r.get("build_ms"): tline += f"  ·  build {fmt(r.get('build_ms'))}ms"
L.append(tline)
L.append("")

# ---- before / after / diff ----
L.append("## Before")
L += ["```isabelle", (ol.get("text") or "(unavailable)").rstrip(), "```", ""]
L.append("## After")
if ml.get("text"):
    L += ["```isabelle", ml["text"].rstrip(), "```", ""]
    diff = list(difflib.unified_diff((ol.get("text") or "").splitlines(),
                                     ml["text"].splitlines(), "before", "after", lineterm=""))
    if diff:
        L += ["## Diff", "```diff", *diff, "```", ""]
else:
    L += ["_— no audit-passing static path found —_", ""]

# ---- search tree with per-node LLM reasoning ----
L.append("## Search — how the line was rewritten (node = a goal state; ✗ fail · → progress · ✓✓ closes)")
L.append("```")
for e in events:
    k = e.get("kind")
    if k == "node":
        ind = "  " * e.get("d", 0)
        L.append(f"{ind}● NODE {e.get('cp')}  (menu={len(e.get('menu') or [])}  facts={e.get('facts')})")
        fs = e.get("failures_shown") or []
        if fs: L.append(f"{ind}  failures fed to model ({len(fs)}): {fs[-6:]}")
        picks = e.get("llm_ranked") or []
        if picks: L.append(f"{ind}  LLM ranked: {picks}")
        th = (e.get("llm_thought") or "").strip()
        if th: L.append(f"{ind}  thought: {th[:220]}")
    else:
        ind = "  " * (e.get("d", 0) + 1)
        tac = e.get("tac", "")
        if "CLOSES" in (k or ""):   L.append(f"{ind}✓✓ {tac}  → CLOSES (==target)")
        elif k == "progress":       L.append(f"{ind}→  {tac}  → {e.get('ngoals')}sg  {(e.get('first_goal') or '')[:60]}")
        elif k == "fail":           L.append(f"{ind}✗  {tac}")
        elif "growth" in (k or ""): L.append(f"{ind}✂  {tac}  (pruned: diverging)")
        elif "seen" in (k or ""):   L.append(f"{ind}↺  {tac}  (pruned: seen)")
        elif "memo" in (k or ""):   L.append(f"{ind}↺  {tac}  (memo: failed before)")
        else:                       L.append(f"{ind}?  {tac}  {k}")
L.append("```")
L.append("")

# ---- per-line timing (before and after shown separately — line numbers shift) ----
def timing_table(title, rows):
    out = [f"### {title}", "| line | elapsed ms | cpu ms | % | text |", "|---:|---:|---:|---:|---|"]
    for t in rows:
        txt = (t.get("text") or "").replace("|", "\\|")[:64]
        out.append(f"| {t.get('line')} | {fmt(t.get('elapsed_ms'))} | {fmt(t.get('cpu_ms'))} | "
                   f"{fmt(t.get('frac_of_total'))} | `{txt}` |")
    return out

L.append("## Per-line timing")
L += timing_table(f"before (total {fmt(ot)}ms)", ol.get("line_timings", []))
if ml.get("line_timings"):
    L += [""] + timing_table(f"after (total {fmt(st)}ms)", ml.get("line_timings", []))

open(os.path.join(cd, "record.md"), "w").write("\n".join(L) + "\n")
print(f"[record] {os.path.join(cd, 'record.md')}")
