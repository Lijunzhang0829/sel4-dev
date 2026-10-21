# 0035-set-cdt-arch-state-frame-lemma

| Field | Value |
|---|---|
| Pattern | G |
| Key | `G:Untyped_AI:set_cdt:arch_state` |
| Variant | seL4-source PR (rule 5 full record) |
| Branch | spec-strengthen |
| Date | 2026-06-09 |
| File | `verification/l4v/proof/invariant-abstract/Untyped_AI.thy` |
| Verdict | applied |
| Impact verdict | additive |
| Δ wall (trial) | -2.7% |
| Walls | baseline=68616 ms · trial=66787 ms · apply=67591 ms |

## What changed

See `patch.diff`. **Fully auto-generated** by `execute_G` after the
[[execute_G fix 767d57f]] — first Pattern G experiment on this
branch that did not require a hand-written patch.

```isabelle
lemma set_cdt_arch_state[wp]:
  "\<lbrace>\<lambda>s. P (arch_state s)\<rbrace> set_cdt t \<lbrace>\<lambda>_ s. P (arch_state s)\<rbrace>"
  by (wpsimp simp: set_cdt_def)
```

Inserted after `set_cdt_state_hyp_refs_of[wp]` block end (L2878
`done`).

## Reference companion(s)

In Untyped_AI.thy near anchor L2871-2878:
- `set_cdt_state_hyp_refs_of[wp]` (direct anchor block)

Cross-file `set_cdt_<field>[wp]` family covering other state record
fields:
- `set_cdt_machine_state[wp]` ([[0015]])
- `set_cdt_cur_thread[wp]` ([[0016]])
- `set_cdt_idle_thread[wp]` ([[0017]])
- `set_cdt_cur_domain[wp]`

`arch_state` was the missing slot in the literal-field frame
family for `set_cdt`. Detected by `spec_frame_gap.py`'s
mechanical preflight as a clean gap (post-dmo / dxo gates
[[5cd9c65]] confirmed no semantic FP / infra-bound issue).

## Strengthening claim

`set_cdt t` body unfolds to a single `modify` operation that
writes only the `cdt` field. `arch_state` is a separate
top-level record field, untouched. Discharged by
`wpsimp simp: set_cdt_def` (the canonical default tactic
emitted by execute_G).

Strict spec strengthening: pre-PR no public commitment on
`arch_state` preservation by `set_cdt`; post-PR arbitrary
`P (arch_state s)` is preserved.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 1. check-theory.sh --patch | ✓ OK 66787 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline × 1.30 | ✓ (-2.7%) |
| 4. parent SKILL hard rules | ✓ |

## Impact on seL4 (上下游)

| Phase | Wall | Notes |
|---|---:|---|
| baseline | 68,616 ms | Untyped_AI build (AInvs session) |
| trial (with patch) | 66,787 ms | **−2.7%** |
| apply (re-verifies) | 67,591 ms | within noise of baseline |

`consumers_lines=0, consumers_files=0` — brand-new lemma.

The negative trial delta is mildly surprising for a single-frame
add — usually new `[wp]` rules add lookup overhead. Likely
explanation: Untyped_AI proofs include arch_state-touching
`set_cdt` subgoals that previously fell through to a slower wp
fallback chain.

## Tooling milestone

This is the proof-of-concept for the **fully automated Pattern G
workflow**:

1. **Survey** (`spec_strengthen_run.sh survey` + `spec_frame_gap.py`):
   detected gap, gates passed.
2. **Execute** (`execute --candidate <key> -y`): auto-generated
   patch via `spec_op_args.py` (args: `t`) + default tactic
   (`by (wpsimp simp: set_cdt_def)`) + correct anchor block-end
   (L2878 `done`).
3. **Verify**: check-theory.sh trial OK without manual patch.
4. **Apply**: standard_pipeline ran apply + spec_impact + audit
   dir creation without intervention.

The whole flow from `survey → execute -y → audit dir` was a
single command per stage — no hand-crafted patches, no per-op
template tweaks. Previous experiments (0027/0030/0031/0032/0033/
0034) all required `execute_custom --patch <hand-written>`
because `execute_G`'s template was set_object-shape only.

## Notes / follow-ups

- **Remaining 14 new Tier 1 candidates** (from [[e0f560a]] survey)
  can now be applied with the same `execute --candidate <key> -y`
  one-liner:
  - DetSchedSchedule_AI: set_scheduler_action × 4, set_simple_ko × 4
  - CSpaceInv_AI: set_cap × 4
  - Untyped_AI: set_cdt × 2 (domain_index, domain_time — arch_state done here)
- Each apply changes a file → invalidates AInvs heap for downstream
  verification. Cluster applies per-file before re-validating.
- A reasonable batch: pick one file's candidates (e.g. set_cap × 4
  in CSpaceInv_AI), apply 4 frames in sequence, then move on. AInvs
  heap rebuild is needed once at end for full validation, not after
  every single apply.
