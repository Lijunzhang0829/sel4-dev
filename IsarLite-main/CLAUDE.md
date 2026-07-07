# IsarLite — Python PIDE Client

An ultra portable Python CLI for interacting with the Isabelle theorem prover. It directly communicates with the Poly/ML process through the PIDE protocol over TCP sockets.

## Quick Start

```bash
uv sync                           # Install deps into .venv
pytest test/                      # Run all tests (see "Testing" section)
```

**Requirements:** Python >=3.12, managed via `uv`. The `.venv/` is pre-configured — no manual activation needed.

**Isabelle installation:** Point `ISABELLE_HOME` to your Isabelle tree, or the scripts default to `refs/Isabelle2025/`:

```bash
export ISABELLE_HOME=/path/to/Isabelle2025
```

## Key Files

| File | Purpose |
|------|---------|
| `src/isarlite/base.py` | Core library — see [Outline](#isarlitebasepy-outline) below |
| `src/isarlite/cli.py` | PIDE session client (`PIDESession`), CLI entry point (`isar state-at`), goal formatting |
| `pyproject.toml` | Project metadata — dependencies (pydantic, returns), build config (hatchling) |
| `lab/` | Debug & diagnostic scripts (see "Lab scripts" below) |
| `test/` | Pytest-based tests (unit + integration) |
| `refs/Isabelle2025/` | Checked-in Isabelle source tree (read-only reference) |
| `notes/pide.md` | PIDE protocol architecture — document model, edit-execute-assign cycle, wire format, options mechanism |
| `notes/state_at.md` | Proof state querying flow — automatic vs overlay path, data structures |
| `notes/isabelle_process.md` | Prover lifecycle and transport layer — `isabelle process` CLI, `Message_Channel`, `Protocol_Command` dispatch |
| `notes/scala_client_resilience.md` | Scala client architecture — threading, error handling, phase state machine, message dispatch |
| `notes/tokenizer.md` | Scala tokenizer/parser porting notes — keyword classification, `\|\|\|` longest-match, parse_spans flow |
| `notes/scala_build.md` | `isabelle scala_build` reference (external) |

## Architecture

To achieve ultra portability, IsarLite ships all code in only 2 scripts:
- `src/isarlite/base.py` — the "core library" containing all dirty details about the PIDE protocol and Isabelle internals. This is expected to be stable (e.g. under agentic evolutions) once it is published. It does not split into multiple files to avoid import complexity.
- `src/isarlite/cli.py` — the "application" containing the `isar` CLI. This is expected to be more volatile as we add features, especially in agentic iterations.

### The Core Library (`base.py`)

- `YXMLParseError` — Malformed YXML input
- `YXMLElemModel` — YXML element: name, attrs, body. Parsing/serialization. Includes `content_of()` for recursive text extraction.
- `symbol_length()` — Count Isabelle symbols (named `\\<foo>` = 1 symbol)
- `SymbolTable` — Loads `etc/symbols` for character classification (letter/digit/blank/symbolic)
- `Lexicon` — Char-level trie for longest-match keyword scanning
- `KeywordTable` — Major/minor keyword lexicons. `bootstrap()` merges `_BOOTSTRAP_KEYWORDS`, Pure.thy keywords, and `_HOL_EXTRA_KEYWORDS`
- `Tokenizer` / `Token` — Replicates `Token.explode` from `token.scala`
- `CommandSpan` / `parse_spans` — Span parser replicating `Outer_Syntax.parse_spans`
- `PIDEInteractiveOptions` — Typed Pydantic model for ~100 of 333+ Isabelle options. `patch_yxml()` updates raw YXML in-place (preserves unknown options).
- `IsarYXMLUtils` — Builds PIDE protocol messages: `encode_document_update`, `build_deps_yxml`, `build_edits_yxml`, `build_perspective_yxml`, `build_define_command`
- `AssignUpdateBody` — Parsed assign/update message — maps `version_id` to `{command_id: [exec_ids]}`
- `MarkupKind` — Known PIDE message kind constants (STATUS, STATE, INFORMATION, PROTOCOL, etc.)
- `Markup` — Parsed prover message — name (kind), attrs (properties), body
- `PIDEPayload` — Accumulated output grouped by exec ID. `by_kind()` filters by message kind.
- `IsabelleEnv` — Isabelle environment (paths discovered via `isabelle getenv`)
- `ProverProcess` — Launches Poly/ML, sets up TCP socket + password handshake, sends/receives protocol messages
- `read_message` / `write_message` — Byte-message framing: length-prefixed multi-chunk wire format
- `SessionVerifier` — SHA1 source integrity checks against session `.db` files
- `SessionResolver` / `SessionInfo` — ROOT/ROOTS parsing, session dependency resolution, heap verification

### PIDE protocol message format

See [notes/pide.md](notes/pide.md#wire-format-details) for the full specification.

**Output direction (ML → Python):**
```
[chunk0=kind, chunk1=props_count, chunk2..N=props, N+1..=body]
```

**Input direction (Python → ML):**
```
chunk[0] = command_name (raw bytes)
chunk[1..N] = args (raw bytes)
```

### Markup kinds for proof state

| Kind                 | Source                       | Content                                                  |
| -------------------- | ---------------------------- | -------------------------------------------------------- |
| `Markup.STATE`       | Automatic after command eval | Open proof goals (via `show_states` / `print_state`)     |
| `Markup.INFORMATION` | Automatic after command eval | Proof outline with cases, Sledgehammer suggestions, etc. |

`PIDESession.state_at()` returns `Result[tuple[Maybe[StateStr], Maybe[InfoStr]], str]`:
- `StateStr` = content from `Markup.STATE` (proof goals)
- `InfoStr` = content from `Markup.INFORMATION` (proof outline, etc.)

No fallback chain — each kind is collected independently. See [notes/state_at.md](notes/state_at.md) for details.

## Testing

### Fast unit tests (no prover needed)

```bash
pytest test/test_tokenizer.py test/test_yxml.py \
  test/test_protocol.py -v
```

### Integration tests (require prover with built heaps)

```bash
isabelle build -b Pure   # build Pure heap (~30s)
isabelle build -b HOL    # build HOL heap (~5min)
pytest test/integration/ -v
```

Integration tests use `pytest.mark.skipif` to skip when heaps aren't built.

## Test Maintenance
- Testing with real prover:
  - `refs/Isabelle2025/src/HOL/Examples/`: example theories for testing.
  - `refs/l4v/proof/abstract-invariants/`: sel4 theories with relatively small dependencies for testing.
- Put constants (like paths to used theories), fixtures, markers in `test/conftest.py` or `test/integration/conftest.py`.


### Lab scripts

- `lab/dump_markup_kinds.py` — Connect, load a theory, dump ALL PIDE message kinds for a given position with content. Use to discover what markup kinds a command produces.
- `lab/state_at_json.py` — Connect, load a theory, dump raw YXML payloads as JSON. Supports `--kind state` / `--kind information` filtering.

## Message reading patterns

See [notes/scala_client_resilience.md](notes/scala_client_resilience.md) for the full discussion. Key points:

- **Status markers are the source of truth.** `RUNNING`/`FINISHED`/`FAILED`/`CONSOLIDATED` markers track command progress — not timeout heuristics.
- Three scenarios: (1) command terminates normally, (2) intermediate output (e.g. Sledgehammer), (3) infinite loop (no watchdog — user must Ctrl+C).
- The Python client uses `_drain_messages()` with a deadline + quiet-period heuristic for convenience, but the `ProverProcess` layer provides raw message reading.

## Protocol pitfalls

See [notes/pide.md](notes/pide.md#options-mechanism-pitfalls) for detailed discussion.
- **`PIDEInteractiveOptions.from_yxml()` drops unknown options** — use `patch_yxml()` to modify raw YXML in-place.
- **`Document.update` error reporting** swallows root cause (generic "protocol command failure"). Check prover stderr.

### YXML encoding rules (verified against ML decoders)

- `encode_pair(f, g)((a, b))` → `[_node(f(a)), _node(g(b))]`
- ML `pair` decoder strips `<:>` wrappers via `node()` before passing to `f`/`g`
- `encode_variant` produces `Elem(str(tag), attrs, body)` where attrs hold atoms
- ML `variant` uses `tagged()` to extract `(tag, (atoms, body))`
- `encode_list` wraps each element in `<:>`; ML `list` strips via `node()`
- `encode_properties` wraps in `<:` with attributes as key-value pairs

### Protocol command types

- `Protocol_Command.define` — chunks converted to `string list` before handler
- `Protocol_Command.define_bytes` — raw `Bytes.T list` passed to handler
- `Document.update` is the **only** command using `Future.task_context`

### Tokenizer/parser rules

See [notes/tokenizer.md](notes/tokenizer.md) for full porting notes. Key rules:

- **Port from Scala, never guess**: `refs/Isabelle2025/src/Pure/Isar/token.scala` is authoritative.
- **Keywords are dynamic**: `_parse_pure_keywords()` parses `Pure.thy` at runtime. `_HOL_EXTRA_KEYWORDS` is a stopgap.
- **`QUASI_COMMAND` vs `BEFORE_COMMAND`**: `QUASI_COMMAND` (imports, keywords, etc.) does NOT trigger span flush; `BEFORE_COMMAND` always does.
- **`keyword ||| ident` longest-match**: Scan both keyword and identifier; pick the longer.
- **`XML.content_of` must be recursive**: Prover messages nest Text inside Elem trees.
- **`_span_at_offset` must handle whitespace gaps**: Return nearest preceding named span.
- **`load_theory(visible_through=...)`**: Limit initial visibility to query intermediate proof state.

## Agent skills

### Issue tracker

Issues are tracked on GitHub at `Whatever314/IsarLite`. See `docs/agents/issue-tracker.md`.

### Triage labels

Labels match the default five-role vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout — `CONTEXT.md` at root, ADRs in `docs/adr/`. See `docs/agents/domain.md`.

## References

- `refs/Isabelle2025/`: Isabelle source tree (Pure framework, PIDE protocol implementation)
  - `refs/Isabelle2025/src/Pure`: Everything about the poly/ml prover engine.
  - `refs/Isabelle2025/src/Pure/PIDE`: PIDE protocol implementation in Isabelle ML.
  - `refs/Isabelle2025/src/Pure/Isar`: Tokenizer (`token.scala`), keyword table (`keyword.scala`), outer syntax parser (`outer_syntax.scala`), parser combinators (`parse.scala`)
  - `refs/Isabelle2025/src/Pure/General`: Symbol matcher (`symbol.scala`), char-trie lexicon (`scan.scala`), comment parser (`comment.scala`)
  - `refs/Isabelle2025/src/Doc`: Official Isabelle documentation
    - `refs/Isabelle2025/src/Doc/Implementation`
    - `refs/Isabelle2025/src/Doc/System`: Complete Isabelle system including the cli interfaces, third-party plugins on top of the ml engine.
  - `refs/Isabelle2025/src/HOL/Examples`: Example theories for testing.