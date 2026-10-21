# strengthen-CSpace_AI-20260629-025624 — set_cap_is_original_cap_wp

| Field | Value |
|---|---|
| Key | `F:CSpace_AI:set_cap_is_original_cap_wp` |
| Slot | F |
| Delivery | wp / realized |
| Delivery state | realized |
| Delivery target | — |
| File | `proof/invariant-abstract/CSpace_AI.thy` |
| Verdict | trial-passed (dry-run) |
| Impact verdict | additive |
| Δ wall (trial) | 1.6% |
| Walls | baseline=65050 ms · trial=66107 ms |

## Strengthening claim

Strictly stronger delivery: converts the existing monad-equality fact into a wp-registered Hoare triple for is_original_cap over set_cap.

## Why this slot is real (agent rationale)

is_original_cap_set_cap at line 879 proves preservation as a monad equality. This converts it to a wp-registered Hoare triple, mirroring the pattern of set_cap_cdt_wp for the is_original_cap field.

## Delivery attestation

- mechanism: **wp** → resolved **realized**
- delivery_state: realized
- target (agent claim): —
- gate verdict: F+wp accepted (frame-lemma default)

## What changed

See `patch.diff` (additive — original lemmas untouched). `range-patch.patch.txt` is the check-theory.sh range-replace input.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 0. delivery contract (§2.5) | ✓ |
| 1. check-theory.sh --patch | ✓ OK 66107 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline×gate | ✓ (1.6%) |
| 4. additive (no `_old` witness needed) | ✓ |

## Notes / follow-ups

- delivery is **realized** — first-class, no follow-up needed.
