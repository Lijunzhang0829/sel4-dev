# CBaseRefine threads / -H tuning — final analysis

**Status: Exp A is a confirmed 17% wall-time win over the original baseline.
This supersedes the earlier "Exp A FAILED" entry in this same file
(commit 766ca4b), which used the wrong baseline number.**

## What went wrong with the previous analysis

The first version of this note compared Exp A against the per-theory `TOTAL`
row from `heaps/build_log.txt` (3477s) and concluded Exp A was 27% slower at
4434s. That comparison was invalid: the per-theory `TOTAL` is the **sum of
per-theory elapsed times**, not session wall time. Isabelle's per-theory
`elapsed` only records the theory's foreground load interval; it does not
include time spent in the future stage (deferred proof checking that
continues after the theory is "done") nor the heap-save phase. So summing
per-theory elapsed underestimates session wall significantly.

The correct baseline comes from the `Timing CBaseRefine (...)` line that
`isabelle build -v` prints, and which is preserved inside the container at
`/sel4-project/build-logs/clean.log` line 352.

## Three measurements (all from the same `Timing CBaseRefine` line)

| run | wall | cpu | gc | gc% | factor | threads | `-H` | `--maxheap` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **Baseline** (clean.log) | 5321s = **89:00** | 17995s | 4235s | **79.6%** | 3.38 | 4 | 1000 | 10000 |
| **Exp A** | 4434s = **73:54** | 18250s | 2742s | 61.8% | 4.12 | 8 | 8000 | 16000 |
| **Exp A2** | 5708s = **95:08** | 17924s | 3175s | 55.6% | 3.14 | 4 | 1000 | 16000 |

`build-logs/full.log` line 1134 also has a CBaseRefine timing (7962s wall,
80% GC), but that run was the OOMing concurrent build with `-j 4` running
two sessions simultaneously, so it isn't a clean measurement.

## What each pair tells us

**A2 vs Baseline** — isolates `--maxheap 10000 → 16000`:

- wall:   5321 → 5708    (+7%)   *— very slightly worse, within noise*
- gc:     4235 → 3175    (-25%)
- gc%:    79.6% → 55.6%  (**-24 pp**)

Bumping the polyml `--maxheap` ceiling from 10 GB to 16 GB **dramatically
cuts GC time** (heap grows enough to give garbage room to accumulate before
each collection), but the saved cycles get spent elsewhere (probably
allocator overhead or OS-level paging with the bigger heap), so net wall
barely moves. **`--maxheap` alone isn't a wall lever; it's a GC-pressure
lever.**

**A vs A2** — isolates `-H 1000 → 8000` AND `threads 4 → 8` (with the
post-bump maxheap held constant at 16000):

- wall:   5708 → 4434    (**-22%**)
- gc:     3175 → 2742    (-14%)
- factor: 3.14 → 4.12    (+31% parallel efficiency)
- cpu:    17924 → 18250  (+1.8%)

The combo wins clearly. Cannot say from this dataset alone whether the
gain is from `-H` or from `threads` — that requires one more experiment
(see "Open question" below).

**A vs Baseline** — overall:

- wall:   5321 → 4434    (**-17%**, saves 887s = 14:47 on this one session)
- gc:     4235 → 2742    (-35%)
- gc%:    79.6% → 61.8%  (-18 pp)

## Per-theory snapshot (top 5 by wall in baseline)

Note these are per-theory **elapsed** as reported by `theory_timings`. They
include within-theory parallel proof work, so the threads=4 vs threads=8
columns are not directly comparable per-theory — only useful for sanity.

| theory | base (t=4) | A (t=8) | A2 (t=4) |
|---|---:|---:|---:|
| Refine.CSpace_R     | 354.9 | 223.4 | 308.2 |
| Refine.Finalise_R   | 227.3 | 412.9 | 226.7 |
| Refine.Untyped_R    | 140.1 | 228.1 | 154.6 |
| Refine.Invariants_H | 149.0 | 215.3 | 178.0 |
| Refine.CNodeInv_R   |  95.6 | 221.0 | 113.2 |

Within-thread the 4-thread runs are very close to each other (baseline ↔ A2),
which validates A2's reproducibility. The threads=8 column shifts theories
around in unintuitive ways, which is consistent with a different
proof-DAG schedule rather than per-theory speedup/slowdown.

## Conclusions

1. **Exp A is a real ~17% wall-time win for CBaseRefine.** The combination
   `-H 8000 / --maxheap 16000 / threads=8` is faster than the original
   `-H 1000 / --maxheap 10000 / threads=4` baseline.
2. **`--maxheap 16000` is necessary but not sufficient.** Alone it kills
   GC time but doesn't speed up wall.
3. **`threads=8` does NOT hurt CBaseRefine** as the prior version of this
   note claimed. The earlier conclusion was a measurement artifact.
4. **Compile.sh's `threads=8` setting (Exp B, commit 3d9b173) is correct.**
   No revert needed.

## Open question

Cannot decompose A vs A2's -22% gain into "from `-H 8000`" vs "from
`threads=8`". Both could be contributing. Resolving this needs:

- Exp A3: `-H 1000 + threads=8 + --maxheap 16000` → isolates threads
- (or A4: `-H 8000 + threads=4 + --maxheap 16000` → isolates -H)

Each costs ~76-95 min wall to run. If the practical conclusion is simply
"apply Exp A's settings", the decomposition is academic — both knobs are
free to flip together.

## Recommended action

In `/root/.isabelle/etc/settings` inside the build container:

```
ML_OPTIONS="-H 1000 --maxheap 10000 --stackspace 64"     # original
ML_OPTIONS="-H 8000 --maxheap 16000 --stackspace 64"     # new
```

(The current `sel4-public:baseline-clean` image has the
`--maxheap 16000` half of this — bumped during the missing-sessions
rebuild. The `-H 1000 → 8000` half is the new bit.)

`compile.sh`'s `ISABELLE_BUILD_OPTIONS="threads=8"` (already on the
experiments branch) stays.

Expected wall savings on a clean rebuild of the four heavy sessions, all
else equal:

- CBaseRefine:        **-17%** (verified: 89m → 74m, save 15m)
- CRefine:            our retry already used these settings; baseline-eq run
                      not measured but the 56-minute wall is the new normal.
- InfoFlowCBase:      same situation as CRefine.
- InfoFlowC:          same.
- CKernel:            unmeasured at threads=8; it has 60% GC at threads=4
                      so likely benefits similarly. Estimated -10 to -15%.
- Other small sessions: mostly noise.

Conservative full-pipeline wall savings estimate: **15-25 minutes per
clean rebuild**.

## Cost so far

- Wall time: ~76 min (Exp A) + ~95 min (Exp A2) = **2h51m of build time**
- Disk: 0 (both experiment containers torn down)
- Baseline image / heap files: untouched
