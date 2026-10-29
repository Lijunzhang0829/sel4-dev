#!/usr/bin/env python3
"""Record preparation proof status only after matching complete Isabelle builds.

This is not the proof-generation harness's GREEN/commit protocol and cannot
accept model attempts. It records the disclosed local preparation artifacts.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from build import PROJECT, source_hashes


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_build(path: Path, directory: Path) -> dict:
    evidence = json.loads(path.read_text())
    if evidence.get("status") != "passed" or evidence.get("returncode") != 0:
        raise ValueError(f"Build did not pass: {path}")
    expected = source_hashes(directory)
    if not expected or evidence.get("sources_sha256") != expected:
        raise ValueError(f"Build evidence does not match the complete current sources: {path}")
    if not evidence.get("sources_unchanged_during_build"):
        raise ValueError(f"Build did not bind a stable snapshot: {path}")
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-build", type=Path,
                        default=PROJECT / "build/final/build-result.json")
    parser.add_argument("--input-build", type=Path,
                        default=PROJECT / "build/sanitized/build-result.json")
    args = parser.parse_args()
    full = checked_build(args.full_build, PROJECT)
    clean = checked_build(args.input_build, PROJECT / "sanitized")
    if any(not Path(p).is_relative_to(PROJECT / "sanitized")
           for p in clean["sources_sha256"]):
        raise ValueError("Sanitized build includes files outside the sanitized input")
    input_manifest = json.loads((PROJECT / "sanitized/input-manifest.json").read_text())
    for relative, expected in input_manifest["files"].items():
        if sha(PROJECT / "sanitized" / relative) != expected:
            raise ValueError(f"Sanitized input manifest is stale: {relative}")
    subprocess.run([sys.executable, str(PROJECT / "tools/audit.py"),
                    "--require-build", "--build-result", str(args.full_build),
                    "--output", str(PROJECT / "build/audit.json")], check=True)
    registry_path = PROJECT / "targets.json"
    registry = json.loads(registry_path.read_text())
    audit_source = (PROJECT / "theories/Interval_Audit.thy").read_text()
    if "Interval_Audit" not in (PROJECT / "ROOT").read_text():
        raise ValueError("Complete session does not include the native theorem audit")
    for target in registry["targets"]:
        proof = target.get("proof_theorem")
        if proof:
            expected = f'("{target["id"]}", "{target["theory"]}.{target["id"]}", "{proof}")'
            if expected not in audit_source:
                raise ValueError(f"Missing native theorem audit: {target['id']}")
        target["status"] = "proved" if proof else "pending"
        target["evidence_kind"] = "checked_preparation_proof" if proof else "unproved_contract"
    registry["status_note"] = (
        "proved means a disclosed preparation theorem checked by the full Isabelle session "
        "and its exact-target/no-hypotheses/no-oracles audit; not an LLM experiment result")
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n")
    groups = {}
    for group in sorted({t["group"] for t in registry["targets"]}):
        targets = [t for t in registry["targets"] if t["group"] == group]
        groups[group] = {"total": len(targets), **dict(Counter(t["status"] for t in targets))}
    validation = {
        "stage": "preparation", "formal_evaluation_started": False,
        "full_build": {"status": full["status"], "finished_utc": full["finished_utc"],
                       "elapsed_seconds": full["elapsed_seconds"],
                       "image": full.get("image"),
                       "source_hashes": {str(Path(p).relative_to(PROJECT)): h
                                         for p, h in full["sources_sha256"].items()}},
        "sanitized_build": {"status": clean["status"], "finished_utc": clean["finished_utc"],
                            "elapsed_seconds": clean["elapsed_seconds"],
                            "source_hashes": {str(Path(p).relative_to(PROJECT)): h
                                              for p, h in clean["sources_sha256"].items()}},
        "native_audit": {"exact_target": True, "no_undischarged_hypotheses": True,
                         "no_oracle_dependencies": True},
        "groups": groups,
        "targets_sha256": sha(registry_path),
        "input_manifest_sha256": sha(PROJECT / "sanitized/input-manifest.json"),
        "limitations": ["Pending contracts are not assumed or proved.",
                        "No checked equivalence to Python source semantics.",
                        "No formal proof-generation experiment has been run."],
    }
    (PROJECT / "validation.json").write_text(json.dumps(validation, indent=2) + "\n")
    # Refresh the audit's displayed counts after acceptance as well.
    subprocess.run([sys.executable, str(PROJECT / "tools/audit.py"),
                    "--require-build", "--build-result", str(args.full_build),
                    "--output", str(PROJECT / "build/audit.json")], check=True)
    print(json.dumps({"validation": "passed", "groups": groups}, indent=2))


if __name__ == "__main__":
    main()
