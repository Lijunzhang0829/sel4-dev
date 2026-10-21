# strengthen-ArchTcb_AI-20260629-043932 — same_object_also_valid_no_asid_base

| Field | Value |
|---|---|
| Key | `P:ArchTcb_AI:same_object_also_valid_no_asid_base` |
| Slot | P |
| Delivery | named / planned |
| Delivery state | pending (grace 8w) |
| Delivery target | Future callers of same_object_also_valid that do not have cap_asid_base cap = None in context |
| File | `proof/invariant-abstract/ARM/ArchTcb_AI.thy` |
| Verdict | trial-passed (dry-run) |
| Impact verdict | additive |
| Δ wall (trial) | 0.5% |
| Walls | baseline=93808 ms · trial=94258 ms |

## Strengthening claim

(same_object_as cap cap' \<and> s \<turnstile> cap' \<and> wellformed_cap cap \<and> cap_asid_cond \<and> cap_vptr cap = None) ==> (same_object_as cap cap' \<and> s \<turnstile> cap' \<and> wellformed_cap cap \<and> cap_asid_cond \<and> cap_vptr cap = None \<and> cap_asid_base cap = None)

## Why this slot is real (agent rationale)

The proof body of same_object_also_valid never mentions cap_asid_base anywhere — not in any simp lemma name, frule, drule, or clarsimp hint. Dropping it yields a strictly stronger implication lemma. The proof body is copied verbatim.

## Delivery attestation

- mechanism: **named** → resolved **planned**
- delivery_state: pending (orphan after 8w with no consumer)
- target (agent claim): Future callers of same_object_also_valid that do not have cap_asid_base cap = None in context
- gate verdict: named-planned accepted (provisional, 8-week grace)

## What changed

See `patch.diff` (additive — original lemmas untouched). `range-patch.patch.txt` is the check-theory.sh range-replace input.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 0. delivery contract (§2.5) | ✓ |
| 1. check-theory.sh --patch | ✓ OK 94258 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline×gate | ✓ (0.5%) |
| 4. additive (no `_old` witness needed) | ✓ |

## Notes / follow-ups

- ⚠ delivery_state is **pending** (planned): a consumer/downstream must land within the 8-week grace period or `spec_delivery_lifecycle.py` will mark this **orphan** (design §6.2). Run that sweep periodically to advance the state.
