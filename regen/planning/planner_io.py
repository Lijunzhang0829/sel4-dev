"""Shared audited I/O helpers for repository planning model calls."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from regen.infrastructure.llm_transcript import (
    artifact_paths,
    reconcile_transcript_family,
    validate_call_artifact,
)


FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_model_json(text: str) -> dict[str, Any]:
    """Parse one planner JSON object, tolerating a surrounding Markdown fence."""
    cleaned = FENCE_RE.sub("", text.strip())
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("repository planner response contains no JSON object")
        value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("repository planner response must be a JSON object")
    return value


def select_completed_transcript(
    preferred: Path, *, prompt: str, model: str,
) -> tuple[Path, dict[str, Any]] | None:
    """Return the newest sealable physical call for one logical planner call."""
    expected_prompt = prompt.encode("utf-8")
    for item in reversed(reconcile_transcript_family(preferred)):
        if item["underlying_state"] != "complete":
            continue
        prefix = Path(item["prefix"])
        try:
            meta = validate_call_artifact(prefix, require_real=True)
        except ValueError:
            continue
        paths = artifact_paths(prefix)
        if paths["prompt"].read_bytes() != expected_prompt:
            continue
        if meta.get("model") != model:
            continue
        return prefix, meta
