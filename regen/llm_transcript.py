#!/usr/bin/env python3
"""Unified audited LLM backend and seal-time transcript validation."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

try:
    from .audited_llm import call_codex as _call_codex
    from .audited_llm import record_file_agent as _record_file_agent
    from .audited_llm import sha256_text
except ImportError:
    from audited_llm import call_codex as _call_codex
    from audited_llm import record_file_agent as _record_file_agent
    from audited_llm import sha256_text

SCHEMA_VERSION = 2
from regen.progress_events import emit_event

REAL_PROVIDERS = {"codex-exec-audited", "claude-print-audited"}

class TranscriptSink(Protocol):
    def record_llm_artifact(self, prefix: Path, *, stage: str) -> None: ...



def artifact_paths(prefix: Path) -> dict[str, Path]:
    suffixes = {
        "prompt": ".prompt.txt", "raw": ".ndjson", "final": ".final.txt",
        "stderr": ".stderr.txt", "meta": ".meta.json", "audit": ".audit.json",
    }
    return {name: Path(str(prefix) + suffix) for name, suffix in suffixes.items()}


def allocate_artifact_prefix(preferred: Path) -> Path:
    """Allocate a transcript prefix without ever reusing prior evidence."""
    if not any(path.exists() for path in artifact_paths(preferred).values()):
        return preferred
    for index in range(1, 10000):
        candidate = preferred.with_name(
            f"{preferred.name}--call-{index:03d}"
        )
        if not any(path.exists() for path in artifact_paths(candidate).values()):
            return candidate
    raise RuntimeError(f"cannot allocate transcript prefix after {preferred}")


def transcript_health(prefix: Path, *, orphaned: bool = False) -> dict[str, Any]:
    """Classify transcript damage without making generation fail."""
    paths = artifact_paths(prefix)
    present = [name for name, path in paths.items() if path.exists()]
    if not present:
        return {"state": "absent", "missing": sorted(paths)}
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        return {"state": "corrupt", "missing": missing}
    try:
        meta = json.loads(paths["meta"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {"state": "corrupt", "error": str(error)}
    if meta.get("state") == "running":
        return {"state": "interrupted" if orphaned else "active"}
    mismatches = []
    for field, actual in _hashes(paths).items():
        expected = meta.get(field)
        if expected is not None and expected != actual:
            mismatches.append(field)
    if mismatches:
        return {"state": "corrupt", "hash_mismatches": mismatches}
    if meta.get("returncode") == 0 and not meta.get("timed_out"):
        return {"state": "complete"}
    return {"state": "failed"}


def reconcile_transcript_family(
    preferred: Path, *, orphaned: bool = True,
) -> list[dict[str, Any]]:
    """Return logical-call history; older physical calls are superseded."""
    prefixes = []
    if any(path.exists() for path in artifact_paths(preferred).values()):
        prefixes.append(preferred)
    for meta in sorted(preferred.parent.glob(
        f"{preferred.name}--call-*.meta.json"
    )):
        prefixes.append(Path(str(meta).removesuffix(".meta.json")))
    result = []
    for index, prefix in enumerate(prefixes):
        health = transcript_health(prefix, orphaned=orphaned)
        state = health["state"]
        if index < len(prefixes) - 1:
            state = "superseded"
        result.append({
            "prefix": str(prefix), "state": state,
            "underlying_state": health["state"],
        })
    return result


def _events(path: Path) -> tuple[list[dict[str, Any]], int]:
    result, errors = [], 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            errors += 1
            continue
        if isinstance(event, dict):
            result.append(event)
    return result, errors


def _hashes(paths: dict[str, Path]) -> dict[str, str]:
    return {
        f"{name}_sha256": sha256_text(path.read_text(encoding="utf-8", errors="replace"))
        for name, path in paths.items()
        if name in {"prompt", "raw", "final", "stderr", "audit"}
        and path.is_file()
    }


def _upgrade(prefix: Path, *, theory: str | None, stage: str | None,
             sealable: bool, network_call: bool) -> None:
    paths = artifact_paths(prefix)
    paths["stderr"].touch(exist_ok=True)
    meta = json.loads(paths["meta"].read_text(encoding="utf-8"))
    meta.update({
        "schema_version": SCHEMA_VERSION, "theory": theory, "stage": stage,
        "provider_network_call": network_call, "sealable_generation": sealable,
        **_hashes(paths),
        **{f"{name}_path": str(path) for name, path in paths.items() if name != "meta"},
    })
    paths["meta"].write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def call_codex(prompt: str, artifact_prefix: Path, *, model: str = "gpt-5.5",
               timeout: int = 1800, theory: str | None = None,
               stage: str | None = None,
               clean_runtime: bool = False) -> str:
    try:
        final = _call_codex(
            prompt, artifact_prefix, model=model, timeout=timeout,
            clean_runtime=clean_runtime)
    finally:
        if artifact_paths(artifact_prefix)["meta"].exists():
            meta = json.loads(
                artifact_paths(artifact_prefix)["meta"].read_text(
                    encoding="utf-8"
                )
            )
            sealable = bool(
                meta.get("returncode") == 0
                and not meta.get("timed_out")
                and meta.get("no_tool_events")
            )
            _upgrade(artifact_prefix, theory=theory, stage=stage,
                     sealable=sealable, network_call=True)
    return final


def _relay_token() -> str:
    token = os.environ.get("CODEX_RELAY_TOKEN", "")
    if token:
        return token
    env_path = Path(os.environ.get(
        "CODEX_RELAY_ENV", "/home/lijun/codex-relay/.env",
    ))
    if env_path.is_file():
        for line in env_path.read_text(
            encoding="utf-8", errors="replace",
        ).splitlines():
            if line.startswith("CODEX_RELAY_TOKEN="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("codex-relay token is unavailable")


def call_codex_relay(
    prompt: str, artifact_prefix: Path, *, model: str = "gpt-5.5",
    timeout: int = 1800, theory: str | None = None,
    stage: str | None = None,
) -> str:
    """Call the text relay and preserve a non-sealable development transcript."""
    artifact_prefix.parent.mkdir(parents=True, exist_ok=True)
    paths = artifact_paths(artifact_prefix)
    paths["prompt"].write_text(prompt, encoding="utf-8")
    url = os.environ.get(
        "REGEN_CODEX_RELAY_URL", "http://127.0.0.1:18080/v1/codex",
    )
    repo = os.environ.get("REGEN_CODEX_RELAY_REPO", "default")
    payload = {
        "repo": repo, "model": model, "prompt": prompt, "write": False,
    }
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={
            "Authorization": "Bearer " + _relay_token(),
            "Content-Type": "application/json",
        },
    )
    started = datetime.now(timezone.utc).isoformat()
    t0, status_code, error = time.time(), None, ""
    raw = ""
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status_code = response.status
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        raw = exc.read().decode("utf-8", errors="replace")
        error = str(exc)
    except Exception as exc:
        error = repr(exc)
    paths["raw"].write_text(raw, encoding="utf-8")
    answer, relay_error = "", ""
    if raw:
        try:
            response_payload = json.loads(raw)
            if isinstance(response_payload, dict):
                answer = str(
                    response_payload.get("answer")
                    or response_payload.get("result")
                    or response_payload.get("message")
                    or ""
                )
                relay_error = str(response_payload.get("error") or "")
            else:
                answer = raw
        except json.JSONDecodeError:
            answer = raw
    paths["final"].write_text(answer, encoding="utf-8")
    paths["stderr"].write_text(error or relay_error, encoding="utf-8")
    audit = {
        "provider": "codex-relay",
        "no_tool_events": None,
        "no_tool_events_observed": True,
        "tool_event_visibility": "unavailable-from-relay-api",
        "sealable_generation": False,
        "note": (
            "The HTTP relay exposes text only. No tool event was observed in "
            "the response, but absence of model tool use cannot be proven."
        ),
    }
    paths["audit"].write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    success = (
        status_code == 200 and not error and not relay_error
        and bool(answer.strip())
    )
    meta = {
        "schema_version": SCHEMA_VERSION,
        "provider": "codex-relay",
        "model": model,
        "theory": theory,
        "stage": stage,
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "wall_ms": int((time.time() - t0) * 1000),
        "returncode": 0 if success else 1,
        "timed_out": False,
        "status_code": status_code,
        "relay_url": url,
        "repo": repo,
        "provider_network_call": True,
        "model_network_tool_access": "unknown",
        "sealable_generation": False,
        "no_tool_events": None,
        "no_tool_events_observed": True,
        **_hashes(paths),
        **{
            f"{name}_path": str(path)
            for name, path in paths.items() if name != "meta"
        },
    }
    paths["meta"].write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    if not success:
        detail = error or relay_error or "empty answer"
        raise RuntimeError(f"codex-relay failed (HTTP {status_code}): {detail}")
    return answer


def _claude_audit(raw: Path) -> dict[str, Any]:
    events, parse_errors = _events(raw)
    event_types: dict[str, int] = {}
    content_types: dict[str, int] = {}
    forbidden = []
    for event in events:
        kind = str(event.get("type", "unknown"))
        event_types[kind] = event_types.get(kind, 0) + 1
        message = event.get("message")
        blocks = message.get("content", []) if isinstance(message, dict) else []
        for block in blocks if isinstance(blocks, list) else []:
            if not isinstance(block, dict):
                continue
            kind = str(block.get("type", "unknown"))
            content_types[kind] = content_types.get(kind, 0) + 1
            if kind in {"tool_use", "server_tool_use", "mcp_tool_use"}:
                forbidden.append(kind)
    return {
        "provider": "claude-print-audited", "event_types": event_types,
        "content_types": content_types, "parse_errors": parse_errors,
        "forbidden_content_types": sorted(set(forbidden)),
        "no_tool_events": not forbidden,
    }


def _claude_final(raw: Path) -> str:
    result = ""
    for event in _events(raw)[0]:
        if event.get("type") == "result" and isinstance(event.get("result"), str):
            result = event["result"]
        message = event.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            text = "".join(
                block.get("text", "") for block in message.get("content", [])
                if isinstance(block, dict) and block.get("type") == "text"
            )
            if text:
                result = text
    return result


def call_claude(prompt: str, artifact_prefix: Path, *, model: str = "opus",
                timeout: int = 1800, max_turns: int = 1,
                theory: str | None = None, stage: str | None = None) -> str:
    """Run Claude print mode with tools disabled and preserve stream-json."""
    artifact_prefix.parent.mkdir(parents=True, exist_ok=True)
    paths = artifact_paths(artifact_prefix)
    paths["prompt"].write_text(prompt, encoding="utf-8")
    executable = os.environ.get("REGEN_CLAUDE_BIN") or shutil.which("claude")
    if not executable:
        raise RuntimeError("Claude CLI not found")
    cwd = Path("/tmp/regen-llm-sanitized")
    cwd.mkdir(parents=True, exist_ok=True)
    argv = [
        executable, "-p", prompt, "--model", model, "--max-turns", str(max_turns),
        "--tools", "", "--output-format", "stream-json", "--verbose",
    ]
    started = datetime.now(timezone.utc).isoformat()
    t0, timed_out = time.time(), False
    try:
        proc = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, timeout=timeout)
        stdout, stderr, returncode = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, returncode, timed_out = exc.stdout or "", exc.stderr or "", 124, True
    paths["raw"].write_text(stdout, encoding="utf-8")
    paths["stderr"].write_text(stderr, encoding="utf-8")
    final = _claude_final(paths["raw"])
    paths["final"].write_text(final, encoding="utf-8")
    audit = _claude_audit(paths["raw"])
    paths["audit"].write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    meta = {
        "schema_version": SCHEMA_VERSION, "provider": "claude-print-audited",
        "model": model, "theory": theory, "stage": stage,
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "wall_ms": int((time.time() - t0) * 1000), "returncode": returncode,
        "timed_out": timed_out, "cwd": str(cwd), "provider_network_call": True,
        "model_network_tool_access": False, "sealable_generation": True,
        "tools": [], "max_turns": max_turns,
        "argv": ["<PROMPT_IN_FILE>" if arg == prompt else arg for arg in argv],
        **_hashes(paths),
        **{f"{name}_path": str(path) for name, path in paths.items() if name != "meta"},
        "no_tool_events": audit["no_tool_events"],
    }
    paths["meta"].write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if returncode != 0:
        raise RuntimeError(f"Claude failed with exit code {returncode}; see {paths['stderr']}")
    if not audit["no_tool_events"]:
        raise RuntimeError(f"Claude tool use detected; see {paths['audit']}")
    if not final.strip():
        raise RuntimeError("Claude returned an empty final answer")
    return final


def call_llm(provider: str, prompt: str, artifact_prefix: Path, *, model: str,
             timeout: int = 1800, theory: str | None = None,
             stage: str | None = None,
             clean_runtime: bool = False,
             transcript_sink: TranscriptSink | None = None) -> str:
    backend = {
        "codex": call_codex,
        "codex-relay": call_codex_relay,
        "claude": call_claude,
    }.get(provider)
    if backend is None:
        raise ValueError(f"unsupported audited provider: {provider}")
    emit_event(
        "llm_started", provider=provider, model=model, stage=stage,
        theory=theory, timeout_seconds=timeout,
        artifact_prefix=str(artifact_prefix), clean_runtime=clean_runtime,
    )
    started = time.time()
    finished = threading.Event()

    def heartbeat() -> None:
        while not finished.wait(30):
            emit_event(
                "llm_heartbeat", provider=provider, model=model, stage=stage,
                theory=theory, elapsed_seconds=int(time.time() - started),
                artifact_prefix=str(artifact_prefix),
            )

    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
    heartbeat_thread.start()
    try:
        kwargs = {
            "model": model, "timeout": timeout,
            "theory": theory, "stage": stage,
        }
        if provider == "codex":
            kwargs["clean_runtime"] = clean_runtime
        return backend(prompt, artifact_prefix, **kwargs)
    finally:
        finished.set()
        heartbeat_thread.join(timeout=1)
        paths = artifact_paths(artifact_prefix)
        meta = {}
        if paths["meta"].is_file():
            try:
                meta = json.loads(paths["meta"].read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        emit_event(
            "llm_finished", provider=provider, model=model, stage=stage,
            theory=theory,
            wall_ms=meta.get("wall_ms", int((time.time() - started) * 1000)),
            returncode=meta.get("returncode"), timed_out=meta.get("timed_out"),
            turn_completed=(meta.get("returncode") == 0 and not meta.get("timed_out")),
            artifact_prefix=str(artifact_prefix),
        )
        if transcript_sink is not None and paths["meta"].is_file():
            transcript_sink.record_llm_artifact(
                artifact_prefix, stage=stage or artifact_prefix.name,
            )


def record_replay(prompt: str, response: str, artifact_prefix: Path, *,
                  model: str = "development-replay", theory: str | None = None,
                  stage: str | None = None,
                  transcript_sink: TranscriptSink | None = None) -> str:
    """Archive supplied text while explicitly making it non-sealable."""
    result = _record_file_agent(prompt, response, artifact_prefix, model=model)
    _upgrade(artifact_prefix, theory=theory, stage=stage,
             sealable=False, network_call=False)
    if transcript_sink is not None:
        transcript_sink.record_llm_artifact(
            artifact_prefix, stage=stage or artifact_prefix.name,
        )
    return result


def inspect_call_artifact(artifact_prefix: Path, *, require_real: bool = True,
                          require_success: bool = True) -> dict[str, Any]:
    paths = artifact_paths(artifact_prefix)
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise ValueError(f"incomplete LLM transcript {artifact_prefix}: missing {missing}")
    meta = json.loads(paths["meta"].read_text(encoding="utf-8"))
    audit = json.loads(paths["audit"].read_text(encoding="utf-8"))
    if require_real and meta.get("provider") not in REAL_PROVIDERS:
        raise ValueError(f"non-network/replay transcript cannot seal: {artifact_prefix}")
    for field, actual in _hashes(paths).items():
        if meta.get(field) != actual:
            raise ValueError(f"transcript hash mismatch for {field}: {artifact_prefix}")
    if require_real and not meta.get("provider_network_call"):
        raise ValueError(f"transcript did not perform a provider call: {artifact_prefix}")
    if require_real and not meta.get("sealable_generation"):
        raise ValueError(f"transcript is explicitly non-sealable: {artifact_prefix}")
    if require_success and (meta.get("returncode") != 0 or meta.get("timed_out")):
        raise ValueError(f"unsuccessful LLM transcript: {artifact_prefix}")
    if not meta.get("no_tool_events") or not audit.get("no_tool_events"):
        raise ValueError(f"tool-using LLM transcript cannot seal: {artifact_prefix}")
    required_nonempty = ["prompt"]
    if require_success:
        required_nonempty.append("raw")
        required_nonempty.append("final")
    for name in required_nonempty:
        if not paths[name].read_text(encoding="utf-8", errors="replace").strip():
            raise ValueError(f"empty {name} transcript: {artifact_prefix}")
    return meta


def validate_call_artifact(artifact_prefix: Path, *, require_real: bool = True) -> dict[str, Any]:
    return inspect_call_artifact(
        artifact_prefix, require_real=require_real, require_success=True,
    )
