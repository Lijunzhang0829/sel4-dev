# Spec strengthening — pattern catalog with worked examples

Companion to `isabelle_prover_spec/SKILL.md`. Each pattern lists:
- the textual shape to look for,
- a real example from this codebase,
- the strengthening move,
- the expected downstream impact.

All examples are file:line citations into
`verification/l4v/proof/invariant-abstract/` or
`verification/l4v/spec/abstract/`. Verified on the current branch.

---

## Pattern A — Paired weak/strong postcondition

**Shape.** Two lemmas about the same operation differ only in the
postcondition, and one postcondition implies the other via a known lemma
(e.g. `real_cte_at_cte : real_cte_at p s ⟹ cte_at p s`).

**Example 1 — `cte_at` vs `real_cte_at`**

```isabelle
(* proof/invariant-abstract/CSpace_AI.thy:237 *)
lemma lookup_slot_real_cte_at_wp [wp]:
  "\<lbrace>valid_objs\<rbrace> lookup_slot_for_thread t addr \<lbrace>\<lambda>rv. real_cte_at (fst rv)\<rbrace>,-"
  ...

(* proof/invariant-abstract/CSpace_AI.thy:246 *)
lemma lookup_slot_cte_at_wp[wp]:
  "\<lbrace>valid_objs\<rbrace> lookup_slot_for_thread t addr \<lbrace>\<lambda>rv. cte_at (fst rv)\<rbrace>,-"
  by (strengthen real_cte_at_cte, wp)
```

The second lemma's *body* is a one-line redirection through
`real_cte_at_cte`. It exists only because some downstream `wp` pattern-
matches against the weaker `cte_at`.

**Move.** Either:
- (Aggressive) Delete `lookup_slot_cte_at_wp`; fix consumers by adding
  `wp (once) lookup_slot_real_cte_at_wp` + a manual
  `hoare_strengthen_post`.
- (Conservative) Keep both but tag the weak one `[wp del]` so it stops
  being the default match, forcing consumers to consciously pick.

Same shape appears at `CSpace_AI.thy:185 / 226` for
`resolve_address_bits_real_cte_at` vs `_cte_at`.

**Example 2 — `valid_pspace` vs `valid_objs`**

```isabelle
(* proof/invariant-abstract/CSpace_AI.thy:3295 (approx) *)
lemma set_cdt_valid_pspace : "\<lbrace>valid_pspace\<rbrace> set_cdt m \<lbrace>\<lambda>_. valid_pspace\<rbrace>"

(* proof/invariant-abstract/CSpace_AI.thy:3913 *)
lemma set_cdt_valid_objs : "\<lbrace>valid_objs\<rbrace> set_cdt m \<lbrace>\<lambda>_. valid_objs\<rbrace>"
```

Direction here is *opposite* to Example 1: `set_cdt_valid_objs` is the
finer-grained statement about one component of `valid_pspace`. It is the
right building block for proofs that don't need full `valid_pspace`.
**Don't delete**. The scanner flags both directions — read the proofs
before acting.

**Verifying direction — and a scanner caveat.** The scanner pairs by
*operation in the Hoare body* (e.g. both lemmas call `set_cap`). But
two lemmas with the same body op can still have **incomparable
preconditions** — one may require `invs`, the other `valid_objs and
valid_cap cap`, with neither implying the other. In that case the
postconditions chain (`invs ⟹ valid_objs`) but the lemmas aren't
substitutable; both stay. Confirm comparability before acting:

```
grep -A4 '^lemma <weak_name>:' <file>
grep -A4 '^lemma <strong_name>:' <file>
```

If the strong lemma's precondition is strictly *stronger* (or equal) to
the weak's, deletion / inlining is sound. If preconditions are
incomparable, the weak lemma is providing a different entry point and
should remain.

**Verifying direction (downstream).** Before deleting either lemma, run:

```
grep -rn '\b<weak_lemma_name>\b' verification/l4v/proof/<session_root>/
```

If the weak lemma is referenced *outside* its own file in `wp` rule sets,
prefer the conservative move (tag `[wp del]` or strengthen statement
in-place) over deletion.

---

## Pattern B — `set_*`/`update_*` missing functional postcondition

**Shape.** An operation `set_X y` only has invariant-preservation lemmas
(`\<lbrace>P\<rbrace> set_X y \<lbrace>\<lambda>_. P\<rbrace>`), never a functional
lemma stating *what changed* (`\<lambda>_ s. X s = y`).

**Example — `set_original`**

```isabelle
(* spec/abstract/CSpaceAcc_A.thy:99 *)
definition set_original :: "cslot_ptr ⇒ bool ⇒ (unit, 'z::state_ext) s_monad" where
  "set_original slot v ≡ do
     s ← get;
     put (s\<lparr>is_original_cap := (is_original_cap s) (slot := v)\<rparr>)
   od"

(* proof/invariant-abstract/CSpace_AI.thy:3805 *)
lemma set_original_valid_ioc[wp]:
  "\<lbrace>valid_ioc\<rbrace> set_original slot v \<lbrace>\<lambda>_. valid_ioc\<rbrace>"
```

The definition makes the functional behaviour explicit: after
`set_original slot v`, `is_original_cap s slot = v`. But no lemma in
the invariant-abstract layer states this — every consumer that needs to
reason "did the bit actually flip?" has to unfold `set_original_def`
itself.

**Move.** Add a new lemma immediately below
`set_original_valid_ioc`:

```isabelle
lemma set_original_is_original_cap:
  "\<lbrace>\<top>\<rbrace> set_original slot v \<lbrace>\<lambda>_ s. is_original_cap s slot = v\<rbrace>"
  by (wpsimp simp: set_original_def)
```

**Downstream impact.** Existing proofs that unfolded `set_original_def`
manually can now `wp set_original_is_original_cap`. The functional lemma
also composes with `hoare_post_conj` to give a strengthened compound
postcondition for free.

**Other ops in the same shape (from scanner output on CSpace_AI.thy):**
`update_cdt`, `set_cdt`, `set_cap`, `set_untyped_cap_as_full`.

---

## Pattern C — Unused precondition

**Shape.** Hoare triple mentions `valid_objs` / `invs` / `valid_pspace`
in the precondition, but the postcondition is purely structural
(`cte_at p`, `obj_at P p`, type-cast properties) — the operation itself
doesn't *need* the precondition to establish the postcondition.

**Example — `get_cap_cte_wp_at`**

```isabelle
(* proof/invariant-abstract/CSpace_AI.thy:393 (approx) *)
lemma get_cap_cte_wp_at:
  "\<lbrace>\<top>\<rbrace> get_cap p \<lbrace>\<lambda>rv. cte_wp_at (\<lambda>c. c = rv) p\<rbrace>"
```

This one is already correct (precondition `\<top>`). It exists as the
"clean" version to motivate why other `get_cap_*` lemmas with
`valid_objs` preconditions are weaker than they need to be.

**Counter-example (does not strengthen).**

```isabelle
lemma resolve_address_bits_real_cte_at:
  "\<lbrace>valid_objs and valid_cap (fst args)\<rbrace>
   resolve_address_bits args \<lbrace>\<lambda>rv. real_cte_at (fst rv)\<rbrace>, -"
```

The scanner flags this (Pattern C, low severity) but the precondition
*is* load-bearing: `resolve_address_bits` walks the capability tree, and
`real_cte_at` of the final slot requires `valid_objs` for the walk to
make sense. **Don't try to remove**.

**Move.** Write the patch removing the suspect conjunct from the
precondition, run `check-theory.sh --patch`. If `OK` — great, apply.
If the proof fails — the precondition was load-bearing; revert and
move on. Cheap to test, no need to reason in advance.

**Pattern C is a "try and see" pattern.** Don't burn budget reasoning
about whether the premise is used; the prover answers in seconds.

---

## Pattern D — Loose bound that's actually exact

**Shape.** Postcondition contains `≤`, `\<subseteq>`, or `\<longrightarrow>`
where the operation's definition forces equality / equivalence.

**Example — argument decoding**

```isabelle
(* spec/abstract/CSpace_A.thy:163 *)
whenE (\<not> guard \<le> cref) ...
```

Here `guard \<le> cref` is part of an `if`/`whenE` guard, not a
postcondition — not a strengthening target. **Look at definitions of
postconditions of `decode_*` operations** in `Decode_A.thy` and check
whether they assert `≤` on bit-widths / array indices that the
definition actually pins to `=`.

**Move.** Replace the inequality with the equality form in the lemma
statement; `check-theory.sh --patch`; if `OK`, downstream might need
small fixes (`wp (once) <strong_lemma>` insertions).

**Caveat.** Equality postconditions can be too strong if the operation
admits multiple outcomes (non-determinism). Read the definition before
acting.

---

## Pattern E — Missing co-preserved invariant

**Shape.** An operation has several separate preservation lemmas
(`f_valid_pspace`, `f_valid_mdb`, `f_valid_idle`, …) but no compound
`f_invs` lemma. Or it has `f_invs` but its proof shows it actually
preserves more (e.g. `valid_arch_state` plus everything in `invs`).

**Example — `cap_insert`'s compound invariant**

```isabelle
(* proof/invariant-abstract/CSpace_AI.thy:3860 (approx) *)
lemma cap_insert_invs[wp]:
  "\<lbrace>invs and cte_wp_at (\<lambda>c. c = NullCap) dest and valid_cap cap
        and tcb_cap_valid cap dest and …\<rbrace>
   cap_insert cap src dest \<lbrace>\<lambda>rv. invs\<rbrace>"
```

The proof composes ~7 separate lemmas (`cap_insert_valid_pspace`,
`cap_insert_ifunsafe`, `cap_insert_idle`, `cap_insert_mdb_cte_at`,
`cap_insert_valid_irq_node`, …). These all coexist with the compound
lemma — that's correct (the building blocks are useful on their own).

The *anti-pattern* would be: an operation has all the building blocks
but no compound `_invs` lemma, forcing every caller to manually
`hoare_post_conj` the components together. The scanner doesn't detect
this directly — find it by inspection: in any `_AI.thy` file, search for
`crunches <op>` blocks with many `for` lines but no
`lemma <op>_invs` nearby.

**Move.** State the missing compound lemma:

```isabelle
lemma <op>_invs[wp]:
  "\<lbrace>invs and <op-specific premises>\<rbrace> <op> args \<lbrace>\<lambda>_. invs\<rbrace>"
  by (wpsimp wp: <op>_valid_pspace <op>_valid_mdb <op>_valid_idle …
                 simp: invs_def)
```

**Downstream impact.** Largest among the five patterns. Every consumer
that previously assembled the compound by hand can collapse to a single
`wp <op>_invs`. Build wall may go *up* slightly in the AI file (one more
lemma to verify) but goes *down* in every downstream session that uses
it.

---

## Anti-pattern — don't strengthen the spec by tightening the definition

If the strengthening involves changing the *definition* of a kernel
operation in `spec/abstract/**`, you're no longer strengthening — you're
modifying the kernel's observable behaviour. That belongs to the
`isabelle_prover_haskell` or `isabelle_prover_c` flow, not here. This
sub-skill only changes **statements about** existing definitions, not
the definitions themselves.

The only exception: adding a *derived* `definition` (e.g. naming an
expression that already appears unfolded throughout the file) is fine —
it's a renaming, not a behaviour change.
