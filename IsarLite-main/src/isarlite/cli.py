#!/usr/bin/env python3
"""
IsarLite CLI — query Isabelle/PIDE proof state through the PIDE protocol.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import Callable, Self

from returns.maybe import Maybe, Nothing, Some
from returns.result import Failure, Result, Success

from isarlite.base import (
    AssignUpdateBody,
    CommandID,
    CommandSpan,
    ExecID,
    InfoStr,
    IsarYXMLUtils,
    IsabelleEnv,
    KeywordTable,
    Markup,
    MarkupKind,
    ProverProcess,
    SessionResolver,
    StateStr,
    SymbolTable,
    Tokenizer,
    VersionID,
    YXMLElemModel,
    parse_spans,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="isar",
        description="IsarLite — query Isabelle proof state via PIDE protocol",
    )
    parser.add_argument(
        "--isabelle-home",
        help="Isabelle installation directory (default: auto-discover)",
    )
    parser.add_argument(
        "-d",
        "--session-dir",
        type=Path,
        default=None,
        help="Session directory (bounds the ROOT/ROOTS walk-up, default: auto-detect)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    state_parser = sub.add_parser("state-at", help="Query proof state at a position")
    state_parser.add_argument("file", type=Path, help="Path to theory file")
    state_parser.add_argument("line", type=int, help="Line number (1-based)")
    state_parser.add_argument("col", type=int, help="Column number (1-based)")

    return parser


def _resolve_isabelle_home(home_arg: str | None) -> Result[Path, str]:
    """
    Resolve ISABELLE_HOME from arg or env.
    """

    if home_arg:
        return Success(Path(home_arg))
    env_val = os.environ.get("ISABELLE_HOME")
    if env_val:
        return Success(Path(env_val))
    return Failure(
        "ISABELLE_HOME not found. Set --isabelle-home or ISABELLE_HOME env var."
    )


# ---------------------------------------------------------------------------
# Goal formatting
# ---------------------------------------------------------------------------


def _bracket_subgoal(goal: str) -> str:
    """Convert chained ``\\<Longrightarrow>`` in a subgoal to ``\\<lbrakk>...\\<rbrakk>``.

    ``A \\<Longrightarrow> B \\<Longrightarrow> C`` becomes ``\\<lbrakk>A; B\\<rbrakk> \\<Longrightarrow> C``.
    Only top-level implications (outside parentheses/brackets) are split.
    """
    parts: list[str] = []
    depth = 0
    start = 0
    impl = "\\<Longrightarrow>"
    i = 0
    while i < len(goal):
        ch = goal[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and goal[i : i + len(impl)] == impl:
            parts.append(goal[start:i])
            i += len(impl)
            start = i
            continue
        i += 1
    parts.append(goal[start:])

    if len(parts) <= 2:
        return goal
    premises = "; ".join(parts[:-1])
    conclusion = parts[-1]
    return f"\\<lbrakk>{premises}\\<rbrakk> \\<Longrightarrow> {conclusion}"


def _format_goal_brackets(text: str) -> str:
    """
    Apply bracket formatting to subgoal lines in a proof state text.
    """

    lines = text.split("\n")
    result: list[str] = []
    for line in lines:
        m = re.match(r"^(\s*\d+\.\s*)(.*)", line)
        if m:
            result.append(m.group(1) + _bracket_subgoal(m.group(2)))
        else:
            result.append(line)
    return "\n".join(result)


# ---------------------------------------------------------------------------
# High-level PIDE session client
# ---------------------------------------------------------------------------


class PIDESession:
    """A high-level client for a PIDE session.

    Wraps a ProverProcess and exposes a higher-level API for sending
    document commands and reading results.
    """

    def __init__(
        self, isabelle_home: Path | None = None, *, env: IsabelleEnv | None = None
    ) -> None:
        self.prover: ProverProcess | None = None
        self._tokenizer: Tokenizer | None = None
        # Next document version ID, incremented each update
        self._next_version: VersionID = 1
        # Parsed command spans from the loaded theory
        self._spans: list[CommandSpan] = []
        # Raw source text of the loaded theory
        self._source: str = ""
        # Assignments table: version → command_id → exec_ids
        # Populated from assign_update protocol messages (PIDE Phase 3).
        # Maps each version to the exec IDs assigned to each command,
        # which are the actual results we query for proof state.
        self._assignments: dict[VersionID, dict[CommandID, list[ExecID]]] = {}
        # Output messages keyed by exec ID, accumulated from all protocol
        # messages whose properties include an "id" field.
        # Each exec ID maps to the list of STATE, INFORMATION, etc. messages
        # produced by that execution.
        self._output: dict[ExecID, list[Markup]] = {}
        # The most recently assigned version ID — used as the default
        # version for state queries. Updated on every assign_update.
        self._last_assign_ver: VersionID = 0
        self.env: IsabelleEnv
        self.isabelle_home: Path
        if env is not None:
            self.env = env
            self.isabelle_home = env.isabelle_home
        else:
            if isabelle_home is None:
                isabelle_home_str = os.environ.get("ISABELLE_HOME", "")
                if not isabelle_home_str:
                    raise ValueError(
                        "ISABELLE_HOME not set. "
                        "Pass isabelle_home=... or set ISABELLE_HOME env var."
                    )
                isabelle_home = Path(isabelle_home_str)
            if not isabelle_home.is_dir():
                raise ValueError(
                    f"ISABELLE_HOME not set or invalid: {isabelle_home!r}. "
                    f"Pass isabelle_home=... or set ISABELLE_HOME env var."
                )
            self.isabelle_home = isabelle_home
            match IsabelleEnv.from_isabelle_home(isabelle_home):
                case Success(e):
                    self.env = e
                case Failure(msg):
                    raise ValueError(f"failed to create IsabelleEnv: {msg}")
                case _:
                    assert False, "unreachable"

    def connect(self, heap: Path | tuple[Path, ...] | None = None) -> None:
        """
        Launch the prover and connect.
        """

        self.prover = ProverProcess(self.env)
        self.prover.start(heap=heap)
        self._tokenizer = Tokenizer(KeywordTable.bootstrap(self.isabelle_home))

    def disconnect(self) -> Result[None, str]:
        """
        Shut down the prover. Returns success or logs the error.
        """

        if not self.prover:
            return Success(None)
        try:
            self.prover.send_command("Prover.stop", ["0"])
        except (OSError, RuntimeError) as e:
            self.prover.cleanup()
            self.prover = None
            return Failure(str(e))
        self.prover.cleanup()
        self.prover = None
        return Success(None)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.disconnect()

    @staticmethod
    def _is_statistics(msg: Markup) -> bool:
        if msg.name != MarkupKind.PROTOCOL:
            return False
        func = msg.attrs.get("function", "")
        return func in (MarkupKind.ML_statistics, MarkupKind.command_timing)

    def _drain_messages(
        self,
        deadline: float,
        *,
        quiet_after: float | None = None,
        skip_fn: Callable[[Markup], bool] | None = None,
    ) -> list[Markup]:
        """
        Read and accumulate messages until *deadline* or quiet period.
        """

        messages: list[Markup] = []
        last_activity = time.time()
        assert self.prover is not None
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            msg = self.prover.read_protocol_message(
                timeout=min(1.0, remaining)
            ).value_or(None)
            if msg is None:
                if (
                    quiet_after is not None
                    and time.time() - last_activity > quiet_after
                ):
                    break
                time.sleep(0.1)
                continue
            if skip_fn and skip_fn(msg):
                continue
            self._accumulate_message(msg)
            messages.append(msg)
            last_activity = time.time()
        return messages

    def read_until(self, timeout: float = 5.0) -> list[Markup]:
        """
        Read all available messages within *timeout* seconds.
        """

        if self.prover is None:
            return []
        return self._drain_messages(time.time() + timeout)

    def _accumulate_message(self, msg: Markup) -> None:
        """
        Record output messages by exec ID from Position.Id.
        """

        if msg.name == MarkupKind.PROTOCOL:
            func = msg.attrs.get("function", "")
            if func == "assign_update" and msg.body:
                self._handle_assign_update(msg)
            return
        eid_raw = msg.attrs.get("id")
        if eid_raw is not None:
            exec_id = int(eid_raw) if not isinstance(eid_raw, int) else eid_raw
            if exec_id not in self._output:
                self._output[exec_id] = []
            self._output[exec_id].append(msg)

    def _handle_assign_update(self, msg: Markup) -> None:
        """
        Parse assign_update body and record assignments.
        """

        body = msg.body
        if len(body) < 3:
            return
        try:
            a = AssignUpdateBody.model_validate(body)
        except Exception:
            return
        if a.assignments:
            self._assignments[a.version_id] = a.assignments
            self._last_assign_ver = a.version_id

    # ---- theory loading -------------------------------------------------------

    def load_theory(
        self, path: Path, *, visible_through: tuple[int, int] | None = None
    ) -> None:
        """Tokenize, send document commands, wait for execution.

        If *visible_through* is ``(line, col)``, only commands that end at or
        before that position are initially visible (executed).  Commands beyond
        are still defined but not run — use this to query intermediate state
        before subsequent commands close a proof.
        """
        with open(path, encoding="utf-8") as f:
            self._source = f.read()

        text = self._source
        if self._tokenizer is None:
            raise RuntimeError("call connect() before load_theory()")
        if self.prover is None:
            raise RuntimeError("call connect() before load_theory()")

        tokens = self._tokenizer.tokenize(text)
        self._spans = parse_spans(self._tokenizer.keywords, tokens)
        for i, span in enumerate(self._spans):
            span.id = i + 1

        for span in self._spans:
            if not span.name:
                continue
            self.prover.send_message(IsarYXMLUtils.build_define_command(span))

        node_name = str(path.resolve())
        theory_name = path.stem
        master_dir = str(path.resolve().parent)
        import re as _re
        _m = _re.search(
            r'theory\s+\S+\s+imports\s+(.*?)\s+(?:keywords\s+.*?)?begin',
            text, _re.DOTALL,
        )
        if _m:
            imports = [
                tok.strip('"') for tok in _m.group(1).split() if tok.strip('"')
            ]
        else:
            imports = ["Main"] if theory_name != "Main" else []
        deps = IsarYXMLUtils.build_deps_yxml(
            node_name, theory_name, master_dir, imports
        )
        edits = IsarYXMLUtils.build_edits_yxml(node_name, self._spans)

        if visible_through is not None:
            cutoff = (
                CommandSpan.line_offset(self._source, visible_through[0])
                + visible_through[1]
                - 1
            )
            visible = [s.id for s in self._spans if s.name and s.stop <= cutoff]
        else:
            visible = [s.id for s in self._spans if s.name]
        persp = IsarYXMLUtils.build_perspective_yxml(node_name, visible)
        # Each of deps/edits/persp is a YXML ``pair(string, variant(...))``.
        # The prover expects each edit as a separate chunk.
        edit_yxmls = [deps, edits, persp]

        update_chunks = IsarYXMLUtils.encode_document_update(
            "0",
            str(self._next_version),
            edit_yxmls,
            consolidate=[node_name],
        )
        self._next_version += 1
        self.prover.send_message(update_chunks)
        self._drain_messages(
            time.time() + 30.0, quiet_after=5.0, skip_fn=self._is_statistics
        )

    # ---- state querying -------------------------------------------------------

    def state_at(
        self, line: int, column: int
    ) -> Result[tuple[Maybe[StateStr], Maybe[InfoStr]], str]:
        """
        Get proof state at (*line*, *column*) in the loaded theory.
        """

        if not self._spans:
            return Failure("no spans loaded")
        offset = CommandSpan.line_offset(self._source, line) + column - 1
        if offset >= len(self._source):
            return Failure("position overflow")

        span_result = CommandSpan.at_offset(self._source, self._spans, offset)
        if isinstance(span_result, Some):
            s = span_result.unwrap()
            if s.start <= offset < s.stop:
                return Failure("position not inside command boundary")
        else:
            return Failure("no command span at position")

        version_assign: dict[CommandID, list[ExecID]] = self._assignments.get(
            self._last_assign_ver, {}
        )
        exec_ids: list[ExecID] = version_assign.get(s.id, [])
        if not exec_ids:
            return Failure("no proof state at position")

        state_raw: list[StateStr] = []
        info_raw: list[InfoStr] = []
        for eid in exec_ids:
            for m in self._output.get(eid, []):
                t = YXMLElemModel.content_of(m.body)
                if not t:
                    continue
                decoded = SymbolTable.default().to_symbols(t)
                if m.name == MarkupKind.STATE:
                    state_raw.append(decoded)
                elif m.name == MarkupKind.INFORMATION:
                    info_raw.append(decoded)

        state_msg: Maybe[StateStr] = (
            Some(_format_goal_brackets("\n".join(dict.fromkeys(state_raw))))
            if state_raw
            else Nothing
        )
        info_msg: Maybe[InfoStr] = (
            Some("\n".join(dict.fromkeys(info_raw))) if info_raw else Nothing
        )
        return Success((state_msg, info_msg))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "state-at":
        _cmd_state_at(args)


def _cmd_state_at(args: argparse.Namespace) -> None:
    """
    Implement the ``state-at`` subcommand.
    """

    home_result = _resolve_isabelle_home(args.isabelle_home)
    if isinstance(home_result, Failure):
        raise SystemExit(f"error: {home_result.failure()}")
    assert isinstance(home_result, Success)
    isabelle_home: Path = home_result.unwrap()

    if not args.file.is_file():
        raise SystemExit(f"error: file not found: {args.file}")

    if args.line < 1 or args.col < 1:
        raise SystemExit("error: line and col must be positive integers")

    try:
        info = SessionResolver.resolve(Path(args.file), args.session_dir)
        info = SessionResolver.resolve_dependencies(info, isabelle_home)
        env = IsabelleEnv.from_isabelle_home(isabelle_home).unwrap()
        heaps = SessionResolver.verify_heaps(info.dependencies, env)
        with PIDESession(env=env) as session:
            session.connect(heap=tuple(heaps) if len(heaps) > 1 else heaps[0])
            session.load_theory(args.file)
            match session.state_at(args.line, args.col):
                case Success((state, info)):
                    parts: list[str] = []
                    if isinstance(state, Some):
                        parts.append(state.unwrap())
                    if isinstance(info, Some):
                        parts += ["\n---\n", info.unwrap()]
                    if parts:
                        print("\n".join(parts))
                case Failure(msg):
                    print(f"error: {msg}", file=sys.stderr)
                    sys.exit(1)
                case _:
                    sys.exit(2)
    except Exception as exc:
        raise SystemExit(f"error: {exc}") from exc


if __name__ == "__main__":
    main()
