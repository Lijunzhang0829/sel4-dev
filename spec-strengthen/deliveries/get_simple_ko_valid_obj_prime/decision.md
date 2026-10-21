# strengthen-KHeap_AI-20260628-210729 — get_simple_ko_valid_obj'

| Field | Value |
|---|---|
| Key | `P:KHeap_AI:get_simple_ko_valid_obj'` |
| Slot | P |
| Delivery | named / planned |
| Delivery state | pending (grace 8w) |
| Delivery target | Callers of get_simple_ko_valid_obj that already hold valid_objs but cannot guarantee obj_at (bound ...) — e.g. sites that call get_simple_ko on an endpoint whose existence is not yet established. |
| File | `proof/invariant-abstract/KHeap_AI.thy` |
| Verdict | trial-passed (dry-run) |
| Impact verdict | additive |
| Δ wall (trial) | -7.5% |
| Walls | baseline=47337 ms · trial=43793 ms |

## Strengthening claim

(valid_objs and obj_at (\<lambda>ko. bound (partial_inv f ko)) ep) ==> (valid_objs) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `obj_at (\<lambda>ko. bound (partial_inv f ko)) ep`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

## Why this slot is real (agent rationale)

get_simple_ko is a read/decode operation. If the object at ep does not satisfy bound (partial_inv f ko) then get_simple_ko assert-fails, making the Hoare triple vacuously true. When get_simple_ko does succeed, get_object_valid already supplies valid_obj ep ko, and the partial_inv/the_equality rewriting in the proof relates ko to f r — no use of the obj_at conjunct is made. The original proof body copies verbatim: the hoare_pre_imp step only needs to discharge valid_objs s => valid_objs s (trivial), and the final wpsimp still closes the continuation goal.

## Delivery attestation

- mechanism: **named** → resolved **planned**
- delivery_state: pending (orphan after 8w with no consumer)
- target (agent claim): Callers of get_simple_ko_valid_obj that already hold valid_objs but cannot guarantee obj_at (bound ...) — e.g. sites that call get_simple_ko on an endpoint whose existence is not yet established.
- gate verdict: named-planned accepted (provisional, 8-week grace)

## What changed

See `patch.diff` (additive — original lemmas untouched). `range-patch.patch.txt` is the check-theory.sh range-replace input.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 0. delivery contract (§2.5) | ✓ |
| 1. check-theory.sh --patch | ✓ OK 43793 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline×gate | ✓ (-7.5%) |
| 4. additive (no `_old` witness needed) | ✓ |

## Notes / follow-ups

- ⚠ delivery_state is **pending** (planned): a consumer/downstream must land within the 8-week grace period or `spec_delivery_lifecycle.py` will mark this **orphan** (design §6.2). Run that sweep periodically to advance the state.
