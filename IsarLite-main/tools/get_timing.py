#!/usr/bin/env python3
"""Print all command spans and their timing for a theory file.

Connects to the prover, loads a theory, captures ``command_timing`` protocol
messages (elapsed, cpu, gc), maps them to command spans via the assignment
table (exec ID → span ID), and prints a table of all named command spans
with their timing data.

Usage::

    python lab/get_timing.py PATH/TO/Theory.thy [line] [col]

If line and col are given, the matching span is highlighted.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from isarlite.base import (
    CommandID,
    CommandSpan,
    ExecID,
    IsabelleEnv,
    Markup,
    MarkupKind,
    PIDEInteractiveOptions,
    SessionResolver,
    VersionID,
)
from isarlite.cli import PIDESession


# ── Custom timing-capturing session ----------------------------------------


class TimingSession(PIDESession):
    """PIDESession variant that captures ``command_timing`` protocol messages.

    Overrides two methods from ``PIDESession``:

    * ``_is_statistics`` — only skips ``ML_statistics``, keeps ``command_timing``
    * ``_accumulate_message`` — diverts ``command_timing`` messages into
      ``self.command_timings`` instead of discarding them.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.command_timings: list[Markup] = []

    @staticmethod
    def _is_statistics(msg: Markup) -> bool:
        if msg.name != MarkupKind.PROTOCOL:
            return False
        func = msg.attrs.get("function", "")
        return func == MarkupKind.ML_statistics  # keep command_timing

    def _accumulate_message(self, msg: Markup) -> None:
        if msg.name == MarkupKind.PROTOCOL:
            func = msg.attrs.get("function", "")
            if func == "command_timing":
                self.command_timings.append(msg)
                return
            if func == "assign_update" and msg.body:
                self._handle_assign_update(msg)
            return
        eid_raw = msg.attrs.get("id")
        if eid_raw is not None:
            exec_id = int(eid_raw) if not isinstance(eid_raw, int) else eid_raw
            if exec_id not in self._output:
                self._output[exec_id] = []
            self._output[exec_id].append(msg)


# ── Helpers -----------------------------------------------------------------


def _resolve_isabelle_home(home_arg: str | None) -> Path:
    if home_arg:
        return Path(home_arg)
    env_val = os.environ.get("ISABELLE_HOME")
    if env_val:
        return Path(env_val)
    ref = Path(__file__).resolve().parent.parent / "refs" / "Isabelle2025"
    if ref.is_dir():
        return ref
    raise RuntimeError(
        "ISABELLE_HOME not found. Pass --isabelle-home or set ISABELLE_HOME env var."
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="get-timing",
        description="Print command timing for a theory file",
    )
    p.add_argument("file", type=Path, help="Path to theory file")
    p.add_argument("line", type=int, nargs="?", default=None, help="Line (1-based)")
    p.add_argument("col", type=int, nargs="?", default=None, help="Column (1-based)")
    p.add_argument(
        "--isabelle-home",
        default=None,
        help="Isabelle installation directory (default: auto-discover)",
    )
    p.add_argument(
        "-d",
        "--session-dir",
        type=Path,
        default=None,
        help="Session directory (default: auto-detect)",
    )
    p.add_argument(
        "--threshold",
        type=float,
        default=0.0,
        help="command_timing_threshold (default: 0.0 = all commands)",
    )
    return p


def _offset_to_line(source: str, offset: int) -> int:
    """Return the 1-based line number for a character offset in *source*."""
    return source[: min(offset, len(source))].count("\n") + 1


def _build_timing_table(
    spans: list[CommandSpan],
    assignments: dict[VersionID, dict[CommandID, list[ExecID]]],
    timings: list[Markup],
    source: str,
) -> list[dict[str, object]]:
    """Build a list of span-timing rows.

    Uses the assignment table to map command_timing exec IDs to span IDs,
    then aggregates elapsed/cpu/gc per span.

    Returns ``[{id, line, elapsed, cpu, gc, source_preview}]`` for every
    named span.  Spans with no timing get ``elapsed=cpu=gc=None``.
    """
    # Build exec_id → span_id reverse map from ALL assignment versions
    # (assign_update may be split across multiple messages)
    exec_to_span: dict[ExecID, CommandID] = {}
    for ver_assign in assignments.values():
        for span_id, exec_ids in ver_assign.items():
            for eid in exec_ids:
                exec_to_span[eid] = span_id

    # Accumulate timing by span_id (a span may have multiple timing records)
    timing_by_span: dict[CommandID, list[dict[str, float]]] = {}
    for m in timings:
        raw_id = m.attrs.get("id")
        if raw_id is None:
            continue
        eid = int(raw_id) if not isinstance(raw_id, int) else raw_id
        sid = exec_to_span.get(eid)
        if sid is None:
            continue
        timing_by_span.setdefault(sid, []).append(
            {
                "elapsed": float(m.attrs.get("elapsed", 0)),  # type: ignore[arg-type]
                "cpu": float(m.attrs.get("cpu", 0)),  # type: ignore[arg-type]
                "gc": float(m.attrs.get("gc", 0)),  # type: ignore[arg-type]
            }
        )

    rows: list[dict[str, object]] = []
    for s in spans:
        if not s.name:
            continue
        t_list = timing_by_span.get(s.id, [])
        if t_list:
            elapsed = sum(t["elapsed"] for t in t_list)
            cpu = sum(t["cpu"] for t in t_list)
            gc = sum(t["gc"] for t in t_list)
        else:
            elapsed = cpu = gc = None
        preview = s.source[:60].replace("\n", "\\n")
        rows.append(
            {
                "id": s.id,
                "line": _offset_to_line(source, s.start),
                "source_preview": preview,
                "elapsed": elapsed,
                "cpu": cpu,
                "gc": gc,
            }
        )
    return rows


# ── Main --------------------------------------------------------------------


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if not args.file.is_file():
        raise SystemExit(f"error: file not found: {args.file}")

    isabelle_home = _resolve_isabelle_home(args.isabelle_home)
    print(f"Isabelle home: {isabelle_home}", file=sys.stderr)

    try:
        info = SessionResolver.resolve(args.file, args.session_dir)
        info = SessionResolver.resolve_dependencies(info, isabelle_home)
        env = IsabelleEnv.from_isabelle_home(isabelle_home).unwrap()
        heaps = SessionResolver.verify_heaps(info.dependencies, env)
        print(
            f"Session: {info.name}, heaps: {len(heaps)}",
            file=sys.stderr,
        )
    except (RuntimeError, Exception) as exc:
        raise SystemExit(f"error: session resolution failed: {exc}") from exc

    with TimingSession(env=env) as session:
        session.connect(heap=tuple(heaps) if len(heaps) > 1 else heaps[0])

        # Lower the timing threshold so every command reports timing
        if args.threshold >= 0:
            p = session.prover
            assert p is not None
            low_thresh_yxml = PIDEInteractiveOptions.patch_yxml(
                p._patched_options_yxml,  # type: ignore[reportPrivateUsage]
                {"command_timing_threshold": str(args.threshold)},
            )
            p.send_command("Prover.options", [low_thresh_yxml])

        session.load_theory(args.file)

        rows = _build_timing_table(
            session._spans, session._assignments, session.command_timings, session._source
        )

        if not rows:
            print("No named command spans found.", file=sys.stderr)
            sys.exit(1)

        # Header
        hdr = f"{'ID':>4} {'Line':>4} {'Elapsed (s)':>10} {'CPU (s)':>8} {'GC (s)':>6}  Source"
        print(hdr)
        print("-" * len(hdr))

        # Determine which span to highlight (if line/col given)
        target_span_id: int | None = None
        if args.line is not None and args.col is not None:
            offset = (
                CommandSpan.line_offset(session._source, args.line) + args.col - 1
            )
            span_at = CommandSpan.at_offset(
                session._source, session._spans, offset
            ).value_or(None)
            if span_at:
                target_span_id = span_at.id

        for r in rows:
            sid: int = r["id"]  # type: ignore[assignment]
            line = r["line"]
            elapsed = r["elapsed"]
            elapsed_s = f"{elapsed:>10.3f}" if elapsed is not None else f"{'--':>10}"
            cpu_s = f"{r['cpu']:>8.3f}" if r["cpu"] is not None else f"{'--':>8}"
            gc_s = f"{r['gc']:>6.3f}" if r["gc"] is not None else f"{'--':>6}"
            marker = "  >" if sid == target_span_id else "   "
            print(
                f"{sid:>4} {line:>4} {elapsed_s} {cpu_s} {gc_s}{marker} {r['source_preview']}"
            )

        timed = sum(1 for r in rows if r["elapsed"] is not None)
        untimed = len(rows) - timed
        total_elapsed = sum(
            float(r["elapsed"]) for r in rows if r["elapsed"] is not None  # type: ignore[arg-type]
        )
        print(file=sys.stderr)
        print(
            f"Spans: {len(rows)} total, {timed} with timing, {untimed} without",
            file=sys.stderr,
        )
        print(f"Total elapsed: {total_elapsed:.3f}s", file=sys.stderr)


if __name__ == "__main__":
    main()