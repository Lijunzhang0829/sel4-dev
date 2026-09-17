"""Append-only repository-plan revisions with one stable identity per run."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import secrets
from typing import Any

from regen.preparation.refine_campaign import sha256_json, write_immutable_json


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_projection(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def lineage_root(planning_root: Path, identity: dict[str, Any]) -> Path:
    return planning_root / identity["plan_id"]


def start_plan(
    planning_root: Path, benchmark_id: str, *, derived_from_plan_id: str | None = None,
    identity_token: str | None = None,
) -> dict[str, Any]:
    identity_path = planning_root / "identity.json"
    if identity_path.exists():
        identity = read_json(identity_path)
        if identity["benchmark_id"] != benchmark_id:
            raise ValueError("plan identity belongs to a different benchmark")
        if identity.get("derived_from_plan_id") != derived_from_plan_id:
            raise ValueError("plan derivation identity changed")
        return identity
    token = identity_token or secrets.token_hex(12)
    core = {
        "schema_version": 1,
        "artifact_type": "repository-plan-identity",
        "benchmark_id": benchmark_id,
        "derived_from_plan_id": derived_from_plan_id,
        "identity_token_sha256": sha256_json(token),
    }
    identity = {
        **core,
        "plan_id": "repository-plan:" + sha256_json(core)[:20],
    }
    write_immutable_json(identity_path, identity)
    return identity


def _theory_projection(plan: dict[str, Any], theory: str) -> dict[str, Any]:
    record = next((item for item in plan["theories"] if item["name"] == theory), None)
    if record is None:
        raise ValueError(f"started theory disappeared from plan: {theory}")
    edges = sorted(
        item for item in plan.get("suggested_theory_edges", [])
        if item["provider"] == theory or item["consumer"] == theory
    )
    return {"theory": record, "incident_edges": edges}


def append_revision(
    planning_root: Path, plan: dict[str, Any], *,
    started_theories: dict[str, str] | None = None,
) -> dict[str, Any]:
    identity = read_json(planning_root / "identity.json")
    lineage = lineage_root(planning_root, identity)
    revisions = lineage / "revisions"
    existing = sorted(revisions.glob("*.json")) if revisions.exists() else []
    history = [read_json(path) for path in existing]
    previous = history[-1] if history else None
    sequence = len(existing) + 1
    if previous and previous["revision"] != sequence - 1:
        raise ValueError("repository-plan revision sequence is not contiguous")
    started_theories = started_theories or {}
    if previous:
        by_revision = {item["revision_id"]: item for item in history}
        for theory, frozen_revision_id in started_theories.items():
            frozen = by_revision.get(frozen_revision_id)
            if frozen is None:
                raise ValueError(f"started theory {theory} has an unknown frozen revision")
            if _theory_projection(frozen["plan"], theory) != _theory_projection(plan, theory):
                raise ValueError(f"repository-plan revision changes started theory: {theory}")
    content = deepcopy(plan)
    content.pop("plan_id", None)
    content.pop("revision", None)
    content.pop("revision_id", None)
    content.pop("parent_revision_id", None)
    revision_core = {
        "schema_version": 1,
        "artifact_type": "repository-plan-revision",
        "plan_id": identity["plan_id"],
        "revision": sequence,
        "parent_revision_id": previous["revision_id"] if previous else None,
        "plan": content,
    }
    revision = {
        **revision_core,
        "revision_id": "repository-plan-revision:" + sha256_json(revision_core)[:20],
    }
    write_immutable_json(revisions / f"{sequence:04d}.json", revision)
    head = {
        "plan_id": revision["plan_id"],
        "revision": sequence,
        "revision_id": revision["revision_id"],
        "path": f"revisions/{sequence:04d}.json",
    }
    write_projection(lineage / "HEAD.json", head)
    current = {
        **content,
        "plan_id": revision["plan_id"],
        "revision": sequence,
        "revision_id": revision["revision_id"],
        "parent_revision_id": revision["parent_revision_id"],
    }
    write_projection(planning_root.parent / "repository-plan.json", current)
    return current


def verify_revisions(planning_root: Path) -> dict[str, Any]:
    identity = read_json(planning_root / "identity.json")
    lineage = lineage_root(planning_root, identity)
    paths = sorted((lineage / "revisions").glob("*.json"))
    parent = None
    for sequence, path in enumerate(paths, 1):
        revision = read_json(path)
        core = {key: value for key, value in revision.items() if key != "revision_id"}
        expected = "repository-plan-revision:" + sha256_json(core)[:20]
        if (
            revision["plan_id"] != identity["plan_id"]
            or revision["revision"] != sequence
            or revision["parent_revision_id"] != parent
            or revision["revision_id"] != expected
        ):
            raise ValueError(f"invalid repository-plan revision: {path.name}")
        parent = revision["revision_id"]
    head = read_json(lineage / "HEAD.json")
    if not paths or head["revision_id"] != parent or head["revision"] != len(paths):
        raise ValueError("repository-plan HEAD does not identify the latest revision")
    return read_json(paths[-1])
