#!/usr/bin/env python3
"""M1: extract contracts and fixed semantic declarations from every Refine theory."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from regen.refine_campaign import read_json, write_immutable_json
from regen.refine_contracts import (
    declaration_statement,
    discover_session_theories,
    extract_theory,
    load_contract_policy,
    normalize_statement,
    sha256_path,
    strip_nested_comments,
    strip_symbolic_comments,
)
from regen.semantic_base_pipeline import build_environment_manifest

REPO = Path(__file__).resolve().parent.parent
FIXED_KINDS = {
    "datatype", "record", "type_synonym", "definition", "defs",
    "abbreviation", "consts", "constants", "locale", "syntax", "no_syntax",
    "notation", "no_notation", "translations", "primrec", "fun",
    "inductive", "inductive_set", "coinductive", "coinductive_set",
    "unbundle", "named_theorems",
}
ERASED_KINDS = {
    "lemma", "theorem", "corollary", "proposition", "lemmas",
    "schematic_goal", "declare", "notes",
}
REVIEW_KINDS = {
    "interpretation", "global_interpretation", "sublocale", "instantiation",
    "instance", "class", "typedef", "quotient_type", "function",
    "setup", "local_setup", "ML", "bundle", "axiomatization",
}


def clean_shell(raw: str) -> str:
    value = declaration_statement(raw)
    value = strip_symbolic_comments(strip_nested_comments(value))
    return "\n".join(line.rstrip() for line in value.splitlines() if line.strip())


def declaration_id(theory: str, kind: str, shell: str) -> str:
    digest = hashlib.sha256(normalize_statement(shell).encode()).hexdigest()[:12]
    return f"semantic:{theory}:{kind}:{digest}"


def extract_semantic_theory(
    theory: str, path: Path, *, repo: Path = REPO,
) -> tuple[dict[str, Any], dict[str, Any]]:
    slice_decls, is_contract = load_contract_policy()
    contract_record = extract_theory(
        theory, path, slice_decls=slice_decls, is_contract=is_contract, repo=repo,
    )
    source = path.read_text(encoding="utf-8", errors="replace")
    fixed = []
    review = []
    erased_count = 0
    contract_ids = {item["contract_id"] for item in contract_record["contracts"]}
    for declaration in slice_decls(source):
        if is_contract(declaration):
            continue
        kind = declaration["kind"]
        if kind in FIXED_KINDS:
            shell = clean_shell(declaration["stmt"])
            item_id = declaration_id(theory, kind, shell)
            fixed.append({
                "declaration_id": item_id,
                "kind": kind,
                "name": declaration.get("name"),
                "shell": shell,
                "source_line": declaration["start"] + 1,
            })
        elif kind in ERASED_KINDS:
            erased_count += 1
        elif kind in REVIEW_KINDS:
            shell = clean_shell(declaration["stmt"])
            item_id = declaration_id(theory, kind, shell)
            review.append({
                "review_id": item_id,
                "kind": kind,
                "name": declaration.get("name"),
                "command_shell": shell,
                "source_line": declaration["start"] + 1,
                "resolution": "pending",
            })
    package = {
        "schema_version": 1,
        "artifact_type": "semantic-base-theory-package",
        "campaign_id": "",
        "theory": theory,
        "file_slot": contract_record["file_slot"],
        "fixed_declarations": fixed,
        "contracts": contract_record["contracts"],
        "framework_review_items": review,
        "visibility_assertions": {
            "human_theorem_statements_included": False,
            "human_proof_bodies_included": False,
            "human_automation_registrations_included": False,
        },
    }
    private = {
        "theory": theory,
        "file_slot": contract_record["file_slot"],
        "source_path": contract_record["trusted_source_path"],
        "source_sha256": contract_record["trusted_source_sha256"],
        "fixed_declaration_ids": [item["declaration_id"] for item in fixed],
        "contract_ids": sorted(contract_ids),
        "framework_review_ids": [item["review_id"] for item in review],
        "erased_human_theorem_commands": erased_count,
    }
    return package, private


def extract_all(repo: Path, campaign_id: str, architecture: str = "ARM") -> dict[str, Any]:
    global REPO
    REPO = repo.resolve()
    discovered = discover_session_theories(
        REPO / "verification" / "l4v",
        architecture=architecture,
        session="Refine",
    )
    packages = []
    private_records = []
    for theory, path in discovered:
        package, private = extract_semantic_theory(theory, path, repo=REPO)
        package["campaign_id"] = campaign_id
        packages.append(package)
        private_records.append(private)
    environment = build_environment_manifest(
        campaign_id,
        [{
            "theory": package["theory"],
            "file_slot": package["file_slot"],
            "fixed_declarations": [
                item["declaration_id"] for item in package["fixed_declarations"]
            ],
            "contract_ids": [
                item["contract_id"] for item in package["contracts"]
            ],
            "framework_obligation_ids": [],
        } for package in packages],
    )
    audit = {
        "schema_version": 1,
        "artifact_type": "semantic-base-m1-audit",
        "campaign_id": campaign_id,
        "verdict": "pass",
        "theory_count": len(packages),
        "contract_count": environment["counts"]["contracts"],
        "fixed_declaration_count": environment["counts"]["fixed_declarations"],
        "pending_framework_reviews": sum(
            len(item["framework_review_items"]) for item in packages
        ),
        "erased_human_theorem_commands": sum(
            item["erased_human_theorem_commands"] for item in private_records
        ),
        "legacy_file_categories_emitted": 0,
    }
    return {
        "packages": packages,
        "private_source_inventory": {
            "schema_version": 1,
            "artifact_type": "semantic-base-source-inventory",
            "campaign_id": campaign_id,
            "theories": private_records,
        },
        "environment_manifest": environment,
        "audit": audit,
    }


def write_m1(output: Path, artifacts: dict[str, Any]) -> None:
    write_immutable_json(
        output / "private" / "source-inventory.json",
        artifacts["private_source_inventory"],
    )
    write_immutable_json(
        output / "environment-manifest.json", artifacts["environment_manifest"],
    )
    write_immutable_json(output / "audit.json", artifacts["audit"])
    for package in artifacts["packages"]:
        write_immutable_json(
            output / "public" / "theory-packages" / f"{package['file_slot']}.json",
            package,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--architecture", default="ARM")
    args = parser.parse_args()
    artifacts = extract_all(
        args.repo.resolve(), args.campaign_id, args.architecture,
    )
    write_m1(args.output, artifacts)
    print(json.dumps(artifacts["audit"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
