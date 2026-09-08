#!/usr/bin/env python3
"""Measure generated-proof use of facts exported by retained semantic commands."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


AUTOMATION_METHOD_RE = re.compile(
    r"\b(?:simp(?:_all)?|auto|clarsimp|fastforce|force|wp)\b"
)
REFINEMENT_SHAPED_RE = re.compile(
    r"(?:^|[._])(?:c?corres|refine|relation)(?:$|[._])", re.IGNORECASE,
)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def retained_records(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        record for record in snapshot.get("records", [])
        if record.get("source_class") == "retained_semantic_export"
    ]


def record_aliases(record: dict[str, Any]) -> set[str]:
    provenance = record.get("provenance", {})
    aliases = {
        record.get("canonical_name", ""),
        record.get("declaration_name", ""),
        provenance.get("validation_name", ""),
        provenance.get("name_hint", ""),
        provenance.get("derivation_name", ""),
    }
    return {alias for alias in aliases if alias and alias != "??.unknown"}


def record_index(
    records: Iterable[dict[str, Any]],
) -> dict[tuple[str, int], list[dict[str, Any]]]:
    index: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        fact_index = int(record.get("fact_index", 0))
        for alias in record_aliases(record):
            index[(alias, fact_index)].append(record)
    return index


def match_dependencies(
    dependencies: dict[str, Any], records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    index = record_index(records)
    matched: dict[str, dict[str, Any]] = {}
    for dependency in dependencies.get("dependencies", []):
        key = (
            str(dependency.get("canonical_name", "")),
            int(dependency.get("fact_index", 0)),
        )
        for record in index.get(key, []):
            matched[record["record_id"]] = record
    return [matched[key] for key in sorted(matched)]


def source_mentions(
    proof_body: str, records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    alias_owners: dict[str, set[str]] = defaultdict(set)
    record_by_id = {record["record_id"]: record for record in records}
    for record in records:
        for alias in record_aliases(record):
            alias_owners[alias].add(record["record_id"])
            short = alias.rsplit(".", 1)[-1]
            if short:
                alias_owners[short].add(record["record_id"])
    mentioned: set[str] = set()
    for alias, owners in alias_owners.items():
        if len(owners) != 1:
            continue
        pattern = rf"(?<![A-Za-z0-9_']){re.escape(alias)}(?![A-Za-z0-9_'])"
        if re.search(pattern, proof_body):
            mentioned.update(owners)
    return [record_by_id[key] for key in sorted(mentioned)]


def automation_memberships(
    snapshot: dict[str, Any], records: list[dict[str, Any]],
) -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    records_by_alias: dict[str, set[str]] = defaultdict(set)
    for record in records:
        for alias in record_aliases(record):
            records_by_alias[alias].add(record["record_id"])
    for entry in snapshot.get("records", []):
        kind = str(entry.get("kind", ""))
        if kind.startswith("simp_"):
            pool = "simp"
        elif kind.startswith("wp_"):
            pool = "wp"
        else:
            continue
        names = {
            str(entry.get("name_hint", "")),
            str(entry.get("derivation_name", "")),
            str(entry.get("congruence_name", "")),
        } - {"", "??.unknown"}
        for name in names:
            for record_id in records_by_alias.get(name, set()):
                index[record_id].add(pool)
    for record in records:
        for attribute in record.get("attributes", []):
            if attribute in {"simp", "wp", "cong"}:
                index[record["record_id"]].add(attribute)
    return index


def summarize_node(
    node: dict[str, Any], proof_body: str, audit: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    dependencies = audit["proof_dependencies"]
    automation = audit["automation_context"]
    if dependencies.get("has_skip_proof"):
        raise ValueError(f"node dependency certificate contains skip proof: {node['id']}")
    matched = match_dependencies(dependencies, records)
    mentioned = source_mentions(proof_body, records)
    memberships = automation_memberships(automation, records)
    uses_automation = bool(AUTOMATION_METHOD_RE.search(proof_body))
    automation_associated = [
        record for record in matched
        if uses_automation and memberships.get(record["record_id"])
    ]

    def compact(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "record_id": record["record_id"],
            "canonical_name": record["canonical_name"],
            "owner_theory": record["owner_theory"],
            "automation_memberships": sorted(
                memberships.get(record["record_id"], set())
            ),
        }

    return {
        "node_id": node["id"],
        "theorem_name": node["name"],
        "proof_dependency_count": len(dependencies.get("dependencies", [])),
        "uses_automation_method": uses_automation,
        "retained_source_mentions": [compact(item) for item in mentioned],
        "retained_certificate_dependencies": [compact(item) for item in matched],
        "retained_automation_associated_dependencies": [
            compact(item) for item in automation_associated
        ],
        "automation_context_id": automation.get("automation_context_id"),
        "dependency_set_id": dependencies.get("dependency_set_id"),
    }


def build_theory_report(
    *, campaign_id: str, semantic_base_seal_id: str, theory: str,
    graph_revision: int, records: list[dict[str, Any]],
    node_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    attributes = Counter(
        attribute for record in records for attribute in record.get("attributes", [])
    )
    certificate_users = [
        node for node in node_reports if node["retained_certificate_dependencies"]
    ]
    automation_users = [
        node for node in node_reports
        if node["retained_automation_associated_dependencies"]
    ]
    source_users = [
        node for node in node_reports if node["retained_source_mentions"]
    ]
    used_records = {
        item["record_id"]: item
        for node in certificate_users
        for item in node["retained_certificate_dependencies"]
    }
    refinement_records = [
        record for record in records
        if REFINEMENT_SHAPED_RE.search(record.get("canonical_name", ""))
    ]
    return {
        "schema_version": 1,
        "artifact_type": "retained-semantic-impact-report",
        "verdict": "pass",
        "campaign_id": campaign_id,
        "semantic_base_seal_id": semantic_base_seal_id,
        "theory": theory,
        "graph_revision": graph_revision,
        "measurement_semantics": {
            "source_mentions": "unique retained fact names found in generated proof text",
            "certificate_dependencies": "retained facts in Isabelle theorem dependencies",
            "automation_associated": (
                "certificate dependencies that are automation-pool members while the "
                "proof invokes a supported automation method; this is association, "
                "not a claim of tactic-internal causality"
            ),
        },
        "retained_surface": {
            "fact_count": len(records),
            "automation_memberships": dict(sorted(attributes.items())),
            "refinement_shaped_fact_count": len(refinement_records),
            "refinement_shaped_facts": sorted(
                record["canonical_name"] for record in refinement_records
            ),
        },
        "actual_impact": {
            "generated_theorem_count": len(node_reports),
            "theorems_with_source_mentions": len(source_users),
            "theorems_with_certificate_dependencies": len(certificate_users),
            "theorems_with_automation_associated_dependencies": len(automation_users),
            "used_retained_fact_count": len(used_records),
            "used_retained_facts": [used_records[key] for key in sorted(used_records)],
            "affected_theorem_ids": sorted(node["node_id"] for node in certificate_users),
        },
        "nodes": node_reports,
    }


def audit_theory(
    *, checker: Any, graph: dict[str, Any], proofs: dict[str, str],
    premise_snapshot_path: Path, output_path: Path,
) -> dict[str, Any]:
    snapshot = read_json(premise_snapshot_path)
    records = retained_records(snapshot)
    check = checker.audit_complete_theory(graph, proofs)
    if check.get("verdict") != "green" or "complete_theory_audit" not in check:
        raise ValueError("retained semantic complete-theory audit failed")
    evidence = check["complete_theory_audit"]
    automation = evidence["automation_context"]
    dependencies = evidence["proof_dependencies"]
    node_reports = []
    for node in graph["nodes"]:
        node_id = node["id"]
        if node_id not in dependencies:
            raise ValueError(f"impact audit lacks dependencies for {node_id}")
        node_reports.append(summarize_node(
            node, proofs[node_id], {
                "automation_context": automation,
                "proof_dependencies": dependencies[node_id],
            }, records,
        ))
    report = build_theory_report(
        campaign_id=snapshot["campaign_id"],
        semantic_base_seal_id=snapshot["semantic_base_seal_id"],
        theory=graph["theory"], graph_revision=int(graph["revision"]),
        records=records, node_reports=node_reports,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    return report


def aggregate_session(
    theory_report_paths: list[Path], output_path: Path,
) -> dict[str, Any]:
    reports = [read_json(path) for path in theory_report_paths]
    if not reports or any(report.get("verdict") != "pass" for report in reports):
        raise ValueError("session impact audit requires PASS theory reports")
    campaigns = {report["campaign_id"] for report in reports}
    seals = {report["semantic_base_seal_id"] for report in reports}
    if len(campaigns) != 1 or len(seals) != 1:
        raise ValueError("theory impact reports do not share one environment identity")
    affected = {
        f"{report['theory']}:{node_id}"
        for report in reports
        for node_id in report["actual_impact"]["affected_theorem_ids"]
    }
    used = {
        item["record_id"]: item
        for report in reports
        for item in report["actual_impact"]["used_retained_facts"]
    }
    result = {
        "schema_version": 1,
        "artifact_type": "session-retained-semantic-impact-report",
        "verdict": "pass",
        "campaign_id": next(iter(campaigns)),
        "semantic_base_seal_id": next(iter(seals)),
        "theory_count": len(reports),
        "generated_theorem_count": sum(
            report["actual_impact"]["generated_theorem_count"] for report in reports
        ),
        "affected_theorem_count": len(affected),
        "affected_theorem_ids": sorted(affected),
        "used_retained_fact_count": len(used),
        "used_retained_facts": [used[key] for key in sorted(used)],
        "theory_reports": [str(path) for path in theory_report_paths],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--theory-report", type=Path, action="append", required=True)
    parser.add_argument("--session-report", type=Path, required=True)
    args = parser.parse_args()
    result = aggregate_session(args.theory_report, args.session_report)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
