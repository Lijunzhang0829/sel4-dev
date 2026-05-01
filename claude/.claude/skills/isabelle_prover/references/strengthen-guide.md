# Strengthen Guide — examples and background

> **This document is optional background.** Hard rules live in `SKILL.md`
> (no direct edits, no bypassing the prover, two JSONL logs). The
> patterns below are illustrative, not required. Scan strategy, fix
> ordering, budget allocation, parallelism — all your call.

## The four types at a glance

| Type | Target | Typical goal |
|------|--------|--------------|
| **spec** | `.thy` in `spec/abstract/`, `proof/invariant-abstract/` | Tighten theorem statements — stronger conclusions, fewer assumptions |
| **proof** | `.thy` in `proof/` | Make proofs faster / smaller / more readable |
| **haskell** | `.hs` / `.lhs` in `spec/haskell/` | Better algorithms / data structures in the executable spec |
| **c** | `seL4/src/**` | Better kernel C while keeping CRefine green |

---

## Spec — example patterns

```isabelle
(* Weak: loose bound *)
lemma foo: "length [x] ≤ Suc 0 + 1"
(* Strong: exact bound *)
lemma foo_strong: "length [x] = Suc 0"

(* Weak: unnecessary premise *)
lemma foo: assumes "x > 0" shows "x * 1 = x"
(* Strong: holds unconditionally *)
lemma foo_strong: "x * 1 = x"

(* Weak: partial postcondition *)
lemma rev_length: "length (rev xs) = length xs"
(* Strong: adds set preservation *)
lemma rev_length_strong:
  "length (rev xs) = length xs ∧ set (rev xs) = set xs"
```

Signals worth looking for (non-exhaustive):
- Postconditions that omit obvious co-properties
- `≤` / `⊆` that are actually `=`
- Unused premises that can be dropped
- Missing safety properties the surrounding code relies on

---

## Proof — moves that often pay off

- Replace heavy `auto` / `fastforce` with composed targeted tactics
  (`simp add: ...; rule ...; blast`).
- Narrow `simp` / `wp` / Eisbach combinator rule lists to avoid search
  blow-ups.
- Gate expensive simplifier recursion with `[[simp_depth_limit = N]]`.
- Hoist a repeated sub-proof out of a slow block into a reusable lemma.

Run `proof-timing.sh` to find hot spots; try ideas; verify with
`check-theory.sh --patch`; log attempts + impact (see SKILL.md).

---

## Haskell — things to consider

The `spec/haskell/` tree is the executable kernel spec — real code for
scheduling, IPC, capabilities, memory. Useful signals:

- Naïve recursion / O(n²) that can be O(n log n)
- Lists where sets/maps/finite maps would behave better
- Repeated lookups of invariant data
- Lazy-evaluation space leaks
- Dead branches, unused parameters

Changing Haskell cascades downstream: the Haskell→HOL translator
regenerates `spec/design/*.thy`, and then affected `proof/refine/` and
`proof/crefine/` files need re-verifying. The `strengthen-hooks/`
harness automates the regen step; fixing the cascaded proofs is the
substantive work.

---

## C — refinement gives you freedom

seL4's refinement architecture lets C implementation strategy diverge
from the Haskell spec. The spec stays fixed; CRefine proves the new C
still refines it.

Typical licenses:
- Haskell linear scan → C bitmap for O(1) lookup
- Haskell no cache → C cache (as long as it's transparent)
- Haskell simple structures → C optimized ones

After editing C, regeneration (kernel_all.c_pp) is handled by
`strengthen-hooks/`. The substantive work is fixing whatever CRefine
obligations break.

---

## Patch file format (what `check-theory.sh --patch` expects)

```
<start_line> <end_line>
<replacement text spanning one or more lines>
---
<start_line> <end_line>
<replacement text>
```

Line numbers refer to the **original** file. Patches are applied in
reverse order internally — no manual offset arithmetic.
