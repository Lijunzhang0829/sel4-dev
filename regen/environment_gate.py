#!/usr/bin/env python3
"""Create and verify the canonical environment seal consumed by downstream modules."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from regen.refine_campaign import write_immutable_json


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def artifact_ref(root: Path, path: Path) -> dict[str, Any]:
    root = root.resolve()
    path = path.resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"canonical artifact is outside environment root: {path}") from error
    if not path.is_file():
        raise ValueError(f"canonical artifact is not a file: {path}")
    return {
        "path": str(relative),
        "sha256": sha256_path(path),
        "bytes": path.stat().st_size,
    }


def directory_identity(root: Path, directory: Path) -> dict[str, Any]:
    root = root.resolve()
    directory = directory.resolve()
    try:
        relative = directory.relative_to(root)
    except ValueError as error:
        raise ValueError(f"canonical directory is outside environment root: {directory}") from error
    if not directory.is_dir():
        raise ValueError(f"canonical directory is missing: {directory}")
    files = []
    for path in sorted(item for item in directory.rglob("*") if item.is_file()):
        files.append({
            "path": str(path.relative_to(directory)),
            "sha256": sha256_path(path),
            "bytes": path.stat().st_size,
        })
    return {
        "path": str(relative),
        "file_count": len(files),
        "payload_sha256": payload_sha256(files),
        "files": files,
    }


def resolve_ref(root: Path, reference: dict[str, Any]) -> Path:
    path = (root / reference["path"]).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"artifact reference escapes environment root: {path}") from error
    if not path.is_file():
        raise ValueError(f"referenced artifact is missing: {path}")
    if sha256_path(path) != reference["sha256"]:
        raise ValueError(f"referenced artifact hash mismatch: {path}")
    return path


def _build_parts(build_result: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if "isabelle" in build_result:
        return build_result["workspace"], build_result["isabelle"]
    raise ValueError("build result must bind workspace and Isabelle result")


def _check_workspace_files(workspace: Path, manifest: dict[str, Any]) -> None:
    for theory in manifest.get("theories", []):
        path = workspace / theory["workspace_path"]
        if not path.is_file() or sha256_path(path) != theory["sha256"]:
            raise ValueError(f"Semantic Base workspace drift: {path}")
    for required in ("ROOT", "Semantic_Root.thy", "build.log"):
        if not (workspace / required).is_file():
            raise ValueError(f"Semantic Base workspace is missing {required}")


def raw_target_refine_theories(l4v: Path) -> list[str]:
    refine = l4v.resolve() / "proof" / "refine"
    if not refine.is_dir():
        raise ValueError(f"sanitized runtime lacks proof/refine: {refine}")
    leaked = []
    for path in sorted(refine.rglob("*.thy")):
        relative = path.relative_to(refine)
        if not relative.parts or relative.parts[0] == "base":
            continue
        leaked.append(str(path.relative_to(l4v.resolve())))
    return leaked


def _verify_payload_identity(value: dict[str, Any], id_field: str, prefix: str) -> None:
    stored = value.get(id_field)
    core = dict(value)
    core.pop(id_field, None)
    expected = f"{prefix}{payload_sha256(core)[:20]}"
    if stored != expected:
        raise ValueError(f"{id_field} does not match the artifact payload")


def validate_semantic_base_seal(
    environment_root: Path, seal_path: Path,
) -> dict[str, Any]:
    root = environment_root.resolve()
    seal = read_json(seal_path)
    if seal.get("artifact_type") != "semantic-base-seal" or seal.get("verdict") != "pass":
        raise ValueError("invalid Semantic Base seal")
    _verify_payload_identity(seal, "seal_id", "semantic-base:")

    campaign = read_json(resolve_ref(root, seal["campaign_manifest"]))
    inventory = read_json(resolve_ref(root, seal["command_inventory"]))
    ledger = read_json(resolve_ref(root, seal["command_necessity_ledger"]))
    build_result = read_json(resolve_ref(root, seal["semantic_base_build"]))
    l4v_manifest = read_json(resolve_ref(root, seal["l4v_runtime_manifest"]))
    campaign_id = seal["campaign_id"]
    if any(item.get("campaign_id") != campaign_id for item in (
        campaign, inventory, ledger,
    )):
        raise ValueError("Semantic Base seal inputs belong to different campaigns")
    workspace_manifest, isabelle = _build_parts(build_result)
    if workspace_manifest.get("campaign_id") != campaign_id:
        raise ValueError("Semantic Base workspace belongs to another campaign")
    if (
        seal["checker"].get("base_session") != workspace_manifest.get("session")
        or seal["checker"].get("base_import") != workspace_manifest.get("base_import")
        or seal["checker"].get("base_session") != isabelle.get("session")
    ):
        raise ValueError("Semantic Base checker identity is inconsistent")

    l4v = (root / seal["l4v"]["path"]).resolve()
    if Path(l4v_manifest.get("destination", "")).resolve() != l4v:
        raise ValueError("sanitized runtime manifest binds another l4v directory")
    leaked = raw_target_refine_theories(l4v)
    if leaked:
        raise ValueError(f"sanitized runtime contains target Refine theories: {leaked[:5]}")
    if l4v_manifest.get("raw_target_refine_theory_count") != len(leaked):
        raise ValueError("sanitized runtime manifest leak count is inconsistent")
    return seal


def validate_context_effect_disclosure(
    environment_root: Path, disclosure_path: Path, *,
    seal: dict[str, Any], snapshot: dict[str, Any],
) -> dict[str, Any]:
    root = environment_root.resolve()
    disclosure = read_json(disclosure_path)
    if (
        disclosure.get("artifact_type") != "context-effect-disclosure"
        or disclosure.get("status") != "complete"
        or disclosure.get("gate_requirement_satisfied") is not True
    ):
        raise ValueError("Context Effect Disclosure is not complete")
    if (
        disclosure.get("campaign_id") != seal.get("campaign_id")
        or disclosure.get("semantic_base_seal_id") != seal.get("seal_id")
    ):
        raise ValueError("Context Effect Disclosure binds another environment")
    required = {
        "facts", "simp", "wp", "cong", "weak_cong",
        "classical_intro_elim", "named_collections",
    }
    coverage = disclosure.get("coverage", {})
    missing = sorted(name for name in required if coverage.get(name) is not True)
    if missing:
        raise ValueError(f"Context Effect Disclosure lacks coverage: {missing}")
    records_hash = payload_sha256(snapshot.get("records", []))
    if disclosure.get("premise_records_payload_sha256") != records_hash:
        raise ValueError("Context Effect Disclosure and Premise Catalog differ")
    records = snapshot.get("records", [])
    if disclosure.get("exported_fact_count") != len(records):
        raise ValueError("Context Effect Disclosure fact count differs")
    raw_export = disclosure.get("raw_export", {})
    if any(not raw_export.get(name) for name in (
        "facts_sha256", "automation_pools_sha256", "context_metadata_sha256",
    )):
        raise ValueError("Context Effect Disclosure lacks raw export identities")
    retained = sum(
        item.get("source_class") == "retained_semantic_export"
        for item in records
    )
    if disclosure.get("retained_exported_fact_count") != retained:
        raise ValueError("Context Effect Disclosure retained-fact count differs")
    return disclosure


def build_semantic_base_seal(
    environment_root: Path,
    campaign_manifest_path: Path,
    inventory_path: Path,
    ledger_path: Path,
    build_result_path: Path,
    workspace: Path,
    heaps: Path,
    l4v: Path,
    l4v_manifest_path: Path,
) -> dict[str, Any]:
    root = environment_root.resolve()
    campaign = read_json(campaign_manifest_path)
    inventory = read_json(inventory_path)
    ledger = read_json(ledger_path)
    build_result = read_json(build_result_path)
    workspace_manifest, isabelle = _build_parts(build_result)

    campaign_id = campaign.get("campaign_id")
    if not campaign_id or inventory.get("campaign_id") != campaign_id:
        raise ValueError("campaign manifest and command inventory do not match")
    if inventory.get("artifact_type") != "refine-contract-command-inventory":
        raise ValueError("invalid contract-command inventory")
    if ledger.get("artifact_type") != "command-necessity-ledger":
        raise ValueError("invalid command-necessity ledger")
    if ledger.get("campaign_id") != campaign_id:
        raise ValueError("command-necessity ledger belongs to another campaign")
    if ledger.get("verdict") != "pass" or not ledger.get("selection_complete"):
        raise ValueError("command selection has not passed the production gate")
    if ledger.get("unresolved_command_ids"):
        raise ValueError("command-necessity ledger contains unresolved commands")
    if isabelle.get("artifact_type") != "semantic-base-isabelle-build":
        raise ValueError("invalid Isabelle build result")
    if isabelle.get("verdict") != "pass":
        raise ValueError("Semantic Base Isabelle build is not PASS")
    if workspace_manifest.get("campaign_id") != campaign_id:
        raise ValueError("workspace manifest belongs to another campaign")
    if workspace_manifest.get("contract_shell_count") != inventory["counts"]["contracts"]:
        raise ValueError("contract-shell coverage differs from the inventory")
    if workspace_manifest.get("retained_command_count") != len(
        ledger["retained_command_ids"]
    ):
        raise ValueError("workspace retained-command count differs from the ledger")
    if len(workspace_manifest.get("contract_shell_theories", [])) != len(
        [item for item in inventory["theories"] if item["contracts"]]
    ):
        raise ValueError("contract-shell owner-theory coverage is incomplete")
    _check_workspace_files(workspace.resolve(), workspace_manifest)

    build_log = workspace.resolve() / isabelle["build_log"]["path"]
    if sha256_path(build_log) != isabelle["build_log"]["sha256"]:
        raise ValueError("full Semantic Base build log does not match build result")
    heap_identity = directory_identity(root, heaps)
    if heap_identity["file_count"] == 0:
        raise ValueError("Semantic Base heap directory is empty")
    l4v_identity = directory_identity(root, l4v)
    l4v_manifest = read_json(l4v_manifest_path)
    if l4v_manifest.get("artifact_type") != "sanitized-l4v-runtime":
        raise ValueError("invalid sanitized l4v runtime manifest")
    leaked_refine_theories = raw_target_refine_theories(l4v)
    if leaked_refine_theories:
        raise ValueError(
            "sanitized l4v runtime still contains target Refine theories: "
            + ", ".join(leaked_refine_theories[:5])
        )
    if l4v_manifest.get("raw_target_refine_theory_count") != len(
        leaked_refine_theories
    ):
        raise ValueError("sanitized runtime manifest leak count is inconsistent")
    if Path(l4v_manifest.get("destination", "")).resolve() != l4v.resolve():
        raise ValueError("sanitized l4v runtime manifest binds another directory")
    for record in l4v_manifest.get("generated_overlay_files", []):
        path = l4v.resolve() / record["path"]
        if not path.is_file() or sha256_path(path) != record["sha256"]:
            raise ValueError(f"generated specification overlay drift: {path}")

    core = {
        "schema_version": 1,
        "artifact_type": "semantic-base-seal",
        "verdict": "pass",
        "campaign_id": campaign_id,
        "campaign_manifest": artifact_ref(root, campaign_manifest_path),
        "command_inventory": artifact_ref(root, inventory_path),
        "command_inventory_counts": inventory["counts"],
        "command_necessity_ledger": artifact_ref(root, ledger_path),
        "necessity": {
            "selection_complete": True,
            "fixed_point_complete": bool(ledger.get("fixed_point_complete")),
            "command_minimality_claimed": bool(
                ledger.get("command_minimality_claimed", False)
            ),
            "retained_command_count": len(ledger["retained_command_ids"]),
            "removed_command_count": len(ledger.get("removed_command_ids", [])),
            "unresolved_command_count": 0,
            "pass_count": len(ledger.get("passes", [])),
        },
        "semantic_base_build": artifact_ref(root, build_result_path),
        "workspace": directory_identity(root, workspace),
        "heap": heap_identity,
        "l4v": l4v_identity,
        "l4v_runtime_manifest": artifact_ref(root, l4v_manifest_path),
        "checker": {
            "base_session": workspace_manifest["session"],
            "base_import": workspace_manifest["base_import"],
            "contract_shell_count": workspace_manifest["contract_shell_count"],
            "contract_owner_theory_count": len(
                workspace_manifest["contract_shell_theories"]
            ),
            "container_image": isabelle.get("container_image"),
            "effective_command": isabelle.get("effective_command"),
        },
    }
    return {**core, "seal_id": f"semantic-base:{payload_sha256(core)[:20]}"}


def _premise_database_count(path: Path) -> int:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM premises").fetchone()[0])
    finally:
        connection.close()


def build_environment_gate(
    environment_root: Path,
    semantic_base_seal_path: Path,
    context_effect_disclosure_path: Path,
    contract_package_dir: Path,
    premise_snapshot_path: Path,
    premise_database_path: Path,
) -> dict[str, Any]:
    root = environment_root.resolve()
    seal = validate_semantic_base_seal(root, semantic_base_seal_path)
    snapshot = read_json(premise_snapshot_path)
    if snapshot.get("artifact_type") != "semantic-base-premise-snapshot":
        raise ValueError("invalid Premise Catalog snapshot")
    if snapshot.get("campaign_id") != seal.get("campaign_id"):
        raise ValueError("Premise Catalog and Semantic Base seal belong to different campaigns")
    from regen.premise_catalog import verify_catalog
    catalog_check = verify_catalog(
        semantic_base_seal_path, premise_snapshot_path, premise_database_path,
    )
    disclosure = validate_context_effect_disclosure(
        root, context_effect_disclosure_path, seal=seal, snapshot=snapshot,
    )
    package_dir = contract_package_dir.resolve()
    try:
        package_relative = package_dir.relative_to(root)
    except ValueError as error:
        raise ValueError("contract package directory is outside environment root") from error
    packages = [read_json(path) for path in sorted(package_dir.glob("*.json"))]
    contract_count = sum(len(item.get("contracts", [])) for item in packages)
    expected_contracts = seal["command_inventory_counts"]["contracts"]
    if contract_count != expected_contracts:
        raise ValueError(
            f"contract package coverage mismatch: {contract_count} != {expected_contracts}"
        )
    package_identity = directory_identity(root, package_dir)
    record_count = len(snapshot.get("records", []))
    database_count = _premise_database_count(premise_database_path)
    if record_count != database_count:
        raise ValueError("Premise Catalog snapshot and database record counts differ")

    core = {
        "schema_version": 1,
        "artifact_type": "repo-level-environment-gate",
        "verdict": "pass",
        "campaign_id": seal["campaign_id"],
        "semantic_base_seal": artifact_ref(root, semantic_base_seal_path),
        "semantic_base_seal_id": seal["seal_id"],
        "context_effect_disclosure": artifact_ref(
            root, context_effect_disclosure_path,
        ),
        "context_effect_disclosure_status": disclosure["status"],
        "contract_packages": {
            **package_identity,
            "package_count": len(packages),
            "contract_count": contract_count,
        },
        "premise_catalog": {
            "snapshot": artifact_ref(root, premise_snapshot_path),
            "database": artifact_ref(root, premise_database_path),
            "record_count": record_count,
            "snapshot_id": catalog_check["snapshot_id"],
        },
        "runtime": {
            "workspace_path": seal["workspace"]["path"],
            "heaps_path": seal["heap"]["path"],
            "l4v_path": seal["l4v"]["path"],
            "base_session": seal["checker"]["base_session"],
            "base_import": seal["checker"]["base_import"],
            "base_catalog": "/semantic-base",
        },
        "proof_generation_allowed": True,
    }
    return {**core, "gate_id": f"environment:{payload_sha256(core)[:20]}"}


def validate_environment_gate(
    environment_root: Path, gate_path: Path,
) -> dict[str, Any]:
    root = environment_root.resolve()
    gate = read_json(gate_path)
    if gate.get("artifact_type") != "repo-level-environment-gate":
        raise ValueError("invalid environment gate artifact")
    _verify_payload_identity(gate, "gate_id", "environment:")
    if gate.get("verdict") != "pass" or not gate.get("proof_generation_allowed"):
        raise ValueError("environment gate does not allow proof generation")
    seal_path = resolve_ref(root, gate["semantic_base_seal"])
    seal = validate_semantic_base_seal(root, seal_path)
    if seal.get("seal_id") != gate.get("semantic_base_seal_id"):
        raise ValueError("environment gate binds the wrong Semantic Base seal")
    snapshot = resolve_ref(root, gate["premise_catalog"]["snapshot"])
    database = resolve_ref(root, gate["premise_catalog"]["database"])
    from regen.premise_catalog import verify_catalog
    catalog_check = verify_catalog(seal_path, snapshot, database)
    disclosure_ref = gate.get("context_effect_disclosure")
    if not isinstance(disclosure_ref, dict):
        raise ValueError("environment gate does not bind a completed Context Effect Disclosure")
    disclosure_path = resolve_ref(root, disclosure_ref)
    disclosure = validate_context_effect_disclosure(
        root, disclosure_path, seal=seal, snapshot=read_json(snapshot),
    )
    if (
        gate.get("context_effect_disclosure_status") != "complete"
        or disclosure.get("status") != "complete"
    ):
        raise ValueError("environment gate disclosure status is inconsistent")
    if (
        catalog_check["record_count"] != gate["premise_catalog"]["record_count"]
        or catalog_check["snapshot_id"] != gate["premise_catalog"]["snapshot_id"]
    ):
        raise ValueError("Premise Catalog identity changed after gate creation")
    package_directory = (root / gate["contract_packages"]["path"]).resolve()
    package_identity = directory_identity(root, package_directory)
    if package_identity["payload_sha256"] != gate["contract_packages"]["payload_sha256"]:
        raise ValueError("contract packages changed after gate creation")
    expected_runtime = {
        "base_session": seal["checker"]["base_session"],
        "base_import": seal["checker"]["base_import"],
        "base_catalog": "/semantic-base",
    }
    for key, expected in expected_runtime.items():
        if gate["runtime"].get(key) != expected:
            raise ValueError(f"environment gate runtime identity differs: {key}")
    runtime_identities = {
        "workspace_path": seal["workspace"],
        "heaps_path": seal["heap"],
        "l4v_path": seal["l4v"],
    }
    for key, expected in runtime_identities.items():
        path = (root / gate["runtime"][key]).resolve()
        current = directory_identity(root, path)
        if current["payload_sha256"] != expected["payload_sha256"]:
            raise ValueError(f"environment runtime directory changed: {path}")
    return gate


def write_semantic_base_seal(
    output: Path, seal: dict[str, Any],
) -> None:
    write_immutable_json(output, seal)


def write_environment_gate(output: Path, gate: dict[str, Any]) -> None:
    write_immutable_json(output, gate)
