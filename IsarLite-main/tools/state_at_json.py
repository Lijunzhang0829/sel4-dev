#!/usr/bin/env python3
"""Connect to the prover, load a theory, dump raw YXML payloads.

Shows decoded YXML element tree and text content for every protocol message.
Useful for understanding the wire format of STATE, INFORMATION, and other
markup kinds.

Usage::

    uv run python state_at_json.py PATH/TO/Theory.thy [--line L] [--col C]
    uv run python state_at_json.py PATH/TO/Theory.thy --kind state

Dumps everything by default.  Use ``--kind state`` or ``--kind information``
to filter by specific message kind.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from pydantic import TypeAdapter
from returns.result import Failure, Result, Success

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from isarlite.base import (
    ExecID,
    Markup,
    YXMLElemModel,
)
from isarlite.cli import PIDESession


def _find_isabelle_home() -> Path:
    env = os.environ.get("ISABELLE_HOME", "")
    if env:
        return Path(env)
    ref = Path(__file__).parent.parent / "refs/Isabelle2025"
    if ref.is_dir():
        return ref
    raise RuntimeError("set ISABELLE_HOME or place Isabelle at refs/Isabelle2025")


def _heap_path(name: str) -> Result[Path, None]:
    """
    Resolve the path to a built session heap.
    """

    isabelle_home = _find_isabelle_home()
    isabelle_bin = isabelle_home / "bin/isabelle"
    try:
        result = subprocess.run(
            [
                str(isabelle_bin),
                "getenv",
                "-b",
                "ML_IDENTIFIER",
                "ISABELLE_HEAPS_SYSTEM",
                "ISABELLE_HEAPS",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "ISABELLE_HOME": str(isabelle_home)},
        )
        if result.returncode != 0:
            return Failure(None)
        ml_id, heaps_sys, heaps_user = result.stdout.strip().split("\n")
        for base in [heaps_sys, heaps_user]:
            p = Path(base) / ml_id / name
            if p.is_file():
                return Success(p)
        return Failure(None)
    except (OSError, subprocess.TimeoutExpired):
        return Failure(None)


def _pp_yxml_tree(items: list[YXMLElemModel | str], indent: int = 0) -> list[str]:
    pad = "  " * indent
    lines: list[str] = []
    for item in items:
        if isinstance(item, str):
            lines.append(f"{pad}TEXT: {item!r}")
        else:
            attrs_str = ", ".join(f"{k}={v!r}" for k, v in item.attrs)
            lines.append(f"{pad}<{item.name} [{attrs_str}]>")
            if item.body:
                lines.extend(_pp_yxml_tree(item.body, indent + 1))
            lines.append(f"{pad}</{item.name}>")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dump raw YXML payloads from Isabelle prover"
    )
    parser.add_argument("theory", type=Path, help="Path to .thy file")
    parser.add_argument("--line", type=int, default=0)
    parser.add_argument("--col", type=int, default=0)
    parser.add_argument(
        "--kind", default=None, help="Filter by message kind (state, information, etc.)"
    )
    args = parser.parse_args()

    isabelle_home = _find_isabelle_home()
    pure: Path | None = _heap_path("Pure").value_or(None)
    hol: Path | None = _heap_path("HOL").value_or(None)
    print(f"Isabelle home: {isabelle_home}")
    print(f"Pure heap: {pure}")
    print(f"HOL heap: {hol}")
    print()

    if not pure or not hol:
        sys.exit(
            "Pure and/or HOL heaps not built. Run: isabelle build -b Pure && isabelle build -b HOL"
        )
    assert pure is not None and hol is not None

    with PIDESession(isabelle_home=isabelle_home) as session:
        session.connect(heap=(pure, hol))
        session.load_theory(
            args.theory, visible_through=(args.line, args.col) if args.line else None
        )
        print(
            TypeAdapter(dict[ExecID, list[Markup]])
            .dump_json(session._output, indent=2)  # type: ignore[reportPrivateUsage]
            .decode("utf-8")
        )


if __name__ == "__main__":
    main()
