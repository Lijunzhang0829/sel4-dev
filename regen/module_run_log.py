#!/usr/bin/env python3
"""Aggregate one module run into one review-friendly JSON log."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from regen.llm_transcript import artifact_paths


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def artifact_reference(path: Path) -> dict[str, Any]:
    """Describe one immutable artifact without copying its payload."""
    data = path.read_bytes()
    return {
        "path": str(path.resolve()),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def output_summary(path: Path) -> dict[str, Any]:
    if path.suffix != ".json":
        return {}
    value = read_json(path)
    if not isinstance(value, dict):
        return {}
    fields = {
        "status", "verdict", "returncode", "decision", "operation",
        "failure_class", "applied", "action", "graph_revision", "revision",
        "node_id", "file_slot", "theory",
    }
    return {key: value[key] for key in fields if key in value}


def visible_reasoning_events(raw_text: str) -> list[dict[str, Any]]:
    result = []
    for line in raw_text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        rendered = json.dumps(event, ensure_ascii=False).lower()
        if "reasoning" in rendered:
            result.append(event)
    return result


def write_llm_module_log(
    transcript_prefix: Path, destination: Path, *, module: str,
    run_name: str, context: dict[str, Any] | None = None,
    outputs: dict[str, Path] | None = None,
) -> dict[str, Any]:
    paths = artifact_paths(transcript_prefix)
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise ValueError(f"incomplete transcript for module log: {missing}")
    output_artifacts: dict[str, Any] = {}
    for name, path in (outputs or {}).items():
        output_artifacts[name] = {
            **artifact_reference(path),
            "summary": output_summary(path),
        }
    attempt = {
        "context": context or {},
        "transcript_prefix": str(transcript_prefix.resolve()),
        "transcript_artifacts": {
            name: artifact_reference(path) for name, path in paths.items()
        },
        "metadata": read_json(paths["meta"]),
        "audit": read_json(paths["audit"]),
        "output_artifacts": output_artifacts,
        "reasoning_scope_note": (
            "The exact prompt, raw provider stream, final response, stderr, and "
            "audit are stored once at transcript_artifacts. Hidden provider "
            "chain-of-thought is not exposed by the API."
        ),
    }
    if destination.is_file():
        value = read_json(destination)
        if value.get("module") != module or value.get("run_name") != run_name:
            raise ValueError("module log destination belongs to another run")
        if "attempts" not in value:
            previous = {
                key: value.pop(key) for key in list(value)
                if key not in {"schema_version", "module", "run_name"}
            }
            value["attempts"] = [previous]
        value["attempts"].append(attempt)
    else:
        value = {
            "schema_version": 2,
            "module": module,
            "run_name": run_name,
            "attempts": [attempt],
        }
    write_json(destination, value)
    return value


def attach_module_outputs(
    destination: Path, outputs: dict[str, Path],
) -> None:
    value = read_json(destination)
    attempt = value["attempts"][-1]
    attempt["output_artifacts"] = {
        name: {
            **artifact_reference(path),
            "summary": output_summary(path),
        }
        for name, path in outputs.items()
    }
    write_json(destination, value)
