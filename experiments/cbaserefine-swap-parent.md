# Experiment: CBaseRefine + CRefineSyscall parent swap (target a, P0.5 ROOT inheritance dedupe)

## Context

P0.5 finding (commit `3cf5194`): `verification/l4v/proof/ROOT:106` declares
```
session CBaseRefine in "crefine/base" = CSpec +
  sessions
    CLib
    Refine
    AutoCorres
```

The `+ CSpec` heap-merges CSpec (149s) cleanly. But `sessions Refine` is a
namespace declaration only — it does NOT merge Refine.heap. Consequently,
when CBaseRefine theories `imports "Refine.X"`, Isabelle re-executes the
source of those theories in CBaseRefine's CSpec-derived ML state.

Empirical impact (from [reports/session-duplication-scan.md](../reports/session-duplication-scan.md)):
- 45 of 48 Refine theories appear in CBaseRefine.db with elapsed times
  larger than in Refine.db (different ML state pulls more locale work)
- Aggregate cost: Refine 3538s + AInvs 1416s + BaseRefine 342s = **5296s**
  of duplicated work, ~98.6% of CBaseRefine's session sum

## Hypothesis

Swap parent and `sessions` direction:

```diff
- session CBaseRefine in "crefine/base" = CSpec +
+ session CBaseRefine in "crefine/base" = Refine +
    sessions
      CLib
-     Refine
+     CSpec
      AutoCorres
```

After swap:
- Refine.heap (with AInvs + BaseRefine + ASpec + Word_Lib + HOL upstream)
  is heap-merged → those 45+119+63 theories no longer reprocessed
- CSpec becomes the reprocessed side → ~149s aggregate overhead added
- Net aggregate save: ~5296 − 149 = **~5150s** (~93% of current dup overhead)

Wall save estimate (factor 3.87 intra-session parallelism on CBaseRefine):
~5150 / 3.87 = **~1330s** wall reduction. CBaseRefine wall would drop from
5183s to ~3850s.

## Risk assessment

**Low**. Argument:
- The current configuration successfully loads BOTH Refine and CSpec
  symbols into CBaseRefine's ML state during build (Refine via source
  re-execution from `sessions` namespace; CSpec via `+` heap merge)
- Therefore there are no fundamental namespace collisions between Refine
  and CSpec content
- The swap merely relocates which side is heap-merged vs source-rebuilt;
  the final ML state should contain the same definitions
- Both directions go through the same `Word_Lib + HOL` shared base

Possible failure modes:
1. **Locale interpretation order dependency** — if CBaseRefine theories
   transitively expect a specific load order (e.g., CParser definitions
   loaded before some Refine locale), swapping could break. But:
   `L4VerifiedLinks` and `Include_C` are explicitly designed to bridge
   the two, so order-dependence is unlikely to surface in source theories.
2. **C-side syntax bundle preceding spec** — CParser registers some
   syntax bundles that ASpec might rely on or conflict with. Unlikely
   given current build works.
3. **Slot-sharing oracle state** — Isabelle 2024 oracle table is
   per-session; mixing Refine + CSpec via either direction shouldn't differ.

**Failure containment**: any ML-level conflict surfaces in seconds during
heap merge or in the first imported theory load (within ~1 minute).
Long-tail validation failures unlikely.

## Validation status

| stage | status | notes |
|---|---|---|
| ROOT structural diff | ✓ applied | [cbaserefine-swap-parent.patch](cbaserefine-swap-parent.patch) |
| `isabelle build -n -d . CBaseRefine` (dry-run) | ✓ pass | topo order shows Refine + CSpec correctly precede CBaseRefine |
| Actual incremental CBaseRefine build | **✓ PASS** | 25:48 wall; see Results below |
| lemma_inventory diff | n/a | ROOT change only, no .thy modifications |
| canonical TUNED rebuild + wall comparison | pending | optional full-7h confirmation |

## Results (2026-05-09T03:17Z, host = lijun-VMware-Virtual-Platform)

Run log: [cbaserefine-swap-runs/20260509T025109Z.log](cbaserefine-swap-runs/20260509T025109Z.log)

### Headline timing comparison

```
                        baseline (= CSpec +)        swap (= Refine +)        delta
  threads                            8                       8
  elapsed (wall)              5183.2s                  1318.3s            -3865s   -74.6%
  cpu                        20053.4s                  5631.9s          -14421s   -71.9%
  gc                          3446.0s                   291.9s           -3154s   -91.5%
  factor (cpu/wall)              3.87                    4.27             +0.40
```

**Wall save: 3865s (≈ 64 minutes) on a single session — far exceeds the
1330s estimate in the original Hypothesis section.** Reason: P0.5
amplification was bidirectional. In the old config, Refine theories
re-executed in CSpec-derived context took ~1.5–2× longer than in their
native session (Finalise_R: 226s → 424s). Heap-merging via `+ Refine`
eliminates BOTH the original-side cost AND the amplification.

### CBaseRefine.db structural change

P0.5 finding directly verified: the duplication footprint changed
exactly as predicted by the swap.

```
                    OLD                     NEW                  delta
  total theories    304                     94                  -210
  sum_elapsed       5598.7s                 1237.7s             -4361.0s

  per-session prefix breakdown:
    Refine          38 thys / 3504.4s       0 / 0.0s            heap-merged ✓
    AInvs           79 thys / 1329.8s       0 / 0.0s            heap-merged ✓
    ExecSpec        71 thys /  370.2s       0 / 0.0s            heap-merged ✓
    ASpec           36 thys /  183.6s       0 / 0.0s            heap-merged ✓
    Lib/Monads/...  52 thys /  130.0s       0 / 0.0s            heap-merged ✓
    BaseRefine       1 thy  /    9.5s       0 / 0.0s            heap-merged ✓
    ─────────────────────────────────────────────────────────────
                    REMOVED 277 thys / 5527.5s aggregate
    ─────────────────────────────────────────────────────────────
    CKernel          0                      1 thy  / 764.0s     re-executed (Kernel_C)
    CSpec            0                      7 thys / 199.2s     re-executed
    CParser          0                     33 thys / 116.0s     re-executed
    Simpl-VCG        0                     14 thys /  57.4s     re-executed
    HOL-Statespace   0                      3 thys /  11.5s     re-executed
    AsmRefine        0                      3 thys /   6.9s     re-executed
    HOL-Library      0                      1 thy  /   7.1s     re-executed
    CLib             2 thys /    7.2s       4 thys /   9.9s     marginal
    AutoCorres      25 thys /   41.7s      25 thys /  38.5s     marginal (heap loaded?)
    ─────────────────────────────────────────────────────────────
                    ADDED 66 thys / 1166.6s aggregate
    ─────────────────────────────────────────────────────────────
                    NET:  -210 thys / -4361s aggregate
```

The trade is favourable: removed 5527s of Refine-system reprocessing
(plus its amplification) for 1166s of C-system reprocessing.

### Non-fatal warnings observed

The build emitted two `*** Missing session sources entry "$L4V_ARCH/..."`
warnings before continuing to successful completion:

```
*** Missing session sources entry "/sel4-project/verification/l4v/tools/c-parser/umm_heap/$L4V_ARCH/TargetNumbers.ML"
*** Missing session sources entry "/sel4-project/verification/l4v/spec/cspec/c/build/$L4V_ARCH/kernel_all.c_pp"
```

These are Scala-side `Isabelle.Session.manager` source-tracking complaints
about unexpanded `$L4V_ARCH` in non-thy file paths declared by CParser /
CSpec. The Poly/ML side resolves them via a different path with proper
expansion, so compilation proceeds. **Likely cosmetic** but worth
investigating if incremental rebuild dependency-tracking is impacted.

### Heap state after build

- `CBaseRefine.heap`: 592 MB (timestamp 2026-05-09 03:17 UTC)
- `CBaseRefine.db`: 14.8 MB
- All other heaps untouched (Refine, CSpec, CKernel, etc. still from canonical baseline)

## Run 2 (2026-05-09T04:36Z): CRefineSyscall parent swap downstream test

After validating CBaseRefine swap, applied an analogous swap to
CRefineSyscall (commit f9f7597 → next):

```diff
- session CRefineSyscall in "crefine/intermediate" = CBaseRefine +
-   sessions
-     CRefine
+ session CRefineSyscall in "crefine/intermediate" = CRefine +
```

CRefineSyscall declared 100% duplication of CRefine theories in P0.5
(`= CBaseRefine + sessions CRefine` source-re-executes 44 CRefine
theories, 1546s aggregate). Swap merges CRefine.heap properly via `+`;
CRefine's own parent is CBaseRefine, so the chain
CRefineSyscall → CRefine → CBaseRefine → Refine + CSpec is preserved.

Wiped CRefine + CRefineSyscall heaps to test cascade rebuild.

Run log: [cbaserefine-swap-runs/20260509T032551Z-crefinesyscall.log](cbaserefine-swap-runs/20260509T032551Z-crefinesyscall.log)

### CRefine: parity-with-bonus-GC-savings

```
                        baseline (= CBaseRefine +)    swap-cascade        delta
  threads                            8                        8
  elapsed (wall)              4556.6s                  4038.8s           -518s    -11.4%
  cpu                        20590.9s                 18538.7s          -2052s    -10.0%
  gc                          4689.3s                  2426.3s          -2263s    -48.3%
  factor                         4.52                     4.59           +0.07
```

CRefine's session structure didn't change directly. The wall reduction
comes from inheriting the smaller / cleaner new CBaseRefine.heap as
parent — primarily lower GC pressure (-48% GC time). This corroborates
the hypothesis that P0.5 amplification was driven by ML-state pollution.

### CRefineSyscall: pure-duplication elimination

```
                        baseline (= CBaseRefine + sessions CRefine)    swap (= CRefine +)    delta
  threads                            8                                          8
  elapsed (wall)              3306.7s                                       1.158s         -3305.5s   -99.97%
  cpu                        14336.6s                                       1.668s        -14334.9s
  gc                          1149.4s                                       0.000s         -1149.4s
  factor                         4.34                                        1.44
```

The result is dramatic: CRefineSyscall's actual own work is just
`Intermediate_C` (a small bridging theory). The baseline 3306s was
**entirely** the cost of source-re-executing the full CRefine session
inside CRefineSyscall's ML state. With heap-merge via `+`, that work is
zero.

The session log makes this explicit:
```
Building CRefineSyscall ...
CRefineSyscall: theory CRefineSyscall.Intermediate_C
Timing CRefineSyscall (8 threads, 1.158s elapsed time, ...)
```

44 CRefine theories that previously appeared in CRefineSyscall.db
(P0.5 finding: 100% duplication, 1546s aggregate) are now entirely
absent — they live in CRefine.heap, merged on session start.

### Combined proof-core wall savings (3 sessions)

```
                         baseline       swap         delta wall         %
  CBaseRefine            5183.2s        1318.3s     -3864.9s          -74.6%
  CRefine                4556.6s        4038.8s      -517.8s          -11.4%
  CRefineSyscall         3306.7s           1.2s     -3305.5s          -99.97%
  ─────────────────────────────────────────────────────────────────
  proof-core total      13046.5s        5358.3s     -7688.2s          -58.9%
```

**Proof-core (CBaseRefine + CRefine + CRefineSyscall) wall reduced by
2 hours 8 minutes (-58.9%).** Total canonical TUNED build wall is
25,331s — this is **30.4%** of total wall.

### Heaps state after Run 2

- `CRefine.heap`: rebuilt (timestamp 2026-05-09 ~04:33 UTC, replaces baseline)
- `CRefineSyscall.heap`: 5.4 MB (timestamp 2026-05-09 04:36 UTC, vs baseline ~?)
- `CRefineSyscall.db`: 152 KB (vs baseline several MB) — collapsed dup metadata

Downstream of CBaseRefine: AutoCorresCRefine, InfoFlowCBase, InfoFlowC
heaps remain stale (built on old CBaseRefine.heap). They will be
rebuilt next.

## Run 3 (2026-05-09T05:19Z): downstream rebuild — InfoFlowCBase + InfoFlowC

After validating the upstream swaps, wiped InfoFlowCBase + InfoFlowC +
AutoCorresCRefine heaps and rebuilt InfoFlowCBase + InfoFlowC (the two
downstream sessions on the canonical Theorem-3 chain). Goal: confirm
they still build under the new chain, and measure any wall regression.

(AutoCorresCRefine excluded by `compile.sh` per
[reports/baseline-restore.md](../reports/baseline-restore.md), so no
baseline to compare against.)

Run log: [cbaserefine-swap-runs/20260509T044018Z-downstream.log](cbaserefine-swap-runs/20260509T044018Z-downstream.log)

### Result: builds succeed, but small wall regression downstream

```
                       baseline        swap (Run 3)         delta wall
  InfoFlowCBase       1109.9s          1217.9s             +108s   +9.7%
    cpu               4793.3s          5349.6s             +556s   +11.6%
    gc                 194.0s           257.2s              +63s   +32.5%
    factor             4.32             4.39                +0.07

  InfoFlowC            844.7s           958.7s             +114s   +13.5%
    cpu               2847.2s          3054.3s             +207s   +7.3%
    gc                 137.3s            51.2s              -86s   -62.7%
    factor             3.37             3.19               -0.18
```

Both sessions completed successfully (EXIT=0, heaps written). The wall
regression is modest (~+222s combined) but **opposite in sign** from
the upstream sessions where wall fell sharply.

### Why downstream regressed

Two structural reasons:

1. **InfoFlowCBase has its own P0.5 issue** independent of the
   CBaseRefine swap: `session InfoFlowCBase = CRefine + sessions InfoFlow Access`.
   This means InfoFlow + Access source-re-execute inside InfoFlowCBase
   (P0.5 reported 99.4% dup, 644s aggregate). The CBaseRefine swap does
   not touch this; only an analogous swap on InfoFlowCBase itself would.

2. **The new CRefine.heap has a different ML state layout**. Under the
   old chain (`CRefine = CBaseRefine = CSpec + sessions Refine`), the
   CRefine.heap incorporated source-re-executed Refine state. Under the
   new chain (`CRefine = CBaseRefine = Refine + sessions CSpec`), it
   incorporates source-re-executed CSpec state. When InfoFlowCBase
   loads CRefine.heap, deserialization + locale interpretation costs
   may differ slightly. InfoFlowCBase's `sessions InfoFlow Access`
   reprocessing also runs against the new layout, possibly hitting
   slightly different rule-set sizes during tactic search.

The InfoFlowC GC actually went DOWN (-87s), so the regression is not
uniformly a GC issue. cpu went up modestly (+207s vs +556s for
InfoFlowCBase), suggesting InfoFlowCBase is the main affected session.

### Net result across all 5 affected sessions

```
                       baseline       swap          delta wall    %
  CBaseRefine         5183.2s         1318.3s      -3864.9s    -74.6%
  CRefine             4556.6s         4038.8s       -517.8s    -11.4%
  CRefineSyscall      3306.7s            1.2s     -3305.5s    -99.97%
  InfoFlowCBase       1109.9s         1217.9s        +108s     +9.7%
  InfoFlowC            844.7s          958.7s        +114s     +13.5%
  ──────────────────────────────────────────────────────────
  total              15001.1s         7534.9s     -7466.2s    -49.8%
```

**Net proof-side wall reduction: ~7466s (~2 hours 4 minutes) across
the 5 sessions affected by these two ROOT swaps. ~29.5% of the entire
canonical TUNED build wall (25,331s).**

### Pending: optimisation candidate (d1) — InfoFlowCBase swap

The +222s regression on InfoFlowCBase + InfoFlowC suggests an analogous
swap could turn it into a further win:

```diff
- session InfoFlowCBase = CRefine +
-   sessions InfoFlow Access ...
+ session InfoFlowCBase = InfoFlow +    (or = Access +)
+   sessions ... appropriate other side ...
```

P0.5 says InfoFlowCBase's 99.4% dup splits as InfoFlow 371s + Access 273s
+ DPolicy 63s. The natural new parent is whichever of InfoFlow / Access
has the most dup overhead. Worth testing as a follow-up after the
canonical rebuild.

## How to apply

```bash
cd verification/l4v
git apply ../../experiments/cbaserefine-swap-parent.patch
```

## How to revert

```bash
cd verification/l4v
git checkout proof/ROOT
```

(Working tree of `verification/l4v` is detached at `seL4-13.0.0` —
modifications there are untracked from the outer repo's perspective.)

## Next steps

1. ~~Incremental empirical test~~ — **done above**. Wall 1318s; -3865s
   vs baseline; far exceeds initial estimate.

2. **Apply analogous swap to CRefineSyscall** —
   ```
   - session CRefineSyscall ... = CBaseRefine +
   -   sessions CRefine
   + session CRefineSyscall ... = CRefine +
   ```
   CRefineSyscall is CBaseRefine + sessions CRefine, with CRefineSyscall
   showing 100% duplication of CRefine in P0.5. CRefine has CBaseRefine
   as parent already, so swapping CRefineSyscall to `= CRefine +` gets
   both heaps via parent chain (CRefineSyscall → CRefine → CBaseRefine →
   ... → Refine and ... → CSpec via the new CBaseRefine).

   Expected wall save (analogous reasoning): ~400-700s on CRefineSyscall
   (baseline 3306s with ~1546s P0.5 dup overhead).

3. **Full canonical rebuild for definitive confirmation** — once both
   CBaseRefine and CRefineSyscall swaps land, run
   `tools/rebuild_canonical.sh WIPE=1` end-to-end to get a directly
   comparable canonical build_log.txt. Resource cost: ~6h (proof side
   ~30% faster, other sessions unchanged).

4. **Investigate `Missing session sources entry` warnings** — non-fatal
   here, but worth confirming whether they affect Isabelle's
   incremental-rebuild dependency tracking (e.g. does editing
   `kernel_all.c_pp` correctly invalidate CBaseRefine downstream?).
   If the warnings are cosmetic-only, document and move on; if they
   break dep tracking, may need to also update CBaseRefine's ROOT to
   re-declare some `directories "$L4V_ARCH"` or absolute paths.

5. **Update [reports/critical-path-summary.md](../reports/critical-path-summary.md)
   target (a) section** — replace upper-bound estimate (~5500s) with
   measured single-session ≥3865s wall recovery.

6. **Optional: verify C-side downstream still build** — the new
   CBaseRefine.heap is structurally different (parent chain Refine
   instead of CSpec). CRefine and CRefineSyscall depend on
   CBaseRefine, so they'd inherit the new structure when rebuilt.
   Validate that they still build correctly (incremental test).

## Files in this experiment

- [cbaserefine-swap-parent.patch](cbaserefine-swap-parent.patch) — the ROOT diff
- this `cbaserefine-swap-parent.md` — record + context

## Branch

`experiment/cbaserefine-swap-parent` (off `experiment/dag-critical-path`),
both off `baseline`.

The ROOT modification itself sits in the inner `verification/l4v/` repo
(detached HEAD at `seL4-13.0.0`), not in this outer-repo branch — the
patch file is the durable record.
