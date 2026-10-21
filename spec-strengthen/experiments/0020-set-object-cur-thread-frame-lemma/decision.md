# spec-0020 — `set_object_cur_thread[wp]` Pattern G frame lemma (seL4-source PR)

| Field | Value |
|---|---|
| **Variant** | seL4-source PR (rule 5 full record) |
| **Branch** | `spec-strengthen` |
| **Date** | 2026-06-05 |
| **Verdict** | applied |
| **Patch shape** | 2 (additive — Pattern G frame preservation) |
| **Impact verdict** | `additive` |
| **Acceptance** | PASS (all 4 gates) |
| **File** | `proof/invariant-abstract/KHeap_AI.thy` |
| **Base** | [[0019]] applied on top of l4v baseline `00d9073f70d0` |

## What changed

Inserted right after [[0019]]'s `set_object_cdt[wp]`:

```isabelle
lemma set_object_cur_thread[wp]:
  "\<lbrace>\<lambda>s. P (cur_thread s)\<rbrace> set_object p ko \<lbrace>\<lambda>_ s. P (cur_thread s)\<rbrace>"
  by (wpsimp wp: set_object_wp_strong)
```

Pattern G frame lemma for the literal `cur_thread` field across
`set_object`. Same shape as [[0019]], for a different state component.

## First attempt aborted — collision with crunch derivation

Originally I planned 0020 = `set_object_interrupt_states[wp]`,
chosen because the existing `valid_irq_states_triv` lemma right
below the insertion point uses `\<lambda>s. P (interrupt_states s)`
as an assumption — so the new rule would directly enable
`valid_irq_states_triv` to fire on `set_object` automatically.

**Result**: `check-theory.sh --patch` failed with

```
*** Duplicate fact declaration
"Tmp_cecc3aa5ee556ddd.set_object_interrupt_states" vs.
"Tmp_cecc3aa5ee556ddd.set_object_interrupt_states"
```

Root cause: `KHeap_AI.thy:925` contains
```
crunch interrupt_states[wp]: set_simple_ko "\<lambda>s. P (interrupt_states s)"
```
`crunch` recursively descends through `set_simple_ko`'s body and
auto-generates `set_object_interrupt_states[wp]` as an intermediate
fact — even though the lemma name never appears in source text.
A grep against the file does NOT find this derivation; it only
shows up when Isabelle compiles the file and the duplicate
declaration error fires.

**Lesson learned (recorded in cross-batch summary)**: before
adding a `set_<op>_<field>` Pattern G frame lemma, also search
the file for any `crunch <field>` on operations that internally
call `<op>`. If found, the crunch will already have generated
the frame implicitly.

I switched the candidate to `cur_thread`, which has no such crunch
nearby (verified by `grep 'crunch.*cur_thread.*set_'` on the
invariant-abstract tree returning nothing). The `cur_thread` apply
succeeded.

## Why shape 2 (additive)

Brand new. `spec_witness_gen.py` → SHAPE 2 (additive), no
witness needed.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 1. `check-theory.sh --patch` returns OK | ✓ 23,966 ms |
| 2. `spec_impact` emits non-weakening + accepting verdict | ✓ `additive` |
| 3. Trial wall ≤ baseline × 1.30 | ✓ **−4.4%** (25,076 → 23,966) — net negative |
| 4. Parent SKILL hard rules inherited | ✓ |

## Impact on seL4 (上下游)

### Same-file wall — measurable negative delta

| Phase | Wall | Notes |
|---|---:|---|
| baseline (post-0019) | 25,076 ms | KHeap_AI with set_object_cdt[wp] in place |
| trial (with patch) | 23,966 ms | −4.4% |
| apply re-verifies | 23,954 ms | within noise of trial |

A measurable wp-class pickup: the new `set_object_cur_thread[wp]`
rule lets wp discharge `\<lambda>s. P (cur_thread s)` postcondition
fragments on `set_object`-using sequences instantly, rather than
falling through to manual `(simp add: set_object_def)` unfolds.

Same effect as 0015's −11.3% but smaller in magnitude — because
KHeap_AI itself doesn't contain many `cur_thread`-frame proof
obligations (those live mostly in scheduling files), so the
in-file pickup is small. The bigger downstream value is in
DetSchedSchedule_AI and Refine.

### Cross-file consumers

Tier-2 grep: 0 lines / 0 files (brand new). Downstream pickup
expected in:
- Scheduler proofs (DetSchedSchedule_AI.thy) that compose with
  `set_object`-based operations
- TCB-mutation proofs (TcbAcc_AI.thy) that need `cur_thread`
  frame across `set_object` underneath `thread_set` or
  `set_thread_state`

### Cross-session — NOT rebuilt

Same justification as the CSpace_AI batch (0015-0017): zero
breakage upper bound for additive `[wp]` rules; downstream wall
measurement deferred to a future Refine rebuild.

## PR description fields

| Field | Value |
|---|---|
| Lemma | `set_object_cur_thread[wp]` (new) |
| File | `verification/l4v/proof/invariant-abstract/KHeap_AI.thy` (line 1285 post-apply) |
| Baseline wall | `25,076 ms` (state with [[0019]] applied) |
| Trial wall from `--apply` | `23,954 ms` (`--patch` trial: `23,966 ms`) |
| Experiment ID | `0020-set-object-cur-thread-frame-lemma` |

## Notes / follow-ups

- The aborted `set_object_interrupt_states[wp]` attempt produced
  a useful negative result: **the unlock-`valid_irq_states_triv`
  story is already in place via the existing crunch on
  set_simple_ko**. So that downstream-readiness gap doesn't need
  a separate PR — only the `cur_thread`/`idle_thread`/etc. gaps
  do.
- Combined with [[0019]], two of the missing
  `set_object_<field>[wp]` slots are now filled: `cdt` and
  `cur_thread`. The remaining truly-missing slots (per the
  pre-batch survey, modulo the crunch-derived ones we can now
  identify) are `idle_thread`, `scheduler_action`,
  `cur_domain`, `domain_index`, `domain_time`, plus
  `arch_state` and `interrupt_irq_node`. Each is a natural
  candidate for a future batch with the same shape.
- l4v submodule pointer unchanged.
