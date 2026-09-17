#!/usr/bin/env python3
"""Recorded, no-tool-audited LLM calls for regeneration experiments."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


MODEL_CONFIG = Path(__file__).resolve().parents[1] / "config/proof-models.json"

def model_settings():
    return json.loads(MODEL_CONFIG.read_text())

PASSIVE_ITEM_TYPES = {"reasoning", "agent_message", "error"}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def audit_events(raw_path: Path) -> dict:
    event_types: dict[str, int] = {}
    item_types: dict[str, int] = {}
    parse_errors = 0
    for line in raw_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            parse_errors += 1
            continue
        event_type = str(event.get("type", "unknown"))
        event_types[event_type] = event_types.get(event_type, 0) + 1
        item = event.get("item")
        if isinstance(item, dict):
            item_type = str(item.get("type", "unknown"))
            item_types[item_type] = item_types.get(item_type, 0) + 1
    # Codex emits item.type=error for transport fallback and reconnect
    # diagnostics. It is not a model tool invocation. Unknown active item
    # types remain forbidden, including command_execution and file_change.
    forbidden = sorted(set(item_types) - PASSIVE_ITEM_TYPES)
    transport_diagnostics = (
        event_types.get("error", 0) + item_types.get("error", 0)
    )
    return {
        "event_types": event_types,
        "item_types": item_types,
        "parse_errors": parse_errors,
        "passive_item_types": sorted(PASSIVE_ITEM_TYPES),
        # Keep the old key for existing transcript consumers.
        "allowed_item_types": sorted(PASSIVE_ITEM_TYPES),
        "forbidden_item_types": forbidden,
        "no_tool_events": not forbidden,
        "transport_diagnostic_count": transport_diagnostics,
    }


def call_codex(
    prompt: str,
    artifact_prefix: Path,
    *,
    model: str = "gpt-5.5",
    timeout: int = 1800,
    codex_bin: Path | None = None,
    clean_runtime: bool = False,
    reasoning_effort: str | None = None,
) -> str:
    """Run one Codex turn and persist enough evidence to reproduce/audit it."""
    if len(prompt) > 1000000:
        raise ValueError('input_too_large: locally rejected before provider call')
    artifact_prefix.parent.mkdir(parents=True, exist_ok=True)
    prompt_path = artifact_prefix.with_suffix(".prompt.txt")
    raw_path = artifact_prefix.with_suffix(".ndjson")
    final_path = artifact_prefix.with_suffix(".final.txt")
    stderr_path = artifact_prefix.with_suffix(".stderr.txt")
    meta_path = artifact_prefix.with_suffix(".meta.json")
    audit_path = artifact_prefix.with_suffix(".audit.json")
    artifacts = [
        prompt_path, raw_path, final_path, stderr_path, meta_path, audit_path,
    ]
    existing = [str(path) for path in artifacts if path.exists()]
    if existing:
        raise FileExistsError(
            "refusing to overwrite an LLM transcript: " + ", ".join(existing)
        )
    prompt_path.write_text(prompt, encoding="utf-8")
    raw_path.write_text("", encoding="utf-8")
    final_path.write_text("", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")
    audit_path.write_text(json.dumps({
        "state": "running", "no_tool_events": None,
        "note": "Provider call has not reached a terminal state.",
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    settings = model_settings()
    override = codex_bin or os.environ.get("REGEN_CODEX_BIN")
    executable = Path(override or settings["codex_bin"]).resolve()
    if not executable.is_file():
        raise RuntimeError(f"configured Codex CLI not found: {executable}")
    executable_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
    if not override and executable_hash != settings["codex_sha256"]:
        raise RuntimeError("configured Codex CLI changed; explicitly refresh proof-models.json")
    effort = reasoning_effort or settings["reasoning_effort"]
    if effort not in {"low", "medium", "high", "xhigh", "max"}:
        raise ValueError(f"unsupported explicit reasoning effort: {effort}")

    sandbox = Path("/tmp/regen-repository-planner-sanitized")
    sandbox.mkdir(parents=True, exist_ok=True)
    runtime_args = []
    if clean_runtime:
        runtime_args = [
            "--ignore-user-config", "--ignore-rules", "--ephemeral",
            "-c", f'model_reasoning_effort="{effort}"',
            "--disable", "apps", "--disable", "plugins",
            "--disable", "remote_plugin", "--disable", "hooks",
        ]
    argv = [
        str(executable), "-m", model, "-C", str(sandbox), "-s", "read-only",
        "exec", "--skip-git-repo-check", *runtime_args,
        "--json", "-o", str(final_path), "-",
    ]
    started = datetime.now(timezone.utc).isoformat()
    t0 = time.time()
    timed_out = False
    meta_path.write_text(json.dumps({
        "schema_version": 2,
        "state": "running",
        "provider": "codex-exec-audited",
        "model": model,
        "reasoning_effort": effort if clean_runtime else None,
        "codex_bin": str(executable),
        "codex_sha256": executable_hash,
        "started_at": started,
        "returncode": None,
        "timed_out": False,
        "sealable_generation": False,
        "prompt_path": str(prompt_path),
        "raw_path": str(raw_path),
        "final_path": str(final_path),
        "stderr_path": str(stderr_path),
        "audit_path": str(audit_path),
        "prompt_sha256": sha256_text(prompt),
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        proc = subprocess.run(
            argv, input=prompt, text=True, capture_output=True, timeout=timeout,
        )
        stdout, stderr, returncode = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        def output_text(value: str | bytes | None) -> str:
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace")
            return value or ""

        # TimeoutExpired may carry bytes even when subprocess.run used
        # text=True. Normalize before persisting the partial provider stream.
        stdout = output_text(exc.stdout)
        stderr = output_text(exc.stderr)
        returncode = 124
        timed_out = True
    raw_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    audit = audit_events(raw_path)
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    final = final_path.read_text(
        encoding="utf-8", errors="replace"
    ) if final_path.exists() else ""
    final_path.write_text(final, encoding="utf-8")
    meta = {
        "state": "complete" if returncode == 0 else "failed",
        "provider": "codex-exec-audited",
        "model": model,
        "reasoning_effort": effort if clean_runtime else None,
        "codex_bin": str(executable),
        "codex_sha256": executable_hash,
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "wall_ms": int((time.time() - t0) * 1000),
        "returncode": returncode,
        "timed_out": timed_out,
        "clean_runtime": clean_runtime,
        "cwd": str(sandbox),
        "sandbox": "read-only",
        "provider_network_call": True,
        "model_network_tool_access": False,
        "argv": argv,
        "prompt_sha256": sha256_text(prompt),
        "final_sha256": sha256_text(final),
        "prompt_path": str(prompt_path),
        "raw_path": str(raw_path),
        "final_path": str(final_path),
        "stderr_path": str(stderr_path),
        "audit_path": str(audit_path),
        "no_tool_events": audit["no_tool_events"],
        "sealable_generation": bool(
            returncode == 0 and not timed_out and audit["no_tool_events"]
        ),
    }
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if returncode != 0 and 'input_too_large' in stderr_path.read_text(encoding='utf-8', errors='replace'):
        raise ValueError('input_too_large: provider rejected the input; do not retry unchanged')
    if returncode != 0:
        raise RuntimeError(f"Codex failed with exit code {returncode}; see {stderr_path}")
    if not audit["no_tool_events"]:
        raise RuntimeError(
            f"Codex used forbidden tools {audit['forbidden_item_types']}; output rejected"
        )
    if not final.strip():
        raise RuntimeError("Codex returned an empty final answer")
    return final


def record_file_agent(
    prompt: str,
    response: str,
    artifact_prefix: Path,
    *,
    model: str = "current-codex-session",
) -> str:
    """Archive a response produced by the current audited agent session."""
    artifact_prefix.parent.mkdir(parents=True, exist_ok=True)
    prompt_path = artifact_prefix.with_suffix(".prompt.txt")
    raw_path = artifact_prefix.with_suffix(".ndjson")
    final_path = artifact_prefix.with_suffix(".final.txt")
    meta_path = artifact_prefix.with_suffix(".meta.json")
    audit_path = artifact_prefix.with_suffix(".audit.json")
    prompt_path.write_text(prompt, encoding="utf-8")
    final_path.write_text(response, encoding="utf-8")
    raw_path.write_text(
        json.dumps({
            "type": "file_agent.completed",
            "model": model,
            "prompt_sha256": sha256_text(prompt),
            "answer_sha256": sha256_text(response),
        }, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    audit = {
        "event_types": {"file_agent.completed": 1},
        "item_types": {},
        "parse_errors": 0,
        "forbidden_item_types": [],
        "no_tool_events": True,
        "note": "Response supplied by the current Codex conversation; no second network call.",
    }
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    meta = {
        "provider": "current-session-file-agent",
        "model": model,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "prompt_sha256": sha256_text(prompt),
        "final_sha256": sha256_text(response),
        "prompt_path": str(prompt_path),
        "raw_path": str(raw_path),
        "final_path": str(final_path),
        "audit_path": str(audit_path),
        "no_tool_events": True,
        "independence": "not independent; generated in the orchestrating Codex session",
    }
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return response
