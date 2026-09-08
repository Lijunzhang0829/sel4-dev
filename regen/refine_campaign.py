#!/usr/bin/env python3
"""Freeze a deterministic proof-regeneration campaign."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
DEFAULT_CONFIG = Path("regen/config/refine-setting-b-campaign.json")
DEFAULT_OUTPUT_ROOT = Path("reports/regen/refine-setting-b")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def repository_revision(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True,
        capture_output=True, check=True,
    )
    return result.stdout.strip()


def repository_is_clean(repo: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=normal"],
        cwd=repo, text=True, capture_output=True, check=True,
    )
    return not result.stdout.strip()


def repository_paths_are_clean(repo: Path, paths: list[str]) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=normal", "--", *paths],
        cwd=repo, text=True, capture_output=True, check=True,
    )
    return not result.stdout.strip()


def validate_config(config: dict[str, Any]) -> None:
    required = {
        "schema_version", "campaign_name", "architecture", "setting",
        "target_stages", "experimental_claim", "trust_boundary",
        "contract_selection_policy", "model_backends", "budgets",
        "completion_criteria", "frozen_input_files",
        "trusted_source_repositories", "forbidden_path_policy",
    }
    missing = sorted(required - config.keys())
    if missing:
        raise ValueError(f"campaign config is missing fields: {missing}")
    if config["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"unsupported config schema: {config['schema_version']}")
    if config["setting"] not in {"B", "Semantic-Base"}:
        raise ValueError(f"unsupported setting: {config['setting']}")
    stages = config["target_stages"]
    stage_names = (
        [item.get("stage") for item in stages]
        if isinstance(stages, list) else []
    )
    expected = (
        ["Refine", "CRefine"] if config["setting"] == "B" else ["Refine"]
    )
    if stage_names != expected:
        raise ValueError(f"target_stages must be {expected} for {config['setting']}")
    files = config["frozen_input_files"]
    if not isinstance(files, list) or not files or not all(isinstance(p, str) for p in files):
        raise ValueError("frozen_input_files must be a non-empty string list")
    if len(files) != len(set(files)):
        raise ValueError("frozen_input_files contains duplicates")
    repositories = config["trusted_source_repositories"]
    if not isinstance(repositories, list):
        raise ValueError("trusted_source_repositories must be a list")
    repository_ids = [item.get("id") for item in repositories]
    if any(not value for value in repository_ids) or len(repository_ids) != len(set(repository_ids)):
        raise ValueError("trusted source repository IDs must be non-empty and unique")
    for item in repositories:
        if not isinstance(item.get("path"), str):
            raise ValueError("trusted source repository path must be a string")
        paths = item.get("required_clean_paths")
        if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
            raise ValueError("each trusted source repository needs required_clean_paths")
    policy = config["forbidden_path_policy"]
    if not isinstance(policy, dict) or not policy.get("id"):
        raise ValueError("forbidden_path_policy must have an ID")
    patterns = policy.get("static_forbidden_path_patterns")
    if not isinstance(patterns, list) or not patterns:
        raise ValueError("forbidden_path_policy needs concrete static path patterns")


def frozen_file_records(repo: Path, paths: list[str]) -> list[dict[str, Any]]:
    repo = repo.resolve()
    records = []
    for relative in sorted(paths):
        candidate = repo / relative
        resolved = candidate.resolve()
        try:
            resolved.relative_to(repo)
        except ValueError as exc:
            raise ValueError(f"frozen input escapes repository: {relative}") from exc
        if candidate.is_symlink() or not resolved.is_file():
            raise ValueError(f"frozen input is not a regular repository file: {relative}")
        data = resolved.read_bytes()
        records.append({
            "path": relative,
            "sha256": sha256_bytes(data),
            "size_bytes": len(data),
        })
    return records


def trusted_repository_records(repo: Path, specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    for spec in sorted(specs, key=lambda item: item["id"]):
        path = (repo / spec["path"]).resolve()
        try:
            relative = str(path.relative_to(repo))
        except ValueError as exc:
            raise ValueError(f"trusted repository escapes workspace: {spec['path']}") from exc
        revision = repository_revision(path)
        clean = repository_paths_are_clean(path, spec["required_clean_paths"])
        if spec.get("require_clean", True) and not clean:
            raise ValueError(
                f"trusted repository {spec['id']} is dirty in required paths: "
                f"{spec['required_clean_paths']}"
            )
        records.append({
            "id": spec["id"],
            "path": relative,
            "revision": revision,
            "required_clean_paths": spec["required_clean_paths"],
            "required_clean": bool(spec.get("require_clean", True)),
            "paths_clean": clean,
        })
    return records


def build_manifest(
    repo: Path,
    config_path: Path,
    *,
    revision: str | None = None,
    worktree_clean: bool | None = None,
) -> dict[str, Any]:
    """Construct the Environment Freeze artifact without clocks, randomness, or model calls."""
    repo = repo.resolve()
    config_path = config_path if config_path.is_absolute() else repo / config_path
    config = read_json(config_path)
    validate_config(config)

    config_relative = str(config_path.resolve().relative_to(repo))
    config_payload = dict(config)
    input_records = frozen_file_records(repo, config["frozen_input_files"])
    source_repositories = trusted_repository_records(
        repo, config["trusted_source_repositories"],
    )
    frozen_body = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "campaign-manifest",
        "campaign_name": config["campaign_name"],
        "repository": {
            "revision": revision or repository_revision(repo),
            "worktree_clean": (
                repository_is_clean(repo) if worktree_clean is None else worktree_clean
            ),
            "dirty_worktree_policy": (
                "allowed only with content hashes for every declared frozen input; "
                "Contract Inventory separately hashes every trusted source it extracts"
            ),
        },
        "campaign_config": {
            "path": config_relative,
            "sha256": sha256_json(config_payload),
        },
        "frozen_inputs": input_records,
        "trusted_source_repositories": source_repositories,
        "forbidden_path_policy": config["forbidden_path_policy"],
        "architecture": config["architecture"],
        "setting": config["setting"],
        "target_stages": config["target_stages"],
        "experimental_claim": config["experimental_claim"],
        "trust_boundary": config["trust_boundary"],
        "contract_selection_policy": config["contract_selection_policy"],
        "model_backends": config["model_backends"],
        "budgets": config["budgets"],
        "completion_criteria": config["completion_criteria"],
        "staging_rule": (
            "Refine must be completely sealed before production CRefine starts; "
            "CRefine must consume the exact generated Refine heap"
            if config["setting"] == "B"
            else "This campaign generates and seals Refine only"
        ),
        "model_calls_performed": 0,
    }
    payload_hash = sha256_json(frozen_body)
    campaign_id = f"{config['campaign_name']}-{payload_hash[:16]}"
    return {
        **frozen_body,
        "campaign_id": campaign_id,
        "manifest_payload_sha256": payload_hash,
    }


def write_immutable_json(path: Path, value: dict[str, Any]) -> str:
    rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != rendered:
            raise FileExistsError(f"refusing to replace immutable artifact: {path}")
        return "unchanged"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")
    return "created"


def verify_manifest(manifest: dict[str, Any]) -> None:
    stored = manifest.get("manifest_payload_sha256")
    body = dict(manifest)
    body.pop("campaign_id", None)
    body.pop("manifest_payload_sha256", None)
    actual = sha256_json(body)
    if stored != actual:
        raise ValueError("manifest payload hash mismatch")
    expected_id = f"{manifest['campaign_name']}-{actual[:16]}"
    if manifest.get("campaign_id") != expected_id:
        raise ValueError("campaign ID does not match manifest payload")
    if manifest.get("model_calls_performed") != 0:
        raise ValueError("Environment Freeze must not perform model calls")


def verify_frozen_inputs(
    repo: Path, manifest: dict[str, Any], *, revision: str | None = None,
) -> None:
    """Prove that the current checkout still matches the frozen Environment Freeze inputs."""
    repo = repo.resolve()
    current_revision = revision or repository_revision(repo)
    if current_revision != manifest["repository"]["revision"]:
        raise ValueError(
            f"repository revision drift: {current_revision} != "
            f"{manifest['repository']['revision']}"
        )
    config_record = manifest["campaign_config"]
    config_path = repo / config_record["path"]
    if sha256_json(read_json(config_path)) != config_record["sha256"]:
        raise ValueError(f"campaign config drift: {config_record['path']}")
    for record in manifest["frozen_inputs"]:
        path = repo / record["path"]
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"frozen input missing or no longer regular: {record['path']}")
        data = path.read_bytes()
        if len(data) != record["size_bytes"] or sha256_bytes(data) != record["sha256"]:
            raise ValueError(f"frozen input drift: {record['path']}")
    for record in manifest.get("trusted_source_repositories", []):
        source_repo = repo / record["path"]
        current_revision = repository_revision(source_repo)
        if current_revision != record["revision"]:
            raise ValueError(
                f"trusted repository revision drift for {record['id']}: "
                f"{current_revision} != {record['revision']}"
            )
        clean = repository_paths_are_clean(source_repo, record["required_clean_paths"])
        if record["required_clean"] and not clean:
            raise ValueError(
                f"trusted repository path drift for {record['id']}: "
                f"{record['required_clean_paths']}"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--verify", type=Path, help="verify an existing manifest")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = args.repo.resolve()
    if args.verify:
        path = args.verify if args.verify.is_absolute() else repo / args.verify
        manifest = read_json(path)
        verify_manifest(manifest)
        verify_frozen_inputs(repo, manifest)
        print(f"verified {manifest['campaign_id']}: {path}")
        return 0

    manifest = build_manifest(repo, args.config)
    output_root = args.output_root if args.output_root.is_absolute() else repo / args.output_root
    output = output_root / manifest["campaign_id"] / "Environment-Freeze" / "campaign-manifest.json"
    disposition = write_immutable_json(output, manifest)
    print(f"{disposition} {manifest['campaign_id']}: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
