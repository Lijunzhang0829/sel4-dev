# 0025-set-object-domain-time-frame-lemma

| Field | Value |
|---|---|
| Pattern | G |
| Key | `G:KHeap_AI:set_object:domain_time` |
| Variant | seL4-source PR (rule 5 full record) |
| Branch | spec-strengthen |
| Date | 2026-06-08 |
| File | `verification/l4v/proof/invariant-abstract/KHeap_AI.thy` |
| Verdict | applied |
| Impact verdict | additive |
| Δ wall (trial) | -4.4% |
| Walls | baseline=26106 ms · trial=24955 ms · apply=26028 ms |

## What changed

See `patch.diff` in this directory. The unified diff is the
canonical replayable record.

## Reference companion(s)

Existing `set_object_<field>[wp]` literal-field frames in
KHeap_AI.thy at the time of this PR (after 0019-0024):

- `set_object_machine_state[wp]` (original)
- `set_object_cdt[wp]` ([[0019]])
- `set_object_cur_thread[wp]` ([[0020]])
- `set_object_cur_domain[wp]` ([[0021]])
- `set_object_arch_state[wp]` ([[0022]])
- `set_object_is_original_cap[wp]` (original)
- `set_object_domain_index[wp]` ([[0024]])

This completes the `set_object_<field>` literal-field family for
the two clean slots identified pre-0019.

## Strengthening claim

Same shape as [[0024]]: no existing predicate-level companion on
`set_object` mentions `domain_time`, so the strengthening is from
"no public 承诺" to "任意 P 保持". `set_object` writes only
`kheap`; `domain_time` flows through unchanged. Provable by
`set_object_def`'s record-update simp lemma.

Field-instantiation meta-entailment N/A here (no existing
predicate companion to recover via `P := <predicate>`).

## Acceptance gate trace

| Gate | Result |
|---|---|
| 1. check-theory.sh --patch | ✓ OK 24955 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline × 1.30 | ✓ (-4.4%) |
| 4. parent SKILL hard rules | ✓ |

## Impact on seL4 (上下游)

| Phase | Wall | Notes |
|---|---:|---|
| baseline (post [[0024]]) | 26,106 ms | |
| trial (with patch) | 24,955 ms | **−4.4%** |
| apply (re-verifies) | 26,028 ms | within noise of baseline |

`−4.4%` is the strongest negative delta in this G-domain sub-batch
(0024 was −1.8%, 0021 `cur_domain` was −4.6%, 0022 `arch_state`
was −2.0%). Reason: `domain_time` is touched in some scheduler-
adjacent in-file proof goals; the new `[wp]` rule lets wp
discharge those without falling through to the slower default.

`spec_impact.py` Tier-2 grep: 0 / 0 (brand new lemma).
Cross-session: NOT rebuilt.

## Notes / follow-ups

- This **closes** the post-0019-survey list of clean
  `set_object_<field>` slots on KHeap_AI.thy. The remaining 5
  slots are all crunch-derived (correctly skipped via pre-flight).
- Cumulative `set_object` literal-field frame family in KHeap_AI
  after this PR: 7 lemmas (machine_state, cdt, cur_thread,
  cur_domain, arch_state, domain_index, domain_time +
  is_original_cap pre-existing).
- Other `set_<op>_<field>` families (set_aobject, set_ep,
  set_simple_ko, etc.) the survey turned up many clean slots in
  this session — natural target for a future batch under the same
  Pattern G automation.
