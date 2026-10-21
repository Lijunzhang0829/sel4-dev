#!/usr/bin/env python3
"""Dump every PIDE message kind for a given position in a theory file.

Usage:
    python lab/dump_markup_kinds.py <file.thy> <line> <col>
    python lab/dump_markup_kinds.py refs/Isabelle2025/src/HOL/Examples/Ackermann.thy 76 1

This connects, loads the theory, finds the span at (line, col), then prints
every message kind (STATE, WRITELN, RESULT, INFORMATION, etc.) with content.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from returns.maybe import Some
from returns.result import Failure

from isarlite.base import (
    AssignUpdateBody,
    CommandSpan,
    ExecID,
    IsarYXMLUtils,
    IsabelleEnv,
    KeywordTable,
    Markup,
    MarkupKind,
    ProverProcess,
    SessionResolver,
    Tokenizer,
    YXMLElemModel,
    SessionVerifier,
    parse_spans,
    write_message,
)


def _parse_assign_update(
    msg: Markup,
) -> tuple[int, dict[int, list[ExecID]]] | None:
    try:
        body_ab = AssignUpdateBody.model_validate(msg.body)
    except Exception:
        body_ab = None
    assign = body_ab.assignments if body_ab else {}
    if assign and len(msg.body) >= 1 and isinstance(msg.body[0], YXMLElemModel):
        first = msg.body[0]
        if (
            first.name == ":"
            and len(first.body) == 1
            and isinstance(first.body[0], str)
        ):
            try:
                return (int(first.body[0]), assign)
            except (ValueError, TypeError):
                pass
    return None


def main() -> None:
    args = sys.argv[1:]
    if len(args) < 3:
        print(f"Usage: {sys.argv[0]} <file.thy> <line> <col>", file=sys.stderr)
        sys.exit(1)

    file_path = Path(args[0])
    line = int(args[1])
    col = int(args[2])

    if not file_path.is_file():
        print(f"error: file not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    match SessionVerifier.discover_home():
        case Some(home):
            isabelle_home = home
        case _:
            refs = Path(__file__).resolve().parent.parent / "refs" / "Isabelle2025"
            if refs.is_dir():
                isabelle_home = refs
            else:
                print("error: ISABELLE_HOME not found", file=sys.stderr)
                sys.exit(1)

    env_result = IsabelleEnv.from_isabelle_home(isabelle_home)
    if isinstance(env_result, Failure):
        print(f"error: {env_result.failure()}", file=sys.stderr)
        sys.exit(1)
    env = env_result.unwrap()

    info = SessionResolver.resolve(Path(file_path), None)
    deps_info = SessionResolver.resolve_dependencies(info, isabelle_home)
    heaps = SessionResolver.verify_heaps(deps_info.dependencies, env)

    source = file_path.read_text(encoding="utf-8")

    prover = ProverProcess(env)
    prover.start(heap=tuple(heaps) if len(heaps) > 1 else heaps[0])

    tokenizer = Tokenizer(KeywordTable.bootstrap(isabelle_home))
    tokens = tokenizer.tokenize(source)
    spans = parse_spans(tokenizer.keywords, tokens)
    for i, span in enumerate(spans):
        span.id = i + 1

    conn = prover.socket
    assert conn is not None, "prover not connected"
    IsarYXMLUtils.send_define_commands(conn, spans)

    node_name = str(file_path.resolve())
    theory_name = file_path.stem
    master_dir = str(file_path.resolve().parent)
    imports = ["Main"] if theory_name != "Main" else []

    deps_yxml = IsarYXMLUtils.build_deps_yxml(
        node_name, theory_name, master_dir, imports
    )
    edits_yxml = IsarYXMLUtils.build_edits_yxml(node_name, spans)
    persp_yxml = IsarYXMLUtils.build_perspective_yxml(
        node_name, [s.id for s in spans if s.name]
    )

    update_chunks = IsarYXMLUtils.encode_document_update(
        "0", "1", [deps_yxml, edits_yxml, persp_yxml], consolidate=[node_name]
    )
    write_message(conn, update_chunks)

    # Drain messages
    assignments: dict[int, dict[int, list[ExecID]]] = {}
    output: dict[ExecID, list[Markup]] = {}
    deadline = time.time() + 30.0
    quiet_period = 5.0
    last_activity = time.time()

    while time.time() < deadline:
        remaining = deadline - time.time()
        msg = prover.read_protocol_message(timeout=min(1.0, remaining)).value_or(None)
        if msg is None:
            if time.time() - last_activity > quiet_period:
                break
            time.sleep(0.1)
            continue
        if msg.name == MarkupKind.PROTOCOL:
            func = msg.attrs.get("function", "")
            assert isinstance(func, str)
            if func == "assign_update":
                result = _parse_assign_update(msg)
                if result is not None:
                    ver, assign = result
                    assignments[ver] = assign
            last_activity = time.time()
            continue
        eid_raw = msg.attrs.get("id")
        if eid_raw is not None:
            exec_id = int(eid_raw) if not isinstance(eid_raw, int) else eid_raw
            if exec_id not in output:
                output[exec_id] = []
            output[exec_id].append(msg)
        last_activity = time.time()

    # Find span at position
    offset = CommandSpan.line_offset(source, line) + col - 1
    span = CommandSpan.at_offset(source, spans, offset).value_or(None)
    if span is None:
        print(f"No named span at position ({line}, {col})")
        print(f"  offset={offset}, source_len={len(source)}")
        prover.cleanup()
        sys.exit(1)

    print(f"=== Span at ({line}, {col}) ===")
    print(
        f"  Span: id={span.id}, name={span.name!r}, start={span.start}, stop={span.stop}"
    )
    print(f"  Source text: {source[span.start : span.stop]!r}")
    print()

    last_ver = max(assignments.keys()) if assignments else None
    if last_ver is None:
        print("No assignments received — no exec IDs.")
        prover.cleanup()
        sys.exit(1)

    assign = assignments[last_ver]
    exec_ids = assign.get(span.id, [])
    print(f"=== Assignments (version {last_ver}) ===")
    print(f"  Command {span.id} exec IDs: {exec_ids}")
    print()

    if not exec_ids:
        print("No exec IDs for this span — command may not have executed.")
        prover.cleanup()
        sys.exit(1)

    # Collect messages by kind for this span's exec IDs
    by_kind: dict[str, list[str]] = {}
    seen: set[int] = set()
    for eid in exec_ids:
        for m in output.get(eid, []):
            msg_id = id(m)
            if msg_id in seen:
                continue
            seen.add(msg_id)
            kind = m.name
            text = YXMLElemModel.content_of(m.body).strip()
            if kind not in by_kind:
                by_kind[kind] = []
            by_kind[kind].append(text)

    print(f"=== All message kinds for span {span.id} ===")
    print(f"  ({len(by_kind)} distinct kinds)")
    print()
    for kind in sorted(by_kind.keys()):
        texts = by_kind.get(kind, [])
        if not texts:
            continue
        unique = list(dict.fromkeys(texts))
        content = "\n".join(unique)
        print(f"[{kind}] ({len(unique)} message(s))".ljust(40))
        for line_text in content.split("\n"):
            print(f"  | {line_text}")
        print()

    prover.cleanup()


if __name__ == "__main__":
    main()
