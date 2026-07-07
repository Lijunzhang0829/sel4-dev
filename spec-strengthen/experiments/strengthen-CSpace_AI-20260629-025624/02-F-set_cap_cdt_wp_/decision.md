# strengthen-CSpace_AI-20260629-025624 — set_cap_cdt_wp

| Field | Value |
|---|---|
| Key | `F:CSpace_AI:set_cap_cdt_wp` |
| Slot | F |
| Delivery | wp / realized |
| Delivery state | realized |
| Delivery target | — |
| File | `proof/invariant-abstract/CSpace_AI.thy` |
| Verdict | trial-passed (dry-run) |
| Impact verdict | additive |
| Δ wall (trial) | 1.5% |
| Walls | baseline=65050 ms · trial=65995 ms |

## Strengthening claim

Strictly stronger delivery: converts the existing monad-equality fact into a wp-registered Hoare triple usable directly by the wp tactic.

## Why this slot is real (agent rationale)

mdb_set_cap at line 885 already proves (as a monad fact) that set_cap preserves cdt. This adds the canonical Hoare-triple wp form so the frame fires automatically in wp tactic searches.

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
| 1. check-theory.sh --patch | ✓ OK 65995 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline×gate | ✓ (1.5%) |
| 4. additive (no `_old` witness needed) | ✓ |

## Notes / follow-ups

- delivery is **realized** — first-class, no follow-up needed.
