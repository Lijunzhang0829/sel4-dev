# Proof-side optimization effort — outcome record

> **2026-05-29 final disposition: ALL proof-side optimizations across both
> attempts (cstr-2graph and haskell-mega-merge) are rejected.** This document
> consolidates everything attempted, everything learned, and the data
> artifacts that have standalone value. The two branches and their derived
> files are being deleted; this file plus `reports/spec-strengthen/` are
> the only proof/spec-tree artifacts preserved. The branch this file lives
> on is renamed from `cstr-2graph` to `spec-strengthen` as a starting point
> for the actual proof-strengthening work that the spec-strengthen reports
> already document.

## 1. Mission as originally framed

Speed up the seL4 proof-build wall time by attacking three layers of the
verification stack:

- **Layer 1 (ROOT topology)** — swap `CBaseRefine`'s parent session from
  `CSpec` to `Refine` so that Refine theories are heap-merged instead of
  source-re-executed.
- **Layer 2 (theory placement)** — move 5 `.thy` files from
  `crefine/ARM/` to `refine/ARM/` based on their dependency-closure pillar.
- **Layer 3 (lemma-level tactic refinement)** — replace specific
  `by (force <hints>)` with `by (fastforce <hints>)`, etc., for individual
  proof bodies.

All three were validated under our test setup and looked impressive in
isolation. None of them are accepted upstream.

## 2. What was actually attempted

### 2.1 `cstr-2graph` branch — three layers

| layer | mechanism | how validated | headline number when promoted |
|---|---|---|---:|
| L1 | `CBaseRefine = Refine + sessions CSpec`; `CRefineSyscall = CRefine +` | full canonical rebuild on the branch | **−33.1%** total canonical TUNED wall (25,331s → 16,942s) |
| L2 | move 5 theories: `IsolatedThreadAction.thy`, `Fastpath_{Equiv,Defs}.thy`, `ArchMove_C.thy`, `Move_C.thy`; FQN-update 4 importers | rebuild under L1 | −48% wall on the 5-theory subset |
| L3 | `Schedule_R.thy:1364` and `StateRelation.thy:756` — `force` → `fastforce` with safe modifiers | `check-theory.sh` A/B | −13% to −17% file wall per patch |

All committed to `verification/l4v` branch `optimize-refine-build` as commit
`bb65974`. Outer repo's `cstr-2graph` branch contains the analysis tooling,
experiment records, and ~80 KB of supporting reports.

### 2.2 `haskell-mega-merge` branch — alternative ROOT consolidation

A separate attempt (started before cstr-2graph, run in parallel for a
while) tried to merge 11 Haskell-axis theories into a shared parent
session, eliminating cross-session reprocessing of design-spec content.

| step | result |
|---|---|
| dry-run validated 11-session conservative merge | OK |
| applied to `verification/l4v` and rebuilt | **wall-negative by +882s (+7.8%)** |
| rollback to pristine ROOTs | validated by independent CBaseRefine rebuild |

Failure mode: the merged shared parent forced more downstream sessions to
load + skip-proof a larger heap, costing more wall than was saved on the
deduplicated theories themselves. Same lesson as cstr-2graph L1 in
different clothing: structural changes that look optimal under one
measurement regime can be wall-negative under the actual build workflow.
198 files changed across this branch; all deleted with the rest.

## 3. Why each layer is rejected

### 3.1 L1 — ROOT swap

Two upstream emails from seL4 maintainer (`devel@sel4.systems`, 2026-05-27):

1. **Build-wall is not the optimization target upstream cares about.**
   "Please don't. This is not a particularly useful thing for us to optimise
   and we are very unlikely to accept pull requests for it." They run with
   `SKIP_DUPLICATED_PROOFS=on` and full proof-check is infrequent.
2. **Our measurement regime was wrong.** We measured under `-j 1`,
   `-H 8000`, `--maxheap 16000`, all of which the maintainer specifically
   flagged: "Constraining the heap size is not useful, it will lead to
   excessive garbage collection. `-H 8000` is also not a particularly
   useful setting." Under their actual workflow
   (`SKIP_DUPLICATED_PROOFS=on`, no `-H`, no `--maxheap`), the duplication
   L1 attacks does not appear at the magnitude we reported — the conditional
   `skip_proofs` block in `proof/ROOT:111` reduces source-re-execution to
   statement-only loading.
3. **`CRefineSyscall` is intentional, not a CSTR pathology.** "The
   interactive-only session CRefineSyscall that you discovered exists
   specifically so that people can work on CRefine on memory-constrained
   machines." Its ~100% theory-duplication is the design intent.
4. **Methodological flaw recognized later**: even if the L1 measurement
   were honest under our regime, it modeled wall as the SUM of session walls
   (`-j 1` assumption). Under `-j N`, total wall = critical path through the
   session DAG. A session-level optimization only shortens total wall if
   that session is on the critical path AND the optimization doesn't shift
   the bottleneck to an incompressible session (e.g., CRefine at 2h25m
   stays unmovable). The L1 saving on CBaseRefine — even maximal — likely
   shifts the bottleneck to CRefine and produces ~0 total wall save.

### 3.2 L2 — theory pillar re-alignment

Same cognitive-dissonance argument the maintainer used to reject the
parallel ASpec↔ExecSpec decoupling applies here: having `Fastpath_Equiv`
under `crefine/` is a deliberate locality signal even when its closure is
haskell-pillar-clean. Moving it for build-wall reasons removes that signal.

L2 also depends on L1; without the CBaseRefine swap, the moved theories
run twice (in Refine + via `sessions Refine` source-re-execution in
CRefine), costing +17s net (validated by empirical trial). So L2 cannot
stand alone.

### 3.3 L3 — lemma tactic refinement

Empirically saturated: 16 attempts, 39 trials, only 2 patches cleared the
5% single-lemma threshold. Most candidate tactic swaps regress (force ↔
fastforce search-explosion, elim! → elim non-reproducible). The two
passing patches are technically standalone-mergeable but do not constitute
an optimization story.

Additionally, the underlying measurement used the `command_timings` heap
log, which we discovered has an offset misalignment bug — per-command
elapsed times are attributed to the wrong source position with ~0.1s
quantization noise, making per-tactic measurements only directionally
trustworthy. Documented in
`reports/layer3-lemma/lemma-optimization-master-log-20260526.md` before
deletion.

## 4. What survives the rejection

Things that have value independent of the proof-optimization mission:

### 4.1 Maintainer feedback content

The two emails from Klein (devel@sel4.systems, 2026-05-27) contain
explicit guidance worth remembering:

- ASpec includes ExecSpec theories deliberately as cognitive marker;
  cognitive dissonance is a feature, not a bug.
- Build with `SKIP_DUPLICATED_PROOFS=on` (matches CI `skip_dups: true`).
- Stock Isabelle defaults for threads/parallelism; don't constrain heap.
- Three open research questions worth pursuing:
  1. Is 8 threads per session still the sweet spot on modern machines?
  2. Does `-j N × threads > cores` oversaturation help?
  3. Where can `A imports B, B imports C` be flattened to `A imports
     {B, C}` (the only direction the maintainer would consider PRs on)?
     Constraint: <100 line diff total.

### 4.2 The arch_split observation (structural fact)

312 files in the `verification/l4v/proof/` tree carry an
`(*FIXME: arch_split*)` comment. In the 4 largest proof sessions
(AInvs/Access/InfoFlow/InfoFlowC) the critical-path serialization is
**95-100% along arch_split zigzag chains** (generic↔arch interleaving
caused by `requalify_facts` / `named_theorems` discipline). This is
upstream's own multi-year structural debt; our `flatten_simulator.py`
quantified it but the fix is not within the maintainer's "<100 line
diff" constraint. The observation stands as evidence material if upstream
ever prioritizes the arch_split refactor.

Refine and CRefine, by contrast, have ~15% arch_split share in their top
candidates. Symbol-level analysis on 5 of those candidates classified
them into 3 regimes:

- **0% pass-through** (e.g., `TcbAcc_R imports CSpace_R`): refactor-able
  with ~2 line diff, but real wall save is structurally bounded to ~9%
  of the simulator's predicted ΔCP because the dependency just shifts
  to a sibling.
- **1-5% sparse** (e.g., `Ipc_R imports Finalise_R`): refactor-able by
  extracting 4-5 lemmas to a shared parent.
- **25%+ heavy**: structural dependency, not flatten-able.

### 4.3 Golden baseline data (build infrastructure)

A full ARM proof build under upstream-aligned config
(`SKIP_DUPLICATED_PROOFS=1`, no `--maxheap`, document=false,
`L4V_ARCH=ARM`) on consumer hardware (16-core Intel i5-14500, 23 GB RAM,
Docker container). **29 sessions built across 5 rounds; 36,567 lemmas
inventoried; 0 outstanding sorrys in kernel proof code.**

Top 10 sessions by wall on this hardware:

| session | wall | factor |
|---|---:|---:|
| CRefine | 2h 25m 38s | 2.00 |
| CRefineSyscall | 1h 54m 17s | 2.04 |
| SimplExportAndRefine | 1h 50m 10s | 1.98 |
| Refine | 1h 21m 27s | 1.83 |
| AutoCorresSEL4 | 1h 04m 16s | 1.20 |
| AInvs | 42m 34s | 1.79 |
| CBaseRefine | 35m 33s | 1.63 |
| InfoFlowC | 34m 01s | 1.54 |
| InfoFlow | 32m 47s | 1.74 |
| CKernel | 32m 44s | 1.82 |

`l4v` HEAD: `00d9073f70d064b31b4b1fad8a0e7086bb258007`.

A 2026 survey of 10 academic seL4 papers and the official CI configuration
shows **0/10 papers report hardware specs for seL4 build measurements**,
and the seL4 CI's AWS instance type is private. This baseline is the only
known publicly-documented complete ARM-proof wall-clock measurement on
consumer-grade hardware.

Empirical note: this 23 GB / 16-core machine cannot run `isabelle build
-j 2` for the AInvs/CRefine chain — each has a ~10-14 GB working set
under `SKIP_DUPLICATED_PROOFS=on` and the pair OOMs. The upstream CI
runs `-j 2` cleanly, which implies their runner has ≥32 GB RAM (probably
`c5.4xlarge` class). The l4v README's own recommendation ("~16 GB RAM,
8 cores useful") is for the C-refinement-only case under sequential build.

### 4.4 Methodology lessons (the bitter ones)

1. **Wall ≠ sum of session walls under any `-j > 1`.** Under `-j N`,
   total wall = critical path through the session DAG. A session-level
   optimization that doesn't shorten the critical path is wall-neutral
   regardless of how much per-session wall it saves.
2. **Every wall measurement is regime-dependent.** A number reported
   without explicit `(j, skip_duplicated_proofs, ml_options)` context
   is meaningless. The CSTR-2 −33% number existed only under the
   intersection of `j=1`, `skip_dups=off`, and `--maxheap 16000`.
3. **Upstream's design intent is not waste to be optimized away.** The
   `ASpec includes ExecSpec theories` pattern, the `CRefineSyscall`
   separate session, the `crefine/` placement of haskell-axis helpers,
   the arch_split `requalify_facts` discipline — all of these look
   like duplication or misplacement to a build-wall optimizer, and all
   of them are deliberate design choices serving readability,
   memory-constrained development, or proof maintenance. "Optimizing"
   them by removing the cognitive markers loses information upstream
   considers load-bearing.
4. **`flatten_simulator.py`'s ΔCP is an upper bound that's typically
   ~10× larger than recoverable wall.** The simulator assumes unbounded
   parallelism and that edges can be erased without consequence. Real
   refactors must preserve transitive symbol dependencies, which usually
   means the dropped edge's wait time just shifts to a sibling. Calibrated
   on `TcbAcc_R imports CSpace_R`: simulator says 475s ΔCP, structural
   bound is ~44s, ratio ~9%.

## 5. What is being deleted

- Outer repo: this entire `cstr-2graph` branch, including all the
  analysis tooling under `tools/critical_path/`, `tools/lemma_inventory/`,
  `tools/golden_baseline/`, all reports under `reports/`, all
  experiments under `experiments/`, the `BRANCH-SUMMARY.md`, and the
  build artifacts. Plus sister branches `cstr`, `cstr-1graph`,
  `baseline`, `haskell-mega-merge`.
- `verification/l4v` submodule: branch `optimize-refine-build` containing
  the `bb65974` commit (L1+L2+L3 applied). The submodule HEAD is
  returned to `00d9073f` (= `origin/master`).
- Any cached heaps under `heaps/db-archive/`, `heaps/db-live/`,
  `heaps/db-archive-pre-swap/`, `heaps/db-sequential/` — all of these
  were generated by the cstr-2graph build runs and have no value beyond
  this branch.

## 6. What is NOT changed

- `verification/l4v` submodule itself stays on commit `00d9073f`; the
  pristine upstream sources are untouched.
- `.claude/skills/` directory: the four `isabelle_prover_*` skills are
  retained as future tools (they target proof / spec / haskell / c
  optimization respectively); each could be used in a different,
  better-scoped effort.
- **`reports/spec-strengthen/`** is preserved (3 files, 40 KB) — these
  are real applied-and-verified spec strengthening patches and ranked
  candidates from the `isabelle_prover_spec` skill, *not* part of the
  rejected proof-side optimization story. The branch this file lives on
  is renamed to `spec-strengthen` to make that the active continuation
  point.
  - Caveat: the actual `.thy` modifications in `CSpace_AI.thy` and
    `Finalise_AI.thy` were `git restore`'d during the golden-baseline
    rebuild for measurement cleanliness. The reports preserve the patch
    contents and can be re-applied verbatim when work resumes.
- The two emails from Klein referenced in §3.1 are not under our
  control; they remain in upstream-mailing-list archives.

## 7. If anyone tries this again

Read this document first. The key recognition is in §4.4 lesson 2:
**every wall number needs a regime tag**. Before claiming any
optimization, measure under the same `(j, skip_duplicated_proofs,
ml_options)` upstream uses (or document explicitly why a different
regime is being used). The closest reference for "upstream's actual
workflow" is `verification/l4v/.github/workflows/proof.yml` plus the
maintainer email content quoted in this file.
