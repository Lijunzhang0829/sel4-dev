#!/usr/bin/env python3
"""Per-theory generation timeline and production seal gate."""
from __future__ import annotations

# Allow both python -m and direct script invocation.
if __package__ in {None, ""}:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from regen.infrastructure.llm_transcript import artifact_paths, validate_call_artifact

def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())

def seal_theory_audit(campaign_dir: Path, theory: str, source_path: Path,
                      events: list[dict[str, Any]], *,
                      required_llm_stages: set[str]) -> dict[str, Any]:
    """Validate and freeze the complete generation history for one theory."""
    campaign_dir = campaign_dir.resolve()
    repo = campaign_dir.parent.parent.parent
    basename = theory.split(".", 1)[-1]
    out_dir = campaign_dir / "audit" / basename
    out_dir.mkdir(parents=True, exist_ok=True)
    if not source_path.is_file():
        raise ValueError(f"missing generated source for audit: {source_path}")
    timeline, seen_stages = [], set()
    green_checks = 0
    failed_check_pending_repair = False
    for sequence, event in enumerate(events, start=1):
        kind = event.get("kind")
        if kind == "llm":
            prefix = Path(event["prefix"])
            meta = validate_call_artifact(prefix, require_real=True)
            stage = str(event.get("stage") or meta.get("stage") or "")
            if not stage:
                raise ValueError(f"LLM event lacks a stage: {prefix}")
            if meta.get("theory") not in {None, theory}:
                raise ValueError(f"LLM transcript theory mismatch: {prefix}")
            if failed_check_pending_repair and not stage.startswith("repair"):
                raise ValueError(f"failed Isabelle check must be followed by repair: {theory}")
            if stage.startswith("repair"):
                failed_check_pending_repair = False
            seen_stages.add(stage)
            paths = artifact_paths(prefix)
            timeline.append({
                "sequence": sequence, "kind": "llm", "stage": stage,
                "provider": meta["provider"], "model": meta["model"],
                "started_at": meta.get("started_at"),
                "finished_at": meta.get("finished_at"),
                "wall_ms": meta.get("wall_ms"), "prefix": relative(prefix, repo),
                "prompt_sha256": meta["prompt_sha256"],
                "raw_sha256": meta["raw_sha256"],
                "final_sha256": meta["final_sha256"],
                "artifacts": {name: relative(path, repo) for name, path in paths.items()},
            })
        elif kind == "isabelle":
            record = dict(event["record"])
            log = Path(record["log"])
            if not log.is_absolute():
                log = repo / log
            if not log.is_file():
                raise ValueError(f"missing Isabelle log: {log}")
            verdict = record.get("verdict")
            if verdict == "pass":
                green_checks += 1
                failed_check_pending_repair = False
            elif verdict == "fail":
                failed_check_pending_repair = True
            else:
                raise ValueError(f"invalid Isabelle verdict: {verdict}")
            timeline.append({
                "sequence": sequence, "kind": "isabelle",
                "stage": event.get("stage", "theory-check"),
                "verdict": verdict, "returncode": record.get("returncode"),
                "wall_ms": record.get("wall_ms"), "source": record.get("source"),
                "source_sha256": record.get("source_sha256"),
                "log": relative(log, repo), "log_sha256": sha256_path(log),
            })
        elif kind == "harness":
            timeline.append({"sequence": sequence, **event})
        else:
            raise ValueError(f"unknown theory audit event kind: {kind}")
    missing = sorted(required_llm_stages - seen_stages)
    if missing:
        raise ValueError(f"missing required LLM stages for {theory}: {missing}")
    if failed_check_pending_repair:
        raise ValueError(f"last Isabelle check is red for {theory}")
    if green_checks == 0:
        raise ValueError(f"no green Isabelle check recorded for {theory}")
    timeline_path = out_dir / "timeline.jsonl"
    timeline_path.write_text("".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
        for item in timeline
    ), encoding="utf-8")
    manifest = {
        "schema_version": 1, "status": "llm_transcript_audited",
        "sealed_at": datetime.now(timezone.utc).isoformat(), "theory": theory,
        "source": relative(source_path, repo), "source_sha256": sha256_path(source_path),
        "required_llm_stages": sorted(required_llm_stages),
        "observed_llm_stages": sorted(seen_stages),
        "llm_call_count": sum(item["kind"] == "llm" for item in timeline),
        "isabelle_check_count": sum(item["kind"] == "isabelle" for item in timeline),
        "timeline": relative(timeline_path, repo),
        "timeline_sha256": sha256_path(timeline_path),
        "provider_network_calls_only": True, "replay_or_file_agent_calls": 0,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest
