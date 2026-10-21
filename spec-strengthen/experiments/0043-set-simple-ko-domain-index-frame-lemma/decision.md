# 0043-set-simple-ko-domain-index-frame-lemma

| Field | Value |
|---|---|
| Pattern | G |
| Key | `G:DetSchedSchedule_AI:set_simple_ko:domain_index` |
| Variant | seL4-source PR (rule 5 full record) |
| Branch | spec-strengthen |
| Date | 2026-06-09 |
| Verdict | **trial_failed** — auto-template default tactic insufficient |

## What was attempted

Auto-generated patch from execute_G (post [[767d57f]]):

```isabelle

lemma set_simple_ko_domain_index[wp]:
  "\<lbrace>\<lambda>s. P (domain_index s)\<rbrace> set_simple_ko f ptr ep \<lbrace>\<lambda>_ s. P (domain_index s)\<rbrace>"
  by (wpsimp simp: set_simple_ko_def)
```

## Why trial failed

`by (wpsimp simp: domain_index)_def)` leaves a residual goal
with branch-by-branch case obligations that simp doesn't auto-close:

- **0043**: set_simple_ko_def partial_inv branches

Tail of trial output:
```
  (tail of full trial output:)
    ***                 (a_type obj = AEndpoint \<longrightarrow>
    ***                  (\<exists>y. partial_inv f obj = Some y) \<longrightarrow>
    ***                  P (domain_index s)) \<and>
    ***                 (a_type obj = ANTFN \<longrightarrow>
    ***                  (\<exists>y. partial_inv f obj = Some y) \<longrightarrow>
```

## Diagnosis

The default tactic in execute_G is `by (wpsimp simp: <op>_def)`.
This works for ops whose body unfolds to monad-level operations
without internal case-analysis on output values (`set_object`,
`set_cdt`, `set_thread_state`, etc.). It does **not** close for
ops with case-on-input dispatch where each branch leaves a
predicate-on-state residual:

- `set_cap` does case-analysis on TCB cnode indices when the cap
  is being placed into a TCB slot.
- `set_simple_ko` uses `partial_inv f obj` to recognize the
  endpoint / notification flavor.

Both produce conjunction-of-implication leftover goals like
`(x2 = idx ⟶ P (field s)) ∧ (x2 ≠ idx ⟶ ...)` that simp cannot
collapse — each implication's conclusion IS in scope (from the
precondition), but wpsimp doesn't see it without an extra
`clarsimp` or explicit case-split simp rule.

## Fix

A stronger tactic that closes these would be:

```isabelle
by (wpsimp simp: <op>_def split: ... | clarsimp)+
```

or splitting case-on-input via `split:` declarations on the
specific datatype (`cap.split` for set_cap, `a_type.split` for
set_simple_ko). This is a candidate for a follow-up execute_G
enhancement (op-specific tactic library), not a fundamental
limitation — the lemma statement itself is true and provable.

## Notes / follow-ups

- Could be applied with a hand-written patch in a future
  experiment using `execute --pattern G --patch <custom>` (the
  framework path validated by [[0027]] / [[0030]] / etc).
- Not a semantic FP — the frame claim is true. Just the auto-
  template doesn't close.
