#!/usr/bin/env python3
"""Complete M2 in an isolated container with a parent-only Isabelle cache."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_INITIAL_REPO = Path(__file__).resolve().parent.parent
if str(_INITIAL_REPO) not in sys.path:
    sys.path.insert(0, str(_INITIAL_REPO))

from regen.refine_campaign import read_json, sha256_json, write_immutable_json
from regen.refine_contracts import sha256_path, verify_m1_sources


PLATFORM = "polyml-5.9.1_x86_64_32-linux"
PARENT_HEAP_SESSIONS = (
    "Pure", "HOL", "Word_Lib", "Lib", "ASpec", "AInvs", "BaseRefine",
)
SESSION_CATALOGS = (
    "lib/Word_Lib", "lib/ML_Utils", "lib/Basics", "lib/Eisbach_Tools",
    "lib/Monads", "lib/sep_algebra", "lib", "tools/c-parser",
    "tools/autocorres", "tools/asmrefine", "spec", "proof",
)
STOCK_TARGET_HEAPS = ("Refine", "CRefine")


def run(command: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, text=True, capture_output=True, timeout=timeout, check=False,
    )


def immutable_copy(source: Path, destination: Path) -> str:
    if destination.exists():
        if sha256_path(source) != sha256_path(destination):
            raise FileExistsError(f"immutable copy differs: {destination}")
        return "unchanged"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return "created"


def proof_source_paths(campaign_dir: Path) -> list[str]:
    inventory = read_json(
        campaign_dir / "M1" / "private" / "proof-source-forbidden-inventory.json"
    )
    return sorted(item["source_path"] for item in inventory["exact_proof_sources"])


def l4v_relative_path(source_path: str) -> str:
    prefix = "verification/l4v/"
    if not source_path.startswith(prefix):
        raise ValueError(f"forbidden Proof source is outside l4v: {source_path}")
    return source_path.removeprefix(prefix)


def prepare_sanitized_l4v(repo: Path, campaign_dir: Path, stage_dir: Path) -> dict[str, Any]:
    source = repo / "verification" / "l4v"
    destination = stage_dir / "isolated-environment" / "l4v"
    forbidden = [l4v_relative_path(item) for item in proof_source_paths(campaign_dir)]
    completed_manifest = stage_dir / "sanitized-l4v-manifest.json"
    if not completed_manifest.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "rsync", "-aL", "--delete", "--delete-excluded",
            "--exclude=/.git/", "--exclude=/spec/cspec/c/build/",
        ]
        command.extend(f"--exclude=/{path}" for path in forbidden)
        command.extend([f"{source}/", f"{destination}/"])
        result = run(command)
        if result.returncode:
            raise RuntimeError(f"sanitized l4v copy failed: {result.stderr}")

    materialized = [path for path in forbidden if (destination / path).exists()]
    symlinks = [str(path.relative_to(destination)) for path in destination.rglob("*") if path.is_symlink()]
    if materialized or symlinks:
        raise ValueError(
            f"sanitized l4v is unsafe: forbidden={materialized}, symlinks={symlinks[:10]}"
        )
    records = []
    for path in sorted(item for item in destination.rglob("*") if item.is_file()):
        records.append({
            "path": str(path.relative_to(destination)),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_path(path),
        })
    return {
        "path": str(destination),
        "file_count": len(records),
        "tree_payload_sha256": sha256_json(records),
        "forbidden_sources_excluded": len(forbidden),
        "forbidden_sources_materialized": materialized,
        "symlink_count": 0,
    }


def docker_image_id(image: str) -> str:
    result = run(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
    if result.returncode:
        raise RuntimeError(f"cannot inspect image {image}: {result.stderr}")
    return result.stdout.strip()


def container_heap_record(container: str, session: str) -> dict[str, Any]:
    path = f"/root/.isabelle/heaps/{PLATFORM}/{session}"
    log_path = f"/root/.isabelle/heaps/{PLATFORM}/log/{session}.db"
    result = run([
        "docker", "exec", container, "sh", "-lc",
        f"sha256sum {shlex.quote(path)} && stat -c %s {shlex.quote(path)} && "
        f"sha256sum {shlex.quote(log_path)} && stat -c %s {shlex.quote(log_path)}",
    ])
    lines = result.stdout.splitlines()
    if result.returncode or len(lines) != 4:
        raise RuntimeError(f"parent heap unavailable for {session}: {result.stderr}")
    return {
        "session": session,
        "source_container_path": path,
        "sha256": lines[0].split()[0],
        "size_bytes": int(lines[1]),
        "source_log_path": log_path,
        "log_sha256": lines[2].split()[0],
        "log_size_bytes": int(lines[3]),
    }


def prepare_parent_cache(
    repo: Path, stage_dir: Path, *, source_container: str, image: str,
) -> dict[str, Any]:
    cache_root = stage_dir / "parent-cache"
    platform_dir = cache_root / "heaps" / PLATFORM
    platform_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for session in PARENT_HEAP_SESSIONS:
        record = container_heap_record(source_container, session)
        destination = platform_dir / session
        if destination.exists():
            if sha256_path(destination) != record["sha256"]:
                raise FileExistsError(f"parent heap identity changed: {destination}")
            disposition = "unchanged"
        else:
            temporary = destination.with_suffix(".incoming")
            copied = run([
                "docker", "cp",
                f"{source_container}:{record['source_container_path']}",
                str(temporary),
            ])
            if copied.returncode:
                raise RuntimeError(f"could not copy parent heap {session}: {copied.stderr}")
            if sha256_path(temporary) != record["sha256"]:
                temporary.unlink(missing_ok=True)
                raise ValueError(f"copied parent heap hash mismatch: {session}")
            temporary.replace(destination)
            disposition = "created"
        log_destination = platform_dir / "log" / f"{session}.db"
        if log_destination.exists():
            if sha256_path(log_destination) != record["log_sha256"]:
                raise FileExistsError(f"parent log identity changed: {log_destination}")
            log_disposition = "unchanged"
        else:
            log_destination.parent.mkdir(parents=True, exist_ok=True)
            temporary_log = log_destination.with_suffix(".incoming")
            copied_log = run([
                "docker", "cp", f"{source_container}:{record['source_log_path']}",
                str(temporary_log),
            ])
            if copied_log.returncode:
                raise RuntimeError(f"could not copy parent log {session}: {copied_log.stderr}")
            if sha256_path(temporary_log) != record["log_sha256"]:
                temporary_log.unlink(missing_ok=True)
                raise ValueError(f"copied parent log hash mismatch: {session}")
            temporary_log.replace(log_destination)
            log_disposition = "created"
        records.append({
            **record,
            "cache_path": str(destination),
            "cache_log_path": str(log_destination),
        })

    executable = repo / "verification" / "isabelle" / "bin" / "isabelle"
    identity = {
        "schema_version": 1,
        "artifact_type": "m2-parent-cache-manifest",
        "source_container": source_container,
        "container_image": image,
        "container_image_id": docker_image_id(image),
        "platform": PLATFORM,
        "isabelle_executable_sha256": sha256_path(executable),
        "heap_records": records,
    }
    stable_content = {
        "container_image_id": identity["container_image_id"],
        "platform": PLATFORM,
        "isabelle_executable_sha256": identity["isabelle_executable_sha256"],
        "heap_records": [
            {
                "session": item["session"],
                "sha256": item["sha256"],
                "size_bytes": item["size_bytes"],
                "log_sha256": item["log_sha256"],
                "log_size_bytes": item["log_size_bytes"],
            }
            for item in records
        ],
    }
    identity["parent_cache_content_sha256"] = sha256_json(stable_content)
    identity["parent_cache_payload_sha256"] = sha256_json(identity)
    write_immutable_json(stage_dir / "parent-cache-manifest.json", identity)
    return identity


def prepare_run_heaps(stage_dir: Path, parent_cache: dict[str, Any]) -> Path:
    destination = stage_dir / "isolated-environment" / "run-heaps" / PLATFORM
    destination.mkdir(parents=True, exist_ok=True)
    for record in parent_cache["heap_records"]:
        immutable_copy(Path(record["cache_path"]), destination / record["session"])
        immutable_copy(
            Path(record["cache_log_path"]), destination / "log" / f"{record['session']}.db",
        )
    for forbidden in STOCK_TARGET_HEAPS:
        target = destination / forbidden
        if target.exists():
            raise ValueError(f"stock target heap leaked into parent-only cache: {target}")
    return destination.parent


def tree_payload(path: Path) -> dict[str, Any]:
    records = [
        {
            "path": str(item.relative_to(path)),
            "size_bytes": item.stat().st_size,
            "sha256": sha256_path(item),
        }
        for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file())
    ]
    return {"file_count": len(records), "payload_sha256": sha256_json(records)}

def payload_without_hash(value: dict[str, Any], hash_field: str) -> dict[str, Any]:
    result = dict(value)
    result.pop(hash_field, None)
    return result


def verify_payload_hash(value: dict[str, Any], hash_field: str) -> None:
    expected = value.get(hash_field)
    actual = sha256_json(payload_without_hash(value, hash_field))
    if expected != actual:
        raise ValueError(
            f"payload hash mismatch for {value.get('artifact_type')}: {expected} != {actual}"
        )


def artifact_binding(campaign_dir: Path, path: Path) -> dict[str, Any]:
    campaign = campaign_dir.resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(campaign)
    except ValueError as error:
        raise ValueError(f"M2 evidence is outside the campaign directory: {path}") from error
    if not resolved.is_file():
        raise FileNotFoundError(f"M2 evidence is missing: {resolved}")
    return {
        "path": str(relative),
        "sha256": sha256_path(resolved),
        "size_bytes": resolved.stat().st_size,
    }


def verify_artifact_binding(campaign_dir: Path, record: dict[str, Any]) -> Path:
    relative = Path(record["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe M2 artifact binding path: {relative}")
    path = campaign_dir / relative
    if not path.is_file():
        raise FileNotFoundError(f"bound M2 artifact is missing: {path}")
    if path.stat().st_size != record.get("size_bytes"):
        raise ValueError(f"bound M2 artifact size drifted: {path}")
    if sha256_path(path) != record.get("sha256"):
        raise ValueError(f"bound M2 artifact hash drifted: {path}")
    return path



def reuse_support_cache(
    run_heaps: Path, *, source_stage: Path, source_preflight: Path,
    current_preflight: Path,
) -> dict[str, Any]:
    source_inputs = tree_payload(source_preflight / "sanitized-workspace" / "frozen-support")
    current_inputs = tree_payload(current_preflight / "sanitized-workspace" / "frozen-support")
    if source_inputs != current_inputs:
        raise ValueError("refusing Support heap reuse: frozen-support inputs differ")

    source_gate = read_json(source_stage / "m2-gate.json")
    if (
        source_gate.get("verdict") != "pass"
        or source_gate.get("contract_elaboration") != "pass"
    ):
        raise ValueError("refusing Support heap reuse: source M2 gate is not PASS")

    source_platform = source_stage / "isolated-environment" / "run-heaps" / PLATFORM
    observed_source_identity = support_heap_identity(
        source_stage / "isolated-environment" / "run-heaps"
    )
    if observed_source_identity != source_gate.get("support_heap_identity"):
        raise ValueError("refusing Support heap reuse: source heap identity drifted")
    target_platform = run_heaps / PLATFORM
    files = [
        (source_platform / "M2_Frozen_Support", target_platform / "M2_Frozen_Support"),
        (
            source_platform / "log" / "M2_Frozen_Support.db",
            target_platform / "log" / "M2_Frozen_Support.db",
        ),
    ]
    records = []
    for source, target in files:
        if not source.is_file():
            raise FileNotFoundError(f"Support cache artifact is missing: {source}")
        immutable_copy(source, target)
        records.append({
            "source": str(source),
            "target": str(target),
            "sha256": sha256_path(source),
            "size_bytes": source.stat().st_size,
        })
    result = {
        "schema_version": 1,
        "artifact_type": "m2-support-cache-reuse",
        "source_stage": str(source_stage),
        "source_preflight": str(source_preflight),
        "current_preflight": str(current_preflight),
        "source_campaign_id": source_gate.get("campaign_id"),
        "source_gate_sha256": sha256_path(source_stage / "m2-gate.json"),
        "frozen_support_identity": current_inputs,
        "files": records,
    }
    result["support_cache_content_sha256"] = sha256_json({
        "frozen_support_identity": current_inputs,
        "files": [
            {"sha256": item["sha256"], "size_bytes": item["size_bytes"]}
            for item in records
        ],
    })
    result["reuse_payload_sha256"] = sha256_json(result)
    return result


def reuse_contract_shell_cache(
    run_heaps: Path, *, source_stage: Path, source_preflight: Path,
    current_preflight: Path,
) -> dict[str, Any]:
    source_gate = read_json(source_stage / "m2-gate.json")
    if source_gate.get("verdict") != "pass":
        raise ValueError("refusing contract-shell heap reuse: source M2 gate is not PASS")
    source_inputs = tree_payload(source_preflight / "sanitized-workspace" / "contract-shells")
    current_inputs = tree_payload(current_preflight / "sanitized-workspace" / "contract-shells")
    if source_inputs != current_inputs:
        raise ValueError("refusing contract-shell heap reuse: shell inputs differ")
    source_platform = source_stage / "isolated-environment" / "run-heaps" / PLATFORM
    target_platform = run_heaps / PLATFORM
    files = [
        (
            source_platform / "log" / "M2_Contract_Shells.db",
            target_platform / "log" / "M2_Contract_Shells.db",
        ),
    ]
    optional_log = source_platform / "log" / "M2_Contract_Shells.gz"
    if optional_log.is_file():
        files.append((optional_log, target_platform / "log" / optional_log.name))
    records = []
    for source, target in files:
        if not source.is_file():
            raise FileNotFoundError(f"contract-shell cache artifact is missing: {source}")
        immutable_copy(source, target)
        records.append({
            "source": str(source), "target": str(target),
            "sha256": sha256_path(source), "size_bytes": source.stat().st_size,
        })
    stable = {
        "contract_shell_identity": current_inputs,
        "files": [
            {"sha256": item["sha256"], "size_bytes": item["size_bytes"]}
            for item in records
        ],
    }
    result = {
        "schema_version": 1,
        "artifact_type": "m2-contract-shell-cache-reuse",
        "source_stage": str(source_stage),
        "source_preflight": str(source_preflight),
        "current_preflight": str(current_preflight),
        "source_campaign_id": source_gate.get("campaign_id"),
        "source_gate_sha256": sha256_path(source_stage / "m2-gate.json"),
        "contract_shell_identity": current_inputs,
        "files": records,
        "content_sha256": sha256_json(stable),
    }

    result["reuse_payload_sha256"] = sha256_json(result)
    return result

def support_heap_identity(run_heaps: Path) -> dict[str, Any] | None:
    platform = run_heaps / PLATFORM
    files = [
        platform / "M2_Frozen_Support",
        platform / "log" / "M2_Frozen_Support.db",
    ]
    if not all(path.is_file() for path in files):
        return None
    records = [
        {"name": path.name, "sha256": sha256_path(path), "size_bytes": path.stat().st_size}
        for path in files
    ]
    return {"files": records, "content_sha256": sha256_json(records)}


def mount_args(
    repo: Path, sanitized_l4v: Path, workspace: Path, run_heaps: Path,
) -> list[str]:
    return [
        "--network", "none",
        "--mount", f"type=bind,src={sanitized_l4v},dst=/sel4-project/verification/l4v,readonly",
        "--mount", f"type=bind,src={repo / 'verification/isabelle'},dst=/workspace/verification/isabelle,readonly",
        "--mount", f"type=bind,src={workspace},dst=/m2,readonly",
        "--mount", f"type=bind,src={run_heaps},dst=/root/.isabelle/heaps",
        "--tmpfs", "/tmp:rw,exec,nosuid,size=2g",
    ]


def container_forbidden_paths(campaign_dir: Path) -> list[str]:
    paths = [
        "/sel4-project/verification/l4v/" + l4v_relative_path(item)
        for item in proof_source_paths(campaign_dir)
    ]
    paths.extend(f"/root/.isabelle/heaps/{PLATFORM}/{name}" for name in STOCK_TARGET_HEAPS)
    return paths


def access_policy(
    repo: Path, campaign_dir: Path, *, image: str, image_id: str,
    sanitized_l4v: Path, workspace: Path, run_heaps: Path,
) -> dict[str, Any]:
    policy = {
        "schema_version": 1,
        "artifact_type": "m2-isolated-access-policy",
        "container_image": image,
        "container_image_id": image_id,
        "network": "none",
        "mounts": [
            {"destination": "/sel4-project/verification/l4v", "source": str(sanitized_l4v), "mode": "read-only"},
            {"destination": "/workspace/verification/isabelle", "source": str(repo / "verification/isabelle"), "mode": "read-only"},
            {"destination": "/m2", "source": str(workspace), "mode": "read-only"},
            {"destination": "/root/.isabelle/heaps", "source": str(run_heaps), "mode": "read-write-parent-only"},
        ],
        "full_host_workspace_mounted": False,
        "docker_socket_mounted": False,
        "forbidden_read_paths": container_forbidden_paths(campaign_dir),
    }
    policy["access_policy_sha256"] = sha256_json(policy)
    return policy


def run_denial_probes(
    repo: Path, campaign_dir: Path, *, image: str, mounts: list[str],
    policy: dict[str, Any], shells: Path,
) -> dict[str, Any]:
    forbidden = policy["forbidden_read_paths"]
    positive = [
        "/sel4-project/verification/l4v/proof/refine/base/Include.thy",
        "/m2/contract-shells/ROOT",
    ]
    script_lines = ["set +e"]
    for index, path in enumerate(forbidden):
        script_lines.extend([
            f"if cat {shlex.quote(path)} >/dev/null 2>&1; then",
            f"  printf 'DENY\\t{index}\\tFAIL\\n'",
            "else",
            f"  printf 'DENY\\t{index}\\tPASS\\n'",
            "fi",
        ])
    for index, path in enumerate(positive):
        script_lines.extend([
            f"if cat {shlex.quote(path)} >/dev/null 2>&1; then",
            f"  printf 'ALLOW\\t{index}\\tPASS\\n'",
            "else",
            f"  printf 'ALLOW\\t{index}\\tFAIL\\n'",
            "fi",
        ])
    command = ["docker", "run", "--rm", *mounts, image, "sh", "-lc", "\n".join(script_lines)]
    result = run(command, timeout=120)
    observed = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            observed[(parts[0], int(parts[1]))] = parts[2]
    denial_records = [
        {"path": path, "operation": "cat", "expected": "denied", "verdict": observed.get(("DENY", index), "MISSING").lower()}
        for index, path in enumerate(forbidden)
    ]
    allow_records = [
        {"path": path, "operation": "cat", "expected": "readable", "verdict": observed.get(("ALLOW", index), "MISSING").lower()}
        for index, path in enumerate(positive)
    ]
    passed = (
        result.returncode == 0
        and all(item["verdict"] == "pass" for item in denial_records + allow_records)
    )
    return {
        "schema_version": 1,
        "artifact_type": "m2-source-access-isolation-results",
        "campaign_id": read_json(campaign_dir / "M0" / "campaign-manifest.json")["campaign_id"],
        "stage": "Refine",
        "verdict": "pass" if passed else "fail",
        "access_policy_identity": policy,
        "denial_probes": denial_records,
        "positive_control_probes": allow_records,
        "container_returncode": result.returncode,
        "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest(),
    }


def run_shell_elaboration(
    campaign_dir: Path, *, image: str, mounts: list[str], timeout_seconds: int,
) -> dict[str, Any]:
    isabelle = "/workspace/verification/isabelle/bin/isabelle"
    command = ["docker", "run", "--rm", *mounts, "-e", "L4V_ARCH=ARM", image,
               "timeout", str(timeout_seconds), isabelle, "build", "-S", "-v"]
    for catalog in SESSION_CATALOGS:
        command.extend(["-d", f"/sel4-project/verification/l4v/{catalog}"])
    command.extend([
        "-D", "/m2/frozen-support",
        "-D", "/m2/contract-shells",
        "M2_Contract_Shells",
    ])
    started = time.monotonic()
    result = run(command, timeout=timeout_seconds + 60)
    wall_ms = int((time.monotonic() - started) * 1000)
    verdict = "timeout" if result.returncode == 124 else ("pass" if result.returncode == 0 else "fail")
    return {
        "schema_version": 1,
        "artifact_type": "m2-contract-elaboration-results",
        "campaign_id": read_json(campaign_dir / "M0" / "campaign-manifest.json")["campaign_id"],
        "stage": "Refine",
        "shell_session": "M2_Contract_Shells",
        "verdict": verdict,
        "returncode": result.returncode,
        "timeout_seconds": timeout_seconds,
        "wall_ms": wall_ms,
        "command": command,
        "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest(),
        "stdout_tail": result.stdout[-8000:],
        "stderr_tail": result.stderr[-8000:],
    }

def verify_physical_m2_environment(
    campaign_dir: Path, stage_dir: Path, workspace_path: Path,
    workspace: dict[str, Any], sanitized: dict[str, Any],
    parent: dict[str, Any], gate: dict[str, Any],
) -> None:
    expected_workspace = workspace_path.parent / "sanitized-workspace"
    recorded_workspace = Path(workspace["workspace_path"])
    if recorded_workspace.resolve() != expected_workspace.resolve():
        raise ValueError("M2 workspace path does not match its bound preflight")
    if any(item.is_symlink() for item in recorded_workspace.rglob("*")):
        raise ValueError("M2 workspace contains a symlink")
    observed_workspace = tree_payload(recorded_workspace)
    if workspace.get("workspace_tree_identity") != {
        "file_count": observed_workspace["file_count"],
        "files_payload_sha256": observed_workspace["payload_sha256"],
    }:
        raise ValueError("M2 physical workspace identity drifted")

    expected_l4v = stage_dir / "isolated-environment" / "l4v"
    recorded_l4v = Path(sanitized["path"])
    if recorded_l4v.resolve() != expected_l4v.resolve():
        raise ValueError("M2 sanitized-l4v path escapes its stage")
    if any(item.is_symlink() for item in recorded_l4v.rglob("*")):
        raise ValueError("M2 sanitized l4v contains a symlink")
    observed_l4v = tree_payload(recorded_l4v)
    if (
        observed_l4v["file_count"] != sanitized.get("file_count")
        or observed_l4v["payload_sha256"] != sanitized.get("tree_payload_sha256")
    ):
        raise ValueError("M2 physical sanitized-l4v identity drifted")
    for source_path in proof_source_paths(campaign_dir):
        if (recorded_l4v / l4v_relative_path(source_path)).exists():
            raise ValueError(f"M2 forbidden Proof source rematerialized: {source_path}")

    parent_platform = stage_dir / "parent-cache" / "heaps" / PLATFORM
    run_platform = stage_dir / "isolated-environment" / "run-heaps" / PLATFORM
    if parent.get("platform") != PLATFORM:
        raise ValueError("M2 parent-cache platform mismatch")
    for record in parent.get("heap_records", []):
        expected_paths = (
            (parent_platform / record["session"], record["sha256"], record["size_bytes"]),
            (parent_platform / "log" / f"{record['session']}.db",
             record["log_sha256"], record["log_size_bytes"]),
            (run_platform / record["session"], record["sha256"], record["size_bytes"]),
            (run_platform / "log" / f"{record['session']}.db",
             record["log_sha256"], record["log_size_bytes"]),
        )
        for path, digest, size in expected_paths:
            if (
                path.is_symlink() or not path.is_file()
                or path.stat().st_size != size or sha256_path(path) != digest
            ):
                raise ValueError(f"M2 parent/run heap identity drifted: {path}")
    for target in STOCK_TARGET_HEAPS:
        if (run_platform / target).exists():
            raise ValueError(f"stock target heap rematerialized: {target}")
    if support_heap_identity(run_platform.parent) != gate.get("support_heap_identity"):
        raise ValueError("M2 physical Support heap identity drifted")

    for binding_name in ("support_cache_reuse", "contract_shell_cache_reuse"):
        binding = gate.get("artifact_bindings", {}).get(binding_name)
        if binding is None:
            continue
        reuse = read_json(campaign_dir / binding["path"])
        for record in reuse.get("files", []):
            target = Path(record["target"])
            try:
                target.resolve().relative_to(run_platform.resolve())
            except ValueError as error:
                raise ValueError(f"M2 reused cache target escapes run heaps: {target}") from error
            if (
                target.is_symlink() or not target.is_file()
                or target.stat().st_size != record["size_bytes"]
                or sha256_path(target) != record["sha256"]
            ):
                raise ValueError(f"M2 reused cache artifact drifted: {target}")


def verify_m2_artifacts(campaign_dir: Path, stage_dir: Path) -> dict[str, Any]:
    gate = read_json(stage_dir / "m2-gate.json")
    verify_payload_hash(gate, "gate_payload_sha256")
    manifest = read_json(campaign_dir / "M0" / "campaign-manifest.json")
    campaign_id = manifest["campaign_id"]
    if gate.get("campaign_id") != campaign_id or gate.get("verdict") != "pass":
        raise ValueError("M2 PASS gate is missing or belongs to another campaign")

    bindings = gate.get("artifact_bindings")
    if not isinstance(bindings, dict):
        raise ValueError("M2 gate has no artifact bindings")
    required = {
        "m1_artifact_seal",
        "preflight_workspace_manifest",
        "preflight_workspace_content_audit",
        "sanitized_l4v_manifest",
        "parent_cache_manifest",
        "source_access_isolation_results",
        "contract_elaboration_results",
    }
    missing = required - set(bindings)
    if missing:
        raise ValueError(f"M2 gate is missing required artifact bindings: {sorted(missing)}")
    paths = {
        name: verify_artifact_binding(campaign_dir, record)
        for name, record in bindings.items()
    }

    workspace = read_json(paths["preflight_workspace_manifest"])
    verify_payload_hash(workspace, "manifest_payload_sha256")
    parent = read_json(paths["parent_cache_manifest"])
    verify_payload_hash(parent, "parent_cache_payload_sha256")
    isolation = read_json(paths["source_access_isolation_results"])
    elaboration = read_json(paths["contract_elaboration_results"])
    sanitized = read_json(paths["sanitized_l4v_manifest"])
    content = read_json(paths["preflight_workspace_content_audit"])

    for artifact in (workspace, isolation, elaboration):
        if artifact.get("campaign_id") != campaign_id:
            raise ValueError("bound M2 artifact belongs to another campaign")
    policy = isolation.get("access_policy_identity", {})
    verify_payload_hash(policy, "access_policy_sha256")
    if (
        content.get("verdict") != "pass"
        or isolation.get("verdict") != "pass"
        or elaboration.get("verdict") != "pass"
        or elaboration.get("returncode") != 0
    ):
        raise ValueError("bound M2 evidence is not PASS")
    if gate.get("workspace_content_audit", {}).get("verdict") != content["verdict"]:
        raise ValueError("M2 workspace-content verdict does not match bound evidence")
    if gate.get("source_access_isolation") != isolation["verdict"]:
        raise ValueError("M2 isolation verdict does not match bound evidence")
    if gate.get("contract_elaboration") != elaboration["verdict"]:
        raise ValueError("M2 elaboration verdict does not match bound evidence")
    if gate.get("parent_cache_identity") != parent["parent_cache_content_sha256"]:
        raise ValueError("M2 parent-cache identity does not match bound evidence")
    if gate.get("sanitized_l4v_identity") != sanitized["tree_payload_sha256"]:
        raise ValueError("M2 sanitized-l4v identity does not match bound evidence")
    if gate.get("proof_package_count") != workspace.get("proof_package_count"):
        raise ValueError("M2 Proof-package count does not match bound workspace")
    if gate.get("contract_count") != workspace.get("contract_count"):
        raise ValueError("M2 contract count does not match bound workspace")

    reuse_specs = (
        ("support_cache_reuse", "reuse_payload_sha256",
         "support_cache_content_sha256", "support_cache_reuse_identity"),
        ("contract_shell_cache_reuse", "reuse_payload_sha256",
         "content_sha256", "contract_shell_cache_reuse_identity"),
    )
    for binding_name, payload_hash, identity_field, gate_field in reuse_specs:
        if binding_name in paths:
            reuse = read_json(paths[binding_name])
            verify_payload_hash(reuse, payload_hash)
            if not reuse.get("source_campaign_id"):
                raise ValueError(f"M2 cache source campaign is missing: {binding_name}")
            if not reuse.get("source_gate_sha256"):
                raise ValueError(f"M2 cache source gate identity is missing: {binding_name}")
            if gate.get(gate_field) != reuse.get(identity_field):
                raise ValueError(f"M2 cache identity mismatch: {binding_name}")
        elif gate.get(gate_field) is not None:
            raise ValueError(f"M2 gate names unbound cache reuse: {binding_name}")
    verify_physical_m2_environment(
        campaign_dir, stage_dir, paths["preflight_workspace_manifest"],
        workspace, sanitized, parent, gate,
    )
    return {**gate, "replay": "unchanged"}


def verify_m2_stage(repo: Path, campaign_dir: Path, stage_dir: Path) -> dict[str, Any]:
    verify_m1_sources(repo, campaign_dir / "M1" / "private" / "contract-manifest.json")
    return verify_m2_artifacts(campaign_dir, stage_dir)


def complete_m2(
    repo: Path, campaign_dir: Path, preflight_dir: Path, stage_dir: Path, *,
    source_container: str, image: str, timeout_seconds: int,
    reuse_support_stage: Path | None = None,
    reuse_support_preflight: Path | None = None,
    reuse_contract_stage: Path | None = None,
    reuse_contract_preflight: Path | None = None,
) -> dict[str, Any]:
    verify_m1_sources(repo, campaign_dir / "M1" / "private" / "contract-manifest.json")
    sanitized = prepare_sanitized_l4v(repo, campaign_dir, stage_dir)
    parent_cache = prepare_parent_cache(
        repo, stage_dir, source_container=source_container, image=image,
    )
    run_heaps = prepare_run_heaps(stage_dir, parent_cache)
    support_reuse = None
    if reuse_support_stage is not None or reuse_support_preflight is not None:
        if reuse_support_stage is None or reuse_support_preflight is None:
            raise ValueError("Support cache reuse requires both source stage and source preflight")
        support_reuse = reuse_support_cache(
            run_heaps, source_stage=reuse_support_stage,
            source_preflight=reuse_support_preflight, current_preflight=preflight_dir,
        )
        write_immutable_json(stage_dir / "support-cache-reuse.json", support_reuse)
    contract_reuse = None
    if reuse_contract_stage is not None or reuse_contract_preflight is not None:
        if reuse_contract_stage is None or reuse_contract_preflight is None:
            raise ValueError("contract-shell cache reuse requires source stage and preflight")
        contract_reuse = reuse_contract_shell_cache(
            run_heaps, source_stage=reuse_contract_stage,
            source_preflight=reuse_contract_preflight, current_preflight=preflight_dir,
        )
        write_immutable_json(stage_dir / "contract-shell-cache-reuse.json", contract_reuse)
    workspace = preflight_dir / "sanitized-workspace"
    shells = workspace / "contract-shells"
    mounts = mount_args(repo, Path(sanitized["path"]), workspace, run_heaps)
    policy = access_policy(
        repo, campaign_dir, image=image, image_id=parent_cache["container_image_id"],
        sanitized_l4v=Path(sanitized["path"]), workspace=workspace, run_heaps=run_heaps,
    )
    isolation = run_denial_probes(
        repo, campaign_dir, image=image, mounts=mounts, policy=policy, shells=shells,
    )
    elaboration = run_shell_elaboration(
        campaign_dir, image=image, mounts=mounts, timeout_seconds=timeout_seconds,
    ) if isolation["verdict"] == "pass" else {
        "schema_version": 1,
        "artifact_type": "m2-contract-elaboration-results",
        "campaign_id": isolation["campaign_id"],
        "stage": "Refine",
        "verdict": "blocked",
        "reason": "source-access isolation probes failed",
    }
    final_support_identity = support_heap_identity(run_heaps)
    content_path = preflight_dir / "workspace-content-audit.json"
    if not content_path.exists():
        content_path = preflight_dir / "source-access-audit.json"
    old_content = read_json(content_path)
    content_verdict = old_content["verdict"]
    gate_verdict = "pass" if (
        content_verdict == isolation["verdict"] == elaboration["verdict"] == "pass"
    ) else "incomplete" if "fail" not in {content_verdict, isolation["verdict"], elaboration["verdict"]} else "fail"
    sanitized_path = stage_dir / "sanitized-l4v-manifest.json"
    isolation_path = stage_dir / "source-access-isolation-results.json"
    elaboration_path = stage_dir / "contract-elaboration-results.json"
    write_immutable_json(sanitized_path, sanitized)
    write_immutable_json(isolation_path, isolation)
    write_immutable_json(elaboration_path, elaboration)

    workspace_manifest_path = preflight_dir / "sanitized-workspace-manifest.json"
    workspace_manifest = read_json(workspace_manifest_path)
    bindings = {
        "m1_artifact_seal": artifact_binding(
            campaign_dir, campaign_dir / "M1" / "artifact-seal.json"
        ),
        "preflight_workspace_manifest": artifact_binding(
            campaign_dir, workspace_manifest_path
        ),
        "preflight_workspace_content_audit": artifact_binding(campaign_dir, content_path),
        "sanitized_l4v_manifest": artifact_binding(campaign_dir, sanitized_path),
        "parent_cache_manifest": artifact_binding(
            campaign_dir, stage_dir / "parent-cache-manifest.json"
        ),
        "source_access_isolation_results": artifact_binding(campaign_dir, isolation_path),
        "contract_elaboration_results": artifact_binding(campaign_dir, elaboration_path),
    }
    if support_reuse is not None:
        bindings["support_cache_reuse"] = artifact_binding(
            campaign_dir, stage_dir / "support-cache-reuse.json"
        )
    if contract_reuse is not None:
        bindings["contract_shell_cache_reuse"] = artifact_binding(
            campaign_dir, stage_dir / "contract-shell-cache-reuse.json"
        )
    gate = {
        "schema_version": 1,
        "artifact_type": "m2-gate",
        "campaign_id": isolation["campaign_id"],
        "stage": "Refine",
        "verdict": gate_verdict,
        "workspace_content_audit": {
            "verdict": content_verdict,
            "source_artifact": bindings["preflight_workspace_content_audit"]["path"],
            "scope": "materialized-workspace-content-only",
        },
        "source_access_isolation": isolation["verdict"],
        "contract_elaboration": elaboration["verdict"],
        "parent_cache_identity": parent_cache["parent_cache_content_sha256"],
        "sanitized_l4v_identity": sanitized["tree_payload_sha256"],
        "support_cache_reuse_identity": (
            None if support_reuse is None else support_reuse["support_cache_content_sha256"]
        ),
        "support_heap_identity": final_support_identity,
        "contract_shell_cache_reuse_identity": (
            None if contract_reuse is None else contract_reuse["content_sha256"]
        ),
        "proof_package_count": workspace_manifest["proof_package_count"],
        "contract_count": workspace_manifest["contract_count"],
        "artifact_bindings": bindings,
        "harness_source_hashes": {
            "refine_m2_preflight.py": sha256_path(repo / "regen/refine_m2_preflight.py"),
            "refine_m2_isolated.py": sha256_path(repo / "regen/refine_m2_isolated.py"),
            "parse_roots.py": sha256_path(repo / "tools/lemma_inventory/parse_roots.py"),
        },
    }
    gate["gate_payload_sha256"] = sha256_json(gate)
    write_immutable_json(stage_dir / "m2-gate.json", gate)
    if gate_verdict == "pass":
        verify_m2_artifacts(campaign_dir, stage_dir)
    return gate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=_INITIAL_REPO)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--preflight-dir", type=Path, required=True)
    parser.add_argument("--stage-dir", type=Path, required=True)
    parser.add_argument("--source-container", default="sel4-l4v")
    parser.add_argument("--image", default="sel4-public:tuned")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--reuse-support-stage", type=Path)
    parser.add_argument("--reuse-support-preflight", type=Path)
    parser.add_argument("--reuse-contract-stage", type=Path)
    parser.add_argument("--reuse-contract-preflight", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = args.repo.resolve()
    resolve = lambda path: path if path.is_absolute() else repo / path
    reuse_stage = None if args.reuse_support_stage is None else resolve(args.reuse_support_stage)
    reuse_preflight = (
        None if args.reuse_support_preflight is None else resolve(args.reuse_support_preflight)
    )
    reuse_contract_stage = (
        None if args.reuse_contract_stage is None else resolve(args.reuse_contract_stage)
    )
    reuse_contract_preflight = (
        None if args.reuse_contract_preflight is None else resolve(args.reuse_contract_preflight)
    )
    gate = complete_m2(
        repo, resolve(args.campaign_dir), resolve(args.preflight_dir),
        resolve(args.stage_dir), source_container=args.source_container,
        image=args.image, timeout_seconds=args.timeout_seconds,
        reuse_support_stage=reuse_stage, reuse_support_preflight=reuse_preflight,
        reuse_contract_stage=reuse_contract_stage,
        reuse_contract_preflight=reuse_contract_preflight,
    )
    print(json.dumps(gate, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if gate["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
