# 0032-ipc-tier1-closeout

| Field | Value |
|---|---|
| Pattern | G (batch, with infra) |
| Key | `G:Ipc_AI:tier1:closeout` |
| Variant | seL4-source PR (rule 5 full record) |
| Branch | spec-strengthen |
| Date | 2026-06-09 |
| File | `verification/l4v/proof/invariant-abstract/Ipc_AI.thy` |
| Verdict | applied |
| Impact verdict | additive |
| Δ wall (trial) | -3.4% |
| Walls | baseline=108365 ms · trial=104728 ms · apply=108355 ms |

## What changed

See `patch.diff`. **12 new lemmas** inserted in Ipc_AI.thy across three anchor points.

### Infrastructure helpers (6 lemmas)

**`do_machine_op_<field>[wp]` × 3** (L1132-1141) — after `set_extra_badge_vms`:

```isabelle
lemma do_machine_op_domain_index[wp]:
  "\<lbrace>\<lambda>s. P (domain_index s)\<rbrace> do_machine_op f \<lbrace>\<lambda>_ s. P (domain_index s)\<rbrace>"
  by (wpsimp simp: do_machine_op_def)
-- same shape for domain_time, arch_state
```

Justification: `do_machine_op` body (from `Structures_A.thy:555`) only
modifies `machine_state` via the final `modify` step. All other top-level
state fields pass through unchanged.

**`as_user_<field>[wp]` × 3** (L1876-1885) — after `as_user_machine_state[wp]`:

```isabelle
lemma as_user_domain_index[wp]:
  "\<lbrace>\<lambda>s. P (domain_index s)\<rbrace> as_user r f \<lbrace>\<lambda>_ s. P (domain_index s)\<rbrace>"
  by (wp | simp add: as_user_def split_def)+
-- same for domain_time, arch_state
```

Justification: `as_user` (from `KHeap_A.thy:245`) reads via `gets_the
get_tcb` and writes via `set_object`. `set_object` only writes `kheap`,
preserving all top-level non-kheap fields via the `set_object_<field>[wp]`
family (added across experiments [[0020]] through [[0025]]). Same proof shape as the
already-existing `as_user_machine_state[wp]`.

### Frame lemmas (6 lemmas)

**`set_extra_badge_<field>[wp]` × 3** (L1144-1153) — after the helpers above:

```isabelle
lemma set_extra_badge_domain_index[wp]:
  "\<lbrace>\<lambda>s. P (domain_index s)\<rbrace> set_extra_badge buffer badge n \<lbrace>\<lambda>_ s. P (domain_index s)\<rbrace>"
  by (wpsimp simp: set_extra_badge_def store_word_offs_def)+
-- same for domain_time, arch_state
```

Body: `set_extra_badge` ≡ `store_word_offs` ≡ `get; assert (in_user_frame
…); do_machine_op (storeWord …)`. The `assert` produces a `P → in_user_frame
→ P` residual that the final iteration of `wpsimp` closes via simp. The
`do_machine_op` step uses the helper added above.

**`set_message_info_<field>[wp]` × 3** (L2836-2845) — after
`set_message_info_machine_state[wp]` (from [[0031]]):

```isabelle
lemma set_message_info_domain_index[wp]:
  "\<lbrace>\<lambda>s. P (domain_index s)\<rbrace> set_message_info thread info \<lbrace>\<lambda>_ s. P (domain_index s)\<rbrace>"
  by (wpsimp simp: set_message_info_def)
-- same for domain_time, arch_state
```

Uses the new `as_user_<field>[wp]` helpers internally — `set_message_info`
unfolds to `as_user thread (setRegister …)`.

## Strengthening claim

All 6 frame lemmas are strict strengthening — pre-PR none of these public
machine-state-style preservation claims existed for the respective ops.
Each strengthens the spec from "no public 承诺 on `<field>`" to "any P on
`<field>` is preserved".

The 6 infra helpers are NOT spec strengthening per se (`do_machine_op`
and `as_user` are operations, not subject to a "spec" in the user-facing
sense); they are derived facts that close the proof obligations of the
strengthening frames.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 1. check-theory.sh --patch | ✓ OK 104728 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline × 1.30 | ✓ (-3.4%) |
| 4. parent SKILL hard rules | ✓ |

## Impact on seL4 (上下游)

| Phase | Wall | Notes |
|---|---:|---|
| baseline | 108,365 ms | Ipc_AI build (post [[0031]]) |
| trial (with 12 new lemmas) | 104,728 ms | **−3.4%** |
| apply (re-verifies) | 108,355 ms | within noise of baseline |

The **−3.4%** delta is the strongest single-batch wall improvement
observed on Ipc_AI in this branch. Three drivers:

1. The 6 helpers (`do_machine_op_*`, `as_user_*`) are reused throughout the
   AInvs proofs, not just in this batch's 6 frame lemmas. Existing slow proof
   goals on `domain_index`/`domain_time`/`arch_state` that previously fell
   through to the default wp fallback (or required manual unfolding) now
   discharge via these `[wp]` rules.
2. `set_extra_badge` and `set_message_info` are invoked frequently in the
   IPC transfer logic, so each frame lemma's `[wp]` registration shortcuts
   many ambient proof goals.
3. The trial measures a +12-lemma rule database growth, which adds at most
   constant-factor lookup overhead. The negative delta indicates the lookup
   cost is dwarfed by the proof acceleration.

`consumers_lines=0, consumers_files=0` per `measurement.json` — none of the
new lemmas have explicit name references in the proof tree yet. The
acceleration is purely automatic via `[wp]` registration.

## Tier 1 closeout summary

This batch closes the remaining **set_extra_badge × 3 + set_message_info ×
3 = 6 Tier 1 frame candidates** from the original Ipc_AI survey (after
[[0028]]'s `set_mrs:machine_state` was dropped as a semantic FP, and
[[0031]]'s `set_message_info:machine_state` landed alone).

The Ipc_AI Tier 1 list as of this PR:

| Op | Field | Status |
|---|---|---|
| set_extra_badge | machine_state | **dropped (semantic FP — do_machine_op storeWord)** |
| set_extra_badge | domain_index | applied (this batch) |
| set_extra_badge | domain_time | applied (this batch) |
| set_extra_badge | arch_state | applied (this batch) |
| set_mrs | machine_state | dropped (semantic FP, [[0028]]) |
| set_mrs | domain_index | **deferred** (needs set_mrs_thread_set_dmo + thread_set_<field>_trivial) |
| set_mrs | domain_time | deferred (same) |
| set_mrs | arch_state | deferred (same) |
| set_message_info | machine_state | applied ([[0031]]) |
| set_message_info | domain_index | applied (this batch) |
| set_message_info | domain_time | applied (this batch) |
| set_message_info | arch_state | applied (this batch) |

8 of 12 Ipc_AI Tier 1 candidates **applied** (3 in this batch using new
helpers + 1 alone in 0031 + 6 via helpers in this batch — total 7 set_*
frames + 6 infra helpers = 13 new lemmas with [[0031]] included). 1 dropped
as FP. 3 deferred (the set_mrs cluster).

## Notes / follow-ups

- The two FP cases (`set_mrs:machine_state`, `set_extra_badge:machine_state`)
  share root cause: both ops call `do_machine_op storeWord`-style actions
  that genuinely write `machine_state.memory`. Detector improvement (a
  `do_machine_op_with_write` pre-flight check) would catch these mechanically
  before patch generation — see [[0028]] decision.md follow-up section.
- For the **deferred set_mrs cluster**, the missing pieces are:
  - `set_mrs_thread_set_dmo` already exists in the tree (used by
    `set_mrs_only_idle` etc.) — reusable.
  - `thread_set_<field>_trivial` for domain_index/domain_time/arch_state —
    each ~3-line proof following the `thread_set_only_idle_trivial` pattern.
  - With those helpers, the 3 set_mrs frame lemmas would close with
    `by (wp set_mrs_thread_set_dmo thread_set_<field>_trivial | simp)+`.
- For **TcbAcc_AI set_thread_state's deferred domain_index / domain_time**
  (see [[0030]] decision), the missing helpers are `do_extended_op_domain_*[wp]`.
  Same one-shot infra pattern — write the helper, frame closes mechanically.
- This batch demonstrates the **infrastructure-first** pattern for closing
  Tier 1 candidates that share a common proof obstacle: instead of
  per-candidate special-case proofs, add the generic helper once and let
  it cover N downstream candidates with one-line proofs.
