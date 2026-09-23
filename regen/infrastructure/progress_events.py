#!/usr/bin/env python3
"""Line-buffered and persistent progress protocol for modular-proof runs."""
from __future__ import annotations

import atexit
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOCK = threading.RLock()
_EVENT_PATH: Path | None = None
_STATUS_PATH: Path | None = None
_STATUS: dict[str, Any] = {}
_MONITOR_STOP = threading.Event()
_MONITOR_THREAD: threading.Thread | None = None


def _write_status_locked() -> None:
    if _STATUS_PATH is None:
        return
    temporary = _STATUS_PATH.with_suffix(_STATUS_PATH.suffix + ".tmp")
    temporary.write_text(
        json.dumps(_STATUS, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(_STATUS_PATH)


def configure_event_monitor(
    run_dir: Path, *, run_id: str, theory: str | None = None,
    heartbeat_seconds: float = 0.0,
) -> None:
    """Persist progress independently of the controlling terminal."""
    global _EVENT_PATH, _STATUS_PATH, _STATUS, _MONITOR_THREAD
    run_dir = Path(run_dir)
    event_path = run_dir / "runner-events.ndjson"
    status_path = run_dir / "runner-status.json"
    with _LOCK:
        if (
            _EVENT_PATH == event_path and _STATUS_PATH == status_path
            and _STATUS.get("running")
        ):
            _STATUS["theory"] = theory
            _write_status_locked()
            return
    close_event_monitor("reconfigured")
    run_dir.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        _EVENT_PATH = event_path
        _STATUS_PATH = status_path
        _MONITOR_STOP.clear()
        _STATUS = {
            "schema_version": 1,
            "run_id": run_id,
            "theory": theory,
            "pid": os.getpid(),
            "running": True,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "last_event": None,
            "active_event": None,
        }
        _write_status_locked()
    # heartbeat_seconds remains accepted for older callers; no periodic writer.


def close_event_monitor(reason: str = "completed") -> None:
    """Mark runtime monitoring terminal without changing proof state."""
    global _EVENT_PATH, _STATUS_PATH, _MONITOR_THREAD
    with _LOCK:
        if _STATUS_PATH is None or not _STATUS.get("running"):
            return
    emit_event("runner_monitor_stopped", reason=reason, pid=os.getpid())
    _MONITOR_STOP.set()
    with _LOCK:
        _STATUS["running"] = False
        _STATUS["stopped_at"] = datetime.now(timezone.utc).isoformat()
        _STATUS["stop_reason"] = reason
        _write_status_locked()
        thread = _MONITOR_THREAD
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=1.0)
    with _LOCK:
        _EVENT_PATH = None
        _STATUS_PATH = None
        _MONITOR_THREAD = None


def emit_event(event: str, **fields: Any) -> None:
    # Periodic liveness is not work progress; retain only transitions.
    if event.endswith("_heartbeat"):
        return
    payload = {
        "time": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **{key: value for key, value in fields.items() if value is not None},
    }
    serialized = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), default=str,
    )
    # The terminal is a secondary sink: losing it must not erase durable status
    # or turn successful proof work into a BrokenPipe infrastructure failure.
    with _LOCK:
        if _EVENT_PATH is not None:
            with _EVENT_PATH.open("a", encoding="utf-8") as stream:
                stream.write(serialized + "\n")
            _STATUS["last_event"] = payload
            _STATUS["last_event_at"] = payload["time"]
            if event != "runner_heartbeat":
                _STATUS["active_event"] = payload
                _STATUS["last_transition_at"] = payload["time"]
                if event == "runner_failed":
                    _STATUS["failure"] = payload
            else:
                _STATUS["last_heartbeat_at"] = payload["time"]
            _write_status_locked()
    try:
        print("[regen-event] " + serialized, file=sys.stdout, flush=True)
    except OSError:
        pass


def short_node(node_id: str) -> str:
    """Return a readable node name while events retain the full stable ID."""
    return node_id.rsplit(":", 1)[-1]


atexit.register(close_event_monitor, "process-exit")
