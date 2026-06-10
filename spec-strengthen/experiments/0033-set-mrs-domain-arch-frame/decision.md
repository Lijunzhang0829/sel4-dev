# 0033-set-mrs-domain-arch-frame

| Field | Value |
|---|---|
| Pattern | G (batch, with infra) |
| Key | `G:Ipc_AI:set_mrs:domain-arch` |
| Variant | seL4-source PR (rule 5 full record) |
| Branch | spec-strengthen |
| Date | 2026-06-09 |
| File | `verification/l4v/proof/invariant-abstract/Ipc_AI.thy` |
| Verdict | applied |
| Impact verdict | additive |
| Δ wall (trial) | +2.2% |
| Walls | baseline=107257 ms · trial=109573 ms · apply=108567 ms |

## What changed

**6 new lemmas** appended after `set_mrs_vms[wp]` block (L1917).

### Helper lemmas (3)

```isabelle
lemma thread_set_domain_index[wp]: ...
lemma thread_set_domain_time[wp]:  ...
lemma thread_set_arch_state[wp]:   ...
  -- all by (wpsimp simp: thread_set_def)
```

Justification: `thread_set f t` body unfolds to `gets_the (get_tcb t) ;
set_object t (TCB (f tcb))`. Both reads/writes operate on `kheap` only —
top-level non-kheap fields are preserved via the
`set_object_<field>[wp]` family ([[0020]]/[[0021]]/[[0022]]/[[0024]]/[[0025]]).

### Frame lemmas (3) — using helpers + the existing `set_mrs_thread_set_dmo`

```isabelle
lemma set_mrs_domain_index[wp]:
  "\<lbrace>\<lambda>s. P (domain_index s)\<rbrace> set_mrs t b m \<lbrace>\<lambda>_ s. P (domain_index s)\<rbrace>"
  by (wp set_mrs_thread_set_dmo)
-- same shape for domain_time, arch_state
```

`set_mrs_thread_set_dmo` (TcbAcc_AI.thy:1816) is the existing parametric
decomposition rule: given (i) `thread_set ...` preserves a predicate P, and
(ii) `do_machine_op (storeWord ...)` preserves P, conclude
`set_mrs ...` preserves P. The thread_set side is closed by the new
helpers above; the dmo side is closed by the `do_machine_op_<field>[wp]`
helpers already added in [[0032]].

## Strengthening claim

3 strict strengthening frames on `set_mrs` for `domain_index` /
`domain_time` / `arch_state`. The IPC message-register write does not
mutate these top-level abstract state fields (only `kheap` via
`thread_set` and `machine_state.memory` via `do_machine_op storeWord` —
neither overlapping with the framed fields).

The `set_mrs:machine_state` candidate from the original survey was already
ruled out as a **semantic FP** in [[0028]] (storeWord directly writes
machine_state.memory). This batch closes the remaining 3 non-machine
candidates.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 1. check-theory.sh --patch | ✓ OK 109573 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline × 1.30 | ✓ (+2.2%) |
| 4. parent SKILL hard rules | ✓ |

## Impact on seL4 (上下游)

| Phase | Wall | Notes |
|---|---:|---|
| baseline | 107,257 ms | Ipc_AI build (post [[0032]]) |
| trial (with 6 new lemmas) | 109,573 ms | +2.2% |
| apply (re-verifies) | 108,567 ms | within noise of baseline |

The +2.2% trial delta (≈2.3s on 107s) is within trivial noise — the
6 new wp rules are not fired in Ipc_AI's existing proofs (since no
in-file goal previously required these specific frames). Rule-database
growth incurs constant-factor lookup overhead.

Cross-file expected payoff: downstream IPC-heavy theories (CRefine,
Refine arch-specific Ipc_R) that reason about set_mrs invariants may
see acceleration via these new auto-discharge rules. Cross-session NOT
rebuilt here.

## Tier 1 closeout: Ipc_AI complete

After this PR, all Ipc_AI Tier 1 set_mrs candidates from the survey are
addressed:

| Candidate | Status |
|---|---|
| `set_mrs:machine_state` | FP (semantic — do_machine_op storeWord), [[0028]] |
| `set_mrs:domain_index` | **applied** (this batch) |
| `set_mrs:domain_time`  | **applied** (this batch) |
| `set_mrs:arch_state`   | **applied** (this batch) |

Combined with [[0028]]/[[0031]]/[[0032]], the full Ipc_AI Tier 1 list is
**closed** except `set_mrs:machine_state` and `set_extra_badge:machine_state`
(both FPs).

## TcbAcc_AI set_mrs note

The original survey also generated TcbAcc_AI:set_mrs:* candidates
(survey detected the candidate per-file). Adding `set_mrs_<field>[wp]`
in Ipc_AI makes them globally available downstream of Ipc_AI's load —
this is sufficient for callers in Refine/CRefine and other AInvs files
loaded after Ipc_AI. TcbAcc_AI itself doesn't use these frames in its
own proofs (set_mrs is primarily an IPC-layer op), so no per-file
duplication needed.

If a future need arises in TcbAcc_AI proper, the same patch could be
relocated higher in the load chain (TcbAcc_AI's own end-of-set_mrs-block
at L1815).

## Notes / follow-ups

- One Tier 1 candidate **still deferred** after this PR:
  `set_thread_state:domain_index/domain_time`. The set_thread_state body
  has a `do_extended_op (set_thread_state_ext t)` step. set_thread_state_ext
  conditionally fires `set_scheduler_action` — a `modify` on the ext-state.
  For arbitrary `f`, `do_extended_op f` can change any ext-state field
  including domain_index_internal, so a generic
  `do_extended_op_domain_index[wp]` doesn't hold. The proof would need
  a `set_thread_state_ext_domain_index[wp]` helper proven via unfolding
  `set_thread_state_ext_def` + `set_scheduler_action_def` + showing
  `modify (scheduler_action_update _)` preserves `domain_index_internal`
  on exst. Multi-step proof, beyond single-experiment scope.
- The **infra-first pattern** is now well-established:
  - 0032 added do_machine_op_<field>[wp] + as_user_<field>[wp] helpers
  - 0033 added thread_set_<field>[wp] helpers + leveraged 0032's helpers via set_mrs_thread_set_dmo
  - Both batches showcase how a single ~3-line helper unblocks 3 frame
    lemmas with one-line proofs.
- With this PR, **AInvs session has a new generic wp toolkit** for
  framing top-level abstract state fields across the common
  `thread_set` / `do_machine_op` / `as_user` IPC-layer primitives.
