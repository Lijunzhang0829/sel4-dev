# 0050-set-cap-domain-index-frame-lemma

| Field | Value |
|---|---|
| Pattern | G |
| Key | `G:CSpaceInv_AI:set_cap:domain_index` |
| Variant | seL4-source PR (rule 5 full record) |
| Branch | spec-strengthen |
| Date | 2026-06-09 |
| File | `verification/l4v/proof/invariant-abstract/CSpaceInv_AI.thy` |
| Verdict | applied |
| Impact verdict | additive |
| Δ wall (trial) | -0.4% |
| Walls | baseline=28307 ms · trial=28196 ms · apply=28881 ms |

## What changed

See `patch.diff` in this directory. The unified diff is the
canonical replayable record.

## Reference companion(s)

(TODO: list the existing companion lemma(s) in the same family that
motivated this addition, with file/line references.)

## Strengthening claim

(TODO: explain why the new lemma is strictly stronger than the
reference companion. The relationship may be:
- a clean field instantiation (P := <predicate>) — common but NOT
  universal
- or a meta-level argument requiring an explicit proof sketch

Don't assume the field instantiation always works; verify the
entailment manually before claiming strict strengthening.)

## Acceptance gate trace

| Gate | Result |
|---|---|
| 1. check-theory.sh --patch | ✓ OK 28196 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline × 1.30 | ✓ (-0.4%) |
| 4. parent SKILL hard rules | ✓ |

## Impact on seL4 (上下游)

(TODO: same-file wall delta interpretation; cross-file consumer
count from measurement.json; cross-session deferred per policy.)

## Notes / follow-ups

(TODO)
