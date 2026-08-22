#!/usr/bin/env python3
"""Small, line-buffered progress protocol for long modular-proof runs."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any


def emit_event(event: str, **fields: Any) -> None:
    payload = {
        "time": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **{key: value for key, value in fields.items() if value is not None},
    }
    print(
        "[regen-event] " + json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), default=str,
        ),
        file=sys.stdout,
        flush=True,
    )


def short_node(node_id: str) -> str:
    """Return a readable node name while events retain the full stable ID."""
    return node_id.rsplit(":", 1)[-1]
