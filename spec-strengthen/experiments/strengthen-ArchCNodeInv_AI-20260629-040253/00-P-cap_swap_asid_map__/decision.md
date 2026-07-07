# strengthen-ArchCNodeInv_AI-20260629-040253 — cap_swap_asid_map'

| Field | Value |
|---|---|
| Key | `P:ArchCNodeInv_AI:cap_swap_asid_map'` |
| Slot | P |
| Delivery | named / planned |
| Delivery state | pending (grace 8w) |
| Delivery target | Future consumers of cap_swap_asid_map that currently carry the cte_wp_at preconditions can be simplified to call cap_swap_asid_map' instead. |
| File | `proof/invariant-abstract/ARM/ArchCNodeInv_AI.thy` |
| Verdict | trial-passed (dry-run) |
| Impact verdict | additive |
| Δ wall (trial) | 0.8% |
| Walls | baseline=47504 ms · trial=47866 ms |

## Strengthening claim

(valid_asid_map and
    cte_wp_at (weak_derived c) a and
    cte_wp_at (weak_derived c') b) ==> (valid_asid_map) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `cte_wp_at (weak_derived c) a`; `cte_wp_at (weak_derived c') b`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

## Why this slot is real (agent rationale)

The proof of cap_swap_asid_map never mentions cte_wp_at, weak_derived, or either slot name: it unfolds cap_swap_def/set_cdt_def and then discharges everything via set_cap.vs_lookup and hoare_lift_Pf on arch_state. Both cte_wp_at conjuncts are therefore inert to the proof. Dropping them both in one step yields the strongest additive strengthening.

## Delivery attestation

- mechanism: **named** → resolved **planned**
- delivery_state: pending (orphan after 8w with no consumer)
- target (agent claim): Future consumers of cap_swap_asid_map that currently carry the cte_wp_at preconditions can be simplified to call cap_swap_asid_map' instead.
- gate verdict: named-planned accepted (provisional, 8-week grace)

## What changed

See `patch.diff` (additive — original lemmas untouched). `range-patch.patch.txt` is the check-theory.sh range-replace input.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 0. delivery contract (§2.5) | ✓ |
| 1. check-theory.sh --patch | ✓ OK 47866 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline×gate | ✓ (0.8%) |
| 4. additive (no `_old` witness needed) | ✓ |

## Notes / follow-ups

- ⚠ delivery_state is **pending** (planned): a consumer/downstream must land within the 8-week grace period or `spec_delivery_lifecycle.py` will mark this **orphan** (design §6.2). Run that sweep periodically to advance the state.
