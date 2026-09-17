#!/usr/bin/env python3
"""Safe wiring smoke for the Stepwise backend; sends no project premises."""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.stepwise_lemma_backend import prove_node_stepwise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment-run", type=Path, required=True)
    parser.add_argument("--proof-run", type=Path, required=True)
    args = parser.parse_args()
    environment = args.environment_run.resolve()
    proof_run = args.proof_run.resolve()
    run = json.loads((proof_run / "run.json").read_text(encoding="utf-8"))
    output = proof_run / "backend-smoke"
    if output.exists():
        shutil.rmtree(output)

    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "empty.sqlite3"
        connection = sqlite3.connect(database)
        connection.execute(
            "CREATE TABLE premises (record_id TEXT, canonical_name TEXT, "
            "exact_statement TEXT, owner_theory TEXT, owner_session TEXT, "
            "source_class TEXT, attributes_json TEXT)"
        )
        connection.commit()
        connection.close()

        checker = IsabelleChecker(
            environment, output / "checker", timeout=600,
            generated_dir=proof_run / "generated",
            heaps_dir=proof_run / "heaps",
            l4v_dir=Path(run["l4v"]),
            semantic_workspace=Path(run["semantic_workspace"]),
        )
        graph = {
            "run_id": "stepwise-safe-smoke",
            "theory": "Smoke",
            "file_slot": "Smoke.thy",
            "revision": 1,
            "execution_order": ["node:smoke"],
            "nodes": [{
                "id": "node:smoke",
                "name": "stepwise_backend_smoke",
                "role": "helper",
                "statement": 'lemma stepwise_backend_smoke: "rev (rev xs) = xs"',
                "context": [],
                "dependencies": [],
                "consumers": ["contract:smoke"],
                "strategy": "simp or induction",
            }],
        }
        result = prove_node_stepwise(
            graph, "node:smoke", {}, checker=checker,
            premise_database=database, output_dir=output,
            model="gpt-5.5", timeout=600, width=4, max_attempts=2,
            selected_states=1, max_depth=6, hammer_leaves=2,
        )
    print(result)
    return 0 if result["status"] == "green" else 2


if __name__ == "__main__":
    raise SystemExit(main())
