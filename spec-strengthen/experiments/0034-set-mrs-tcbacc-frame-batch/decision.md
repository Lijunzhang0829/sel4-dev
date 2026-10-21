# 0034-set-mrs-tcbacc-frame-batch

| Field | Value |
|---|---|
| Pattern | G (batch) |
| Key | `G:TcbAcc_AI:set_mrs:batch` |
| Variant | seL4-source PR (rule 5 full record) |
| Branch | spec-strengthen |
| Date | 2026-06-09 |
| File | `verification/l4v/proof/invariant-abstract/TcbAcc_AI.thy` |
| Verdict | applied |
| Impact verdict | additive |
| Δ wall (trial) | +0.4% |
| Walls | baseline=62325 ms · trial=62545 ms · apply=63189 ms |

## What changed

See `patch.diff`. **3 new frame lemmas** inserted after
`set_mrs_pred_tcb_at[wp]` (L1835):

```isabelle
lemma set_mrs_domain_index[wp]:
  "\<lbrace>\<lambda>s. P (domain_index s)\<rbrace> set_mrs t b m \<lbrace>\<lambda>_ s. P (domain_index s)\<rbrace>"
  apply (rule set_mrs_thread_set_dmo)
   apply (wpsimp simp: thread_set_def)
  apply (wpsimp simp: do_machine_op_def)
  done

lemma set_mrs_domain_time[wp]:  ... (same proof shape)
lemma set_mrs_arch_state[wp]:   ... (same proof shape)
```

## Reference companion(s)

In TcbAcc_AI.thy:
- `set_mrs_invs[wp]` (L1804)
- `set_mrs_thread_set_dmo` (L1816) — the workhorse lemma used by every set_mrs frame
- `set_mrs_pred_tcb_at[wp]` (L1829 — direct anchor block)

In Ipc_AI (added by [[0033]]):
- `set_mrs_domain_index[wp]`, `set_mrs_domain_time[wp]`, `set_mrs_arch_state[wp]`
  — Wait, **these have the same name as the new lemmas in this file**. Yet
  no duplicate-fact error occurred. Reason: each is qualified by its host
  theory (`TcbAcc_AI.set_mrs_domain_index` vs `Ipc_AI.set_mrs_domain_index`).
  Both register `[wp]` and both end up in the wp database; wp search just
  finds a hit either way. The redundancy is harmless code-hygiene noise, not
  a verification bug. A follow-up could dedupe by relocating Ipc_AI's
  versions (or removing them).

## Strengthening claim

`set_mrs t b m` body decomposes via `set_mrs_thread_set_dmo` into
two paths:

1. **`thread_set`** (per-register TCB update): preserves
   `domain_index`/`domain_time`/`arch_state` because thread_set
   writes only kheap (via `set_object`), and the
   `set_object_<field>[wp]` family from [[0022]]/[[0024]]/[[0025]]
   covers these three fields.
2. **`do_machine_op (storeWord ...)`** (overflow-register memory
   write): preserves any non-machine_state top-level field
   because `do_machine_op` only writes the `machine_state`
   record field. Established here by inline-unfolding
   `do_machine_op_def` and discharging via wpsimp.

**Why this isn't the same semantic FP as [[0028]] / [[0029]]**:
The 0028 candidate was `set_mrs:machine_state`. For machine_state,
the dmo subgoal is `\<lbrace>P (machine_state s)\<rbrace> do_machine_op
storeWord \<lbrace>P (machine_state s)\<rbrace>` — which is **false**
since storeWord writes `machine_state.memory`. Here, for
`domain_index/time/arch_state`, the dmo subgoal is provable
because none of those three fields live inside `machine_state`.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 1. check-theory.sh --patch | ✓ OK 62545 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline × 1.30 | ✓ (+0.4%) |
| 4. parent SKILL hard rules | ✓ |

## Impact on seL4 (上下游)

| Phase | Wall | Notes |
|---|---:|---|
| baseline | 62,325 ms | TcbAcc_AI build (post [[0030]]) |
| trial (with 3 new lemmas) | 62,545 ms | **+0.4%** |
| apply (re-verifies) | 63,189 ms | within noise of baseline |

`consumers_lines=0, consumers_files=0` — new lemmas, no usage
at introduction. The near-zero wall delta is consistent with
the [[0030]] pattern: `set_mrs` is a less-popular op than
`set_thread_state` in TcbAcc_AI proof traffic, so the new
`[wp]` rules add lookup cost but rarely fire shortcut paths.

## What this completes for Tier 1

This experiment closes the last 3 TcbAcc_AI set_mrs candidates
from the original [[Tier 1 survey]]:

- ✓ `G:TcbAcc_AI:set_mrs:domain_index`
- ✓ `G:TcbAcc_AI:set_mrs:domain_time`
- ✓ `G:TcbAcc_AI:set_mrs:arch_state`

**Remaining un-closed TcbAcc_AI Tier 1**:

- `G:TcbAcc_AI:set_mrs:machine_state` — same semantic FP as
  [[0028]] (`set_mrs` writes `machine_state.memory` via
  `do_machine_op storeWord`). Confirmed analytically; not
  re-tested in TcbAcc_AI.
- `G:TcbAcc_AI:set_thread_state:domain_index`,
  `G:TcbAcc_AI:set_thread_state:domain_time` — deferred per
  [[0030]] decision. Requires `do_extended_op` lift
  infrastructure for ext-state-projected fields. Not in scope
  for single-experiment work.

## Tactic notes

The explicit `apply (rule set_mrs_thread_set_dmo) ; ... ; done`
form (vs `wpsimp wp:`) was used because the `set_mrs_thread_set_dmo`
rule produces two pinned subgoals (thread_set + dmo) that need
DIFFERENT unfolds:

- thread_set subgoal: needs `thread_set_def` unfold (then
  `set_object_<field>[wp]` discharges)
- dmo subgoal: needs `do_machine_op_def` unfold (then the
  `modify` step trivially preserves non-machine fields)

A naive `(wp set_mrs_thread_set_dmo | simp add: thread_set_def
do_machine_op_def)+` leaves residual subgoals because the simp
unfolds both at once, mixing the goal structure in ways that
don't auto-close. Two separate `apply wpsimp simp: ...` lines
keep the goal structure clean.

## Notes / follow-ups

- **Possible refactor**: relocate the Ipc_AI 0033 thread_set/
  set_mrs helpers to TcbAcc_AI (early). This file is the
  natural home for thread_set-frame helpers (thread_set is
  defined in KHeap_A, used by both TcbAcc_AI and Ipc_AI).
  Doing so makes the helpers visible to all downstream files,
  not just Ipc_AI and later. Not blocking; cleanup-grade work.
- **TcbAcc_AI Tier 1 effective closure**: 9/12 candidates
  applied; 1 semantic FP; 2 deferred-on-infrastructure. With
  this experiment the file is "as far as single-shot
  experiments take it" for the original survey scope.
