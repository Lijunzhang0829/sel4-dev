# Session-level theory duplication scan (P0.5)

_Empirical scan of `heaps/db-archive/*.db` `theory_timings` BLOBs to quantify cross-session re-execution of the same .thy source. See [`tools/critical_path/session_dedupe_scan.py`](../tools/critical_path/session_dedupe_scan.py) for the scanner._

## Headline

- Sessions scanned: **28**
- Unique theory names: **845**
- Theories duplicated across ≥2 sessions: **464** (54.9%)
- Σ per-theory elapsed across all sessions: **19507.1s**
- **Duplication overhead estimate**: **5585.3s** (28.6%)

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

**`sessions X` (namespace only).** `sessions X` only declares X for name resolution. When a theory in the current session writes `imports "X.foo"`, Isabelle locates X.foo's source file via X's session dir, then EXECUTES it in the current session's ML state (NOT loaded from X.heap). This is the source of the cross-session duplication observed in P0.5.

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
| `CBaseRefine` | A | 304 | 5598.7 | 274 | 5519.3 | 98.6% | `CLib`→7s; `AutoCorres`→42s | `Refine` |
| `CRefine` | A | 52 | 2334.1 | 44 | 1882.6 | 80.7% | — | `CBaseRefine` |
| `Refine` | A | 48 | 1733.8 | 47 | 1723.0 | 99.4% | `Lib`→32s; `CorresK`→5s | `BaseRefine` |
| `CRefineSyscall` | A | 44 | 1546.1 | 43 | 1546.0 | 100.0% | — | `CRefine` |
| `AInvs` | A | 119 | 1052.6 | 119 | 1052.6 | 100.0% | — | `ASpec` |
| `InfoFlowCBase` | A | 70 | 647.9 | 69 | 644.0 | 99.4% | `Access`→273s; `InfoFlow`→366s | `CRefine` |
| `InfoFlow` | A | 46 | 367.7 | 42 | 349.1 | 94.9% | — | `Access` |
| `BaseRefine` | A | 63 | 283.6 | 63 | 283.6 | 100.0% | `Lib`→0s | `AInvs` |
| `Access` | A | 28 | 306.7 | 27 | 281.8 | 91.9% | — | `AInvs` |
| `ASpec` | A | 107 | 270.5 | 105 | 270.0 | 99.8% | `Lib`→23s; `ExecSpec`→39s | `Word_Lib` |
| `DSpec` | A | 83 | 174.4 | 83 | 174.4 | 100.0% | `ExecSpec`→17s; `ASpec`→3s | `Word_Lib` |
| `DBaseRefine` | A | 19 | 94.5 | 18 | 89.8 | 95.1% | `DSpec`→89s | `AInvs` |
| `CKernel` | C | 61 | 785.5 | 56 | 87.3 | 11.1% | `ExecSpec`→12s; `CLib`→1s; `AsmRefine`→5s | `CParser` |
| `SepDSpec` | C | 56 | 104.7 | 29 | 71.3 | 68.1% | `Sep_Algebra`→19s; `SepTactics`→1s | `DSpec` |
| `DPolicy` | B | 8 | 81.3 | 7 | 60.9 | 75.0% | `Access`→61s | `DRefine` |
| `InfoFlowC` | B | 7 | 234.0 | 1 | 30.0 | 12.8% | — | `InfoFlowCBase` |
| `CParser` | B | 35 | 66.2 | 2 | 1.4 | 2.1% | `ML_Utils`→1s; `Basics`→1s | `Simpl-VCG` |
| `SimplExport` | B | 7 | 646.7 | 1 | 0.3 | 0.1% | — | `CSpec` |
| `Bisim` | B | 6 | 27.6 | 0 | 0.0 | 0.0% | `ASepSpec`→4s | `AInvs` |
| `CSpec` | B | 8 | 148.9 | 0 | 0.0 | 0.0% | — | `CKernel` |
| `DRefine` | B | 18 | 163.4 | 0 | 0.0 | 0.0% | — | `DBaseRefine` |
| `DSpecProofs` | B | 12 | 27.0 | 0 | 0.0 | 0.0% | — | `SepDSpec` |
| `HOL` | B | 114 | 379.4 | 0 | 0.0 | 0.0% | — | — |
| `Pure` | B | 3 | 1.3 | 0 | 0.0 | 0.0% | — | — |
| `RefineOrphanage` | B | 1 | 49.1 | 0 | 0.0 | 0.0% | — | `Refine` |
| `Simpl-VCG` | B | 18 | 44.7 | 0 | 0.0 | 0.0% | — | `Word_Lib` |
| `SimplExportAndRefine` | B | 8 | 2263.7 | 0 | 0.0 | 0.0% | — | `SimplExport` |
| `Word_Lib` | B | 66 | 73.2 | 0 | 0.0 | 0.0% | — | `HOL` |

## Case studies (top 5 by dup elapsed)

### CBaseRefine_re-executes_Refine

- **Perpetrator (does the re-execution)**: `CBaseRefine`
- **Victim (its theories get re-executed)**: `Refine`
- **Elapsed in perpetrator on overlapping theories**: 3537.9s
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

- `verification/l4v/proof/crefine/base/Include_C.thy` → `imports "Refine.Refine"`

**Spotlight theory `Refine.Finalise_R`** — same .thy source, two ML contexts:

| in session | elapsed (s) | cpu (s) | gc (s) |
|---|---:|---:|---:|
| `CBaseRefine` | 424.3 | 1813.7 | 258.7 |
| `Refine` | 225.6 | 1406.6 | 146.4 |

_Same .thy source; ran in two different ML contexts. In CBaseRefine (this session) it took 424.3s; in Refine (originating session) it took 225.6s. Difference reflects the different sets of active simp rules / locale interpretations / type class instances inherited from each session's `+` parent heap._

### CRefineSyscall_re-executes_CRefine

- **Perpetrator (does the re-execution)**: `CRefineSyscall`
- **Victim (its theories get re-executed)**: `CRefine`
- **Elapsed in perpetrator on overlapping theories**: 1546.0s
- **Pattern**: `A_high_dup_structural_fix_candidate`

**ROOT declaration**:

```
session CRefineSyscall in "intermediate" = CRefine +
```

**Concrete trigger imports** (source lines that pull in the duplicated session):

- `verification/l4v/proof/crefine/intermediate/Intermediate_C.thy` → `imports "CRefine.Syscall_C"`

**Spotlight theory `CRefine.Ipc_C`** — same .thy source, two ML contexts:

| in session | elapsed (s) | cpu (s) | gc (s) |
|---|---:|---:|---:|
| `CRefine` | 184.0 | 949.7 | 128.6 |
| `CRefineSyscall` | 148.3 | 880.3 | 105.9 |

_Same .thy source; ran in two different ML contexts. In CRefine (this session) it took 184.0s; in CRefineSyscall (originating session) it took 148.3s. Difference reflects the different sets of active simp rules / locale interpretations / type class instances inherited from each session's `+` parent heap._

### CBaseRefine_re-executes_AInvs

- **Perpetrator (does the re-execution)**: `CBaseRefine`
- **Victim (its theories get re-executed)**: `AInvs`
- **Elapsed in perpetrator on overlapping theories**: 1416.0s
- **Pattern**: `A_high_dup_structural_fix_candidate`

**ROOT declaration**:

```
session CBaseRefine in "base" = Refine +
  sessions
    CLib
    CSpec
    AutoCorres
```

**Spotlight theory `AInvs.ArchRetype_AI`** — same .thy source, two ML contexts:

| in session | elapsed (s) | cpu (s) | gc (s) |
|---|---:|---:|---:|
| `CBaseRefine` | 84.2 | 434.1 | 25.6 |
| `AInvs` | 75.6 | 427.6 | 40.0 |

_Same .thy source; ran in two different ML contexts. In CBaseRefine (this session) it took 84.2s; in AInvs (originating session) it took 75.6s. Difference reflects the different sets of active simp rules / locale interpretations / type class instances inherited from each session's `+` parent heap._

### InfoFlowCBase_re-executes_InfoFlow

- **Perpetrator (does the re-execution)**: `InfoFlowCBase`
- **Victim (its theories get re-executed)**: `InfoFlow`
- **Elapsed in perpetrator on overlapping theories**: 370.9s
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

**Spotlight theory `InfoFlow.ArchUserOp_IF`** — same .thy source, two ML contexts:

| in session | elapsed (s) | cpu (s) | gc (s) |
|---|---:|---:|---:|
| `InfoFlowCBase` | 40.5 | 232.2 | 34.6 |
| `InfoFlow` | 26.4 | 151.3 | 5.2 |

_Same .thy source; ran in two different ML contexts. In InfoFlowCBase (this session) it took 40.5s; in InfoFlow (originating session) it took 26.4s. Difference reflects the different sets of active simp rules / locale interpretations / type class instances inherited from each session's `+` parent heap._

### CBaseRefine_re-executes_BaseRefine

- **Perpetrator (does the re-execution)**: `CBaseRefine`
- **Victim (its theories get re-executed)**: `BaseRefine`
- **Elapsed in perpetrator on overlapping theories**: 342.3s
- **Pattern**: `A_high_dup_structural_fix_candidate`

**ROOT declaration**:

```
session CBaseRefine in "base" = Refine +
  sessions
    CLib
    CSpec
    AutoCorres
```

**Spotlight theory `ExecSpec.Structures_H`** — same .thy source, two ML contexts:

| in session | elapsed (s) | cpu (s) | gc (s) |
|---|---:|---:|---:|
| `CBaseRefine` | 29.1 | 174.9 | 6.0 |
| `BaseRefine` | 23.6 | 38.9 | 0.9 |

_Same .thy source; ran in two different ML contexts. In CBaseRefine (this session) it took 29.1s; in BaseRefine (originating session) it took 23.6s. Difference reflects the different sets of active simp rules / locale interpretations / type class instances inherited from each session's `+` parent heap._

### InfoFlowCBase_re-executes_Access

- **Perpetrator (does the re-execution)**: `InfoFlowCBase`
- **Victim (its theories get re-executed)**: `Access`
- **Elapsed in perpetrator on overlapping theories**: 273.1s
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
| `Access` | 67.2 | 306.6 | 3.6 |
| `InfoFlowCBase` | 63.2 | 284.8 | 2.3 |

_Same .thy source; ran in two different ML contexts. In Access (this session) it took 67.2s; in InfoFlowCBase (originating session) it took 63.2s. Difference reflects the different sets of active simp rules / locale interpretations / type class instances inherited from each session's `+` parent heap._

### CBaseRefine_re-executes_ASpec

- **Perpetrator (does the re-execution)**: `CBaseRefine`
- **Victim (its theories get re-executed)**: `ASpec`
- **Elapsed in perpetrator on overlapping theories**: 223.1s
- **Pattern**: `A_high_dup_structural_fix_candidate`

**ROOT declaration**:

```
session CBaseRefine in "base" = Refine +
  sessions
    CLib
    CSpec
    AutoCorres
```

**Spotlight theory `ASpec.Structures_A`** — same .thy source, two ML contexts:

| in session | elapsed (s) | cpu (s) | gc (s) |
|---|---:|---:|---:|
| `CBaseRefine` | 22.7 | 164.0 | 4.9 |
| `ASpec` | 19.4 | 28.0 | 1.1 |

_Same .thy source; ran in two different ML contexts. In CBaseRefine (this session) it took 22.7s; in ASpec (originating session) it took 19.4s. Difference reflects the different sets of active simp rules / locale interpretations / type class instances inherited from each session's `+` parent heap._

### DSpec_re-executes_ASpec

- **Perpetrator (does the re-execution)**: `DSpec`
- **Victim (its theories get re-executed)**: `ASpec`
- **Elapsed in perpetrator on overlapping theories**: 93.1s
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
| `CKernel` | 10.0 | 28.4 | 1.3 |
| `ASpec` | 9.4 | 28.3 | 1.0 |
| `DSpec` | 9.0 | 31.3 | 0.7 |

_Same .thy source; ran in two different ML contexts. In CKernel (this session) it took 10.0s; in ASpec (originating session) it took 9.4s. Difference reflects the different sets of active simp rules / locale interpretations / type class instances inherited from each session's `+` parent heap._

### DBaseRefine_re-executes_DSpec

- **Perpetrator (does the re-execution)**: `DBaseRefine`
- **Victim (its theories get re-executed)**: `DSpec`
- **Elapsed in perpetrator on overlapping theories**: 89.8s
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
| `DBaseRefine` | 36.5 | 56.0 | 0.6 |
| `DSpec` | 27.6 | 104.0 | 3.4 |

_Same .thy source; ran in two different ML contexts. In DBaseRefine (this session) it took 36.5s; in DSpec (originating session) it took 27.6s. Difference reflects the different sets of active simp rules / locale interpretations / type class instances inherited from each session's `+` parent heap._

### DPolicy_re-executes_Access

- **Perpetrator (does the re-execution)**: `DPolicy`
- **Victim (its theories get re-executed)**: `Access`
- **Elapsed in perpetrator on overlapping theories**: 60.9s
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
| `DPolicy` | 25.8 | 41.4 | 1.9 |
| `Access` | 24.9 | 41.3 | 1.9 |
| `InfoFlowCBase` | 21.6 | 46.0 | 2.4 |

_Same .thy source; ran in two different ML contexts. In DPolicy (this session) it took 25.8s; in Access (originating session) it took 24.9s. Difference reflects the different sets of active simp rules / locale interpretations / type class instances inherited from each session's `+` parent heap._

## Shared third-party dependency overhead

These are theories owned by sessions NOT in the canonical 28-session build set (libraries like `Lib`, `Monads`, `Eisbach_Tools`, etc.) that appear in MULTIPLE canonical sessions' BLOBs. Each canonical session re-executes them independently because they're declared via `sessions Y` rather than heap-merged via `+ Y`. This is a different structural pattern from perpetrator/victim — there's no single 'duplicator' to fault. Remediation requires either making the third-party session a `+` parent of all consumers, or introducing a shared parent that already merges it.

| 3rd-party session | #thys | in #canonical sessions | Σelapsed (all appearances) | avg/appearance |
|---|---:|---:|---:|---:|
| `ExecSpec` | 75 | 5 | 717.6 | 4.54s |
| `Lib` | 54 | 11 | 277.3 | 1.86s |
| `Monads` | 34 | 6 | 212.6 | 2.17s |
| `CLib` | 5 | 2 | 56.5 | 5.65s |
| `Eisbach_Tools` | 11 | 5 | 40.3 | 1.30s |
| `CorresK` | 1 | 2 | 20.3 | 10.16s |
| `HOL-Library` | 2 | 3 | 4.3 | 0.72s |
| `ML_Utils` | 2 | 4 | 2.6 | 0.44s |
| `HOL-Combinatorics` | 1 | 2 | 1.8 | 0.92s |
| `Basics` | 1 | 3 | 1.3 | 0.44s |

## Top 30 duplicated theories by total cross-session cost

| theory | #sess | Σelapsed (all) | max single | max session | overhead | Δmax/2nd % | per-session breakdown |
|---|---:|---:|---:|---|---:|---:|---|
| `Refine.Finalise_R` | 2 | 649.8 | 424.3 | `CBaseRefine` | 225.6 | 88.1% | `CBaseRefine`:424s; `Refine`:226s |
| `Refine.Retype_R` | 2 | 382.0 | 294.4 | `CBaseRefine` | 87.7 | 235.8% | `CBaseRefine`:294s; `Refine`:88s |
| `Refine.Untyped_R` | 2 | 366.9 | 249.5 | `CBaseRefine` | 117.5 | 112.4% | `CBaseRefine`:250s; `Refine`:118s |
| `Refine.Invariants_H` | 2 | 359.4 | 230.6 | `CBaseRefine` | 128.8 | 79.1% | `CBaseRefine`:231s; `Refine`:129s |
| `Refine.IpcCancel_R` | 2 | 348.0 | 272.5 | `CBaseRefine` | 75.5 | 261.0% | `CBaseRefine`:272s; `Refine`:76s |
| `CRefine.Ipc_C` | 2 | 332.3 | 184.0 | `CRefine` | 148.3 | 24.0% | `CRefine`:184s; `CRefineSyscall`:148s |
| `CRefine.StateRelation_C` | 2 | 315.4 | 166.2 | `CRefineSyscall` | 149.2 | 11.4% | `CRefineSyscall`:166s; `CRefine`:149s |
| `CRefine.Retype_C` | 2 | 297.3 | 158.3 | `CRefine` | 139.1 | 13.8% | `CRefine`:158s; `CRefineSyscall`:139s |
| `Refine.CSpace_R` | 2 | 282.5 | 181.8 | `CBaseRefine` | 100.8 | 80.4% | `CBaseRefine`:182s; `Refine`:101s |
| `Refine.Ipc_R` | 2 | 265.5 | 193.7 | `CBaseRefine` | 71.7 | 170.1% | `CBaseRefine`:194s; `Refine`:72s |
| `Refine.Detype_R` | 2 | 262.8 | 170.0 | `CBaseRefine` | 92.8 | 83.3% | `CBaseRefine`:170s; `Refine`:93s |
| `Refine.CNodeInv_R` | 2 | 260.0 | 157.9 | `CBaseRefine` | 102.1 | 54.7% | `CBaseRefine`:158s; `Refine`:102s |
| `CRefine.Tcb_C` | 2 | 238.8 | 206.6 | `CRefine` | 32.2 | 541.7% | `CRefine`:207s; `CRefineSyscall`:32s |
| `Refine.CSpace1_R` | 2 | 237.9 | 152.7 | `CBaseRefine` | 85.1 | 79.4% | `CBaseRefine`:153s; `Refine`:85s |
| `CRefine.VSpace_C` | 2 | 195.6 | 104.8 | `CRefine` | 90.8 | 15.3% | `CRefine`:105s; `CRefineSyscall`:91s |
| `Refine.CSpace_I` | 2 | 174.0 | 132.2 | `CBaseRefine` | 41.8 | 216.3% | `CBaseRefine`:132s; `Refine`:42s |
| `CRefine.SyscallArgs_C` | 2 | 170.5 | 93.5 | `CRefine` | 76.9 | 21.6% | `CRefine`:94s; `CRefineSyscall`:77s |
| `CRefine.Finalise_C` | 2 | 166.1 | 84.4 | `CRefine` | 81.7 | 3.3% | `CRefine`:84s; `CRefineSyscall`:82s |
| `AInvs.ArchRetype_AI` | 2 | 159.7 | 84.2 | `CBaseRefine` | 75.6 | 11.4% | `CBaseRefine`:84s; `AInvs`:76s |
| `Refine.ADT_H` | 2 | 158.2 | 102.5 | `CBaseRefine` | 55.8 | 83.7% | `CBaseRefine`:102s; `Refine`:56s |
| `Refine.Tcb_R` | 2 | 156.1 | 123.9 | `CBaseRefine` | 32.2 | 285.0% | `CBaseRefine`:124s; `Refine`:32s |
| `Refine.TcbAcc_R` | 2 | 150.1 | 101.5 | `CBaseRefine` | 48.6 | 108.9% | `CBaseRefine`:102s; `Refine`:49s |
| `CRefine.Invoke_C` | 2 | 130.7 | 81.8 | `CRefine` | 48.9 | 67.3% | `CRefine`:82s; `CRefineSyscall`:49s |
| `Access.ArchRetype_AC` | 2 | 130.4 | 67.2 | `Access` | 63.2 | 6.3% | `Access`:67s; `InfoFlowCBase`:63s |
| `CRefine.Wellformed_C` | 2 | 128.4 | 67.5 | `CRefineSyscall` | 60.8 | 11.0% | `CRefineSyscall`:68s; `CRefine`:61s |
| `CRefine.Corres_C` | 2 | 120.8 | 66.0 | `CRefine` | 54.8 | 20.6% | `CRefine`:66s; `CRefineSyscall`:55s |
| `CRefine.Arch_C` | 2 | 118.7 | 73.4 | `CRefine` | 45.4 | 61.8% | `CRefine`:73s; `CRefineSyscall`:45s |
| `Refine.KHeap_R` | 2 | 115.3 | 68.1 | `CBaseRefine` | 47.1 | 44.5% | `CBaseRefine`:68s; `Refine`:47s |
| `AInvs.Invariants_AI` | 2 | 115.0 | 64.7 | `CBaseRefine` | 50.4 | 28.4% | `CBaseRefine`:65s; `AInvs`:50s |
| `Refine.VSpace_R` | 2 | 111.5 | 74.3 | `CBaseRefine` | 37.2 | 99.7% | `CBaseRefine`:74s; `Refine`:37s |

## Remediation hints

### fix_root_inheritance_pattern_A_VALIDATED

**EMPIRICALLY VALIDATED 2026-05-09** (experiments/cbaserefine-swap-parent.md, 4 runs). Two ROOT swaps applied: (1) CBaseRefine `= CSpec + sessions Refine` → `= Refine + sessions CSpec`; (2) CRefineSyscall `= CBaseRefine + sessions CRefine` → `= CRefine +`. Result on 5 affected sessions: CBaseRefine wall 5183s → 1318s (-74.6%), CRefineSyscall 3306s → 1.2s (-99.97%, was pure dup), CRefine 4557s → 4039s (-11.4%, indirect bonus from cleaner parent heap), InfoFlowCBase +149s and InfoFlowC -4s (downstream regression from residual P0.5 in InfoFlow* chain). Total: -7543s wall = -29.8% of canonical TUNED 25,331s. Far exceeded original 5519s upper bound. Excess gain came from: (a) ML-state amplification (re-executed theories take 1.79-3.61x longer than original session timing), (b) GC pressure as wall multiplier (CBaseRefine GC 66.5% of wall → 22.1%), (c) CRefineSyscall being a 1-theory packaging session (own work ≈1s, all baseline wall was P0.5 dup), (d) parent-heap hygiene transfer to descendants. Patch in experiments/cbaserefine-swap-parent.patch. **Status: applied to C-refinement chain; InfoFlow*/D-spec/D-policy chains untouched (size asymmetry makes the same swap pattern infeasible).**

### remove_dead_sessions_X_post_swap_VALIDATED

**Run 4 (2026-05-09) finding**: after a parent swap that puts session Y into the new heap chain, any `sessions Y` declaration in DOWNSTREAM sessions becomes dead but is not necessarily harmless. Removed `sessions Refine` from InfoFlowCBase (Refine now in CRefine.heap parent chain post-Run 1) → InfoFlowC wall 958s → 841s (-118s, back to baseline). Mechanism: dead `sessions X` declarations cause the host session's .heap to retain extra namespace/dep metadata; downstream consumers pay a small load tax for that metadata. **Action**: after applying any pattern A fix, scan downstream ROOT entries for now-redundant `sessions X` lines and remove. Estimated wall recovery per dead declaration: 50-150s on downstream session (small but positive).

### split_meta_imports_pattern_A

Backup if full ROOT change is too risky for some other session: split the meta-theory (e.g., `Refine.Refine` that re-exports all session theories) so the current session imports only the subset it actually needs. Use reports/critical-path-all.json fanout data to identify which theories the downstream session actually uses. **Not pursued** since fix_root_inheritance_pattern_A_VALIDATED was viable.

### skip_proofs_environment_workaround

l4v already provides this: `proof/ROOT:111` declares the duplicated theories under condition=SKIP_DUPLICATED_PROOFS with quick_and_dirty + skip_proofs, allowing CI/dev workflows to bypass re-execution at the cost of weaker checking. Suitable for incremental dev cycles, not release builds. Orthogonal to the validated structural fix.

### split_heavy_theory_into_base_heavy_pattern_A

Per-theory micro-optimisation: for specific high-weight duplicated theories (Refine.Finalise_R 650s total, Refine.Invariants_H 359s, etc.), split the .thy into Base (stable interface lemmas downstream needs) + Heavy (internal proof bodies only Refine itself uses). **Not pursued** for the C-refinement chain since fix_root_inheritance_pattern_A obsoleted the per-theory amplification (those theories no longer appear in CBaseRefine.db). May still apply to InfoFlow* chain where the structural fix isn't viable.

### remediation_status_summary

**Solved (P0.5 chain on C-refinement)**: CBaseRefine, CRefine, CRefineSyscall — covered by fix_root_inheritance_pattern_A_VALIDATED. Saved -7543s wall.  **Untouched (residual P0.5)**: InfoFlowCBase ↔ InfoFlow + Access + DPolicy (~640s aggregate), DSpec ↔ ASpec (~93s), DBaseRefine ↔ DSpec (~80s), DPolicy ↔ Access (~60s), Pattern C broadcasts (ASpec ↔ multiple, ~270s). Total residual: ~1100s aggregate dup, estimated ~250s wall — much smaller than the proof-chain win. Most residual cases have unfavourable size asymmetry (small heap in `+`, large heap in `sessions`) so the same swap pattern would make things worse; would need DAG restructure (e.g., split_heavy_theory_into_base_heavy_pattern_A) to address.

## How downstream tools use this

`reports/critical-path-{proof,spec,haskell,c}.md` and `dag.py` use the `top_duplicated_theories[].total_elapsed_across_sessions` field as each theory's weight in the CPM cost model — capturing the true rebuild cost (vs the lower single-session elapsed). The `session_summary[].inheritance_trigger_breakdown` field directly identifies the ROOT lines a structural-fix branch would edit.

