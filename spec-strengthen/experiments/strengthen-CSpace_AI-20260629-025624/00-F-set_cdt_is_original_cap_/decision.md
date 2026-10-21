# strengthen-CSpace_AI-20260629-025624 — set_cdt_is_original_cap

| Field | Value |
|---|---|
| Key | `F:CSpace_AI:set_cdt_is_original_cap` |
| Slot | F |
| Delivery | wp / realized |
| Delivery state | realized |
| Delivery target | — |
| File | `proof/invariant-abstract/CSpace_AI.thy` |
| Verdict | trial-passed (dry-run) |
| Impact verdict | additive |
| Δ wall (trial) | 0.2% |
| Walls | baseline=65050 ms · trial=65193 ms |

## Strengthening claim

Strictly stronger than any implicit top-precondition invariance; adds an explicit wp-registered frame for is_original_cap over set_cdt where none existed.

## Why this slot is real (agent rationale)

set_cdt only updates the cdt field via modify; is_original_cap is unaffected. The file already has set_cdt_cdt_update[wp] at line 432 mirroring this pattern; is_original_cap is a natural sibling field to frame.

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
| 1. check-theory.sh --patch | ✓ OK 65193 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline×gate | ✓ (0.2%) |
| 4. additive (no `_old` witness needed) | ✓ |

## Notes / follow-ups

- delivery is **realized** — first-class, no follow-up needed.
