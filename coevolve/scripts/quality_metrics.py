#!/usr/bin/env python3
"""quality_metrics.py — the two cheap robustness proxies per repair.
1) fragility profile: tactic composition of ADDED lines (search-heavy vs named)
2) fan-in exposure: how many proof/ files cite each touched/added lemma name
Usage: quality_metrics.py <case> <repaired_content_file> ; writes repair/quality.json
"""
import json, re, subprocess, sys

SEARCHY = r"\b(auto|fastforce|force|blast|clarsimp|metis|smt|meson)\b|simp add:|simp_all"
NAMED = r"\b(rule|erule|drule|frule|intro|elim|subst|unfold)\b|simp only:"

case, repfile = sys.argv[1], sys.argv[2]
base = "coevolve/cases/" + case
broken = open("logs/adj-%s-0.patch" % case).read().split("\n", 1)[1].split("\n")
rep = open(repfile).read().split("\n")
import difflib
added = [l for l in difflib.unified_diff(broken, rep, lineterm="", n=0)
         if l.startswith("+") and not l.startswith("+++")]
frag = {"added_lines": len(added),
        "searchy": sum(1 for l in added if re.search(SEARCHY, l)),
        "named": sum(1 for l in added if re.search(NAMED, l))}
# touched lemma names = GT touched + names newly defined in added lines
gt = json.load(open(base + "/ground_truth.json"))
names = set()
for f in gt.values():
    names |= {n for n, e in f["lemmas"].items()
              if e.get("kind") in ("modified", "added", "relocated")}
for l in added:
    m = re.match(r"\+\s*lemma\s+([A-Za-z_][\w']*)", l)
    if m:
        names.add(m.group(1))
fanin = {}
for n in sorted(names):
    r = subprocess.run(["git", "-C", "verification/l4v", "grep", "-lw", n,
                        "--", "proof/"], capture_output=True, text=True)
    fanin[n] = len([x for x in r.stdout.split("\n") if x.strip()])
out = {"fragility": frag, "fanin_exposure": fanin}
json.dump(out, open(base + "/repair/quality.json", "w"), indent=1)
print(json.dumps(out, indent=1))
