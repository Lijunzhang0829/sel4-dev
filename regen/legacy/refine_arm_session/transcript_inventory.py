#!/usr/bin/env python3
"""Inventory legacy and generation-ledger LLM evidence for a regen campaign."""
from __future__ import annotations

# Allow both python -m and direct script invocation.
if __package__ in {None, ""}:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from regen.infrastructure.llm_transcript import REAL_PROVIDERS, artifact_paths, inspect_call_artifact


REPO = Path(__file__).resolve().parents[3]
DEFAULT_CAMPAIGN = REPO / "reports" / "regen" / "refine-arm-session"


def relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def transcript_prefix(meta_path: Path) -> Path:
    suffix = ".meta.json"
    value = str(meta_path)
    if not value.endswith(suffix):
        raise ValueError(f"not a transcript metadata path: {meta_path}")
    return Path(value[:-len(suffix)])


def inspect_legacy(prefix: Path, repo: Path) -> dict:
    paths = artifact_paths(prefix)
    missing = [name for name, path in paths.items() if not path.is_file()]
    meta = {}
    if paths["meta"].is_file():
        meta = json.loads(paths["meta"].read_text(encoding="utf-8"))
    integrity = False
    error = None
    if not missing:
        try:
            inspect_call_artifact(prefix, require_real=False, require_success=False)
            integrity = True
        except Exception as exc:
            error = str(exc)
    provider = meta.get("provider")
    real = bool(
        provider in REAL_PROVIDERS
        and meta.get("provider_network_call")
        and meta.get("sealable_generation")
    )
    return {
        "prefix": relative(prefix, repo),
        "provider": provider,
        "model": meta.get("model"),
        "stage": meta.get("stage") or prefix.name,
        "theory_metadata": meta.get("theory"),
        "complete_six_tuple": not missing,
        "missing_artifacts": missing,
        "hash_integrity": integrity,
        "validation_error": error,
        "real_provider_call": real,
        "non_independent_replay": not real,
    }


def generated_theories(campaign: Path) -> set[str]:
    result = set()
    for path in campaign.glob("generated*/**/*.thy"):
        if "/smoke/" not in str(path) and "Smoke" not in path.stem:
            result.add(f"Refine.{path.stem}")
    return result


def run(campaign: Path) -> dict:
    campaign = campaign.resolve()
    repo = campaign.parent.parent.parent
    calls_by_theory: dict[str, list[dict]] = defaultdict(list)
    for meta_path in sorted((campaign / "planner").glob("**/*.meta.json")):
        call = inspect_legacy(transcript_prefix(meta_path), repo)
        parent = meta_path.parent.name
        theory = call["theory_metadata"]
        if not theory or theory == "null":
            theory = "Refine-session" if parent == "planner" else f"Refine.{parent}"
        calls_by_theory[theory].append(call)

    ledger_runs: dict[str, list[dict]] = defaultdict(list)
    audit_root = campaign / "audit"
    if audit_root.is_dir():
        for run_path in sorted(audit_root.glob("*/*/run.json")):
            value = json.loads(run_path.read_text(encoding="utf-8"))
            manifest = run_path.parent / "manifest.json"
            ledger_runs[value["theory"]].append({
                "generation_id": value["generation_id"],
                "status": value["status"],
                "run": relative(run_path, repo),
                "manifest": relative(manifest, repo) if manifest.is_file() else None,
            })

    theories = sorted(generated_theories(campaign) | set(calls_by_theory) | set(ledger_runs))
    records = []
    for theory in theories:
        calls = calls_by_theory.get(theory, [])
        runs = ledger_runs.get(theory, [])
        records.append({
            "theory": theory,
            "legacy_call_count": len(calls),
            "legacy_real_call_count": sum(item["real_provider_call"] for item in calls),
            "legacy_replay_call_count": sum(item["non_independent_replay"] for item in calls),
            "legacy_complete_call_count": sum(item["complete_six_tuple"] for item in calls),
            "legacy_calls": calls,
            "generation_ledgers": runs,
            "has_production_audit_seal": any(item["status"] == "sealed" for item in runs),
            "historical_claim": (
                "production-audited" if any(item["status"] == "sealed" for item in runs)
                else "legacy-transcript-only; not a complete per-theory generation audit"
            ),
        })

    report = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "campaign": relative(campaign, repo),
        "policy": "Legacy prompt/response files cannot be upgraded to a production theory seal.",
        "theories": records,
    }
    output = campaign / "transcript-inventory.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    args = parser.parse_args()
    report = run(args.campaign_dir)
    print(json.dumps({
        "campaign": report["campaign"],
        "theories": len(report["theories"]),
        "production_audit_seals": sum(
            item["has_production_audit_seal"] for item in report["theories"]
        ),
        "output": str(args.campaign_dir.resolve() / "transcript-inventory.json"),
    }, indent=2))


if __name__ == "__main__":
    main()
