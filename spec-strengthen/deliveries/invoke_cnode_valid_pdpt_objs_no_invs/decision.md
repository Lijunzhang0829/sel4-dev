# strengthen-ArchVSpaceEntries_AI-20260629-051913 — invoke_cnode_valid_pdpt_objs_no_invs

| Field | Value |
|---|---|
| Key | `P:ArchVSpaceEntries_AI:invoke_cnode_valid_pdpt_objs_no_invs` |
| Slot | P |
| Delivery | named / planned |
| Delivery state | pending (grace 8w) |
| Delivery target | A strengthened version of invoke_cnode_valid_pdpt_objs usable wherever valid_pdpt_objs preservation is needed without an invs requirement; e.g. future refactors of handle_invocation callers |
| File | `proof/invariant-abstract/ARM/ArchVSpaceEntries_AI.thy` |
| Verdict | trial-passed (dry-run) |
| Impact verdict | additive |
| Δ wall (trial) | -0.7% |
| Walls | baseline=55083 ms · trial=54723 ms |

## Strengthening claim

(valid_pdpt_objs and invs and valid_cnode_inv i) ==> (valid_pdpt_objs and valid_cnode_inv i) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `invs`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

## Why this slot is real (agent rationale)

All operations invoked inside invoke_cnode (cap_insert, cap_swap_for_delete, empty_slot, cap_delete, cap_move, etc.) are already proven [wp] for valid_pdpt_objs with only valid_pdpt_objs in their preconditions (L92, L607, L418). The proof fully delegates to those [wp] lemmas via a crunch-style wp chain; invs never appears in the proof text and no consumed conjunct is identified, making it a pure opaque delegation. The decode op classification (read/decode shape) further supports droppability.

## Delivery attestation

- mechanism: **named** → resolved **planned**
- delivery_state: pending (orphan after 8w with no consumer)
- target (agent claim): A strengthened version of invoke_cnode_valid_pdpt_objs usable wherever valid_pdpt_objs preservation is needed without an invs requirement; e.g. future refactors of handle_invocation callers
- gate verdict: named-planned accepted (provisional, 8-week grace)

## What changed

See `patch.diff` (additive — original lemmas untouched). `range-patch.patch.txt` is the check-theory.sh range-replace input.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 0. delivery contract (§2.5) | ✓ |
| 1. check-theory.sh --patch | ✓ OK 54723 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline×gate | ✓ (-0.7%) |
| 4. additive (no `_old` witness needed) | ✓ |

## Notes / follow-ups

- ⚠ delivery_state is **pending** (planned): a consumer/downstream must land within the 8-week grace period or `spec_delivery_lifecycle.py` will mark this **orphan** (design §6.2). Run that sweep periodically to advance the state.
