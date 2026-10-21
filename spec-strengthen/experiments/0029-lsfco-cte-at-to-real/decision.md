# 0029-lsfco-cte-at-to-real

| Field | Value |
|---|---|
| Pattern | A |
| Key | `A:Ipc_AI:lsfco_cte_at` |
| Variant | seL4-source PR (rule 5 full record) |
| Branch | spec-strengthen |
| Date | 2026-06-08 |
| File | `verification/l4v/proof/invariant-abstract/Ipc_AI.thy` |
| Verdict | **trial_failed** (in-file consumer breakage — no apply) |
| Walls | baseline=109007 ms · trial=FAILED · apply=N/A |

## Attempted patch

See `range-patch.patch.txt`. Range-replace L50–54 with an
in-place strengthening of `lsfco_cte_at` from `cte_at` to
`real_cte_at`, plus a `lsfco_cte_at_old` witness:

```isabelle
lemma lsfco_cte_at:
  "\<lbrace>valid_objs and valid_cap cn\<rbrace>
  lookup_slot_for_cnode_op f cn idx depth
  \<lbrace>\<lambda>rv. real_cte_at rv\<rbrace>,-"
  by (rule lookup_cnode_slot_real_cte)

lemma lsfco_cte_at_old:
  "\<lbrace>valid_objs and valid_cap cn\<rbrace>
  lookup_slot_for_cnode_op f cn idx depth
  \<lbrace>\<lambda>rv. cte_at rv\<rbrace>,-"
  by (rule hoare_strengthen_postE_R, rule lsfco_cte_at, simp add: real_cte_at_cte)
```

The strict-strengthening claim is solid: the original proof body
was already a redirect from `lookup_cnode_slot_real_cte` plus a
`real_cte_at_cte` weakening; promoting the postcondition is just
removing that weakening step.

## What went wrong

`check-theory.sh --patch` reported failure at L407 (the `done`
of `lsfco_cte_wp_at_univ`, the only in-file consumer of
`lsfco_cte_at`):

```
*** At command "done" (line 407 of "/tmp/.../Tmp_*.thy")
*** goal: \<And>a b s.
***   \<lbrakk>\<forall>a b. All (P (a, b)); cap_table_at (length b) a s\<rbrakk>
***   \<Longrightarrow> \<exists>cap. fst (get_cap (a, b) s) = {(cap, s)}
```

`lsfco_cte_wp_at_univ` (Ipc_AI.thy:391) applies `lsfco_cte_at`
and then runs `clarsimp simp: cte_wp_at_def`. The clarsimp
recipe was tuned for `cte_at rv` antecedent; with the new
`real_cte_at rv` antecedent it can no longer close the goal
without an extra `cap_table_at`-to-`cte_at` simp step.

## Root cause — coupled multi-site change

Strict-strengthening `lsfco_cte_at` in place requires
**coordinated edits** at every site that relies on the exact
`cte_at rv` postcondition:

- **1 in-file consumer** in Ipc_AI.thy (L399 / lemma at L391).
  Fixable with one extra simp lemma in the same patch.
- **89 lines / 24 files** of cross-file consumers across
  Refine, CRefine, Access, and other AInvs theories.

The framework's single-site `execute` path is not designed for
multi-site coordinated edits. The "execute --pattern A" gate
correctly stops here at trial.

## Cross-file consumer survey

| Session | File pattern | Lines |
|---|---|---:|
| AInvs | Ipc_AI / CSpace_AI / CNodeInv_AI | 2 + 1 + 5 = 8 |
| Refine | {arch}/CNodeInv_R + {arch}/Ipc_R (×5 arches) | 9×5 + 2×5 = 55 |
| CRefine | {arch}/Invoke_C + {arch}/Ipc_C (×5 arches) | 4×5 + 1×5 = 25 |
| access-control | CNode_AC | 1 |
| **Total** | | **89** |

## Strengthening still valid (just unreachable here)

The claim "`lookup_slot_for_cnode_op` returns a `real_cte_at`,
not just `cte_at`" is true — `lookup_cnode_slot_real_cte`
already proves it (CSpace_AI.thy:4730). The PR's intent (give
`lsfco_cte_at` the same strong postcondition) is a code-quality
cleanup, not a new theorem. But the cleanup is a
**migration**, not a strengthening per the project's
single-shot execute model.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 1. check-theory.sh --patch | ✗ trial failed at L407 (in-file consumer) |
| 2. spec_impact verdict | N/A (gate 1 blocked) |
| 3. trial wall | N/A |
| 4. parent SKILL hard rules | N/A |

## Tooling observations

- Pattern A's "manual review only" status is validated by this
  outcome. The mechanical redirect-shape detector correctly
  identified a genuine candidate, but the heuristic+manual tier
  classification is right — the rest of the analysis (consumer
  audit, cascade design) is genuinely manual.
- The framework correctly refused to apply on trial failure.
  Ledger event: `trial_failed` (auto-recorded).
- This is the **first** Pattern A execution attempt on this
  branch and serves as a baseline for the cost shape: even with
  a clean strict-strengthening claim, the coupled multi-site
  cascade is the limiting factor, not the proof obligation.

## Notes / follow-ups

- If a future PR wants this strengthening, the recommended path
  is a multi-site coordinated migration:
  1. Add `lsfco_cte_at_old` first (alias for old form), update
     no in-file consumers — purely additive.
  2. In a separate PR, migrate consumer at Ipc_AI:399 plus
     simp/wp adjustments.
  3. In subsequent PRs (one per session: Refine, CRefine,
     Access), migrate all consumers, retaining `_old` as a
     fallback during transition.
  4. Once all consumers are migrated, finally promote
     `lsfco_cte_at` to `real_cte_at` and remove `_old`.
- Or: leave the duplication. `lookup_cnode_slot_real_cte`
  already exists; consumers wanting the stronger form can use
  it directly. The naming asymmetry (`lsfco_cte_at` /
  `lookup_cnode_slot_real_cte`) is a minor code-hygiene issue
  but not a verification problem.
