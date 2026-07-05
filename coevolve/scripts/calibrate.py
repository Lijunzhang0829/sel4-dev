#!/usr/bin/env python3
"""calibrate.py — score localizer_pred.json against ground_truth.json.

Lemma-level recall is computed ONLY over name-matchable GT lemmas
(kind=modified/deleted — they existed pre-change). Human-ADDED lemmas
cannot be predicted by name; they are counted separately (file-level
recall is the credit surface for them). Python 3.8.
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

    lem_hits = lem_total = evo_ok = evo_total = n_added = 0
    rows = []
    pred_by_file = {w["file"]: w for w in pred.get("work_list", [])}
    for f in sorted(gt_files):
        all_lems = {k: v for k, v in gt[f]["lemmas"].items()
                    if v.get("kind") in ("modified", "added", "deleted")}
        matchable = {k: v for k, v in all_lems.items()
                     if v["kind"] in ("modified", "deleted")}
        n_added += sum(1 for v in all_lems.values() if v["kind"] == "added")
        if f not in tp_f:
            for name, g in matchable.items():
                rows.append((f, name, g.get("statement_evolved"), None,
                             "MISS-FILE"))
            continue
        pred_lems = {l["name"]: l for l in pred_by_file[f].get("lemmas", [])}
        for name, g in matchable.items():
            lem_total += 1
            p = pred_lems.get(name)
            if p:
                lem_hits += 1
                evo_total += 1
                evo_ok += int(bool(p.get("statement_must_evolve"))
                              == bool(g.get("statement_evolved")))
                rows.append((f, name, g.get("statement_evolved"),
                             p.get("statement_must_evolve"), "HIT"))
            else:
                rows.append((f, name, g.get("statement_evolved"), None, "MISS"))

    out = {
        "file_precision": round(prec, 3), "file_recall": round(rec, 3),
        "n_gt_files": len(gt_files), "n_pred_files": len(pred_files),
        "n_tp_files": len(tp_f),
        "gt_files": sorted(gt_files), "pred_files": sorted(pred_files),
        "lemma_recall_modified":
            round(lem_hits / lem_total, 3) if lem_total else None,
        "n_matchable": lem_total, "n_lem_hits": lem_hits,
        "n_gt_added": n_added,
        "evolve_flag_accuracy":
            round(evo_ok / evo_total, 3) if evo_total else None,
        "n_evo_ok": evo_ok, "n_evo_total": evo_total,
        "lemma_rows": [
            {"file": r[0], "lemma": r[1], "gt_evolved": r[2],
             "pred_evolve": r[3], "status": r[4]} for r in rows],
    }
    json.dump(out, open(os.path.join(case_dir, "calibration.json"), "w"), indent=1)
    return out


if __name__ == "__main__":
    agg = {"tp_f": 0, "pred_f": 0, "gt_f": 0, "lem_hits": 0, "lem_total": 0,
           "evo_ok": 0, "evo_total": 0, "added": 0, "cases": 0, "skipped": 0}
    print("%-14s %5s %5s %6s %7s %6s" %
          ("case", "fileP", "fileR", "lemR", "evoAcc", "added"))
    for d in sys.argv[1:]:
        base = os.path.basename(d.rstrip("/"))
        try:
            o = score(d)
        except FileNotFoundError:
            print("%-14s SKIP (no prediction)" % base)
            agg["skipped"] += 1
            continue
        agg["cases"] += 1
        agg["tp_f"] += o["n_tp_files"]; agg["pred_f"] += o["n_pred_files"]
        agg["gt_f"] += o["n_gt_files"]
        agg["lem_hits"] += o["n_lem_hits"]; agg["lem_total"] += o["n_matchable"]
        agg["evo_ok"] += o["n_evo_ok"]; agg["evo_total"] += o["n_evo_total"]
        agg["added"] += o["n_gt_added"]
        fmt = lambda v: "-" if v is None else "%.2f" % v
        print("%-14s %5s %5s %6s %7s %6d" %
              (base, fmt(o["file_precision"]), fmt(o["file_recall"]),
               fmt(o["lemma_recall_modified"]),
               fmt(o["evolve_flag_accuracy"]), o["n_gt_added"]))
    if agg["cases"]:
        micro = lambda a, b: "%.2f" % (a / b) if b else "-"
        print("-" * 52)
        print("%-14s %5s %5s %6s %7s %6d   (micro, n=%d, skipped=%d)" %
              ("MICRO-AVG",
               micro(agg["tp_f"], agg["pred_f"]),
               micro(agg["tp_f"], agg["gt_f"]),
               micro(agg["lem_hits"], agg["lem_total"]),
               micro(agg["evo_ok"], agg["evo_total"]),
               agg["added"], agg["cases"], agg["skipped"]))
