#!/usr/bin/env python3
"""Trusted M1 extraction and Setting B Proof/Support classification."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable

_INITIAL_REPO = Path(__file__).resolve().parent.parent
if str(_INITIAL_REPO) not in sys.path:
    sys.path.insert(0, str(_INITIAL_REPO))

from regen.refine_campaign import (
    read_json,
    sha256_json,
    verify_frozen_inputs,
    verify_manifest,
    write_immutable_json,
)


REPO = _INITIAL_REPO
L4V = REPO / "verification" / "l4v"
DEFAULT_MANIFEST_ROOT = REPO / "reports" / "regen" / "refine-setting-b"
ARCHITECTURE_DIRS = {"ARM", "ARM_HYP", "AARCH64", "RISCV64", "X64"}
TOP_LEVEL_BOUNDARY_RE = re.compile(
    r"^(?:chapter|section|subsection|subsubsection|paragraph|text|txt|"
    r"class|locale|context|experiment|bundle|unbundle|notation|no_notation|"
    r"syntax|translations|ML|ML_file|setup|local_setup|begin|end)\b"
)
WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_']*")
STATEMENT_CLAUSE_WORDS = {"fixes", "assumes", "defines", "shows", "obtains", "for"}
REMOVABLE_CONTEXT_CLAUSE_WORDS = {"notes", "includes"}
FORBIDDEN_OUTER_WORDS = {
    "using", "including", "unfolding", "supply",
    "by", "apply", "proof", "done", "qed", "sorry", "oops",
}


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_statement(statement: str) -> str:
    return " ".join(statement.split())


def _blank_preserving_newlines(text: str) -> str:
    return "".join("\n" if char == "\n" else " " for char in text)


def strip_nested_comments(source: str) -> str:
    """Remove Isabelle block comments while preserving line numbers."""
    result = list(source)
    depth = 0
    quote = False
    index = 0
    while index < len(source):
        if depth == 0 and source[index] == '"':
            escaped = index > 0 and source[index - 1] == "\\"
            if not escaped:
                quote = not quote
            index += 1
            continue
        if not quote and source.startswith("(*", index):
            depth += 1
            result[index:index + 2] = "  "
            index += 2
            continue
        if depth:
            if source.startswith("(*", index):
                depth += 1
                result[index:index + 2] = "  "
                index += 2
                continue
            if source.startswith("*)", index):
                depth -= 1
                result[index:index + 2] = "  "
                index += 2
                continue
            if result[index] != "\n":
                result[index] = " "
        index += 1
    if depth:
        raise ValueError("unterminated Isabelle block comment in declaration")
    return "".join(result)


def strip_symbolic_comments(source: str) -> str:
    """Remove `\\<comment> \\<open>...\\<close>` document comments."""
    marker = "\\<comment>"
    open_marker = "\\<open>"
    close_marker = "\\<close>"
    result = source
    search_from = 0
    while True:
        start = result.find(marker, search_from)
        if start < 0:
            return result
        cursor = start + len(marker)
        while cursor < len(result) and result[cursor].isspace():
            cursor += 1
        if not result.startswith(open_marker, cursor):
            result = result[:start] + _blank_preserving_newlines(marker) + result[start + len(marker):]
            search_from = start + len(marker)
            continue
        depth = 1
        end = cursor + len(open_marker)
        while end < len(result) and depth:
            if result.startswith(open_marker, end):
                depth += 1
                end += len(open_marker)
            elif result.startswith(close_marker, end):
                depth -= 1
                end += len(close_marker)
            else:
                end += 1
        if depth:
            raise ValueError("unterminated symbolic comment in declaration")
        result = result[:start] + _blank_preserving_newlines(result[start:end]) + result[end:]
        search_from = start


def outer_words(source: str) -> list[tuple[str, int, int]]:
    """Return word tokens outside Isabelle strings and cartouches."""
    words = []
    index = 0
    quote = False
    cartouche_depth = 0
    while index < len(source):
        if source[index] == '"' and not cartouche_depth:
            escaped = index > 0 and source[index - 1] == "\\"
            if not escaped:
                quote = not quote
            index += 1
            continue
        if not quote and source.startswith("\\<open>", index):
            cartouche_depth += 1
            index += len("\\<open>")
            continue
        if not quote and cartouche_depth and source.startswith("\\<close>", index):
            cartouche_depth -= 1
            index += len("\\<close>")
            continue
        if not quote and not cartouche_depth:
            match = WORD_RE.match(source, index)
            if match:
                words.append((match.group(0), match.start(), match.end()))
                index = match.end()
                continue
        index += 1
    if quote or cartouche_depth:
        raise ValueError("unterminated Isabelle string or cartouche in declaration")
    return words


def _matching_parenthesis(source: str, start: int) -> int:
    depth = 0
    quote = False
    cartouche_depth = 0
    index = start
    while index < len(source):
        if source[index] == '"' and not cartouche_depth:
            escaped = index > 0 and source[index - 1] == "\\"
            if not escaped:
                quote = not quote
            index += 1
            continue
        if not quote and source.startswith("\\<open>", index):
            cartouche_depth += 1
            index += len("\\<open>")
            continue
        if not quote and cartouche_depth and source.startswith("\\<close>", index):
            cartouche_depth -= 1
            index += len("\\<close>")
            continue
        if not quote and not cartouche_depth:
            if source[index] == "(":
                depth += 1
            elif source[index] == ")":
                depth -= 1
                if depth == 0:
                    return index + 1
        index += 1
    raise ValueError("unterminated parenthesized theorem pattern")


def remove_is_patterns(source: str) -> str:
    result = source
    while True:
        target = None
        for word, start, _end in outer_words(result):
            if word != "is":
                continue
            left = start - 1
            while left >= 0 and result[left].isspace():
                left -= 1
            if left >= 0 and result[left] == "(":
                target = (left, _matching_parenthesis(result, left))
                break
        if target is None:
            return result
        start, end = target
        result = result[:start] + _blank_preserving_newlines(result[start:end]) + result[end:]


def remove_context_blocks(source: str) -> str:
    """Remove declaration context clauses without dropping later propositions."""
    result = source
    while True:
        words = outer_words(result)
        context_index = next(
            (index for index, item in enumerate(words)
             if item[0] in REMOVABLE_CONTEXT_CLAUSE_WORDS),
            None,
        )
        if context_index is None:
            return result
        _word, start, _end = words[context_index]
        end = len(result)
        for word, next_start, _next_end in words[context_index + 1:]:
            if (
                word in STATEMENT_CLAUSE_WORDS
                or word in REMOVABLE_CONTEXT_CLAUSE_WORDS
                or word in FORBIDDEN_OUTER_WORDS
            ):
                end = next_start
                break
        result = result[:start] + _blank_preserving_newlines(result[start:end]) + result[end:]


def sanitize_contract_statement(raw: str) -> str:
    """Keep theorem clauses and proposition; remove human proof-context commands."""
    result = strip_nested_comments(raw)
    result = strip_symbolic_comments(result)
    result = remove_is_patterns(result)
    result = remove_context_blocks(result)
    forbidden = [item for item in outer_words(result) if item[0] in FORBIDDEN_OUTER_WORDS]
    if forbidden:
        result = result[:min(item[1] for item in forbidden)]
    lines = []
    for line in result.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        stripped = line.lstrip()
        first = WORD_RE.match(stripped)
        if first and first.group(0) in STATEMENT_CLAUSE_WORDS:
            line = stripped
        lines.append(line)
    result = "\n".join(lines).strip()
    if not result:
        raise ValueError("contract sanitizer produced an empty declaration")
    return result


def forbidden_statement_evidence(statement: str) -> list[str]:
    evidence = []
    if "(*" in statement or "*)" in statement:
        evidence.append("block-comment")
    if "\\<comment>" in statement:
        evidence.append("symbolic-comment")
    for word, start, _end in outer_words(statement):
        if word in REMOVABLE_CONTEXT_CLAUSE_WORDS or word in FORBIDDEN_OUTER_WORDS:
            evidence.append(f"outer-command:{word}@{start}")
        if word == "is":
            left = start - 1
            while left >= 0 and statement[left].isspace():
                left -= 1
            if left >= 0 and statement[left] == "(":
                evidence.append(f"is-pattern@{left}")
    return evidence


def declaration_statement(raw: str) -> str:
    lines = raw.splitlines()
    kept = []
    for index, line in enumerate(lines):
        if index and line == line.lstrip() and TOP_LEVEL_BOUNDARY_RE.match(line):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def contract_contexts_by_line(source: str) -> dict[int, list[str]]:
    """Map source lines to the minimal enclosing Isabelle `context` commands."""
    clean = strip_symbolic_comments(strip_nested_comments(source))
    active: list[str] = []
    pending: list[str] | None = None
    result: dict[int, list[str]] = {}
    for line_number, line in enumerate(clean.splitlines()):
        stripped = line.strip()
        if pending is None and stripped == "end" and active:
            active.pop()
        result[line_number] = list(active)
        if pending is not None:
            pending.append(line.rstrip())
            block = "\n".join(pending)
            if any(word == "begin" for word, _start, _end in outer_words(block)):
                active.append(block)
                pending = None
            continue
        if re.match(r"^context\b", stripped):
            pending = [line.rstrip()]
            block = "\n".join(pending)
            if any(word == "begin" for word, _start, _end in outer_words(block)):
                active.append(block)
                pending = None
    if pending is not None:
        raise ValueError("unterminated context command in theory source")
    return result


def sanitize_contract_context(raw: str) -> str:
    """Keep a context opener but reject proof commands or unrelated declarations."""
    clean = strip_symbolic_comments(strip_nested_comments(raw)).strip()
    words = outer_words(clean)
    if not words or words[0][0] != "context" or not any(word == "begin" for word, _, _ in words):
        raise ValueError(f"invalid contract context opener: {clean!r}")
    forbidden = REMOVABLE_CONTEXT_CLAUSE_WORDS | FORBIDDEN_OUTER_WORDS | {
        "lemma", "theorem", "corollary", "lemmas", "definition", "declare",
        "method", "imports", "ML", "setup",
    }
    hits = [word for word, _start, _end in words if word in forbidden]
    if hits:
        raise ValueError(f"forbidden command in contract context opener: {hits}")
    return "\n".join(line.rstrip() for line in clean.splitlines() if line.strip())


def load_contract_policy():
    scripts = REPO / "regen" / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        from regen_extract import is_contract, slice_decls
    finally:
        sys.path.pop(0)
    return slice_decls, is_contract


def load_session_parser():
    parser_dir = REPO / "tools" / "lemma_inventory"
    sys.path.insert(0, str(parser_dir))
    try:
        import parse_roots
    finally:
        sys.path.pop(0)
    return parse_roots


def discover_session_theories(
    l4v: Path, *, architecture: str, session: str,
) -> list[tuple[str, Path]]:
    parse_roots = load_session_parser()
    parsed = parse_roots.parse_all_roots(l4v, arch=architecture)
    records = []
    seen_names: dict[str, Path] = {}
    for path in sorted(l4v.rglob("*.thy")):
        relative_parts = path.relative_to(l4v).parts
        architecture_parts = ARCHITECTURE_DIRS.intersection(relative_parts)
        if architecture_parts and architecture not in architecture_parts:
            continue
        if parse_roots.assign_session_for_thy(path, parsed) != session:
            continue
        name = f"{session}.{path.stem}"
        if name in seen_names:
            raise ValueError(f"duplicate theory identity {name}: {seen_names[name]} and {path}")
        seen_names[name] = path
        records.append((name, path))
    if not records:
        raise ValueError(f"no theories discovered for session {session}")
    return records


def attribute_names(raw: str) -> list[str]:
    if not raw:
        return []
    body = raw.strip()
    if body.startswith("[") and body.endswith("]"):
        body = body[1:-1]
    return [item for item in re.split(r"[\s,]+", body) if item]


def extract_theory(
    theory: str,
    path: Path,
    *,
    slice_decls: Callable[[str], list[dict[str, Any]]],
    is_contract: Callable[[dict[str, Any]], bool],
    repo: Path | None = None,
) -> dict[str, Any]:
    repo = (repo or REPO).resolve()
    source = path.read_text(encoding="utf-8", errors="replace")
    contexts = contract_contexts_by_line(source)
    contracts = []
    for declaration in slice_decls(source):
        if not is_contract(declaration):
            continue
        trusted_raw_statement = declaration_statement(declaration["stmt"])
        statement = sanitize_contract_statement(trusted_raw_statement)
        statement_hash = hashlib.sha256(
            normalize_statement(statement).encode("utf-8")
        ).hexdigest()
        start_line = declaration["start"] + 1
        contract_context = [
            sanitize_contract_context(opener)
            for opener in contexts.get(declaration["start"], [])
        ]
        context_hash = hashlib.sha256(
            normalize_statement("\n".join(contract_context)).encode("utf-8")
        ).hexdigest()
        semantic_hash = hashlib.sha256(
            (context_hash + "\0" + statement_hash).encode("utf-8")
        ).hexdigest()
        end_line = start_line + trusted_raw_statement.count("\n")
        contracts.append({
            "contract_id": f"contract:{theory}:{declaration['name']}:{semantic_hash[:12]}",
            "name": declaration["name"],
            "kind": declaration["kind"],
            "exact_statement": statement,
            "normalized_statement_sha256": statement_hash,
            "contract_owned_context": contract_context,
            "contract_context_sha256": context_hash,
            "contract_semantic_sha256": semantic_hash,
            "trusted_raw_statement_sha256": hashlib.sha256(
                trusted_raw_statement.encode("utf-8")
            ).hexdigest(),
            "owner_theory": theory,
            "owner_file_slot": f"{path.stem}.thy",
            "contract_owned_attributes_raw": declaration["attr"],
            "contract_owned_attributes": attribute_names(declaration["attr"]),
            "source_location": {
                "path": str(path.resolve().relative_to(repo)),
                "start_line": start_line,
                "end_line": end_line,
            },
        })
    return {
        "theory": theory,
        "file_slot": f"{path.stem}.thy",
        "trusted_source_path": str(path.resolve().relative_to(repo)),
        "trusted_source_sha256": sha256_path(path),
        "classification": "Proof" if contracts else "Support",
        "contracts": contracts,
    }


def public_package(campaign_id: str, theory: dict[str, Any]) -> dict[str, Any]:
    if theory["classification"] != "Proof":
        raise ValueError("public proof package requested for Support theory")
    contracts = []
    for contract in sorted(theory["contracts"], key=lambda item: item["contract_id"]):
        contracts.append({key: contract[key] for key in (
            "contract_id", "name", "kind", "exact_statement",
            "normalized_statement_sha256", "owner_theory", "owner_file_slot",
            "contract_owned_attributes_raw", "contract_owned_attributes",
            "contract_owned_context", "contract_context_sha256",
            "contract_semantic_sha256",
        )})
    package = {
        "schema_version": 1,
        "artifact_type": "proof-file-package",
        "campaign_id": campaign_id,
        "stage": "Refine",
        "theory": theory["theory"],
        "file_slot": theory["file_slot"],
        "contracts": contracts,
        "contract_order_policy": "semantic-contract-id-order-not-source-order",
        "visibility_assertions": {
            "human_imports_included": False,
            "human_body_included": False,
            "human_helpers_included": False,
            "human_source_locations_included": False,
        },
        "semantic_context_policy": "minimal-enclosing-context-openers-only",
    }
    package["package_payload_sha256"] = sha256_json(package)
    return package


def validate_extraction(theories: list[dict[str, Any]], packages: list[dict[str, Any]]) -> None:
    theory_names = [item["theory"] for item in theories]
    if len(theory_names) != len(set(theory_names)):
        raise ValueError("theory identities are not unique")
    private_contracts = [contract for theory in theories for contract in theory["contracts"]]
    private_ids = [item["contract_id"] for item in private_contracts]
    if len(private_ids) != len(set(private_ids)):
        raise ValueError("contract IDs are not unique")
    public_contracts = [contract for package in packages for contract in package["contracts"]]
    public_ids = [item["contract_id"] for item in public_contracts]
    if Counter(private_ids) != Counter(public_ids):
        raise ValueError("public packages do not cover private contracts exactly once")
    for package in packages:
        forbidden = {"source_location", "trusted_source_path", "trusted_source_sha256", "imports", "proof"}
        if forbidden & package.keys():
            raise ValueError(f"private field leaked into package {package['theory']}")
        for contract in package["contracts"]:
            if forbidden & contract.keys():
                raise ValueError(f"private contract field leaked in {package['theory']}")
            actual = hashlib.sha256(
                normalize_statement(contract["exact_statement"]).encode("utf-8")
            ).hexdigest()
            if actual != contract["normalized_statement_sha256"]:
                raise ValueError(f"statement hash mismatch: {contract['contract_id']}")
            context = contract["contract_owned_context"]
            actual_context = hashlib.sha256(
                normalize_statement("\n".join(context)).encode("utf-8")
            ).hexdigest()
            if actual_context != contract["contract_context_sha256"]:
                raise ValueError(f"context hash mismatch: {contract['contract_id']}")
        contract_ids = [item["contract_id"] for item in package["contracts"]]
        if contract_ids != sorted(contract_ids):
            raise ValueError(f"public contract order leaks source order: {package['theory']}")


def extraction_audit(
    campaign_id: str,
    theories: list[dict[str, Any]],
    packages: list[dict[str, Any]],
) -> dict[str, Any]:
    private_ids = [
        contract["contract_id"] for theory in theories for contract in theory["contracts"]
    ]
    public_contracts = [contract for package in packages for contract in package["contracts"]]
    public_ids = [contract["contract_id"] for contract in public_contracts]
    command_leak_hits = {
        contract["contract_id"]: forbidden_statement_evidence(contract["exact_statement"])
        for contract in public_contracts
        if forbidden_statement_evidence(contract["exact_statement"])
    }
    context_leak_hits = {}
    for contract in public_contracts:
        for opener in contract["contract_owned_context"]:
            try:
                sanitize_contract_context(opener)
            except ValueError as error:
                context_leak_hits.setdefault(contract["contract_id"], []).append(str(error))
    forbidden_fields = {
        "source_location", "trusted_source_path", "trusted_source_sha256",
        "imports", "proof", "source_path", "source_line",
    }
    forbidden_field_hits = []
    for package in packages:
        for field in sorted(forbidden_fields.intersection(package)):
            forbidden_field_hits.append(f"{package['theory']}:{field}")
        for contract in package["contracts"]:
            for field in sorted(forbidden_fields.intersection(contract)):
                forbidden_field_hits.append(f"{contract['contract_id']}:{field}")
    checks = {
        "private_contract_ids_unique": len(private_ids) == len(set(private_ids)),
        "public_contract_ids_unique": len(public_ids) == len(set(public_ids)),
        "public_contracts_cover_private_exactly_once": Counter(private_ids) == Counter(public_ids),
        "proof_packages_equal_proof_theories": len(packages) == sum(
            theory["classification"] == "Proof" for theory in theories
        ),
        "trusted_source_paths_unique": len(theories) == len({
            theory["trusted_source_path"] for theory in theories
        }),
        "all_trusted_sources_hashed": all(
            len(theory["trusted_source_sha256"]) == 64 for theory in theories
        ),
        "public_command_or_comment_leak_hits": command_leak_hits,
        "public_context_leak_hits": context_leak_hits,
        "public_forbidden_field_hits": forbidden_field_hits,
        "all_visibility_assertions_false": all(
            not any(package["visibility_assertions"].values()) for package in packages
        ),
        "public_contracts_use_semantic_order": all(
            [item["contract_id"] for item in package["contracts"]]
            == sorted(item["contract_id"] for item in package["contracts"])
            for package in packages
        ),
    }
    verdict = "pass" if (
        all(value is True for value in checks.values() if isinstance(value, bool))
        and not command_leak_hits
        and not context_leak_hits
        and not forbidden_field_hits
    ) else "fail"
    return {
        "schema_version": 1,
        "artifact_type": "m1-extraction-audit",
        "campaign_id": campaign_id,
        "stage": "Refine",
        "verdict": verdict,
        "checks": checks,
    }


def extract_m1(repo: Path, manifest_path: Path) -> dict[str, Any]:
    global REPO, L4V
    REPO = repo.resolve()
    L4V = REPO / "verification" / "l4v"
    manifest = read_json(manifest_path)
    verify_manifest(manifest)
    verify_frozen_inputs(REPO, manifest)
    if manifest["setting"] != "B" or manifest["target_stages"][0]["stage"] != "Refine":
        raise ValueError("M1 requires a Setting B Refine campaign")

    selector_record = next(
        (item for item in manifest["frozen_inputs"] if item["path"] == "regen/scripts/regen_extract.py"),
        None,
    )
    selector_path = REPO / "regen" / "scripts" / "regen_extract.py"
    if selector_record is None or selector_record["sha256"] != sha256_path(selector_path):
        raise ValueError("contract selector no longer matches the M0 frozen input")

    slice_decls, is_contract = load_contract_policy()
    discovered = discover_session_theories(
        L4V, architecture=manifest["architecture"], session="Refine",
    )
    theories = [
        extract_theory(name, path, slice_decls=slice_decls, is_contract=is_contract)
        for name, path in discovered
    ]
    packages = [public_package(manifest["campaign_id"], item) for item in theories if item["classification"] == "Proof"]
    validate_extraction(theories, packages)
    audit = extraction_audit(manifest["campaign_id"], theories, packages)
    if audit["verdict"] != "pass":
        raise ValueError(f"M1 extraction audit failed: {audit['checks']}")

    contracts = [contract for theory in theories for contract in theory["contracts"]]
    source_inventory = [{
        "theory": item["theory"],
        "file_slot": item["file_slot"],
        "classification": item["classification"],
        "source_path": item["trusted_source_path"],
        "source_sha256": item["trusted_source_sha256"],
    } for item in theories]
    private_manifest = {
        "schema_version": 1,
        "artifact_type": "private-contract-manifest",
        "campaign_id": manifest["campaign_id"],
        "stage": "Refine",
        "trusted_stage": True,
        "original_proof_bodies_read": True,
        "model_visible": False,
        "contract_policy": manifest["contract_selection_policy"],
        "extractor_sha256": sha256_path(Path(__file__)),
        "theory_source_count": len(theories),
        "trusted_source_inventory_sha256": sha256_json(source_inventory),
        "trusted_source_inventory": source_inventory,
        "contract_count": len(contracts),
        "contracts_sha256": sha256_json(contracts),
        "contracts": contracts,
    }
    classification = {
        "schema_version": 1,
        "artifact_type": "proof-support-classification",
        "campaign_id": manifest["campaign_id"],
        "stage": "Refine",
        "rule": "Proof iff the frozen selector finds at least one target contract; otherwise Support",
        "proof_theory_count": sum(item["classification"] == "Proof" for item in theories),
        "support_theory_count": sum(item["classification"] == "Support" for item in theories),
        "contract_count": len(contracts),
        "theories": [{
            "theory": item["theory"],
            "file_slot": item["file_slot"],
            "classification": item["classification"],
            "contract_count": len(item["contracts"]),
        } for item in theories],
    }
    classification["classification_payload_sha256"] = sha256_json(classification)
    support_inventory = {
        "schema_version": 1,
        "artifact_type": "frozen-support-inventory",
        "campaign_id": manifest["campaign_id"],
        "stage": "Refine",
        "theories": [{
            "theory": item["theory"],
            "file_slot": item["file_slot"],
            "source_path": item["trusted_source_path"],
            "source_sha256": item["trusted_source_sha256"],
        } for item in theories if item["classification"] == "Support"],
    }
    support_inventory["inventory_payload_sha256"] = sha256_json(support_inventory)
    forbidden_inventory = {
        "schema_version": 1,
        "artifact_type": "private-proof-source-forbidden-inventory",
        "campaign_id": manifest["campaign_id"],
        "stage": "Refine",
        "model_visible": False,
        "policy": manifest["forbidden_path_policy"],
        "exact_proof_sources": [{
            "theory": item["theory"],
            "source_path": item["trusted_source_path"],
            "source_sha256": item["trusted_source_sha256"],
        } for item in theories if item["classification"] == "Proof"],
    }
    forbidden_inventory["inventory_payload_sha256"] = sha256_json(forbidden_inventory)
    return {
        "private_manifest": private_manifest,
        "classification": classification,
        "support_inventory": support_inventory,
        "forbidden_inventory": forbidden_inventory,
        "packages": packages,
        "audit": audit,
    }


def write_m1(campaign_dir: Path, artifacts: dict[str, Any]) -> list[tuple[Path, str]]:
    root = campaign_dir / "M1"
    outputs = [
        (root / "private" / "contract-manifest.json", artifacts["private_manifest"]),
        (root / "private" / "proof-support-classification.json", artifacts["classification"]),
        (root / "private" / "proof-source-forbidden-inventory.json", artifacts["forbidden_inventory"]),
        (root / "support-inventory.json", artifacts["support_inventory"]),
        (root / "audit.json", artifacts["audit"]),
    ]
    for package in artifacts["packages"]:
        outputs.append((root / "public" / "proof-file-packages" / f"{package['file_slot']}.json", package))
    results = [(path, write_immutable_json(path, value)) for path, value in outputs]
    records = [{
        "path": str(path.relative_to(root)),
        "sha256": sha256_path(path),
        "size_bytes": path.stat().st_size,
    } for path, _ in sorted(results, key=lambda item: str(item[0]))]
    seal = {
        "schema_version": 1,
        "artifact_type": "m1-artifact-seal",
        "campaign_id": artifacts["private_manifest"]["campaign_id"],
        "stage": "Refine",
        "payload_artifact_count": len(records),
        "proof_package_count": len(artifacts["packages"]),
        "contract_count": artifacts["private_manifest"]["contract_count"],
        "artifacts_payload_sha256": sha256_json(records),
        "artifacts": records,
    }
    seal["seal_payload_sha256"] = sha256_json(seal)
    seal_path = root / "artifact-seal.json"
    results.append((seal_path, write_immutable_json(seal_path, seal)))
    return results


def verify_embedded_payload_hash(value: dict[str, Any], field: str) -> None:
    expected = value.get(field)
    payload = dict(value)
    payload.pop(field, None)
    if expected != sha256_json(payload):
        raise ValueError(f"M1 payload hash mismatch: {value.get('artifact_type')}")


def verify_m1_artifact_seal(root: Path, private_manifest: dict[str, Any]) -> dict[str, Any]:
    seal = read_json(root / "artifact-seal.json")
    verify_embedded_payload_hash(seal, "seal_payload_sha256")
    if seal.get("campaign_id") != private_manifest.get("campaign_id"):
        raise ValueError("M1 artifact seal belongs to a different campaign")

    records = seal.get("artifacts")
    if not isinstance(records, list) or sha256_json(records) != seal.get("artifacts_payload_sha256"):
        raise ValueError("M1 artifact record set hash mismatch")
    if len(records) != seal.get("payload_artifact_count"):
        raise ValueError("M1 artifact seal count mismatch")
    recorded_paths = [item.get("path") for item in records]
    if any(not isinstance(path, str) for path in recorded_paths) or len(recorded_paths) != len(set(recorded_paths)):
        raise ValueError("M1 artifact seal paths are invalid or duplicated")

    classification = read_json(root / "private" / "proof-support-classification.json")
    verify_embedded_payload_hash(classification, "classification_payload_sha256")
    proof_slots = sorted(
        item["file_slot"] for item in classification["theories"]
        if item["classification"] == "Proof"
    )
    expected_paths = {
        "private/contract-manifest.json",
        "private/proof-support-classification.json",
        "private/proof-source-forbidden-inventory.json",
        "support-inventory.json",
        "audit.json",
        *(f"public/proof-file-packages/{slot}.json" for slot in proof_slots),
    }
    if set(recorded_paths) != expected_paths:
        missing = sorted(expected_paths - set(recorded_paths))
        extra = sorted(set(recorded_paths) - expected_paths)
        raise ValueError(f"M1 artifact coverage mismatch: missing={missing}, extra={extra}")

    for record in records:
        path = root / record["path"]
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"M1 sealed artifact is missing or not regular: {record['path']}")
        if path.stat().st_size != record.get("size_bytes") or sha256_path(path) != record.get("sha256"):
            raise ValueError(f"M1 sealed artifact drift: {record['path']}")

    if seal.get("proof_package_count") != classification.get("proof_theory_count"):
        raise ValueError("M1 sealed Proof-package count mismatch")
    if seal.get("contract_count") != private_manifest.get("contract_count"):
        raise ValueError("M1 sealed contract count mismatch")
    contracts = private_manifest.get("contracts")
    if not isinstance(contracts, list) or sha256_json(contracts) != private_manifest.get("contracts_sha256"):
        raise ValueError("M1 private contract payload hash mismatch")

    public_contract_ids = []
    for slot in proof_slots:
        package = read_json(root / "public" / "proof-file-packages" / f"{slot}.json")
        verify_embedded_payload_hash(package, "package_payload_sha256")
        public_contract_ids.extend(item["contract_id"] for item in package.get("contracts", []))
    private_contract_ids = [item["contract_id"] for item in contracts]
    if Counter(public_contract_ids) != Counter(private_contract_ids):
        raise ValueError("M1 sealed public contracts do not cover private contracts exactly once")

    return {
        "payload_artifacts_verified": len(records),
        "proof_packages_verified": len(proof_slots),
        "contracts_verified": len(private_contract_ids),
    }


def verify_source_records(repo: Path, inventory: list[dict[str, Any]]) -> None:
    for record in inventory:
        path = repo / record["source_path"]
        if not path.is_file() or sha256_path(path) != record["source_sha256"]:
            raise ValueError(f"M1 trusted source drift: {record['source_path']}")


def verify_m1_reconstruction(
    repo: Path, campaign_dir: Path, manifest_path: Path,
) -> dict[str, int]:
    reconstructed = extract_m1(repo, manifest_path)
    root = campaign_dir / "M1"
    expected = {
        "private/contract-manifest.json": reconstructed["private_manifest"],
        "private/proof-support-classification.json": reconstructed["classification"],
        "private/proof-source-forbidden-inventory.json": reconstructed["forbidden_inventory"],
        "support-inventory.json": reconstructed["support_inventory"],
        "audit.json": reconstructed["audit"],
    }
    expected.update({
        f"public/proof-file-packages/{package['file_slot']}.json": package
        for package in reconstructed["packages"]
    })
    for relative, value in expected.items():
        path = root / relative
        if not path.is_file() or read_json(path) != value:
            raise ValueError(f"M1 artifact does not reconstruct from trusted sources: {relative}")
    actual_json_paths = {
        str(path.relative_to(root))
        for path in root.rglob("*.json")
        if path.name != "artifact-seal.json"
    }
    if actual_json_paths != set(expected):
        raise ValueError("M1 reconstructed artifact set differs from materialized artifacts")
    return {
        "reconstructed_artifacts_verified": len(expected),
        "reconstructed_contracts_verified": reconstructed["private_manifest"]["contract_count"],
    }


def verify_m1_sources(repo: Path, private_manifest_path: Path) -> dict[str, Any]:
    private_manifest = read_json(private_manifest_path)
    campaign_dir = private_manifest_path.parent.parent.parent
    m0_path = campaign_dir / "M0" / "campaign-manifest.json"
    manifest = read_json(m0_path)
    verify_manifest(manifest)
    verify_frozen_inputs(repo, manifest)
    if private_manifest.get("campaign_id") != manifest["campaign_id"]:
        raise ValueError("M1 private manifest belongs to a different campaign")
    inventory = private_manifest.get("trusted_source_inventory")
    if not isinstance(inventory, list) or len(inventory) != private_manifest.get("theory_source_count"):
        raise ValueError("M1 trusted source inventory is missing or incomplete")
    if sha256_json(inventory) != private_manifest.get("trusted_source_inventory_sha256"):
        raise ValueError("M1 trusted source inventory hash mismatch")
    verify_source_records(repo, inventory)
    reconstruction = verify_m1_reconstruction(
        repo, campaign_dir, m0_path,
    )
    artifact_verification = verify_m1_artifact_seal(campaign_dir / "M1", private_manifest)
    return {
        "campaign_id": manifest["campaign_id"],
        "trusted_sources_verified": len(inventory),
        **reconstruction,
        **artifact_verification,
        "verdict": "pass",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--manifest", type=Path, help="run trusted M1 extraction from M0")
    action.add_argument("--verify", type=Path, help="verify M1 private source inventory")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = args.repo.resolve()
    if args.verify:
        private_path = args.verify if args.verify.is_absolute() else repo / args.verify
        print(json.dumps(verify_m1_sources(repo, private_path), indent=2, sort_keys=True))
        return 0
    manifest_path = args.manifest if args.manifest.is_absolute() else repo / args.manifest
    artifacts = extract_m1(repo, manifest_path)
    campaign_dir = manifest_path.parent.parent
    results = write_m1(campaign_dir, artifacts)
    summary = {
        "campaign_id": artifacts["private_manifest"]["campaign_id"],
        "theories": artifacts["private_manifest"]["theory_source_count"],
        "proof_theories": artifacts["classification"]["proof_theory_count"],
        "support_theories": artifacts["classification"]["support_theory_count"],
        "contracts": artifacts["private_manifest"]["contract_count"],
        "created": sum(status == "created" for _, status in results),
        "unchanged": sum(status == "unchanged" for _, status in results),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
