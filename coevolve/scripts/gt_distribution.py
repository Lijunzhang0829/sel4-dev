#!/usr/bin/env python3
"""gt_distribution.py — aggregate L1/L2 distribution over cases/*/ground_truth.json.
L1-ish = touched lemma with body change only; L2-ish = statement evolved
(modified w/ statement_evolved, or added/deleted). Python 3.8."""
import glob, json, os, sys

base = sys.argv[1] if len(sys.argv) > 1 else "coevolve/cases"
rows, tot = [], {"files": 0, "mod_stmt": 0, "mod_body": 0, "reloc": 0,
                 "added": 0, "deleted": 0, "other": 0}
for gt_path in sorted(glob.glob(os.path.join(base, "*", "ground_truth.json"))):
    c = os.path.basename(os.path.dirname(gt_path))
    gt = json.load(open(gt_path))
    meta = json.load(open(os.path.join(os.path.dirname(gt_path), "meta.json")))
    n = {"mod_stmt": 0, "mod_body": 0, "reloc": 0, "added": 0, "deleted": 0,
         "other": 0}
    for f, v in gt.items():
        for name, e in v["lemmas"].items():
            k = e.get("kind")
            if k == "modified":
                n["mod_stmt" if e.get("statement_evolved") else "mod_body"] += 1
            elif k == "relocated":
                n["reloc"] += 1
            elif k == "added":
                n["added"] += 1
            elif k == "deleted":
                n["deleted"] += 1
            else:
                n["other"] += 1
    tot["files"] += len(gt)
    for k in n:
        tot[k] += n[k]
    level = "L2" if (n["mod_stmt"] or n["added"] or n["deleted"]) else "L1"
    rows.append((c, meta["subject"][:46], len(gt),
                 ",".join(meta["sessions"]), n, level))

print("| case | subject | files | sessions | stmt-evo | body-only | reloc | added | del | level |")
print("|---|---|---|---|---|---|---|---|---|---|")
for c, s, nf, sess, n, lv in rows:
    print("| %s | %s | %d | %s | %d | %d | %d | %d | %d | **%s** |"
          % (c[:9], s, nf, sess, n["mod_stmt"], n["mod_body"], n["reloc"],
             n["added"], n["deleted"], lv))
l2 = sum(1 for r in rows if r[5] == "L2")
print("\n**Totals**: %d cases (%d L2-involving / %d pure-L1), %d proof files, "
      "lemma edits: %d stmt-evolved · %d body-only · %d relocated · %d added · %d deleted"
      % (len(rows), l2, len(rows) - l2, tot["files"], tot["mod_stmt"],
         tot["mod_body"], tot["reloc"], tot["added"], tot["deleted"]))
