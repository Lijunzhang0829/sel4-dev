#!/usr/bin/env python3
"""Prepare the direct, data-only Repository Planner input for Refine.

Refine and CRefine are independent benchmarks.  This module intentionally
supports Refine only and rejects target-session facts from the immutable
parent-premise database.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable

from regen.preparation.refine_campaign import sha256_json, write_immutable_json
from tools.lemma_inventory.parse_roots import parse_all_roots


TARGET_SESSION = "Refine"
FORBIDDEN_SESSIONS = {"Refine", "CRefine"}
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_'.]*")
TYPE = re.compile(r"(?:::|\<Rightarrow>|=>)\s*([A-Za-z_][A-Za-z0-9_'.]*)")
IMPORTS = re.compile(
    r"\btheory\s+(?P<name>[A-Za-z_][A-Za-z0-9_']*)\s+imports\s+"
    r"(?P<imports>.*?)\bbegin\b",
    re.DOTALL,
)
IMPORT_ITEM = re.compile(r'"([^"]+)"|([A-Za-z_][A-Za-z0-9_./\'-]*)')
GENERIC_WORDS = {
    "lemma", "theorem", "corollary", "fixes", "assumes", "shows", "obtains",
    "and", "True", "False",
}
FORBIDDEN_PAYLOAD_KEYS = {
    "proof", "proof_body", "human_proof", "original_proof",
    "source_location", "trusted_source_path",
}

PARENT_SCHEMA = """
CREATE TABLE premises (
  record_id TEXT PRIMARY KEY,
  canonical_name TEXT NOT NULL,
  declaration_name TEXT NOT NULL,
  fact_index INTEGER NOT NULL,
  exact_statement TEXT NOT NULL,
  normalized_statement_sha256 TEXT NOT NULL,
  logical_shape_json TEXT NOT NULL,
  constants_json TEXT NOT NULL,
  types_json TEXT NOT NULL,
  attributes_json TEXT NOT NULL,
  definition_equations_json TEXT NOT NULL,
  owner_theory TEXT NOT NULL,
  owner_session TEXT NOT NULL,
  architecture TEXT NOT NULL,
  locale_context_json TEXT NOT NULL,
  aliases_json TEXT NOT NULL,
  context_profile_ids_json TEXT NOT NULL,
  visibility_path_json TEXT NOT NULL,
  source_class TEXT NOT NULL,
  provenance_json TEXT NOT NULL,
  search_text TEXT NOT NULL,
  embedding_ref TEXT
);
CREATE INDEX premises_name_idx ON premises(canonical_name);
CREATE INDEX premises_statement_idx ON premises(normalized_statement_sha256);
CREATE INDEX premises_owner_idx ON premises(owner_session, owner_theory);
"""


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _records(value: Any, field: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        rows = value
    elif isinstance(value, dict) and isinstance(value.get(field), list):
        rows = value[field]
    else:
        raise ValueError(f"expected a JSON list or object containing {field}")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{field} must contain objects")
    return [dict(row) for row in rows]


def load_records(path: Path, field: str) -> list[dict[str, Any]]:
    if path.is_dir():
        result: list[dict[str, Any]] = []
        for item in sorted(path.glob("*.json")):
            value = _read_json(item)
            if isinstance(value, dict) and isinstance(value.get(field), list):
                result.extend(_records(value, field))
        if not result:
            raise ValueError(f"no {field} records found in {path}")
        return result
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    return _records(_read_json(path), field)


def normalized_statement(statement: str) -> str:
    return " ".join(statement.split())


def statement_hash(statement: str) -> str:
    return hashlib.sha256(normalized_statement(statement).encode()).hexdigest()


def symbols(statement: str) -> list[str]:
    return sorted({
        token for token in WORD.findall(statement)
        if token not in GENERIC_WORDS and len(token) > 1
    })


def logical_shape(statement: str) -> dict[str, Any]:
    lower = statement.lower()
    return {
        "has_assumptions": "assumes" in lower or r"\<Longrightarrow>" in statement,
        "has_equivalence": r"\<longleftrightarrow>" in statement or r"\<equiv>" in statement,
        "has_equality": "=" in statement,
        "quantifier_count": sum(statement.count(item) for item in (r"\<forall>", r"\<exists>")),
        "implication_count": statement.count(r"\<Longrightarrow>") + statement.count("-->"),
    }


def _validate_data_only(value: Any, path: str = "root") -> None:
    if isinstance(value, dict):
        forbidden = FORBIDDEN_PAYLOAD_KEYS.intersection(value)
        if forbidden:
            raise ValueError(f"forbidden proof/source fields at {path}: {sorted(forbidden)}")
        for key, item in value.items():
            _validate_data_only(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_data_only(item, f"{path}[{index}]")
    elif isinstance(value, str) and len(value) > 200_000:
        raise ValueError(f"unbounded text field at {path}")


def session_closure(parsed: dict[str, Any], target: str = TARGET_SESSION) -> list[str]:
    sessions = parsed["sessions"]
    if target not in sessions:
        raise ValueError(f"prepared repository has no {target} session")
    result: set[str] = set()
    pending = [
        sessions[target].get("parent"),
        *sessions[target].get("imported_sessions", []),
    ]
    while pending:
        name = pending.pop()
        if not name or name in result or name in FORBIDDEN_SESSIONS:
            continue
        result.add(name)
        record = sessions.get(name)
        if record:
            pending.extend([record.get("parent"), *record.get("imported_sessions", [])])
    return sorted(result)


def _session_roots(parsed: dict[str, Any]) -> list[tuple[str, str]]:
    return sorted(
        ((os.path.abspath(path).rstrip(os.sep) + os.sep, sorted(names)[0])
         for path, names in parsed["theory_search_paths"].items()),
        key=lambda item: len(item[0]), reverse=True,
    )


def _session_for_path(path: Path, roots: list[tuple[str, str]]) -> str | None:
    absolute = os.path.abspath(path)
    return next((session for directory, session in roots
                 if absolute.startswith(directory)), None)


def theory_session_candidates(l4v: Path, parsed: dict[str, Any]) -> dict[str, set[str]]:
    """Resolve l4v theory basenames to ROOT sessions without trusting export labels."""
    roots = _session_roots(parsed)
    result: dict[str, set[str]] = {}
    for theory in l4v.rglob("*.thy"):
        session = _session_for_path(theory, roots)
        if session:
            result.setdefault(theory.stem, set()).add(session)
    return result


def select_parent_premises(
    l4v: Path, parsed: dict[str, Any], premises: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Select strict Refine-upstream facts from a broad checker export.

    Historical exports labelled ``owner_session`` with a theory name.  ROOT
    ownership is therefore reconstructed from ``owner_theory``.  Unknown
    external/export-wrapper theories are excluded instead of being guessed.
    """
    upstream = set(session_closure(parsed))
    owners = theory_session_candidates(l4v, parsed)
    selected: list[dict[str, Any]] = []
    audit = {"source_records": 0, "selected_records": 0,
             "excluded_target_session": 0, "excluded_unresolved_owner": 0,
             "excluded_non_upstream": 0}
    for raw in premises:
        audit["source_records"] += 1
        owner_theory = str(raw.get("owner_theory"))
        candidates = owners.get(owner_theory, set()) or owners.get(
            owner_theory.rsplit(".", 1)[-1], set()
        )
        legal = sorted(candidates & upstream)
        if len(candidates) == 1 and len(legal) == 1:
            selected.append({**raw, "owner_session": legal[0],
                             "export_owner_label": raw.get("owner_session")})
            audit["selected_records"] += 1
        elif candidates & FORBIDDEN_SESSIONS:
            audit["excluded_target_session"] += 1
        elif not candidates:
            audit["excluded_unresolved_owner"] += 1
        else:
            audit["excluded_non_upstream"] += 1
    return selected, audit


def theory_import_graph(l4v: Path, architecture: str) -> dict[str, list[str]]:
    graph: dict[str, list[str]] = {}
    parsed = parse_all_roots(l4v, arch=architecture)
    roots = _session_roots(parsed)
    for path in sorted(l4v.rglob("*.thy")):
        if _session_for_path(path, roots) != TARGET_SESSION:
            continue
        match = IMPORTS.search(path.read_text(encoding="utf-8", errors="replace"))
        if not match:
            continue
        graph[f"{TARGET_SESSION}.{match.group('name')}"] = [
            quoted or bare
            for quoted, bare in IMPORT_ITEM.findall(match.group("imports"))
        ]
    return graph


def contract_record(raw: dict[str, Any]) -> dict[str, Any]:
    required = {
        "contract_id", "name", "exact_statement", "owner_theory",
        "contract_owned_context",
    }
    missing = required - raw.keys()
    if missing:
        raise ValueError(f"contract missing fields: {sorted(missing)}")
    if not str(raw["owner_theory"]).startswith("Refine."):
        raise ValueError("Refine input contains a non-Refine contract")
    record = {key: raw[key] for key in (
        "contract_id", "name", "kind", "exact_statement",
        "normalized_statement_sha256", "owner_theory", "owner_file_slot",
        "contract_owned_attributes_raw", "contract_owned_attributes",
        "contract_owned_context", "contract_context_sha256",
        "contract_semantic_sha256",
    ) if key in raw}
    expected = statement_hash(record["exact_statement"])
    actual = record.get("normalized_statement_sha256")
    if actual is not None and actual != expected:
        raise ValueError(f"contract statement hash mismatch: {record['contract_id']}")
    record["normalized_statement_sha256"] = expected
    _validate_data_only(record)
    return record


def parent_record(
    raw: dict[str, Any], *, architecture: str, context_profile_id: str,
    upstream_sessions: set[str],
) -> dict[str, Any]:
    required = {
        "record_id", "canonical_name", "exact_statement", "owner_theory",
        "owner_session", "attributes",
    }
    missing = required - raw.keys()
    if missing:
        raise ValueError(f"parent premise missing fields: {sorted(missing)}")
    session = raw["owner_session"]
    theory = raw["owner_theory"]
    if session in FORBIDDEN_SESSIONS or str(theory).startswith(("Refine.", "CRefine.")):
        raise ValueError(f"target-session fact cannot enter parent database: {raw['record_id']}")
    if session not in upstream_sessions:
        raise ValueError(f"premise is outside the Refine parent closure: {raw['record_id']}")
    statement = raw["exact_statement"]
    name = raw["canonical_name"]
    record = {
        "record_id": raw["record_id"],
        "canonical_name": name,
        "declaration_name": raw.get("declaration_name", name.rsplit(".", 1)[-1]),
        "fact_index": int(raw.get("fact_index", 0)),
        "exact_statement": statement,
        "normalized_statement_sha256": statement_hash(statement),
        "logical_shape": raw.get("logical_shape", logical_shape(statement)),
        "constants": sorted(set(raw.get("constants", symbols(statement)))),
        "types": sorted(set(raw.get("types", TYPE.findall(statement)))),
        "attributes": sorted(set(raw["attributes"])),
        "definition_equations": raw.get("definition_equations", []),
        "owner_theory": theory,
        "owner_session": session,
        "architecture": architecture,
        "locale_context": raw.get("locale_context", []),
        "aliases": sorted(set(raw.get("aliases", [name]))),
        "context_profile_ids": [context_profile_id],
        "visibility_path": raw.get("visibility_path", [session, TARGET_SESSION]),
        "source_class": raw.get("source_class", "frozen_parent"),
        "provenance": raw.get("provenance", {}),
        "embedding_ref": raw.get("embedding_ref"),
    }
    record["search_text"] = " ".join([
        record["canonical_name"], record["exact_statement"],
        *record["constants"], *record["attributes"],
    ])
    _validate_data_only(record)
    return record


def contract_card(contract: dict[str, Any]) -> dict[str, Any]:
    statement = contract["exact_statement"]
    return {
        "contract_id": contract["contract_id"],
        "name": contract["name"],
        "owner_theory": contract["owner_theory"],
        "normalized_statement_sha256": contract["normalized_statement_sha256"],
        "principal_symbols": symbols(statement)[:32],
        "logical_shape": logical_shape(statement),
    }


def relation_hints(cards: list[dict[str, Any]], limit: int = 2000) -> list[dict[str, Any]]:
    hints = []
    for index, left in enumerate(cards):
        for right in cards[index + 1:]:
            shared = sorted(set(left["principal_symbols"]).intersection(right["principal_symbols"]))
            same_owner = left["owner_theory"] == right["owner_theory"]
            if not shared and not same_owner:
                continue
            evidence = {
                "shared_symbols": shared[:16],
                "same_owner_theory": same_owner,
            }
            hints.append({
                "relation_id": "relation:" + sha256_json({
                    "left": left["contract_id"], "right": right["contract_id"],
                    "evidence": evidence,
                })[:16],
                "left_contract_id": left["contract_id"],
                "right_contract_id": right["contract_id"],
                "candidate_relation": "same-owner" if same_owner else "shared-symbols",
                "evidence": evidence,
            })
    return sorted(
        hints,
        key=lambda item: (
            -len(item["evidence"]["shared_symbols"]),
            item["left_contract_id"], item["right_contract_id"],
        ),
    )[:limit]


def _parent_row(record: dict[str, Any]) -> tuple[Any, ...]:
    json_field = lambda key: json.dumps(record[key], sort_keys=True)
    return (
        record["record_id"], record["canonical_name"], record["declaration_name"],
        record["fact_index"], record["exact_statement"],
        record["normalized_statement_sha256"], json_field("logical_shape"),
        json_field("constants"), json_field("types"), json_field("attributes"),
        json_field("definition_equations"), record["owner_theory"],
        record["owner_session"], record["architecture"], json_field("locale_context"),
        json_field("aliases"), json_field("context_profile_ids"),
        json_field("visibility_path"), record["source_class"],
        json_field("provenance"), record["search_text"], record["embedding_ref"],
    )


def write_parent_database(path: Path, records: list[dict[str, Any]]) -> None:
    if path.exists():
        raise ValueError(f"refusing to overwrite parent-premise database: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(PARENT_SCHEMA)
        connection.executemany(
            "INSERT INTO premises VALUES (" + ",".join("?" for _ in range(22)) + ")",
            [_parent_row(record) for record in records],
        )
        connection.commit()
    finally:
        connection.close()


def build_refine_input(
    l4v: Path, contracts: Iterable[dict[str, Any]],
    premises: Iterable[dict[str, Any]], *, architecture: str = "ARM",
    premise_selection_audit: dict[str, int] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    l4v = l4v.resolve()
    parsed = parse_all_roots(l4v, arch=architecture)
    upstream = session_closure(parsed)
    profile_id = "context:Refine:parents"
    checked_contracts = sorted(
        (contract_record(item) for item in contracts),
        key=lambda item: item["contract_id"],
    )
    ids = [item["contract_id"] for item in checked_contracts]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Refine contracts must be nonempty and unique")
    checked_premises = sorted(
        (parent_record(
            item, architecture=architecture, context_profile_id=profile_id,
            upstream_sessions=set(upstream),
        ) for item in premises),
        key=lambda item: item["record_id"],
    )
    premise_ids = [item["record_id"] for item in checked_premises]
    if len(premise_ids) != len(set(premise_ids)):
        raise ValueError("duplicate parent-premise record ID")
    cards = [contract_card(item) for item in checked_contracts]
    sessions = {
        name: {
            "parent": parsed["sessions"][name].get("parent"),
            "imported_sessions": parsed["sessions"][name].get("imported_sessions", []),
        }
        for name in [TARGET_SESSION, *upstream] if name in parsed["sessions"]
    }
    core = {
        "schema_version": 1,
        "artifact_type": "repository-planning-input",
        "benchmark": {
            "id": f"sel4-refine-{architecture.lower()}",
            "target_session": TARGET_SESSION,
            "architecture": architecture,
            "independent_experiment": True,
            "cross_experiment_inputs": [],
        },
        "contracts": checked_contracts,
        "contract_cards": cards,
        "owner_groups": {
            owner: sorted(card["contract_id"] for card in cards if card["owner_theory"] == owner)
            for owner in sorted({card["owner_theory"] for card in cards})
        },
        "session_graph": sessions,
        "upstream_session_closure": upstream,
        "theory_import_graph": theory_import_graph(l4v, architecture),
        "context_profiles": [{
            "context_profile_id": profile_id,
            "target_session": TARGET_SESSION,
            "upstream_sessions": upstream,
            "generated_import": "Semantic_Base.Semantic_Root",
        }],
        "contract_relation_candidates": relation_hints(cards),
        "parent_premises": {
            "database": "parent-premises.sqlite",
            "manifest": "parent-premises-manifest.json",
            "record_count": len(checked_premises),
            "record_ids_sha256": sha256_json(premise_ids),
            "query_interface": "bounded-read-only-v1",
            "selection_audit": premise_selection_audit or {
                "source_records": len(checked_premises),
                "selected_records": len(checked_premises),
                "excluded_target_session": 0,
                "excluded_unresolved_owner": 0,
                "excluded_non_upstream": 0,
            },
        },
        "planner_interfaces": {
            "inspect_contracts": {"max_ids": 32},
            "search_parent_premises": {"max_results": 50},
            "inspect_parent_premises": {"max_ids": 50},
            "resolve_symbols": {"max_symbols": 64},
            "validate_repository_plan": {},
        },
    }
    return {
        **core,
        "planning_input_id": "refine-planning-input:" + sha256_json(core)[:20],
    }, checked_premises


def prepare(
    l4v: Path, contracts_path: Path, premises_path: Path, output: Path,
    *, architecture: str = "ARM",
) -> dict[str, Any]:
    parsed = parse_all_roots(l4v.resolve(), arch=architecture)
    selected, selection_audit = select_parent_premises(
        l4v.resolve(), parsed, load_records(premises_path, "records"),
    )
    planning_input, premises = build_refine_input(
        l4v, load_records(contracts_path, "contracts"), selected,
        architecture=architecture, premise_selection_audit=selection_audit,
    )
    output.mkdir(parents=True, exist_ok=True)
    write_immutable_json(output / "repository-planning-input.json", planning_input)
    write_parent_database(output / "parent-premises.sqlite", premises)
    manifest_core = {
        "schema_version": 1,
        "artifact_type": "parent-premises-manifest",
        "benchmark_id": planning_input["benchmark"]["id"],
        "planning_input_id": planning_input["planning_input_id"],
        "record_count": len(premises),
        "records_sha256": sha256_json(premises),
        "forbidden_sessions": sorted(FORBIDDEN_SESSIONS),
        "publication": "immutable-parent-only",
    }
    manifest = {
        **manifest_core,
        "manifest_id": "parent-premises:" + sha256_json(manifest_core)[:20],
    }
    write_immutable_json(output / "parent-premises-manifest.json", manifest)
    return {
        "verdict": "pass",
        "planning_input": str(output / "repository-planning-input.json"),
        "parent_premises": str(output / "parent-premises.sqlite"),
        "contracts": len(planning_input["contracts"]),
        "premises": len(premises),
    }


class PlanningInputQueries:
    """Bounded read-only interfaces exposed to Repository Planner."""

    def __init__(self, planning_input_path: Path):
        self.path = planning_input_path.resolve()
        self.root = self.path.parent
        self.input = _read_json(self.path)
        if self.input.get("artifact_type") != "repository-planning-input":
            raise ValueError("invalid repository planning input")
        self.contracts = {item["contract_id"]: item for item in self.input["contracts"]}
        self.database = self.root / self.input["parent_premises"]["database"]
        from regen.preparation.premise_database import verify_database
        self.database_version = verify_database(self.database)
        if self.database_version == 2:
            from regen.preparation.premise_database import digest
            if self.input["parent_premises"].get("database_sha256") != digest(self.database):
                raise ValueError("planning input premise binding mismatch")

    def inspect_contracts(self, contract_ids: list[str]) -> list[dict[str, Any]]:
        limit = self.input["planner_interfaces"]["inspect_contracts"]["max_ids"]
        if not contract_ids or len(contract_ids) > limit or len(set(contract_ids)) != len(contract_ids):
            raise ValueError("invalid contract inspection batch")
        try:
            return [self.contracts[key] for key in contract_ids]
        except KeyError as error:
            raise ValueError(f"unknown contract ID: {error.args[0]}") from error

    def search_parent_premises(
        self, query: str, *, context_profile_ids: list[str],
        attributes: list[str] | None = None, limit: int = 20,
    ) -> list[dict[str, Any]]:
        maximum = self.input["planner_interfaces"]["search_parent_premises"]["max_results"]
        if not query.strip() or not 0 < limit <= maximum:
            raise ValueError("invalid premise search")
        allowed = {item["context_profile_id"] for item in self.input["context_profiles"]}
        if not context_profile_ids or not set(context_profile_ids) <= allowed:
            raise ValueError("unknown context profile")
        if self.database_version == 2:
            from regen.execution.proof_state_recall import recall_for_proof_state
            records = recall_for_proof_state(self.database, query, limit=limit, attributes=attributes or ())['records']
            return [{key: record[key] for key in (
                'record_id', 'canonical_name', 'owner_theory', 'owner_session', 'score'
            )} for record in records]
        terms = [item.lower() for item in symbols(query)]
        connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT record_id, canonical_name, owner_theory, owner_session, "
                "attributes_json, context_profile_ids_json, search_text FROM premises"
            ).fetchall()
        finally:
            connection.close()
        required_attributes = set(attributes or [])
        ranked = []
        for row in rows:
            profiles = set(json.loads(row["context_profile_ids_json"]))
            attrs = set(json.loads(row["attributes_json"]))
            if not set(context_profile_ids).intersection(profiles) or not required_attributes <= attrs:
                continue
            haystack = row["search_text"].lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                ranked.append((score, row))
        ranked.sort(key=lambda item: (-item[0], item[1]["record_id"]))
        return [{
            "record_id": row["record_id"],
            "canonical_name": row["canonical_name"],
            "owner_theory": row["owner_theory"],
            "owner_session": row["owner_session"],
            "score": score,
        } for score, row in ranked[:limit]]

    def inspect_parent_premises(self, record_ids: list[str]) -> list[dict[str, Any]]:
        maximum = self.input["planner_interfaces"]["inspect_parent_premises"]["max_ids"]
        if not record_ids or len(record_ids) > maximum or len(set(record_ids)) != len(record_ids):
            raise ValueError("invalid premise inspection batch")
        connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT * FROM premises WHERE record_id IN ("
                + ",".join("?" for _ in record_ids) + ")",
                record_ids,
            ).fetchall()
        finally:
            connection.close()
        by_id = {row["record_id"]: dict(row) for row in rows}
        if set(by_id) != set(record_ids):
            raise ValueError("unknown parent-premise record ID")
        json_fields = {
            "logical_shape_json", "constants_json", "types_json", "attributes_json",
            "definition_equations_json", "locale_context_json", "aliases_json",
            "context_profile_ids_json", "visibility_path_json", "provenance_json",
        }
        result = []
        for record_id in record_ids:
            record = by_id[record_id]
            for key in json_fields.intersection(record):
                record[key.removesuffix("_json")] = json.loads(record.pop(key))
            record.pop("search_text", None)
            result.append(record)
        return result

    def resolve_symbols(self, names: list[str], context_profile_id: str) -> list[dict[str, Any]]:
        if len(names) > self.input["planner_interfaces"]["resolve_symbols"]["max_symbols"]:
            raise ValueError("symbol resolution batch too large")
        if context_profile_id not in {
            item["context_profile_id"] for item in self.input["context_profiles"]
        }:
            raise ValueError("unknown context profile")
        connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                ("SELECT record_id, canonical_name, '[]' FROM premises" if self.database_version == 2 else
                 "SELECT record_id, canonical_name, aliases_json FROM premises")
            ).fetchall()
        finally:
            connection.close()
        result = []
        for name in names:
            matches = sorted({
                record_id for record_id, canonical, aliases in rows
                if name == canonical or name in json.loads(aliases)
            })
            result.append({
                "symbol": name,
                "record_ids": matches,
                "status": "resolved" if len(matches) == 1 else (
                    "ambiguous" if matches else "not-found"
                ),
            })
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--l4v", type=Path, required=True)
    parser.add_argument("--contracts", type=Path, required=True)
    parser.add_argument("--premises", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--architecture", default="ARM")
    args = parser.parse_args()
    print(json.dumps(prepare(
        args.l4v, args.contracts, args.premises, args.output,
        architecture=args.architecture,
    ), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
