#!/usr/bin/env python3
"""report.py — assemble a PR-style human-review artifact from a pipeline run.

Input: coevolve/pipeline-runs/<run_id>/run-record.json (+ per-file fix-*.thy,
claude transcripts). Output: report.md in the same dir — diff, LLM rationale,
quality vector, cost, and the human-review queue (the "semi" surface).
"""
import json, os, re, subprocess, sys

B = "/data/zljj/sel4-dev"


def sh(cmd):
    # always run relative to the B repo so relative run_dir args resolve
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "zljj@114.212.82.216",
                           "cd %s && %s" % (B, cmd)],
                          capture_output=True, text=True).stdout


def rationale(transcripts):
    """Prose from the last reply-bearing round (skip transport-timeout rounds,
    which archive only a prompt)."""
    for tp in reversed(transcripts):
        txt = sh("cat %s 2>/dev/null" % tp)
        if "=== REPLY (full) ===" in txt:
            body = txt.rsplit("=== REPLY (full) ===", 1)[1]
            pre = body.split("<<<<SEARCH")[0].strip()
            if pre:
                return pre[:900]
    return "(no reply-bearing round — all transport timeouts)"


def main():
    run_dir = sys.argv[1]
    rec = json.loads(sh("cat %s/run-record.json" % run_dir))
    L = []
    L.append("# Co-evolution repair — run `%s`\n" % rec["run_id"])
    a = rec.get("accounting", {})
    L.append("**Final: %s** · session `%s` · seed `%s`\n"
             % (rec.get("final"), rec.get("session"), rec.get("seed")))
    L.append("| files repaired | escalated | autonomy | LLM calls | cost | wall |")
    L.append("|---|---|---|---|---|---|")
    L.append("| %s | %s | %s | %s | $%s | %ss |\n" % (
        a.get("files_repaired"), a.get("files_escalated"),
        a.get("autonomy_rate"), a.get("total_llm_calls"),
        a.get("total_cost_usd"), a.get("wall_s")))

    if a.get("human_review_queue"):
        L.append("## ⚠ Human-review queue (the \"semi\" surface)")
        for f in a["human_review_queue"]:
            L.append("- `%s` — repair budget exhausted; needs a human." % f)
        L.append("")

    for fr in rec.get("files", []):
        L.append("## `%s` — %s (%d effective rounds)"
                 % (fr["file"], fr["verdict"], fr["rounds"]))
        cost = sum((m.get("cost_usd") or 0) for m in fr["llm"])
        L.append("build wall %ds · %d LLM calls · $%.4f\n"
                 % (fr.get("build_wall_s", 0), len(fr["llm"]), cost))
        # diff vs pre-repair tree state (HEAD)
        fix = "%s/fix-%s.thy" % (run_dir, fr["file"].replace("/", "_"))
        diff = sh("cd %s && git -C verification/l4v show HEAD:%s > /tmp/orig.thy "
                  "2>/dev/null; diff -u /tmp/orig.thy %s 2>/dev/null | head -60"
                  % (B, fr["file"], fix))
        if diff.strip():
            L.append("```diff\n%s\n```" % diff.strip())
        if fr.get("transcripts"):
            L.append("\n**LLM rationale (last reply-bearing round):**\n> %s\n"
                     % rationale(fr["transcripts"]).replace("\n", "\n> "))

    out = run_dir + "/report.md"
    sh("cat > %s <<'REPORTEOF'\n%s\nREPORTEOF" % (out, "\n".join(L)))
    print("wrote %s (%d lines)" % (out, len(L)))
    print("\n".join(L[:12]))


if __name__ == "__main__":
    main()
