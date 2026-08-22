#!/usr/bin/env python3
"""M4: freeze a layered Refine generation list and a mutable file ledger."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from regen.semantic_base_pipeline import (
    build_layered_file_generation_list,
    build_progress_ledger,
    read_json,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--edges", type=Path)
    parser.add_argument("--file-list", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args()
    environment = read_json(args.environment)
    edges = []
    if args.edges:
        payload = read_json(args.edges)
        edges = [
            (item["provider"], item["consumer"])
            for item in payload.get("generation_edges", [])
        ]
    file_list = build_layered_file_generation_list(
        environment["campaign_id"], environment, edges,
    )
    ledger = build_progress_ledger(file_list)
    write_json(args.file_list, file_list)
    write_json(args.ledger, ledger)
    print(json.dumps({
        "files": file_list["file_count"],
        "layers": len(file_list["layers"]),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
