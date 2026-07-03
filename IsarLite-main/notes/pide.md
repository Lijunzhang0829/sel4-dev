# PIDE — The Shared Protocol Layer

PIDE (Prover IDE) is the document-oriented protocol that both `isabelle build` (batch proof checking) and the interactive GUI (jEdit/VSCode) use to communicate with the Isabelle prover. It models theory files as **documents** composed of **commands** that are asynchronously evaluated, with results streamed back as **markup**. This is the same model whether driven by a headless session or a GUI editor.

## Isabelle's Two-Sided Architecture

Isabelle runs as two processes connected by a byte-stream socket:

- **Prover** (Poly/ML): the proof engine. Poly/ML is a Standard ML compiler and runtime that Isabelle embeds. It provides the thread pool, futures library, and garbage collector that PIDE's async execution depends on. All proof checking runs inside this single Poly/ML process.
- **Frontend** (Scala/JVM): the user interface. It manages editor state, sends edits as the user types, and renders prover output as syntax highlighting, error squiggles, tooltips, etc. Both jEdit and VSCode use the same Scala layer.

They communicate via PIDE protocol messages: commands flow from frontend to prover, results (markup) flow back. Each side maintains its own `Document.State`: the ML side is the **execution authority** (which commands are running), the Scala side is the **query authority** (what markup to display). The two synchronize through the assign-update protocol message.

## Isabelle Concepts (30 seconds)

A **theory** (`.thy` file) is a sequence of **commands** — individual statements like `lemma`, `by`, `definition`, `fun`. Theories form an import DAG (directed acyclic graph — a dependency graph where no theory can import itself, directly or transitively): theory A importing theory B means A depends on B. Each theory becomes a **node** in the document graph.

In Isabelle, **outer syntax** refers to the command-level grammar (`lemma`, `by`, `definition`), while **inner syntax** is the term and type language inside commands. A node's **keyword table** is the merged set of all command and keyword declarations from the Pure framework plus all imported theories — it tells the parser which words are commands (like `lemma`, `by`) versus identifiers or inner-syntax tokens.

**Blobs** are auxiliary files (e.g., ML files loaded via `ML_file`) tracked by their SHA1 digest. When a command references a blob, the blob's content is sent alongside the command definition.

## Preliminaries — Key Concepts

**YXML**: Isabelle's wire format for structured data. It is a binary-friendly encoding of XML that uses a single sentinel byte `0x05` (ASCII ENQ, written `\5` in Isabelle source) to mark tree structure, avoiding the verbosity of angle brackets. Each protocol message is a YXML body preceded by a length-prefixed header. See [yxml.ML](../refs/Isabelle2025/src/Pure/PIDE/yxml.ML), [yxml.scala](../refs/Isabelle2025/src/Pure/PIDE/yxml.scala).

**Markup**: typed, position-indexed metadata attached to command results. Every piece of prover output — a type annotation, an error message, a theorem reference — is a markup element with a `Markup` kind and a source position. Markup is the sole mechanism for the prover to communicate results; the frontend queries markup by command ID and position range.

Markup falls into two categories:
- **Status markup**: lifecycle transitions tracking command progress — `RUNNING`, `FINISHED`, `FAILED`, `FORKED`, `JOINED`, `CANCELED`. These are low-latency markers emitted during execution.
- **Content markup**: semantic results produced after evaluation — `TYPING` (type annotations), `ENTITY` (definition/reference links), `STATE` (proof goals), `ERROR`, `WARNING`, `WRITELN` (user-visible output). These are produced by **print functions** (ML functions that run after a command finishes evaluating, scanning the result for displayable information like types, references, and syntax highlighting).

### Futures (30 seconds)

A **future** is a placeholder for a value being computed on another thread. `Future.fork(fn)` starts computing `fn()` on a worker thread; the caller gets a future handle back immediately. The caller can later `Future.join(handle)` to block until the value is ready, or pass the handle as a **dependency** to another future (the scheduler waits automatically). Poly/ML runs futures on a fixed-size thread pool. All PIDE concurrency — command evaluation, print functions, node scheduling — is built on futures.

**Consolidated**: a node is "consolidated" once its final theory command has finished eval, all eval forks in the node have joined, and every persistent print function has run. Consolidation is the terminal state — it means the node is fully processed with no pending work.

**Command ID vs. Exec ID**: each command has a static `Document_ID.command` assigned at definition time (`define_command`). Command IDs are generated on the Scala side and sent with `Document.define_command`. When the command is **executed** (evaluated), one or more `Document_ID.exec` IDs are created on the ML side — one for the eval, plus one per print function. The assignment table maps `command_id → [eval_id, print_id1, print_id2, ...]`. A command can be re-executed across versions, producing new exec IDs each time. The ML side and Scala side generate IDs independently (ML increments positive, Scala decrements negative), ensuring their ID spaces never overlap — see "ID Types and ID Spaces" below.

**Toplevel transition**: the ML-level operation of advancing Isabelle's proof state. `Toplevel` is the ML module that manages Isabelle's proof state; a `Toplevel.state` represents the current theory-or-proof-mode context, and a `Toplevel.transition` is a function that transforms it. Isabelle is a two-mode state machine: in **theory mode** you define types, constants, and state lemmas; a `lemma` command transitions into **proof mode** with a goal to prove; a `by` command runs a proof method and transitions back to theory mode, recording the proved theorem. This state transformation is the actual "proof checking" step.

**Perspective**: the subset of commands in a node that the frontend declares as "visible" or "required." Only commands in the perspective are executed and receive print functions; commands outside it have their evaluation garbage-collected. The GUI updates perspective as the user scrolls; headless mode declares all commands required.

## Architecture Overview

```
┌──────────────────────────────┐
│  Frontend (GUI or Headless)  │  Scala side
│  Session + Document.State    │
├──────────────────────────────┤
│  Protocol messages (YXML)    │  transport (stdin/stdout or socket)
├──────────────────────────────┤
│  Prover (Poly/ML process)    │  ML side
│  Document.state + Execution  │
└──────────────────────────────┘
```

The protocol has two sides: the Scala side defines message formats in [protocol.scala](../refs/Isabelle2025/src/Pure/PIDE/protocol.scala), the ML side handles them in [protocol.ML](../refs/Isabelle2025/src/Pure/PIDE/protocol.ML).

## Core Data Model

The document model is defined in [document.ML](../refs/Isabelle2025/src/Pure/PIDE/document.ML) (ML) and [document.scala](../refs/Isabelle2025/src/Pure/PIDE/document.scala) (Scala). The descriptions below use ML type notation, but the concepts translate directly to the Scala side. Key notation: `'a option` = nullable/maybe (`SOME x` or `NONE`), `a * b` = pair/tuple, `unit lazy` = a lazily-computed value, `Linear_Set` = an insertion-ordered set (like a linked hash set), `Inttab.table` = a hash table keyed by integer IDs (like `dict[int, T]`). Later sections use Scala notation: `List[X]` = list of X, `Map[K, V]` = map from K to V, `Option[A]` = Scala's equivalent of ML's `'a option`.

### Document = Named Nodes in a DAG

A **Document** is a collection of versions. Each **Version** is a directed acyclic graph of **nodes** (theory files) connected by imports. In the code this is `Graph[Node.Name, Node]` — a graph keyed by node name. Auxiliary files (ML files, image assets) are stored as **blobs** (raw byte content keyed by SHA1 digest).

Each `Node` contains:
- **header**: `{master: directory, header: Thy_Header, errors: string list}` — theory name, imports with positions, keyword declarations, parse errors
- **keywords**: the merged `Keyword.keywords` from Pure + all imports + local declarations; used to parse commands in this node
- **entries**: an ordered `Linear_Set` mapping `Document_ID.command → (Command.eval * print list) option` — the commands with their execution state (or `NONE` if not yet executed)
- **perspective**: `{required: bool, visible: Bitset, visible_last: command option, overlays: (command_id → (print_fn * args) list)}` — which commands the frontend cares about. `visible` is a compact bitset indexed by `Document_ID.command`, marking which commands are inside the editor viewport. Overlays are temporary print-function registrations keyed to command IDs, used for queries like `print_state` and Sledgehammer (see Output Panel / Sledgehammer sections).
- **result**: `(command_id * Command.eval) option` — the result of the last command's eval (holding the final `theory` value)
- **consolidated**: `unit lazy` — lazily computed flag. The first time any code evaluates (forces) this flag, the prover runs all pending presentation output (LaTeX, HTML generation) and caches the result; subsequent reads return the cached value immediately.

### Commands

A **command** is defined via `Document.define_command` and contains:
- `command_id`: unique integer ID
- `name`: the command kind (e.g., `"lemma"`, `"by"`, `"definition"`)
- `parents`: list of theory URLs this command's theory imports (for the theory header command)
- `blobs_digests`: list of `{file_node, src_path, digest} Exn.result` for ML file `blobs` referenced by this command
- `blobs_index`: which token position holds the file references
- `tokens`: lazily-parsed `Token.T list` — the parsed source span

The `name` field determines which outer syntax parser to use (`Outer_Syntax.parse` uses the keyword table from the node's `keywords`).

### Versioning

Every edit produces a new **version** of the document. Each version, once created, is never modified (immutable data structure). Old versions are eventually garbage-collected via `remove_versions`. This enables:
- **Undo/redo** in the GUI (history navigation via `History` in [document.scala](../refs/Isabelle2025/src/Pure/PIDE/document.scala))
- **Incremental updates**: only re-execute commands that changed or depend on changed results

The invariants:
- Each version has a unique `Document_ID.version`
- Versions form a linear history (`History.undo_list` — a `List[Change]` with `Change.init` at the tail)
- `tip` is the most recent change (latest edit); `recent_stable` is the most recent change whose assign-update (the protocol message carrying execution results from ML to Scala, see Phase 3: Assignment below) has been fully processed. Because the prover executes asynchronously, assign-updates lag behind edits — there can be several versions queued ahead of the last fully-processed one. `tip` tracks the user's latest edit while `recent_stable` tracks the latest renderable state.

## The Edit-Execute-Assign Cycle

Both batch and interactive mode follow the same three-phase cycle. Here it is traced end-to-end.

### Phase 1: Edits

The frontend sends `Document.update` to the prover. The message carries:
- `old_id` / `new_id`: version IDs (the transition)
- `edits`: list of per-node changes — either `Edits` (command insert/remove), `Deps` (header/dependency change), or `Perspective` (visible range, required flag)

On the ML side (`Document.update` in [document.ML](../refs/Isabelle2025/src/Pure/PIDE/document.ML)):

1. **Apply edits**: `edit_nodes` folds the edit list over the `old_version`, producing `new_version`. For `Edits`, it inserts/removes entries in the command `Linear_Set`. For `Deps`, it updates the theory header and rewires the dependency graph edges (handling cycles by accumulating error messages). For `Perspective`, it updates the visible command mask.

2. **Update keywords**: `edit_keywords` propagates keyword declarations through the dependency graph — if a node's imports change, its descendants' merged keyword tables are recomputed.

3. **Compute the "last common" command** (`last_common` function): for each changed node, walk through its commands. A command is **NOT reused** (and everything after it is re-executed) when:
   - Its eval result differs from the previous version's result (`not Command.eval_eq`), OR
   - It is currently running (its eval was started under the old execution, which has been discontinued, so it may produce a stale `Toplevel.state`), OR
   - It is no longer in the new perspective.

   Otherwise the command is reused and the walk continues to the next command.

4. **Re-execute changed commands** (`new_exec`): starting from just after the common command, call `Command.eval` for each command. `Command.eval` produces a **suspended computation record** (an `(eval, prints)` pair containing an unevaluated thunk, not an already-executed result). The actual proof checking happens later when Phase 2 submits this thunk to the thread pool. `Command.eval` takes:
   - The keywords table (to parse the command)
   - The master directory (for resolving `blobs` file paths)
   - An initialization function for the theory header command (calls `Resources.begin_theory`)
   - Inlined blob content
   - The current `Toplevel.state` from the previous command's eval result

   Each eval produces a `(eval, prints)` pair. The new execs are accumulated into an `assign_update` table.

5. **Build the new version**: `define_version` stores `new_version` in the ML state with the new execution.

### Phase 2: Execution

`Document.start_execution` in [document.ML](../refs/Isabelle2025/src/Pure/PIDE/document.ML):

1. **Determine required nodes**: `make_required` computes the transitive closure of required + visible nodes through the dependency graph. In the import DAG ("A imports B" means A depends on B): visibility propagates **downward** through imports (all ancestors of a visible node must be computed, because their results feed into the visible node). Required-ness propagates **upward** through importers (a node is required if any visible theory depends on it, because its evaluation is needed for those visible descendants).

2. **Schedule nodes**: `String_Graph.schedule` (from [General/graph.ML](../refs/Isabelle2025/src/Pure/General/graph.ML)) performs a topological sort of the import DAG (a `String_Graph.T` — a graph keyed by node name strings), grouping nodes by dependency level. For each node, it:
   - Checks if the node is required, visible, or has a pending result
   - Waits for all import dependencies' eval futures to complete
   - Calls `iterate_entries` to process commands sequentially: for each command entry that has an `exec` (`SOME (eval, prints)`), calls `Command.exec` — which executes the suspended computation inside a future on the thread pool (forcing the thunk produced by `Command.eval` in Phase 1), then submits each print function as a separate future.
   - The node's work is wrapped in a `Future` whose dependencies include the imports' futures — this is what enforces the DAG ordering.

3. **Concurrency model**: nodes at the same dependency level execute in parallel (Poly/ML futures on a shared thread pool). Commands within a single node execute sequentially (each step reads the `Toplevel.state` produced by the previous step).

### Phase 3: Assignment

When execution completes, the `Document.update` function sends back an **assign update** protocol message (`Markup.assign_update`) containing:
- The `new_id` (version)
- The list of `edited` node names
- The `assign_update`: a list of `(command_id, [exec_ids...])` pairs

The Scala side ([session.scala](../refs/Isabelle2025/src/Pure/PIDE/session.scala) `handle_output`) processes this:
1. `State.assign(version_id, edited, update)` — stores the new assignment in `State.assignments`, creates new exec entries in `State.execs` for any previously-unseen exec IDs
2. Posts `Commands_Changed` to notify consumers (GUI renderer, headless progress tracker) which nodes and commands have fresh results

This completes the feedback loop: edits flow from frontend to prover, assign updates flow back.

## Exec ID Lifecycle

This section traces an exec ID from creation to garbage collection — the complete lifecycle of the identifiers that the protocol uses to track command evaluation.

### ID Types and ID Spaces

The ML side (prover) and Scala side (frontend) use **separate ID counters**:

| Side | Type | Direction | First ID |
|------|------|-----------|----------|
| ML | `type exec = generic = int` | Increments positive | 2, 4, 6, ... |
| Scala/JVM | `type Exec = Generic = Long` | Decrements negative | -1, -2, -3, ... |

This is just a convention (defined in [document_id.scala](../refs/Isabelle2025/src/Pure/PIDE/document_id.scala) and [document_id.ML](../refs/Isabelle2025/src/Pure/PIDE/document_id.ML)) — the two sides never need to agree on numeric values because ML creates exec IDs and the Scala side receives them passively via the assign_update. The sign convention prevents accidental collision during debugging when IDs appear in logs. The counter implementations are in [counter.scala](../refs/Isabelle2025/src/Pure/Concurrent/counter.scala) (Scala) and [counter.ML](../refs/Isabelle2025/src/Pure/Concurrent/counter.ML) (ML).

### Stage 1: Creation

Exec IDs are minted via `Document_ID.make()` in two places within [command.ML](../refs/Isabelle2025/src/Pure/PIDE/command.ML):

1. **Eval exec** (`Command.eval`, line 264): one exec ID per command evaluation — this is the primary ID representing the command's execution.
2. **Print execs** (`Command.make_print`, line 319): one exec ID per persistent print function. Print functions produce markup (type info, entity references, etc.) that the GUI renders.

So a single command evaluation produces an `exec` tuple: `(eval_exec_id, [print_exec_id1, print_exec_id2, ...])`. The `exec_ids` helper ([command.ML](../refs/Isabelle2025/src/Pure/PIDE/command.ML) line 413) extracts all IDs from the tuple.

### Stage 2: Execution and Status Emission

During execution, the ML side emits **status markups** at key transitions ([command.ML](../refs/Isabelle2025/src/Pure/PIDE/command.ML) lines 222-257):

```
running  →  (eval runs)  →  finished   (success)
                          →  failed     (error, then finished)
                          →  canceled   (interrupted)
```

When a command spawns sub-computations (e.g., parallel proof methods or ML `Future.fork`), each is tracked as a separate fork with its own lifecycle ([execution.ML](../refs/Isabelle2025/src/Pure/PIDE/execution.ML) lines 139-186):
```
forked  →  running  →  finished/failed  →  joined
```

Each status marker is sent as a `STATUS` protocol message referencing the exec ID. These are the **only** source of truth for command progress — the GUI never uses timeouts or heuristics.

On the Scala side, `Command.State.accumulate` ([command.scala](../refs/Isabelle2025/src/Pure/PIDE/command.scala) lines 202-353) collects these status markers into a `status: List[Markup]` list. `Document_Status.Command_Status` ([document_status.scala](../refs/Isabelle2025/src/Pure/PIDE/document_status.scala) lines 13-93) derives the composite state:

| Derived state | Condition |
|---------------|-----------|
| `is_unprocessed` | Accepted, not failed, and either untouched or has pending forks with no runs |
| `is_running` | `runs != 0` (at least one active eval) |
| `is_finished` | Not failed, touched, all forks joined, no runs |
| `is_warned` | Has `WARNING` or `LEGACY` markup |
| `is_failed` | Has `FAILED` or `ERROR` markup |
| `is_canceled` | Has `CANCELED` markup |
| `is_finalized` | Theory end reached (has `FINALIZED` markup) |

### Stage 3: Assignment

The assign_update is the bridge between ML exec IDs and Scala state. During `Document.update` ([document.ML](../refs/Isabelle2025/src/Pure/PIDE/document.ML) lines 806-912):

1. A table `assign_update: (command_id → Command.exec option) Inttab.table` is built.
2. `assign_update_result` flattens it to `(command_id, [eval_exec_id, print_exec_id1, ...])` pairs.
3. The result is sent via `Output.protocol_message Markup.assign_update` with YXML encoding of `(version_id, edited_nodes, [(command_id, "id1,id2,..."), ...])`.

On the Scala side ([protocol.scala](../refs/Isabelle2025/src/Pure/PIDE/protocol.scala) lines 45-63), `Protocol.Assign_Update` decodes this. `State.assign` ([document.scala](../refs/Isabelle2025/src/Pure/PIDE/document.scala) lines 1060-1097) then:

1. Copies the command's **static state** into `State.execs` keyed by the **eval exec ID** (the first ID in each list).
2. Creates empty `Command.State` entries keyed by each **print exec ID**.
3. Updates `State.assignments(version).command_execs` with the new `command_id → [exec_ids]` mapping.

After assignment, the Scala session manager routes all incoming prover messages — status markers, REPORT markup, WRITELN output, WARNING/ERROR diagnostics — to the correct `State.execs` entry using the exec ID carried in each message's properties. `Command.State.accumulate` handles this routing: it appends status markers to the status list, appends REPORT markup to the markup tree, and collects output messages into the results table. All markup types (not just status markers) accumulate here, building up the data that Snapshots query for rendering.

### Stage 4: Cleanup

There are two cleanup paths at different timescales:

**Immediate** (when a new `Document.update` arrives while execution is still in progress):
1. `Execution.discontinue()` ([execution.ML](../refs/Isabelle2025/src/Pure/PIDE/execution.ML)) invalidates the current execution ID. All running futures check `Execution.is_running` and skip further work.
2. Removed exec IDs are collected and their futures are canceled.
3. `Execution.purge(removed_exec_ids)` deletes them from the ML `execs` table.

**Periodic** (version GC, coordinated between Scala and ML):
The Scala side periodically calls `State.remove_versions(retain)` which prunes old document versions. Unreachable exec states are purged from `State.execs`, and the dropped version IDs are sent to the ML side via `Document.remove_versions` for server-side cleanup. See "Garbage Collection" below for the full protocol exchange.

## Protocol Messages

All communication uses YXML-encoded messages over byte streams. The Scala side sends commands on the socket stream `command_input` (Scala → ML); the prover sends results on the socket stream `message_output` (ML → Scala). Both use length-prefixed byte chunks. See [prover.scala](../refs/Isabelle2025/src/Pure/PIDE/prover.scala).

### Key protocol commands (Scala → ML)

| Command | Purpose |
|---------|---------|
| `Document.define_command` | Register a command: its ID, name, parent theories, blob digests, lazily-parsed tokens |
| `Document.define_commands` | Bulk variant — registers multiple commands in one message |
| `Document.update` | Submit edits: carries `old_id`, `new_id`, list of per-node edits, list of nodes to consolidate |
| `Document.remove_versions` | GC old versions: carries a YXML-encoded `int list` of version IDs |
| `Document.cancel_exec` | Cancel a running exec by its ID |
| `Document.discontinue_execution` | Invalidate the current execution (causes all running evals to be discarded) |
| `Prover.options` | Set system options (YXML-encoded `Options`) |
| `Prover.init_session` | Initialize session resources (loaded theories, file-system state) |

### Key result messages (ML → Scala)

Messages on the wire have a **protocol envelope** type (e.g., `STATUS`, `REPORT`, `RESULT`). Some envelope types carry **markup payloads** (e.g., `Markup.running`, `Markup.TYPING`) inside them.

**Protocol envelope types** (the structure of each wire message):

| Envelope | Content |
|----------|---------|
| `Markup.assign_update` | `(version_id, edited_nodes, (command_id, exec_ids) list)` — result of an update |
| `Markup.removed_versions` | ACK of version removal |
| `STATUS` | Carries exec lifecycle markup (e.g., `running`, `finished`, `failed`) |
| `RESULT` | Carries eval result markup (proof state, theorem value) |
| `REPORT` | Carries position-linked markup (type info, references, syntax highlighting) |
| `WRITELN` / `WARNING` / `ERROR` | Carries user-visible output markup |

**Markup kinds** (payload inside the envelopes above):

| Markup kind | Envelope | Meaning |
|-------------|----------|---------|
| `Markup.running` / `Markup.finished` / `Markup.failed` / `Markup.joined` / `Markup.canceled` | `STATUS` | Exec lifecycle transitions |
| `Markup.forked` / `Markup.joined` | `STATUS` | Sub-computation lifecycle |
| `Markup.consolidating` / `Markup.consolidated` | `STATUS` | Node consolidation progress |
| `Markup.bad` | `REPORT` | Command-level error (parse failure, type error, proof failure) |
| `Markup.RESULT` | `RESULT` | Command evaluation result |
| `Markup.WRITELN` / `Markup.WARNING` / `Markup.ERROR` | `WRITELN` etc. | User-visible output |
| `Markup.TYPING` / `Markup.ENTITY` / `Markup.STATE` | `REPORT` | Semantic markup (type info, references, proof goals) |

### Lifecycle of a single command's output

1. Prover sends `STATUS {running}` for the exec
2. Eval runs, produces `RESULT` with the `Toplevel.state`
3. Print functions run, producing `REPORT` markups (typing, refs, etc.)
4. Prover sends `STATUS {finished}`
5. On `assign_update`, the Scala side links these to the command/version

This lifecycle is identical regardless of whether the frontend is headless or GUI.

## Markup and GUI Rendering

This section covers how markup — the typed, position-indexed metadata produced by the prover — drives every visible feature in the GUI.

### Markup Query Mechanism

The GUI queries markup through `Document.Snapshot` ([document.scala](../refs/Isabelle2025/src/Pure/PIDE/document.scala) lines 769-814). A **Snapshot** is a point-in-time, immutable view of a specific document version's state. It captures all command results and accumulated markup as they existed at that version, ensuring consistent rendering even as new prover output arrives for newer versions. Snapshots are cheap to create — they are read-only views over the shared `State`, not copies.

The two core query methods:

First, the **Markup_Index** selects which markup stream to query ([document.scala](../refs/Isabelle2025/src/Pure/PIDE/document.scala) line 782):
- **`status=true`**: syntax-status markup — `ACCEPTED`, `FORKED`, `RUNNING`, `FINISHED`, `FAILED`, `CANCELED`. These are low-latency markers emitted during execution.
- **`status=false`** (default): consolidated markup — semantic results like `ENTITY`, `TYPING`, `PATH`, `STATE`. These are available after the command finishes.

**`Snapshot.cumulate[A]`** — fold over all matching markup in a text range. Takes a start value `A` and walks the markup tree, accumulating results wherever the markup kind matches `elements`. Stops descending when the callback returns `None`.

**`Snapshot.select[A]`** — find specific markup matches. Wraps `cumulate` with an `Option[A]` accumulator: the callback returns `Some(value)` to record a hit, `None` to skip. Returns only successful matches. Used for "find first/all occurrences" patterns (e.g., find the hyperlink at the cursor).

### Command Status Colors

The colored background bar behind each command in the editor is driven by status markups queried via `rendering.background()` ([rendering.scala](../refs/Isabelle2025/src/Pure/PIDE/rendering.scala) lines 441-495).

The elements queried are `Document_Status.Command_Status.proper_elements`:

| Status markup | Meaning | Display color |
|---------------|---------|---------------|
| `ACCEPTED` | Command registered, not yet executed | (prerequisite) |
| `FORKED` | A sub-computation was forked | (prerequisite) |
| `RUNNING` | Eval is executing | Green (`Color.running1`) |
| `FINISHED` | Eval completed (success or failure) | (neutral) |
| `FAILED` | Eval encountered an error | Red (`Color.bad`) |
| `CANCELED` | Execution was canceled | Gray (`Color.canceled`) |
| `JOINED` | Forked sub-computation joined | (prerequisite) |

`Document_Status.Command_Status.make` ([document_status.scala](../refs/Isabelle2025/src/Pure/PIDE/document_status.scala) lines 21-53) combines these into a composite status. `rendering.background()` maps the composite state to colors: `is_unprocessed` → yellow, `is_running` → green, `is_canceled` → gray, and otherwise draws from `BAD` report markup (not directly from the `FAILED` status markup) for error red. The text overview (scrollbar gutter) uses a similar query with additional `WARNING`/`LEGACY`/`ERROR` elements to show orange/red markers for warnings and errors.

### Error Squiggles and Diagnostics

Underline squiggles and gutter icons are driven by message markups.

**jEdit** ([jedit_rendering.scala](../refs/Isabelle2025/src/Tools/jEdit/src/jedit_rendering.scala) lines 141-142, 354-355):

```scala
val squiggly_elements =
  Markup.Elements(Markup.WRITELN, Markup.INFORMATION, Markup.WARNING, Markup.LEGACY, Markup.ERROR)
```

`squiggly_underline()` calls `message_underline_color()` ([rendering.scala](../refs/Isabelle2025/src/Pure/PIDE/rendering.scala) lines 556-569), which uses `snapshot.cumulate[Int]` with message priorities:

| Priority | Markup kind | Color |
|----------|-------------|-------|
| 2 | `WRITELN` | Blue (`Color.writeln`) |
| 3 | `INFORMATION` | Blue (`Color.information`) |
| 5 | `WARNING` | Orange (`Color.warning`) |
| 6 | `LEGACY` | Orange (`Color.legacy`) |
| 7 | `ERROR` | Red (`Color.error`) |

The highest-priority message in each text region determines the squiggle color. The actual zigzag lines are painted in [rich_text_area.scala](../refs/Isabelle2025/src/Tools/jEdit/src/rich_text_area.scala) lines 382-393.

**VSCode** maps these to LSP diagnostics ([vscode_rendering.scala](../refs/Isabelle2025/src/Tools/VSCode/src/vscode_rendering.scala) lines 140-162):
- `Markup.ERROR` → `LSP.DiagnosticSeverity.Error`
- `Markup.LEGACY` → `LSP.DiagnosticSeverity.Warning`

### Goto-Definition (Hyperlinks)

When the user Ctrl-clicks an identifier, the GUI finds navigable targets via `rendering.hyperlink()` ([jedit_rendering.scala](../refs/Isabelle2025/src/Tools/jEdit/src/jedit_rendering.scala) lines 242-273).

The hyperlink elements queried are:

| Markup kind | What it provides | Navigation target |
|-------------|-----------------|-------------------|
| `Markup.ENTITY` | `def` property (declaration serial number) | Resolved to source position via `PIDE.editor.hyperlink_def_position` |
| `Markup.POSITION` | `FILE`, `LINE`, `OFFSET`, `ID` properties | Resolved via `PIDE.editor.hyperlink_position` |
| `Markup.PATH` | File path string | Opens the file |
| `Markup.DOC` | Documentation reference | Opens doc viewer |
| `Markup.URL` | External URL | Opens browser |

The query uses `snapshot.cumulate` with `hyperlink_elements`. The `ENTITY` mechanism is central: every definition in Isabelle (theorem, constant, type, etc.) gets an `ENTITY` markup with a `def` property containing a unique serial number. References to that entity carry a `ref` property. The GUI resolves `def` properties to source locations through the editor's position mapping.

### Tooltip and Hover

When hovering over source text, the tooltip shows type annotations, entity info, timing, and error messages.

The tooltip elements ([rendering.scala](../refs/Isabelle2025/src/Pure/PIDE/rendering.scala) lines 244-253) include:

| Markup kind | Displayed as |
|-------------|--------------|
| `Markup.TYPING` | `:: <type>` |
| `Markup.ML_TYPING` | `ML: <type>` |
| `Markup.ENTITY` | `name :: kind` (entity name and kind, plus command timing) |
| `Markup.SORTING` | Sort constraint |
| `Markup.CLASS_PARAMETER` | Class parameter display |
| `Markup.TIMING` | Elapsed time (if >= threshold) |
| `Markup.BAD` | Error context |
| `Markup.PATH` / `Markup.DOC` / `Markup.URL` | Resource location |
| `Markup.WRITELN` / `Markup.WARNING` / `Markup.ERROR` | Full message text |

The tooltip is rendered via `rendering.tooltips()` which uses `snapshot.cumulate[Tooltip_Info]`, accumulating all matching markup in the hovered range into a formatted list. In jEdit, the Ctrl modifier toggles between full tooltip (all elements above) and message-only tooltip (just `WRITELN`/`WARNING`/`ERROR`/`BAD`).

### Output Panel (Proof State)

The output panel shows proof goals and context for the command at the caret position.

**jEdit** (`state_dockable.scala`): Uses `Query_Operation` with the `"print_state"` print function. See "Sledgehammer Progress" below for a detailed explanation of the `Query_Operation` framework. Briefly: a temporary **overlay** (print function keyed to a command ID) is registered, the prover runs `print_state` asynchronously, and results stream back as `Markup.STATE` markup. On caret move, the current command is located, the overlay is registered, and the prover evaluates `print_state` for that command. The result — containing proof goals from `Markup.STATE` — is rendered as rich text in the dockable panel.

**VSCode** (`dynamic_output.scala`): `handle_update()` finds the current command via caret position, then calls `Rendering.output_messages()` which filters command results for: `STATE`, `WRITELN`, `INFORMATION`, `TRACING`, `WARNING`, `LEGACY`, `ERROR` messages. The output goes to a pretty-printed panel.

The key markup kinds for the output panel:

| Markup kind | Content |
|-------------|---------|
| `Markup.STATE` | Open proof goals (subgoals, assumptions, conclusion) |
| `Markup.INFORMATION` | Proof outline, cases, suggestions |
| `Markup.WRITELN` | Print output from the command |
| `Markup.WARNING` | Warnings generated by the command |
| `Markup.ERROR` | Errors (if command failed) |

### Sledgehammer Progress

Sledgehammer is Isabelle's automated proof-finding tool that calls external theorem provers (ATPs like E, Vampire, Z3) and SMT solvers. It is invoked via Isabelle's `Query_Operation` framework ([query_operation.scala](../refs/Isabelle2025/src/Pure/PIDE/query_operation.scala)).

When the user invokes Sledgehammer on a goal:

1. A **query overlay** (`sledgehammer_query` print function) is registered on the current command. The overlay causes the prover to execute Sledgehammer asynchronously.
2. The progress state is tracked through standard status markups:

   | Status | Markup | GUI display |
   |--------|--------|-------------|
   | Waiting | (none yet) | "Waiting for evaluation of context ..." with slow animation (5 fps) |
   | Running | `STATUS {running}` | "Sledgehammering ..." with fast animation (15 fps) |
   | Finished | `STATUS {finished}` | Animation stops, results displayed |
   | Failed | `STATUS {failed}` | Error displayed |

3. The `Query_Operation` class ([query_operation.scala](../refs/Isabelle2025/src/Pure/PIDE/query_operation.scala) lines 123-131) determines status by checking the accumulated results for `Markup.FINISHED` or `Markup.RUNNING` presence.
4. The `Process_Indicator` widget ([process_indicator.scala](../refs/Isabelle2025/src/Tools/jEdit/src/process_indicator.scala)) renders the animated spinner — it cycles through icon frames at the status-dependent rate.
5. If the running status message contains a `Position.Id`, the exec ID is extracted to enable **cancellation** via `cancel_exec`.
6. When finished, the results (found proofs, timings) arrive as `WRITELN` messages and are displayed in the Sledgehammer dockable panel.

The same `Query_Operation` mechanism is used by other long-running commands (Quickcheck, Nitpick, `find_theorems`), not just Sledgehammer.

### Entity Highlighting

When the caret is on an identifier, all occurrences of that entity are highlighted. This uses `rendering.entity_focus_defs()` ([rendering.scala](../refs/Isabelle2025/src/Pure/PIDE/rendering.scala) lines 509-551):

1. At the caret position, look for `Markup.ENTITY` — extract the `def` or `ref` serial number.
2. Across the visible text, find all `Markup.ENTITY` markups matching that serial number.
3. Highlight all matching ranges (definitions and references alike).

### Text Coloring

Syntax highlighting uses `rendering.text_color()` ([rendering.scala](../refs/Isabelle2025/src/Pure/PIDE/rendering.scala)). The markup elements include:

| Markup kind | Purpose |
|-------------|---------|
| `Markup.KEYWORD1` / `Markup.KEYWORD2` / `Markup.KEYWORD3` | Keywords (different colors by level) |
| `Markup.STRING` / `Markup.ALT_STRING` / `Markup.CARTOUCHE` | String literals |
| `Markup.COMMENT` / `Markup.COMMENT1` / `Markup.COMMENT2` / `Markup.COMMENT3` | Comments (different levels for nested/canceled) |
| `Markup.FREE` / `Markup.BOUND` / `Markup.VAR` | Variable kinds (free, bound, schematic) |
| `Markup.INNER_STRING` / `Markup.INNER_CARTOUCHE` | Embedded strings/cartouches |
| `Markup.TFREE` / `Markup.TVAR` | Type variables |
| `Markup.ANTIQUOTATION` | Antiquotations |

These are classification markups produced by the tokenizer and parser. The text painter uses `snapshot.select` to find the dominant `Text.Info[Color]` for each position. The table is not exhaustive — other kinds like `Markup.DELIMITER`, `Markup.INNER_COMMENT`, etc. also appear in rendering.

### End-to-End Trace: User Types a Lemma

Here is what happens when a user types `lemma "P ⟶ P"` in the editor and the caret is on the closing quote:

1. **Edit**: The keystroke triggers a pending edit. After `editor_input_delay` (~300ms), a `Document.update` is sent with `Edits` adding the `lemma` command. The node's `Perspective` includes the new command in the visible range.

2. **Prover processes**: `Document.update` on the ML side creates a new version, parses the command, and evaluates it. The eval runs `Toplevel.transition` for `lemma`, which enters proof mode with goal `P ⟶ P`.

3. **Status stream**: The prover emits `STATUS {running}`, then eval completes → `STATUS {finished}`, then print functions produce `REPORT` markups and proof state → `STATUS {consolidated}`.

4. **Assign**: The assign_update message carries `(command_id, [eval_exec_id, print_exec_id1, ...])` back to the Scala side. `State.assign` stores the exec states.

5. **GUI renders** (triggered by `Commands_Changed`):
   - **Background**: Status query shows `FINISHED` → neutral/white background.
   - **Text color**: Tokenizer markups (`Markup.KEYWORD2` for `lemma`, `Markup.STRING` for `"P ⟶ P"`, etc.) → syntax highlighting applied.
   - **Output panel**: `rendering.output_messages()` finds `STATE` markup → proof goal `P ⟶ P` displayed in the output dockable.
   - **Errors**: If there were errors, `Markup.BAD`/`Markup.ERROR` would trigger red squiggles.
   - **Goto**: When Ctrl-clicking `P`, `rendering.hyperlink()` finds `ENTITY` markup — resolves `def` serial number to source location of `P`'s definition.
   - **Tooltip**: Hovering over `⟶` shows `Markup.ENTITY` / `Markup.NOTATION` info.

## How `isabelle build` Uses PIDE

The batch build mode lives in [headless.scala](../refs/Isabelle2025/src/Pure/PIDE/headless.scala) (Scala) and [build.ML](../refs/Isabelle2025/src/Pure/Build/build.ML) (ML).

### Headless session

`Headless.Session` extends the standard `Session` class ([session.scala](../refs/Isabelle2025/src/Pure/PIDE/session.scala)) and overrides:
- **Timer settings**: uses `headless_consolidate_delay`, `headless_prune_delay` (typically faster than interactive timers)
- **`use_theories()`**: the main entry point for batch processing

The `use_theories()` algorithm:

1. **Load dependencies**: resolve theory files via `Resources.dependencies()`, building the import DAG
2. **Initialize load state**: `Load_State` tracks which theories are queued, in-progress, or finished, with a `load_limit` for memory-bound scheduling
3. **Send edits**: for each theory to load, read the file, create a `Headless.Resources.Theory`, and send `Document.update` with `Deps` + `Edits` + `Perspective(required=true)` — this makes all commands required (no viewport culling)
4. **Poll for consolidation**: an `Event_Timer` fires at `check_delay` intervals, calling `check_state()`. This checks whether all queued theories have reached consolidated state. The `Document_Status.Nodes_Status` provides per-node progress (percentage of commands finished, OK/failed status)
5. **Commit (optional)**: if a commit callback is provided, it is invoked for each theory as soon as it consolidates (used for document preparation, export extraction, Sledgehammer caching)
6. **Return result**: a `Use_Theories_Result` containing the final state, version, and per-node status

### Build process (ML side)

`build_session` in [build.ML](../refs/Isabelle2025/src/Pure/Build/build.ML):
- Receives a list of `(options, (theory_name, position))` pairs via YXML
- Calls `Thy_Info.use_theories` which internally drives the same document update/execution/assign cycle
- Waits for all theories to be processed
- Calls `Session.finish` for cleanup
- Returns `(rc, errors)` via `Markup.build_session_finished`

The batch path does not use:
- Editor models or pending edits (loads files atomically)
- Caret focus or cursor-based queries
- Viewport-limited perspectives (always `required=true` for all commands)
- Incremental update coalescing timers (`editor_input_delay`, `editor_output_delay`)

But it uses the exact same `Document.update` protocol command, the same `Execution` engine, the same `Document.State` versioning, and the same `Command.eval` evaluation.

## How the GUI Uses PIDE

The interactive GUI path in [session.scala](../refs/Isabelle2025/src/Pure/PIDE/session.scala) adds:

- **Models**: each open editor tab is a `Document.Model`, tracking the node name, current text buffer, and **pending edits** (keystrokes not yet sent to the prover). Pending edits are coalesced by timers (`editor_input_delay` ~0.3s) to batch rapid typing into fewer updates.

- **Perspective**: only the visible viewport is marked `required`; commands scrolled off-screen are garbage-collected and their exec futures cancelled. The perspective is sent as a `Perspective` edit alongside text edits.

- **Markup rendering**: the GUI queries `Snapshot.cumulate()` / `Snapshot.select()` over visible ranges to render syntax highlighting, type info, error squiggles, etc. These queries are read-only on the Scala `State` and do not require prover round-trips.

- **Output panel**: the current caret position determines which command's `RESULT` state is shown. The GUI calls `Snapshot.find_command()` to locate the command at the cursor.

- **Undo/redo**: the `Change` history in `Document.History` enables navigation. Undo creates a new version that truncates the edit history; the prover re-uses cached evals where possible via the "last common" algorithm.

The `Session.manager` thread ([session.scala](../refs/Isabelle2025/src/Pure/PIDE/session.scala)) is the central event loop that dispatches all incoming prover output, manages edit coalescing, triggers consolidation, and prunes old versions.

### Markup querying

The GUI queries markup on the **Scala side only** — no prover round-trip is needed after the assign_update has been processed. The two core query methods are `Snapshot.cumulate` and `Snapshot.select` (see "Markup Query Mechanism" above).

For a detailed trace of how each GUI feature queries markup, see the "Markup and GUI Rendering" section.

## Execution Engine in Detail

The execution engine ([execution.ML](../refs/Isabelle2025/src/Pure/PIDE/execution.ML)) is an async execution system centered on a unique `Document_ID.execution` that acts as a barrier (a synchronization point where all running futures must complete before the next update can proceed):

1. **Discontinue on update**: `Document.update` calls `Execution.discontinue()`, which sets the execution ID to `none`. All running futures that check `Execution.is_running` will find their execution invalidated and skip further work.

2. **Start new execution**: `Document.start_execution` creates a new execution ID via `Execution.start()`, then schedules nodes. `String_Graph.schedule` is a topological scheduler that groups nodes by dependency level and processes each group in dependency order. For each node, it forks a future whose dependency list includes the futures of all import nodes — Poly/ML's future scheduler handles the actual parallelism.

3. **Command-level execution**: within a node, `Command.exec` processes commands sequentially:
   - The eval runs as a function of `(keywords, master_dir, init, blobs, command_id, span, prev_state)`
   - The prev_state comes from the previous command's eval result
   - Print functions are collected and submitted as `Execution.print` — they run as separate futures after the eval completes

4. **Print functions**: `Execution.fork_prints` submits all collected print functions for an exec. Some prints are `parallel_print` (can run in any order), some are sequential. Print functions produce `REPORT` markup that the prover sends back to the frontend as protocol output messages.

5. **Self-pruning**: when a new `Document.update` arrives while previous execution is still running:
   - `Execution.discontinue()` invalidates the old execution ID
   - `Execution.purge` is called on removed exec IDs after the frontend ACKs them
   - Already-completed evals (where `Command.eval_eq` returns true) are re-used in the new version via the "last common" optimization
   - In-flight evals (where `Command.eval_running` returns true) are NOT reused even if the eval is logically identical — the running future might produce stale state

## Garbage Collection

Version GC is an explicit protocol exchange:

1. The Scala side periodically calls `State.remove_versions(retain)`, which prunes the `History` to keep `retain` entries and collects the dropped versions
2. `Prover.remove_versions(old_versions)` sends the list of dropped version IDs to the prover
3. On the ML side, `Document.remove_versions` deletes the versions from the `versions` table and garbage-collects unreferenced commands and blobs
4. The prover ACKs with `Markup.removed_versions`
5. On the Scala side, `State.removed_versions` purges the corresponding entries from `versions`, `execs`, `commands`, `assignments`, and `commands_redirection`

The timing is controlled by `prune_delay` and `prune_size` — in the GUI these default to ~30s and retain
~20 versions; in headless mode they are typically tighter.

## Summary

PIDE's essential insight is that **batch proof checking and interactive editing are the same thing**: a sequence of document edits applied to a versioned DAG of commands, with asynchronous execution and incremental result propagation. The batch path eliminates user input latency, perspective limiting, and markup rendering — but the core protocol (`Document.update` → execution → assign) is identical. Both paths share the same `Document.State`, the same `Execution` engine, the same versioning model, the same markup accumulation, and the same garbage collection.

## Practical Observations (IsarLite)

### Document protocol in a bare ML session

**Resolved**: After fixing the options table and YXML encoding, `Document.update` works correctly in a bare ML session with all three edit variants (Edits, Deps, Perspective). All integration tests pass.

**Alternative batch path**: For batch theory processing without writing a PIDE client, use `isabelle process -l <logic> -e 'use_thy "...";'`. This runs the full ML session with Futures initialized and produces terminal output including proof states. (`-l <logic>` loads a pre-built session heap like `HOL`; `use_thy` is the ML function that synchronously processes a theory file.)

The Scala IDE wraps the protocol loop in a `Session.manager` thread that handles initialization. Headless applications like `isabelle build` use the Scala `Headless.Session` class which extends `Session` and properly manages Futures.

### Wire format details

#### Transport framing

All messages on the wire use a length-prefixed format, defined in
[byte_message.scala](../refs/Isabelle2025/src/Pure/PIDE/byte_message.scala) /
[byte_message.ML](../refs/Isabelle2025/src/Pure/PIDE/byte_message.ML):

```
[header_line: "N1,N2,...\n"][chunk1: N1 bytes][chunk2: N2 bytes]...
```

The header line is a comma-separated list of decimal byte counts, terminated by
newline. Each chunk body follows immediately with the exact byte count specified.

Parsing (`read_message`): `read_line` reads byte-by-byte until `\n` or EOF
(returns `None` on EOF). `parse_header` splits the comma-separated string into a
list of byte counts; a malformed header throws `error("Malformed message header:
...")`. `read_chunk` calls `read_block(stream, n)` which reads in a tight loop
until exactly n bytes are received or EOF.

If a chunk is short (EOF before n bytes): `parse_header`/`read_chunk` throws a
`RuntimeException`. This exception is **not caught** by the `message_output`
thread's try/catch (which only catches `IOException` and `Prover.Malformed`), so
it crashes the reading thread. No retry.

ML-side messages are sent via `Message_Channel`
([message_channel.ML](../refs/Isabelle2025/src/Pure/System/message_channel.ML)),
a dedicated thread with a mailbox that decouples ML code from blocking socket
writes. It uses `write_message_yxml` which measures chunk sizes via
`YXML.body_size` — wire-level byte counts include YXML markup overhead, not raw
text length.

#### Output direction (ML → Python)

The logical chunk structure is:

```
[chunk0=kind, chunk1=props_count, chunk2..N=props, N+1..=body]
```

Each chunk is YXML-encoded (properties are `"name = value"` strings) and
individually length-prefixed on the wire per the transport framing above.

| Chunk index | Content | Encoding |
|-------------|---------|----------|
| 0 | Message kind (e.g. `"STATUS"`, `"protocol"`) | YXML atom |
| 1 | Count of property chunks that follow | YXML-encoded integer |
| 2..1+N | Properties as `"name = value"` strings | YXML-encoded |
| 2+N..end | Body chunks (XML trees) | YXML-encoded |

For `kind == "protocol"` messages, body chunks are kept as opaque byte lists
(`Protocol_Output.chunks`). For all other kinds, body chunks are decoded via
`Symbol.decode_yxml_failsafe` into XML trees.

Crucially, `output()` in [prover.scala](\1)
**splits** each wire-level message into 1+N separate `Prover.Output` messages:
one main message with `Markup.REPORT`/`Markup.NO_REPORT` elements stripped (via
`Protocol_Message.clean_reports`), plus one per report element extracted (via
`Protocol_Message.reports`). A single wire message can produce **multiple**
entries in the session manager's message queue — one per extracted report element.

After `message_output` reads and demuxes the raw chunks:
- `decode_prop(bytes)`: parses YXML-encoded property strings into `(name, value)`
  pairs via `Properties.Eq.parse`
- `decode_xml(bytes)`: parses YXML body chunks into XML trees via
  `Symbol.decode_yxml_failsafe`

#### Input direction (Python → ML)

```
chunk[0] = command_name (raw ASCII bytes, no YXML wrapping)
chunk[1..N] = args (raw bytes, one per argument)
```

Input chunks use the same transport framing (length-prefixed), but chunk content
is raw bytes — not YXML-encoded. The ML side reads via `Byte_Message.read_message`
in `protocol_loop()` ([isabelle_process.ML](\1)).
On `NONE` (EOF): raises `Protocol_Command.STOP 0`. On empty message: logs system
message, continues.

The Scala `command_input` thread in
[prover.scala](\1) writes via
`BufferedOutputStream`. On IOException: logged via `system_output`, thread
terminates.

For `Document.define_command` (confirmed working), the argument structure is:
- `id`: decimal string bytes (e.g. `b"1"`)
- `name`: command name as raw UTF-8 bytes
- `parents`: YXML body of `list(string)` — empty = `""`
- `blobs`: YXML body of `pair(list(variant([])), int)` — empty = `[_node([]), _node([XML.Text("0")])]`
- `toks`: YXML body of `list(pair(int,int))` — each pair = token `(kind_id, symbol_length)`
- `sources`: one raw byte chunk per token, the token source text

**Key bug potential**: the Scala IDE encodes `Document_ID.encode(command.id)` as a `String` which gets YXML-encoded when sent through `Symbol.encode_yxml`. Most YXML encoders just emit raw text for simple strings, so the version ID bytes are the raw decimal string. However, some paths may double-wrap in `<:>`. Verify empirically when porting.

### Protocols that work in bare ML PIDE session

| Command | Status | Notes |
|---------|--------|-------|
| `Prover.echo` | ✅ | Returns `commands_accepted` + `writeln` |
| `Prover.stop` | ✅ | Clean shutdown |
| `Prover.init_session` | ✅ | Initializes session resources |
| `Document.define_command` | ✅ | Registers a single command; requires matching token/source counts |
| `Document.define_commands` | ❌ | Bulk variant fails with protocol command failure |
| `Document.update` | ✅ | Works with all three edit variants (Edits, Deps, Perspective) |
| `Document.discontinue_execution` | ✅ | Works (no response message) |
| `Document.cancel_exec` | ❓ | Seems to work but no visible feedback |

### Token encoding for `Document.define_command`

Each token from the tokenizer is encoded as `(kind_id, symbol_length)`:
- `kind_id`: integer matching `Token.Kind` ordinal (0=COMMAND, 1=KEYWORD, ..., 10=SPACE, ...)
- `symbol_length`: the token's length in Isabelle symbol units (counted via `Symbol.length`)

The token count MUST match the source count exactly — they are zipped on the ML side via `toks ~~ sources`.

For delimited tokens (kind_id 11-18: STRING, ALT_STRING, CARTOUCHE, CONTROL, etc.), the ML side ignores the kind_id and re-tokenizes the source text. For immediate tokens (0-10: COMMAND, KEYWORD, IDENT, etc.), the kind_id is used directly via `immediate_kinds` vector lookup.

For advanced details beyond this overview, see:
- [document.ML](../refs/Isabelle2025/src/Pure/PIDE/document.ML) / [document.scala](../refs/Isabelle2025/src/Pure/PIDE/document.scala) — document model
- [protocol.ML](../refs/Isabelle2025/src/Pure/PIDE/protocol.ML) / [protocol.scala](../refs/Isabelle2025/src/Pure/PIDE/protocol.scala) — message formats
- [execution.ML](../refs/Isabelle2025/src/Pure/PIDE/execution.ML) — execution engine
- [headless.scala](../refs/Isabelle2025/src/Pure/PIDE/headless.scala) — batch build PIDE session
- [session.scala](../refs/Isabelle2025/src/Pure/PIDE/session.scala) — interactive GUI session

### Options mechanism pitfalls

#### 1. `Options.load_default()` is destructive

**File:** `refs/Isabelle2025/src/Pure/System/options.ML:201-206`

```ml
fun load_default () =
  (case getenv "ISABELLE_PROCESS_OPTIONS" of
    "" => ()
  | name =>
      try Bytes.read (Path.explode name)
      |> Option.app (set_default o decode o YXML.parse_body_bytes));
```

`Options.load_default()` **replaces** the global defaults table — does NOT merge.
If `ISABELLE_PROCESS_OPTIONS` contains only 2 entries, the options table shrinks
to 2 and `Options.default()` errors on any missing option (e.g. `threads`).

The Scala client avoids this via `Options.init()` which reads ALL `etc/options`
files from ALL component directories, then `Options.encode` writes the full
~333-option YXML to `ISABELLE_PROCESS_OPTIONS` before starting the prover.

**Fix:** Use `isabelle options -x` to dump full defaults, patch 3 custom options
in-place via `PIDEInteractiveOptions.patch_yxml()` (works on raw YXML tree, not the lossy
`PIDEInteractiveOptions.from_yxml()` which only knows ~100 options).

#### 2. `Document.update` error reporting swallows root cause

**File:** `refs/Isabelle2025/src/Pure/PIDE/protocol_command.ML:39-47`

The handler wraps execution in `Runtime.exn_trace_system` — exceptions from the
command handler produce a generic "Isabelle protocol command failure: Document.update"
without the root cause. To debug, capture prover stderr where the traceback may log.

**Related:** `Document.update` is the only handler using `Future.task_context`
(`protocol.ML:100`), which enrolls the current thread as a worker.

#### 3. `Prover.options` handler does two things

**File:** `refs/Isabelle2025/src/Pure/PIDE/protocol.ML:21-24`

1. `Options.set_default()` — replaces the global defaults table
2. `Isabelle_Process.init_options_interactive()` — sets ML runtime state
   (threads, parallel_proofs, print depth, etc.)

After restoring options via `Prover.options`, the ML runtime must be re-initialized
to pick up the new option values.

#### 4. Snapshot of historical debugging findings (May 2025)

These issues were identified and resolved during early development:

- **Edits YXML encoding** verified correct: round-trip parsing succeeds, variant
  tag format matches ML `tagged()` decoder, `option(encode_int)` produces correct
  YXML for `None`/`Some`.
- **Duplicate options crash**: `isabelle options -x` already includes
  `system_channel_address`/`system_channel_password`. Appending duplicates caused
  `Options.load_default()` to abort with "Duplicate declaration of system option".
  Fix: update existing entries in-place instead of appending.
- **Document.update with Edits**: After all fixes, `Document.update` with `Edits`
  works correctly. All integration tests pass.
- **`Prover.options` after init**: Verified the `_sync_options` call (sending options
  via raw patched YXML) correctly preserves unknown options. The `isabelle build`
  session heap path now works for HOL-dependent theories.
