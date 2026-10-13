# Workspace protocol reader and cancellation repair

Latest integration and validation scope: [application framework](HARNESS-APPLICATION-FRAMEWORK.md).
The limitations below describe this protocol-only repair. The later isolated 122-node
Scheduler_Queues finish and Arch optimization have separate evidence; no repository
completion or real-model acceptance is inferred from either study.

Scope: repair the independently reproduced callback receive blockage and sleep
budget defect. No backend migration, target-proof edit or 015 resumption.
Evidence: `reports/regen/harness-protocol-repair-20260929/`.

## Execution ownership

Callbacks still execute serially on their original thread. This preserves
context variables, signal handling and theory-tool locks. Only protocol reading
and validation run on a callback-local reader thread. Incoming mathematical tool
requests are identity/schema checked and queued (maximum 64), never dispatched
on that reader. The owner joins the reader before sending its result. Preflight
validates already buffered messages before dispatching another callback. An
early terminal before the start response is confirmed is rejected, not dropped.

Pre-finish provenance checks use the callback's recorded, fsynced transcript
offset, not the growing file's current EOF. Otherwise the concurrent reader can
expose a partial final line and spuriously abort sealing. Final terminal audits
still check the whole closed session, including subsequent failure markers.

An unsafe event invokes the session's optional `on_abort` hook exactly once.
`TheoryController.abort_pending` latches cancellation and cancels executor tasks
owned by this run and theory, without waiting on the synchronous tool lock.
Registration races are covered before submit, at execution entry and after
registration. Receipts remain readable. The executor's existing acceptance
guard remains authoritative: a commit accepted before cancellation is retained;
an unaccepted late result cannot be published. Charged request counts do not
roll back. Cleanup and process ownership remain with the callback owner.

Cancellation is cooperative. An arbitrary Python callback that ignores its
hook/token cannot be forcibly interrupted by this reader. The production native
path must observe the cancellation token and recycle its owned resources.

## Sleep and deadline authority

New launches explicitly record `sleep_policy_version: 2`. Sleep display events
still require the correct active owner, unique ID, integer duration 0–60000 ms,
matching completion and no pending sleep at successful turn termination. The
cumulative declared 120000 ms per-turn cap is removed. It incorrectly rejected
four legitimate waits while a trusted native callback owned a longer budget.
These events do not cause a second host sleep or credit proof progress.

The model no-information deadline and repeated-read limits still apply outside
callbacks. Time inside trusted callbacks belongs to existing native/action
deadlines. Sessions without an explicit progress timeout now use their session
timeout to bound passive-event-only loops. Parallel Planner settings (including
900-second no-information budgets) are not enlarged by this repair.

Old unversioned/version-1 transcripts retain the 120000 ms audit rule. Failed
markers remain nonsealable under either version. Terminal events are checked
against pending tools for that turn at their actual position; later responses
cannot repair invalid ordering in the record. No old transcript is relabeled.

## Validation boundaries

Fixed acceptance criteria include a 0.5-second maximum event receipt delay,
complete event capture during a 165-second wall-clock callback, and four real
40-second child sleeps. Those protocol producers are synthetic, not model
calls. Independent tests cover cancellation/acceptance races, serial ordering,
context variables, signals, duplicate identities, queue bounds and audit
mutations. Real Isabelle checks and public-document acceptance are separate
fixtures, with substitutions disclosed in their receipts.

These checks do not establish real-model acceptance, full Scheduler_Queues
completion, two-Planner throughput, or a new Isabelle server's suitability.
The complete-theory cumulative timeout is a separate issue and is unchanged.
