#!/usr/bin/env python3
"""Compute command-level semantic necessity from Isabelle counterfactuals."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from typing import Any, Callable

from regen.refine_campaign import write_immutable_json
from regen.semantic_base_builder import (
    all_semantic_candidate_ids,
    initial_retained_command_ids,
    materialize_workspace,
    run_isabelle_build,
    sha256_path,
)


Probe = Callable[[set[str], str | None], dict[str, Any]]


def decide_counterfactual(command_id: str, result: dict[str, Any]) -> dict[str, Any]:
    verdict = result.get("verdict")
    if verdict == "pass":
        decision = "REMOVE"
        reason = "Semantic Base and every exact contract shell pass without this command."
    elif verdict == "fail":
        decision = "KEEP"
        reason = "Removing this command breaks the Isabelle semantic/elaboration gate."
    else:
        decision = "UNRESOLVED"
        reason = "Timeout or infrastructure evidence cannot establish semantic necessity."
    return {
        "command_id": command_id,
        "decision": decision,
        "reason": reason,
        "probe_verdict": verdict,
        "evidence": result,
    }


def necessity_fixed_point(
    inventory: dict[str, Any], probe: Probe, *, max_passes: int | None = None,
    baseline_only: bool = False,
) -> dict[str, Any]:
    """Try single-occurrence deletion in reverse source order until stable."""
    retained = initial_retained_command_ids(inventory)
    ordered = [
        command["command_id"]
        for theory in inventory["theories"]
        for command in theory["commands"]
        if command["role"] == "semantic_candidate"
    ]
    all_candidates = all_semantic_candidate_ids(inventory)
    baseline_excluded = all_candidates - retained
    baseline = probe(set(retained), None)
    if baseline.get("verdict") != "pass":
        return {
            "schema_version": 1,
            "artifact_type": "command-necessity-ledger",
            "campaign_id": inventory["campaign_id"],
            "verdict": "blocked",
            "reason": "baseline Semantic Base or exact contract shells are not green",
            "baseline": baseline,
            "retained_command_ids": sorted(retained),
            "baseline_exclusion": {
                "decision": "UNRESOLVED",
                "command_ids": sorted(baseline_excluded),
                "reason": "The semantic-core baseline did not pass.",
            },
            "passes": [],
        }
    if baseline_only:
        return {
            "schema_version": 1,
            "artifact_type": "command-necessity-ledger",
            "campaign_id": inventory["campaign_id"],
            "verdict": "partial",
            "criterion": (
                "REMOVE iff deleting one source occurrence still permits a clean "
                "Semantic_Base build and elaboration of every exact fixed contract shell"
            ),
            "baseline": baseline,
            "baseline_exclusion": {
                "decision": "REMOVE_AS_GROUP",
                "command_ids": sorted(baseline_excluded),
                "reason": (
                    "The Semantic Base and all exact contract shells pass with every "
                    "listed mixed/certificate command absent."
                ),
            },
            "retained_command_ids": sorted(retained),
            "removed_command_ids": sorted(baseline_excluded),
            "unresolved_command_ids": [],
            "passes": [],
            "fixed_point_complete": False,
        }

    passes = []
    pass_number = 0
    unresolved: set[str] = set()
    while True:
        pass_number += 1
        removed_this_pass = []
        decisions = []
        for command_id in reversed(ordered):
            if command_id not in retained or command_id in unresolved:
                continue
            trial = set(retained)
            trial.remove(command_id)
            decision = decide_counterfactual(command_id, probe(trial, command_id))
            decisions.append(decision)
            if decision["decision"] == "REMOVE":
                retained = trial
                removed_this_pass.append(command_id)
            elif decision["decision"] == "UNRESOLVED":
                unresolved.add(command_id)
        passes.append({
            "pass": pass_number,
            "removed_command_ids": removed_this_pass,
            "decisions": decisions,
        })
        if not removed_this_pass or max_passes is not None and pass_number >= max_passes:
            break
    complete = not unresolved and not passes[-1]["removed_command_ids"]
    return {
        "schema_version": 1,
        "artifact_type": "command-necessity-ledger",
        "campaign_id": inventory["campaign_id"],
        "verdict": "pass" if complete else "partial",
        "criterion": (
            "REMOVE iff deleting one source occurrence still permits a clean Semantic_Base "
            "build and elaboration of every exact fixed contract shell"
        ),
        "baseline": baseline,
        "passes": passes,
        "retained_command_ids": sorted(retained),
        "baseline_exclusion": {
            "decision": "REMOVE_AS_GROUP",
            "command_ids": sorted(baseline_excluded),
            "reason": (
                "The Semantic Base and all exact contract shells pass with every "
                "listed mixed/certificate command absent."
            ),
        },
        "removed_command_ids": sorted(all_candidates - retained),
        "unresolved_command_ids": sorted(unresolved),
        "fixed_point_complete": complete,
    }



def accept_green_baseline(
    inventory: dict[str, Any], baseline_result_path: Path,
) -> dict[str, Any]:
    """Accept the deterministic semantic core without claiming command minimality."""
    result = json.loads(baseline_result_path.read_text(encoding="utf-8"))
    workspace = result.get("workspace", {})
    isabelle = result.get("isabelle", {})
    retained = initial_retained_command_ids(inventory)
    all_candidates = all_semantic_candidate_ids(inventory)
    if isabelle.get("verdict") != "pass":
        raise ValueError("baseline acceptance requires a GREEN Isabelle result")
    if workspace.get("campaign_id") != inventory.get("campaign_id"):
        raise ValueError("baseline result belongs to another campaign")
    if workspace.get("retained_command_count") != len(retained):
        raise ValueError("baseline retained-command count differs from semantic-core policy")
    if workspace.get("contract_shell_count") != inventory["counts"]["contracts"]:
        raise ValueError("baseline contract-shell coverage is incomplete")
    return {
        "schema_version": 1,
        "artifact_type": "command-necessity-ledger",
        "campaign_id": inventory["campaign_id"],
        "verdict": "pass",
        "selection_complete": True,
        "acceptance_basis": "deterministic-semantic-core-plus-green-exact-shell-gate",
        "command_minimality_claimed": False,
        "fixed_point_complete": False,
        "fixed_point_status": "optional-diagnostic-not-run",
        "baseline": {
            "verdict": "pass",
            "result_path": str(baseline_result_path.resolve()),
            "result_sha256": sha256_path(baseline_result_path),
            "wall_ms": isabelle.get("wall_ms"),
        },
        "baseline_exclusion": {
            "decision": "REMOVE_AS_GROUP",
            "command_ids": sorted(all_candidates - retained),
            "reason": (
                "Semantic Base and every exact contract shell pass with all "
                "mixed or certificate candidates absent."
            ),
        },
        "retained_command_ids": sorted(retained),
        "removed_command_ids": sorted(all_candidates - retained),
        "unresolved_command_ids": [],
        "passes": [],
    }


def filesystem_probe(
    inventory: dict[str, Any], root: Path, l4v: Path, heaps: Path,
    *, timeout_seconds: int,
) -> Probe:
    counter = 0

    def run(retained: set[str], command_id: str | None) -> dict[str, Any]:
        nonlocal counter
        counter += 1
        workspace = root / f"attempt-{counter:05d}" / "workspace"
        if workspace.exists():
            shutil.rmtree(workspace)
        manifest = materialize_workspace(inventory, workspace, retained)
        result = run_isabelle_build(
            workspace, l4v, heaps, timeout_seconds=timeout_seconds,
        )
        result["tested_command_id"] = command_id
        result["workspace_manifest"] = manifest
        attempt = workspace.parent / "counterfactual-result.json"
        attempt.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return result

    return run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--l4v", type=Path)
    parser.add_argument("--heaps", type=Path)
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--max-passes", type=int)
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--baseline-result", type=Path)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=True)
    if args.baseline_result:
        ledger = accept_green_baseline(inventory, args.baseline_result)
    else:
        if args.l4v is None or args.heaps is None:
            parser.error("--l4v and --heaps are required for counterfactual probing")
        ledger = necessity_fixed_point(
            inventory,
            filesystem_probe(
                inventory, args.output, args.l4v, args.heaps,
                timeout_seconds=args.timeout,
            ),
            max_passes=args.max_passes,
            baseline_only=args.baseline_only,
        )
    write_immutable_json(
        args.output / "command-necessity-ledger.json", ledger,
    )
    print(json.dumps({
        "verdict": ledger["verdict"],
        "fixed_point_complete": ledger.get("fixed_point_complete", False),
        "retained": len(ledger["retained_command_ids"]),
        "removed": len(ledger.get("removed_command_ids", [])),
        "unresolved": len(ledger.get("unresolved_command_ids", [])),
    }, indent=2, sort_keys=True))
    return 0 if ledger.get("verdict") == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
