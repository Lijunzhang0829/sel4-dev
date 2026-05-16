# cstr-2graph — Branch Summary

Three layers of build-wall optimization for the seL4 proof,
each operating at a different granularity of the verification stack:

| Layer | Granularity | Knob | Measured effect |
|---|---|---|---:|
| 1. ROOT | session topology | `+` parent vs `sessions` declaration | **−33.1%** canonical TUNED total |
| 2. theory | per-`.thy` placement in sessions | move files between session dirs | −48% on the moved subset (coupled to L1) |
| 3. lemma | per-tactic inside one proof | terminating `force` → `fastforce` | −13~17% per touched file (composes additively) |

All three were applied / verified on top of an unmodified `seL4/l4v` upstream;
proof content (lemma statements) is unchanged except for the two named
tactic refinements in layer 3. `tools/lemma_inventory/diff.py` returns 0
on layers 1 and 2.

The actual proof-side changes are committed inside
`verification/l4v` as commit **`bb65974`** on branch `optimize-refine-build`.
The contents below are the analysis + tooling that derived those changes,
plus the `experiments/` patches that materialise them.

---

## Diagnosis tools built in this branch

All under `tools/critical_path/`:

| Tool | What it does | Key output |
|---|---|---|
| `parse_theory_imports.py` | Walk all `.thy` files, extract `imports` declarations, build theory DAG | `reports/theory-dag.json` (1094 nodes, 1961 edges) |
| `build_session_dag.py` | Parse all `ROOT` files, build session DAG | `reports/session-dag.json` (56 nodes, 161 edges) |
| `session_dedupe_scan.py` | Read all `theory_timings` BLOBs, find theories appearing in multiple sessions (CSTR) | `reports/session-duplication-scan.{md,json}` |
| `move_simulator.py` | For each residual cross-session edge, classify intervention feasibility (SWAP / SPLIT / MERGE) | `reports/move-simulator.md` |
| `split_simulator.py` | Import-closure analysis: when SWAP rejected, is origin splittable? | `reports/split-simulator.md` |
| `theory_axis_2d.py` | 2D classification: source pillar × dependency-closure pillar | `reports/theory-axis-2d.{md,json}` |
| `lemma_targets.py` | Rank lemmas across top theories by `log(wall) × size × pressure_weight` | `reports/lemma-optimization-targets.md` |

Plus the parser inside `.claude/skills/isabelle_prover/scripts-container/proof_parser.py`
(by/done/sorry/oops terminator-aware, Isar-depth-tracking, sub-proof and
body-pressure-aware), and its 17-test unit suite.

---

## Layer 1 — ROOT swap (`experiments/cbaserefine-swap-parent.patch`)

3 lines of `proof/ROOT` change:

```diff
- session CBaseRefine in "crefine/base" = CSpec +
+ session CBaseRefine in "crefine/base" = Refine +
    sessions
-     CLib Refine AutoCorres
+     CLib CSpec AutoCorres

- session CRefineSyscall in "crefine/intermediate" = CBaseRefine +
-   sessions
-     CRefine
+ session CRefineSyscall in "crefine/intermediate" = CRefine +
```

**Mechanism**: Isabelle's `+ X` does `loadHierarchy` (millisecond heap merge);
`sessions X` only declares namespace, forcing source re-execution of any
imported theory. Pre-swap, the bigger heap (Refine, ~1.7k wall) was being
source-re-executed inside CBaseRefine; swap promotes the bigger side to
heap-merge.

**Effect** (full canonical rebuild, b15924b):

| Session | pre-swap wall | post-swap wall | Δ |
|---|---:|---:|---:|
| CBaseRefine | 5183s | 1196s | **−77%** |
| CRefineSyscall | 3307s | 1.1s | **−99.97%** |
| CRefine | 4557s | 4039s | −11.4% |
| InfoFlowCBase/C | 1955s | 2177s | +11.4% (downstream regression) |
| **canonical TUNED total** | **25331s** | **16942s** | **−33.1%** |

---

## Layer 2 — Theory pillar re-alignment (`experiments/misalignment-moves.patch`)

5 `.thy` file moves from `proof/crefine/ARM/` → `proof/refine/ARM/`:

```
IsolatedThreadAction.thy
Fastpath_Equiv.thy
Fastpath_Defs.thy
ArchMove_C.thy
Move_C.thy           (lives at proof/crefine/ → proof/refine/)
```

Plus 4 importer files in CRefine updated to use FQN `"Refine.X"`,
and the Refine session block in `proof/ROOT` extended with the 5 entries.

**Rationale** (from `reports/theory-axis-2d.md`): each of these theories'
transitive import closure stays within `{haskell, lib, spec, proof}` —
never touches the `c` pillar. They are Haskell-axis helpers misfiled in
CRefine session dir; correct home is Refine.

**Effect** (when applied on top of Layer 1):

| | Pre-misalignment | Post-misalignment | Δ |
|---|---:|---:|---:|
| 5 theories total ambient wall | 115.6s (in CRefine ML state) | 60.1s (in Refine ML state) | **−48%** |
| CRefine sum_elapsed | 2103.9s | 1268.4s | **−39.7%** (direct attribution ~115s, indirect ~720s — single run, not control-validated) |
| `Refine.*` entries in CRefine.db BLOB | (would be there pre-swap) | **0** | heap-merge through 3-level chain works |

**Critical coupling**: this layer ONLY helps when Layer 1 is also applied.
Without the CBaseRefine swap, the moved theories run twice (in Refine
build + via `sessions Refine` source-re-exec in CRefine), costing +17s
net (proved empirically by a prior trial run).

---

## Layer 3 — Lemma tactic refinement (2 patches, in `bb65974`)

| File:line | Lemma | Change | Effect |
|---|---|---|---:|
| `Schedule_R.thy:1364` | `corres_assert_assume_r` | `by (force simp: ...)` → `by (fastforce simp: ...)` | −13.2% file wall |
| `StateRelation.thy:756` | `ekheap_relation_absD` | `by (force simp add: ...)` → `by (fastforce simp add: ...)` | −17.0% file wall |

**Methodology**: 8 candidate A/B tests via `check-theory.sh --patch`:
- 2 wins (both: terminating `by (force <hint>)` with no `dest!`/`elim!`/`intro!` modifier)
- 2 neutral (mid-proof `apply (force …)` and `by (auto …)`)
- 3 losses (`elim! → elim` failed to reproduce skill prior, even on `delta_sym_refs` which had −24% prior)
- 1 search explosion (`force …dest!:` → fastforce timed out at 600s ceiling)

Empirical narrow winning pattern:
**terminating `by (force <safe-modifiers>)` only**.

Per-patch wall savings folded to canonical: ~3-5s each.

---

## Caveats applying as-is to upstream

1. **Multi-arch incomplete**: only the ARM variants are moved in layer 2.
   The other 4 arches (ARM_HYP/RISCV64/X64/AARCH64) have parallel
   `IsolatedThreadAction.thy` / etc. that would need similar treatment
   for `L4V_ARCH=X64 isabelle build` etc. to work.
2. **Layer 2 strictly requires Layer 1** — see "critical coupling" above.
3. **Layer 2 sub-blocks**: the 5 new entries in Refine ROOT are written
   into all 3 conditional `theories` blocks (QUICK_AND_DIRTY,
   SKIP_REFINE_PROOFS, unconditional). The `skip_proofs` semantics
   for these moved theories under the SKIP condition was not validated.
4. **`apply_success_rate` for layer 3 is ~25%** — each candidate needs an
   individual A/B test; batch swaps regress per the skill's v6 anti-pattern.
