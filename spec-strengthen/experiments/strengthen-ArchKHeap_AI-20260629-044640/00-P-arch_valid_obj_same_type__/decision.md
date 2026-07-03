# strengthen-ArchKHeap_AI-20260629-044640 — arch_valid_obj_same_type'

| Field | Value |
|---|---|
| Key | `P:ArchKHeap_AI:arch_valid_obj_same_type'` |
| Slot | P |
| Delivery | named / planned |
| Delivery state | pending (grace 8w) |
| Delivery target | Future callers of arch_valid_obj_same_type that do not have a_type equality available |
| File | `proof/invariant-abstract/ARM/ArchKHeap_AI.thy` |
| Verdict | trial-passed (dry-run) |
| Impact verdict | additive |
| Δ wall (trial) | -5.5% |
| Walls | baseline=43934 ms · trial=41505 ms |

## Strengthening claim

(arch_valid_obj ao s \<and> kheap s p = Some ko) \<Longrightarrow> (arch_valid_obj ao s \<and> kheap s p = Some ko \<and> a_type k = a_type ko)

## Why this slot is real (agent rationale)

The scanner flags 'NO conjunct visibly consumed' — the strongest signal. The proof uses only `induction ao` and `clarsimp simp: typ_at_same_type`. For all ARM arch_kernel_obj constructors (ASIDPool, PageTable, PageDirectory, DataPage), arch_valid_obj unfolds to conditions on entries that are independent of the type of the replacement object k. If typ_at_same_type is used purely as a rewrite to equate typ_at before/after the kheap update, it may fire without needing a_type equality as a hypothesis (relying instead on the kheap membership fact). The trial will confirm.

## Delivery attestation

- mechanism: **named** → resolved **planned**
- delivery_state: pending (orphan after 8w with no consumer)
- target (agent claim): Future callers of arch_valid_obj_same_type that do not have a_type equality available
- gate verdict: named-planned accepted (provisional, 8-week grace)

## What changed

See `patch.diff` (additive — original lemmas untouched). `range-patch.patch.txt` is the check-theory.sh range-replace input.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 0. delivery contract (§2.5) | ✓ |
| 1. check-theory.sh --patch | ✓ OK 41505 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline×gate | ✓ (-5.5%) |
| 4. additive (no `_old` witness needed) | ✓ |

## Notes / follow-ups

- ⚠ delivery_state is **pending** (planned): a consumer/downstream must land within the 8-week grace period or `spec_delivery_lifecycle.py` will mark this **orphan** (design §6.2). Run that sweep periodically to advance the state.
