# Verified replay and continuation memory boundaries

## Problem

Repository status and planner feedback can repeatedly traverse every theory's
acceptance history. The histories embed predecessor runs and native evidence;
an initial continuation record can therefore be much larger than its current
proof state. These are local parsing/verification costs, not model transport
timeouts. Increasing the model-event timeout does not address them.

The earlier eight-entry journal cache could thrash with nine active histories.
The shared reader separately cached exact snapshots, so appending feedback
invalidated its entire replay. Whole-value JSON encoding added full Unicode and
UTF-8 copies, concurrent cold readers multiplied the peak, and workspace forks
retained all theories' decoded origins together.

## Invariants of the repair

- Writer and shared reader reuse one process-local verified-prefix cache,
  scoped by journal path, binding and validator namespace. Empty theories do
  not evict evidence-bearing histories. The LRU is bounded at 128 histories.
- Every read checks immutable file identity, size and nanosecond mtime/ctime.
  Changed evidence forces verification; missing observed history is rejected.
  Cold starts still validate the full chain and nested origins. New commits
  validate only the suffix when the prefix and validation environment match.
- Shared reads never repair HEAD or promote catalog/source files to authority.
  Historical references retain their original identity and proof hashes.
  Cached metadata includes only commit/graph identities and audited dependency
  projections, not entire raw records or native logs. A separate 128-entry
  historical-state cache prevents repeatedly decoding the same old origin;
  the current full prefix is still checked before using any historical state.
- A process-wide reentrant gate bounds simultaneous cold record decoding and
  validation. Hot verified reads bypass this gate. Per-journal transaction locks
  remain in place so readers cannot observe temporary link-count/ctime changes
  during immutable publication. This does not serialize native proof workers.
  Saved validator exceptions have unwound frames cleared; JSON decode errors
  discard their full input document while retaining diagnostic coordinates.
- Hashing and durable writing use the same canonical/pretty JSON bytes as
  before, encoded incrementally. Immutable collisions compare exact file bytes
  in chunks. Atomic publication and fsync ordering are retained.
- Workspace continuation preflight fully verifies each selected history and
  retains only a lightweight descriptor. Publication loads and verifies one
  origin at a time, checks it against the descriptor, and preserves proofs,
  charged work, accepted order, drafts and seal identities. Descriptors are
  not substitutes for evidence validation.
- Cached planner actions publish one final progress projection instead of
  three. Target readiness reads only the target and hard providers. Full-slot
  coordinator polling does not rescan the repository, while free slots still
  refresh promptly, including when a provider seals before its session exits.
  Explicit priority-stage barriers and rolling refill semantics are preserved.

## Operational boundary

This is not an on-disk evidence-format migration: nested origins and their disk
size remain unchanged. Cold JSON decoding still builds one record's object tree;
streaming encoding also retains the largest encoded string token. Do not infer
constant total memory or zero startup cost from the warm-cache improvement.
The older single-theory/provider continuation helpers are not converted to the
bounded workspace workflow in this change. The workspace fork trades a second
source read/verification pass for bounded origin lifetime; run it with the source
experiment stopped, since fingerprints are not a repository-wide atomic snapshot.

Validate with cache invalidation/concurrency tests, byte-compatibility tests,
continuation lifetime tests, and a resource-limited read-only replay of paused
evidence. Do not restart a model experiment merely to validate this path.
Existing frozen runs keep their original implementation binding; any later
authorized resume uses the controlled continuation workflow rather than
rewriting those bindings. Preserve interrupted task receipts and cumulative
costs when recording an OOM interruption.
