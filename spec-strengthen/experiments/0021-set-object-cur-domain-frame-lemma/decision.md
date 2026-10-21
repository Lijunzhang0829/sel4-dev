# spec-0021 — `set_object_cur_domain[wp]` Pattern G frame lemma (seL4-source PR)

| Field | Value |
|---|---|
| **Variant** | seL4-source PR (rule 5 full record) |
| **Branch** | `spec-strengthen` |
| **Date** | 2026-06-05 |
| **Verdict** | applied |
| **Patch shape** | 2 (additive — Pattern G) |
| **Impact verdict** | `additive` |
| **Acceptance** | PASS (all 4 gates) |
| **File** | `proof/invariant-abstract/KHeap_AI.thy` |
| **Base** | [[0019]] + [[0020]] applied |

## What changed

Inserted right after [[0020]]'s `set_object_cur_thread[wp]`:

```isabelle
lemma set_object_cur_domain[wp]:
  "\<lbrace>\<lambda>s. P (cur_domain s)\<rbrace> set_object p ko \<lbrace>\<lambda>_ s. P (cur_domain s)\<rbrace>"
  by (wpsimp wp: set_object_wp_strong)
```

Third Pattern G frame lemma on `set_object`. Builds out the
scheduler-domain-side frame coverage analogous to [[0020]]'s
thread-side coverage.

## Pre-flight followed the playbook recipe

Per the newly-added §"Pattern G gotcha" in
[`references/spec-strengthen-playbook.md`](../../../.claude/skills/isabelle_prover_spec/references/spec-strengthen-playbook.md),
applied the pre-flight before writing the patch:

```
$ grep -rn 'set_object_cur_domain\b' verification/l4v/    # direct
$ grep -rn 'crunch cur_domain.*set_simple_ko\|crunch cur_domain.*set_cap\|...' \
     verification/l4v/proof/invariant-abstract/             # crunch-derived
```

Both returned zero hits — `cur_domain` is a clean field for
`set_object` Pattern G. The recipe (born from the [[0020]]
`interrupt_states` collision) saved a doomed `--patch` run.

## Acceptance gate trace

| Gate | Result |
|---|---|
| 1. `check-theory.sh --patch` returns OK | ✓ 24,773 ms |
| 2. `spec_impact` emits non-weakening + accepting verdict | ✓ `additive` |
| 3. Trial wall ≤ baseline × 1.30 | ✓ **−4.6%** (25,954 → 24,773) |
| 4. Parent SKILL hard rules inherited | ✓ |

## Impact on seL4 (上下游)

| Phase | Wall | Notes |
|---|---:|---|
| baseline (post-0020) | 25,954 ms | KHeap_AI with cdt + cur_thread frames applied |
| trial (with patch) | 24,773 ms | −4.6% |
| apply re-verifies | 24,238 ms | within noise of trial |

Another measurable wp-class pickup. The pattern from
[[0015]]-[[0020]] continues: each new `set_<op>_<field>[wp]`
rule extends wp's automation surface, and same-file wall responds
when the field is referenced in that file's proof obligations.

`cur_domain` references in KHeap_AI come mostly from invariants
like `valid_sched_action` / `valid_sched_release_queue` (scheduling
invariants that occasionally need to commute past heap writes).

### Cross-file consumers

Tier-2 grep: 0 lines / 0 files. Downstream value lives in
scheduler proofs (DetSchedSchedule_AI.thy) and any invariant
proof that needs `cur_domain` preservation across a `set_object`
chain.

### Cross-session

NOT rebuilt. Standard additive-`[wp]`-rule reasoning applies:
zero downstream proof breakage; potential further speedup in
Refine deferred.

## PR description fields

| Field | Value |
|---|---|
| Lemma | `set_object_cur_domain[wp]` (new) |
| File | `verification/l4v/proof/invariant-abstract/KHeap_AI.thy` (line 1289 post-apply) |
| Baseline wall | `25,954 ms` |
| Trial wall from `--apply` | `24,238 ms` |
| Experiment ID | `0021-set-object-cur-domain-frame-lemma` |

## Notes / follow-ups

- l4v submodule pointer unchanged.
- The playbook's new "Pattern G gotcha — crunch generates
  implicit set-object frames" pre-flight is now in effect.
  This experiment is the first to apply it positively (the
  field passed the screen and the patch succeeded). [[0020]]'s
  aborted attempt was the negative-test case that motivated the
  recipe.
- Three slots filled in the `set_object_<field>[wp]` triplet
  this batch: `cdt`, `cur_thread`, `cur_domain`. [[0022]] adds
  `arch_state`. Remaining truly-missing slots (per pre-flight):
  `domain_index`, `domain_time`. The other "missing" ones from
  the [[0019]] survey (`idle_thread`, `scheduler_action`,
  `interrupt_irq_node`, `ready_queues`) are crunch-derived and
  not safe to add by hand.
