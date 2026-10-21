#!/usr/bin/env python3
"""Append-only per-theory generation traces and production seal validation."""
from __future__ import annotations

# Allow both python -m and direct script invocation.
if __package__ in {None, ""}:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from regen.infrastructure.llm_transcript import REAL_PROVIDERS, artifact_paths, inspect_call_artifact


SCHEMA_VERSION = 1
GENERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


class TheoryAuditTrail:
    """Durable event ledger for one attempt to generate one theory."""

    def __init__(self, campaign_dir: Path, theory: str, *,
                 generation_id: str) -> None:
        self.campaign_dir = campaign_dir.resolve()
        self.repo = self.campaign_dir.parent.parent.parent
        self.theory = theory
        self.basename = theory.split(".", 1)[-1]
        self.generation_id = generation_id
        if not GENERATION_ID_RE.fullmatch(self.generation_id):
            raise ValueError(f"invalid generation id: {self.generation_id!r}")
        self.run_dir = self.campaign_dir / "audit" / self.basename / self.generation_id
        self.events_path = self.run_dir / "events.jsonl"
        self.run_path = self.run_dir / "run.json"
        self.manifest_path = self.run_dir / "manifest.json"
        self._initialize()

    def _initialize(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if self.run_path.exists():
            existing = json.loads(self.run_path.read_text(encoding="utf-8"))
            if existing.get("theory") != self.theory:
                raise ValueError("generation id already belongs to another theory")
            return
        run = {
            "schema_version": SCHEMA_VERSION,
            "generation_id": self.generation_id,
            "theory": self.theory,
            "generation_unit": "one_complete_theory_attempt",
            "created_at": utc_now(),
            "status": "recording",
            "events": relative(self.events_path, self.repo),
            "pid": os.getpid(),
        }
        self.run_path.write_text(
            json.dumps(run, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
        self.events_path.touch(exist_ok=True)
        (self.run_dir.parent / "latest.json").write_text(
            json.dumps({
                "theory": self.theory,
                "generation_id": self.generation_id,
                "run": relative(self.run_path, self.repo),
            }, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def events(self) -> list[dict[str, Any]]:
        result = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError(f"non-object event in {self.events_path}")
                result.append(event)
        return result

    def append(self, kind: str, stage: str, **payload: Any) -> dict[str, Any]:
        if self.manifest_path.exists():
            raise RuntimeError(f"cannot append to sealed generation: {self.generation_id}")
        previous = self.events()
        previous_hash = previous[-1]["event_sha256"] if previous else None
        body = {
            "schema_version": SCHEMA_VERSION,
            "sequence": len(previous) + 1,
            "recorded_at": utc_now(),
            "generation_id": self.generation_id,
            "theory": self.theory,
            "kind": kind,
            "stage": stage,
            "previous_event_sha256": previous_hash,
            **payload,
        }
        body["event_sha256"] = hashlib.sha256(
            canonical_json(body).encode("utf-8")
        ).hexdigest()
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(canonical_json(body) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return body

    def record_harness(self, stage: str, *, artifacts: list[Path] | None = None,
                       details: dict[str, Any] | None = None) -> dict[str, Any]:
        records = []
        safe_stage = re.sub(r"[^A-Za-z0-9_.-]+", "-", stage).strip("-") or "stage"
        bucket = self.run_dir / "harness" / f"{len(self.events()) + 1:04d}-{safe_stage}"
        for index, source in enumerate(artifacts or [], start=1):
            source = source.resolve()
            if not source.is_file():
                raise ValueError(f"missing harness artifact: {source}")
            bucket.mkdir(parents=True, exist_ok=True)
            snapshot = bucket / f"{index:02d}-{source.name}"
            shutil.copy2(source, snapshot)
            records.append({
                "original_path": relative(source, self.repo),
                "path": relative(snapshot, self.repo),
                "sha256": sha256_path(snapshot),
                "size_bytes": snapshot.stat().st_size,
            })
        return self.append(
            "harness", stage, artifacts=records, details=details or {},
        )

    def record_llm_artifact(self, prefix: Path, *, stage: str) -> None:
        meta = inspect_call_artifact(
            prefix, require_real=False, require_success=False,
        )
        if meta.get("theory") not in {None, self.theory}:
            raise ValueError(
                f"LLM transcript theory mismatch: {meta.get('theory')}"
            )
        paths = artifact_paths(prefix)
        successful = meta.get("returncode") == 0 and not meta.get("timed_out")
        self.append(
            "llm", stage,
            prefix=relative(prefix, self.repo),
            provider=meta.get("provider"),
            model=meta.get("model"),
            successful=successful,
            provider_network_call=bool(meta.get("provider_network_call")),
            sealable_generation=bool(meta.get("sealable_generation")),
            no_tool_events=bool(meta.get("no_tool_events")),
            started_at=meta.get("started_at") or meta.get("recorded_at"),
            finished_at=meta.get("finished_at"),
            wall_ms=meta.get("wall_ms"),
            returncode=meta.get("returncode"),
            timed_out=meta.get("timed_out"),
            artifacts={
                name: {
                    "path": relative(path, self.repo),
                    "sha256": sha256_path(path),
                }
                for name, path in paths.items()
            },
        )

    def record_isabelle(self, stage: str, record: dict[str, Any]) -> dict[str, Any]:
        log = Path(record["log"])
        if not log.is_absolute():
            log = self.repo / log
        source = Path(record["source"])
        if not source.is_absolute():
            source = self.repo / source
        if not log.is_file() or not source.is_file():
            raise ValueError(f"missing Isabelle source/log: {source} / {log}")
        safe_stage = re.sub(r"[^A-Za-z0-9_.-]+", "-", stage).strip("-") or "check"
        bucket = self.run_dir / "isabelle" / f"{len(self.events()) + 1:04d}-{safe_stage}"
        bucket.mkdir(parents=True, exist_ok=True)
        source_snapshot = bucket / source.name
        log_snapshot = bucket / log.name
        shutil.copy2(source, source_snapshot)
        shutil.copy2(log, log_snapshot)
        if record.get("source_sha256") != sha256_path(source_snapshot):
            raise ValueError(f"Isabelle source hash disagrees with record: {source}")
        return self.append(
            "isabelle", stage,
            verdict=record.get("verdict"), returncode=record.get("returncode"),
            wall_ms=record.get("wall_ms"),
            original_source=relative(source, self.repo),
            source=relative(source_snapshot, self.repo),
            source_sha256=sha256_path(source_snapshot),
            original_log=relative(log, self.repo),
            log=relative(log_snapshot, self.repo),
            log_sha256=sha256_path(log_snapshot),
        )
    def _verify_chain(self, events: list[dict[str, Any]]) -> None:
        previous_hash = None
        for sequence, event in enumerate(events, start=1):
            if event.get("sequence") != sequence:
                raise ValueError(f"non-contiguous theory event sequence at {sequence}")
            if event.get("previous_event_sha256") != previous_hash:
                raise ValueError(f"broken theory event chain at {sequence}")
            stored_hash = event.get("event_sha256")
            unhashed = dict(event)
            unhashed.pop("event_sha256", None)
            actual_hash = hashlib.sha256(
                canonical_json(unhashed).encode("utf-8")
            ).hexdigest()
            if stored_hash != actual_hash:
                raise ValueError(f"theory event hash mismatch at {sequence}")
            previous_hash = stored_hash

    def seal(self, source_path: Path, *, required_llm_stages: set[str],
             require_real_provider: bool = True) -> dict[str, Any]:
        if not source_path.is_file():
            raise ValueError(f"missing generated source for audit: {source_path}")
        events = self.events()
        self._verify_chain(events)
        successful_stages: set[str] = set()
        green_checks = 0
        last_check_verdict = None
        replay_calls = 0
        pending_failed_check = False
        for event in events:
            if event["kind"] == "llm":
                prefix = Path(event["prefix"])
                if not prefix.is_absolute():
                    prefix = self.repo / prefix
                for artifact in event["artifacts"].values():
                    path = Path(artifact["path"])
                    if not path.is_absolute():
                        path = self.repo / path
                    if sha256_path(path) != artifact["sha256"]:
                        raise ValueError(f"LLM artifact hash mismatch: {path}")
                meta = inspect_call_artifact(
                    prefix, require_real=False, require_success=False,
                )
                is_real = (
                    meta.get("provider") in REAL_PROVIDERS
                    and meta.get("provider_network_call")
                    and meta.get("sealable_generation")
                )
                if not is_real:
                    replay_calls += 1
                if event.get("successful") and is_real:
                    successful_stages.add(event["stage"])
                if pending_failed_check and event.get("successful"):
                    if not event["stage"].startswith("repair"):
                        raise ValueError(
                            "red Isabelle check must be followed by a repair LLM call"
                        )
                    pending_failed_check = False
            elif event["kind"] == "harness":
                for artifact in event.get("artifacts", []):
                    path = Path(artifact["path"])
                    if not path.is_absolute():
                        path = self.repo / path
                    if not path.is_file() or sha256_path(path) != artifact["sha256"]:
                        raise ValueError(f"Harness artifact hash mismatch: {path}")
            elif event["kind"] == "isabelle":
                if pending_failed_check:
                    raise ValueError(
                        "red Isabelle check must be followed by a repair LLM call"
                    )
                log = Path(event["log"])
                if not log.is_absolute():
                    log = self.repo / log
                if sha256_path(log) != event["log_sha256"]:
                    raise ValueError(f"Isabelle log hash mismatch: {log}")
                verdict = event.get("verdict")
                source = event.get("source")
                if source and event.get("source_sha256"):
                    source_path = Path(source)
                    if not source_path.is_absolute():
                        source_path = self.repo / source_path
                    if not source_path.is_file() or sha256_path(source_path) != event["source_sha256"]:
                        raise ValueError(f"Isabelle source hash mismatch: {source_path}")
                if verdict not in {"pass", "fail"}:
                    raise ValueError(f"invalid Isabelle verdict: {verdict}")
                last_check_verdict = verdict
                if verdict == "pass":
                    green_checks += 1
                else:
                    pending_failed_check = True
            else:
                raise ValueError(f"unknown theory event kind: {event['kind']}")
        missing = sorted(required_llm_stages - successful_stages)
        if missing:
            raise ValueError(
                f"missing successful real LLM stages for {self.theory}: {missing}"
            )
        if require_real_provider and replay_calls:
            raise ValueError(
                f"development replay calls cannot production-seal {self.theory}: "
                f"{replay_calls}"
            )
        if pending_failed_check or last_check_verdict != "pass" or green_checks == 0:
            raise ValueError(
                f"the final recorded Isabelle state is not green: {self.theory}"
            )
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "status": "production_audit_seal",
            "sealed_at": utc_now(),
            "generation_id": self.generation_id,
            "theory": self.theory,
            "source": relative(source_path, self.repo),
            "source_sha256": sha256_path(source_path),
            "required_llm_stages": sorted(required_llm_stages),
            "successful_real_llm_stages": sorted(successful_stages),
            "llm_call_count": sum(event["kind"] == "llm" for event in events),
            "isabelle_check_count": sum(
                event["kind"] == "isabelle" for event in events
            ),
            "replay_or_file_agent_calls": replay_calls,
            "events": relative(self.events_path, self.repo),
            "events_sha256": sha256_path(self.events_path),
            "event_chain_head": events[-1]["event_sha256"] if events else None,
        }
        self.manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        run = json.loads(self.run_path.read_text(encoding="utf-8"))
        run.update({
            "status": "sealed",
            "sealed_at": manifest["sealed_at"],
            "manifest": relative(self.manifest_path, self.repo),
        })
        self.run_path.write_text(
            json.dumps(run, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
        return manifest
