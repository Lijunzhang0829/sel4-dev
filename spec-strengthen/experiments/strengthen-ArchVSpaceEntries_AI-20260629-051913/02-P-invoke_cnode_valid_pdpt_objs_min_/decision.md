# strengthen-ArchVSpaceEntries_AI-20260629-051913 — invoke_cnode_valid_pdpt_objs_min

| Field | Value |
|---|---|
| Key | `P:ArchVSpaceEntries_AI:invoke_cnode_valid_pdpt_objs_min` |
| Slot | P |
| Delivery | named / planned |
| Delivery state | pending (grace 8w) |
| Delivery target | A maximally-weak precondition for invoke_cnode valid_pdpt_objs preservation; useful in contexts where no cnode validity is available |
| File | `proof/invariant-abstract/ARM/ArchVSpaceEntries_AI.thy` |
| Verdict | trial-passed (dry-run) |
| Impact verdict | additive |
| Δ wall (trial) | 0.3% |
| Walls | baseline=55083 ms · trial=55256 ms |

## Strengthening claim

(valid_pdpt_objs and invs and valid_cnode_inv i) ==> (valid_pdpt_objs) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `invs`; `valid_cnode_inv i`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

## Why this slot is real (agent rationale)

An independent drop of valid_cnode_inv i from invoke_cnode_valid_pdpt_objs. Since the [wp] lemmas for all component operations (cap_insert, cap_swap, empty_slot, cap_revoke, etc. — L92, L418, L604, L607) carry only valid_pdpt_objs as their precondition for this postcondition, the wp chain needs nothing from valid_cnode_inv. Neither invs nor valid_cnode_inv is textually consumed; the proof body is verbatim delegation. If this verifies it subsumes both hint 10 and hint 11 in a single, maximally-strong proposal.

## Delivery attestation

- mechanism: **named** → resolved **planned**
- delivery_state: pending (orphan after 8w with no consumer)
- target (agent claim): A maximally-weak precondition for invoke_cnode valid_pdpt_objs preservation; useful in contexts where no cnode validity is available
- gate verdict: named-planned accepted (provisional, 8-week grace)

## What changed

See `patch.diff` (additive — original lemmas untouched). `range-patch.patch.txt` is the check-theory.sh range-replace input.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 0. delivery contract (§2.5) | ✓ |
| 1. check-theory.sh --patch | ✓ OK 55256 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline×gate | ✓ (0.3%) |
| 4. additive (no `_old` witness needed) | ✓ |

## Notes / follow-ups

- ⚠ delivery_state is **pending** (planned): a consumer/downstream must land within the 8-week grace period or `spec_delivery_lifecycle.py` will mark this **orphan** (design §6.2). Run that sweep periodically to advance the state.
