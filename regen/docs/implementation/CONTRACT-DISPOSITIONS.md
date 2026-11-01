# Contract dispositions and partial theory exports

`contract-dispositions-v1` lets a checked refutation, or an explicit UNKNOWN
report, end one fixed contract without ending its theory. It is opt-in at run
initialization and is frozen in `run.json` and acceptance journal bindings.
Missing policy preserves the original whole-theory stop behavior.

```sh
python3 -m regen.execution.proof_session_runner init \
  --dataset-root DATASET --schedule PLAN/file-generation-list.json \
  --run-dir NEW_RUN --run-id NEW_ID \
  --contract-outcome-policy contract-dispositions-v1
```

Use the document `parallel-workspace` execution path. The intervaltree deployment
launcher enables this policy for new runs. Existing run directories are never
edited to switch policies.

## Workflow and authority

1. `theory_outcomes.py` appends immutable per-contract dispositions. Refutation
   requires the native checker to prove the exact closed negation; UNKNOWN is a
   report about this search, not a theorem about falsity or unprovability.
   Model reports must identify their contracts explicitly. Neither disposition
   adds a positive theorem or resets attempts, time, or accepted proofs.
2. `parallel_file_planner.py` continues pending contracts, exposes dispositions
   in tool feedback, and rejects attempts to prove a disposed contract again.
   A no-progress stop disposes one pending contract at a time. Retries of
   terminal UNKNOWN outcomes are not implicit; `retry_unresolved` is rejected
   under this policy until a separate retry-event protocol exists.
3. `batch_file_planner.py` checks an internal projection of the original draft.
   Disposed entries are `oops`, so they introduce no fact; their stale failed
   proof bodies cannot block independent candidates. The original model request
   and source hash are still retained for provenance. Fixed statements and
   logical assumptions are not removed or weakened.
4. Once every fixed contract is proved or disposed, `finish` may produce
   `exported_partial`. `partial_theory_export.py` builds the positive-only
   projection; native full checking, dependency audit and an isolated session
   build certify it. Unproved helpers are not exported. An empty positive
   projection is permitted without inventing a dummy theorem.
5. v4 evidence binds the entire original package and proof state, all omitted
   contracts' disposition receipts, and the exact exported subset. The journal
   also checks that the receipts match its durable disposition ledger. A v4
   partial export cannot be relabelled as a v3 complete seal.
6. `repository_execution.py` rebuilds the fact catalog from checked exports.
   Consumers may retain their original theory imports, but can only cite the
   published positive facts. Their immutable imported source and native audit
   remain authoritative. Failed references must be repaired or reported as
   unknown; dropping a planning edge does not prove a goal.
7. After the worker closes, disposable heaps may be reclaimed for either full
   or partial exports. Partial reclamation requires v4 evidence and a successful
   acceptance-journal replay; proof evidence, dispositions and published sources
   are retained.

## Completion and reporting

| State or result | Meaning |
| --- | --- |
| `sealed` | All active nodes proved and complete theory audited/built |
| `exported_partial` | All fixed contracts disposed or proved; only proved facts exported |
| `unpublished` session | Contract work has stopped, but no checked export is available |
| `refuted` contract | Exact negation certified by the native checker |
| `unknown` contract | This search stopped without a mathematical decision |

Explicit stages can advance after partial export or after all fixed contracts
have terminal dispositions. Hard consumers still require checked exports; a
stage advancing does not grant them facts. Repository integration continues to
require every fixed contract to be proved. `repository_verified` must remain
false for a repository containing a partial theory.
The `parallel-workspace` CLI still exits nonzero for a partial result; its success
exit code continues to mean complete selected proofs or repository verification.
Consumers of this CLI should inspect the structured status to distinguish
`selected_theories_exported` from an infrastructure failure.

Progress keeps the full fixed ID list and separately reports checked, exported,
fully sealed, refuted, unknown and pending contracts. The generic activity
observer records theory status so a partial `seal` event is not counted as all
contracts GREEN.

## Recovery and current limits

Same-run restart replays contract receipts, positive commits, export certificates
and costs without repeating completed checks. Old v3 full seals retain their
original validation path. A disposed but unpublished earlier stage remains
eligible for a publication attempt on explicit resume; a failed publication is
not automatically retried in a loop within the same coordinator invocation.

Cross-run fork/continuation of a run containing contract dispositions is
explicitly rejected before creating the destination. Rebinding partial export
receipts and their consumers needs a separate versioned transfer protocol; the
old full-seal transfer must not silently discard dispositions. Start a fresh
experiment, or resume the unchanged run. The old graph and standalone workspace
entry points do not implement this policy and reject it.

## Verification

Unit regressions cover per-contract recovery, immutable claims, rejected positive
proofs for disposed IDs, no-progress isolation, stage transitions, publication
and accounting, journal reconstruction, and forged export evidence. The native
smoke tool is `regen.tools.native_partial_export_smoke`: it checks A/C positive
exports, B's separate refutation, downstream references, empty exports and
tampering. Its hand-authored fixtures are infrastructure evidence, never model
generation scores.

The intervaltree live preflight uses two model-generated positive goals and a
disclosed native replay of one known negative case. It exercises the production
API, partial publication and dependent scheduling; it does not establish full
91-target success, model counterexample discovery, or long-run reliability.

### Validation on 2026-11-01

- Final focused regression: 285 tests, 280 passed, 5 native opt-in skips.
- Independent native smoke: 17 checks passed, including actual positive-fact
  imports, rejected refuted-fact imports and an empty positive export.
- Astra/low production API preflight: both positive goals accepted, the known
  negative case remained separately refuted, and the dependent theory started
  automatically. Generation wall time was 247.93 seconds within 600 seconds.
  Fresh-process recovery passed 15 checks; transcript/publication audit passed 11.
- The live test exposed a partial-export heap-reclamation compatibility warning.
  Its subsequent fix was independently checked against that completed run:
  291,287,992 cache bytes reclaimed, 775 original non-cache files unchanged,
  accepted provider/consumer states unchanged, repeated cleanup returned absent.

Local evidence: [live report](../../../intervaltree/build/contract-isolation-live-20261101-001/REPORT.md),
[native smoke](../../../intervaltree/build/partial-export-native-20261101-001/report.json),
[post-run cleanup](../../../intervaltree/build/contract-isolation-heap-cleanup-20261101-001/report.json).
These build artifacts are not committed; the smoke tool and regression tests are.
