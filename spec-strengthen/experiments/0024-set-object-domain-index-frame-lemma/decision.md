# 0024-set-object-domain-index-frame-lemma

| Field | Value |
|---|---|
| Pattern | G |
| Key | `G:KHeap_AI:set_object:domain_index` |
| Variant | seL4-source PR (rule 5 full record) |
| Branch | spec-strengthen |
| Date | 2026-06-08 |
| File | `verification/l4v/proof/invariant-abstract/KHeap_AI.thy` |
| Verdict | applied |
| Impact verdict | additive |
| Δ wall (trial) | -1.8% |
| Walls | baseline=26528 ms · trial=26042 ms · apply=24906 ms |

## What changed

See `patch.diff` in this directory. The unified diff is the
canonical replayable record.

## Reference companion(s)

Existing `set_object_<field>[wp]` literal-field frames in
KHeap_AI.thy when this PR was applied:

- `set_object_machine_state[wp]` (line 1277, original)
- `set_object_cdt[wp]` (0019)
- `set_object_cur_thread[wp]` (0020)
- `set_object_cur_domain[wp]` (0021)
- `set_object_arch_state[wp]` (0022)
- `set_object_is_original_cap[wp]` (line ~1432, original)

This adds the same shape for `domain_index`, the scheduler-domain
counter field.

## Strengthening claim

`set_object` writes only the `kheap` field; `domain_index` is a
separate record component and flows through unchanged. The new
lemma states this for any `P (domain_index s)`.

Since there is no existing predicate companion (`set_object_<X>`
where `<X>` is a property over `domain_index`), the "old spec" for
this field is **empty** on `set_object`. The strengthening goes
from "no public 承诺" to "任意 P 保持" — strict addition of a
public fact rather than field-level subsuming a predicate-level
companion.

This is the limiting case where the field-instantiation
meta-entailment doesn't apply (no predicate to instantiate);
strict-strengthening status rests on "old spec is empty for this
field on set_object" + "new lemma is provable from set_object_def
record-update simp lemmas."

## Acceptance gate trace

| Gate | Result |
|---|---|
| 1. check-theory.sh --patch | ✓ OK 26042 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline × 1.30 | ✓ (-1.8%) |
| 4. parent SKILL hard rules | ✓ |

## Impact on seL4 (上下游)

| Phase | Wall | Notes |
|---|---:|---|
| baseline | 26,528 ms | KHeap_AI after 0019-0022 applied |
| trial (with patch) | 26,042 ms | −1.8% — wp-class pickup on a small set of in-file goal sites |
| apply (re-verifies) | 24,906 ms | further down (heap warming/noise) |

Same-file Δ −1.8%: a small net speedup from `[wp]`-class pickup,
similar to 0021 (`cur_domain` −4.6%) and 0022 (`arch_state` −2.0%).
`domain_index` references in KHeap_AI itself are uncommon; the
likely real downstream value is in scheduler invariants
(DetSchedSchedule_AI.thy) that need a polymorphic `P (domain_index s)`
to commute past `set_object`-using chains.

`spec_impact.py` Tier-2 grep: 0 consumers / 0 files (brand new
lemma).

Cross-session: NOT rebuilt. Additive `[wp]` rules cannot break
downstream proofs.

## Notes / follow-ups

- First-half of the remaining `set_object_<field>` cleanup
  predicted by the post-0022 survey. [[0025]] fills `domain_time`,
  closing the survey's 2 truly clean slots.
- This was the first apply via the new `spec_strengthen_run.sh
  execute --candidate G:...` end-to-end automation path on a
  candidate that came **fresh** from a survey (not backfilled).
  ~3 min wall from candidate-pick to audit-dir-built.
