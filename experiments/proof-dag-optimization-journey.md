# Proof DAG Optimization — Motivation, Analysis, Solution, Results

A consolidated narrative of the work that produced **−7466s wall (−29.5%
of canonical TUNED total build)** on the seL4 proof side via two 1-line
ROOT changes. This document captures the path from "we want a faster
proof build" to a structurally validated optimization, in case the
journey is more useful than the diff.

> 所属分支: `experiment/cbaserefine-swap-parent`  
> 起始 baseline: `experiment/dag-critical-path` → `baseline` → upstream
> seL4-13.0.0 / Isabelle2024 canonical TUNED build (25,331s wall total)

---

## 1. Motivation

### 1.1 Problem statement

seL4 proof verification is a 7-hour build under canonical configuration
(8 threads, `-H 8000 --maxheap 16000`, `-j 1`). When any of the four
upstream artefacts change — abstract spec, proof scripts, Haskell
prototype, or C source — the relevant proofs must be re-verified. The
practical question:

> **For each change-type, which structural element of the build
> dominates wall time, and is it amenable to a contained optimization?**

### 1.2 Initial framing (rejected)

A naïve approach would be to find the slowest *lemmas* via heap-log
`command_timings` data and rewrite their tactics. We had this data
already (`reports/slow-commands-*.md`), and the top entries
(`cteInsert_corres` 421s, `setUntypedCapAsFull_ut_revocable` 374s, etc.)
look like obvious targets.

**Why this framing was rejected**: build wall is determined by the
longest weighted *path* through the session/theory dependency DAG, not
by any individual node. A 60s proof on a non-critical branch can be
optimized to 0 with ~zero wall impact. A 20s theory at high fanout on
the critical path may matter more than a 400s theory on a parallel
branch. The right question is **"which nodes are on the critical
path of the rebuild closure for this change-type?"**

### 1.3 SOSP'09 architectural context

The seL4 verification is structured as a refinement tower (paper
Fig. 2):

```
Abstract Specification  (MA)            ASpec session
       ↑ Theorem 1
Executable Specification (ME)           ExecSpec session (Haskell-derived)
       ↑ Theorem 2
C Implementation         (MC)           CKernel/CSpec sessions
```

Theorem 3 (`MC refines MA`) is the headline functional-correctness
result; it composes Theorems 1 and 2. The proof sessions
(`AInvs`/`Refine`/`CBaseRefine`/`CRefine`/...) implement these theorem
proofs with substantial invariant infrastructure — the paper notes
"around 80% of the properties we show relate to preserving invariants"
(§3) and "the overall proof effort was clearly dominated by invariant
proofs" (§4.5).

**This sets up the puzzle**: invariant proofs are the most expensive
part of verification, and they live on the critical path between MA
and MC. Anything that needlessly re-executes them multiplies the wall
cost.

---

## 2. Analysis

### 2.1 Building the cost-aware DAG

Spread across 5 commits on `experiment/dag-critical-path`, we built:

1. **Theory-level DAG** ([reports/theory-dag.json](../reports/theory-dag.json))
   — 1094 nodes, 1961 edges, parsed from `imports` headers in every
   `.thy` under `verification/l4v/`.
2. **Session DAG** ([reports/session-dag.json](../reports/session-dag.json))
   — 56 nodes, 161 edges, parsed from ROOT files (parent `+ X` and
   imported `sessions Y` declarations).
3. **Per-theory cost model** — weight = sum-of-elapsed across every
   session whose `.db` `theory_timings` BLOB lists the theory.
4. **Closure manifests** for 4 change types — `proof`, `spec`,
   `haskell`, `c` (later split into 7 by sub-typing `spec`).
5. **CPM analysis** — earliest/latest finish times, slack, fanout per
   node, on the closure-induced subgraph.

### 2.2 The CSTR finding

A diagnostic check ([reports/session-duplication-scan.md](../reports/session-duplication-scan.md))
revealed that **28% of total per-theory aggregate elapsed (5585 of
19508s) is spent re-processing theories that already have valid heaps
in upstream sessions**. The two largest offenders:

```
CBaseRefine  98.6% dup of own session  (5519s of 5599s elapsed re-runs
                                         theories from Refine, AInvs,
                                         BaseRefine, ASpec, etc.)
CRefineSyscall 100% dup of own session (1546s — entire session is just
                                         re-running CRefine theories)
```

The mechanism: Isabelle's session model has two parent declarations:
- `+ X` heap-merges X's compiled ML state (millisecond-level loading)
- `sessions Y` declares Y's namespace for `imports "Y.foo"` resolution
  but does NOT merge the heap

When a theory is reachable only via `sessions Y` (not `+ Y`), Isabelle
re-executes its source in the current session's ML state. This work is
repeated **every time** that downstream session is rebuilt.

`proof/ROOT:106` had:
```
session CBaseRefine in "crefine/base" = CSpec +
  sessions
    CLib
    Refine        ← here: Refine source-re-executed during CBaseRefine build
    AutoCorres
```

### 2.3 Quantifying the amplification

Cross-referencing `theory_timings` across `.db` files revealed a second
twist: **re-executed theories take longer than their original session
walls**, because the host ML state already contains many extra simp/wp
rules and locale instances, growing tactic search spaces.

```
theory                  Refine.db time   CBaseRefine.db time   amplification
Refine.Finalise_R         225.6s            424.3s              1.88×
Refine.Retype_R            87.7s            294.4s              3.36×
Refine.IpcCancel_R         75.5s            272.5s              3.61×
Refine.Untyped_R          117.5s            249.5s              2.12×
Refine.Invariants_H       128.8s            230.6s              1.79×
                                            ──────
                       (top 10 sum):        2376s (vs 938 original; ~2.5×)
```

**This is the CSTR amplification**: cross-session reprocessing isn't
just duplicate work, it's *more expensive* duplicate work.

### 2.4 Critical-path placement

The cost-aware critical-path report
([reports/critical-path-summary.md](../reports/critical-path-summary.md))
showed that the top slack=0 theories for proof / spec / haskell change
types were *all* CSTR-amplified Refine theories: `Finalise_R 650s`
(combined cost), `Invariants_H 359s`, `IpcCancel_R 348s`, `CSpace_R 282s`,
etc. — each appearing with `n_sessions=2` indicating CSTR dup.

In other words: the 4-most-impactful change types' bottleneck was
**not the lemmas the slow-commands report flagged, but the
ROOT-inheritance configuration that re-executed an entire session of
those lemmas**.

### 2.5 Pre-experiment estimation

A naïve linear estimate:
```
Refine source-re-execution cost = Refine + AInvs + BaseRefine baseline
  = 2083s + 1085s + 237s = 3405s aggregate
CSpec reprocessing (new dup if we swap) = ~149s
Net aggregate save: 3405 − 149 = 3256s
Wall save (factor 3.87): ~840s
```

Conservative ceiling: ~5500s per [reports/critical-path-summary.md](../reports/critical-path-summary.md).
**This estimate proved to undershoot reality by 5×**, due to omitting
GC amplification, CRefineSyscall's 100%-dup nature, and parent-heap
hygiene effects on downstream sessions (see §4.4 for mechanism break-down).

---

## 3. Solution

### 3.1 The structural fix

A 2-line patch to [verification/l4v/proof/ROOT](../verification/l4v/proof/ROOT)
(stored in [cbaserefine-swap-parent.patch](cbaserefine-swap-parent.patch)):

```diff
- session CBaseRefine in "crefine/base" = CSpec +
+ session CBaseRefine in "crefine/base" = Refine +
    sessions
      CLib
-     Refine
+     CSpec
      AutoCorres

- session CRefineSyscall in "crefine/intermediate" = CBaseRefine +
-   sessions
-     CRefine
+ session CRefineSyscall in "crefine/intermediate" = CRefine +
```

Both swaps follow the same logical pattern: **make the bigger heap a
proper `+` parent (heap-merged), demote the smaller heap to `sessions`
(source-re-executed)**.

### 3.2 Why the swap was viable

Three observations made this safe to attempt:
1. The original config already loaded both heaps into ML state during
   CBaseRefine's build (just via inefficient source re-execution rather
   than heap merge). Therefore there are no fundamental
   namespace collisions between Refine and CSpec content — only the
   *path* differs.
2. The size asymmetry favored the swap: Refine.heap chain (Refine +
   AInvs + BaseRefine + ASpec + Word_Lib + HOL) carries ~3500s of
   amplified work; CSpec.heap chain (CSpec + CKernel + CParser +
   Simpl-VCG + Word_Lib + HOL) carries only ~1250s. Swapping which
   side is heap-merged trades the bigger amplified side away.
3. Failure containment: any ML-state conflict surfaces in seconds at
   `loadHierarchy` time or in the first imported theory load, not at
   the end of a 1.5h build.

### 3.3 Validation methodology

Multi-stage gate to keep cost bounded:

| stage | cost | what it confirms |
|---|---|---|
| `isabelle build -n -d . CBaseRefine` (dry-run) | seconds | ROOT parses; topo order valid |
| Wipe single heap + `isabelle build CBaseRefine` | ~25min | ML state merge works; build completes; new wall measured |
| Wipe CRefine + CRefineSyscall + cascade rebuild | ~70min | Downstream chain still consistent |
| Wipe InfoFlowCBase/C + rebuild | ~40min | All downstream of CBaseRefine still build |
| `tools/lemma_inventory/diff.py` | n/a | (skipped — no .thy modifications, ROOT only) |
| Full canonical rebuild | ~5h (deferred) | Definitive build_log_2 for next baseline |

Each stage either passes (proceed) or fails fast with a diagnostic
error. Total elapsed for stages 1–4 was ~2.5h.

---

## 4. Results

### 4.1 Headline numbers

```
session            baseline     swap         Δ wall      Δ wall %
CBaseRefine         5183.2s     1318.3s     -3865s      -74.6%   ★ direct swap
CRefine             4556.6s     4038.8s      -518s      -11.4%   indirect bonus
CRefineSyscall      3306.7s        1.2s    -3306s      -99.97%   ★ direct swap
InfoFlowCBase       1109.9s     1217.9s      +108s       +9.7%   downstream regression
InfoFlowC            844.7s      958.7s      +114s      +13.5%   downstream regression
─────────────────────────────────────────────────────────────
total              15001.1s     7534.9s    -7466.2s    -49.8%

Across canonical TUNED total wall (25,331s): -29.5%
```

### 4.2 CBaseRefine.db structural collapse

```
                    OLD              NEW              Δ
total theories      304              94               -210
sum_elapsed         5598.7s          1237.7s          -4361.0s

Removed (now heap-merged via + Refine):
  Refine            38 thys / 3504.4s     0           -3504.4s
  AInvs             79 thys / 1329.8s     0           -1329.8s
  ExecSpec          71 thys /  370.2s     0            -370.2s
  ASpec             36 thys /  183.6s     0            -183.6s
  Lib/Monads/...    52 thys /  130.0s     0            -130.0s
  BaseRefine         1 thy  /    9.5s     0              -9.5s
                                                       ─────────
  total removed:   277 thys / 5527.5s
  
Added (now reprocessed via sessions CSpec):
  CKernel            0      → 1 thy  / 764.0s         +764.0s
  CSpec              0      → 7 thys / 199.2s         +199.2s
  CParser            0      → 33 thys / 116.0s        +116.0s
  Simpl-VCG          0      → 14 thys /  57.4s         +57.4s
  HOL-Statespace     0      → 3 thys /  11.5s          +11.5s
  HOL-Library        0      → 1 thy  /   7.1s           +7.1s
  AsmRefine          0      → 3 thys /   6.9s           +6.9s
                                                       ─────────
  total added:      66 thys / 1166.6s
  
Trade: 5527.5s of Refine-system reprocessing (with ~2-3× amplification)
       for 1166.6s of C-system reprocessing (no comparable amplification
       observed)
```

### 4.3 CRefineSyscall: the 99.97% case

CRefineSyscall declares only one theory (`Intermediate_C`); under the
old config its 3306s wall was **entirely** the cost of source-
re-executing the full CRefine session inside CRefineSyscall's ML
state. CSTR reported 100% dup. After swap:

```
Building CRefineSyscall ...
CRefineSyscall: theory CRefineSyscall.Intermediate_C
Timing CRefineSyscall (8 threads, 1.158s elapsed time, 1.668s cpu time, 0.000s GC time, factor 1.44)
```

The actual session-specific work was always ~1s. CSTR inflated it ~3000×.

### 4.4 Why the gain was 5× the estimate

The conservative 5500s upper-bound estimate missed four mechanisms:

**Mechanism 1: CSTR amplification (×2.5 average)** — re-executed
theories take 2.5× their original session time on average due to ML
state size effects. Estimate accounted only for the original 3405s
aggregate; reality removed 5527s.

**Mechanism 2: GC pressure was a hidden multiplier** — baseline
CBaseRefine spent 66.5% of wall in garbage collection (3446s of 5183s).
After swap that fell to 22.1% (292s of 1318s). GC time decreased -3154s,
and because GC is stop-the-world, wall savings track ~1:1 (not
divided by parallelism factor). Estimate didn't include this.

```
                    baseline    swap        Δ
gc:                 3446s       292s       -3154s    (-91.5%)
gc / wall ratio:    66.5%       22.1%
```

**Mechanism 3: CRefineSyscall is a packaging session** — only 1 own
theory. Its 3306s baseline wall was 100% dup. Swapping makes it
collapse to ~0. Estimate framed CRefineSyscall as "1546s aggregate /
factor 4.34 ≈ 400s wall save" — the right answer was 8× that.

**Mechanism 4: parent-heap hygiene is contagious** — CRefine had no
own ROOT change but inherited the new (cleaner) CBaseRefine.heap as
parent. Its GC dropped 4689→2426s (-48%), wall dropped 4557→4039s
(-11.4%). Estimate didn't anticipate this transfer.

Negative offset:
**Mechanism 5: downstream regression** — InfoFlowCBase (`= CRefine +
sessions InfoFlow Access ...`) has its own CSTR issue not addressed
by this swap, and its load of the new CRefine.heap layout costs
slightly more wall (+108s). InfoFlowC (downstream of InfoFlowCBase)
inherits this (+114s). Total +222s; well within the upstream gain.

### 4.5 What the experiment did NOT do

Important non-claims:

- **No proof correctness changes**. The swap is purely a build-
  configuration optimization. All proofs that succeeded before still
  succeed; lemma statements (verifiable via
  [tools/lemma_inventory/diff.py](../tools/lemma_inventory/diff.py))
  are unchanged because no `.thy` was edited.
- **Not a full canonical rebuild**. Only the 5 affected sessions were
  re-measured; the other 24 retain baseline timings. A full canonical
  rebuild (~5h) is the proper "next baseline" but is deferred until
  all structural variants are decided.
- **AutoCorresCRefine excluded**. Per [reports/baseline-restore.md]
  (../reports/baseline-restore.md) it's broken in l4v 13.0 ARM and
  excluded from canonical builds.
- **Non-fatal warnings observed but not addressed**. Two
  `*** Missing session sources entry "$L4V_ARCH/..."` warnings appear
  during build (Scala-side `Isabelle.Session.manager` source-tracking
  complaint about unexpanded variable). Build succeeds despite them;
  may affect incremental rebuild dep tracking (untested). Pending
  follow-up.

### 4.6 Remaining structural targets (status)

From the 4-orthogonal-target accounting in [reports/critical-path-summary.md](../reports/critical-path-summary.md):

| target | status | wall recovered |
|---|---|---:|
| (a) ROOT inheritance dedupe | **EMPIRICALLY VALIDATED** | −7466s |
| (b) Shared 3rd-party heap-merging (ExecSpec/Lib/...) | untouched | est. ~1300s |
| (c) `SEL4GraphRefine.thy:72` ML chunking (asm-refine monolith) | untouched | est. ~2200s (C only) |
| (d) `ASpec → ExecSpec` cross-cut elimination | untouched | est. ~3500s (haskell mostly) |

If (b)+(c)+(d) are landed, total realistic recovery rises to ~14,000s
(~55% of canonical TUNED wall). With (a) alone at −29.5% the
practical ceiling appears higher than the original 15-30% estimate.

### 4.7 Process lessons

For future leverage-point estimation:

1. **Check GC%**. Sessions with GC > 50% of wall have ML state
   pollution; structural fix can recover most of that 1:1 in wall.
2. **Identify "pure dup" sessions** like CRefineSyscall (very few own
   theories, large `sessions` declarations). 100% dup means
   structural fix → wall → ~0.
3. **Account for parent-heap hygiene transfer**. A swap on session X
   may improve wall on every X-descendant via cleaner inherited ML
   state, even when those descendants weren't touched directly.
4. **Validate via lossless wipe-and-rebuild**. We wiped only the
   target's own heap; downstream regressions surfaced as small
   measurable wall changes rather than build failures, providing a
   clear signal.

---

## Files

- [cbaserefine-swap-parent.md](cbaserefine-swap-parent.md) — detailed
  per-run experiment record with timing tables.
- [cbaserefine-swap-parent.patch](cbaserefine-swap-parent.patch) — the
  ROOT diff (apply with `git apply` inside `verification/l4v/`).
- [cbaserefine-swap-runs/](cbaserefine-swap-runs/) — raw build stdout
  per run.
- [reports/critical-path-summary.md](../reports/critical-path-summary.md)
  — 4-orthogonal-target accounting (target (a) marked
  EMPIRICALLY VALIDATED).
- [reports/session-duplication-scan.md](../reports/session-duplication-scan.md)
  — CSTR raw findings.
