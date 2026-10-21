# strengthen-Tcb_AI-20260628-124151 — decode_unbind_notification_wf_strong

| Field | Value |
|---|---|
| Key | `P:Tcb_AI:decode_unbind_notification_wf_strong` |
| Slot | P |
| Delivery | named / planned |
| Delivery state | pending (grace 8w) |
| Delivery target | Any future caller of decode_unbind_notification_wf that cannot supply invs (e.g., a context where only the thread's cap witness is available) can use decode_unbind_notification_wf_strong directly via `rule` or `wp`. |
| File | `proof/invariant-abstract/Tcb_AI.thy` |
| Verdict | trial-passed (dry-run) |
| Impact verdict | additive |
| Δ wall (trial) | -13.3% |
| Walls | baseline=47149 ms · trial=40869 ms |

## Strengthening claim

(invs and tcb_at t and ex_nonz_cap_to t) ==> (tcb_at t and ex_nonz_cap_to t) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `invs`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

## Why this slot is real (agent rationale)

tcb_inv_wf (NotificationControl t None) unfolds to (tcb_at t and ex_nonz_cap_to t), so the only facts the proof needs are those two conjuncts. The wp chain uses only gbn_wp (a pure read-wp rule with no invs dependency) and wpc for case splits; the final clarsimp discharges the residual goal purely from the tcb_at t and ex_nonz_cap_to t hypotheses. invs never appears in the proof body and is not reachable by any implicit wp-chain dependency here. Dropping it yields a strictly weaker precondition with the original proof verbatim.

## Delivery attestation

- mechanism: **named** → resolved **planned**
- delivery_state: pending (orphan after 8w with no consumer)
- target (agent claim): Any future caller of decode_unbind_notification_wf that cannot supply invs (e.g., a context where only the thread's cap witness is available) can use decode_unbind_notification_wf_strong directly via `rule` or `wp`.
- gate verdict: named-planned accepted (provisional, 8-week grace)

## What changed

See `patch.diff` (additive — original lemmas untouched). `range-patch.patch.txt` is the check-theory.sh range-replace input.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 0. delivery contract (§2.5) | ✓ |
| 1. check-theory.sh --patch | ✓ OK 40869 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline×gate | ✓ (-13.3%) |
| 4. additive (no `_old` witness needed) | ✓ |

## Notes / follow-ups

- ⚠ delivery_state is **pending** (planned): a consumer/downstream must land within the 8-week grace period or `spec_delivery_lifecycle.py` will mark this **orphan** (design §6.2). Run that sweep periodically to advance the state.
