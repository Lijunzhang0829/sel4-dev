# strengthen-DetSchedAux_AI-20260629-050712 — retype_etcb_at_helper_no_valid_etcbs

| Field | Value |
|---|---|
| Key | `P:DetSchedAux_AI:retype_etcb_at_helper_no_valid_etcbs` |
| Slot | P |
| Delivery | named / planned |
| Delivery state | pending (grace 8w) |
| Delivery target | retype_region_etcb_at or any future consumer of retype_etcb_at_helper that does not hold valid_etcbs_2 |
| File | `proof/invariant-abstract/DetSchedAux_AI.thy` |
| Verdict | trial-passed (dry-run) |
| Impact verdict | additive |
| Δ wall (trial) | -1.6% |
| Walls | baseline=45511 ms · trial=44802 ms |

## Strengthening claim

(etcb_at' P t ekh ∧ d ≠ Untyped ∧ foldr...t = Some(TCB tcb) ∧ tcb_state tcb ≠ Inactive) ==> (etcb_at' P t ekh ∧ valid_etcbs_2 ekh kh ∧ d ≠ Untyped ∧ foldr...t = Some(TCB tcb) ∧ tcb_state tcb ≠ Inactive)

## Why this slot is real (agent rationale)

The proof of retype_etcb_at_helper (L68-72) never mentions valid_etcbs_2: the induction and case_tac on d only use d ≠ Untyped, etcb_at', and the foldr-TCB equation. Dropping valid_etcbs_2 gives a strictly stronger implication.

## Delivery attestation

- mechanism: **named** → resolved **planned**
- delivery_state: pending (orphan after 8w with no consumer)
- target (agent claim): retype_region_etcb_at or any future consumer of retype_etcb_at_helper that does not hold valid_etcbs_2
- gate verdict: named-planned accepted (provisional, 8-week grace)

## What changed

See `patch.diff` (additive — original lemmas untouched). `range-patch.patch.txt` is the check-theory.sh range-replace input.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 0. delivery contract (§2.5) | ✓ |
| 1. check-theory.sh --patch | ✓ OK 44802 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline×gate | ✓ (-1.6%) |
| 4. additive (no `_old` witness needed) | ✓ |

## Notes / follow-ups

- ⚠ delivery_state is **pending** (planned): a consumer/downstream must land within the 8-week grace period or `spec_delivery_lifecycle.py` will mark this **orphan** (design §6.2). Run that sweep periodically to advance the state.
