# Scala Client Design For Resilience — Full Reference

This document describes how the Scala reference IDE client reads, dispatches, and handles
PIDE protocol messages from the Poly/ML prover process (referred to as "ML side" throughout).
It exists so that IsarLite's Python client can replicate the same reliability semantics —
understanding error handling, timeout behavior, progress detection, and non-termination
scenarios — without reverse-engineering the Scala source for each decision.

Source: [session.scala](../refs/Isabelle2025/src/Pure/PIDE/session.scala),
[prover.scala](../refs/Isabelle2025/src/Pure/PIDE/prover.scala),
[protocol.scala](../refs/Isabelle2025/src/Pure/PIDE/protocol.scala),
[command.scala](../refs/Isabelle2025/src/Pure/PIDE/command.scala),
[byte_message.scala](../refs/Isabelle2025/src/Pure/PIDE/byte_message.scala),
[byte_message.ML](../refs/Isabelle2025/src/Pure/PIDE/byte_message.ML),
[consumer_thread.scala](../refs/Isabelle2025/src/Pure/Concurrent/consumer_thread.scala),
[synchronized.scala](../refs/Isabelle2025/src/Pure/Concurrent/synchronized.scala),
[isabelle_thread.scala](../refs/Isabelle2025/src/Pure/Concurrent/isabelle_thread.scala),
[mailbox.scala](../refs/Isabelle2025/src/Pure/Concurrent/mailbox.scala),
[system_channel.scala](../refs/Isabelle2025/src/Pure/System/system_channel.scala),
[message_channel.ML](../refs/Isabelle2025/src/Pure/System/message_channel.ML),
[isabelle_process.ML](../refs/Isabelle2025/src/Pure/System/isabelle_process.ML),
[document.ML](../refs/Isabelle2025/src/Pure/PIDE/document.ML).

## Architecture overview

Four threads, unbounded mailboxes, typed outlets (publish-subscribe channels that fan
state-change notifications to subscribers via the dispatcher thread, without blocking
the manager):

| Thread                  | Role                                                                                                                                                                               |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Session.manager`       | All state mutation serialized through one `Consumer_Thread[Any]` mailbox. Receives from `message_output`, `change_parser`, and API calls. Only thread that touches `global_state`. |
| `Session.dispatcher`    | Fans out messages to outlet subscribers without blocking the manager                                                                                                               |
| `change_parser`         | Parses text edits into `Document.Change` in a separate pipeline. Receives from manager, sends back to manager.                                                                     |
| `Prover.message_output` | Dedicated `Isabelle_Thread` that continuously reads length-prefixed frames from the socket. Pushes into manager's mailbox.                                                         |

Each thread is an `Isabelle_Thread` wrapping a `Consumer_Thread` mailbox or doing blocking I/O. No thread pool is involved at this layer.

### Scala-to-Python concept map

| Scala                                  | Python analog                                                   |
| -------------------------------------- | --------------------------------------------------------------- |
| `Consumer_Thread[A]`                   | `queue.Queue` + worker thread loop                              |
| `Mailbox[A]`                           | `queue.Queue` (unbounded, blocking get)                         |
| `Synchronized[A]`                      | `threading.Lock`-guarded variable                               |
| `Future[A]`                            | `concurrent.futures.Future`                                     |
| `Isabelle_Thread.fork(name)(body)`     | `threading.Thread(target=body, name=name, daemon=True).start()` |
| `Option[A]` / `Some(a)` / `None`       | `A \| None`                                                     |
| `Exn.capture(body)`                    | try/except wrapping result in `Result` ADT                      |
| `Exn.Result[A]` = `Exn.Res \| Exn.Exn` | `Ok(value) \| Err(exception)`                                   |
| `for (x <- list) yield f(x)`           | `[f(x) for x in list]`                                          |
| `case class` pattern match             | structural pattern matching (`match`/`case`)                    |
| `error(msg)` / `ERROR(msg)`            | `raise RuntimeError(msg)`                                       |

## Wire format

The PIDE protocol has two layers: **transport framing** (length-prefixed byte chunks)
carrying **YXML-encoded payloads**. For the full format specification (chunk layout,
input direction encoding, YXML details), see [pide.md](pide.md#wire-format-details).
This section focuses on Scala-side parsing behavior and error handling.

### Transport framing and parsing

```
[header_line: "N1,N2,...\n"][chunk1: N1 bytes][chunk2: N2 bytes]...
```

Parsing ([byte_message.scala](../refs/Isabelle2025/src/Pure/PIDE/byte_message.scala:74-75)):
```scala
def read_message(stream: InputStream): Option[List[Bytes]] =
  read_line(stream).map(line => parse_header(line.text).map(read_chunk(stream, _)))
```

`read_line` reads byte-by-byte until `\n` (0x0A) or EOF. `parse_header` splits the
comma-separated string into a list of byte counts. `read_chunk` calls `read_block(stream, n)`
which reads in a tight loop until exactly n bytes are received or EOF.

**Error behavior**: If a chunk is short (EOF before n bytes), `parse_header`/`read_chunk`
throws via `error()` which is a bare `RuntimeException` (NOT `Prover.Malformed`). This
is **not caught** by the `message_output` try/catch (only catches `IOException` and
`Prover.Malformed`), so it crashes the `message_output` thread. No retry. After the
crash, the manager continues operating but is effectively deaf — no further messages
arrive.

## Prover lifecycle

### Startup sequence

1. `Session.start(start_prover)` sends `Start(start_prover)` to manager
2. Manager calls `start_prover(manager.send(_))` which constructs a `Prover` instance
3. `Prover` spawns two threads:
   - `process_result`: `Future.thread("process_result")` — calls `process.join()`, blocks until OS process exits
   - `process_manager`: `Isabelle_Thread.fork("process_manager")` — orchestrates the entire I/O setup
4. `process_manager` first spawns `stdout = physical_output(false)` to read process stdout, then reads stderr byte-by-byte looking for STX (byte 0x02, the Start-of-Text ASCII code written by ML in [isabelle_process.ML](../refs/Isabelle2025/src/Pure/System/isabelle_process.ML:85) once the Poly/ML process has opened the socket and entered its protocol loop):
   ```scala
   while (finished.isEmpty && (process.stderr.ready || !process_result.is_finished)) {
     while (finished.isEmpty && process.stderr.ready) {
       val c = process.stderr.read
       if (c == 2) finished = Some(true)  // STX received → success
       else result += c.toChar
     }
     Time.seconds(0.05).sleep()
   }
   startup_failed = finished.isEmpty || !finished.get
   ```
   Where `finished: Option[Boolean]` is `None` initially, set to `Some(true)` on STX or `Some(false)` on IOException. `startup_failed = finished.isEmpty || !finished.get` evaluates as:

   | Scenario                  | `finished`    | `startup_failed` | Result                |
   | ------------------------- | ------------- | ---------------- | --------------------- |
   | STX received              | `Some(true)`  | `false`          | Proceed to rendezvous |
   | IOException on stderr     | `Some(false)` | `true`           | Terminate process     |
   | Process exits without STX | `None`        | `true`           | Terminate process     |
5. On startup failure: `terminate_process()` (OS-level kill), then `process_result.join` and `stdout.join` to wait for the process and stdout threads to terminate, then `exit_message(Process_Result.startup_failure)`, exit message sent to manager → phase → Terminated
6. On success: `channel.rendezvous()` performs a password-authenticated handshake over the socket — accepts the TCP connection the ML process opened, reads the password line, verifies it matches the randomly-generated password shared via `ISABELLE_PROCESS_OPTIONS` (see [notes/pide.md](pide.md) for the full options lifecycle), and returns `(OutputStream, InputStream)` for command input and message output. Then spawns three threads:
   - `command_input_init(command_stream)` — writes protocol commands to ML
   - `physical_output(true)` — reads stderr, posts as STDERR messages
   - `message_output(message_stream)` — reads PIDE protocol messages

7. ML side sends `INIT` message. Manager's `handle_output`:
   - Loads `Protocol_Handler` services (extensible service plugins; see "Protocol handlers" section below) via `Isabelle_System.make_services`
   - If loading fails: sends `Prover.stop` with error code, phase stays Inactive
   - If OK: sends options, `init_session`, phase → Ready, posts `debugger.ready()`

### Normal operation

1. Edits arrive via `Session.update()` → manager receives `Raw_Edits`
2. `handle_raw_edits`: calls `prover.get.discontinue_execution()`, creates new version ID, sends `Text_Edits` to `change_parser`
3. `change_parser` calls `resources.parse_change(...)`, sends resulting `Change` back to manager
4. `handle_change`: defines commands, calls `prover.get.update(old_id, new_id, doc_edits, consolidate)` → sends `Document.update` protocol command to ML
5. ML executes commands, sends back status messages and results via `Message_Channel` → socket → `message_output` thread → manager mailbox
6. Manager's `handle_output` dispatches each message (see "Message dispatch" below)

### Shutdown sequence

1. `Session.stop()`:
   - `_phase.guarded_access`: Ready → post_phase(Shutdown), Inactive → post_phase(Terminated(ok))
   - `manager.send(Stop)`
2. Manager processes `Stop`:
   - `consolidation.exit()` — revoke delay, disable further consolidation
   - `delay_prune.revoke()`
   - `if (prover.defined)`: reset `global_state`, call `prover.get.terminate()`
3. `prover.terminate()` ([prover.scala](../refs/Isabelle2025/src/Pure/PIDE/prover.scala:164-174)):
   - Sends "Terminating prover process" to system_output
   - `command_input_close()` — shuts down `command_input` Consumer_Thread
   - Polls `process_result.is_finished` 10×100ms = **1 second max**
   - If still not finished: `terminate_process()` — `process.terminate()` (OS-level destroy)
4. Back in `Session.stop()`:
   - `prover.await_reset()` — blocks until exit message sets prover to None
   - Shuts down: `change_parser`, `change_buffer`, `manager`, `dispatcher`
   - Returns `Process_Result`

### Exit message handling

When `process_result` completes and `process_manager` finishes joining threads, it sends `exit_message(result)` → an `EXIT` message to the manager. Manager's `handle_output`:
- `protocol_handlers.exit()` — call exit() on all protocol handlers
- `end_theory` for all nodes in `global_state.value.theories`, post to `finished_theories`
- `file_formats.stop_session()`
- `phase = Session.Terminated(result)`
- `prover.reset()` — sets prover variable to None, unblocking `await_reset()`

## Message dispatch

The dispatcher operates on these primitives:

- **`global_state`**: `Synchronized[Document.State]` — the entire document model. All mutations go through `.change(f)` (atomic update) or `.change_result(f)` (atomic update returning a value).
- **`change_command(f)`**: Applies `global_state.change_result(f)` scoped to one command's `Command.State`, then fires `change_buffer.invoke` for non-theory commands (triggers IDE update notifications).
- **`change_buffer`**: Accumulates which nodes/commands changed, fires `commands_changed.post(...)` after `output_delay` (debounced batch notification).
- **`delay_prune`**: `Delay.first(prune_delay)` — schedules old-version garbage collection after each `Assign_Update`.
- **`bad_output()`**: Logs a warning if `verbose` is set, otherwise silently drops the message.

### Single `handle_output` method ([session.scala](../refs/Isabelle2025/src/Pure/PIDE/session.scala:459-580))

**First level: Protocol_Output vs plain Output**

Protocol_Output has `kind == "protocol"` with raw `chunks` (opaque byte lists). Plain Output has a markup kind (`STATUS`, `REPORT`, `WRITELN`, etc.) and a parsed XML body.

**Protocol_Output path:**

1. `protocol_handlers.invoke(msg)` — extensible handler registry (e.g., Debugger, Simplifier_Trace). Returns `true` if handled.
2. If unhandled, pattern match on properties:
   - `Command_Timing` → post to `command_timings` outlet, accumulate timing markup in command state
   - `Theory_Timing` → post to `theory_timings` outlet
   - `Task_Statistics` → post to `task_statistics` outlet
   - `Export` → accumulate export entry in command state via `change_command`
   - `Loading_Theory` → `global_state.change(_.begin_theory(...))`
   - `Commands_Accepted` → accumulate ACCEPTED status in each command via `change_command`
   - `Assign_Update` → `global_state.change_result(_.assign(...))`, fire `change_buffer.invoke`, send `Change_Flush` to manager, invoke `delay_prune`
   - `Removed_Versions` → `global_state.change(_.removed_versions(...))`, send `Change_Flush`
   - default → `bad_output()` (logged only if `verbose`)
3. Errors in `change_command` or `global_state.change_result` catch `Document.State.Fail` → `bad_output()`

**Plain Output path:**

1. Has `Position.Id(state_id)` → `change_command(_.accumulate(state_id, output.message, cache))`
2. `is_init` (message kind == `Markup.INIT`, no command position) → init protocol handlers, send options, `init_session`, phase → Ready
3. `is_exit` (message kind == `Markup.EXIT` with `Markup.Process_Result` property) → exit handlers, end theories, phase → Terminated, `prover.reset()`
4. Otherwise → `raw_output_messages.post(output)`

### Manager main loop ([session.scala](../refs/Isabelle2025/src/Pure/PIDE/session.scala:586-681))

All messages arrive at a single `Consumer_Thread[Any]`. The message types are:

| Message         | Source                | Action                                                                                                             |
| --------------- | --------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `Prover.Output` | message_output thread | syslog → `syslog_messages`; stdout/stderr → `raw_output_messages`; else → `handle_output`; always → `all_messages` |

> **Important:** stderr matches both `is_syslog` (since `is_syslog = is_init || is_exit || is_system || is_stderr`) and the stdout/stderr check, so stderr messages go to **both** `syslog_messages` and `raw_output_messages`. A Python client subscribing to both outlets would see stderr messages twice.
| `Prover.Input` | protocol_command_args | `all_messages.post(input)` |
| `Start` | Session.start | `prover.set(start_prover(manager.send))` |
| `Stop` | Session.stop | consolidation.exit, prune revoke, reset state, prover.terminate |
| `Get_State` | get_state() | fulfill promise with current global_state |
| `Consolidate_Execution` | consolidation delay | find nodes ready for consolidation, send `handle_raw_edits(consolidate=...)` |
| `Prune_History` | prune delay | remove old versions, send `prover.remove_versions` |
| `Update_Options` | update_options() | send new options to prover, `handle_raw_edits()` to trigger re-execution |
| `Cancel_Exec` | cancel_exec() | `prover.get.cancel_exec(exec_id)` |
| `Raw_Edits` | Session.update() | `handle_raw_edits` |
| `Dialog_Result` | dialog_result() | forward to prover, re-post as Output |
| `Protocol_Command_Raw` | protocol_command_raw() | forward to prover |
| `Protocol_Command_Args` | protocol_command_args() | forward to prover |
| `Change` | change_parser | `handle_change` (or postpone if version not yet assigned) |
| `Change_Flush` | Assign_Update handling | flush postponed changes |
| anything else | — | log warning if verbose, drop |

## Threading details

### Consumer_Thread ([consumer_thread.scala](../refs/Isabelle2025/src/Pure/Concurrent/consumer_thread.scala))

Each Consumer_Thread wraps:
- An `Isabelle_Thread` with a `Mailbox[Option[Request]]`
- A main loop that calls `mailbox.receive()` (blocking) then `process(msgs)`
- `process` calls the `consume` function, stores results in `Request.ack` (for `send_wait`), logs exceptions (for `send`)

Exception handling for `send()` (fire-and-forget, used by message_output → manager):
```scala
case (None, Exn.Exn(exn)) => failure(exn)  // logged, thread continues
```

Exception handling for `send_wait()` (used by Session.update, get_state):
```scala
case (Some(a), _) => a.change(_ => Some(res))  // caller receives the exception
```

**Key guarantee**: An exception in one message handler never kills the manager thread. It's logged and processing continues.

### dispatcher thread

Created via `Consumer_Thread.fork[() => Unit]("Session.dispatcher", daemon = true) { case e => e(); true }`. Simply executes thunks. `Outlet.post(a)` wraps consumer callbacks in `consume_robust` and sends them to the dispatcher:

```scala
def post(a: A): Unit = {
  for (c <- consumers.value.iterator)
    dispatcher.send(() => c.consume_robust(a))
}
```

`consume_robust` catches Throwable per-consumer. A crashing consumer never affects other consumers or the dispatcher.

### change_parser thread

`Consumer_Thread.fork[Text_Edits]("change_parser", daemon = true)`. Calls `resources.parse_change(...)` which can throw. Consumer_Thread catches via `Exn.capture` → exception logged, thread continues.

### Postponed changes

If a `Change` arrives before `Document. assign` has processed the corresponding `Assign_Update`, the change is stored in `postponed_changes` (a list). When `Change_Flush` fires (triggered by `Assign_Update`), `postponed_changes.flush(state)` partitions stored changes by whether their `previous` version is now assigned, and processes the assigned ones in order.

## Status markers and progress detection

Commands communicate progress via status markup in `STATUS` messages (the markup names below appear as properties within `STATUS`; in Scala source they are referenced as `Markup.RUNNING`, `Markup.FINISHED`, etc.). `Command.State` in [command.scala](../refs/Isabelle2025/src/Pure/PIDE/command.scala:203-241) tracks:

| Marker          | Meaning                                                 | Source                                                                                     |
| --------------- | ------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `RUNNING`       | Command is executing                                    | `Execution.fork`                                                                           |
| `FINISHED`      | Command completed                                       | After eval finishes                                                                        |
| `FAILED`        | Command failed (parse error, type error, proof failure) | Exception path                                                                             |
| `FORKED`        | Execution forked sub-tasks                              | `Execution.fork`                                                                           |
| `JOINED`        | Sub-tasks joined                                        | After fork joins                                                                           |
| `INITIALIZED`   | Theory header processed                                 | After `Thy_Info.begin_theory`                                                              |
| `CONSOLIDATING` | Node entering consolidation                             | `print_consolidation` in [document.ML](../refs/Isabelle2025/src/Pure/PIDE/document.ML:788) |
| `CONSOLIDATED`  | Node fully processed (presentation + exports done)      | After `Thy_Info.apply_presentation`                                                        |

`Command.State.maybe_consolidated` ([command.scala](../refs/Isabelle2025/src/Pure/PIDE/command.scala:214-228)):
```scala
touched && forks == 0 && runs == 0
```
Tracks FORKED/JOINED/RUNNING/FINISHED to determine when all execution has settled.

`Command.State.document_status` delegates to `Document_Status.Command_Status` which categorizes:
- `is_running`: `RUNNING` present without `FINISHED`/`FAILED` (includes forked state: `is_running` remains true while `forks > 0`)
- `is_failed`: `FAILED` present
- `is_finished`: `FINISHED` present without `RUNNING`
- `is_unexecuted`: none of `RUNNING`, `FINISHED`, or `FAILED` present
- `is_unprocessed`: `is_unexecuted` and not `INITIALIZED`

## Consolidation

After all commands in a node finish execution, the `consolidation` delay fires, sending `Consolidate_Execution` to the manager. The manager:
1. Gets the stable tip version from `global_state`
2. Finds nodes that: are descendants of changed nodes, are not loaded theories (loaded theories are base sessions like Pure/HOL that were pre-built and imported — consolidation is meaningless for imported heaps with no source file edits), are not already consolidated, and `node_maybe_consolidated(version, name)` (all commands `maybe_consolidated`)
3. Sends `handle_raw_edits(consolidate = [node_names])` — a special edit with no text changes, just the consolidate flag

ML side: `Document.update` with `consolidate` names triggers `print_consolidation` which runs `Thy_Info.apply_presentation` (generates HTML/PDF output, export artifacts), then marks the node `CONSOLIDATED`.

"Finished" = eval done. "Consolidated" = presentation + exports complete.

## Error handling catalog

### I/O errors

| Location                                                                     | Error                            | Behavior                                                                                                                                             |
| ---------------------------------------------------------------------------- | -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `message_output` — `Byte_Message.read_message` (socket read)                 | IOException                      | Caught → `system_output` error → close stream → thread exits                                                                                         |
| `message_output` — `Byte_Message.read_message` → `parse_header`/`read_chunk` | `Exn.User_Error` (via `error()`) | NOT caught (`catch` only matches `IOException` and `Prover.Malformed`) → crashes `message_output` thread, skips `stream.close()` and termination log |
| `message_output` — `Prover.Malformed` (bad chunk count)                      | Malformed                        | Caught → `system_output` error → close stream → thread exits                                                                                         |
| `message_output` — `decode_prop`/`decode_xml`                                | Any exception                    | NOT caught locally → crashes `message_output` thread                                                                                                 |
| `command_input` — write to socket                                            | IOException                      | Caught → `system_output` error → `consume` returns `false` → thread terminates                                                                       |
| `physical_output` (stdout/stderr reader)                                     | IOException                      | Caught → `system_output` error → thread exits                                                                                                        |
| `process_manager` — stderr read during startup                               | IOException                      | `finished = Some(false)` → startup_failed → terminate process                                                                                        |

### Message processing errors

| Location                                       | Error                 | Behavior                                                                             |
| ---------------------------------------------- | --------------------- | ------------------------------------------------------------------------------------ |
| `handle_output` → `change_command`             | `Document.State.Fail` | Caught → `bad_output()` (log if verbose)                                             |
| `handle_output` → `global_state.change`        | `Document.State.Fail` | Caught → `bad_output()`                                                              |
| `handle_output` → protocol handler `.invoke()` | Any exception         | NOT caught by `handle_output`; caught by Consumer_Thread → logged, manager continues |
| `handle_change` → `global_state.change`        | Any exception         | NOT caught; caught by Consumer_Thread → logged, manager continues                    |
| `change_parser` → `parse_change`               | Any exception         | Caught by Consumer_Thread via `Exn.capture` → logged, thread continues               |
| Consumer callback (outlet subscriber)          | Any `Throwable`       | Caught by `consume_robust` → logged per-consumer                                     |

### Startup errors

| Location                                        | Error                  | Behavior                                                                                       |
| ----------------------------------------------- | ---------------------- | ---------------------------------------------------------------------------------------------- |
| `process_manager` — startup stderr              | Startup errors text    | Forwarded as `system_output` (not fatal by itself)                                             |
| `process_manager` — no STX received             | Missing startup signal | `startup_failed = true` → terminate process → `exit_message(Process_Result.startup_failure)`   |
| `handle_output` — init protocol handler loading | Throwable              | Sends `Prover.stop` with error code 1 to ML side, sets `init_ok = false`, phase stays Inactive |
| `channel.rendezvous()` — bad password           | Wrong password         | `error("Failed to connect system channel: bad password")` → crashes `process_manager`          |

### Shutdown errors

| Location                                     | Error       | Behavior                                         |
| -------------------------------------------- | ----------- | ------------------------------------------------ |
| `prover.terminate()` — `process.terminate()` | `ERROR(_)`  | Caught → `system_output` with error message      |
| `Session.stop()` — bad phase after shutdown  | Wrong phase | `error("Bad session phase after shutdown: ...")` |

## Timeout, retry, and non-termination

### No timeouts exist

There are **no timeouts** anywhere in the read/process pipeline:

- Socket reads in `message_output` block forever on `InputStream.read`
- Manager and dispatcher mailboxes block forever on `mailbox.receive()` (no timeout parameter passed)
- `process_result` blocks forever on `process.join()` (waits for OS process)
- ML-side command execution has no built-in timeout

The system is designed for an interactive IDE where the user is the timeout mechanism.

### No retries exist

There is **no retry logic** anywhere:
- If I/O fails, the thread logs the error and exits
- If a message is malformed, it's dropped (`bad_output` or `Malformed` exception)
- If `Document.State.Fail` is thrown, the message is dropped
- If `parse_change` throws, the parse is abandoned

### Non-termination scenarios

**1. ML infinite loop / deadlock**: Command gets `RUNNING` status, never `FINISHED`. The IDE highlights the command as "processing." No watchdog. The user must cancel by editing (which sends `Document.discontinue_execution`) or terminating the process.

**2. ML process hangs without closing socket**: `message_output` blocks forever on `InputStream.read`. Since there's no timeout, the thread hangs. The manager stays alive but idle. `process_result` also hangs (process didn't exit). Only OS-level kill resolves this.

**3. ML process sends partial message then hangs**: Distinct from scenario 2 — here the ML side sends a valid header line (e.g. `"3,1,50\n"`) but then delivers fewer bytes than promised before stalling. `read_chunk` calls `read_block(stream, n)` which reads in a tight loop until exactly n bytes are received. `read_block` blocks forever on the missing N-M bytes. The `message_output` thread is stuck — it has already consumed the header (committed to reading N bytes) and can't recover or read the next message until those bytes arrive.

**4. Manager mailbox unbounded growth**: Senders use `send()` (non-blocking). If the manager can't keep up, the mailbox list grows without bound. The mailbox has no size limit (`limit = 0`).

**5. `parse_change` hangs**: `change_parser` thread blocks. New edits queue up in its mailbox. Manager continues operating on stale state.

### The only "timeout" mechanisms

- `prover.terminate()` waits 10×100ms = **1 second** for `process_result` before force-killing
- `await_stable_snapshot()` polls with `output_delay.sleep()` between attempts — but this is indefinite, not a timeout
- `Delay.first`: leading-edge debounce — the first `invoke()` schedules the deferred action; subsequent calls within the delay window are ignored until the action fires. Used for `output_delay`, `consolidate_delay`, `prune_delay`, `input_delay`. Not a timeout, a batching mechanism
- `Mailbox.receive(timeout)` is supported by the infrastructure but **not used** by any session thread

## Phase state machine

```
Inactive ──[Start]──→ Startup ──[init ok]──→ Ready ──[Stop]──→ Shutdown ──[exit msg]──→ Terminated
                         │                      │
                         └──[init fail]──→ Terminated (failed)
                                              └──[process crash / OS exit]──→ Terminated (crashed)
```

Two paths from Ready to Terminated: (1) orderly `Stop` → `Shutdown` → `Terminated(result)`, or (2) process dies unexpectedly (OS-level exit, crash) → `exit_message` sent by `process_manager` → `Terminated(result)` directly (bypassing Shutdown phase).

- `Inactive`: stable, initial state
- `Startup`: transient, prover launching
- `Ready`: metastable, normal operation
- `Shutdown`: transient, stopping
- `Terminated(result)`: stable, session ended

Phase changes are posted to `phase_changed` outlet. `Session.is_ready` checks `phase == Session.Ready`.

## Outlets (publish-subscribe)

All outlets are typed. Posting to an outlet sends consumer callbacks to the `dispatcher` thread:

```scala
class Outlet[A](dispatcher: Consumer_Thread[() => Unit]) {
  def post(a: A): Unit =
    for (c <- consumers.value.iterator)
      dispatcher.send(() => c.consume_robust(a))
}
```

Outlets:
- `finished_theories` — `Document.Snapshot` when a theory node finishes
- `command_timings` — `Command_Timing` from protocol
- `theory_timings` — `Theory_Timing` from protocol
- `runtime_statistics` — `Runtime_Statistics`
- `task_statistics` — `Task_Statistics`
- `global_options` — `Global_Options` on option updates
- `caret_focus` — `Caret_Focus`
- `raw_edits` — `Raw_Edits` when raw edits arrive
- `commands_changed` — `Commands_Changed` (debounced via `output_delay`)
- `phase_changed` — `Phase` on every phase transition
- `syslog_messages` — syslog `Prover.Output`
- `raw_output_messages` — non-command stdout/stderr
- `trace_events` — `Simplifier_Trace.Event`
- `debugger_updates` — `Debugger.Update`
- `all_messages` — **all** `Prover.Message` (Input + Output); documented as "potential bottleneck"

## Pending changes buffering

`change_buffer` accumulates which nodes/commands changed and fires `commands_changed.post(...)` once per `output_delay` period. This debounces the IDE update — N rapid command state changes produce 1 `commands_changed` notification.

Note: `change_buffer.flush()` also calls `consolidation.update(more_nodes = nodes)`, so consolidation checks are rate-limited by `output_delay` as well as `consolidate_delay`. Rapid edits can indirectly delay consolidation.

## Protocol handlers

Extensible via `Session.Protocol_Handler` service class. Each handler:
- `init(session)`: called when session initializes
- `exit()`: called when session terminates
- `functions: Protocol_Functions`: list of `(name, Protocol_Output => Boolean)` pairs
- `prover_options(options)`: can modify options before sending to prover

Built-in handlers include: `Debugger.Handler`, `Simplifier_Trace.Handler`. The `Debugger` handler is always initialized ([session.scala](../refs/Isabelle2025/src/Pure/PIDE/session.scala:375-378)) even for headless/CLI use — it handles breakpoint protocol messages. A CLI-only client could safely omit it if breakpoint support is not needed.

## Implications for IsarLite's Python client

1. **Status markers are the source of truth.** `RUNNING`/`FINISHED`/`FAILED` markers in STATUS messages track progress. No timeout heuristic is as reliable.

2. **The Scala client never conflates I/O with semantics.** `Prover` does raw socket I/O. `Session` does state management. `Command.State` does message accumulation. Three layers with clear seams.

3. **No timeouts means a CLI needs its own.** For `state-at` and similar operations, implement a configurable timeout. Poll for `CONSOLIDATED` on the target node rather than using a "quiet period" heuristic.

4. **Error handling is drop-and-log.** The Scala client never retries; it drops bad messages and logs them. A CLI should do the same but surface errors to the user.

5. **Consolidation is a separate pass.** After all commands finish, consolidation runs presentation. For `state-at`, a naive approach might watch for `Assign_Update` messages (which signal that command execution results have been assigned to document versions) and assume commands are ready. But `Assign_Update` fires before consolidation (presentation + exports generation), so proof state (emitted as `Markup.STATE` / `Markup.INFORMATION` messages by the prover's print functions) may not yet be available. Wait for `CONSOLIDATED` on the target node (or at minimum all commands `FINISHED`) before reading proof state.

## Uncertainties (<90% confidence)

Resolved during review (now >90%):
- `Future.thread` exception capture: verified — body wrapped in `Exn.capture`, exceptions captured in Future.
- `Delay.first` semantics: verified — leading-edge debounce, subsequent `invoke()` calls within delay window are ignored.
- `message_output` thread crash on `decode_prop`/`decode_xml`: confirmed — thread dies immediately, `join()` returns right away, no hang. Resource leak on the accepted-socket InputStream (`stream.close()` is skipped), though the server socket is already closed by `rendezvous()`'s `finally` block.

Remaining uncertainties:

1. **`Event_Timer.future` behavior in `start_execution`**: Used to schedule a delay-dependent execution start. Exact priority/interaction with `Future.forks` not verified.

2. **Thread safety of `decode_prop`/`decode_xml` in message_output**: These callbacks run on the `message_output` thread and access `XML.Cache`. The cache is documented as thread-safe, but not independently verified.

3. **Whether `process.stderr.ready` can block**: `InputStream.ready()` is documented as non-blocking but implementations may vary. In the startup loop, it's used in a tight 50ms polling loop so a brief block would be harmless.

4. **`Protocol_Message.clean_reports` behavior**: Filters XML elements by **markup name** — removes `Markup.REPORT` and `Markup.NO_REPORT` elements from the body, returning a cleaned tree. Reports are then separately extracted by `Protocol_Message.reports(props, body)` and posted as individual `Prover.Output` messages alongside the main output. Verified from [protocol_message.scala](../refs/Isabelle2025/src/Pure/PIDE/protocol_message.scala). The splitting into multiple outputs means one wire-level message can produce 1+N manager mailbox entries.

5. **`resources.parse_change` exception in change_parser**: Consumer_Thread catches it, but if the `Text_Edits.version_result` promise is not fulfilled, the version Future would remain unresolved. This could block downstream operations waiting for that version.
