"""
Fixtures for integration tests (require Isabelle prover with built heaps).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from returns.result import Failure, Result, Success

from test.conftest import ISABELLE_HOME, PROJECT_ROOT, THYS

ISABELLE_BIN: Path = ISABELLE_HOME / "bin/isabelle"
ISAR_SCRIPT: Path = PROJECT_ROOT / "isar"

requires_isabelle: pytest.MarkDecorator = pytest.mark.skipif(
    not ISABELLE_BIN.is_file(), reason=f"isabelle not found at {ISABELLE_BIN}"
)

ASSET_L4V_PATH: Path = PROJECT_ROOT / "test/assets/l4v"


class MORE_THYS(THYS):
    Rights_AI: Path = ASSET_L4V_PATH / "proof/invariant-abstract/Rights_AI.thy"


def heap_path(name: str) -> Result[Path, None]:
    """Resolve the path to a built session heap."""
    try:
        result = subprocess.run(
            [
                str(ISABELLE_BIN),
                "getenv",
                "-b",
                "ML_IDENTIFIER",
                "ISABELLE_HEAPS_SYSTEM",
                "ISABELLE_HEAPS",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "ISABELLE_HOME": str(ISABELLE_HOME)},
        )
        if result.returncode != 0:
            return Failure(None)
        ml_id, heaps_sys, heaps_user = result.stdout.strip().split("\n")
        for base in [heaps_sys, heaps_user]:
            p = Path(base) / ml_id / name
            if p.is_file():
                return Success(p)
        return Failure(None)
    except Exception:
        return Failure(None)


PURE_HEAP_PATH: Path | None = heap_path("Pure").value_or(None)
HOL_HEAP_PATH: Path | None = heap_path("HOL").value_or(None)

requires_pure: pytest.MarkDecorator = pytest.mark.skipif(
    not PURE_HEAP_PATH, reason="Pure heap not built"
)
requires_hol: pytest.MarkDecorator = pytest.mark.skipif(
    not HOL_HEAP_PATH, reason="HOL heap not built"
)
requires_l4v: pytest.MarkDecorator = pytest.mark.skipif(
    not MORE_THYS.Rights_AI or not MORE_THYS.Rights_AI.is_file(),
    reason="test/assets/l4v not found or Rights_AI.thy missing",
)


def run_cmd(*args: str, timeout: int = 10) -> subprocess.CompletedProcess:
    """Run the isar CLI with *args, capturing stdout/stderr."""
    return subprocess.run(
        [str(ISAR_SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
