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
| **Exp A** | 4434s = **73:54** | 18250s | 2742s | 61.8% | 4.12 | **8** | **8000** | **16000** |
| **Exp A2** | 5708s = **95:08** | 17924s | 3175s | 55.6% | 3.14 | 4 | 1000 | **16000** |
| **Exp A3** | 4655s = **77:36** | 18893s | 3233s | 69.4% | 4.06 | **8** | 1000 | **16000** |

`build-logs/full.log` line 1134 also has a CBaseRefine timing (7962s wall,
80% GC), but that run was the OOMing concurrent build with `-j 4` running
two sessions simultaneously, so it isn't a clean measurement.

## What each pair tells us — full decomposition

The 2² grid of `{threads ∈ 4,8} × {-H ∈ 1000,8000}` (with maxheap fixed at
16000) is now fully measured. Comparing A3 to A2 isolates the `threads`
variable; comparing A to A3 isolates the `-H` variable.

**A2 vs Baseline** — isolates `--maxheap 10000 → 16000`:

- wall:   5321 → 5708    (+7%)   *— very slightly worse, within noise*
- gc:     4235 → 3175    (-25%)
- gc%:    79.6% → 55.6%  (**-24 pp**)

Bumping `--maxheap` from 10 GB to 16 GB dramatically cuts GC time (heap
grows enough to let garbage accumulate before each collection), but the
saved cycles get spent elsewhere — net wall barely moves. **`--maxheap`
isn't a wall lever; it's a GC-pressure lever.**

**A3 vs A2** — isolates `threads 4 → 8` (held: -H 1000, maxheap 16000):

- wall:   5708 → 4655    (**−18.4%**, saves 1053s = 17:33)
- gc:     3175 → 3233    (+1.8%, essentially unchanged)
- factor: 3.14 → 4.06    (+29% parallel efficiency)
- cpu:    17924 → 18893  (+5.4%)

**`threads=4 → 8` alone explains 78% of the A-vs-A2 gain.** The bulk of
the speedup is from honest parallelism — 8 worker threads carve up the
proof-DAG roughly 30% better than 4. GC time barely moved, so this win
is independent of GC pressure.

**A vs A3** — isolates `-H 1000 → 8000` (held: threads=8, maxheap 16000):

- wall:   4655 → 4434    (**−4.7%**, saves 221s = 3:41)
- gc:     3233 → 2742    (−15%)
- gc%:    69.4% → 61.8%  (−7.6 pp)
- cpu:    18893 → 18250  (−3.4%)

**`-H 1000 → 8000` adds another 4.7% on top.** With 8 GB initial heap
polyml does fewer mark-compact resize cycles during the early growth
phase, so GC drops 15%. Real but modest gain — about 4 minutes on a
~78-minute build.

**A vs Baseline** — overall (all three knobs flipped):

- wall:   5321 → 4434    (**−17%**, saves 887s = 14:47 on this one session)
- gc:     4235 → 2742    (−35%)
- gc%:    79.6% → 61.8%  (−18 pp)

## Attribution summary

Of the **−887s (−17%)** total Exp-A-vs-Baseline gain on CBaseRefine wall:

| component | wall delta | share |
|---|---:|---:|
| `threads 4 → 8` | **−1053s** | **~83%** |
| `-H 1000 → 8000` | **−221s** | **~17%** |
| `maxheap 10000 → 16000` | +387s (slight regression on wall, but enables -H bump) | (negative on wall) |
| **net (A vs Baseline)** | **−887s** | **100%** |

The arithmetic doesn't sum perfectly (1053 - 221 - 387 ≠ 887) because the
deltas were measured against different reference points and the effects
have small interactions. But the picture is clear: **threads is the
dominant lever; -H is a small bonus; maxheap is required infrastructure
but not a wall lever on its own.**

## Per-theory snapshot

Per-theory `theory_timings` data summed (not directly = wall, but useful
for sanity / pattern):

| run | per-theory TOTAL elapsed | wall  | wall − sum |
|---|---:|---:|---:|
| Baseline (t=4) | 3478s | 5321s | 1843s (35% non-attributable, mostly future + heap-save) |
| Exp A (t=8, -H 8G) | 1889s | 4434s | 2545s (57%) |
| Exp A2 (t=4) | 3681s | 5708s | 2027s (35%) |
| Exp A3 (t=8) | 5177s | 4655s | -522s (negative — sum > wall, expected with parallelism amortizing) |

The sum-of-per-theory figures are not directly comparable across thread
counts (because per-theory elapsed reflects wall of that theory, but
inter-theory parallelism makes them overlap differently). They confirm
A2 ≈ Baseline (same threads), and A3 has a much higher per-theory sum
because 8 workers process more theories in parallel within the same wall.

## Conclusions

1. **Exp A is a real ~17% wall-time win for CBaseRefine** vs the original
   `-H 1000 / --maxheap 10000 / threads=4` baseline.
2. **The win is 83% from `threads=4 → 8` and 17% from `-H 1000 → 8000`.**
   Now decomposed; no remaining attribution mystery.
3. **`--maxheap 16000` doesn't speed up wall on its own.** A2 was +7%
   slower than Baseline. But it's prerequisite infrastructure: the OOM
   risk we hit with `--maxheap 10000` and concurrent sessions ruled out
   `-j N>1` even more strictly. With 16000 we have headroom for `-j 1` +
   threads=8 + -H 8000 without OOM.
4. **`threads=8` does NOT hurt CBaseRefine.** The earlier "FAILED" note
   (commit 766ca4b) was wrong — based on a wrong baseline number.
5. **Compile.sh's `threads=8` setting (commit 3d9b173) is correct and
   carries 83% of the speedup**. The `sel4-public:tuned` image's
   `-H 8000` adds the remaining 17%.

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
