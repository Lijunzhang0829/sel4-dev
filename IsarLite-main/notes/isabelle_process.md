# `isabelle process` — the Prover Lifecycle and Transport Layer

`isabelle process` is the CLI entry point that launches a Poly/ML process running the Isabelle kernel. It is the **transport bridge** between a Scala/Java frontend and the ML proof engine: it boots Poly/ML, loads a session heap, sets up a TCP socket, and enters the PIDE protocol loop. Every Isabelle tool that talks to the prover — `isabelle build`, the jEdit IDE, the VSCode extension, headless batch sessions — ultimately goes through this same process lifecycle.

## Architecture at a glance

```
┌─────────────────────────────────────────────────────────┐
│ Scala frontend                                          │
│   Isabelle_Process.start()                              │
│   │                                                     │
│   ├─ System_Channel (server socket + password)          │
│   ├─ ML_Process (Poly/ML command line)                  │
│   └─ Prover (message read/write threads)                │
├─────────────────────────────────────────────────────────┤
│                 TCP socket (127.0.0.1:port)             │
├─────────────────────────────────────────────────────────┤
│ Poly/ML process                                         │
│   Isabelle_Process.init()                               │
│   │                                                     │
│   ├─ Socket_IO.open_streams (connect to server)         │
│   ├─ password handshake                                 │
│   ├─ Message_Channel (async output mailbox)             │
│   ├─ output hook redirection (writeln/report/result...) │
│   └─ protocol_loop (read → Protocol_Command.run)        │
└─────────────────────────────────────────────────────────┘
```

## The Scala side: `Isabelle_Process.start()`

Entry point: [isabelle_process.scala](../refs/Isabelle2025/src/Pure/System/isabelle_process.scala)

`Isabelle_Process.start()` is the main factory. It does three things in sequence:

### 1. Create a `System_Channel`

A `System_Channel` ([system_channel.scala](../refs/Isabelle2025/src/Pure/System/system_channel.scala)) is a server socket bound to `127.0.0.1:0` (ephemeral port). It generates a random UUID password. The address and password are injected into the ML process via two system options:

- `system_channel_address` — e.g. `127.0.0.1:45678`
- `system_channel_password` — a UUID string

These are written to a temp file pointed at by `ISABELLE_PROCESS_OPTIONS`, which the ML side reads via `Options.load_default()`.

### 2. Launch `ML_Process`

`ML_Process.apply()` in [ml_process.scala](../refs/Isabelle2025/src/Pure/ML/ml_process.scala) constructs the Poly/ML command line:

```
$POLYML_EXE -q \
  --eval "(PolyML.SaveState.loadHierarchy [\"$HEAP\"]; PolyML.print_depth 0)" \
  --eval "Options.load_default ()" \
  --eval "Resources.init_session_env ()" \
  --eval "Isabelle_Process.init ()"
```

It also writes two temp files:

| Env var | File content |
|---------|-------------|
| `ISABELLE_PROCESS_OPTIONS` | YXML-encoded options including `system_channel_address` and `system_channel_password` |
| `ISABELLE_INIT_SESSION` | YXML-encoded session resources (loaded theories, directories, scala functions) |

The `--eval` chain does:
1. **`loadHierarchy`** — load the Pure session heap (or base heap for other logics)
2. **`Options.load_default()`** — decode options YXML from `ISABELLE_PROCESS_OPTIONS` into a global options table
3. **`Resources.init_session_env()`** — decode session YXML from `ISABELLE_INIT_SESSION`, initializing the theory loader
4. **`Isabelle_Process.init()`** — connect to the system channel and enter the protocol loop

If no session heap is given (bare Poly/ML), the entire `--eval` chain is replaced: the `loadHierarchy` step becomes a set of stub definitions (`fun chapter (_: string) = (); ...`), and the three init steps are replaced by a single `PolyML.print_depth N` expression. No TCP socket or PIDE protocol is set up — the process runs as a raw REPL (see [The bare REPL path](#the-bare-repl-path-no-session-heap) below).

### 3. Create `Prover`

Once the subprocess is running, a `Prover` object ([prover.scala](../refs/Isabelle2025/src/Pure/PIDE/prover.scala)) is created. This spawns several threads:

| Thread | Role |
|--------|------|
| `process_manager` | Reads stderr looking for `STX` (ASCII 0x02) to signal startup; calls `channel.rendezvous()` to accept the TCP connection and authenticate |
| `message_output` | Reads `Byte_Message` frames from the socket input stream, dispatches them as `Prover.Output` or `Prover.Protocol_Output` events |
| `command_input` | Writes `Byte_Message` frames to the socket output stream (protocol commands from frontend to ML) |
| `stdout` / `stderr` reader | Reads the process's stdout/stderr pipes and forwards them as `Prover.Output` events |

## The ML side: `Isabelle_Process.init()`

Entry point: [isabelle_process.ML](../refs/Isabelle2025/src/Pure/System/isabelle_process.ML)

`Isabelle_Process.init()` is the final `--eval` expression. It calls `init_modes`, which reads `system_channel_address` and `system_channel_password` from the options table. If both are non-empty, it enters `init_protocol`; otherwise it falls back to `init_options()` (a bare REPL with no PIDE connection).

### `init_protocol` — the full startup sequence

Wrapped in `Thread_Attributes.uninterruptible` to prevent interruption during critical setup:

1. **SHA1 self-test** — `SHA1.test_samples()` validates the SHA1 implementation. If the test fails, the process aborts immediately with a `Fail` exception before any network I/O occurs.

2. **Send STX on stderr** — `Output.physical_stderr Symbol.STX` signals the Scala `process_manager` that the ML side is alive and initializing.

3. **Connect to the Scala server** — `Socket_IO.open_streams address` opens a TCP connection back to `127.0.0.1:port` (the `System_Channel` server).

4. **Password handshake** — `Byte_Message.write_line out_stream (Bytes.string password)` sends the password over the socket. The Scala `channel.rendezvous()` reads it and verifies it matches.

5. **Create the `Message_Channel`** — a mailbox-based async writer. Messages are enqueued and flushed to the socket by a dedicated thread. This prevents the ML evaluator from blocking on socket I/O.

6. **Redirect output hooks** — the ML `Private_Output` functions are replaced to route all prover output through the socket as protocol messages:

   | Original ML function | Redirected as |
   |---------------------|---------------|
   | `writeln_fn` | Protocol message kind `"writeln"` |
   | `state_fn` | Protocol message kind `"state"` |
   | `information_fn` | Protocol message kind `"information"` |
   | `tracing_fn` | Protocol message kind `"tracing"` (with rate limiting) |
   | `warning_fn` | Protocol message kind `"warning"` |
   | `error_message_fn` | Protocol message kind `"error"` |
   | `report_fn` | Protocol message kind `"report"` |
   | `result_fn` | Protocol message kind `"result"` |
   | `status_fn` | Protocol message kind `"status"` |
   | `system_message_fn` | Protocol message kind `"system"` |
   | `legacy_fn` | Protocol message kind `"legacy"` |
   | `protocol_message_fn` | Protocol message kind `"protocol"` |

   Each redirection captures `Position.thread_data()` to annotate messages with source position, and uses `Markup.serial_properties` to assign unique serial numbers.

7. **Send the `init` message and ML statistics** — `message Markup.initN [] [[XML.Text (Session.welcome ())]]` confirms the session is ready. Immediately after, `ml_statistics()` sends a `"protocol"` message with function `ML_statistics`, carrying the process PID and stats directory path.

8. **Enter the protocol loop** — `protocol_loop()` is a tail-recursive function:
   ```ml
   fun protocol_loop () =
     let
       fun main () =
         (case Byte_Message.read_message in_stream of
           NONE => raise Protocol_Command.STOP 0
         | SOME [] => Output.system_message "Isabelle process: no input"
         | SOME (name :: args) => Protocol_Command.run (Bytes.content name) args);
       ...
     in protocol_loop () end;
   ```
   It reads one `Byte_Message` frame at a time. The first chunk is the command name (e.g. `"Document.update"`, `"Prover.echo"`, `"Prover.stop"`). The remaining chunks are the command arguments. These are dispatched through `Protocol_Command.run`, a registry of named handlers (see [protocol_command.ML](../refs/Isabelle2025/src/Pure/PIDE/protocol_command.ML)).

9. **Shutdown** — when `Protocol_Command.STOP rc` is raised (by `Prover.stop`), the loop exits. Cleanup runs in order: `Future.shutdown()` cancels pending futures, `Execution.reset()` clears the execution state, `Message_Channel.shutdown` drains and stops the output thread, the TCP input and output streams are closed (`BinIO.closeIn` / `BinIO.closeOut`), and `Options.reset_default()` restores the global options table to its defaults.

### The `init_build` variant

`Isabelle_Process.init_build()` is used by `isabelle build`. It skips the `protocol_modes1` print modes (`[Syntax_Trans.no_bracketsN, Syntax_Trans.no_type_bracketsN]`) but otherwise follows the same path. The `protocol_modes2` mode (`[Print_Mode.PIDE]`) is always enabled in protocol mode.

## `Message_Channel` — async output

[message_channel.ML](../refs/Isabelle2025/src/Pure/System/message_channel.ML)

A `Message_Channel` wraps a mailbox and a dedicated thread:

```ml
fun message (Message_Channel {mbox, ...}) name props chunks =
  let
    val kind = XML.Encode.string name;
    val props_length = XML.Encode.int (length props);
    val props_chunks = map (XML.Encode.string o Properties.print_eq) props;
  in Mailbox.send mbox (Message (kind :: props_length :: props_chunks @ chunks)) end;
```

The thread dequeues messages and writes them via `Byte_Message.write_message_yxml` to the socket. This design is critical: without it, protocol output (which the ML evaluator produces during command execution) would block on socket writes, stalling evaluation.

## `Byte_Message` — wire format

Both sides use the same framing protocol defined in [byte_message.ML](../refs/Isabelle2025/src/Pure/PIDE/byte_message.ML) and [byte_message.scala](../refs/Isabelle2025/src/Pure/PIDE/byte_message.scala):

```
<len1>,<len2>,...,<lenN>\n
<chunk1 bytes>
<chunk2 bytes>
...
<chunkN bytes>
```

The header is an ASCII comma-separated list of chunk sizes, terminated by newline. Each chunk follows as raw bytes.

**In the ML-to-Scala direction** (output messages), the chunks follow a fixed structure: the first chunk is the message **kind** (e.g. `"protocol"`, `"writeln"`, `"init"`, `"exit"`), the second chunk is an integer property count, the next N chunks are `"name = value"` property strings, and the remaining chunks are the body payload (YXML-encoded `XML.Body` trees, or raw bytes for protocol messages).

**In the Scala-to-ML direction** (input commands), the structure is simpler: the first chunk is the raw command name (e.g. `"Document.update"`, `"Prover.echo"`), and the remaining chunks are the command arguments as raw bytes. There is no kind marker, property count, or property chunk — the ML `protocol_loop` passes `(name :: args)` directly to `Protocol_Command.run`.

## `Protocol_Command` — the dispatch registry

[protocol_command.ML](../refs/Isabelle2025/src/Pure/PIDE/protocol_command.ML)

All protocol commands are registered in a `Symtab`:

```ml
val commands = Synchronized.var "Protocol_Command.commands"
    (Symtab.empty: (Bytes.T list -> unit) Symtab.table);
```

`Protocol_Command.define` registers a handler; `Protocol_Command.run` looks up the command name and invokes it. The core commands are registered in [protocol.ML](../refs/Isabelle2025/src/Pure/PIDE/protocol.ML):

| Command | Purpose |
|---------|---------|
| `Prover.echo` | Echo arguments back as output (testing) |
| `Prover.stop` | Raise `STOP rc` to exit the protocol loop |
| `Prover.options` | Decode new option values and re-initialize |
| `Prover.init_session` | Initialize session resources from YXML |
| `Document.define_blob` | Register a file blob by SHA1 digest |
| `Document.define_command` | Register a command (ID, name, tokens) |
| `Document.define_commands` | Bulk variant of `define_command` |
| `Document.discontinue_execution` | Invalidate current execution |
| `Document.cancel_exec` | Cancel a specific exec ID |
| `Document.update` | Main edit-update message (see [pide.md](pide.md)) |
| `Document.remove_versions` | GC old document versions |
| `ML_Heap.full_gc` | Trigger full GC |
| `ML_Heap.share_common_data` | Share immutable data to reduce heap |

## Relationship to PIDE

The `isabelle process` interface **is** the PIDE transport layer. The PIDE protocol — document updates, markup reporting, command execution — runs *over* this TCP socket using `Byte_Message` frames and YXML encoding.

The layered relationship:

```
PIDE protocol (Document.update, assign_update, markup REPORT, ...)
    ↑
Protocol_Command dispatch / Protocol_Message output
    ↑
Byte_Message framing (length-prefixed chunks)
    ↑
TCP socket (System_Channel)
    ↑
Poly/ML process                              Scala frontend
Isabelle_Process.init()                      Prover threads
```

The `isabelle process` CLI is the **launcher** and the protocol loop is the **multiplexer**: all PIDE operations (document editing, theory loading, proof checking) arrive as named commands over this single socket, and all prover output (status, results, markup, errors) flows back over the same socket.

## The Python client's reimplementation

In this project, [base.py](../src/isarlite/base.py) reimplements the Scala-side launcher in pure Python. Instead of `Isabelle_Process.start()` + `Prover`, the `ProverProcess` class:

1. Discovers the Isabelle environment via `isabelle getenv` (the `IsabelleEnv` Pydantic model)
2. Creates a Python TCP server socket (analogous to `System_Channel`)
3. Writes options YXML and session init YXML to temp files
4. Constructs the same Poly/ML command line with `--eval` expressions
5. Sets the same environment variables (`ISABELLE_PROCESS_OPTIONS`, `ISABELLE_INIT_SESSION`, etc.)
6. Accepts the prover's TCP connection and reads the password line
7. Reads protocol messages using the same `Byte_Message` framing and YXML parsing

The Python implementation is a single-threaded, synchronous approximation of the Scala `Prover`'s multi-threaded message dispatch. Notably, the Python client does **not** wait for the STX signal before accepting the TCP connection (unlike the Scala `process_manager` which blocks on STX); it accepts the connection and reads the password immediately, relying on the prover to connect promptly. For the full Scala implementation, see [prover.scala](../refs/Isabelle2025/src/Pure/PIDE/prover.scala) and [isabelle_process.scala](../refs/Isabelle2025/src/Pure/System/isabelle_process.scala).

## The bare REPL path (no session heap)

When no session heap is loaded, `isabelle process` runs as a bare Poly/ML REPL:

- `Isabelle_Process.init()` is **not** called (the final `--eval` is just `PolyML.print_depth N`)
- No TCP socket, no PIDE protocol
- Output goes to stdout/stderr directly
- This is conceptually similar to `isabelle console`, a separate tool that also starts a bare Poly/ML REPL, though the two tools go through different code paths (`isabelle console` does not use `ML_Process.apply`)

This path appears in `ML_Process.apply` when `session_heaps` is empty: the stub definitions (`fun chapter`, `fun section`, etc.) and `ML_file = PolyML.use` replace the heap-loaded definitions, and the protocol loop is never entered.
