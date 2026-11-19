#!/usr/bin/env python3
"""Refine independent dataset: contract validators and materialization.

The frozen Refine environment produced by ``environment_preparation`` is
repackaged into the same canonical dataset shape used by the intervaltree and
c-algorithms adapters.  This module provides the owner-blind contract
validators and the trusted ``prepare`` step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from regen.preparation.environment_dataflow import validate_dataflow
from regen.preparation.environment_gate import (
    resolve_ref,
    validate_environment_gate,
)
from regen.preparation.premise_database import SCHEMA, digest, verify_database
from regen.preparation.refine_campaign import sha256_json, write_immutable_json


SCHEMA_VERSION = 1
ARTIFACT_TYPE = "repository-planning-input"
RECEIPT_TYPE = "refine-dataset-preparation-receipt"
CONTRACT_VISIBILITY = "owner-blind-v1"
TARGET_SESSION = "Refine"
PLANNING_INPUT_PREFIX = "owner-blind-input:"
QUERY_INTERFACE = "proof-state-recall-v2"
CONTEXT_PROFILE = "context:Refine:parents"

FORBIDDEN_PAYLOAD_KEYS = {
    "proof",
    "proof_body",
    "human_proof",
    "original_proof",
    "source_location",
    "trusted_source_path",
}

PREPARATION_REQUIRED_FIELDS = {
    "schema_version",
    "artifact_type",
    "status",
    "target_session",
    "architecture",
    "contract_visibility",
    "target_count",
    "source_environment_gate_id",
    "source_dataflow_id",
    "same_fixed_statements",
    "target_proofs_added",
    "parent_facts",
    "quarantined_facts",
    "premise_database_sha256",
    "planning_input_id",
    "checker_context_id",
    "synthetic_owner_is_original_mapping",
    "original_owner_groups_and_imports_removed",
    "excluded",
}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _assert_no_forbidden_keys(value: Any, path: str = "root") -> None:
    if isinstance(value, dict):
        forbidden = FORBIDDEN_PAYLOAD_KEYS.intersection(value)
        if forbidden:
            raise ValueError(f"forbidden proof/source fields at {path}: {sorted(forbidden)}")
        for key, item in value.items():
            _assert_no_forbidden_keys(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_forbidden_keys(item, f"{path}[{index}]")


def validate_planning_input(path: Path) -> dict[str, Any]:
    """Validate the owner-blind canonical Refine planning input."""
    payload = _read_json(path)
    if payload.get("artifact_type") != ARTIFACT_TYPE or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported planning input type or schema")
    if payload.get("contract_visibility") != CONTRACT_VISIBILITY:
        raise ValueError("planning input must be owner-blind-v1")
    for key in ("owner_groups", "session_graph", "theory_import_graph"):
        if payload.get(key) != {}:
            raise ValueError(f"{key} must be empty in the anonymous canonical dataset")
    contracts = payload.get("contracts")
    if not isinstance(contracts, list) or not contracts:
        raise ValueError("contracts must be a nonempty list")
    ids = [c.get("contract_id") for c in contracts]
    if len(ids) != len(set(ids)) or any(not isinstance(i, str) or not i.startswith("goal:") for i in ids):
        raise ValueError("contract ids must be unique goal:<digest> aliases")
    for index, contract in enumerate(contracts, 1):
        if "owner_theory" in contract or "owner_file_slot" in contract:
            raise ValueError(f"contract {index} must not carry owner attribution")
    cards = payload.get("contract_cards")
    if not isinstance(cards, list):
        raise ValueError("contract_cards must be a list")
    for card in cards:
        if set(card) != {"contract_id", "name"}:
            raise ValueError("contract_cards may only contain contract_id and name")
    if {c["contract_id"] for c in cards} != set(ids):
        raise ValueError("contract_cards do not match contracts")
    parent = payload.get("parent_premises")
    if not isinstance(parent, dict) or parent.get("query_interface") != QUERY_INTERFACE:
        raise ValueError("parent_premises must use proof-state-recall-v2")
    for key in ("database", "manifest", "database_sha256", "record_ids_sha256"):
        if not isinstance(parent.get(key), str):
            raise ValueError(f"parent_premises is missing {key}")
    profiles = payload.get("context_profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("context_profiles must be a nonempty list")
    for profile in profiles:
        if not isinstance(profile.get("generated_import"), str) or profile.get("target_session") != TARGET_SESSION:
            raise ValueError("invalid context profile")
    planning_id = payload.get("planning_input_id")
    if not isinstance(planning_id, str) or not planning_id.startswith(PLANNING_INPUT_PREFIX):
        raise ValueError("invalid planning_input_id")
    _assert_no_forbidden_keys(payload)
    return payload


def validate_receipt(path: Path) -> dict[str, Any]:
    """Validate the unified Refine preparation receipt."""
    receipt = _read_json(path)
    missing = PREPARATION_REQUIRED_FIELDS - receipt.keys()
    if missing:
        raise ValueError(f"receipt is missing required fields: {sorted(missing)}")
    if receipt.get("artifact_type") != RECEIPT_TYPE or receipt.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported receipt type or schema")
    if receipt.get("status") != "prepared":
        raise ValueError("receipt status must be prepared")
    if receipt.get("target_session") != TARGET_SESSION:
        raise ValueError("receipt target_session must be Refine")
    if receipt.get("contract_visibility") != CONTRACT_VISIBILITY:
        raise ValueError("receipt must be owner-blind-v1")
    if receipt.get("synthetic_owner_is_original_mapping") is not False:
        raise ValueError("synthetic owner must not encode an original mapping")
    if receipt.get("original_owner_groups_and_imports_removed") is not True:
        raise ValueError("original owner groups and imports must be removed")
    if receipt.get("target_proofs_added") != 0 or receipt.get("same_fixed_statements") is not True:
        raise ValueError("target statements must be fixed and proof-free")
    for key in ("target_count", "parent_facts", "quarantined_facts"):
        value = receipt.get(key)
        if not isinstance(value, int) or value < 0:
            raise ValueError(f"{key} must be a nonnegative integer")
    if receipt.get("target_count") <= 0:
        raise ValueError("target_count must be positive")
    for key in (
        "source_environment_gate_id",
        "source_dataflow_id",
        "premise_database_sha256",
        "planning_input_id",
        "checker_context_id",
    ):
        if not isinstance(receipt.get(key), str) or not receipt[key]:
            raise ValueError(f"{key} must be a nonempty string")
    if not isinstance(receipt.get("excluded"), list):
        raise ValueError("excluded must be a list")
    return receipt


def _parent_row(premise: dict[str, Any]) -> tuple[Any, ...]:
    return (
        premise["record_id"],
        premise["canonical_name"],
        premise["declaration_name"],
        int(premise["fact_index"]),
        premise["exact_statement"],
        "sha256:" + premise["normalized_statement_sha256"],
        premise["owner_theory"],
        premise["owner_session"],
        premise["source_class"],
        json.dumps(sorted(premise["attributes"]), ensure_ascii=False),
    )


def _write_parent_database(path: Path, premises: list[dict[str, Any]]) -> None:
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.executemany(
            "INSERT INTO premises VALUES (?,?,?,?,?,?,?,?,?,?)",
            [_parent_row(item) for item in premises],
        )
        db.execute("INSERT INTO premise_search(premise_search) VALUES ('rebuild')")
        db.commit()


def _source_payload(
    gate: dict[str, Any],
    architecture: str,
    contracts: list[dict[str, Any]],
    premises: list[dict[str, Any]],
    database_sha256: str,
    record_ids_sha256: str,
) -> dict[str, Any]:
    upstream = sorted({item["owner_session"] for item in premises})
    base_import = gate["runtime"]["base_import"]
    contract_keys = (
        "contract_id",
        "name",
        "kind",
        "exact_statement",
        "owner_theory",
        "owner_file_slot",
        "contract_owned_attributes_raw",
        "contract_owned_attributes",
        "contract_owned_context",
    )
    source_contracts = [
        {key: item[key] for key in contract_keys if key in item}
        for item in contracts
    ]
    cards = [
        {"contract_id": item["contract_id"], "name": item["name"], "owner_theory": item["owner_theory"]}
        for item in source_contracts
    ]
    owner_groups = {
        owner: sorted(item["contract_id"] for item in source_contracts if item["owner_theory"] == owner)
        for owner in sorted({item["owner_theory"] for item in source_contracts})
    }
    core = {
        "schema_version": 1,
        "artifact_type": ARTIFACT_TYPE,
        "benchmark": {
            "id": f"sel4-refine-{architecture.lower()}",
            "target_session": TARGET_SESSION,
            "architecture": architecture,
            "independent_experiment": True,
            "cross_experiment_inputs": [],
        },
        "contracts": source_contracts,
        "contract_cards": cards,
        "owner_groups": owner_groups,
        "session_graph": {},
        "theory_import_graph": {},
        "upstream_session_closure": upstream,
        "contract_relation_candidates": [],
        "context_profiles": [{
            "context_profile_id": CONTEXT_PROFILE,
            "target_session": TARGET_SESSION,
            "upstream_sessions": upstream,
            "generated_import": base_import,
        }],
        "parent_premises": {
            "database": "parent-premises.sqlite",
            "manifest": "parent-premises-manifest.json",
            "record_count": len(premises),
            "record_ids_sha256": record_ids_sha256,
            "database_sha256": database_sha256,
            "query_interface": QUERY_INTERFACE,
        },
        "planner_interfaces": {
            "inspect_contracts": {"max_ids": 32},
            "search_parent_premises": {"max_results": 50},
            "inspect_parent_premises": {"max_ids": 50},
            "resolve_symbols": {"max_symbols": 64},
            "validate_repository_plan": {},
        },
    }
    core["planning_input_id"] = "refine-planning-input:" + sha256_json(core)[:20]
    return core


def prepare(
    environment_root: Path,
    output: Path,
    private_map: Path,
    *,
    architecture: str = "ARM",
) -> dict[str, Any]:
    """Materialize an independent Refine dataset from a frozen environment."""
    environment_root = environment_root.resolve()
    output = output.resolve()
    private_map = private_map.resolve()
    if output.is_relative_to(environment_root) or private_map.is_relative_to(output):
        raise ValueError("derived dataset and private map must stay outside the frozen environment and each other")
    if output.exists() or private_map.exists():
        raise ValueError("fresh output and private map are required")

    gate_path = environment_root / "Environment-Preparation" / "environment-gate.json"
    index_path = environment_root / "Environment-Dataflow" / "environment-index.json"
    gate = validate_environment_gate(environment_root, gate_path)
    index = validate_dataflow(environment_root, gate_path, index_path)
    if int(index["counts"]["contracts"]) <= 0:
        raise ValueError("environment dataflow contains no contracts")

    contracts = _read_jsonl(resolve_ref(environment_root, index["streams"]["contracts"]))
    premises = _read_jsonl(resolve_ref(environment_root, index["streams"]["premises"]))

    output.mkdir(parents=True)
    database = output / "parent-premises.sqlite"
    _write_parent_database(database, premises)
    database_sha256 = digest(database)
    record_ids_sha256 = sha256_json(sorted(item["record_id"] for item in premises))

    source = _source_payload(
        gate, architecture, contracts, premises, database_sha256, record_ids_sha256,
    )

    from regen.preparation.owner_blind_dataset import anonymous_payload
    from regen.preparation.checker_context import load_context, migrate_legacy

    payload, mapping = anonymous_payload(source)
    for contract in payload["contracts"]:
        contract.pop("owner_theory", None)
        contract.pop("owner_file_slot", None)
    payload["contract_cards"] = [
        {key: value for key, value in card.items() if key != "owner_theory"}
        for card in payload["contract_cards"]
    ]
    payload["owner_groups"] = {}
    payload.pop("planning_input_id", None)
    payload["planning_input_id"] = PLANNING_INPUT_PREFIX + sha256_json(payload)[:20]

    write_immutable_json(output / "repository-planning-input.json", payload)
    write_immutable_json(output / "parent-premises-manifest.json", {
        "schema_version": 2,
        "artifact_type": "parent-premises-manifest",
        "record_count": len(premises),
        "database_sha256": database_sha256,
        "record_ids_sha256": record_ids_sha256,
        "context_profile_ids": [CONTEXT_PROFILE],
        "planning_input_id": payload["planning_input_id"],
    })

    migrate_legacy(environment_root, output)
    checker_context = _read_json(output / "checker-context.json")

    raw_source = json.dumps(source, sort_keys=True, ensure_ascii=False).encode()
    private_map.parent.mkdir(parents=True, exist_ok=True)
    write_immutable_json(private_map, {
        "source_input_sha256": hashlib.sha256(raw_source).hexdigest(),
        "anonymous_to_original": mapping,
        "model_visible": False,
    })

    receipt = {
        "schema_version": 1,
        "artifact_type": RECEIPT_TYPE,
        "status": "prepared",
        "target_session": TARGET_SESSION,
        "architecture": architecture,
        "contract_visibility": CONTRACT_VISIBILITY,
        "target_count": len(contracts),
        "source_environment_gate_id": gate["gate_id"],
        "source_dataflow_id": index["dataflow_id"],
        "same_fixed_statements": True,
        "target_proofs_added": 0,
        "parent_facts": len(premises),
        "quarantined_facts": 0,
        "premise_database_sha256": database_sha256,
        "planning_input_id": payload["planning_input_id"],
        "checker_context_id": checker_context["checker_context_id"],
        "synthetic_owner_is_original_mapping": False,
        "original_owner_groups_and_imports_removed": True,
        "excluded": [
            "proof/refine original theories",
            "ordinary proofs and proof helpers",
            "private command inventory",
            "original proof import graph",
            "dynamic/private facts already excluded upstream",
        ],
    }
    write_immutable_json(output / "preparation.json", receipt)

    load_context(output)
    if verify_database(output / "parent-premises.sqlite") != 2:
        raise ValueError("parent premise database is not v2")
    validate_planning_input(output / "repository-planning-input.json")
    validate_receipt(output / "preparation.json")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    input_parser = subparsers.add_parser("validate-input")
    input_parser.add_argument("--path", type=Path, required=True)
    receipt_parser = subparsers.add_parser("validate-receipt")
    receipt_parser.add_argument("--path", type=Path, required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--environment-root", type=Path, required=True)
    prepare_parser.add_argument("--output", type=Path, required=True)
    prepare_parser.add_argument("--private-map", type=Path, required=True)
    prepare_parser.add_argument("--architecture", default="ARM")
    args = parser.parse_args()
    if args.command == "validate-input":
        validate_planning_input(args.path)
        result = {"verdict": "pass", "artifact": "repository-planning-input"}
    elif args.command == "validate-receipt":
        validate_receipt(args.path)
        result = {"verdict": "pass", "artifact": RECEIPT_TYPE}
    else:
        result = prepare(
            args.environment_root, args.output, args.private_map,
            architecture=args.architecture,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
