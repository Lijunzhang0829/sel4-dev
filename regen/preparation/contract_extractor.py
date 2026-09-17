#!/usr/bin/env python3
"""Trusted extraction of exact Refine contracts and their minimal context."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable

_INITIAL_REPO = Path(__file__).resolve().parents[2]
if str(_INITIAL_REPO) not in sys.path:
    sys.path.insert(0, str(_INITIAL_REPO))

from regen.infrastructure.retrieval_utils import declaration_statement
from regen.preparation.refine_campaign import (
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
    from regen.preparation.regen_extract import is_contract, slice_decls
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
        "contracts": contracts,
    }


def public_package(campaign_id: str, theory: dict[str, Any]) -> dict[str, Any]:
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
        "artifact_type": "contract-package",
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
