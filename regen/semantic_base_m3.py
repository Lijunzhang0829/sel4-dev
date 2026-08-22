#!/usr/bin/env python3
"""M3: publish a retrievable database of legal Semantic Base premises."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from regen.semantic_base_pipeline import (
    build_initial_premise_snapshot,
    read_json,
    write_json,
)


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


def normalized_record(record: dict) -> tuple:
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


def write_database(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.executemany(
        "INSERT INTO premises VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [normalized_record(record) for record in records],
    )
    connection.commit()
    connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    raw = read_json(args.records)
    result = build_initial_premise_snapshot(
        args.campaign_id, raw.get("records", []),
    )
    write_json(args.snapshot, result)
    write_database(args.database, result["records"])
    print(json.dumps({
        "records": len(result["records"]),
        "counts_by_source_class": result["counts_by_source_class"],
        "database": str(args.database),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
