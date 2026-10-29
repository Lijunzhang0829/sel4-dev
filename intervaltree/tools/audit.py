#!/usr/bin/env python3
"""Check source locks, target bookkeeping, and absence of admitted proofs.

This is a preparation consistency audit, not a substitute for Isabelle. Pending
definitions are allowed; they are never counted as established theorems.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

PROJECT = Path(__file__).resolve().parents[1]


def strip_prose(source: str) -> str:
    """Mask nested comments, quoted strings, and cartouches; preserve newlines."""
    result: list[str] = []
    i = 0
    comment = cartouche = 0
    quoted = False
    while i < len(source):
        char = source[i]
        if comment:
            if source.startswith("(*", i):
                comment += 1; result.extend("  "); i += 2; continue
            if source.startswith("*)", i):
                comment -= 1; result.extend("  "); i += 2; continue
        elif quoted:
            if char == "\\" and i + 1 < len(source):
                result.extend("  "); i += 2; continue
            if char == '"': quoted = False
        elif cartouche:
            if source.startswith("\\<open>", i):
                cartouche += 1; result.extend(" " * 7); i += 7; continue
            if source.startswith("\\<close>", i):
                cartouche -= 1; result.extend(" " * 8); i += 8; continue
            if char == "‹": cartouche += 1
            if char == "›": cartouche -= 1
        else:
            if source.startswith("(*", i):
                comment = 1; result.extend("  "); i += 2; continue
            if source.startswith("\\<open>", i):
                cartouche = 1; result.extend(" " * 7); i += 7; continue
            if char == "‹": cartouche = 1
            elif char == '"': quoted = True
            else:
                result.append(char); i += 1; continue
        result.append("\n" if char == "\n" else " ")
        i += 1
    return "".join(result)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", type=Path, default=PROJECT / "targets.json")
    parser.add_argument("--source-lock", type=Path, default=PROJECT / "source-lock.json")
    parser.add_argument("--require-build", action="store_true")
    parser.add_argument("--build-result", type=Path, default=PROJECT / "build/build-result.json")
    parser.add_argument("--output", type=Path, default=PROJECT / "build/audit.json")
    args = parser.parse_args()
    errors: list[str] = []
    notes: list[str] = []
    theories: dict[str, str] = {}
    for path in sorted((PROJECT / "theories").glob("*.thy")):
        clean = strip_prose(path.read_text())
        theories[path.stem] = clean
        for m in re.finditer(r"\b(sorry|axiomatization|axioms|admit|skip_proof|oracle)\b", clean):
            errors.append(f"{path.relative_to(PROJECT)}:{clean[:m.start()].count(chr(10))+1}: {m.group()}")
        if re.search(r"\boops\b", clean):
            notes.append(f"{path.name}: oops closes an explicitly unproved goal; no theorem is introduced")
    if not args.targets.is_file():
        errors.append(f"Missing target registry: {args.targets}")
        targets = []
    else:
        payload = json.loads(args.targets.read_text())
        targets = payload if isinstance(payload, list) else payload.get("targets", [])
    counts = Counter(t.get("status", "unknown") for t in targets)
    ids = Counter(t.get("id") for t in targets)
    errors += [f"Duplicate target id: {name}" for name, n in ids.items() if n > 1]
    for target in targets:
        name = target.get("id", "<missing>")
        theory = Path(target.get("theory", "")).stem
        source = theories.get(theory)
        if source is None:
            errors.append(f"{name}: unknown theory {theory}")
            continue
        definition = target.get("definition")
        if definition and not re.search(r"\bdefinition\s+" + re.escape(definition) + r"\b", source):
            errors.append(f"{name}: missing definition {definition}")
        proof = target.get("proof_theorem")
        if proof:
            proof_theory, _, proof_name = proof.rpartition(".")
            proof_name = proof_name or proof
            found = False
            for theory_name, candidate in theories.items():
                if proof_theory and proof_theory != theory_name:
                    continue
                declaration = re.search(r"\b(?:lemma|theorem|corollary)\s+" + re.escape(proof_name) + r"\b", candidate)
                if declaration:
                    found = True
                    tail = candidate[declaration.end():]
                    boundary = re.search(r"(?m)^\s*(?:lemma|theorem|corollary|definition|fun|function|datatype|record|end)\b", tail)
                    body = tail[:boundary.start()] if boundary else tail
                    if re.search(r"\boops\b", body):
                        errors.append(f"{name}: {proof} ends in oops and establishes no theorem")
            if not found:
                errors.append(f"{name}: proof theorem {proof} is not declared")
        if target.get("status") in {"proved", "verified", "green"} and not proof:
            errors.append(f"{name}: positive status without proof_theorem")
    locked = 0
    if not args.source_lock.is_file():
        errors.append(f"Missing source lock: {args.source_lock}")
    else:
        lock = json.loads(args.source_lock.read_text())
        for relative, expected in lock.get("files", {}).items():
            path = (PROJECT / relative).resolve()
            if not path.is_relative_to(PROJECT):
                errors.append(f"Source-lock path escapes project: {relative}")
            elif not path.is_file() or sha(path) != expected:
                errors.append(f"Source-lock mismatch: {relative}")
            locked += 1
    if args.require_build:
        if not args.build_result.is_file():
            errors.append("Required build evidence is missing")
        else:
            evidence = json.loads(args.build_result.read_text())
            if evidence.get("status") != "passed":
                errors.append("Required Isabelle build did not pass")
            if evidence.get("sources_unchanged_during_build") is not True:
                errors.append("Build does not attest unchanged sources during checking")
            import importlib.util
            loader = importlib.util.spec_from_file_location("intervaltree_build_tool", PROJECT / "tools/build.py")
            build_tool = importlib.util.module_from_spec(loader)
            assert loader.loader is not None
            loader.loader.exec_module(build_tool)
            expected_full_sources = build_tool.source_hashes(PROJECT)
            observed_sources = evidence.get("sources_sha256", {})
            if not observed_sources or observed_sources != expected_full_sources:
                errors.append("Build evidence must bind exactly the current full ROOT/theory closure; a smoke or sanitized build is insufficient")
            for name, expected in observed_sources.items():
                path = Path(name)
                if not path.is_file() or sha(path) != expected:
                    errors.append(f"Build evidence is stale: {name}")
    result = {"audit": "passed" if not errors else "failed", "theories": len(theories),
              "targets": len(targets), "target_status_counts": dict(counts),
              "locked_source_files": locked, "required_build": args.require_build,
              "errors": errors, "notes": notes,
              "scope": "Text/source consistency plus optional build evidence; pending goals remain unproved."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return bool(errors)


if __name__ == "__main__":
    sys.exit(main())
