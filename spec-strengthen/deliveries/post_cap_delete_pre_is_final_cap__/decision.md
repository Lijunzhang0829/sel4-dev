# strengthen-ArchCNodeInv_AI-20260629-040253 — post_cap_delete_pre_is_final_cap''

| Field | Value |
|---|---|
| Key | `P:ArchCNodeInv_AI:post_cap_delete_pre_is_final_cap''` |
| Slot | P |
| Delivery | named / planned |
| Delivery state | pending (grace 8w) |
| Delivery target | The call site in rec_del_invs'' (L637) applies post_cap_delete_pre_is_final_cap' after extracting invs; a version without valid_ioports would allow dropping the valid_ioports extraction step there. |
| File | `proof/invariant-abstract/ARM/ArchCNodeInv_AI.thy` |
| Verdict | trial-passed (dry-run) |
| Impact verdict | additive |
| Δ wall (trial) | 1.1% |
| Walls | baseline=47504 ms · trial=48019 ms |

## Strengthening claim

(caps_of_state s slot = Some cap \<and> is_final_cap' cap s \<and> cap_cleanup_opt cap \<noteq> NullCap) ==> (valid_ioports s \<and> caps_of_state s slot = Some cap \<and> is_final_cap' cap s \<and> cap_cleanup_opt cap \<noteq> NullCap)

## Why this slot is real (agent rationale)

The proof of post_cap_delete_pre_is_final_cap' never references valid_ioports: it unfolds cap_cleanup_opt_def/post_cap_delete_pre_def and dispatches the IRQHandlerCap case via final_cap_duplicate_irq, which takes caps_of_state and is_final_cap' but not valid_ioports. The valid_ioports hypothesis is inert to the proof.

## Delivery attestation

- mechanism: **named** → resolved **planned**
- delivery_state: pending (orphan after 8w with no consumer)
- target (agent claim): The call site in rec_del_invs'' (L637) applies post_cap_delete_pre_is_final_cap' after extracting invs; a version without valid_ioports would allow dropping the valid_ioports extraction step there.
- gate verdict: named-planned accepted (provisional, 8-week grace)

## What changed

See `patch.diff` (additive — original lemmas untouched). `range-patch.patch.txt` is the check-theory.sh range-replace input.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 0. delivery contract (§2.5) | ✓ |
| 1. check-theory.sh --patch | ✓ OK 48019 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline×gate | ✓ (1.1%) |
| 4. additive (no `_old` witness needed) | ✓ |

## Notes / follow-ups

- ⚠ delivery_state is **pending** (planned): a consumer/downstream must land within the 8-week grace period or `spec_delivery_lifecycle.py` will mark this **orphan** (design §6.2). Run that sweep periodically to advance the state.
