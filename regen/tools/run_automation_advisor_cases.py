#!/usr/bin/env python3
"""Regenerate selected KHeap_R nodes with the Automation Advisor branch."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.stepwise_lemma_backend import prove_node_stepwise


DEFAULT_CASES = [
    "regen_setObject_other_cte_not_tcb_relation_cut",
    "regen_getEndpoint_concrete_preserves_state",
    "getEndpoint_corres",
]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir", type=Path,
        default=Path("reports/regen/proof-runs/refine-main"),
    )
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--think-timeout", type=int, default=180)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--outline-model", default="gpt-5.5")
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    source_dir = run_dir / "theories/KHeap_R"
    graph = read_json(source_dir / "graph-v77.json")
    progress = read_json(source_dir / "progress.json")
    run = read_json(run_dir / "run.json")
    files = read_json(Path(run["session_progress_ledger"]))
    by_name = {node["name"]: node for node in graph["nodes"]}
    cases = args.cases or DEFAULT_CASES
    unknown = [name for name in cases if name not in by_name]
    if unknown:
        raise SystemExit(f"unknown graph-v77 cases: {unknown}")

    sealed_ancestors = [
        item["file_slot"].removesuffix(".thy") for item in files["files"]
        if item["status"] == "sealed"
    ]
    output_dir = run_dir / "automation-advisor" / "KHeap_R"
    output_dir.mkdir(parents=True, exist_ok=True)
    checker = IsabelleChecker(
        Path(run["environment_run"]), output_dir / "isabelle",
        timeout=args.timeout, previous_theories=sealed_ancestors,
        generated_dir=run_dir / "generated", heaps_dir=run_dir / "heaps",
        l4v_dir=Path(run["l4v"]),
        semantic_workspace=Path(run["semantic_workspace"]),
    )
    proofs = progress["proofs"]
    results = []
    for name in cases:
        node_id = by_name[name]["id"]
        safe = re.sub(r"[^A-Za-z0-9_]", "_", node_id)[-80:]
        node_dir = output_dir / "nodes" / safe
        prior_rounds = [
            int(match.group(1)) for path in node_dir.glob("round-*")
            if (match := re.fullmatch(r"round-(\d+)", path.name))
        ]
        round_id = max(prior_rounds, default=0) + 1
        result = prove_node_stepwise(
            graph, node_id, proofs, checker=checker,
            premise_database=Path(run["premise_database"]),
            generated_records=None,
            output_dir=output_dir, model=args.model,
            timeout=args.timeout, think_timeout=args.think_timeout,
            width=8, max_attempts=8, selected_states=2, max_depth=48,
            hammer_leaves=0, round_id=round_id,
            retrieval_hint="Automation Advisor independent regeneration case",
            previous_round_feedback=None,
            outline_provider="codex", outline_model=args.outline_model,
        )
        results.append({
            "case": name,
            "node_id": node_id,
            "status": result["status"],
            "backend": result.get("backend"),
            "failure_class": result.get("failure_class"),
            "stepwise_search_status": result.get("stepwise_search_status"),
            "final_replay_status": result.get("final_replay_status"),
            "proof_body": result.get("proof_body", ""),
            "residual": result.get("residual", ""),
            "retrieved_premises": result.get("retrieved_premises", []),
            "local_proof_outline": result.get("local_proof_outline"),
        })
        case_results = output_dir / "case-results"
        case_results.mkdir(parents=True, exist_ok=True)
        (case_results / f"{name}.round-{round_id:02d}.json").write_text(
            json.dumps(results[-1], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (output_dir / "experiment-results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(item["status"] == "green" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
