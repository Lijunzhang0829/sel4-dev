#!/usr/bin/env python3
"""Create and validate the checker assets of an independent Refine dataset."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from regen.infrastructure.path_identity import source_identity
from regen.preparation.refine_campaign import sha256_json, write_immutable_json


ASSETS = ("l4v", "semantic_workspace", "heaps", "isabelle")


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _asset_link(root: Path, name: str, source: Path) -> str:
    source = source.resolve()
    if not source.is_dir():
        raise ValueError(f"checker asset is not a directory: {source}")
    relative = Path("checker-assets") / name
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        if destination.resolve() != source:
            raise ValueError(f"checker asset link changed: {destination}")
    elif destination.exists():
        raise ValueError(f"checker asset path already exists: {destination}")
    else:
        destination.symlink_to(source, target_is_directory=True)
    return str(relative)


def create_context(
    dataset_root: Path, *, l4v: Path, semantic_workspace: Path,
    heaps: Path, isabelle: Path, image: str,
    base_session: str = "Semantic_Base",
    base_import: str = "Semantic_Base.Semantic_Root",
    base_catalog: str = "/semantic-base",
    architecture: str = "ARM",
    budgets: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Bind checker assets under the dataset without copying large immutable trees."""
    dataset_root = dataset_root.resolve()
    dataset_root.mkdir(parents=True, exist_ok=True)
    database = dataset_root / "parent-premises.sqlite"
    planning_input = dataset_root / "repository-planning-input.json"
    if not database.is_file() or not planning_input.is_file():
        raise ValueError("checker context requires a prepared repository planning input")
    paths = {
        "l4v": _asset_link(dataset_root, "l4v", l4v),
        "semantic_workspace": _asset_link(
            dataset_root, "semantic-base", semantic_workspace,
        ),
        "heaps": _asset_link(dataset_root, "heaps", heaps),
        "isabelle": _asset_link(dataset_root, "isabelle", isabelle),
    }
    refine_root = dataset_root / paths["l4v"] / "proof/refine"
    target_sources = [
        path for path in refine_root.rglob("*.thy")
        if "base" not in path.relative_to(refine_root).parts
    ]
    if target_sources:
        raise ValueError("checker l4v still contains target Refine theories")
    audit_context = {
        "schema_version": 1,
        "artifact_type": "refine-parent-premise-audit-context",
        "campaign_id": "independent-refine",
        "semantic_base_seal_id": "dataset:" + sha256_json({
            "planning_input": _read(planning_input)["planning_input_id"],
            "base_session": base_session,
            "base_import": base_import,
        })[:20],
        "records": [],
    }
    write_immutable_json(dataset_root / "premise-audit-context.json", audit_context)
    core = {
        "schema_version": 1,
        "artifact_type": "refine-checker-context",
        "target_session": "Refine",
        "architecture": architecture,
        "paths": paths,
        "premise_database": "parent-premises.sqlite",
        "premise_audit_context": "premise-audit-context.json",
        "base_session": base_session,
        "base_import": base_import,
        "base_catalog": base_catalog,
        "image": image,
        "isabelle_sha256": source_identity(
            (dataset_root / paths["isabelle"]).resolve(), allow_links=True,
        ),
        "checker_policy": "fixed-prefix-cold-green-v1",
        "budgets": budgets or {
            "graph_revisions_per_generation": 16,
            "model_call_timeout_seconds": 1800,
            "whole_proof_attempts_per_logical_round": 5,
        },
    }
    from regen.preparation.premise_database import verify_database, digest
    if verify_database(database) == 2:
        core["premise_database_sha256"] = digest(database)
    context = {
        **core,
        "checker_context_id": "checker-context:" + sha256_json(core)[:20],
    }
    write_immutable_json(dataset_root / "checker-context.json", context)
    return context


def load_context(dataset_root: Path) -> dict[str, Any]:
    dataset_root = dataset_root.resolve()
    context = _read(dataset_root / "checker-context.json")
    if (
        context.get("artifact_type") != "refine-checker-context"
        or context.get("target_session") != "Refine"
        or "environment_gate_id" in context
        or "environment_run" in context
    ):
        raise ValueError("invalid independent Refine checker context")
    core = {key: value for key, value in context.items()
            if key != "checker_context_id"}
    if context.get("checker_context_id") != "checker-context:" + sha256_json(core)[:20]:
        raise ValueError("checker context identity mismatch")
    resolved = {}
    for name in ASSETS:
        path = (dataset_root / context["paths"][name]).resolve()
        if not path.is_dir():
            raise ValueError(f"checker asset is missing: {name}")
        resolved[name] = path
    database = (dataset_root / context["premise_database"]).resolve()
    audit = (dataset_root / context["premise_audit_context"]).resolve()
    if not database.is_file() or not audit.is_file():
        raise ValueError("checker premise assets are missing")
    from regen.preparation.premise_database import verify_database, digest
    if verify_database(database) == 2 and context.get("premise_database_sha256") != digest(database):
        raise ValueError("checker premise database binding mismatch")
    if source_identity(resolved["isabelle"], allow_links=True) != context["isabelle_sha256"]:
        raise ValueError("checker Isabelle installation changed")
    return {
        **context,
        **resolved,
        "dataset_root": dataset_root,
        "premise_database_path": database,
        "premise_audit_context_path": audit,
    }


def migrate_legacy(environment_root: Path, dataset_root: Path) -> dict[str, Any]:
    """One-time adapter; the resulting runtime interface contains no environment ID."""
    environment_root = environment_root.resolve()
    gate = _read(environment_root / "Environment-Preparation/environment-gate.json")
    recipe = _read(environment_root / ".preparation/recipe.json")
    toolchain = _read(environment_root / "Toolchain/identity.json")
    runtime = gate["runtime"]
    return create_context(
        dataset_root,
        l4v=environment_root / runtime["l4v_path"],
        semantic_workspace=environment_root / runtime["workspace_path"],
        heaps=environment_root / runtime["heaps_path"],
        isabelle=Path(recipe["repo"]) / "verification/isabelle",
        image=toolchain["image"]["image_id"],
        base_session=runtime["base_session"],
        base_import=runtime["base_import"],
        base_catalog=runtime["base_catalog"],
        architecture=_read(dataset_root / "repository-planning-input.json")[
            "benchmark"
        ]["architecture"],
        budgets=recipe["manifest"]["budgets"],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--migrate-legacy-environment", type=Path, required=True)
    args = parser.parse_args()
    value = migrate_legacy(args.migrate_legacy_environment, args.dataset_root)
    print(json.dumps({
        "verdict": "pass",
        "checker_context_id": value["checker_context_id"],
        "dataset_root": str(args.dataset_root.resolve()),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
