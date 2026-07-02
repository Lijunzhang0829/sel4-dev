#!/usr/bin/env python3
"""calibrate.py — score localizer_pred.json against ground_truth.json.

File-level precision/recall; lemma-level recall within true-positive files;
statement_must_evolve flag accuracy on matched lemmas. Python 3.8.
"""
import json, os, sys


def score(case_dir):
    gt = json.load(open(os.path.join(case_dir, "ground_truth.json")))
    pred = json.load(open(os.path.join(case_dir, "localizer_pred.json")))
    gt_files = set(gt.keys())
    pred_files = {w["file"] for w in pred.get("work_list", [])}

    tp_f = gt_files & pred_files
    prec = len(tp_f) / len(pred_files) if pred_files else 0.0
    rec = len(tp_f) / len(gt_files) if gt_files else 0.0

    lem_hits, lem_total, evo_ok, evo_total, rows = 0, 0, 0, 0, []
    pred_by_file = {w["file"]: w for w in pred.get("work_list", [])}
    for f in sorted(tp_f):
        gt_lems = {k: v for k, v in gt[f]["lemmas"].items()
                   if v.get("kind") in ("modified", "added", "deleted")}
        pred_lems = {l["name"]: l for l in pred_by_file[f].get("lemmas", [])}
        for name, g in gt_lems.items():
            lem_total += 1
            p = pred_lems.get(name)
            if p:
                lem_hits += 1
                evo_total += 1
                match = bool(p.get("statement_must_evolve")) == bool(
                    g.get("statement_evolved"))
                evo_ok += int(match)
                rows.append((f, name, g.get("statement_evolved"),
                             p.get("statement_must_evolve"), "HIT"))
            else:
                rows.append((f, name, g.get("statement_evolved"), None, "MISS"))

    out = {
        "file_precision": round(prec, 3), "file_recall": round(rec, 3),
        "gt_files": sorted(gt_files), "pred_files": sorted(pred_files),
        "lemma_recall_in_tp_files":
            round(lem_hits / lem_total, 3) if lem_total else None,
        "evolve_flag_accuracy":
            round(evo_ok / evo_total, 3) if evo_total else None,
        "lemma_rows": [
            {"file": r[0], "lemma": r[1], "gt_evolved": r[2],
             "pred_evolve": r[3], "status": r[4]} for r in rows],
    }
    json.dump(out, open(os.path.join(case_dir, "calibration.json"), "w"), indent=1)
    return out


if __name__ == "__main__":
    for d in sys.argv[1:]:
        try:
            o = score(d)
        except FileNotFoundError as e:
            print("%-28s SKIP (%s)" % (os.path.basename(d), e.filename))
            continue
        print("%-28s fileP=%.2f fileR=%.2f lemR=%s evoAcc=%s"
              % (os.path.basename(d), o["file_precision"], o["file_recall"],
                 o["lemma_recall_in_tp_files"], o["evolve_flag_accuracy"]))
        for r in o["lemma_rows"]:
            print("    %-6s %-45s gt_evo=%-5s pred_evo=%s"
                  % (r["status"], r["lemma"][:45], r["gt_evolved"], r["pred_evolve"]))
