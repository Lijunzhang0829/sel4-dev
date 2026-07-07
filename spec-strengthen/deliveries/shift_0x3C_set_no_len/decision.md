# strengthen-ArchVSpaceEntries_AI-20260629-051913 — shift_0x3C_set_no_len

| Field | Value |
|---|---|
| Key | `P:ArchVSpaceEntries_AI:shift_0x3C_set_no_len` |
| Slot | P |
| Delivery | named / planned |
| Delivery state | pending (grace 8w) |
| Delivery target | Any consumer of shift_0x3C_set that can drop the len_of constraint; likely mapM_x_store_invalid_pte_valid_pdpt or mapM_x_store_pde_valid_pdpt_objs call sites |
| File | `proof/invariant-abstract/ARM/ArchVSpaceEntries_AI.thy` |
| Verdict | trial-passed (dry-run) |
| Impact verdict | additive |
| Δ wall (trial) | -0.1% |
| Walls | baseline=55083 ms · trial=55016 ms |

## Strengthening claim

(is_aligned p 6 \<and> 8 \<le> bits \<and> bits < 32) \<Longrightarrow> (is_aligned p 6 \<and> 8 \<le> bits \<and> bits < 32 \<and> len_of TYPE('a) = bits - 2)

## Why this slot is real (agent rationale)

The proof body of shift_0x3C_set never mentions len_of or any tactic that explicitly consumes the 'len_of TYPE('a) = bits - 2' hypothesis. The two other conjuncts (is_aligned p 6 and 8 <= bits) are textually consumed via is_aligned_add_or and shiftl_less_t2n respectively. The high-priority differential signal (0023 shape) strongly suggests this conjunct is dropped implicitly. The proof is verbatim from the original.

## Delivery attestation

- mechanism: **named** → resolved **planned**
- delivery_state: pending (orphan after 8w with no consumer)
- target (agent claim): Any consumer of shift_0x3C_set that can drop the len_of constraint; likely mapM_x_store_invalid_pte_valid_pdpt or mapM_x_store_pde_valid_pdpt_objs call sites
- gate verdict: named-planned accepted (provisional, 8-week grace)

## What changed

See `patch.diff` (additive — original lemmas untouched). `range-patch.patch.txt` is the check-theory.sh range-replace input.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 0. delivery contract (§2.5) | ✓ |
| 1. check-theory.sh --patch | ✓ OK 55016 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline×gate | ✓ (-0.1%) |
| 4. additive (no `_old` witness needed) | ✓ |

## Notes / follow-ups

- ⚠ delivery_state is **pending** (planned): a consumer/downstream must land within the 8-week grace period or `spec_delivery_lifecycle.py` will mark this **orphan** (design §6.2). Run that sweep periodically to advance the state.
