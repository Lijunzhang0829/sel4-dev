#!/usr/bin/env python3
"""Render a record.md for the claude -p rewrite loop from gate result.json + the
react event stream (think/act). Writes record.md + timing.json.
Usage: gen_record_claude.py <result.json> <attempts.jsonl> <out_dir>
"""
import json, os, sys, difflib

rp, ap, cd = sys.argv[1], sys.argv[2], sys.argv[3]
r = json.load(open(rp))
ev = [json.loads(l) for l in open(ap)] if os.path.exists(ap) else []
ol = r.get("original_lemma") or {}
ml = r.get("modified_lemma") or {}

json.dump({"before": {"total_ms": ol.get("total_ms"), "line_timings": ol.get("line_timings", [])},
           "after":  {"total_ms": ml.get("total_ms"), "line_timings": ml.get("line_timings", [])}},
          open(os.path.join(cd, "timing.json"), "w"), indent=1, ensure_ascii=False)

md = []
md.append(f"# {r.get('lemma')} — {r.get('verdict','?')}   (claude -p loop)\n")
md.append(f"- thy `{r.get('thy')}`  ·  session **{r.get('session')}**  ·  loop_verdict {r.get('loop_verdict')}")
if ol.get("total_ms") is not None and ml.get("total_ms") is not None:
    md.append(f"- total timing: **{round(ol['total_ms'])}ms → {round(ml['total_ms'])}ms**  "
              f"({r.get('delta_pct')}%)  ·  build {r.get('build_ms')}ms")
if r.get("path"):
    md.append(f"- static path ({len(r['path'])} steps): `{' ; '.join(r['path'])}`")
md.append("")

if ol.get("text"): md.append("## Before\n```isabelle\n" + ol["text"] + "\n```\n")
if ml.get("text"):
    md.append("## After\n```isabelle\n" + ml["text"] + "\n```\n")
    diff = difflib.unified_diff((ol.get("text") or "").splitlines(), ml["text"].splitlines(),
                                "before", "after", lineterm="")
    md.append("## Diff\n```diff\n" + "\n".join(diff) + "\n```\n")

# the claude reasoning loop: per step, thought -> chosen action -> result
md.append("## claude -p reasoning loop (think → act)\n```")
think = {e["step"]: e for e in ev if e.get("kind") == "think"}
for e in ev:
    if e.get("kind") == "think":
        md.append(f"\nstep {e.get('step')}: 💭 {(e.get('thought') or '')[:200]}")
        md.append(f"        proposed: {e.get('proposed')}")
    elif e.get("kind") == "act":
        if e.get("closes"): md.append(f"        ✓✓ {e.get('action')}  -> CLOSES")
        elif e.get("ok"):   md.append(f"        → {e.get('action')}  [{e.get('ngoals')}sg]")
        else:               md.append(f"        ✗ {e.get('action')}  -> {(e.get('obs') or '')[:60]}")
md.append("```\n")

def ttable(lt):
    rows = ["| line | elapsed ms | cpu ms | % | text |", "|---:|---:|---:|---:|---|"]
    for t in lt:
        em = "" if t.get("elapsed_ms") is None else round(t["elapsed_ms"])
        cm = "" if t.get("cpu_ms") is None else round(t["cpu_ms"])
        fr = "" if t.get("frac_of_total") is None else t["frac_of_total"]
        rows.append(f"| {t.get('line')} | {em} | {cm} | {fr} | `{(t.get('text') or '')[:55]}` |")
    return "\n".join(rows)
if ol.get("line_timings"):
    md.append(f"## Per-line timing\n### before (total {round(ol.get('total_ms') or 0)}ms)")
    md.append(ttable(ol["line_timings"]))
if ml.get("line_timings"):
    md.append(f"\n### after (total {round(ml.get('total_ms') or 0)}ms)")
    md.append(ttable(ml["line_timings"]))

open(os.path.join(cd, "record.md"), "w").write("\n".join(md) + "\n")
print(f"[record] {cd}/record.md")
