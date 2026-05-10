# Session-level theory duplication scan (CSTR)

_Empirical scan of `heaps/db-archive/*.db` `theory_timings` BLOBs to quantify cross-session re-execution of the same .thy source. See [`tools/critical_path/session_dedupe_scan.py`](../tools/critical_path/session_dedupe_scan.py) for the scanner._

## Headline

- Sessions scanned: **29**
- Unique theory names: **846**
- Theories duplicated across ≥2 sessions: **245** (29.0%)
- Σ per-theory elapsed across all sessions: **12670.0s**
- **Duplication overhead estimate**: **1767.9s** (14.0%)

## Problem statement

**What this scan diagnoses.** Several l4v sessions reprocess theories that have already been proven and cached in upstream sessions' heap files. The theory_timings BLOB inside each session's .db records every theory the session executed during its build; cross-referencing these BLOBs reveals that some theories appear in multiple BLOBs with different (often higher) elapsed times — i.e., the same .thy source was typechecked + proved more than once across the project, in different ML contexts.

**How we detect it.** For every theory T present in BLOB(S) for ≥2 sessions S, we treat the cross-session duplication overhead as Σ elapsed_in_each_session − max_elapsed_in_any_one_session. This is an upper-bound estimate of the wall time recoverable if T were processed exactly once across the project.

**Field semantics**:
- `own_total_elapsed` — Sum of per-theory elapsed in this session's own BLOB. Equals what you'd see in heaps/build_log.txt's per-theory block for this session.
- `duplicated_elapsed_in_self` — How much of own_total_elapsed is on theories that ALSO appear in some other session's BLOB.
- `co_appearing_sessions` — {other_session: elapsed_in_self_on_overlapping_theories}. Tells you which sessions this one is duplicating work with.
- `n_sessions (per theory)` — How many distinct sessions' BLOBs list this theory. >1 implies cross-session reprocessing.
- `duplication_overhead (per theory)` — Σ elapsed across sessions − max elapsed in any one session. Conservative wall-recovery upper bound.

## Mechanism

**Isabelle session model.** An Isabelle session declares its parent via `+ X` (heap merge: X.heap is deserialized into the new session's ML state at start) and may declare additional sessions via `sessions Y, Z, ...` (namespace declaration only — makes Y.foo and Z.bar resolvable from `imports` clauses, but does NOT merge their heaps).

**`+ X` (heap merge).** `= X +` causes Isabelle to load X.heap directly. Theories in X are present as compiled images in the running ML state; they are NOT re-executed and contribute milliseconds-level loading cost only.

**`sessions X` (namespace only).** `sessions X` only declares X for name resolution. When a theory in the current session writes `imports "X.foo"`, Isabelle locates X.foo's source file via X's session dir, then EXECUTES it in the current session's ML state (NOT loaded from X.heap). This is the source of the cross-session duplication observed in CSTR.

**Why elapsed differs across sessions.** When the same .thy is executed in two different sessions, the ambient ML state differs: each session has its own active simp rules, locale interpretations, type class instances, etc., inherited from its `+` parent. Tactic search spaces grow with rule set size, so the same proof script can take significantly longer in a more populated ML state. Example: Refine.Finalise_R takes 226s in Refine session (BaseRefine.heap-derived state) vs 424s in CBaseRefine session (CSpec.heap-derived state) — +88% wall, +24% cpu, +77% gc.

> **Important.** Within ONE `isabelle build CBaseRefine` invocation, each .thy file is processed exactly ONCE and its result cached in ML memory for subsequent imports. The 'duplication' is across DIFFERENT session-build invocations: `isabelle build Refine` processes Finalise_R once, then `isabelle build CBaseRefine` processes it ONE MORE time independently, totaling 2× across the project (NOT 45× within CBaseRefine's build).

## Patterns

### Pattern A_high_dup_structural_fix_candidates

**Definition.** Sessions whose ≥80% of own_total_elapsed lies on theories duplicated elsewhere. Caused by `sessions Y` ROOT declarations whose Y has a meta-theory (e.g., Refine.Refine) that gets pulled in via a single `imports` line and re-executes Y wholesale in the current session's ML state.

**Fix strategy.** (1) Refactor ROOT to make Y a `+` parent if Isabelle's single-parent constraint allows. (2) If Y and the current `+` parent have ML state conflicts, introduce a JointBase session merging both via cascaded `+` so the cost is paid once. (3) Reduce what Y's meta-theory re-exports so the current session only pulls in the subset it actually needs.

### Pattern B_clean_independent_work

**Definition.** Sessions with 0% duplication — their theories do not appear in any other session's BLOB. These represent genuinely independent work and serve as templates for what 'clean' session boundaries look like.

**Fix strategy.** No fix needed. Notable that several large sessions (SimplExportAndRefine 2264s, HOL 379s) achieve 0% — demonstrating clean boundaries are achievable in l4v.

### Pattern C_spec_broadcast

**Definition.** Sessions like ASpec where every theory appears in many downstream sessions but each individual occurrence is tiny (usually < 5s). Wall accumulates via fan-out, not per-occurrence cost.

**Fix strategy.** Lower priority than Pattern A. Fix only if a specific downstream session imports more of the spec than it uses.

## Per-session footprint

Sessions ranked by `duplicated_elapsed_in_self`. The `inheritance_trigger_breakdown` column attributes duplications to the specific `sessions Y` ROOT declaration that pulled Y in.

| session | pattern | #thys | own Σelapsed | dup #thys | dup Σelapsed | dup % | trigger via `sessions` | parent (`+`) |
|---|---|---:|---:|---:|---:|---:|---|---|
| `CBaseRefine` | A | 94 | 1054.9 | 64 | 991.5 | 94.0% | `CLib`→10s; `CSpec`→186s; `AutoCorres`→35s | `Refine` |
| `CKernel` | A | 61 | 700.3 | 61 | 700.3 | 100.0% | `ExecSpec`→11s; `CLib`→0s; `AsmRefine`→5s | `CParser` |
| `InfoFlowCBase` | A | 70 | 643.5 | 69 | 639.4 | 99.4% | `Access`→274s; `InfoFlow`→360s | `CRefine` |
| `InfoFlow` | A | 46 | 346.9 | 42 | 328.9 | 94.8% | — | `Access` |
| `Access` | A | 28 | 300.9 | 27 | 269.3 | 89.5% | — | `AInvs` |
| `DSpec` | A | 83 | 159.2 | 83 | 159.2 | 100.0% | `ExecSpec`→16s; `ASpec`→3s | `Word_Lib` |
| `CSpec` | A | 8 | 125.4 | 8 | 125.4 | 100.0% | — | `CKernel` |
| `ASpec` | C | 107 | 239.0 | 64 | 87.4 | 36.6% | `Lib`→22s; `ExecSpec`→36s | `Word_Lib` |
| `DBaseRefine` | A | 19 | 79.9 | 18 | 76.6 | 95.9% | `DSpec`→76s | `AInvs` |
| `CParser` | A | 35 | 66.9 | 35 | 66.9 | 100.0% | `ML_Utils`→1s; `Basics`→1s | `Simpl-VCG` |
| `SepDSpec` | C | 56 | 97.3 | 29 | 65.9 | 67.7% | `Sep_Algebra`→18s; `SepTactics`→1s | `DSpec` |
| `AInvs` | C | 119 | 946.7 | 29 | 65.7 | 6.9% | — | `ASpec` |
| `DPolicy` | B | 8 | 82.8 | 7 | 64.7 | 78.1% | `Access`→65s | `DRefine` |
| `Simpl-VCG` | A | 18 | 45.7 | 18 | 45.7 | 100.0% | — | `Word_Lib` |
| `Refine` | B | 48 | 1591.4 | 1 | 0.4 | 0.0% | `Lib`→31s; `CorresK`→4s | `BaseRefine` |
| `SimplExport` | B | 7 | 557.4 | 1 | 0.3 | 0.1% | — | `CSpec` |
| `BaseRefine` | B | 63 | 237.7 | 0 | 0.0 | 0.0% | `Lib`→0s | `AInvs` |
| `Bisim` | B | 6 | 24.6 | 0 | 0.0 | 0.0% | `ASepSpec`→4s | `AInvs` |
| `CRefine` | B | 51 | 2103.9 | 0 | 0.0 | 0.0% | — | `CBaseRefine` |
| `CRefineSyscall` | B | 1 | 0.9 | 0 | 0.0 | 0.0% | — | `CRefine` |
| `DRefine` | B | 18 | 146.9 | 0 | 0.0 | 0.0% | — | `DBaseRefine` |
| `DSpecProofs` | B | 12 | 24.5 | 0 | 0.0 | 0.0% | — | `SepDSpec` |
| `HOL` | B | 114 | 428.5 | 0 | 0.0 | 0.0% | — | — |
| `InfoFlowC` | B | 6 | 200.1 | 0 | 0.0 | 0.0% | — | `InfoFlowCBase` |
| `Pure` | B | 3 | 1.1 | 0 | 0.0 | 0.0% | — | — |
| `RefineOrphanage` | B | 1 | 38.6 | 0 | 0.0 | 0.0% | — | `Refine` |
| `SimplExportAndRefine` | B | 8 | 2342.2 | 0 | 0.0 | 0.0% | — | `SimplExport` |
| `UmmTypes` | B | 1 | 3.0 | 0 | 0.0 | 0.0% | — | — |
| `Word_Lib` | B | 66 | 79.9 | 0 | 0.0 | 0.0% | — | `HOL` |

## Case studies (top 5 by dup elapsed)

### CBaseRefine_re-executes_CKernel

- **Perpetrator (does the re-execution)**: `CBaseRefine`
- **Victim (its theories get re-executed)**: `CKernel`
- **Elapsed in perpetrator on overlapping theories**: 683.0s
- **Pattern**: `A_high_dup_structural_fix_candidate`

**ROOT declaration**:

```
session CBaseRefine in "base" = Refine +
  sessions
    CLib
    CSpec
    AutoCorres
```

**Spotlight theory `CKernel.Kernel_C`** — same .thy source, two ML contexts:

| in session | elapsed (s) | cpu (s) | gc (s) |
|---|---:|---:|---:|
| `CBaseRefine` | 675.8 | 2233.6 | 101.7 |
| `CKernel` | 613.6 | 1885.3 | 53.1 |

_Same .thy source; ran in two different ML contexts. In CBaseRefine (this session) it took 675.8s; in CKernel (originating session) it took 613.6s. Difference reflects the different sets of active simp rules / locale interpretations / type class instances inherited from each session's `+` parent heap._

### InfoFlowCBase_re-executes_InfoFlow

- **Perpetrator (does the re-execution)**: `InfoFlowCBase`
- **Victim (its theories get re-executed)**: `InfoFlow`
- **Elapsed in perpetrator on overlapping theories**: 365.3s
- **Pattern**: `A_high_dup_structural_fix_candidate`

**ROOT declaration**:

```
session InfoFlowCBase in "base" = CRefine +
  sessions
    Access
    InfoFlow
```

**Concrete trigger imports** (source lines that pull in the duplicated session):

- `verification/l4v/proof/infoflow/refine/base/Include_IF_C.thy` → `imports "InfoFlow.ArchNoninterference"`
- `verification/l4v/proof/infoflow/refine/base/Include_IF_C.thy` → `imports "InfoFlow.Noninterference_Base_Refinement"`
- `verification/l4v/proof/infoflow/refine/base/Include_IF_C.thy` → `imports "InfoFlow.Example_Valid_State"`

**Spotlight theory `InfoFlow.ArchArch_IF`** — same .thy source, two ML contexts:

| in session | elapsed (s) | cpu (s) | gc (s) |
|---|---:|---:|---:|
| `InfoFlowCBase` | 27.9 | 154.6 | 4.4 |
| `InfoFlow` | 27.7 | 141.6 | 3.8 |

_Same .thy source; ran in two different ML contexts. In InfoFlowCBase (this session) it took 27.9s; in InfoFlow (originating session) it took 27.7s. Difference reflects the different sets of active simp rules / locale interpretations / type class instances inherited from each session's `+` parent heap._

### InfoFlowCBase_re-executes_Access

- **Perpetrator (does the re-execution)**: `InfoFlowCBase`
- **Victim (its theories get re-executed)**: `Access`
- **Elapsed in perpetrator on overlapping theories**: 274.1s
- **Pattern**: `A_high_dup_structural_fix_candidate`

**ROOT declaration**:

```
session InfoFlowCBase in "base" = CRefine +
  sessions
    Access
    InfoFlow
```

**Spotlight theory `Access.ArchRetype_AC`** — same .thy source, two ML contexts:

| in session | elapsed (s) | cpu (s) | gc (s) |
|---|---:|---:|---:|
| `InfoFlowCBase` | 61.6 | 282.4 | 2.1 |
| `Access` | 59.8 | 255.3 | 3.5 |

_Same .thy source; ran in two different ML contexts. In InfoFlowCBase (this session) it took 61.6s; in Access (originating session) it took 59.8s. Difference reflects the different sets of active simp rules / locale interpretations / type class instances inherited from each session's `+` parent heap._

### DSpec_re-executes_ASpec

- **Perpetrator (does the re-execution)**: `DSpec`
- **Victim (its theories get re-executed)**: `ASpec`
- **Elapsed in perpetrator on overlapping theories**: 87.3s
- **Pattern**: `A_high_dup_structural_fix_candidate`

**ROOT declaration**:

```
session DSpec in "capDL" = Word_Lib +
  sessions
    ExecSpec
    ASpec
    HOL
    Combinatorics
```

**Concrete trigger imports** (source lines that pull in the duplicated session):

- `verification/l4v/spec/capDL/Intents_D.thy` → `imports "ASpec.CapRights_A"`
- `verification/l4v/spec/capDL/Types_D.thy` → `imports "ASpec.VMRights_A"`

**Spotlight theory `ExecSpec.MachineTypes`** — same .thy source, two ML contexts:

| in session | elapsed (s) | cpu (s) | gc (s) |
|---|---:|---:|---:|
| `CKernel` | 8.9 | 25.2 | 1.1 |
| `ASpec` | 8.7 | 26.6 | 1.0 |
| `DSpec` | 8.4 | 28.9 | 0.6 |

_Same .thy source; ran in two different ML contexts. In CKernel (this session) it took 8.9s; in ASpec (originating session) it took 8.7s. Difference reflects the different sets of active simp rules / locale interpretations / type class instances inherited from each session's `+` parent heap._

### CBaseRefine_re-executes_CSpec

- **Perpetrator (does the re-execution)**: `CBaseRefine`
- **Victim (its theories get re-executed)**: `CSpec`
- **Elapsed in perpetrator on overlapping theories**: 187.4s
- **Pattern**: `A_high_dup_structural_fix_candidate`

**ROOT declaration**:

```
session CBaseRefine in "base" = Refine +
  sessions
    CLib
    CSpec
    AutoCorres
```

**Concrete trigger imports** (source lines that pull in the duplicated session):

- `verification/l4v/proof/crefine/base/Include_C.thy` → `imports "CSpec.KernelInc_C"`

**Spotlight theory `CSpec.Substitute`** — same .thy source, two ML contexts:

| in session | elapsed (s) | cpu (s) | gc (s) |
|---|---:|---:|---:|
| `CBaseRefine` | 82.6 | 513.4 | 51.0 |
| `CSpec` | 64.9 | 81.6 | 0.6 |

_Same .thy source; ran in two different ML contexts. In CBaseRefine (this session) it took 82.6s; in CSpec (originating session) it took 64.9s. Difference reflects the different sets of active simp rules / locale interpretations / type class instances inherited from each session's `+` parent heap._

### DBaseRefine_re-executes_DSpec

- **Perpetrator (does the re-execution)**: `DBaseRefine`
- **Victim (its theories get re-executed)**: `DSpec`
- **Elapsed in perpetrator on overlapping theories**: 76.6s
- **Pattern**: `A_high_dup_structural_fix_candidate`

**ROOT declaration**:

```
session DBaseRefine in "base" = AInvs +
  sessions
    DSpec
```

**Concrete trigger imports** (source lines that pull in the duplicated session):

- `verification/l4v/proof/drefine/base/Include_D.thy` → `imports "DSpec.Syscall_D"`

**Spotlight theory `DSpec.Intents_D`** — same .thy source, two ML contexts:

| in session | elapsed (s) | cpu (s) | gc (s) |
|---|---:|---:|---:|
| `DBaseRefine` | 31.7 | 48.9 | 0.5 |
| `DSpec` | 25.7 | 97.5 | 3.2 |

_Same .thy source; ran in two different ML contexts. In DBaseRefine (this session) it took 31.7s; in DSpec (originating session) it took 25.7s. Difference reflects the different sets of active simp rules / locale interpretations / type class instances inherited from each session's `+` parent heap._

### CBaseRefine_re-executes_CParser

- **Perpetrator (does the re-execution)**: `CBaseRefine`
- **Victim (its theories get re-executed)**: `CParser`
- **Elapsed in perpetrator on overlapping theories**: 73.2s
- **Pattern**: `A_high_dup_structural_fix_candidate`

**ROOT declaration**:

```
session CBaseRefine in "base" = Refine +
  sessions
    CLib
    CSpec
    AutoCorres
```

**Spotlight theory `CParser.CTypesDefs`** — same .thy source, two ML contexts:

| in session | elapsed (s) | cpu (s) | gc (s) |
|---|---:|---:|---:|
| `CParser` | 16.8 | 24.6 | 0.8 |
| `CBaseRefine` | 12.1 | 50.7 | 2.8 |

_Same .thy source; ran in two different ML contexts. In CParser (this session) it took 16.8s; in CBaseRefine (originating session) it took 12.1s. Difference reflects the different sets of active simp rules / locale interpretations / type class instances inherited from each session's `+` parent heap._

### DPolicy_re-executes_Access

- **Perpetrator (does the re-execution)**: `DPolicy`
- **Victim (its theories get re-executed)**: `Access`
- **Elapsed in perpetrator on overlapping theories**: 64.7s
- **Pattern**: `B_partial_independent`

**ROOT declaration**:

```
session DPolicy in "dpolicy" = DRefine +
  sessions
    Access
```

**Concrete trigger imports** (source lines that pull in the duplicated session):

- `verification/l4v/proof/dpolicy/Dpolicy.thy` → `imports "Access.ArchAccess_AC"`

**Spotlight theory `Access.ArchAccess`** — same .thy source, two ML contexts:

| in session | elapsed (s) | cpu (s) | gc (s) |
|---|---:|---:|---:|
| `InfoFlowCBase` | 21.9 | 46.5 | 2.3 |
| `Access` | 21.7 | 35.9 | 1.8 |
| `DPolicy` | 21.5 | 36.1 | 1.8 |

_Same .thy source; ran in two different ML contexts. In InfoFlowCBase (this session) it took 21.9s; in Access (originating session) it took 21.7s. Difference reflects the different sets of active simp rules / locale interpretations / type class instances inherited from each session's `+` parent heap._

## Shared third-party dependency overhead

These are theories owned by sessions NOT in the canonical 28-session build set (libraries like `Lib`, `Monads`, `Eisbach_Tools`, etc.) that appear in MULTIPLE canonical sessions' BLOBs. Each canonical session re-executes them independently because they're declared via `sessions Y` rather than heap-merged via `+ Y`. This is a different structural pattern from perpetrator/victim — there's no single 'duplicator' to fault. Remediation requires either making the third-party session a `+` parent of all consumers, or introducing a shared parent that already merges it.

| 3rd-party session | #thys | in #canonical sessions | Σelapsed (all appearances) | avg/appearance |
|---|---:|---:|---:|---:|
| `Lib` | 42 | 9 | 159.5 | 1.50s |
| `Monads` | 30 | 5 | 150.7 | 1.91s |
| `ExecSpec` | 8 | 3 | 41.9 | 2.09s |
| `Eisbach_Tools` | 9 | 3 | 34.5 | 1.28s |
| `HOL-Statespace` | 3 | 2 | 14.5 | 2.42s |
| `HOL-Library` | 3 | 5 | 12.7 | 1.58s |
| `AsmRefine` | 3 | 2 | 10.1 | 1.68s |
| `CLib` | 2 | 3 | 5.6 | 1.40s |
| `ML_Utils` | 2 | 4 | 2.5 | 0.42s |
| `HOL-Combinatorics` | 1 | 2 | 1.7 | 0.87s |
| `Basics` | 1 | 3 | 1.3 | 0.45s |

## Top 30 duplicated theories by total cross-session cost

| theory | #sess | Σelapsed (all) | max single | max session | overhead | Δmax/2nd % | per-session breakdown |
|---|---:|---:|---:|---|---:|---:|---|
| `CKernel.Kernel_C` | 2 | 1289.4 | 675.8 | `CBaseRefine` | 613.6 | 10.1% | `CBaseRefine`:676s; `CKernel`:614s |
| `CSpec.Substitute` | 2 | 147.4 | 82.6 | `CBaseRefine` | 64.9 | 27.3% | `CBaseRefine`:83s; `CSpec`:65s |
| `Access.ArchRetype_AC` | 2 | 121.5 | 61.6 | `InfoFlowCBase` | 59.8 | 3.0% | `InfoFlowCBase`:62s; `Access`:60s |
| `CSpec.structures_defs` | 2 | 87.6 | 54.7 | `CBaseRefine` | 32.9 | 66.4% | `CBaseRefine`:55s; `CSpec`:33s |
| `Access.ArchAccess` | 3 | 65.1 | 21.9 | `InfoFlowCBase` | 43.2 | 1.0% | `InfoFlowCBase`:22s; `Access`:22s; `DPolicy`:22s |
| `Access.ArchCNode_AC` | 2 | 62.6 | 32.0 | `InfoFlowCBase` | 30.6 | 4.3% | `InfoFlowCBase`:32s; `Access`:31s |
| `DSpec.Intents_D` | 2 | 57.4 | 31.7 | `DBaseRefine` | 25.7 | 23.2% | `DBaseRefine`:32s; `DSpec`:26s |
| `InfoFlow.ArchArch_IF` | 2 | 55.6 | 27.9 | `InfoFlowCBase` | 27.7 | 0.8% | `InfoFlowCBase`:28s; `InfoFlow`:28s |
| `InfoFlow.ArchUserOp_IF` | 2 | 54.8 | 29.9 | `InfoFlowCBase` | 24.9 | 20.1% | `InfoFlowCBase`:30s; `InfoFlow`:25s |
| `Access.ArchAccess_AC` | 3 | 46.5 | 15.8 | `InfoFlowCBase` | 30.6 | 2.4% | `InfoFlowCBase`:16s; `DPolicy`:15s; `Access`:15s |
| `InfoFlow.ArchRetype_IF` | 2 | 45.1 | 30.9 | `InfoFlowCBase` | 14.2 | 117.2% | `InfoFlowCBase`:31s; `InfoFlow`:14s |
| `InfoFlow.Example_Valid_State` | 2 | 40.4 | 21.0 | `InfoFlowCBase` | 19.4 | 8.4% | `InfoFlowCBase`:21s; `InfoFlow`:19s |
| `CSpec.structures_proofs` | 2 | 39.4 | 22.3 | `CBaseRefine` | 17.1 | 30.7% | `CBaseRefine`:22s; `CSpec`:17s |
| `InfoFlow.ArchScheduler_IF` | 2 | 37.9 | 20.7 | `InfoFlowCBase` | 17.2 | 20.5% | `InfoFlowCBase`:21s; `InfoFlow`:17s |
| `InfoFlow.ArchCNode_IF` | 2 | 36.4 | 19.2 | `InfoFlowCBase` | 17.2 | 12.0% | `InfoFlowCBase`:19s; `InfoFlow`:17s |
| `DSpec.Invocations_D` | 2 | 36.1 | 18.1 | `DSpec` | 18.0 | 1.0% | `DSpec`:18s; `DBaseRefine`:18s |
| `Access.ArchArch_AC` | 2 | 34.9 | 17.8 | `InfoFlowCBase` | 17.0 | 4.6% | `InfoFlowCBase`:18s; `Access`:17s |
| `InfoFlow.ArchIRQMasks_IF` | 2 | 34.5 | 19.3 | `InfoFlowCBase` | 15.2 | 27.0% | `InfoFlowCBase`:19s; `InfoFlow`:15s |
| `InfoFlow.Noninterference` | 2 | 31.6 | 16.6 | `InfoFlowCBase` | 15.0 | 10.5% | `InfoFlowCBase`:17s; `InfoFlow`:15s |
| `InfoFlow.ArchInfoFlow` | 2 | 31.0 | 18.1 | `InfoFlow` | 13.0 | 39.0% | `InfoFlow`:18s; `InfoFlowCBase`:13s |
| `Access.ArchIpc_AC` | 2 | 30.1 | 15.5 | `InfoFlowCBase` | 14.6 | 6.0% | `InfoFlowCBase`:16s; `Access`:15s |
| `Access.ArchInterrupt_AC` | 2 | 30.1 | 15.3 | `InfoFlowCBase` | 14.8 | 3.7% | `InfoFlowCBase`:15s; `Access`:15s |
| `Access.Types` | 3 | 29.2 | 10.9 | `InfoFlowCBase` | 18.2 | 19.4% | `InfoFlowCBase`:11s; `Access`:9s; `DPolicy`:9s |
| `CParser.CTypesDefs` | 2 | 28.9 | 16.8 | `CParser` | 12.1 | 39.5% | `CParser`:17s; `CBaseRefine`:12s |
| `Simpl-VCG.Language` | 2 | 28.6 | 15.8 | `Simpl-VCG` | 12.9 | 22.5% | `Simpl-VCG`:16s; `CBaseRefine`:13s |
| `InfoFlow.ArchNoninterference` | 2 | 28.3 | 15.6 | `InfoFlowCBase` | 12.7 | 22.9% | `InfoFlowCBase`:16s; `InfoFlow`:13s |
| `DSpec.Types_D` | 2 | 27.2 | 13.6 | `DSpec` | 13.6 | 0.7% | `DSpec`:14s; `DBaseRefine`:14s |
| `ExecSpec.MachineTypes` | 3 | 26.0 | 8.9 | `CKernel` | 17.1 | 2.4% | `CKernel`:9s; `ASpec`:9s; `DSpec`:8s |
| `Access.Access` | 3 | 26.0 | 10.2 | `Access` | 15.8 | 0.3% | `Access`:10s; `DPolicy`:10s; `InfoFlowCBase`:6s |
| `CParser.CTranslation` | 2 | 24.3 | 14.7 | `CBaseRefine` | 9.5 | 54.3% | `CBaseRefine`:15s; `CParser`:10s |

## Remediation hints

### fix_root_inheritance_pattern_A_VALIDATED

**EMPIRICALLY VALIDATED 2026-05-09** (experiments/cbaserefine-swap-parent.md, 4 runs). Two ROOT swaps applied: (1) CBaseRefine `= CSpec + sessions Refine` → `= Refine + sessions CSpec`; (2) CRefineSyscall `= CBaseRefine + sessions CRefine` → `= CRefine +`. Result on 5 affected sessions: CBaseRefine wall 5183s → 1318s (-74.6%), CRefineSyscall 3306s → 1.2s (-99.97%, was pure dup), CRefine 4557s → 4039s (-11.4%, indirect bonus from cleaner parent heap), InfoFlowCBase +149s and InfoFlowC -4s (downstream regression from residual CSTR in InfoFlow* chain). Total: -7543s wall = -29.8% of canonical TUNED 25,331s. Far exceeded original 5519s upper bound. Excess gain came from: (a) ML-state amplification (re-executed theories take 1.79-3.61x longer than original session timing), (b) GC pressure as wall multiplier (CBaseRefine GC 66.5% of wall → 22.1%), (c) CRefineSyscall being a 1-theory packaging session (own work ≈1s, all baseline wall was CSTR dup), (d) parent-heap hygiene transfer to descendants. Patch in experiments/cbaserefine-swap-parent.patch. **Status: applied to C-refinement chain; InfoFlow*/D-spec/D-policy chains untouched (size asymmetry makes the same swap pattern infeasible).**

### remove_dead_sessions_X_post_swap_VALIDATED

**Run 4 (2026-05-09) finding**: after a parent swap that puts session Y into the new heap chain, any `sessions Y` declaration in DOWNSTREAM sessions becomes dead but is not necessarily harmless. Removed `sessions Refine` from InfoFlowCBase (Refine now in CRefine.heap parent chain post-Run 1) → InfoFlowC wall 958s → 841s (-118s, back to baseline). Mechanism: dead `sessions X` declarations cause the host session's .heap to retain extra namespace/dep metadata; downstream consumers pay a small load tax for that metadata. **Action**: after applying any pattern A fix, scan downstream ROOT entries for now-redundant `sessions X` lines and remove. Estimated wall recovery per dead declaration: 50-150s on downstream session (small but positive).

### split_meta_imports_pattern_A

Backup if full ROOT change is too risky for some other session: split the meta-theory (e.g., `Refine.Refine` that re-exports all session theories) so the current session imports only the subset it actually needs. Use reports/critical-path-all.json fanout data to identify which theories the downstream session actually uses. **Not pursued** since fix_root_inheritance_pattern_A_VALIDATED was viable.

### skip_proofs_environment_workaround

l4v already provides this: `proof/ROOT:111` declares the duplicated theories under condition=SKIP_DUPLICATED_PROOFS with quick_and_dirty + skip_proofs, allowing CI/dev workflows to bypass re-execution at the cost of weaker checking. Suitable for incremental dev cycles, not release builds. Orthogonal to the validated structural fix.

### split_heavy_theory_into_base_heavy_pattern_A

Per-theory micro-optimisation: for specific high-weight duplicated theories (Refine.Finalise_R 650s total, Refine.Invariants_H 359s, etc.), split the .thy into Base (stable interface lemmas downstream needs) + Heavy (internal proof bodies only Refine itself uses). **Not pursued** for the C-refinement chain since fix_root_inheritance_pattern_A obsoleted the per-theory amplification (those theories no longer appear in CBaseRefine.db). May still apply to InfoFlow* chain where the structural fix isn't viable.

### remediation_status_summary

**Solved (CSTR chain on C-refinement)**: CBaseRefine, CRefine, CRefineSyscall — covered by fix_root_inheritance_pattern_A_VALIDATED. Saved -7543s wall.  **Untouched (residual CSTR)**: InfoFlowCBase ↔ InfoFlow + Access + DPolicy (~640s aggregate), DSpec ↔ ASpec (~93s), DBaseRefine ↔ DSpec (~80s), DPolicy ↔ Access (~60s), Pattern C broadcasts (ASpec ↔ multiple, ~270s). Total residual: ~1100s aggregate dup, estimated ~250s wall — much smaller than the proof-chain win. Most residual cases have unfavourable size asymmetry (small heap in `+`, large heap in `sessions`) so the same swap pattern would make things worse; would need DAG restructure (e.g., split_heavy_theory_into_base_heavy_pattern_A) to address.

## How downstream tools use this

`reports/critical-path-{proof,spec,haskell,c}.md` and `dag.py` use the `top_duplicated_theories[].total_elapsed_across_sessions` field as each theory's weight in the CPM cost model — capturing the true rebuild cost (vs the lower single-session elapsed). The `session_summary[].inheritance_trigger_breakdown` field directly identifies the ROOT lines a structural-fix branch would edit.

