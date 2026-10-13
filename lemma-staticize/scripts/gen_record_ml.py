#!/usr/bin/env python3
"""Render a MULTI-LINE static-ization record from result.json + attempts.jsonl.
Writes record.md (one page) + timing.json + keeps attempts.jsonl/result.json/gate.log.
Usage: gen_record_ml.py <result.json> <attempts.jsonl> <out_dir>
"""
import json, os, sys, difflib
from collections import defaultdict

rp, ap, cd = sys.argv[1], sys.argv[2], sys.argv[3]
r = json.load(open(rp))
ev = [json.loads(l) for l in open(ap)] if os.path.exists(ap) else []

ol = r.get("original_lemma") or {}
ml = r.get("modified_lemma") or {}
lines = r.get("lines") or []

def w(name, text): open(os.path.join(cd, name), "w").write(text)

# timing.json
json.dump({"before": {"total_ms": ol.get("total_ms"), "line_timings": ol.get("line_timings", [])},
           "after":  {"total_ms": ml.get("total_ms"), "line_timings": ml.get("line_timings", [])}},
          open(os.path.join(cd, "timing.json"), "w"), indent=1, ensure_ascii=False)

# group search events by proof-command index (`line`); render a tree per group
by_line = defaultdict(list)
for e in ev:
    by_line[e.get("line")].append(e)

def render_tree(events):
    out = []
    for e in events:
        k = e.get("kind")
        if k == "node":
            out.append(f"  ● NODE {e.get('cp')}  facts={e.get('facts')}")
            if e.get("llm_ranked"): out.append(f"      LLM ranked: {e.get('llm_ranked')}")
            if e.get("llm_thought"): out.append(f"      thought: {e.get('llm_thought')[:160]}")
        elif "CLOSES" in (k or ""):
            out.append(f"      ✓✓ {e.get('tac')}  -> CLOSES")
        elif k == "progress":
            out.append(f"      → {e.get('tac')}  [{e.get('ngoals')}sg]")
        elif k == "fail":
            out.append(f"      ✗ {e.get('tac')}")
    return "\n".join(out)

def timing_table(lt):
    rows = ["| line | elapsed ms | cpu ms | % | text |", "|---:|---:|---:|---:|---|"]
    for t in lt:
        em = "" if t.get("elapsed_ms") is None else round(t["elapsed_ms"])
        cm = "" if t.get("cpu_ms") is None else round(t["cpu_ms"])
        fr = "" if t.get("frac_of_total") is None else t["frac_of_total"]
        rows.append(f"| {t.get('line')} | {em} | {cm} | {fr} | `{(t.get('text') or '')[:60]}` |")
    return "\n".join(rows)

md = []
verd = r.get("verdict", "?")
md.append(f"# {r.get('lemma')} — {verd}   (multi-line)\n")
md.append(f"- thy `{r.get('thy')}`  ·  session **{r.get('session')}**")
md.append(f"- search-commands solved: **{r.get('targets_solved','?')} / {r.get('targets_total','?')}**  "
          f"(rewrote {r.get('modified_line_count', len(lines))} lines)")
if ol.get("total_ms") is not None and ml.get("total_ms") is not None:
    md.append(f"- total lemma timing: **{round(ol['total_ms'])}ms → {round(ml['total_ms'])}ms**  "
              f"({r.get('delta_pct')}%)  ·  build {r.get('build_ms')}ms")
md.append("")

if ol.get("text"):
    md.append("## Before\n```isabelle\n" + ol["text"] + "\n```\n")
if ml.get("text"):
    md.append("## After\n```isabelle\n" + ml["text"] + "\n```\n")
    diff = difflib.unified_diff((ol.get("text") or "").splitlines(), ml["text"].splitlines(),
                                "before", "after", lineterm="")
    md.append("## Diff\n```diff\n" + "\n".join(diff) + "\n```\n")

md.append("## Rewritten commands (linear, top-down)")
for i, ln in enumerate(lines):
    md.append(f"- **line {ln.get('proof_line')}** (closes={ln.get('closes')}): "
              f"`{' ; '.join(ln.get('path') or [])}`")
md.append("")

md.append("## Search per line (node = goal state; ✗ fail · → progress · ✓✓ closes)\n```")
for li in sorted(by_line):
    if li is None: continue
    md.append(f"line-index {li}:")
    md.append(render_tree(by_line[li]))
md.append("```\n")

if ol.get("line_timings"):
    md.append(f"## Per-line timing\n### before (total {round(ol.get('total_ms') or 0)}ms)")
    md.append(timing_table(ol["line_timings"]))
if ml.get("line_timings"):
    md.append(f"\n### after (total {round(ml.get('total_ms') or 0)}ms)")
    md.append(timing_table(ml["line_timings"]))

w("record.md", "\n".join(md) + "\n")
print(f"[record] {cd}/record.md")
