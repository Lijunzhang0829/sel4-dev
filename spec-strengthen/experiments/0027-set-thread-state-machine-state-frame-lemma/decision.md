# 0027-set-thread-state-machine-state-frame-lemma

| Field | Value |
|---|---|
| Pattern | G |
| Key | `G:TcbAcc_AI:set_thread_state:machine_state` |
| Variant | seL4-source PR (rule 5 full record) |
| Branch | spec-strengthen |
| Date | 2026-06-08 |
| File | `verification/l4v/proof/invariant-abstract/TcbAcc_AI.thy` |
| Verdict | applied |
| Impact verdict | additive |
| Δ wall (trial) | -4.4% |
| Walls | baseline=66194 ms · trial=63312 ms · apply=63391 ms |

## What changed

See `patch.diff` in this directory. The unified diff is the
canonical replayable record. New lemma inserted after the
`set_thread_state_valid_ioc[wp]` block (L1474):

```isabelle
lemma set_thread_state_machine_state[wp]:
  "\<lbrace>\<lambda>s. P (machine_state s)\<rbrace> set_thread_state t st \<lbrace>\<lambda>_ s. P (machine_state s)\<rbrace>"
  by (wpsimp simp: set_thread_state_def)
```

## Reference companion(s)

Existing `set_thread_state_*[wp]` companions in TcbAcc_AI.thy at
the time of this PR:

- `set_thread_state_valid_objs[wp]`
- `set_thread_state_aligned[wp]`
- `set_thread_state_typ_at[wp]`
- `set_thread_state_cap_refs_in_kernel_window[wp]` (L1427)
- `set_thread_state_cap_refs_respects_device_regionw[wp]` (L1434)
- `set_thread_state_valid_ioc[wp]` (L1463 — direct anchor)

Cross-op seed: `set_object_machine_state[wp]` in KHeap_AI.thy:1277.
Same shape (frame on `machine_state`, the abstract record field
mutated by `do_machine_op` but not by pure object/heap writes).

## Strengthening claim

`set_thread_state t st` is defined as a TCB update via
`set_object` plus `set_thread_state_ext t` (ext-state). Neither
touches the abstract record's `machine_state`:

- `set_object` writes only `kheap`.
- `set_thread_state_ext` writes only `exst` (the extensible
  state record), not `machine_state` (a separate record field).

The new lemma is from "no public 承诺 on machine_state" to "any P
on machine_state preserved" — strict strengthening with no
prior predicate companion to recover via field instantiation,
hence no meta-entailment argument needed.

Discharged by `wpsimp simp: set_thread_state_def`. The default
wp ruleset already covers `set_object` and `set_thread_state_ext`
machine_state preservation.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 1. check-theory.sh --patch | ✓ OK 63312 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline × 1.30 | ✓ (-4.4%) |
| 4. parent SKILL hard rules | ✓ |

## Impact on seL4 (上下游)

| Phase | Wall | Notes |
|---|---:|---|
| baseline | 66,194 ms | TcbAcc_AI build (AInvs session) |
| trial (with patch) | 63,312 ms | **−4.4%** |
| apply (re-verifies) | 63,391 ms | within noise of baseline |

`−4.4%` is the strongest negative delta seen on a single-lemma
G-frame addition in this branch so far (tied with [[0025]]
`set_object_domain_time`). The likely cause: `set_thread_state`
is invoked on every IPC / fault / scheduler step in AInvs
proofs, and many of those goal sites depended on the default
slow wp fallback for machine_state preservation. The new `[wp]`
rule shortcuts those.

`spec_impact.py` Tier-2 grep: `consumers_lines=0`,
`consumers_files=0` (brand new lemma; no in-file usage at
introduction). Cross-session: NOT rebuilt.

## Notes / follow-ups

- This is the **first** G application outside the
  KHeap_AI/CSpace_AI core ops (`set_object`, `set_cdt`). It
  validates the survey's discovery of new `set_*` op families
  in IPC/TCB paths.
- Counterpart candidate in this same survey for the same field
  ([[0028]] `set_mrs:machine_state`) failed trial — see that
  decision for the semantic-FP analysis.
- Remaining clean Tier 1 candidates on `set_thread_state` in
  TcbAcc_AI: `domain_index`, `domain_time`, `arch_state`. Same
  proof shape expected.
