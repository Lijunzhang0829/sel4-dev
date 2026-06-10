# 0023-unbind-maybe-drop-valid-objs

| Field | Value |
|---|---|
| Pattern | C |
| Key | `C:Finalise_AI:unbind_maybe_notification_not_bound:valid_objs` |
| Variant | seL4-source PR (rule 5 full record) |
| Branch | spec-strengthen |
| Date | 2026-06-08 |
| File | `verification/l4v/proof/invariant-abstract/Finalise_AI.thy` |
| Verdict | applied |
| Impact verdict | premise-weaken |
| Δ wall (trial) | 3.1% |
| Walls | baseline=22383 ms · trial=23074 ms · apply=22864 ms |

## What changed

See `patch.diff` in this directory. The unified diff is the
canonical replayable record.

## Premise dropped

`valid_objs s` was dropped from the precondition of
`unbind_maybe_notification_not_bound`:

```
before: ⟨ntfn_at ntfnptr s ∧ valid_objs s ∧ sym_refs (state_refs_of s)⟩
after:  ⟨ntfn_at ntfnptr s ∧ sym_refs (state_refs_of s)⟩
```

Semantically: the proof of "after `unbind_maybe_notification`, the
notification at `ntfnptr` exists with no bound TCB" does not actually
require well-formedness of all kernel objects (`valid_objs`). Only the
notification's own existence (`ntfn_at`) and the symmetric-refs
invariant (`sym_refs`) are load-bearing for the closing
`(clarsimp simp: obj_at_def)` step.

The strengthening makes the lemma applicable in more contexts —
specifically, any caller that has `ntfn_at + sym_refs` but lacks
`valid_objs`.

## Witness rule + discharge

```isabelle
lemma unbind_maybe_notification_not_bound_old:
  "⟨ntfn_at ntfnptr s ∧ valid_objs s ∧ sym_refs (state_refs_of s)⟩
   unbind_maybe_notification ntfnptr
   ⟨λ_. obj_at ...⟩"
  by (rule hoare_weaken_pre[OF unbind_maybe_notification_not_bound]) simp
```

`hoare_weaken_pre` (the plain-`valid` triple's pre-weaken Hoare-
monotonicity rule) takes the strengthened new lemma and the
implication `old_pre ⟹ new_pre` to derive the old form. Since
`old_pre = ntfn_at ∧ valid_objs ∧ sym_refs` and `new_pre = ntfn_at ∧
sym_refs`, the implication is a trivial conjunction-projection that
`simp` discharges in one step.

## Probe evidence

`spec_premise_probe.sh` (TRIAL-based, v1) returned:

```
verdict: likely-unused
reason:  synthesized lemma's proof closed without the `valid_objs`
         premise — premise is NOT load-bearing.
```

The probe synthesized a copy of the lemma with `valid_objs` removed
and ran the original proof against it inside Isa-REPL. The proof
closed cleanly, giving ground-truth evidence that the premise can
be dropped before any full `check-theory.sh --patch` cycle.

This is consistent with the playbook Case 2 precedent — the same
lemma was the demonstrator there (smoke patch `exp_smoke_C.patch`),
which dropped both `valid_objs` and `sym_refs`. This experiment
drops only `valid_objs`; a future PR could also drop `sym_refs` by
running the pipeline again with `--premise sym_refs`.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 1. check-theory.sh --patch | ✓ OK 23074 ms |
| 2. spec_impact verdict | ✓ premise-weaken |
| 3. trial wall ≤ baseline × 1.30 | ✓ (3.1%) |
| 4. parent SKILL hard rules | ✓ |

## Impact on seL4 (上下游)

### Same-file wall

| Phase | Wall | Notes |
|---|---:|---|
| baseline | 22,383 ms | Finalise_AI.thy clean baseline |
| trial (with patch) | 23,074 ms | +3.1% — small parsing cost for the additional `_old` witness lemma |
| apply (re-verifies) | 22,864 ms | within noise of baseline |

The wall delta is small and positive (the new witness lemma adds
~700 ms of parsing/checking). Within the SKILL +30% gate.

### Cross-file consumers

`spec_impact.py` Tier-2 grep: `consumers_lines: 5`, `consumers_files: 5`.

The lemma is cited 5 times across 5 different .thy files. Dropping
`valid_objs` from its precondition means **those 5 call sites no
longer have to supply `valid_objs`** when invoking
`unbind_maybe_notification_not_bound` — small but real cleanup
opportunity for whoever maintains those callers.

### Strength score

`strength_score: 1.0` (Tier-1). This is the first non-zero strength
score on this branch — previous experiments were all additive
(Pattern G, score=0). Pattern C with a real `premise-weaken` verdict
produces a Tier-1 contribution to spec strengthening.

### Cross-session

NOT rebuilt. Same reasoning as the Pattern G batches: Refine
rebuild ~1h17min wall; a single premise-drop doesn't justify it.
The right time to measure cross-session impact is when a Pattern B
cleanup PR refactors the 5 consumers to drop their now-unneeded
`valid_objs` premises.

## Notes / follow-ups

- **First end-to-end Pattern C apply** on this branch. Validates
  the full automation chain: scanner → survey → probe → patchgen →
  pipeline → audit. ~4 min total wall (probe ~50s + baseline ~22s
  + trial ~23s + apply ~23s + overhead).
- The playbook Case 2 precedent dropped TWO premises (`valid_objs`
  and `sym_refs`). This PR drops only one. A follow-up
  `0024-unbind-maybe-drop-sym-refs` would complete the playbook
  precedent.
- The 5 cross-file consumers are a natural target for a Pattern B
  follow-up cleanup PR that rewrites them to omit the now-
  unneeded `valid_objs`.
