# Spec strengthening — playbook

Companion to `isabelle_prover_spec/SKILL.md`. Read this when:
- The candidates tool returned an unfamiliar shape and you're not
  sure how to write the patch + witness.
- You want to know whether to trust a specific detector hit
  (false-positive rates vary widely by shape).
- You're looking for empirical context on past sessions — what
  worked, what failed, why.

**Not a contract.** The SKILL.md is the contract; this file is
prose, examples, and rationales.

## Reading order

| Question | Section |
|---|---|
| What "kind of strengthening" matters logically? | Taxonomy below |
| How to find one of a given shape? | Pattern catalog below |
| How likely is a given scanner hit to be real? | Reliability table |
| Worked example of a shape | Case studies further down |
| Why was Pattern F rejected? Why does Pattern G need manual scan? | Empirical notes at end |

---

## Taxonomy — what kind of change is this?

Two logical kinds matter for soundness:

| Kind | Effect on consumers | Witness `_old` required? |
|---|---|---|
| **Strengthening** (`P_old ⟹ P_new` ∧/∨ `Q_new ⟹ Q_old`) | Every old caller still works | Yes |
| **Additive** (no `_old` form exists) | Old callers unaffected; new lemma is opt-in | No |

A third pseudo-kind: **packaging** (deleting a redundant weak-form
alias). It looks additive (no statement change to a surviving
lemma) but is functionally a deletion — the surviving lemma must
carry a witness proving the deleted alias's statement is still
derivable. Treat as strengthening for the witness requirement.

The scanner produces *hints* (paired-chain / unused-premise /
missing-functional). The hint tells you what to **try**; the SKILL
contract decides whether the trial passes (check-theory.sh OK +
spec_impact verdict + wall gate).

## Witness rules by Hoare triple shape

The SKILL's witness contract requires `lemma <name>_old: "<old
statement>" by (rule <hoare-monotonicity>[OF <name>]) <discharge>`.
The correct `<hoare-monotonicity>` depends on the **shape of the
triple** (`valid` / `validE` / `validE_R` / `validE_E`) and on
**which side** is being relaxed (precondition vs postcondition).
A common silent failure is picking a name by suffix-pattern-match
(e.g. inventing `hoare_post_imp_R`, which does not exist in l4v)
instead of by triple shape.

The rules below all live under `verification/l4v/lib/Monads/`.
Cross-checked against l4v as of 2026-06-02.

| Triple shape | Strengthening direction | Witness rule | Discharge |
|---|---|---|---|
| `⟨P⟩ f ⟨Q⟩` (`valid`) | Pre weakened (drop premise) | `hoare_weaken_pre[OF <name>]` | `simp` / explicit lemma |
| `⟨P⟩ f ⟨Q⟩` (`valid`) | Post strengthened | `hoare_strengthen_post[OF <name>]` or `hoare_post_imp[OF _ <name>]` | `simp` |
| `⟨P⟩ f ⟨Q⟩,⟨E⟩` (`validE`) | Pre weakened | `hoare_pre[OF <name>]` | `simp` |
| `⟨P⟩ f ⟨Q⟩,⟨E⟩` (`validE`) | Normal-post strengthened | `hoare_post_impE[OF _ _ <name>]` | `simp` (two discharges) |
| `⟨P⟩ f ⟨Q⟩,-` (`validE_R`) | Post strengthened | `hoare_strengthen_postE_R[OF <name>]` | `simp` |
| `⟨P⟩ f -,⟨E⟩` (`validE_E`) | Error-post strengthened | `hoare_strengthen_postE_E[OF <name>]` | `simp` |

**Names that do NOT exist in l4v** (do not invent these — first
`check-theory.sh --patch` will fail with a typecheck error):

- `hoare_post_imp_R` — use `hoare_strengthen_postE_R` for the
  validE_R post side, or `hoare_post_impE` for validE
- `hoare_post_impR` / `hoare_pre_R` — non-existent

When the discharge step takes more than a `simp`, the strengthening
isn't a pure monotonicity step — the spec change has real
semantic content that needs additional lemmas to bridge. That's
fine, but the `_old` proof grows beyond a one-liner; record the
extra reasoning in `decision.md`.

## Pattern catalog (legacy names retained for cross-reference)

The skill's user-facing tool emits descriptive labels. Past
strengthen-logs and historical conversations refer to
Patterns A-G. Mapping:

| Legacy name | Detector kind | Logical kind |
|---|---|---|
| Pattern A — paired weak/strong | `paired-chain` | Packaging |
| Pattern B — missing functional postcond | `missing-functional` | Additive |
| Pattern C — unused premise | `unused-premise` | Strengthening (premise-weaken) |
| Pattern D — loose bound `≤ → =` | (manual) | Strengthening (postcond-strengthen) |
| Pattern E — compound `_invs` | (manual) | Additive |
| Pattern F — composite changed ∧ unchanged | rejected (not l4v idiom) | — |
| Pattern G — frame preservation | (manual) | Additive |

## Scanner reliability (calibration from 2026-05-25/26/30 sessions)

| Kind | Accuracy | Cheap validation |
|---|---|---|
| `unused-premise` | ~50% (premise may be load-bearing) | `check-theory.sh --patch` ~60s |
| `missing-functional` | ~95% detection; but downstream consumer existence rare | Manual review of consumer proofs |
| `paired-chain` | ~20-30% real opportunities | Read both lemmas + grep cross-file before acting |
| (D / G / E — manual) | n/a — no auto detector | Inspect target file by hand |

## ROI weighting rationale (hidden inside spec_candidates.py)

The candidates tool sorts by `consumer_lines × kind_weight`. Weights
reflect empirical ROI:

| Kind | Weight | Rationale |
|---|---:|---|
| `unused-premise` | 5 | Each consumer call saves a wp premise discharge |
| `missing-functional` | 3 | Only consumers that need the new fact benefit (opt-in) |
| `paired-chain` | 1 | Packaging cleanup; usually no downstream wall delta |

Weights are constants at the head of `spec_candidates.py`. Move to
JSON config if more tuning becomes needed.

---

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

## Pattern G — Frame preservation (NEW, Exp 4a 2026-05-31)

**Shape.** Op `f` doesn't touch state accessor `acc`, but no lemma of
the form `⟨λs. P (acc s)⟩ f _ ⟨λ_ s. P (acc s)⟩` exists. The frame
is missing — every downstream proof that wants to lift a property of
`acc` across `f` has to either unfold `f`'s definition or manually
piece together the lift.

**Why this is Tier 1 (post-strengthen).** Adding the frame lemma adds
a logical fact (`P (acc) is preserved`) to the set of things provable
about `f`. It's not a packaging move — it expands what consumers can
prove without unfolding.

**Example — `set_cdt_machine_state` (verified 2026-05-31)**

Inspection:

```isabelle
(* spec/abstract/CSpaceAcc_A.thy:81 *)
definition set_cdt :: "cdt ⇒ (unit, 'z::state_ext) s_monad" where
  "set_cdt t ≡ do s ← get; put $ s\<lparr>cdt := t\<rparr> od"
```

`set_cdt` clearly doesn't touch `machine_state`. But:

```bash
$ grep -rn "set_cdt_machine_state" verification/l4v/proof/invariant-abstract/
(empty — frame lemma missing)
```

**Patch added** (Untyped_AI.thy, after the existing
`set_cdt_state_hyp_refs_of[wp]`):

```isabelle
lemma set_cdt_machine_state[wp]:
  "⟨λs. P (machine_state s)⟩
     set_cdt m
   ⟨λrv s. P (machine_state s)⟩"
  by (wpsimp simp: set_cdt_def)
```

Verified: `check-theory.sh --patch Untyped_AI.thy AInvs` returned
`OK` in 74,463 ms.

**Derivability check (Tier 1 post-strengthen)**: there is no `_old`
form to derive — Pattern G adds a frame lemma that didn't exist
before. Technically this looks like Tier 2 additive, BUT it's
information-adding (not just automation-restructuring), so Tier 1
classification with `additive` verdict + SKIP §4.5 is the operational
treatment.

**Lesson — Pattern G is a scanner blind spot.** `spec_strengthen_scan.py`
doesn't currently detect missing frame lemmas — they have a
non-obvious shape (`P (accessor)` both before and after, instead of
the set-then-equate shape Pattern B catches). Manual detection
recipe:

1. Pick an op `f`. Read its definition. Note which state fields it
   updates.
2. Enumerate state accessors NOT in (1).
3. For each missing-frame candidate, grep `f_<accessor>` in
   `verification/l4v/proof/`. If empty, candidate is real.

Real l4v examples of Pattern G coverage being healthy (don't
duplicate, just illustrating shape):
- `set_cdt_state_refs_of[wp]` (`Untyped_AI.thy:2862`)
- `cap_move_typ_at` (`CSpace_AI.thy:3302`)
- `cap_swap_typ_at` (`CSpace_AI.thy:3946`)
- `crunch update_cdt caps_of_state` (`CSpace_AI.thy:3624`)

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

---

# Case studies — distilled from past sessions

Curated record of strengthenings that proved illustrative — either as
**success templates** (re-usable patterns) or as **failure modes**
(traps to avoid). Sweeping / repetitive patches (e.g. 8× `real_cte_at`
companion additions in a single session) are NOT recorded here; only
load-bearing examples.

Source logs: [AInvs-20260525.md](../../../../reports/spec-strengthen/AInvs-20260525.md),
[AInvs-20260526.md](../../../../reports/spec-strengthen/AInvs-20260526.md).

Add new entries here when you encounter a case that *teaches* something
new about a pattern — not just another instance of an already-documented
shape.

## ★ Case 1 (Pattern A, success) — `lookup_slot_cte_at_wp` cte_at → real_cte_at

**File:** `proof/invariant-abstract/CSpace_AI.thy`
**Wall delta:** −47.7% file wall (single-lemma change)
**What changed:**

```isabelle
(* before *)
lemma lookup_slot_cte_at_wp[wp]:
  "\<lbrace>valid_objs\<rbrace> lookup_slot_for_thread t addr \<lbrace>\<lambda>rv. cte_at (fst rv)\<rbrace>,-"
  by (strengthen real_cte_at_cte, wp)

(* after — postcond strengthened, body redirects to existing strong form *)
lemma lookup_slot_cte_at_wp[wp]:
  "\<lbrace>valid_objs\<rbrace> lookup_slot_for_thread t addr \<lbrace>\<lambda>rv. real_cte_at (fst rv)\<rbrace>,-"
  by (rule lookup_slot_real_cte_at_wp)
```

**Why the dramatic wall drop:** downstream same-file lemmas were each
re-running `strengthen real_cte_at_cte` after each `wp` step. Strengthening
the rule at source eliminated that conversion across every consumer in
the same file.

**Lesson — when Pattern A genuinely wins:** the weak form's *proof body*
is `(strengthen <weakening_lemma>, wp)` — a literal one-line redirect to
the strong companion. That means the weak lemma is **strictly redundant**
once consumers can absorb the stronger postcond. Look for this shape
before any A patch.

**Derivability check** (A' ⟹ A):
```isabelle
lemma lookup_slot_cte_at_wp_old: "\<lbrace>valid_objs\<rbrace> lookup_slot_for_thread t addr \<lbrace>\<lambda>rv. cte_at (fst rv)\<rbrace>,-"
  by (rule hoare_strengthen_postE_R[OF lookup_slot_cte_at_wp])
     (rule real_cte_at_cte)
```

---

## ★ Case 2 (Pattern C, success) — `unbind_maybe_notification_not_bound` drop unused premises

**File:** `proof/invariant-abstract/Finalise_AI.thy`
**Wall delta:** −6.8% file wall
**Downstream consumers:** 6 (across all 5 arch dirs)
**What changed:**

```isabelle
(* before *)
lemma unbind_maybe_notification_not_bound:
  "\<lbrace>\<lambda>s. ntfn_at ntfnptr s \<and> valid_objs s \<and> sym_refs (state_refs_of s)\<rbrace>
     unbind_maybe_notification ntfnptr
   \<lbrace>\<lambda>_. obj_at (\<lambda>ko. \<exists>ntfn. ko = Notification ntfn \<and> ntfn_bound_tcb ntfn = None) ntfnptr\<rbrace>"
  ...

(* after — valid_objs and sym_refs dropped *)
lemma unbind_maybe_notification_not_bound:
  "\<lbrace>\<lambda>s. ntfn_at ntfnptr s\<rbrace>
     unbind_maybe_notification ntfnptr
   \<lbrace>\<lambda>_. obj_at (\<lambda>ko. \<exists>ntfn. ko = Notification ntfn \<and> ntfn_bound_tcb ntfn = None) ntfnptr\<rbrace>"
```

**Proof body unchanged.** Original body uses only `get_simple_ko_wp`,
`sbn_obj_at_impossible`, `simple_obj_set_prop_at` + `clarsimp simp: obj_at_def` —
none reference `valid_objs` or `sym_refs`. The premises were copied
from sibling `unbind_notification_not_bound` (which DOES use both)
without re-examining the actual proof body.

**Lesson — Pattern C green flag:** when a lemma's proof body is short
(≤5 lines) and uses only `wp` rules + `clarsimp` (no `valid_objsE`,
no `sym_refs_*` consumers), the precondition is suspect. Try removing
each non-structural conjunct; `check-theory.sh --patch` answers in ~60s.

**Derivability check** (A' ⟹ A):
```isabelle
lemma unbind_maybe_notification_not_bound_old:
  "\<lbrace>\<lambda>s. ntfn_at ntfnptr s \<and> valid_objs s \<and> sym_refs (state_refs_of s)\<rbrace>
     unbind_maybe_notification ntfnptr \<lbrace>\<lambda>_. ...\<rbrace>"
  by (rule hoare_pre[OF unbind_maybe_notification_not_bound]) simp
```

---

## ★ Case 3 (Pattern C, failure) — `suspend_unlive` premise is load-bearing

**File:** `proof/invariant-abstract/IpcCancel_AI.thy`
**Attempted:** Drop `valid_mdb ∧ valid_objs` from precondition.
**Result:** FAILED — proof left goal
`bound_tcb_at ((=) None) t s ⟹ valid_mdb s ∧ valid_objs s`.

**Lesson:** Pattern C is "try and see" but ~50% fail. The proof body
matters: even when it looks like it doesn't use `valid_mdb`, internal
wp rules may invoke `valid_objsE` or `valid_mdb_lift`-style intermediaries.
Always check via `check-theory.sh --patch` — never reason about premise
necessity in advance.

---

## ★ Case 4 (Pattern A, wrong-direction trap) — `set_cdt_valid_objs` is not redundant

**Where scanner says:** `set_cdt_valid_objs` (CSpace_AI.thy:3927) is paired
with stronger `set_cdt_pspace` (line 3309). `valid_pspace ⟹ valid_objs`,
so prima facie A "delete weak".

**Why it's wrong:** preconditions are *incomparable*:
```
set_cdt_valid_objs : \<lbrace>valid_objs\<rbrace> set_cdt m \<lbrace>\<lambda>_. valid_objs\<rbrace>
set_cdt_pspace    : \<lbrace>valid_pspace\<rbrace> set_cdt m \<lbrace>\<lambda>_. valid_pspace\<rbrace>
```
A caller with `valid_objs` but NOT `valid_pspace` (e.g. mid-proof state
where pspace_distinct is being rebuilt) can use the first but not the
second. The weak lemma is the granular building block; deleting it
breaks ~9 downstream files.

**Lesson — Pattern A direction rule:** check that the strong companion's
**precondition is at least as strong** (more restrictive) as the weak's.
If not, the lemmas are incomparable entry points and both stay. The
scanner pairs by postcond chain only; the human checks the precondition.

---

## ★ Case 5 (Pattern B, additive) — `set_cdt_cdt_update` new functional postcond

**File:** `proof/invariant-abstract/CSpace_AI.thy` (post `set_cdt_valid_pspace`)
**What added:**

```isabelle
lemma set_cdt_cdt_update:
  "\<lbrace>\<top>\<rbrace> set_cdt t \<lbrace>\<lambda>_ s. cdt s = t\<rbrace>"
  by (wpsimp simp: set_cdt_def)
```

**Why no immediate downstream win:** existing proofs that need
`cdt s = t` after a `set_cdt t` had **already worked around the absence**
by unfolding `set_cdt_def` manually (e.g. `update_cdt_cdt`,
`cap_move_typ_at`). They don't refactor automatically just because the
new lemma exists — Pattern B is *opt-in*.

**Lesson — Pattern B ROI is delayed:** the immediate verification cost
is small (one new lemma, seconds to verify), but ROI accrues only when
future proofs are written *or* a separate refactor pass replaces manual
`unfold *_def` with `wp <op>_<functional>`.

**Derivability check:** Pattern B has no "old form" — the lemma is
brand new. Derivability gate is **skipped** for additive patterns.

---

## ★ Case 6 (Pattern E, failure) — `set_cdt_invs` blocked by missing preservation rules

**Attempted:** Add compound

```isabelle
lemma set_cdt_invs:
  "\<lbrace>\<lambda>s. invs s \<and> valid_mdb (s\<lparr>cdt := m\<rparr>)\<rbrace> set_cdt m \<lbrace>\<lambda>_. invs\<rbrace>"
```

**Result:** 5 proof-tactic variants all FAILED. Root cause:
`set_cdt` lacks `[wp]` preservation rules for many of `invs`'s 25
components — particularly arch-specific ones:
`valid_arch_state`, `valid_machine_state`, `valid_vspace_objs`,
`valid_arch_caps`, `valid_global_objs`, `valid_kernel_mappings`,
`equal_kernel_mappings`, `valid_asid_map`,
`valid_global_vspace_mappings`, `pspace_in_kernel_window`,
`cap_refs_in_kernel_window`, `pspace_respects_device_region`,
`cap_refs_respects_device_region`, `valid_irq_handlers`,
`valid_ioports`, `only_idle`, `valid_global_refs`.

Error signature on each failed attempt: `Unification bound exceeded`
or `Failed to finish proof` with the conjunctive postcondition still
holding 20+ unfolded components.

**Lesson — Pattern E preflight is mandatory:** before composing
`op_invs`, run a per-component check (the coverage matrix from
`spec_coverage_matrix.py`). If any `invs` component lacks
`op_<component>[wp]`, the compound proof cannot close. Filling those
gaps is base plumbing (a separate multi-step project), not a
strengthening patch.

**Workaround pattern when E is infeasible:** stop at the building-block
level. Add the missing `op_<component>[wp]` rules one by one, each as
its own Pattern B/E-precursor patch. Compound `op_invs` becomes
possible only after the matrix shows ≥95% coverage.

---

## ★ Case 7 (Pattern A, success — historic) — `set_original_is_original_cap` from 20260525

(Now superseded by Case 5 as the canonical Pattern B example. The
20260525 entry showed a slightly different shape — `set_original` modifies
a boolean flag, while Case 5's `set_cdt` modifies a record field. The
proof tactic `by (wpsimp simp: set_X_def)` works identically.)

Use Case 5 as the template. This case is referenced only for completeness.
