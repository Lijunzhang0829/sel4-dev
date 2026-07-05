# 0054-poc-additive-lsfco-real-cte-at

**Proof-of-concept: additive-unification design**

| Field | Value |
|---|---|
| Pattern | G_sub (additive variant of A) |
| Key | `G_sub:Ipc_AI:lsfco_real_cte_at` |
| Variant | seL4-source PR (rule 5 full record) |
| Branch | spec-strengthen |
| Date | 2026-06-10 |
| File | `verification/l4v/proof/invariant-abstract/Ipc_AI.thy` |
| Verdict | **applied** |
| Impact verdict | additive |
| Δ wall (trial) | +2.3% |
| Walls | baseline=105963 ms · trial=108351 ms · apply=109629 ms |

## What this PoC validates

The [[0029 lsfco_cte_at_to_real]] candidate was originally attempted
in **modify mode** (rewrite `lsfco_cte_at`'s post from `cte_at` to
`real_cte_at`, add `lsfco_cte_at_old` witness for backward compat).
It failed at trial — the in-file consumer `lsfco_cte_wp_at_univ`
(L391) uses `lsfco_cte_at` as a rule then closes via
`clarsimp simp: cte_wp_at_def`, and the clarsimp configuration
didn't close when the antecedent shape became `real_cte_at` instead
of `cte_at`. This was the canonical example of "cascade fail" that
motivated the additive-unification redesign discussion.

This PoC verifies that the additive form **avoids the cascade
entirely**:

1. **`lsfco_cte_at` (L50) is unchanged** — same `cte_at rv` post,
   same proof body. `git diff` on lines 50-54: zero.
2. **`lsfco_real_cte_at` (L56) is the new addition** — separate
   lemma with stronger `real_cte_at rv` post, proved trivially
   from the existing `lookup_cnode_slot_real_cte`.
3. **`lsfco_cte_wp_at_univ` (L399) is unaffected** — still uses
   `apply (rule lsfco_cte_at)` followed by
   `apply (clarsimp simp: cte_wp_at_def)`. Since `lsfco_cte_at`
   still gives the `cte_at` form, the clarsimp configuration
   still closes. No cascade.
4. **Trial passed without invoking ANY downstream proof
   adjustments**. The whole Ipc_AI file verified at +2.3% wall
   (well within the +30% gate).

## Patch shape

```isabelle
(* L50-54: UNCHANGED *)
lemma lsfco_cte_at:
  "\<lbrace>valid_objs and valid_cap cn\<rbrace>
  lookup_slot_for_cnode_op f cn idx depth
  \<lbrace>\<lambda>rv. cte_at rv\<rbrace>,-"
  by (rule hoare_strengthen_postE_R, rule lookup_cnode_slot_real_cte, simp add: real_cte_at_cte)

(* L56-60: NEW (additive) *)
lemma lsfco_real_cte_at:
  "\<lbrace>valid_objs and valid_cap cn\<rbrace>
  lookup_slot_for_cnode_op f cn idx depth
  \<lbrace>\<lambda>rv. real_cte_at rv\<rbrace>,-"
  by (rule lookup_cnode_slot_real_cte)
```

No `[wp]` attribute on `lsfco_real_cte_at` — keeps wp database
unchanged. High-level proofs that want the stronger fact invoke
the new lemma by name.

## Strict-strengthening claim

`lsfco_real_cte_at`'s spec is strictly stronger than
`lsfco_cte_at`'s along the post axis:

```
real_cte_at rv  ⟹  cte_at rv     (via real_cte_at_cte simp)
```

Old `lsfco_cte_at` is fully derivable from new `lsfco_real_cte_at`:

```isabelle
lemma lsfco_cte_at_derivable_from_new:
  "\<lbrace>valid_objs and valid_cap cn\<rbrace> lookup_slot_for_cnode_op f cn idx depth \<lbrace>\<lambda>rv. cte_at rv\<rbrace>, -"
  by (rule hoare_strengthen_postE_R, rule lsfco_real_cte_at, simp add: real_cte_at_cte)
```

(The above isn't added to the file — `lsfco_cte_at`'s original form
already serves this role.)

## Acceptance gate trace

| Gate | Result |
|---|---|
| 1. check-theory.sh --patch | ✓ OK 108351 ms |
| 2. spec_impact verdict | ✓ additive |
| 3. trial wall ≤ baseline × 1.30 | ✓ (+2.3%) |
| 4. parent SKILL hard rules | ✓ |

## Impact on seL4 (上下游)

| Phase | Wall | Notes |
|---|---:|---|
| baseline | 105,963 ms | Ipc_AI build (AInvs session) |
| trial | 108,351 ms | **+2.3%** |
| apply | 109,629 ms | within noise of baseline |

`consumers_lines=3, consumers_files=3` per `spec_impact.py` Tier 2
grep. The new `lsfco_real_cte_at` is provided as a tool lemma; no
existing code uses it. Cross-session: NOT rebuilt.

Cross-file consumers of the ORIGINAL `lsfco_cte_at` (~89 lines
across 24 files in Refine / CRefine / Access / AInvs) are
**completely unaffected** — the original lemma signature is
preserved. This is the design's central benefit vs. [[0029]]'s
modify path.

## Validates design hypothesis

The additive-unification design rests on three claims. This PoC
confirms each:

1. **No cascade**: changing nothing in the existing lemma graph
   eliminates the entire consumer-breakage failure mode.
   ✓ Verified — `lsfco_cte_wp_at_univ` untouched, trial passes.

2. **Trial-as-ground-truth holds**: the new lemma's correctness is
   captured by check-theory.sh --patch. No silent acceptance
   path.  ✓ Verified — trial OK 108s, all gates pass.

3. **Pure-add tooling sufficient**: `execute_custom --pattern A`
   (which accepts arbitrary patches) handles the additive shape
   without any new framework code. The future `execute_additive`
   path that this design proposes would just be a thinner
   front-end over this same standard_pipeline.  ✓ Verified —
   reused existing tooling, no plumbing changes.

## Implications for Tier 2 / Tier 3 work

| Pattern | Modify failure rate (this branch) | Additive prediction |
|---|---|---|
| C (Tier 2) | 26/27 load-bearing | ~unchanged (premise really is used) |
| A (Tier 3) | 1/1 cascade (0029) | **avoidable**: this PoC's exact category |

The Tier 3 A class — where the original modify mode is blocked by
in-file/cross-file consumer cascade — gains the biggest lift from
the additive design. Each such A candidate can be re-run as
"add the strong-form alongside" and the cascade is structurally
prevented.

Tier 2 C class gains less. Dropping a premise from a NEW lemma
copy doesn't change the truth of "is the premise load-bearing in
this proof body"; the new lemma needs the same proof to verify.
Where additive can help here is in LLM-driven new-proof search —
the agent attempts a proof that doesn't use the premise; if it
finds one, the additive lemma is added (and the original C
candidate stays not-applicable).

## Notes / follow-ups

- This PoC was driven via `execute_custom --pattern A` as a
  placeholder. The next-phase work is to add a dedicated
  `execute_additive` path that:
  1. Auto-suffixes the new lemma name (`_strong`, `_no_X`, etc.).
  2. Defaults `[wp]` registration to OFF for non-frame additives.
  3. Records `kind` metadata in the ledger
     (`G_frame` / `C_drop` / `A_strengthen` / `D_tighten` /
     `G_sub`) for analytics.
- The `Pattern: A` field in the table is mildly misleading
  (this is `G_sub` semantically). The framework currently has
  no `G_sub` enum value — the tooling unification mentioned
  above would address this.
- This is the first experiment that validates the
  additive-unification design hypothesis. Full design is in
  conversation history; a `reports/spec-strengthen/
  additive-unification.md` formal write-up is a reasonable
  next step.
