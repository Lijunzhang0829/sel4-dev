# State-at-Position: Proof State Querying in the PIDE Protocol

How the Isabelle/jEdit IDE requests proof state at a cursor position,
from the GUI trigger through the ML prover, and back to the display layer.

## Overview

Three message kinds carry proof-related output:

| Mechanism | Message kind | Content | Trigger |
|-----------|-------------|---------|---------|
| `show_states` / `print_state` | `Markup.stateN` (`"state"`) | Proof goals | Automatic after command eval |
| `print_state_query` overlay | `Markup.resultN` (`"result"`) with `<state>` inside | Proof goals | Explicit IDE query via overlay |
| `Proof.show_cases` | `Markup.informationN` (`"information"`) | Proof outline with cases | Automatic during structured induction |

In the Python PIDE client (`PIDESession.state_at`), `STATE` and `INFORMATION` are
collected independently — no fallback chain. The return type uses typed aliases:

- `StateStr` = content from `STATE` markup (proof goals)
- `InfoStr` = content from `INFORMATION` markup (proof outline, etc.)

See `lab/dump_markup_kinds.py` for a diagnostic tool that dumps all message
kinds at a given position.

## End-to-end flow

### Automatic path (the one IsarLite uses)

1. **Command evaluation**: The prover executes each command via `Document.update`.
   For proof commands, the ML evaluator transitions `Toplevel.state` into proof mode.

2. **`show_states`** (`toplevel.ML:624`): After each command evaluation, checks
   `Options.default_bool show_states` (enabled by default). If the command
   transitioned to proof mode, calls `Output.state (Toplevel.string_of_state st')`.

3. **`print_state`** (`command.ML:477`): A print function that fires for commands
   whose keyword passes `Keyword.is_printed` (lemma, theorem, have, show, etc.).
   Also calls `Output.state` — redundant with `show_states` in most cases.

4. **`Proof.show_cases`**: During structured induction (`proof (induction ...)`),
   emits a `Markup.informationN` message with the "Proof outline with cases" text.

5. **Output.state** → `isabelle_process.ML:125-126`:
   ```ml
   Unsynchronized.setmp Private_Output.state_fn
     (fn s => standard_message (serial_props ()) Markup.stateN s)
   ```

6. **Wire format**: The message is sent as a `Byte_Message` frame with
   kind=`"state"` (or `"information"`), YXML body, and `Position.Id(exec_id)`.

### Query overlay path (IDE-specific)

The IDE uses `print_state_query` overlay for cursor-based queries:

1. `Query_Operation.apply_query(["cmd"])` inserts a `print_state_query` overlay
   on the target command
2. `Document.update` with perspective carries the overlay
3. ML `Command.print` resolves the overlay via `Query_Operation.register`
4. `output_result` sends `Output.result` with kind=`"result"` and `<state>` nested
5. Scala `Query_Operation.content_update` filters results by `instance` property

The Python client does NOT use this path — it relies on the automatic path
(Approach A above), which is simpler and works without a functioning overlay
mechanism.

## Key data structures

### ML side
```
Toplevel.state = Theory.state | Proof.state
Command.eval  = keywords × master_dir × init × blobs × command_id × span × prev_state
               → {failed, command: Toplevel.transition, state: Toplevel.state}
```

### Python side (`PIDESession`)

- `_spans: list[CommandSpan]` — parsed command spans with IDs
- `_assignments: dict[version_id, dict[command_id, list[exec_id]]]` — exec ID mapping
- `_output: dict[exec_id, list[ProtocolMessage]]` — output messages by exec ID
- `state_at(line, col)` → `Result[tuple[Maybe[StateStr], Maybe[InfoStr]], str]`

## How `PIDESession.state_at` works

1. **Resolve span**: `_span_at_offset` finds the nearest named command span
   before the given position. Position inside a named span body is rejected.
2. **Get exec IDs**: Looks up `span.id` in the latest version's assignment table.
3. **Collect markup**: Single pass through exec IDs, collecting `STATE` and
   `INFORMATION` messages independently.
4. **Format**: `STATE` content is passed through `_format_goal_brackets`
   (converts chained implications to `\<lbrakk>...\<rbrakk>`). `INFORMATION`
   content is returned as-is.
5. **Return**: `Success((Some(state), Some(info)))` — each field is `Maybe` since
   not all commands produce both kinds.

## Verification tools

- `lab/dump_markup_kinds.py` — connects, loads a theory, dumps ALL message kinds
  for a given position. Use to discover what markup a command produces.
- API-level tests: `test/integration/test_theory.py` (state_at queries), `test/integration/test_session.py` (protocol edge cases)
- CLI-level tests: `test/integration/test_cli.py` (state-at command, error handling)

## Reference files

| File | Role |
|------|------|
| [query_operation.scala](../refs/Isabelle2025/src/Pure/PIDE/query_operation.scala) | Scala: manages the overlay-based query lifecycle |
| [query_operation.ML](../refs/Isabelle2025/src/Pure/PIDE/query_operation.ML) | ML: registers print_state_query, calls `Output.result` |
| [command.ML](../refs/Isabelle2025/src/Pure/PIDE/command.ML) | ML: Command.eval, Command.print, print_state print function |
| [command.scala](../refs/Isabelle2025/src/Pure/PIDE/command.scala) | Scala: Command.State, results accumulation |
| [protocol.ML](../refs/Isabelle2025/src/Pure/PIDE/protocol.ML) | ML: Document.update handler, decodes edits |
| [document.ML](../refs/Isabelle2025/src/Pure/PIDE/document.ML) | ML: Document.update, command_overlays, new_exec |
| [document.scala](../refs/Isabelle2025/src/Pure/PIDE/document.scala) | Scala: Document.Snapshot, command_results |
| [session.scala](../refs/Isabelle2025/src/Pure/PIDE/session.scala) | Scala: Session manager, handle_output, change_buffer |
| [toplevel.ML](../refs/Isabelle2025/src/Pure/Isar/toplevel.ML) | ML: string_of_state, show_state, Toplevel transitions |
| [output.ML](../refs/Isabelle2025/src/Pure/General/output.ML) | ML: Output channels (state, result, writeln, etc.) |
