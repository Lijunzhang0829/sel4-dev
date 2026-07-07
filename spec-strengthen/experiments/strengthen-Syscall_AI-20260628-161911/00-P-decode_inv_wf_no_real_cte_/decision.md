# strengthen-Syscall_AI-20260628-161911 — decode_inv_wf_no_real_cte

| Field | Value |
|---|---|
| Key | `P:Syscall_AI:decode_inv_wf_no_real_cte` |
| Slot | P |
| Delivery | named / planned |
| Delivery state | pending (grace 8w) |
| Delivery target | Any caller of decode_inv_wf that does not hold real_cte_at slot in its precondition context (e.g. future handle_invocation refinements); wire in a follow-up patch. |
| File | `proof/invariant-abstract/Syscall_AI.thy` |
| Verdict | trial-passed (dry-run) |
| Impact verdict | additive |
| Δ wall (trial) | -8.3% |
| Walls | baseline=86011 ms · trial=78889 ms |

## Strengthening claim

(valid_cap cap and invs and cte_wp_at ((=) cap) slot
           and real_cte_at slot
           and ex_cte_cap_to slot
           and (\<lambda>s::'state_ext state. \<forall>r\<in>zobj_refs cap. ex_nonz_cap_to r s)
           and (\<lambda>s. \<forall>r\<in>cte_refs cap (interrupt_irq_node s). ex_cte_cap_to r s)
           and (\<lambda>s. \<forall>cap \<in> set excaps. \<forall>r\<in>cte_refs (fst cap) (interrupt_irq_node s). ex_cte_cap_to r s)
           and (\<lambda>s. \<forall>x \<in> set excaps. s \<turnstile> (fst x))
           and (\<lambda>s. \<forall>x \<in> set excaps. \<forall>r\<in>zobj_refs (fst x). ex_nonz_cap_to r s)
           and (\<lambda>s. \<forall>x \<in> set excaps. cte_wp_at ((=) (fst x)) (snd x) s)
           and (\<lambda>s. \<forall>x \<in> set excaps. real_cte_at (snd x) s)
           and (\<lambda>s. \<forall>x \<in> set excaps. ex_cte_cap_wp_to is_cnode_cap (snd x) s)
           and (\<lambda>s. \<forall>x \<in> set excaps. cte_wp_at (interrupt_derived (fst x)) (snd x) s)) ==> (valid_cap cap and invs and cte_wp_at ((=) cap) slot
           and ex_cte_cap_to slot
           and (\<lambda>s::'state_ext state. \<forall>r\<in>zobj_refs cap. ex_nonz_cap_to r s)
           and (\<lambda>s. \<forall>r\<in>cte_refs cap (interrupt_irq_node s). ex_cte_cap_to r s)
           and (\<lambda>s. \<forall>cap \<in> set excaps. \<forall>r\<in>cte_refs (fst cap) (interrupt_irq_node s). ex_cte_cap_to r s)
           and (\<lambda>s. \<forall>x \<in> set excaps. s \<turnstile> (fst x))
           and (\<lambda>s. \<forall>x \<in> set excaps. \<forall>r\<in>zobj_refs (fst x). ex_nonz_cap_to r s)
           and (\<lambda>s. \<forall>x \<in> set excaps. cte_wp_at ((=) (fst x)) (snd x) s)
           and (\<lambda>s. \<forall>x \<in> set excaps. real_cte_at (snd x) s)
           and (\<lambda>s. \<forall>x \<in> set excaps. ex_cte_cap_wp_to is_cnode_cap (snd x) s)
           and (\<lambda>s. \<forall>x \<in> set excaps. cte_wp_at (interrupt_derived (fst x)) (snd x) s)) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `real_cte_at slot`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

## Why this slot is real (agent rationale)

The conjunct `real_cte_at slot` does not appear in any tactic in the proof body (no `real_cte_at`, `real_cte_at_def`, etc.). The op is `decode_invocation`, a read/decode operation whose wp-chain decomposition goes through `decode_tcb_inv_wf` and `decode_domain_inv_wf`; neither visibly consumes a slot-validity predicate about the caller's own slot. The remaining conjuncts (`invs`, `cte_wp_at ((=) cap) slot`, `ex_cte_cap_to slot`) are consumed explicitly in the clarsimp/eq_no_cap_to_obj_with_diff_ref steps.

## Delivery attestation

- mechanism: **named** → resolved **planned**
- delivery_state: pending (orphan after 8w with no consumer)
- target (agent claim): Any caller of decode_inv_wf that does not hold real_cte_at slot in its precondition context (e.g. future handle_invocation refinements); wire in a follow-up patch.
- gate verdict: named-planned accepted (provisional, 8-week grace)

## What changed

See `patch.diff` (additive — original lemmas untouched). `range-patch.patch.txt` is the check-theory.sh range-replace input.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 0. delivery contract (§2.5) | ✓ |
| 1. check-theory.sh --patch | ✓ OK 78889 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline×gate | ✓ (-8.3%) |
| 4. additive (no `_old` witness needed) | ✓ |

## Notes / follow-ups

- ⚠ delivery_state is **pending** (planned): a consumer/downstream must land within the 8-week grace period or `spec_delivery_lifecycle.py` will mark this **orphan** (design §6.2). Run that sweep periodically to advance the state.
