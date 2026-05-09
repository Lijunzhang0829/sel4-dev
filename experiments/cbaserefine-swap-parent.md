# Experiment: CBaseRefine parent swap (target a, P0.5 ROOT inheritance dedupe)

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
| `isabelle build -n -d . CBaseRefine` (dry-run) | ✓ pass | exit=1 expected (heap stale); topo order shows Refine + CSpec correctly precede CBaseRefine in build chain |
| Actual incremental build | **pending** | requires ~1.5h on host (canonical config); see "Next steps" |
| lemma_inventory diff | n/a | ROOT change only, no .thy modifications |
| canonical TUNED rebuild + wall comparison | **pending** | full ~7h |

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

1. **Incremental empirical test** — run `isabelle build CBaseRefine` from
   the modified ROOT against a wiped CBaseRefine heap (keep all other
   heaps). Expected: ~3850s wall vs current 5183s baseline. Resource
   cost: ~1.5h on host.

2. **Full canonical rebuild** — once incremental passes, run
   `tools/rebuild_canonical.sh` end-to-end with WIPE=1 to get a
   directly-comparable build_log.txt for the swapped configuration.
   Resource cost: ~7h (~6h if other sessions unchanged).

3. **If swap succeeds** — apply the same logical swap to:
   - `CRefineSyscall = CBaseRefine + sessions CRefine` →
     `CRefineSyscall = CRefine +` (and remove `sessions CRefine`).
     Expected wall save: ~400s (1546s aggregate / 4.34 factor).

4. **Document outcome** — update [reports/critical-path-summary.md]
   (../reports/critical-path-summary.md) target (a) section with
   measured numbers.

## Files in this experiment

- [cbaserefine-swap-parent.patch](cbaserefine-swap-parent.patch) — the ROOT diff
- this `cbaserefine-swap-parent.md` — record + context

## Branch

`experiment/cbaserefine-swap-parent` (off `experiment/dag-critical-path`),
both off `baseline`.

The ROOT modification itself sits in the inner `verification/l4v/` repo
(detached HEAD at `seL4-13.0.0`), not in this outer-repo branch — the
patch file is the durable record.
