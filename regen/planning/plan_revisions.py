"""Append-only repository-plan revisions with one stable identity per run."""
from __future__ import annotations

from copy import deepcopy
from regen.planning.import_contract import import_edges
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
        (item for item in import_edges(plan)
         if item["provider"] == theory or item["consumer"] == theory),
        key=lambda item: (item["provider"], item["consumer"], item.get("reason", "")),
    )
    return {"theory": record, "incident_edges": edges}


def append_revision(
    planning_root: Path, plan: dict[str, Any], *,
    started_theories: dict[str, int] | None = None,
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
    if history:
        verify_revisions(planning_root, check_head=False)
    started_theories = started_theories or {}
    if previous:
        by_revision = {item["revision"]: item for item in history}
        for theory, frozen_sequence in started_theories.items():
            frozen = by_revision.get(frozen_sequence)
            if frozen is None:
                raise ValueError(f"started theory {theory} has an unknown frozen revision")
            if _theory_projection(frozen["plan"], theory) != _theory_projection(plan, theory):
                raise ValueError(f"repository-plan revision changes started theory: {theory}")
    content = deepcopy(plan)
    for key in ("plan_id", "revision", "revision_id", "parent_revision_id"):
        content.pop(key, None)
    # Replaying a completed or interrupted publication must not create history.
    if previous and previous["plan"] == content:
        revision = previous
    else:
        core = {
            "schema_version": 2,
            "artifact_type": "repository-plan-revision",
            "plan_id": identity["plan_id"],
            "revision": sequence,
            "plan": content,
        }
        revision = {**core, "sha256": sha256_json(core)}
        write_immutable_json(revisions / f"{sequence:04d}.json", revision)
    sequence = revision["revision"]
    write_projection(lineage / "HEAD.json", {
        "plan_id": identity["plan_id"], "revision": sequence,
        "path": f"revisions/{sequence:04d}.json",
    })
    current = {
        **content, "plan_id": identity["plan_id"], "revision": sequence,
    }
    write_projection(planning_root.parent / "repository-plan.json", current)
    return current


def verify_revisions(planning_root: Path, *, check_head: bool = True) -> dict[str, Any]:
    identity = read_json(planning_root / "identity.json")
    lineage = lineage_root(planning_root, identity)
    paths = sorted((lineage / "revisions").glob("*.json"))
    if not paths:
        raise ValueError("repository-plan history is empty")
    parent = None
    for sequence, path in enumerate(paths, 1):
        revision = read_json(path)
        if revision.get("schema_version") == 1:
            # Read old logs without rewriting accepted evidence.
            core = {k: v for k, v in revision.items() if k != "revision_id"}
            valid = (revision.get("revision_id") ==
                     "repository-plan-revision:" + sha256_json(core)[:20]
                     and revision.get("parent_revision_id") == parent)
            parent = revision.get("revision_id")
        else:
            core = {k: v for k, v in revision.items() if k != "sha256"}
            valid = (revision.get("schema_version") == 2
                     and revision.get("sha256") == sha256_json(core))
        if (not valid or revision["plan_id"] != identity["plan_id"]
                or revision["revision"] != sequence
                or path.name != f"{sequence:04d}.json"):
            raise ValueError(f"invalid repository-plan revision: {path.name}")
    if check_head:
        head = read_json(lineage / "HEAD.json")
        if (head.get("plan_id") != identity["plan_id"]
                or head.get("revision") != len(paths)
                or head.get("path") != f"revisions/{len(paths):04d}.json"):
            raise ValueError("repository-plan HEAD does not identify the latest revision")
    return revision
