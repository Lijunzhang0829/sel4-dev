# Read-only loop / clock.sleep compatibility recovery

Historical record: the cumulative sleep policy described here is version 1.
For the 2026-09-29 repair and versioned compatibility rules, see
[workspace protocol repair](WORKSPACE-PROTOCOL-REPAIR-20260929.md).

Authorized by user: pause experiment, fix both issues, then resume. Run 009 was
stopped via its receipt-bound service on 2026-09-28 at 02:09 UTC. Its coordinator,
app-server and native containers exited; no nonterminal check task remained.
All old runs/transcripts remain untouched. This does not reclassify old failed
sessions as successful.

## Fixes

- Parallel File Planner session carries a progress watch across turns. A new
  tool response fingerprint counts as information. A real check/submission/seal
  or symbol-check attempt counts as work even if the checker returns RED.
- Two consecutive turns with no new information/work stop that theory. Eight
  consecutive duplicate tool results also stop a loop within one turn. Protocol
  messages and sleep do not count as work. A progress deadline equal to the
  existing call timeout (600 seconds here) bounds message-only stalls. Time in
  synchronous trusted callbacks is excluded; their own native deadlines apply.
- Continuation instructions no longer repeatedly direct the model to reread
  unchanged state. No target-specific proof hints were added.
- Runtime and transcript audit share clock.sleep display-item validation:
  exact id/type/duration fields, current/known active thread and turn, integer
  duration 0..60000 ms, at most 120000 ms requested per turn, matching unique
  start/completion, and no unfinished sleep at successful turn completion.
  The harness does not execute a second sleep and does not enable shell tools.
- Unknown built-ins, malformed/unbound sleep, approval requests and failed
  provenance still fail closed. Old failed transcript suffixes are not erased.

Protocol source: offline schema exported by the frozen Codex
0.155.0-alpha.16.3 executable. Its SleepThreadItem is an interruptible clock.sleep
display event. Both real failed events (50 and 60 seconds) satisfy that schema.
Official lifecycle reference: https://learn.chatgpt.com/docs/app-server .

## Recovery

New run 010 is forked from verified run 009 using the existing harness. Exactly
three production files change: codex_workspace_session.py,
workspace_transcript.py, parallel_file_planner.py. Model, client executable,
concurrency (two), checker limits and charged proof budgets remain unchanged.
All 27 theories are selected again, including the theories affected by these
two repaired issues; no automatic service restart or failed-session retry loop
is enabled. Accepted proofs, graphs, request/history/cost fields, seals and
unsealed drafts are compared before launch.

Diagnostics and recovery validation: `reports/regen/progress-sleep-recovery-20260928/`
and the new run's `recovery-verification.json`. Unit tests use synthetic local
protocol servers, not external model calls; live launch must be checked separately.

Final regression: 853 tests, zero failures, four skipped (43.095 seconds).
The stopped source contains 118 accepted contracts, 188 helpers and six sealed
theories. Test log is preserved as `full-tests.log` in the diagnostics directory.

Live recovery verification completed at 02:30:33 UTC. Run
`repository-theory-document-astra-20260928-010` was launched at 02:31:04 UTC using
`regen-proof-46f59b7842fe0f4b03ffc35d.service`, PID 361434, invocation
`21393b99011d4629b55f5fcd67a3efc5`. Its policy selects all 27 theories and at most
two Planners. Service launch is not proof completion; subsequent accepted work
must be read from the new run's immutable commits.

Offline replay of the historical repeated-read responses triggered the new
watch after nine reads (one novel result, eight duplicates). Both original
sleep-start messages now validate; their historical failed sessions remain failed.

At 02:43:07 UTC, both Scheduler_Queues and Capability_Access had one real
app-server launch, thread/start, turn/start and validated tool_started callback
in the new run, with zero session_failed events. Both first callbacks were still
reading recovered state (tool_finished count zero). This establishes live dual
Planner resumption, not new accepted proofs or live reproduction of clock.sleep.
Translation_Lookup remains selected and waits for a scheduler slot.
