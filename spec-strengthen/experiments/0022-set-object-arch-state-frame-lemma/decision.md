# spec-0022 — `set_object_arch_state[wp]` Pattern G frame lemma (seL4-source PR)

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
| **Base** | [[0019]] + [[0020]] + [[0021]] |

## What changed

Inserted right after [[0021]]'s `set_object_cur_domain[wp]`:

```isabelle
lemma set_object_arch_state[wp]:
  "\<lbrace>\<lambda>s. P (arch_state s)\<rbrace> set_object p ko \<lbrace>\<lambda>_ s. P (arch_state s)\<rbrace>"
  by (wpsimp wp: set_object_wp_strong)
```

Fourth Pattern G frame lemma on `set_object`. `arch_state` carries
architecture-specific kernel state (ASID tables, ARM hardware regs
abstraction, etc.) — referenced extensively in arch invariants
(`valid_arch_state`, `valid_vspace_objs`, `valid_global_objs`,
...).

## Pre-flight

Per playbook recipe:
- `grep set_object_arch_state` → 0 hits ✓
- `grep 'crunch arch_state.*set_simple_ko|...crunch arch_state.*set_cap|...'` → 0 hits ✓

`arch_state` is one of the four clean fields the playbook pre-flight
identified for this batch (vs the 4 crunch-derived ones we skipped).

## Acceptance gate trace

| Gate | Result |
|---|---|
| 1. `check-theory.sh --patch` | ✓ OK 24,184 ms |
| 2. `spec_impact` verdict | ✓ `additive` |
| 3. Trial wall ≤ baseline × 1.30 | ✓ **−2.0%** (24,677 → 24,184) |
| 4. Parent SKILL hard rules | ✓ |

## Impact on seL4 (上下游)

| Phase | Wall | Notes |
|---|---:|---|
| baseline (post-0021) | 24,677 ms | cdt + cur_thread + cur_domain applied |
| trial | 24,184 ms | −2.0% |
| apply re-verifies | 24,229 ms | flat |

Modest wp-class pickup. `arch_state`-frame references in KHeap_AI
are uncommon (most arch-specific invariants live in
ArchAcc_AI.thy and downstream ARM-specific files), so the
in-file gain is small. The expected downstream value is in:

- **ArchAcc_AI.thy** — proofs that compose with `set_object`-using
  sequences and need `valid_arch_state` preservation.
- **VSpace_AI.thy / ArchVSpace_AI.thy** — vspace-object proofs
  that touch the heap.
- **Refine-tier arch state preservation lifts** — biggest
  downstream impact, not measured.

### Cross-file consumers / cross-session

Same as 0019-0021: brand new lemma, 0 immediate Tier-2 grep
consumers, Refine NOT rebuilt.

## PR description fields

| Field | Value |
|---|---|
| Lemma | `set_object_arch_state[wp]` (new) |
| File | `verification/l4v/proof/invariant-abstract/KHeap_AI.thy` (line 1293 post-apply) |
| Baseline wall | `24,677 ms` |
| Trial wall from `--apply` | `24,229 ms` |
| Experiment ID | `0022-set-object-arch-state-frame-lemma` |

## Notes / follow-ups

- l4v submodule pointer unchanged.
- Four Pattern G frame lemmas applied to `set_object` in this
  KHeap_AI batch ([[0019]] cdt, [[0020]] cur_thread, [[0021]]
  cur_domain, here arch_state). Cumulative KHeap_AI wall delta
  vs pre-batch baseline (24,538 ms → 24,229 ms) = **−1.3%** —
  the file got marginally faster despite adding 4 lemmas, because
  the per-rule wp-class pickups dominate over the additional
  parsing cost.
- Remaining truly-missing slots per pre-flight: `domain_index`,
  `domain_time`. Available for a future batch. `arch_state` is
  the highest-impact one I'm willing to take in this round —
  the others have less obvious downstream pickup.
