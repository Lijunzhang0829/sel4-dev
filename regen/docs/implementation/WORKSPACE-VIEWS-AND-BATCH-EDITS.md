# P1 workspace views and P2 atomic proof edits

Implemented 2026-11-17. These changes affect the model-facing document interface
in new application releases. They add no experiment-launch flags, change no
native acceptance rule, and do not hot-update existing frozen experiments.
Arch reuse, deferred audit, publication management and time accounting retain
their existing bindings. P0 diagnostics remain separate. P3 makes document-mode checks mandatory asynchronous and no-change waiting harness-side for new releases; P4 is diagnostic-only and does not change acceptance.

## P1 reading and state deltas

`read_theory` for the assigned theory defaults to `view="summary"`. It retains
the source, current check feedback, imports/context revision, accepted/pending
IDs and contract dispositions. Larger workflow, review, planning advice and
observation bodies become content-bound navigation. Workflow entry statuses,
pending failure IDs and review obligations requiring decisions remain visible.
Current diagnostics and exact source are not abbreviated by this projection.

Use the same interface to retrieve complete content:

```json
{"theory":"Target","view":"full"}
```

Or retrieve one section: `workflow`, `observations`, `planning_review`,
`planning_advice`, or `feedback`:

```json
{"theory":"Target","section":"observations"}
```

Here full means the existing model-facing representation, after the established
diagnostic/evidence projection. It is not a replacement for raw evidence reads.
`read_entry` continues to return exact target-specific statements, contexts,
readiness and retrieval memory. No historical records are deleted.

Responses include a cursor. Pass `since_cursor` from the preceding response of
that view/section for a delta. The cursor binds the whole projected state and
run/theory/view identity, not just source text. A check completing, a review
obligation changing, or an environment revision can therefore produce a delta
without a source edit.

The delta has `changed` top-level values and `removed` top-level keys. Replace
each changed value completely; do not recursively merge old nested objects.
Null is an explicit value. `unchanged=true` means no change in this view.
Unknown/stale cursors or a fresh controller return a replacement snapshot with
`cursor_reset=true`. Cursors and view caches are disposable projections, not
proof evidence or recovery authority. Each view caches only its last snapshot.

The existing `if_source_sha256` remains available for source-only omission.
Do not combine it with `since_cursor`: state deltas and omission of an already
held source have different merge semantics. Source text is not currently sent
as line patches: when a changed document field is returned, it is replaced as
a whole. This avoids introducing patch-offset races in P1.

Compatibility: consumers expecting full histories in the default `observations`
field must request the section or full view. File Planner instructions and tool
schemas advertise the new behavior. Full internal state and journal formats do
not change. The helper review/finish gates still run even when their full records
are not in a default read response.

## P2 atomic multi-entry proof updates

```json
{
  "action":"edit_proofs",
  "expected_hash":"current whole-document source hash",
  "proofs":[
    {"entry_id":"first","proof_body":"by simp"},
    {"entry_id":"second","proof_body":"by blast"}
  ]
}
```

All proof spans are found and validated against the same source snapshot, then
the ordinary hygiene/sketch/review save gate runs once. The operation does not
accept a proof or automatically submit a check. It returns `entry_ids`, the new
`source_sha256`/`expected_hash`, `atomic=true` and `acceptance="draft_only"`.
The existing asynchronous `check_theory` can check the returned IDs and supplies
per-entry results through the existing acceptance path.

Duplicate/unknown IDs, stale versions, invalid proof bodies, reserved markers,
structural fields or any validation failure reject the entire batch. No subset
is saved. During pending checks, every entry is checked against protected
snapshot IDs; one conflict rejects the batch. Disposed contracts cannot receive
new positive candidates. The operation cannot change imports or declarations.
Single-entry `edit_proof` and structural `edit_theory` remain supported.

The shared generic document schema adapter also exposes this action so it does
not reject a tool supported by the shared controller. This changes future
releases of that adapter, not the frozen VERO/Codex baseline experiments.

## Validation and practical limits

Targeted tests cover view navigation/full recovery, same-source state changes,
null/removal/unchanged distinctions, cursor reset, actual controller dispatch,
single-save batches, invalid-second-item rollback, pending protection, schema
validation and disposed-contract rejection. Existing declaration, import,
review, cancellation/recovery, independent-check and publication tests run too.

Offline replay of 37 Object_Views read responses reduced serialized characters
from 5,420,437 to 2,808,625 (48.18%). Original source and current feedback were
preserved and `view=full` matched the original payload. This is a response-size
measurement, not a token-cost or proof-throughput experiment. No model/native
benchmark run was started for this implementation.

Evidence is recorded under
`reports/regen/interaction-overhead-plan-20261116-001/P12-PROJECTION-REPLAY.json`.
The active Refine application manifest and `arch-reuse-v1` /
`declaration-export-deferred-v2` bindings were verified unchanged.

P3 document-mode checks are mandatory asynchronous; wait semantics now wait for at least one terminal task or cancellation;
the legacy timeout field is compatibility-only. Targeted tests cover terminal return, duplicate-ID
rejection and cancellation during an unchanged wait. P4 is diagnostic-only:
regen/tools/audit_helper_use.py reads saved acceptance and independent-check artifacts.
