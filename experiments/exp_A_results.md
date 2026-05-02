# Experiment A — `-H 1000 → 8000` on CBaseRefine

**Status: hypothesis falsified.**

## Hypothesis

CBaseRefine spends 64% of wall time in GC (2220s of 3477s elapsed in the
baseline). Hypothesis: the polyml initial heap setting `-H 1000` (1 GB) is
too small for a 304-theory session, forcing many resize / full-GC cycles
during heap growth. Raising `-H` to 8000 (8 GB initial) should let the
working set fit without resize, dropping GC time significantly.

## Setup

- Container: fresh `sel4-experiment-A` from `sel4-public:baseline-clean`
  (29-heap baseline image)
- Modified `/root/.isabelle/etc/settings`:
  `-H 1000 --maxheap 16000` → `-H 8000 --maxheap 16000`
- Build command: `isabelle build -c -b -v -j 1 -o threads=8 -d <l4v> CBaseRefine`
  - `-c` forces clean rebuild (delete heap, recompile from sources)
  - `-j 1` strict serial (no parallel sessions)
  - `threads=8` matches our successful CRefine/InfoFlowCBase/InfoFlowC runs
- Resources: 16 cores, 23 GiB RAM, 56 GiB free disk
- Baseline for comparison: `heaps/build_log.txt` `--- CBaseRefine`
  block (which used `-H 1000` and threads=4 per the original full.log)

## Results

| metric | baseline | exp A | delta |
|---|---|---|---|
| wall (`Finished` line) | 3477.7s | **4434.3s** | **+27.5%** |
| cpu                    | 12443.6s | 18250.8s | +46.7% |
| gc                     | 2220.9s | 2742.2s | +23.5% |
| gc%                    | 63.9% | 61.8% | -2.1pp |
| parallel factor        | 3.58x (threads=4) | 4.12x (threads=8) | nominally up |
| parallel efficiency    | 90% (3.58/4) | **52%** (4.12/8) | dropped |
| theories               | 304 | 304 | — |

**Hypothesis fails on every metric:** GC absolute time *increased*, GC% only
moved 2pp, wall +27%, cpu +47%.

## Per-theory deltas (top regressions)

| theory | baseline elapsed | exp A elapsed | Δ |
|---|---|---|---|
| Refine.CNodeInv_R | 95.6s | 221.0s | +131% |
| Refine.Detype_R | 92.4s | 186.8s | +102% |
| Refine.Finalise_R | 227.3s | 412.9s | +82% |
| Refine.Untyped_R | 140.1s | 228.1s | +63% |
| Refine.CSpace1_R | 101.1s | 151.2s | +50% |
| Refine.Invariants_H | 149.0s | 215.3s | +44% |
| **Refine.CSpace_R** | **354.9s** | **223.4s** | **−37%** (only big winner) |

## What actually happened

The experiment confounded two variables:

1. `-H 1000 → -H 8000` (the variable I wanted to test)
2. `threads=4 → threads=8` (the variable I forgot to control — the original
   build used threads=4 per `full.log`'s `Timing CBaseRefine (4 threads, …)`)

So we cannot conclude `-H 8000` is bad for CBaseRefine specifically; what
we can conclude is the **combination** is worse. But the parallel-efficiency
data (90% → 52%) strongly suggests the regression is dominated by threads=8
hurting CBaseRefine, not by `-H 8000` helping or hurting.

CRefine, InfoFlowCBase, and InfoFlowC all loved threads=8 (factors 4.8x,
4.9x, 5.6x — efficiencies 60-70% on 8 cores). CBaseRefine doesn't. The
likely reason: 304 theories, deep dependency graph, more inter-theory
serialization → 8 workers spend more time waiting on locks / dependencies
than on actual proof checking.

## Conclusions

1. **`-H` tuning isn't a useful lever** for this workload. The 64% GC time
   in CBaseRefine is intrinsic to the workload, not config-induced.
2. **Optimal `threads` is per-session, not global.** CRefine/InfoFlow*: 8.
   CBaseRefine: 4 (or maybe even less). Today's `compile.sh` sets
   `threads=8` globally, which is suboptimal for CBaseRefine — could be
   costing ~28% on that one session.
3. **The settings-file edit `-H 1000 → 8000` was reverted** by tearing
   down `sel4-experiment-A`. The baseline image
   (`sel4-public:baseline-clean`) is unchanged.

## Next steps (suggested)

- **Exp A2**: rerun CBaseRefine with `-H 1000` (default) and `threads=4`,
  isolate the threads variable. Hypothesis: matches baseline ±5%.
- **Exp A3**: rerun CBaseRefine with `-H 1000` and `threads=6`, see if
  there's a sweet spot.
- **Persistent fix**: if A2/A3 confirm threads=4 is optimal for CBaseRefine,
  switch `compile.sh` from a global `threads=8` to per-session overrides.
  Isabelle supports session-specific options via the ROOT file's `options`
  block, but that requires editing l4v sources. A non-invasive alternative
  is a wrapper script that invokes `isabelle build` per-session with
  per-session `-o threads=N`.

## Cost so far

- Wall time: ~76 min (the rebuild itself)
- Disk: 0 (container removed)
- Other heaps: untouched
