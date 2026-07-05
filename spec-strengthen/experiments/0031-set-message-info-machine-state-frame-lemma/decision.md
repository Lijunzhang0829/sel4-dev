# 0031-set-message-info-machine-state-frame-lemma

| Field | Value |
|---|---|
| Pattern | G |
| Key | `G:Ipc_AI:set_message_info:machine_state` |
| Variant | seL4-source PR (rule 5 full record) |
| Branch | spec-strengthen |
| Date | 2026-06-09 |
| File | `verification/l4v/proof/invariant-abstract/Ipc_AI.thy` |
| Verdict | applied |
| Impact verdict | additive |
| Δ wall (trial) | +1.6% |
| Walls | baseline=106564 ms · trial=108310 ms · apply=107629 ms |

## What changed

See `patch.diff`. New frame lemma inserted after
`set_message_info_global_refs[wp]` (L2794):

```isabelle
lemma set_message_info_machine_state[wp]:
  "\<lbrace>\<lambda>s. P (machine_state s)\<rbrace> set_message_info thread info \<lbrace>\<lambda>_ s. P (machine_state s)\<rbrace>"
  by (wpsimp simp: set_message_info_def)
```

## Reference companion(s)

In Ipc_AI.thy:
- `set_message_info_valid_arch[wp]` (L2780)
- `set_message_info_global_refs[wp]` (L2792 — direct anchor)

Cross-op family (same machine_state-frame shape):
- `set_thread_state_machine_state[wp]` ([[0027]])
- `set_bound_notification_machine_state[wp]` ([[0030]])
- `set_object_machine_state[wp]` (KHeap_AI seed)

Crucial dependency: `as_user_machine_state[wp]` (already exists
at `Ipc_AI.thy:1848`) — provides the inner step for the
wpsimp chain.

## Strengthening claim

`set_message_info thread info` body unfolds to
`as_user thread (setRegister msg_info_register
(message_info_to_data info))`. `as_user` lifts a user-monad
action over the TCB's `arch_tcb_context`, not over the kernel's
`machine_state`. The `setRegister` action only mutates register
state inside the TCB.

`as_user_machine_state[wp]` (already in tree) proves that
`as_user` preserves any P on machine_state. The wpsimp chain
discharges the `set_message_info_def` unfold + `as_user_*` rule
in one step.

This is a **strict strengthening**: pre-PR, no public
machine_state-preservation claim on `set_message_info`. Post-PR,
arbitrary P on machine_state preserved. No prior predicate
companion to recover via field instantiation, so no
meta-entailment argument needed.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 1. check-theory.sh --patch | ✓ OK 108310 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline × 1.30 | ✓ (+1.6%) |
| 4. parent SKILL hard rules | ✓ |

## Impact on seL4 (上下游)

| Phase | Wall | Notes |
|---|---:|---|
| baseline | 106,564 ms | Ipc_AI build (AInvs session) |
| trial (with patch) | 108,310 ms | **+1.6%** |
| apply (re-verifies) | 107,629 ms | within noise of baseline |

`consumers_lines=0, consumers_files=0` — brand new lemma; no
in-file usage at introduction. Cross-session: NOT rebuilt.

Positive delta (+1.6%) is within trivial noise — single new
[wp] rule adds rule-database lookups even when not fired. Wall
is ~107s for Ipc_AI alone, so 1.6% ≈ 1.7s. Below the wall gate
threshold (+30%), well above the "applied" cutoff.

## What was dropped from this attempt

Original Tier 1 candidate slate for Ipc_AI:

- set_extra_badge × 4 fields (machine_state, domain_index,
  domain_time, arch_state)
- set_mrs × 4 fields (already known FP on machine_state from
  [[0028]])
- set_message_info × 4 fields (machine_state above, plus
  domain_index, domain_time, arch_state)

**Skipped in this PR**:

- **set_extra_badge × 4**: body unfolds to `store_word_offs`
  which is `do { s ← get; assert (...); do_machine_op
  (storeWord ...) }`. For machine_state this is a hard
  semantic FP (same as [[0028]] set_mrs). For non-machine
  fields, the `assert` produces a Hoare obligation
  (in_user_frame check) that `wpsimp simp:
  set_extra_badge_def store_word_offs_def` does not
  auto-discharge. Needs `wp assert_wp` + an explicit lift
  rule that says `do_machine_op` preserves the abstract
  field. No such rule exists for `domain_index`, etc.
- **set_message_info domain_index / domain_time /
  arch_state**: `as_user_machine_state[wp]` covers the
  machine_state path, but no analogous `as_user_<other>[wp]`
  rules exist. `wpsimp simp: set_message_info_def as_user_def
  select_f_def set_object_def get_object_def` leaves an
  unsimplified subgoal involving `kheap` + `a_type` checks
  for the modified TCB. Provable with `clarsimp` + tcb-cap
  case analysis, but no longer a one-liner.

Per the spec_strengthen workflow (one-shot apply per
experiment), only the trivially-provable
`set_message_info_machine_state` was admitted here.

## Notes / follow-ups

- Same `wpsimp simp: <op>_def` pattern works for any op that
  decomposes into single-step state writes covered by existing
  `[wp]` rules. The `set_message_info` body is exactly such an
  op (one `as_user` call).
- **Infrastructure for the 7 deferred Tier 1 candidates** on
  Ipc_AI:
  1. `as_user_domain_index[wp]`, `as_user_domain_time[wp]`,
     `as_user_arch_state[wp]` — analogues of
     `as_user_machine_state[wp]`. Each ~2-line proof.
  2. `do_machine_op_<field>[wp]` for non-machine fields — also
     analogues. Could also use the meta-lift via
     `Invariants_AI.thy:3437` style helpers.
  3. An `assert_wp_trivial` / `assert_inv[wp]` rule for the
     in-user-frame assert in store_word_offs.
  Once those exist, all 7 Tier 1 candidates close with the same
  `wpsimp simp: <op>_def` one-liner pattern.
- **set_mrs × 3 fields** in both files (Ipc_AI and TcbAcc_AI)
  also deferred — needs the same do_machine_op preservation
  rules + per-field `thread_set_<field>_trivial` helpers.
