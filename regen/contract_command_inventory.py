#!/usr/bin/env python3
"""Extract fixed contracts and a source-complete Refine command inventory.

This is a trusted preparation module.  Raw command text and source locations are
private inputs to Semantic Base construction and are never planner/prover input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from typing import Any, Iterable

from regen.refine_campaign import write_immutable_json
from regen.contract_extractor import (
    discover_session_theories,
    extract_theory,
    load_contract_policy,
    normalize_statement,
    public_package,
    sha256_path,
)


SEMANTIC_COMMANDS = {
    "abbreviation", "axiomatization", "bundle", "class", "coinductive",
    "coinductive_set", "consts", "constants", "datatype", "definition",
    "defs", "fun", "function", "global_interpretation", "inductive",
    "inductive_set", "instance", "instantiation", "interpretation",
    "locale", "local_setup", "ML", "ML_file", "named_theorems",
    "no_notation", "no_syntax", "notation", "primrec", "quotient_type",
    "record", "requalify_facts", "requalify_types", "setup", "sublocale",
    "syntax", "termination", "translations", "type_synonym", "typedef",
    "unbundle",
}

ORDINARY_PROOF_COMMANDS = {
    "add_upd_simps", "corollary", "crunch", "crunches", "crunch_ignore",
    "declare", "lemma", "lemmas", "method", "notes", "proposition",
    "schematic_goal", "theorem",
}

STRUCTURAL_COMMANDS = {
    "begin", "chapter", "context", "end", "experiment", "global_naming",
    "paragraph", "section", "subsection", "subsubsection", "text", "txt",
}

COMMAND_KINDS = SEMANTIC_COMMANDS | ORDINARY_PROOF_COMMANDS | STRUCTURAL_COMMANDS
COMMAND_START_RE = re.compile(
    r"^(?:(private|local|global)\s+)?("
    + "|".join(sorted(map(re.escape, COMMAND_KINDS), key=len, reverse=True))
    + r")\b"
)
THEORY_HEADER_RE = re.compile(r"\btheory\s+(?:\"[^\"]+\"|\S+)\s+imports\s+.*?\s+begin\b", re.S)
PROOF_BODY_START_RE = re.compile(
    r"^(?:by|apply|proof|qed|done|oops|sorry|using|including|unfolding|supply)\b"
)
NAME_RE = re.compile(
    r"^(?:(?:private|local|global)\s+)?(?:lemma|theorem|corollary|proposition|"
    r"lemmas|definition|defs|abbreviation|primrec|fun|function|inductive|"
    r"inductive_set|coinductive|coinductive_set|schematic_goal|record|datatype|"
    r"type_synonym|locale|class|named_theorems)\s+([A-Za-z0-9_']+)"
)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _outer_line_mask(source: str) -> list[bool]:
    """Return whether each line starts outside comments, strings and cartouches."""
    result: list[bool] = []
    comment_depth = 0
    in_string = False
    cartouche_depth = 0
    unicode_cartouche_depth = 0
    escaped = False
    lines = source.splitlines(keepends=True)
    for line in lines:
        result.append(
            comment_depth == 0 and not in_string
            and cartouche_depth == 0 and unicode_cartouche_depth == 0
        )
        index = 0
        while index < len(line):
            if comment_depth:
                if line.startswith("(*", index):
                    comment_depth += 1
                    index += 2
                elif line.startswith("*)", index):
                    comment_depth -= 1
                    index += 2
                else:
                    index += 1
                continue
            if cartouche_depth:
                if line.startswith("\\<open>", index):
                    cartouche_depth += 1
                    index += 7
                elif line.startswith("\\<close>", index):
                    cartouche_depth -= 1
                    index += 8
                else:
                    index += 1
                continue
            char = line[index]
            if unicode_cartouche_depth:
                if char == "\u2039":
                    unicode_cartouche_depth += 1
                elif char == "\u203a":
                    unicode_cartouche_depth -= 1
                index += 1
                continue
            if in_string:
                if char == '"' and not escaped:
                    in_string = False
                escaped = char == "\\" and not escaped
                if char != "\\":
                    escaped = False
                index += 1
                continue
            if line.startswith("(*", index):
                comment_depth = 1
                index += 2
            elif line.startswith("\\<open>", index):
                cartouche_depth = 1
                index += 7
            elif char == "\u2039":
                unicode_cartouche_depth = 1
                index += 1
            elif char == '"':
                in_string = True
                escaped = False
                index += 1
            else:
                index += 1
    return result


def _contains_internal_proof_syntax(raw: str) -> bool:
    outer = _outer_line_mask(raw)
    return any(
        outer[index] and PROOF_BODY_START_RE.match(line.lstrip())
        for index, line in enumerate(raw.splitlines(keepends=True))
    )


def _command_name(raw: str, kind: str, slice_decls) -> str | None:
    normalized = re.sub(r"^(?:private|local|global)\s+", "", raw.lstrip(), count=1)
    declarations = slice_decls(normalized)
    if declarations and declarations[0]["kind"] == kind:
        return declarations[0].get("name")
    match = NAME_RE.match(normalized)
    return match.group(1) if match else None


def command_occurrences(
    source: str, theory: str, *, contract_starts: dict[int, dict[str, Any]], slice_decls,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Split one theory into stable, source-ordered outer command occurrences."""
    header_match = THEORY_HEADER_RE.search(source)
    if not header_match:
        raise ValueError(f"cannot parse theory header: {theory}")
    lines = source.splitlines(keepends=True)
    outer = _outer_line_mask(source)
    starts: list[tuple[int, str | None, str]] = []
    for index, line in enumerate(lines):
        if not outer[index]:
            continue
        match = COMMAND_START_RE.match(line.lstrip())
        if not match:
            continue
        kind = match.group(2)
        if kind == "begin" and sum(len(item) for item in lines[:index]) < header_match.end():
            continue
        starts.append((index, match.group(1), kind))

    header_end_line = source[:header_match.end()].count("\n") + 1
    starts = [item for item in starts if item[0] + 1 >= header_end_line]
    occurrences: list[dict[str, Any]] = []
    for ordinal, (start, modifier, kind) in enumerate(starts):
        end = starts[ordinal + 1][0] if ordinal + 1 < len(starts) else len(lines)
        raw = "".join(lines[start:end])
        digest = sha256_text(raw)
        contract = contract_starts.get(start + 1)
        role = (
            "fixed_contract" if contract is not None
            else "ordinary_proof" if kind in ORDINARY_PROOF_COMMANDS
            else "structure" if kind in STRUCTURAL_COMMANDS
            else "semantic_candidate"
        )
        occurrence_id = (
            f"command:{theory}:{ordinal:05d}:L{start + 1}:{kind}:{digest[:12]}"
        )
        occurrences.append({
            "command_id": occurrence_id,
            "source_order": ordinal,
            "kind": kind,
            "modifier": modifier,
            "name": contract["name"] if contract else _command_name(raw, kind, slice_decls),
            "role": role,
            "source_start_line": start + 1,
            "source_end_line": end,
            "source_sha256": digest,
            "raw_text": raw,
            "contract_id": None if contract is None else contract["contract_id"],
            "internal_proof_syntax_present": _contains_internal_proof_syntax(raw),
            "initial_policy": (
                "replace_with_exact_shell" if contract is not None
                else "remove" if role == "ordinary_proof"
                else "retain_as_structure" if role == "structure"
                else "necessity_candidate"
            ),
        })

    # The legacy selector also sees declaration-like text inside comments.
    # Completeness concerns only lexically outer commands.
    declaration_starts = {
        declaration["start"] + 1
        for declaration in slice_decls(source)
        if outer[declaration["start"]]
    }
    inventoried_declaration_starts = {
        item["source_start_line"] for item in occurrences
        if item["kind"] not in STRUCTURAL_COMMANDS
    }
    missing = sorted(declaration_starts - inventoried_declaration_starts)
    if missing:
        raise ValueError(f"declaration starts missing from command inventory for {theory}: {missing}")

    stack: list[int] = []
    for index, command in enumerate(occurrences):
        source_lines = command["raw_text"].splitlines()
        first_line = source_lines[0] if source_lines else ""
        opens_scope = (
            command["kind"] in {
                "context", "experiment", "class", "instantiation", "bundle", "locale",
            }
            and re.search(r"\bbegin\b", first_line) is not None
        ) or command["kind"] == "begin"
        if opens_scope:
            if command["kind"] == "begin" and index:
                prelude_kinds = {
                    "context", "experiment", "class", "instantiation", "bundle", "locale",
                }
                for previous in reversed(occurrences[:index]):
                    if previous["kind"] in {"begin", "end"}:
                        break
                    if previous["kind"] in prelude_kinds:
                        command["scope_prelude_command_id"] = previous["command_id"]
                        previous["matching_begin_command_id"] = command["command_id"]
                        break
            stack.append(index)
        elif command["kind"] == "end":
            if stack:
                opener = stack.pop()
                occurrences[opener]["matching_end_command_id"] = command["command_id"]
                command["matching_start_command_id"] = occurrences[opener]["command_id"]
                for child in occurrences[opener + 1:index]:
                    child.setdefault("enclosing_scope_command_ids", []).append(
                        occurrences[opener]["command_id"]
                    )
            else:
                command["closes_theory"] = True
    if stack:
        raise ValueError(f"unterminated structural scope in {theory}: {stack}")

    return {
        "raw_text": source[:header_match.end()] + "\n",
        "source_start_line": 1,
        "source_end_line": header_end_line,
        "source_sha256": sha256_text(source[:header_match.end()] + "\n"),
    }, occurrences


def build_inventory(
    repo: Path, campaign_id: str, *, architecture: str = "ARM",
) -> dict[str, Any]:
    repo = repo.resolve()
    l4v = repo / "verification" / "l4v"
    slice_decls, is_contract = load_contract_policy()
    theories = []
    public_packages = []
    for theory, path in discover_session_theories(l4v, architecture=architecture, session="Refine"):
        contract_record = extract_theory(
            theory, path, slice_decls=slice_decls, is_contract=is_contract, repo=repo,
        )
        contracts_by_line = {
            item["source_location"]["start_line"]: item
            for item in contract_record["contracts"]
        }
        source = path.read_text(encoding="utf-8", errors="replace")
        header, occurrences = command_occurrences(
            source, theory, contract_starts=contracts_by_line, slice_decls=slice_decls,
        )
        relative = path.relative_to(l4v / "proof" / "refine")
        theories.append({
            "theory": theory,
            "file_slot": contract_record["file_slot"],
            "workspace_path": str(relative),
            "trusted_source_path": contract_record["trusted_source_path"],
            "trusted_source_sha256": contract_record["trusted_source_sha256"],
            "header": header,
            "contracts": contract_record["contracts"],
            "commands": occurrences,
        })
        if contract_record["contracts"]:
            public_packages.append(public_package(campaign_id, contract_record))

    all_commands = [command for theory in theories for command in theory["commands"]]
    role_counts = Counter(item["role"] for item in all_commands)
    inventory = {
        "schema_version": 2,
        "artifact_type": "refine-contract-command-inventory",
        "campaign_id": campaign_id,
        "architecture": architecture,
        "source_session": "Refine",
        "theories": theories,
        "counts": {
            "theories": len(theories),
            "contracts": sum(len(item["contracts"]) for item in theories),
            "commands": len(all_commands),
            "commands_by_role": dict(sorted(role_counts.items())),
            "semantic_candidates_with_internal_proof_syntax": sum(
                item["role"] == "semantic_candidate"
                and item["internal_proof_syntax_present"]
                for item in all_commands
            ),
        },
        "policy": {
            "ordinary_proof": "remove before Semantic Base construction",
            "fixed_contract": "publish exact shell; remove human proof body",
            "semantic_candidate": "retain verbatim only while deletion counterfactual fails",
            "structure": "retained only as syntax surrounding retained commands",
        },
    }
    validate_inventory(inventory, public_packages)
    return {"inventory": inventory, "public_packages": public_packages}


def validate_inventory(
    inventory: dict[str, Any], public_packages: list[dict[str, Any]],
) -> None:
    commands = [item for theory in inventory["theories"] for item in theory["commands"]]
    command_ids = [item["command_id"] for item in commands]
    if len(command_ids) != len(set(command_ids)):
        raise ValueError("command occurrence IDs are not unique")
    private_contract_ids = {
        item["contract_id"]
        for theory in inventory["theories"] for item in theory["contracts"]
    }
    command_contract_ids = {
        item["contract_id"] for item in commands if item["role"] == "fixed_contract"
    }
    public_contract_ids = {
        item["contract_id"]
        for package in public_packages for item in package["contracts"]
    }
    if private_contract_ids != command_contract_ids or private_contract_ids != public_contract_ids:
        raise ValueError("private, command, and public contract coverage differ")
    for theory in inventory["theories"]:
        previous_end = theory["header"]["source_end_line"]
        for command in theory["commands"]:
            if command["source_start_line"] < previous_end:
                raise ValueError(f"overlapping command occurrence: {command['command_id']}")
            if sha256_text(command["raw_text"]) != command["source_sha256"]:
                raise ValueError(f"command source hash mismatch: {command['command_id']}")
            previous_end = command["source_end_line"]


def write_inventory(output: Path, artifacts: dict[str, Any]) -> None:
    write_immutable_json(output / "private" / "command-inventory.json", artifacts["inventory"])
    for package in artifacts["public_packages"]:
        write_immutable_json(
            output / "public" / "contract-packages" / f"{package['file_slot']}.json",
            package,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--architecture", default="ARM")
    args = parser.parse_args()
    artifacts = build_inventory(args.repo, args.campaign_id, architecture=args.architecture)
    write_inventory(args.output, artifacts)
    print(json.dumps(artifacts["inventory"]["counts"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
