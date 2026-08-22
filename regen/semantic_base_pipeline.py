#!/usr/bin/env python3
"""Classification-free contracts for the Semantic-Base proof pipeline.

Every Refine theory has the same record shape. Whether it needs generation is
derived only from its fixed contract and framework-obligation roots.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

THEORY_FIELDS = {
    "theory", "file_slot", "fixed_declarations", "contract_ids",
    "framework_obligation_ids",
}
LEGACY_FIELDS = {
    "classification", "proof_theories", "support_theories", "support_plan",
    "support_status", "unlock_by_support", "blocked_support_theories",
}
TASK_STATUSES = {
    "pending", "generating", "sealed", "failed", "skipped", "retry",
}
LEGAL_INITIAL_PREMISE_CLASSES = {
    "frozen_parent", "fixed_semantic", "fixed_declaration_generated",
}
LEGAL_DYNAMIC_PREMISE_CLASSES = LEGAL_INITIAL_PREMISE_CLASSES | {
    "generated_theorem",
}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a list of strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} contains duplicates")
    return value


def reject_legacy_classification(value: Any, path: str = "$") -> None:
    """Ensure no old file-category or unlock state crosses the new boundary."""
    if isinstance(value, dict):
        overlap = LEGACY_FIELDS.intersection(value)
        if overlap:
            raise ValueError(f"legacy field at {path}: {sorted(overlap)}")
        for key, item in value.items():
            reject_legacy_classification(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            reject_legacy_classification(item, f"{path}[{index}]")


def validate_theory_record(record: dict[str, Any]) -> dict[str, Any]:
    reject_legacy_classification(record)
    missing = THEORY_FIELDS - record.keys()
    extra = record.keys() - THEORY_FIELDS
    if missing or extra:
        raise ValueError(
            f"invalid theory record fields; missing={sorted(missing)}, "
            f"extra={sorted(extra)}"
        )
    theory = record["theory"]
    file_slot = record["file_slot"]
    if not isinstance(theory, str) or not theory:
        raise ValueError("theory must be a non-empty string")
    if not isinstance(file_slot, str) or not file_slot.endswith(".thy"):
        raise ValueError("file_slot must be a .thy path")
    return {
        "theory": theory,
        "file_slot": file_slot,
        "fixed_declarations": _string_list(
            record["fixed_declarations"], "fixed_declarations",
        ),
        "contract_ids": _string_list(record["contract_ids"], "contract_ids"),
        "framework_obligation_ids": _string_list(
            record["framework_obligation_ids"], "framework_obligation_ids",
        ),
    }


def build_environment_manifest(
    campaign_id: str, theories: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    records = [validate_theory_record(record) for record in theories]
    records.sort(key=lambda item: (item["file_slot"], item["theory"]))
    theory_names = [item["theory"] for item in records]
    file_slots = [item["file_slot"] for item in records]
    if len(theory_names) != len(set(theory_names)):
        raise ValueError("duplicate theory name")
    if len(file_slots) != len(set(file_slots)):
        raise ValueError("duplicate file slot")
    groups = {
        "fixed declaration": [
            item for record in records for item in record["fixed_declarations"]
        ],
        "contract": [item for record in records for item in record["contract_ids"]],
        "framework obligation": [
            item for record in records for item in record["framework_obligation_ids"]
        ],
    }
    for label, identifiers in groups.items():
        if len(identifiers) != len(set(identifiers)):
            raise ValueError(f"duplicate {label} ID")
    result = {
        "schema_version": 1,
        "artifact_type": "semantic-base-environment-manifest",
        "campaign_id": campaign_id,
        "theories": records,
        "counts": {
            "theories": len(records),
            "fixed_declarations": len(groups["fixed declaration"]),
            "contracts": len(groups["contract"]),
            "framework_obligations": len(groups["framework obligation"]),
        },
    }
    reject_legacy_classification(result)
    return result


def generation_tasks(environment_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    reject_legacy_classification(environment_manifest)
    tasks = []
    for raw in environment_manifest.get("theories", []):
        record = validate_theory_record(raw)
        roots = record["contract_ids"] + record["framework_obligation_ids"]
        if roots:
            tasks.append({
                "theory": record["theory"],
                "file_slot": record["file_slot"],
                "contract_ids": record["contract_ids"],
                "framework_obligation_ids": record["framework_obligation_ids"],
                "root_ids": roots,
            })
    return tasks


def build_checker_manifest(
    campaign_id: str,
    environment_manifest: dict[str, Any],
    *,
    parent_sessions: list[str],
    base_session: str = "Semantic_Base",
) -> dict[str, Any]:
    reject_legacy_classification(environment_manifest)
    parents = _string_list(parent_sessions, "parent_sessions")
    if not parents:
        raise ValueError("Semantic Base requires at least one parent session")
    theories = [
        validate_theory_record(item) for item in environment_manifest["theories"]
    ]
    result = {
        "schema_version": 1,
        "artifact_type": "semantic-base-checker-manifest",
        "campaign_id": campaign_id,
        "base_session": base_session,
        "parent_sessions": parents,
        "theory_files": [item["file_slot"] for item in theories],
        "fixed_declaration_ids": [
            declaration
            for item in theories
            for declaration in item["fixed_declarations"]
        ],
        "contract_shell_ids": [
            contract for item in theories for contract in item["contract_ids"]
        ],
        "framework_obligation_ids": [
            obligation
            for item in theories
            for obligation in item["framework_obligation_ids"]
        ],
        "forbidden_fact_scope": "all human-written theorem facts owned by Refine",
        "proof_context_rule": (
            "generated modules run only against this base session plus already "
            "sealed generated theories"
        ),
    }
    reject_legacy_classification(result)
    return result


def _validate_premise_record(
    record: dict[str, Any], *, allowed_classes: set[str],
) -> dict[str, Any]:
    required = {
        "record_id", "canonical_name", "exact_statement", "owner_theory",
        "owner_session", "source_class", "attributes",
    }
    missing = required - record.keys()
    if missing:
        raise ValueError(f"premise record is missing fields: {sorted(missing)}")
    if record["source_class"] not in allowed_classes:
        raise ValueError(
            f"illegal premise source class: {record['source_class']} "
            f"for {record['record_id']}"
        )
    if record.get("human_refine_source"):
        raise ValueError(
            f"human Refine theorem cannot be published: {record['record_id']}"
        )
    if not isinstance(record["attributes"], list):
        raise ValueError("premise attributes must be a list")
    return dict(record)


def build_initial_premise_snapshot(
    campaign_id: str, records: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    checked = [
        _validate_premise_record(
            record, allowed_classes=LEGAL_INITIAL_PREMISE_CLASSES,
        )
        for record in records
    ]
    checked.sort(key=lambda item: item["record_id"])
    ids = [item["record_id"] for item in checked]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate premise record ID")
    result = {
        "schema_version": 1,
        "artifact_type": "semantic-base-premise-snapshot",
        "campaign_id": campaign_id,
        "publication_phase": "initial",
        "records": checked,
        "counts_by_source_class": {
            source_class: sum(
                item["source_class"] == source_class for item in checked
            )
            for source_class in sorted(LEGAL_INITIAL_PREMISE_CLASSES)
        },
    }
    reject_legacy_classification(result)
    return result


def publish_generated_records(
    snapshot: dict[str, Any], records: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    reject_legacy_classification(snapshot)
    current = {
        item["record_id"]: _validate_premise_record(
            item, allowed_classes=LEGAL_DYNAMIC_PREMISE_CLASSES,
        )
        for item in snapshot.get("records", [])
    }
    additions = list(records)
    if not additions:
        raise ValueError("sealed publication contains no records")
    for raw in additions:
        record = _validate_premise_record(
            raw, allowed_classes={"generated_theorem"},
        )
        if not record.get("theory_seal_id"):
            raise ValueError(
                f"generated premise has no theory seal: {record['record_id']}"
            )
        if record["record_id"] in current:
            raise ValueError(f"premise record already exists: {record['record_id']}")
        current[record["record_id"]] = record
    result = {
        **snapshot,
        "publication_phase": "dynamic",
        "records": [current[key] for key in sorted(current)],
    }
    result["counts_by_source_class"] = {
        source_class: sum(
            item["source_class"] == source_class for item in result["records"]
        )
        for source_class in sorted(LEGAL_DYNAMIC_PREMISE_CLASSES)
    }
    reject_legacy_classification(result)
    return result


def build_file_generation_list(
    campaign_id: str,
    environment_manifest: dict[str, Any],
    ordered_file_slots: list[str],
) -> dict[str, Any]:
    tasks = generation_tasks(environment_manifest)
    by_slot = {task["file_slot"]: task for task in tasks}
    _string_list(ordered_file_slots, "ordered_file_slots")
    if set(ordered_file_slots) != set(by_slot):
        missing = sorted(set(by_slot) - set(ordered_file_slots))
        extra = sorted(set(ordered_file_slots) - set(by_slot))
        raise ValueError(f"generation order mismatch; missing={missing}, extra={extra}")
    result = {
        "schema_version": 1,
        "artifact_type": "semantic-base-file-generation-list",
        "campaign_id": campaign_id,
        "files": [
            {"order": index, **by_slot[file_slot]}
            for index, file_slot in enumerate(ordered_file_slots)
        ],
    }
    reject_legacy_classification(result)
    return result


def build_layered_file_generation_list(
    campaign_id: str,
    environment_manifest: dict[str, Any],
    dependency_edges: Iterable[tuple[str, str]],
) -> dict[str, Any]:
    """Topologically layer tasks from explicit generation-priority edges."""
    tasks = generation_tasks(environment_manifest)
    by_theory = {item["theory"]: item for item in tasks}
    incoming = {name: set() for name in by_theory}
    edge_records = []
    for provider, consumer in dependency_edges:
        if provider not in by_theory or consumer not in by_theory:
            raise ValueError(
                f"generation edge names unknown theory: {provider}->{consumer}"
            )
        if provider == consumer:
            raise ValueError(f"self generation edge: {provider}")
        incoming[consumer].add(provider)
        edge_records.append({"provider": provider, "consumer": consumer})
    layers = []
    remaining = set(by_theory)
    emitted: set[str] = set()
    while remaining:
        ready = sorted(name for name in remaining if incoming[name] <= emitted)
        if not ready:
            raise ValueError(f"generation dependency cycle: {sorted(remaining)}")
        layers.append({
            "layer": len(layers),
            "theories": [
                {"order_in_layer": index, **by_theory[name]}
                for index, name in enumerate(ready)
            ],
        })
        emitted.update(ready)
        remaining.difference_update(ready)
    result = {
        "schema_version": 1,
        "artifact_type": "semantic-base-file-generation-list",
        "campaign_id": campaign_id,
        "layers": layers,
        "generation_edges": sorted(
            edge_records,
            key=lambda item: (item["provider"], item["consumer"]),
        ),
        "file_count": len(tasks),
    }
    reject_legacy_classification(result)
    return result


def build_progress_ledger(file_generation_list: dict[str, Any]) -> dict[str, Any]:
    reject_legacy_classification(file_generation_list)
    if "files" in file_generation_list:
        files = file_generation_list["files"]
    else:
        files = [
            {"order": index, **item}
            for index, item in enumerate(
                theory
                for layer in file_generation_list.get("layers", [])
                for theory in layer["theories"]
            )
        ]
    return {
        "schema_version": 1,
        "artifact_type": "session-progress-ledger",
        "campaign_id": file_generation_list["campaign_id"],
        "files": [
            {
                "order": item["order"],
                "theory": item["theory"],
                "file_slot": item["file_slot"],
                "status": "pending",
                "generation_id": None,
            }
            for item in files
        ],
    }


def update_file_status(
    ledger: dict[str, Any],
    file_slot: str,
    status: str,
    generation_id: str | None,
) -> dict[str, Any]:
    reject_legacy_classification(ledger)
    if status not in TASK_STATUSES:
        raise ValueError(f"invalid file status: {status}")
    matches = [
        item for item in ledger.get("files", []) if item["file_slot"] == file_slot
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one ledger entry for {file_slot}")
    if status in {"generating", "sealed", "failed", "retry"} and not generation_id:
        raise ValueError(f"{status} requires generation_id")
    matches[0]["status"] = status
    matches[0]["generation_id"] = generation_id
    return ledger


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--theory-inventory", type=Path, required=True)
    parser.add_argument("--environment-manifest", type=Path, required=True)
    args = parser.parse_args()
    inventory = read_json(args.theory_inventory)
    theories = inventory.get("theories")
    if not isinstance(theories, list):
        raise ValueError("theory inventory must contain a theories list")
    manifest = build_environment_manifest(args.campaign_id, theories)
    write_json(args.environment_manifest, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
