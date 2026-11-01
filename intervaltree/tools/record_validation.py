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
from prepare_benchmark import expansion_audit


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_build(path: Path, directory: Path) -> dict:
    evidence = json.loads(path.read_text())
    if evidence.get("status") != "passed" or evidence.get("returncode") != 0:
        raise ValueError(f"Build did not pass: {path}")
    # source_hashes binds all discovered files even when build.py selected only
    # one session.  Such a receipt does not establish that the goals/audit ran.
    command = evidence.get("command")
    if not isinstance(command, list) or command[-2:] != ["-D", str(directory.resolve())]:
        raise ValueError(f"Build did not cover the complete session directory: {path}")
    expected = source_hashes(directory)
    if not expected or evidence.get("sources_sha256") != expected:
        raise ValueError(f"Build evidence does not match the complete current sources: {path}")
    if not evidence.get("sources_unchanged_during_build"):
        raise ValueError(f"Build did not bind a stable snapshot: {path}")
    return evidence


def checked_expansion(registry: dict, manifest: dict, directory: Path) -> dict | None:
    """Bind the executed native expansion audit to the actual public goals."""
    if manifest.get("input_revision") != "expanded-specs-v2":
        return None
    targets = registry["targets"]
    ids = [target["id"] for target in targets]
    if not ids or len(set(ids)) != len(ids) or len(ids) != manifest["target_count"]:
        raise ValueError("Spec expansion target coverage does not match the manifest")
    expected_root = (
        'session Intervaltree_Definitions = HOL +\n'
        '  options [document = false, threads = 2]\n'
        '  directories "theories"\n'
        '  theories Interval_Input\n'
        'session Intervaltree_Goals in "goals" = Intervaltree_Definitions +\n'
        '  options [document = false, threads = 2]\n'
        '  theories Interval_Goals\n')
    if (directory / "ROOT").read_text() != expected_root:
        raise ValueError("Expanded input does not include the expected Goals session")
    for target in targets:
        if target["statement"] != f'lemma {target["id"]}: "{target["expanded_proposition"]}"':
            raise ValueError("Spec expansion statement differs from its audited proposition")
    expected_goals = (
        "theory Interval_Goals\n  imports Intervaltree_Definitions.Interval_Input\nbegin\n\n"
        + expansion_audit(targets) + "\n"
        + "\n\n".join(t["statement"] + "\n  oops" for t in targets) + "\n\nend\n")
    goals = directory / "goals/Interval_Goals.thy"
    if goals.read_text() != expected_goals:
        raise ValueError("Native spec expansion audit does not match the complete target registry")
    contracts_path = directory / "contracts.jsonl"
    contracts = [json.loads(line) for line in contracts_path.read_text().splitlines() if line.strip()]
    if [(c.get("contract_id"), c.get("exact_statement")) for c in contracts] != [
            (t["id"], t["statement"]) for t in targets]:
        raise ValueError("Public contracts differ from the natively audited spec statements")
    return {"status": "passed", "checked_targets": len(ids),
            "method": "native definitional conversion; no target theorem proved",
            "goals_sha256": sha(goals), "contracts_sha256": sha(contracts_path)}


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
    expansion = checked_expansion(registry, input_manifest, PROJECT / "sanitized")
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
        "and its exact-target/no-hypotheses/no-oracles audit; not an LLM experiment result. "
        "pending only means no preparation proof; known refutations are recorded separately "
        "in model-feedback.json and are never supplied to generation.")
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n")
    groups = {}
    for group in sorted({t["group"] for t in registry["targets"]}):
        targets = [t for t in registry["targets"] if t["group"] == group]
        groups[group] = {"total": len(targets), **dict(Counter(t["status"] for t in targets))}
    validation = {
        "stage": "preparation", "formal_evaluation_started": False,
        "input_revision": input_manifest.get("input_revision", "named-specs-v1"),
        "semantic_feedback_file": "model-feedback.json",
        "spec_expansion_audit": expansion,
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
                        "No proof-generation experiment has been run on this input revision.",
                        "A known refutation is retained; this is not a guaranteed all-true spec set."],
    }
    (PROJECT / "validation.json").write_text(json.dumps(validation, indent=2) + "\n")
    # Refresh the audit's displayed counts after acceptance as well.
    subprocess.run([sys.executable, str(PROJECT / "tools/audit.py"),
                    "--require-build", "--build-result", str(args.full_build),
                    "--output", str(PROJECT / "build/audit.json")], check=True)
    print(json.dumps({"validation": "passed", "groups": groups}, indent=2))


if __name__ == "__main__":
    main()
