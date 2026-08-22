#!/usr/bin/env python3
"""Submit out-of-band checker evidence to the Graph Revision Agent."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from regen.graph_revision_agent import revise_graph


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def next_decision_index(theory_dir: Path) -> int:
    indices = []
    for path in (theory_dir / "graph-revisions").glob("decision-*"):
        match = re.fullmatch(r"decision-(\d+)", path.name)
        if match:
            indices.append(int(match.group(1)))
    return max(indices, default=0) + 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--theory", required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--evidence", action="append", required=True)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    run = read_json(args.run_dir / "run.json")
    files_path = args.run_dir / "files.json"
    files = read_json(files_path)
    entry = next(item for item in files["files"] if item["theory"] == args.theory)
    theory_dir = args.run_dir / "theories" / entry["file_slot"].removesuffix(".thy")
    progress_path = theory_dir / "progress.json"
    progress = read_json(progress_path)
    graph = read_json(theory_dir / f"graph-v{progress['graph_revision']}.json")
    node = next(item for item in graph["nodes"] if item["id"] == args.node_id)
    environment_run = Path(run["environment_run"])
    package = read_json(
        environment_run / "M1" / "public" / "proof-file-packages"
        / f"{entry['file_slot']}.json"
    )
    green_ids = {
        node_id for node_id, status in progress["statuses"].items()
        if status == "green"
    }
    feedback = progress.get("proof_feedback_by_node", {}).get(args.node_id, {})
    red_result = {
        "status": "red",
        "node_id": args.node_id,
        "statement": node["statement"],
        "residual": "\n".join(args.evidence),
        "attempts": feedback.get("proof_candidates", []),
        "retrieved_legal_premises": [],
        "evidence_source": "checker-dependency-closure-audit",
    }
    revised, decision = revise_graph(
        package, graph, red_result, green_ids,
        output_dir=theory_dir,
        provider="codex",
        model=args.model,
        timeout=args.timeout,
        decision_index=next_decision_index(theory_dir),
        decision_context={
            "phase": "dependency-closure-audit",
            "failed_statement_node_id": args.node_id,
            "retry_proof_allowed": False,
            "trusted_framework_semantic_evidence": args.evidence,
            "requested_review": (
                "Review whether the declared dependency edge from "
                "regen_setObject_other_corres_bridge to "
                "regen_setObject_other_state_relation_update is stale."
            ),
        },
        premise_database=Path(run["premise_database"]),
    )
    if revised["revision"] == graph["revision"]:
        print(json.dumps(decision, ensure_ascii=False, indent=2))
        return 2

    progress["graph_revision"] = revised["revision"]
    progress["statuses"] = {
        item["id"]: "green" if item["id"] in green_ids else "planned"
        for item in revised["nodes"]
    }
    progress.get("proof_feedback_by_node", {}).pop(args.node_id, None)
    progress.get("retrieval_hints", {}).pop(args.node_id, None)
    write_json(progress_path, progress)

    entry.update({
        "status": "generating",
        "graph_revision": revised["revision"],
        "graph_source": str(theory_dir / f"graph-v{revised['revision']}.json"),
    })
    files["events"].append({
        "theory": args.theory,
        "status": "generating",
        "reason": "accepted dependency-closure graph revision",
    })
    write_json(files_path, files)
    print(json.dumps({
        "decision": decision,
        "graph_revision": revised["revision"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
