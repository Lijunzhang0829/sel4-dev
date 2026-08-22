#!/usr/bin/env python3
"""M2: bind generated modules to one checked Semantic Base Isabelle context."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from regen.semantic_base_pipeline import (
    build_checker_manifest,
    read_json,
    reject_legacy_classification,
    write_json,
)


def build_m2(
    environment: dict[str, Any],
    packages: list[dict[str, Any]],
    *,
    parent_sessions: list[str],
) -> dict[str, Any]:
    reject_legacy_classification(environment)
    pending = [
        {
            "theory": package["theory"],
            "review_id": item["review_id"],
            "kind": item["kind"],
        }
        for package in packages
        for item in package.get("framework_review_items", [])
        if item.get("resolution") == "pending"
    ]
    checker = build_checker_manifest(
        environment["campaign_id"],
        environment,
        parent_sessions=parent_sessions,
    )
    return {
        "schema_version": 1,
        "artifact_type": "semantic-base-environment-gate",
        "campaign_id": environment["campaign_id"],
        "verdict": "blocked" if pending else "ready-for-isabelle",
        "checker_manifest": checker,
        "pending_framework_reviews": pending,
        "required_isabelle_checks": [
            "fixed declaration shells elaborate",
            "contract shells elaborate",
            "exported facts contain no human-written Refine theorem",
            "stock Refine heap is absent",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m1", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--parent-session", action="append", required=True)
    args = parser.parse_args()
    environment = read_json(args.m1 / "environment-manifest.json")
    packages = [
        read_json(path)
        for path in sorted((args.m1 / "public" / "theory-packages").glob("*.json"))
    ]
    result = build_m2(
        environment, packages, parent_sessions=args.parent_session,
    )
    write_json(args.output, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["verdict"] == "ready-for-isabelle" else 2


if __name__ == "__main__":
    raise SystemExit(main())
