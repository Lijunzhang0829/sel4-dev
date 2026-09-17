#!/usr/bin/env python3
"""Functional entry point for repo-level environment preparation and sealing."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from regen.preparation.contract_command_inventory import build_inventory, write_inventory
from regen.preparation.environment_gate import (
    build_environment_gate,
    build_semantic_base_seal,
    validate_environment_gate,
    write_environment_gate,
    write_semantic_base_seal,
)
from regen.preparation.refine_campaign import write_immutable_json
from regen.preparation.semantic_base_builder import (
    initial_retained_command_ids,
    materialize_workspace,
    run_isabelle_build,
)


def materialize_sanitized_l4v(
    source: Path, destination: Path, manifest_path: Path,
    generated_overlay: Path | None = None,
) -> dict:
    """Copy l4v while removing raw target Refine theory sources."""
    source = source.resolve()
    destination = destination.resolve()
    if destination.exists():
        raise ValueError(f"refusing to overwrite sanitized l4v runtime: {destination}")
    shutil.copytree(
        source, destination, symlinks=True,
        ignore=shutil.ignore_patterns(
            ".git", "heaps", "browser_info", "build",
        ),
    )
    overlay_files = []
    if generated_overlay is not None:
        overlay = generated_overlay.resolve()
        for subtree in (
            "spec/design", "spec/machine", "spec/abstract/document",
        ):
            for path in sorted(
                item for item in (overlay / subtree).rglob("*") if item.is_file()
            ):
                relative = path.relative_to(overlay)
                target = destination / relative
                if target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                overlay_files.append({
                    "path": str(relative),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                })
    refine = destination / "proof" / "refine"
    removed = []
    for child in sorted(refine.iterdir()):
        if child.name in {"base", "README.md"}:
            continue
        removed.append(child.name)
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    leaked = [
        str(path.relative_to(destination))
        for path in refine.rglob("*.thy")
        if path.relative_to(refine).parts[0] != "base"
    ]
    if leaked:
        raise ValueError(f"raw Refine theories remain in sanitized runtime: {leaked[:5]}")
    manifest = {
        "schema_version": 1,
        "artifact_type": "sanitized-l4v-runtime",
        "source": str(source),
        "generated_overlay": (
            str(generated_overlay.resolve()) if generated_overlay else None
        ),
        "generated_overlay_files": overlay_files,
        "destination": str(destination),
        "removed_refine_entries": removed,
        "retained_base_refine_theories": sorted(
            str(path.relative_to(destination)) for path in (refine / "base").rglob("*.thy")
        ),
        "raw_target_refine_theory_count": 0,
    }
    write_immutable_json(manifest_path, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare", help="freeze and run the checkpointed environment pipeline")
    prepare_parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    prepare_parser.add_argument("--config", type=Path, default=Path("regen/config/refine-semantic-base-campaign.json"))
    prepare_parser.add_argument("--environment-root", type=Path, required=True)
    prepare_parser.add_argument("--generated-overlay", type=Path)
    prepare_parser.add_argument("--image", default="sel4-public:latest")
    prepare_parser.add_argument("--timeout", type=int, default=2400)
    prepare_parser.add_argument("--upstream-heaps", type=Path, help="allowlisted parent heaps and build databases")
    from regen.preparation.environment_pipeline import STAGES
    prepare_parser.add_argument("--until", choices=STAGES, default="dataflow")
    resume_parser = subparsers.add_parser("resume", help="verify/restore completed outputs and continue")
    resume_parser.add_argument("--environment-root", type=Path, required=True)
    resume_parser.add_argument("--until", choices=STAGES, default="dataflow")
    restore_parser = subparsers.add_parser("restore", help="restore verified checkpoints without model or prover calls")
    restore_parser.add_argument("--environment-root", type=Path, required=True)
    dataflow_parser = subparsers.add_parser("verify-dataflow", help="verify the downstream streams against the gate")
    dataflow_parser.add_argument("--environment-root", type=Path, required=True)

    runtime_parser = subparsers.add_parser("runtime")
    runtime_parser.add_argument("--source-l4v", type=Path, required=True)
    runtime_parser.add_argument("--destination", type=Path, required=True)
    runtime_parser.add_argument("--manifest", type=Path, required=True)
    runtime_parser.add_argument("--generated-overlay", type=Path)

    inventory_parser = subparsers.add_parser("inventory")
    inventory_parser.add_argument("--repo", type=Path, required=True)
    inventory_parser.add_argument("--campaign-id", required=True)
    inventory_parser.add_argument("--output", type=Path, required=True)
    inventory_parser.add_argument("--architecture", default="ARM")

    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--inventory", type=Path, required=True)
    build_parser.add_argument("--workspace", type=Path, required=True)
    build_parser.add_argument("--retained", type=Path)
    build_parser.add_argument("--l4v", type=Path, required=True)
    build_parser.add_argument("--heaps", type=Path, required=True)
    build_parser.add_argument("--timeout", type=int, default=2400)
    build_parser.add_argument("--result", type=Path)

    seal_parser = subparsers.add_parser("seal")
    seal_parser.add_argument("--environment-root", type=Path, required=True)
    seal_parser.add_argument("--campaign-manifest", type=Path, required=True)
    seal_parser.add_argument("--inventory", type=Path, required=True)
    seal_parser.add_argument("--necessity-ledger", type=Path, required=True)
    seal_parser.add_argument("--build-result", type=Path, required=True)
    seal_parser.add_argument("--workspace", type=Path, required=True)
    seal_parser.add_argument("--heaps", type=Path, required=True)
    seal_parser.add_argument("--l4v", type=Path, required=True)
    seal_parser.add_argument("--l4v-manifest", type=Path, required=True)
    seal_parser.add_argument("--output", type=Path, required=True)

    gate_parser = subparsers.add_parser("gate")
    gate_parser.add_argument("--environment-root", type=Path, required=True)
    gate_parser.add_argument("--semantic-base-seal", type=Path, required=True)
    gate_parser.add_argument(
        "--context-effect-disclosure", type=Path, required=True,
    )
    gate_parser.add_argument("--contract-packages", type=Path, required=True)
    gate_parser.add_argument("--premise-snapshot", type=Path, required=True)
    gate_parser.add_argument("--premise-database", type=Path, required=True)
    gate_parser.add_argument("--output", type=Path, required=True)

    verify_parser = subparsers.add_parser("verify-gate")
    verify_parser.add_argument("--environment-root", type=Path, required=True)
    verify_parser.add_argument("--gate", type=Path, required=True)
    args = parser.parse_args()

    if args.command in {"prepare", "resume", "restore", "verify-dataflow"}:
        from regen.preparation.environment_pipeline import GATE, INDEX, initialize, restore, run
        from regen.preparation.environment_dataflow import validate_dataflow
        try:
            if args.command == "prepare":
                initialize(args.environment_root, args.repo, args.config,
                           overlay=args.generated_overlay, image=args.image, timeout=args.timeout,
                           upstream_cache=args.upstream_heaps)
            if args.command in {"prepare", "resume"}:
                result = run(args.environment_root, until=args.until)
            elif args.command == "restore":
                result = restore(args.environment_root)
            else:
                root = args.environment_root.resolve()
                result = validate_dataflow(root, root / GATE, root / INDEX)
        except (ValueError, OSError) as error:
            print(json.dumps({"status": "failed", "command": args.command,
                              "error_type": type(error).__name__, "diagnostic": str(error)}), file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "runtime":
        manifest = materialize_sanitized_l4v(
            args.source_l4v, args.destination, args.manifest,
            args.generated_overlay,
        )
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0

    if args.command == "inventory":
        artifacts = build_inventory(
            args.repo, args.campaign_id, architecture=args.architecture,
        )
        write_inventory(args.output, artifacts)
        print(json.dumps(artifacts["inventory"]["counts"], indent=2, sort_keys=True))
        return 0

    if args.command == "build":
        inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
        retained = initial_retained_command_ids(inventory)
        if args.retained:
            retained = set(json.loads(
                args.retained.read_text(encoding="utf-8"),
            )["retained_command_ids"])
        workspace = materialize_workspace(inventory, args.workspace, retained)
        isabelle = run_isabelle_build(
            args.workspace, args.l4v, args.heaps, timeout_seconds=args.timeout,
        )
        result = {"workspace": workspace, "isabelle": isabelle}
        if args.result:
            write_immutable_json(args.result, result)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if isabelle["verdict"] == "pass" else 2

    if args.command == "seal":
        seal = build_semantic_base_seal(
            args.environment_root, args.campaign_manifest, args.inventory,
            args.necessity_ledger, args.build_result, args.workspace, args.heaps,
            args.l4v, args.l4v_manifest,
        )
        write_semantic_base_seal(args.output, seal)
        print(json.dumps({
            "seal_id": seal["seal_id"],
            "verdict": seal["verdict"],
            "next_required_artifact": "completed context-effect-disclosure",
        }, indent=2, sort_keys=True))
        return 0

    if args.command == "gate":
        gate = build_environment_gate(
            args.environment_root, args.semantic_base_seal,
            args.context_effect_disclosure, args.contract_packages,
            args.premise_snapshot, args.premise_database,
        )
        write_environment_gate(args.output, gate)
        print(json.dumps({
            "gate_id": gate["gate_id"],
            "contracts": gate["contract_packages"]["contract_count"],
            "premises": gate["premise_catalog"]["record_count"],
        }, indent=2, sort_keys=True))
        return 0

    gate = validate_environment_gate(args.environment_root, args.gate)
    print(f"verified {gate['gate_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
