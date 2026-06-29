# strengthen-ArchAInvsPre-20260629-050242 — some_get_page_info_umapsD_no_valid_objs

| Field | Value |
|---|---|
| Key | `P:ArchAInvsPre:some_get_page_info_umapsD_no_valid_objs` |
| Slot | P |
| Delivery | named / planned |
| Delivery state | pending (grace 8w) |
| Delivery target | Future callers of some_get_page_info_umapsD can switch to the weaker precondition; ptable_rights_imp_frame is a candidate consumer. |
| File | `proof/invariant-abstract/ARM/ArchAInvsPre.thy` |
| Verdict | trial-passed (dry-run) |
| Impact verdict | additive |
| Δ wall (trial) | 5.0% |
| Walls | baseline=33439 ms · trial=35102 ms |

## Strengthening claim

(get_page_info ... = Some ... \<and> (\<exists>\<rhd> pd_ref) s \<and> p \<notin> kernel_mappings \<and> valid_vspace_objs s \<and> pspace_aligned s \<and> valid_asid_table ... s) ==> (get_page_info ... = Some ... \<and> (\<exists>\<rhd> pd_ref) s \<and> p \<notin> kernel_mappings \<and> valid_vspace_objs s \<and> pspace_aligned s \<and> valid_asid_table ... s \<and> valid_objs s)

## Why this slot is real (agent rationale)

The premise `valid_objs s` does not appear anywhere in the proof body of some_get_page_info_umapsD (lines 118-164). The proof drives the result through valid_vspace_objsD and stronger_vspace_objsD (which require valid_vspace_objs and reachability), obj_bits_data_at (which only needs data_at), and pspace_aligned_def (which needs pspace_aligned). None of these visibly require valid_objs as a side condition.

## Delivery attestation

- mechanism: **named** → resolved **planned**
- delivery_state: pending (orphan after 8w with no consumer)
- target (agent claim): Future callers of some_get_page_info_umapsD can switch to the weaker precondition; ptable_rights_imp_frame is a candidate consumer.
- gate verdict: named-planned accepted (provisional, 8-week grace)

## What changed

See `patch.diff` (additive — original lemmas untouched). `range-patch.patch.txt` is the check-theory.sh range-replace input.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 0. delivery contract (§2.5) | ✓ |
| 1. check-theory.sh --patch | ✓ OK 35102 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline×gate | ✓ (5.0%) |
| 4. additive (no `_old` witness needed) | ✓ |

## Notes / follow-ups

- ⚠ delivery_state is **pending** (planned): a consumer/downstream must land within the 8-week grace period or `spec_delivery_lifecycle.py` will mark this **orphan** (design §6.2). Run that sweep periodically to advance the state.
