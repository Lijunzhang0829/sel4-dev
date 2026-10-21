# 0030-tcb-frame-batch

| Field | Value |
|---|---|
| Pattern | G (batch) |
| Key | `G:TcbAcc_AI:batch:tcb-ops` |
| Variant | seL4-source PR (rule 5 full record) |
| Branch | spec-strengthen |
| Date | 2026-06-09 |
| File | `verification/l4v/proof/invariant-abstract/TcbAcc_AI.thy` |
| Verdict | applied |
| Impact verdict | additive |
| Δ wall (trial) | -0.7% |
| Walls | baseline=61185 ms · trial=60774 ms · apply=63112 ms |

## What changed

See `patch.diff`. **5 new frame lemmas** inserted across two anchor points.

After `set_thread_state_machine_state[wp]` (L1478, added by [[0027]]):

```isabelle
lemma set_thread_state_arch_state[wp]:
  "\<lbrace>\<lambda>s. P (arch_state s)\<rbrace> set_thread_state t st \<lbrace>\<lambda>_ s. P (arch_state s)\<rbrace>"
  by (wpsimp simp: set_thread_state_def)
```

After `set_bound_notification_valid_ioc[wp]` (L1491):

```isabelle
lemma set_bound_notification_machine_state[wp]: ...
lemma set_bound_notification_domain_index[wp]:  ...
lemma set_bound_notification_domain_time[wp]:   ...
lemma set_bound_notification_arch_state[wp]:    ...
  -- all by (wpsimp simp: set_bound_notification_def)
```

## Reference companion(s)

In TcbAcc_AI.thy:
- `set_thread_state_machine_state[wp]` (the [[0027]] addition — same op family, different field)
- `set_bound_notification_valid_ioc[wp]` (L1480 — direct anchor block)
- All earlier `set_object_<field>[wp]` lemmas in KHeap_AI ([[0019]]/[[0020]]/[[0021]]/[[0022]]/[[0024]]/[[0025]])

## Strengthening claim — why each field is preserved

**set_thread_state arch_state**: `set_thread_state` decomposes
into `set_object` (kheap write, preserves arch_state via
`set_object_arch_state[wp]` from [[0022]]) + `do_extended_op
(set_thread_state_ext t)` (ext-state write, preserves arch_state
because arch_state is a top-level abstract state field NOT in
exst; verified by the meta lemma at `Invariants_AI.thy:3437`).

**set_bound_notification × 4 fields**: `set_bound_notification`
body is `gets_the (get_tcb ref) ⨾ set_object ...` — pure object
write to kheap. **No `do_extended_op` call** (unlike
set_thread_state). Each top-level field's frame goes through
the same wpsimp lift using the existing
`set_object_<field>[wp]` lemma family.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 1. check-theory.sh --patch | ✓ OK 60774 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline × 1.30 | ✓ (-0.7%) |
| 4. parent SKILL hard rules | ✓ |

## Impact on seL4 (上下游)

| Phase | Wall | Notes |
|---|---:|---|
| baseline | 61,185 ms | TcbAcc_AI build (post [[0027]]) |
| trial (with 5 new lemmas) | 60,774 ms | **−0.7%** |
| apply (re-verifies) | 63,112 ms | within noise of baseline |

`consumers_lines=0, consumers_files=0` — brand new lemmas; no
in-file usage at introduction. Cross-session: NOT rebuilt.

The lower-magnitude delta (vs −4.4% for [[0027]]) is expected:
`set_thread_state` is invoked far more often than
`set_bound_notification` across AInvs proofs, so a single new
frame on the more-popular op has a larger wall payoff.

## What was dropped from this batch

The original survey listed 12 set_thread_state /
set_bound_notification / set_mrs candidates × 4 fields = 12 for
TcbAcc_AI's Tier 1. **5 applied here**, **2 dropped**
(set_thread_state:domain_index, set_thread_state:domain_time),
**3 deferred** (set_mrs:* — see below).

**set_thread_state domain_index / domain_time dropped reason**:
These fields live inside `exst` (the ext-state).
`set_thread_state` calls `do_extended_op (set_thread_state_ext
t)` which can fire `set_scheduler_action` — a `modify`
operation on `exst`. `wpsimp` cannot discharge the obligation
`P (domain_index_internal (f (exst s))) = P (domain_index s)`
for arbitrary P without a helper lemma proving that the
specific `f` preserves `domain_index_internal`. The
Invariants_AI:3421-3437 meta lemmas cover
`idle_thread`/`machine_state`/`arch_state` (all top-level
non-ext fields) but not the ext-state projections.

**set_mrs × 3 fields deferred reason**: `set_mrs` decomposes
into per-message `thread_set` + `do_machine_op storeWord`
chains via `zipWithM_x_mapM`. The proof requires
`set_mrs_thread_set_dmo` + per-field `thread_set_<field>_trivial`
helpers (`set_mrs_only_idle[wp]` uses
`thread_set_only_idle_trivial`). For `domain_index/time/
arch_state` no such helpers exist. Adding them would be 3 × 2 =
6 extra one-time infrastructure lemmas plus the 3 frame lemmas
— beyond this single-experiment scope.

## Notes / follow-ups

- The framework's `execute_custom` was extended to accept
  `--pattern G --patch <custom>` (in addition to A/D) to
  support ops whose signature differs from `set_object p ko`.
  The auto-template in `execute_G` is still set_object-specific.
- Heap stale-state cost: this batch required a 45-min AInvs
  cascade rebuild because earlier experiments (0014-0027) had
  left the AInvs heap's fingerprint chain out of sync with
  on-disk source. Once rebuilt, subsequent applies don't
  invalidate cross-file verification (Ipc_AI baseline kept
  working after this batch — see [[0031]]).
- **Follow-up infrastructure work** to unlock the deferred
  candidates: add `do_extended_op_domain_index[wp]`,
  `do_extended_op_domain_time[wp]`, plus a
  `thread_set_<field>_trivial` family for set_mrs. Mechanical
  one-time additions.
- Also: a minor framework fix landed during this run —
  `spec_strengthen_run.sh` step [2/6] baseline now uses
  `|| true` defense (mirroring step [3/6] trial), preventing
  silent `set -e`/`pipefail` abort when check-theory.sh fails
  (e.g. on a stale heap lock).
