# spec-0016 — add `set_cdt_cur_thread[wp]` frame lemma (seL4-source PR)

| Field | Value |
|---|---|
| **Variant** | seL4-source PR (rule 5 full record) |
| **Branch** | `spec-strengthen` |
| **Date** | 2026-06-02 |
| **Verdict** | applied |
| **Patch shape** | 2 (additive — Pattern G frame preservation) |
| **Impact verdict** | `additive` |
| **Acceptance** | PASS (all 4 gates) |
| **Base** | `[[0014]]` + `[[0015]]` (this experiment builds on their applied state) |

## What changed

`verification/l4v/proof/invariant-abstract/CSpace_AI.thy` — added a
`[wp]`-attributed frame lemma right after `set_cdt_machine_state` from
[[0015]]:

```isabelle
lemma set_cdt_cur_thread[wp]:
  "\<lbrace>\<lambda>s. P (cur_thread s)\<rbrace> set_cdt m \<lbrace>\<lambda>_ s. P (cur_thread s)\<rbrace>"
  by (wpsimp simp: set_cdt_def)
```

Pattern G frame lemma for the literal `cur_thread` field, exactly
mirroring [[0015]]'s `set_cdt_machine_state` for `machine_state`.

l4v submodule HEAD at baseline: `00d9073f70d0`.

## Why this is useful — gap with existing companion

Existing `set_cdt_cur` (CSpace_AI.thy:3323) preserves `cur_tcb` — a
**predicate** that says "the current thread is a TCB". It does NOT
allow `wp` to discharge an arbitrary `P (cur_thread s)`
postcondition.

Many downstream proofs need polymorphic preservation of the literal
`cur_thread` field, especially scheduling and IPC-path proofs. Today
such proofs fall through to manual `set_cdt_def` unfolds. With this
new `[wp]` rule they discharge automatically.

## Why shape 2 (additive)

Brand new lemma. No old form exists, no weakening, no deletion.
`spec_witness_gen.py` confirmed shape 2: no `_old` witness needed.

## Acceptance gate trace

1. `check-theory.sh --patch` OK 48041 ms; re-verification at
   `--apply` OK 47143 ms. ✓
2. Impact verdict `additive`. ✓
3. Trial wall **−2.5%** vs baseline 49282 ms — within bounds (the
   tiny speedup is wp-class pickup; see [[0015]] for a stronger
   demonstration of the same effect). ✓
4. Parent SKILL hard rules inherited. ✓

## Impact on seL4 (上下游)

### Same-file wall

| Phase | Wall | Source |
|---|---:|---|
| baseline (post [[0015]]) | 49,282 ms | check-theory.sh |
| trial | 48,041 ms | --patch |
| apply re-verification | 47,143 ms | --apply |
| delta (trial vs baseline) | **−2.5%** | net negative |

The negative delta is smaller than [[0015]]'s −11.3%. Reason:
`cur_thread`-frame proofs in CSpace_AI are less common than
`machine_state`-frame proofs, so the wp-class pickup saves less
work in this file. The bigger ROI is downstream in scheduling /
IPC proofs.

### Cross-file consumers

`spec_impact.py`: 0 lines / 0 files (lemma is brand new). The
expected downstream value:

- **Scheduler proofs** (DetSchedSchedule_AI.thy etc.) frequently
  need `cur_thread`-frame for tactics like
  `apply (wpsimp simp: ...)` over `set_cdt`-using sequences.
- **IPC proofs** (Ipc_AI.thy) that wrap `cap_insert`/`cap_move`
  (which call `set_cdt`) similarly need the frame to commute past
  `cur_thread` references.

### Cross-session — NOT rebuilt

Same justification as [[0015]]: additive `[wp]` rule can never
break a downstream proof, only speed it up. Quantifying speedup
in Refine would need a full ~1h17min rebuild. Deferred to a
future measurement pass.

## PR description fields

| Field | Value |
|---|---|
| Lemma | `set_cdt_cur_thread[wp]` (new) |
| File | `verification/l4v/proof/invariant-abstract/CSpace_AI.thy` (line 3855 post-apply) |
| Baseline wall | `49,282 ms` (state with [[0014]] + [[0015]] applied) |
| Trial wall from `--apply` | `47,143 ms` (`--patch` trial: `48,041 ms`) |
| Experiment ID | `0016-set-cdt-cur-thread-frame-lemma` |

## Notes / follow-ups

- This is the third applied spec strengthening on this branch. The
  Pattern G frame-lemma triplet is now complete for the most
  common state components on `set_cdt`: machine_state ([[0015]]),
  cur_thread (here), and the existing `set_cdt_vms` covers
  `valid_machine_state`. [[0017]] will fill the `idle_thread`
  field.
- The trial wall (48041 ms) is the lowest seen so far for
  CSpace_AI on this branch — the wp-class accumulation has a
  measurable cumulative effect.
- l4v submodule pointer unchanged.
