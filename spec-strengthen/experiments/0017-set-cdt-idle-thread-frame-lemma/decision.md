# spec-0017 — add `set_cdt_idle_thread[wp]` frame lemma (seL4-source PR)

| Field | Value |
|---|---|
| **Variant** | seL4-source PR (rule 5 full record) |
| **Branch** | `spec-strengthen` |
| **Date** | 2026-06-02 |
| **Verdict** | applied |
| **Patch shape** | 2 (additive — Pattern G frame preservation) |
| **Impact verdict** | `additive` |
| **Acceptance** | PASS (all 4 gates) |
| **Base** | [[0014]] + [[0015]] + [[0016]] |

## What changed

`verification/l4v/proof/invariant-abstract/CSpace_AI.thy` — added a
`[wp]`-attributed frame lemma right after `set_cdt_cur_thread` from
[[0016]]:

```isabelle
lemma set_cdt_idle_thread[wp]:
  "\<lbrace>\<lambda>s. P (idle_thread s)\<rbrace> set_cdt m \<lbrace>\<lambda>_ s. P (idle_thread s)\<rbrace>"
  by (wpsimp simp: set_cdt_def)
```

Pattern G frame lemma for the literal `idle_thread` field. Completes
the basic threading-field frame triplet on `set_cdt`:
`machine_state` ([[0015]]), `cur_thread` ([[0016]]), `idle_thread`
(here).

l4v submodule HEAD at baseline: `00d9073f70d0`.

## Why this is useful — gap with existing companion

Existing `set_cdt_idle [wp]` (CSpace_AI.thy:3789, attribute present
under a different name) covers the `valid_idle` predicate, but no
rule existed for the literal `idle_thread` field. Adding this fills
the gap for any proof that needs to commute a generic
`P (idle_thread s)` postcondition past `set_cdt`.

`idle_thread` is referenced in:
- Scheduler proofs (DetSchedSchedule_AI.thy).
- IRQ-handling paths that distinguish idle vs running thread.
- Various invariant compositions like `invs` that include
  `valid_idle` (predicate over `kheap (idle_thread s)`).

This lemma lets all of those discharge their `set_cdt`-cross
frame automatically via wp.

## Why shape 2 (additive)

Brand new. `spec_witness_gen.py` confirmed shape 2 — no witness.

## Acceptance gate trace

1. `check-theory.sh --patch` OK 47192 ms; re-verify at `--apply`
   OK 47460 ms. ✓
2. Impact verdict `additive`. ✓
3. Trial wall **−0.4%** vs baseline 47361 ms — essentially flat. ✓
4. Parent SKILL hard rules inherited. ✓

## Impact on seL4 (上下游)

### Same-file wall — measured flat

| Phase | Wall | Source |
|---|---:|---|
| baseline (post [[0014]]+[[0015]]+[[0016]]) | 47,361 ms | check-theory.sh |
| trial | 47,192 ms | --patch |
| apply re-verification | 47,460 ms | --apply |
| delta (trial vs baseline) | **−0.4%** | flat |

The flat result reinforces the [[0015]]/[[0016]] pattern: each
additional `[wp]` rule extends wp's discharge capabilities; for
state components rarely referenced in the SAME file as the rule
itself, the in-file speedup is small. Downstream pickup is the
real win.

Note: CSpace_AI's wall has settled at ~47-49 sec after 4 applied
strengthenings (started at 45 sec pre-0014). Net cost over baseline:
+5%. Each individual `--patch` trial was within −12.5% to +8.7%.
The cumulative wall hasn't trended monotonically; wp-class pickup
periodically offsets the parsing cost of new lemmas.

### Cross-file consumers

`spec_impact.py`: 0 lines / 0 files (brand new). Downstream pickup
expected in scheduler / IRQ-handling proofs that reference
`idle_thread` across `set_cdt`-using sequences.

### Cross-session — NOT rebuilt

Same justification as [[0015]] / [[0016]]: zero breakage upper
bound; ROI quantification deferred to a future Refine rebuild.

## PR description fields

| Field | Value |
|---|---|
| Lemma | `set_cdt_idle_thread[wp]` (new) |
| File | `verification/l4v/proof/invariant-abstract/CSpace_AI.thy` (line 3859 post-apply) |
| Baseline wall | `47,361 ms` |
| Trial wall from `--apply` | `47,460 ms` (`--patch` trial: `47,192 ms`) |
| Experiment ID | `0017-set-cdt-idle-thread-frame-lemma` |

## Notes / follow-ups

- This completes the threading-field Pattern G triplet for
  `set_cdt`: `machine_state` ([[0015]]), `cur_thread` ([[0016]]),
  `idle_thread` (this PR). Any future scheduler / IRQ proof that
  composes over `set_cdt` now gets the three most-relevant frame
  preservations for free via wp.
- Both [[0016]] and this experiment show much smaller wall
  improvements than [[0015]]'s −11.3%. The asymmetry suggests
  [[0015]]'s `machine_state` was particularly under-served by the
  prior wp class — worth recording as a follow-up case study in
  the playbook ("which fields most need their literal frame
  lemma").
- l4v submodule pointer unchanged.
- Probe note: shape 2 candidates don't pass through the premise
  probe; they're selected by inspection. The new TRIAL-based
  probe ([[0011]] revised) was used pre-experiment to filter
  unused-premise candidates (`get_rs_real_cte_at` / `valid_objs`
  → load-bearing → skipped), but Pattern G additions don't fit
  that gate.
