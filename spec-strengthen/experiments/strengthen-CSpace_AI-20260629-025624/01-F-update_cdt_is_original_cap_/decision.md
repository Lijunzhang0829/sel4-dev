# strengthen-CSpace_AI-20260629-025624 — update_cdt_is_original_cap

| Field | Value |
|---|---|
| Key | `F:CSpace_AI:update_cdt_is_original_cap` |
| Slot | F |
| Delivery | wp / realized |
| Delivery state | realized |
| Delivery target | — |
| File | `proof/invariant-abstract/CSpace_AI.thy` |
| Verdict | trial-passed (dry-run) |
| Impact verdict | additive |
| Δ wall (trial) | 1.0% |
| Walls | baseline=65050 ms · trial=65698 ms |

## Strengthening claim

Adds an explicit wp-registered frame for is_original_cap over update_cdt where none existed.

## Why this slot is real (agent rationale)

update_cdt is defined as set_cdt applied to a modified cdt; it touches only the cdt field. The file proves update_cdt_cdt[wp] at line 136 for the cdt field; is_original_cap is the natural sibling frame to add.

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
| 1. check-theory.sh --patch | ✓ OK 65698 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline×gate | ✓ (1.0%) |
| 4. additive (no `_old` witness needed) | ✓ |

## Notes / follow-ups

- delivery is **realized** — first-class, no follow-up needed.
