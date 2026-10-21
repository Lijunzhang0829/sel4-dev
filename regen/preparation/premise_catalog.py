#!/usr/bin/env python3
"""Publish and verify the legal Premise Catalog for a sealed environment."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from typing import Any, Iterable

from regen.preparation.environment_gate import payload_sha256, read_json, sha256_path
from regen.preparation.refine_campaign import write_immutable_json


LEGAL_INITIAL_SOURCE_CLASSES = {
    "frozen_parent",
    "retained_semantic_export",
}
SCHEMA = """
CREATE TABLE premises (
  record_id TEXT PRIMARY KEY,
  canonical_name TEXT NOT NULL,
  declaration_name TEXT NOT NULL,
  fact_index INTEGER NOT NULL,
  exact_statement TEXT NOT NULL,
  normalized_statement_sha256 TEXT NOT NULL,
  owner_theory TEXT NOT NULL,
  owner_session TEXT NOT NULL,
  source_class TEXT NOT NULL,
  attributes_json TEXT NOT NULL,
  definition_equations_json TEXT NOT NULL,
  publication_state TEXT NOT NULL,
  provenance_json TEXT NOT NULL
);
CREATE INDEX premises_name_idx ON premises(canonical_name);
CREATE INDEX premises_statement_idx ON premises(normalized_statement_sha256);
CREATE INDEX premises_owner_idx ON premises(owner_theory);
CREATE INDEX premises_source_class_idx ON premises(source_class);
"""


def normalized_record(record: dict[str, Any]) -> tuple[Any, ...]:
    statement = record["exact_statement"]
    name = record["canonical_name"]
    return (
        record["record_id"],
        name,
        record.get("declaration_name", name.rsplit(".", 1)[-1]),
        int(record.get("fact_index", 0)),
        statement,
        record.get(
            "normalized_statement_sha256",
            hashlib.sha256(" ".join(statement.split()).encode()).hexdigest(),
        ),
        record["owner_theory"],
        record["owner_session"],
        record["source_class"],
        json.dumps(record["attributes"], sort_keys=True),
        json.dumps(record.get("definition_equations", []), sort_keys=True),
        record.get("publication_state", "published"),
        json.dumps(record.get("provenance", {}), sort_keys=True),
    )


def validate_record(record: dict[str, Any]) -> dict[str, Any]:
    required = {
        "record_id", "canonical_name", "exact_statement", "owner_theory",
        "owner_session", "source_class", "attributes",
    }
    missing = required - record.keys()
    if missing:
        raise ValueError(f"premise record is missing fields: {sorted(missing)}")
    if record["source_class"] not in LEGAL_INITIAL_SOURCE_CLASSES:
        raise ValueError(
            f"illegal initial premise source class: {record['source_class']}"
        )
    if not isinstance(record["attributes"], list):
        raise ValueError("premise attributes must be a list")
    return dict(record)


def build_snapshot(
    semantic_base_seal: dict[str, Any], records: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    if semantic_base_seal.get("artifact_type") != "semantic-base-seal":
        raise ValueError("Premise Catalog requires a Semantic Base seal")
    if semantic_base_seal.get("verdict") != "pass":
        raise ValueError("Premise Catalog cannot publish from a non-PASS seal")
    checked = sorted(
        (validate_record(record) for record in records),
        key=lambda item: item["record_id"],
    )
    identifiers = [item["record_id"] for item in checked]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("duplicate premise record ID")
    records_hash = payload_sha256(checked)
    core = {
        "schema_version": 1,
        "artifact_type": "semantic-base-premise-snapshot",
        "campaign_id": semantic_base_seal["campaign_id"],
        "semantic_base_seal_id": semantic_base_seal["seal_id"],
        "publication_phase": "initial",
        "records": checked,
        "counts_by_source_class": {
            source_class: sum(
                item["source_class"] == source_class for item in checked
            )
            for source_class in sorted(LEGAL_INITIAL_SOURCE_CLASSES)
        },
        "records_payload_sha256": records_hash,
    }
    return {
        **core,
        "snapshot_id": f"premise-snapshot:{records_hash[:20]}",
    }


def write_database(path: Path, records: list[dict[str, Any]]) -> None:
    if path.exists():
        raise ValueError(f"refusing to overwrite Premise Catalog database: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA)
        connection.executemany(
            "INSERT INTO premises VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [normalized_record(record) for record in records],
        )
        connection.commit()
    finally:
        connection.close()


def verify_catalog(
    semantic_base_seal_path: Path,
    snapshot_path: Path,
    database_path: Path,
) -> dict[str, Any]:
    seal = read_json(semantic_base_seal_path)
    snapshot = read_json(snapshot_path)
    if snapshot.get("semantic_base_seal_id") != seal.get("seal_id"):
        raise ValueError("Premise Catalog snapshot binds the wrong Semantic Base seal")
    records_hash = payload_sha256(snapshot.get("records", []))
    if snapshot.get("records_payload_sha256") != records_hash:
        raise ValueError("Premise Catalog records payload hash is invalid")
    if snapshot.get("snapshot_id") != f"premise-snapshot:{records_hash[:20]}":
        raise ValueError("Premise Catalog snapshot ID is invalid")
    connection = sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT record_id, canonical_name, declaration_name, fact_index, "
            "exact_statement, normalized_statement_sha256, owner_theory, "
            "owner_session, source_class, attributes_json, "
            "definition_equations_json, publication_state, provenance_json "
            "FROM premises ORDER BY record_id"
        ).fetchall()
    finally:
        connection.close()
    expected = [normalized_record(item) for item in snapshot["records"]]
    if rows != expected:
        raise ValueError("Premise Catalog database does not reconstruct the snapshot")
    return {
        "verdict": "pass",
        "campaign_id": snapshot["campaign_id"],
        "semantic_base_seal_id": snapshot["semantic_base_seal_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "record_count": len(rows),
        "snapshot_sha256": sha256_path(snapshot_path),
        "database_sha256": sha256_path(database_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--semantic-base-seal", type=Path, required=True)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    if not args.verify:
        seal = read_json(args.semantic_base_seal)
        raw = read_json(args.records)
        snapshot = build_snapshot(seal, raw.get("records", []))
        write_immutable_json(args.snapshot, snapshot)
        write_database(args.database, snapshot["records"])
    result = verify_catalog(
        args.semantic_base_seal, args.snapshot, args.database,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
