# IsarLite — Isabelle theorem prover for AI agents.

An ultra-portable cli for agentic proof engineering with Isabelle/HOL — no Scala, no JVM, no jEdit.

IsarLite talks to Isabelle's Poly/ML process over TCP using the PIDE protocol (Prover IDE, Isabelle's document-oriented communication protocol). Commands are encoded in YXML (Isabelle's compact XML-like wire format) and sent directly to the prover.

**Requires Python >=3.12 and an Isabelle2025 installation** (see [Developing](#developing)).

```bash
pip install -e .
isar --help               # prints CLI reference (works standalone)
```

Actual proof-state queries also need `ISABELLE_HOME` set — see [Usage](#usage) below.

## Usage

```
isar state-at <file.thy> <line> <col> [--isabelle-home <path>] [-d <session-dir>]
```

- `--isabelle-home`: path to Isabelle installation (default: `$ISABELLE_HOME` env var)
- `-d <session-dir>`: optional session root directory — bounds the ROOT/ROOTS walk-up for session resolution (auto-detected from `<file.thy>` by default)

Example:

```bash
export ISABELLE_HOME=/path/to/Isabelle2025

# Query the proof state at line 76, column 1 of Ackermann.thy
isar state-at $ISABELLE_HOME/src/HOL/Examples/Ackermann.thy 76 1
```

The `state-at` command loads the theory and returns what Isabelle reports at that position — typically open proof goals (`STATE` markup) and a proof outline or sledgehammer suggestions (`INFORMATION` markup).

## Developing

### Prerequisites

- Python >=3.12
- [uv](https://docs.astral.sh/uv/)
- Isabelle **2025** (currently only exact version are tested). Download from [isabelle.in.tum.de](https://isabelle.in.tum.de/website-Isabelle2025/index.html). Only exact 2025 is supported.

### Isabelle2025 Setup

Place Isabelle2025 into `refs/Isabelle2025`:

```
refs/Isabelle2025/
├── bin/
├── contrib/
├── doc/
├── etc/
├── heaps/
├── lib/
├── src/
│   ├── Pure/
│   ├── HOL/
│   ├── Doc/
│   └── ...
├── ROOT
└── ROOTS
```

Either copy the tree there or create a symlink:

```bash
ln -s /path/to/Isabelle2025 refs/Isabelle2025
```

### l4v (optional)

To run integration tests against seL4 formal proofs, place [l4v](https://github.com/seL4/l4v) (the seL4 verification proof library, version 14.0.0 or newer, with all build artifacts generated) at `test/assets/l4v/`:

```
test/assets/l4v/
├── proof/
├── spec/
├── camkes/
├── lib/
├── docs/
└── misc/
```

### Install Dependencies

```bash
uv sync
```

This installs the package in editable mode plus dev dependencies (pytest, basedpyright). `ISABELLE_HOME` points to your Isabelle tree, and `$ISABELLE_HOME/bin/isabelle` is the CLI entry point for building session heaps.

### Run Tests

Ensure `ISABELLE_HOME` is set:

```bash
# All tests (unit + integration)
pytest -s test/

# Unit tests only (no prover needed)
pytest test/test_tokenizer.py test/test_yxml.py test/test_protocol.py -v

# Integration tests only (requires built session heaps ~ compiled images)
export ISABELLE_HOME=$(pwd)/refs/Isabelle2025
$ISABELLE_HOME/bin/isabelle build -b -v Pure   # ~30s
$ISABELLE_HOME/bin/isabelle build -b -v HOL    # ~5min
pytest test/integration/ -v
```

## Project Structure

| Component              | Description                                                    |
| ---------------------- | -------------------------------------------------------------- |
| `src/isarlite/base.py` | Core library — PIDE protocol, YXML, tokenizer, prover process  |
| `src/isarlite/cli.py`  | CLI entry point (`isar`), proof state formatting               |
| `test/`                | Pytest tests (unit + integration)                              |
| `tools/`               | Utility scripts — dump markup kinds, timing, raw YXML          |
| `notes/`               | AI-generated documentation by RTFSC about the Isabelle details |
| `refs/`                | Isabelle2025 references                                        |

## Design

IsarLite ships everything in two files:

- **[base.py](src/isarlite/base.py)** — all PIDE protocol internals: YXML encoding/decoding, tokenizer (ported from Scala), keyword table, prover process lifecycle, message framing, session resolution
- **[cli.py](src/isarlite/cli.py)** — `isar` CLI, `PIDESession` state management, proof goal formatting

## Testing Notes

- Integration tests use `pytest.mark.skipif` and auto-skip when heaps aren't built (seeing skips is normal unless you need those particular tests).
- Example theories for testing: `refs/Isabelle2025/src/HOL/Examples/`.
- Place shared fixtures and markers in `test/conftest.py` or `test/integration/conftest.py`.

## Tools

All tools require `ISABELLE_HOME` to be set.

- [tools/dump_markup_kinds.py](tools/dump_markup_kinds.py) — dump every PIDE message kind at a position. Usage: `python tools/dump_markup_kinds.py <file.thy> <line> <col>`
- [tools/state_at_json.py](tools/state_at_json.py) — dump raw YXML payloads as JSON. Usage: `python tools/state_at_json.py <file.thy> [--line L] [--col C]`
- [tools/get_timing.py](tools/get_timing.py) — print command timing (elapsed, CPU, GC) for each span. Usage: `python tools/get_timing.py <file.thy> [line] [col]`