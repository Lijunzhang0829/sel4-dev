# Theory Axis Analysis — 2D Classification

_Every theory in the canonical ARM build is labeled along two axes:_

  - **source pillar** = which pillar's *files* invalidate this theory directly
    (`spec` / `haskell` / `c` / `proof` / `lib`)
  - **dep pillar class** = which pillars' *changes* transitively reach this theory
    (`needs-H-AND-C` / `needs-H-NOT-C` / `needs-C-NOT-H` /
    `needs-spec-only` / `lib-only`)

`spec` and `lib` are foundational — almost every proof imports them —
so the dep classification *distinguishes* only on the haskell-vs-c axis.
A theory labeled `needs-H-NOT-C` still typically imports spec and lib,
but does NOT import any c-pillar content.

## Theory universe (canonical ARM build)

- 1243 unique theories total
- 1094 in theory-DAG (have file path + import edges)
- 846 actually executed in some BLOB during the canonical build
- 149 have no DAG record (mostly Isabelle distribution: HOL, Pure, Simpl-VCG, UmmTypes)

## Source pillar distribution

| Source pillar | Theory count | Sum wall (s) | Description |
|---|---:|---:|---|
| `spec` | 81 | 1045 | abstract spec + cspec Isabelle wrappers + other specs |
| `haskell` | 69 | 316 | spec/design/ generated from spec/haskell/ |
| `c` | 4 | 60 | spec/cspec/c/build/ generated from seL4/src/ |
| `proof` | 304 | 8683 | all proof/* subdirs (refine/crefine/access/infoflow/invariant-abstract/...) |
| `lib` | 785 | 1032 | lib/ + tools/ + Isabelle distribution (HOL/Pure/Simpl-VCG/...) |

## 2D matrix: source × dep class

_Counts shown as `count theories / wall_s`. Empty cells = 0._

| Source ↓ \ Dep → | needs-H-AND-C | needs-H-NOT-C | needs-C-NOT-H | needs-spec-only | lib-only |
|---|---|---|---|---|---|
| `spec` | 1 / 1s | 49 / 960s | — | 25 / 81s | 6 / 4s |
| `haskell` | — | 66 / 291s | — | 3 / 25s | — |
| `c` | — | — | — | — | 4 / 60s |
| `proof` | 45 / 1931s | 248 / 6725s | — | 2 / 4s | 9 / 23s |
| `lib` | 1 / 0s | 27 / 5s | — | — | 757 / 1027s |

## Sample theories per cell (selected)

### `source=proof` × `dep=needs-H-AND-C` — the cross-cut refinement work — irreducible CSTR

_45 theories, 1931s wall_

- `CRefine.Schedule_C` (20.9s)
- `CRefine.Ipc_C` (150.1s)
- `CRefine.DetWP` (24.0s)
- `CRefine.VSpace_C` (94.3s)

### `source=proof` × `dep=needs-H-NOT-C` — Haskell-side proofs; belong in Refine/Access/InfoFlow

_248 theories, 6725s wall_

- `Refine.KernelInit_R` (2.6s)
- `Access.DomainSepInv` (4.6s)
- `AInvs.Deterministic_AI` (20.5s)
- `InfoFlow.FinalCaps` (6.1s)

### `source=proof` × `dep=needs-C-NOT-H` — if any — would be surprise (seL4's C proofs are ccorres)

_(empty)_

### `source=proof` × `dep=needs-spec-only` — proof depends only on abstract spec, not h or c

_2 theories, 4s wall_

- `SepDSpec.AbstractSeparation_SD` (3.8s)
- `AInvs.Rights_AI` (0.4s)

### `source=proof` × `dep=lib-only` — self-contained helper theories

_9 theories, 23s wall_

- `SepDSpec.Sep_Tactic_Helper` (0.5s)
- `CBaseRefine.L4VerifiedLinks` (0.4s)
- `DRefine.MoreHOL` (0.2s)
- `InfoFlow.Noninterference_Base_Alternatives` (7.7s)

### `source=spec` × `dep=needs-H-AND-C` — spec material that transitively needs c — odd

_1 theories, 1s wall_

- `CSpec.KernelInc_C` (0.7s)

### `source=c` × `dep=needs-H-NOT-C` — c-source theories transitively needing haskell — odd

_(empty)_

## Misalignment candidates

Theories whose **source dir** sits in a cross-cutting session
but whose **dep class** is `needs-H-NOT-C` — these are Haskell-side
proofs that ended up living in cross-cut directories. They could
be moved to a Haskell-axis session without paying CSTR amplification.

_Found 12 candidates totaling 2462s wall:_

| Theory | Current session | Wall (s) | File |
|---|---|---:|---|
| `SimplExportAndRefine.SEL4GraphRefine` | `SimplExportAndRefine` | 2260.3 | `verification/l4v/proof/asmrefine/SEL4GraphRefine.thy` |
| `CRefine.IsolatedThreadAction` | `CRefine` | 36.1 | `verification/l4v/proof/crefine/ARM/IsolatedThreadAction.thy` |
| `InfoFlowC.Example_Valid_StateH` | `InfoFlowC` | 35.7 | `verification/l4v/proof/infoflow/refine/ARM/Example_Valid_StateH.thy` |
| `CRefine.Fastpath_Equiv` | `CRefine` | 32.8 | `verification/l4v/proof/crefine/ARM/Fastpath_Equiv.thy` |
| `InfoFlowC.ADT_IF_Refine` | `InfoFlowC` | 21.3 | `verification/l4v/proof/infoflow/refine/ADT_IF_Refine.thy` |
| `CRefine.Fastpath_Defs` | `CRefine` | 20.8 | `verification/l4v/proof/crefine/ARM/Fastpath_Defs.thy` |
| `CRefine.ArchMove_C` | `CRefine` | 19.6 | `verification/l4v/proof/crefine/ARM/ArchMove_C.thy` |
| `SimplExportAndRefine.SEL4GlobalsSwap` | `SimplExportAndRefine` | 11.9 | `verification/l4v/proof/asmrefine/SEL4GlobalsSwap.thy` |
| `InfoFlowC.ArchADT_IF_Refine` | `InfoFlowC` | 9.9 | `verification/l4v/proof/infoflow/refine/ARM/ArchADT_IF_Refine.thy` |
| `CRefine.CToCRefine` | `CRefine` | 7.2 | `verification/l4v/proof/crefine/lib/CToCRefine.thy` |
| `CRefine.Move_C` | `CRefine` | 6.3 | `verification/l4v/proof/crefine/Move_C.thy` |
| `SimplExportAndRefine.TestGraphRefine` | `SimplExportAndRefine` | 0.0 | `verification/l4v/proof/asmrefine/TestGraphRefine.thy` |

## Methodology notes

- **Per-arch view**: theory-DAG was generated with `L4V_ARCH=ARM`,
  so the per-pillar counts reflect what ARM build pulls in. Other
  archs (X64/ARM_HYP/AARCH64/RISCV64) have their own arch-specific
  variants that would multiply some counts ~5×.
- **DAG completeness**: theories not in DAG (HOL/Pure/Simpl-VCG/
  UmmTypes — ~140 of the 846 BLOB total) are assigned `source=lib,
  dep=lib-only` by namespace fallback. Their actual imports are
  inside the Isabelle distribution and not relevant for proof-axis
  restructuring.
- **Wall accounting**: `theory_wall` is the elapsed time from the
  theory's origin-session BLOB (the lighter, non-CSTR-amplified
  measurement). The CSTR-amplified cost in downstream sessions is
  separate and tracked in [session-duplication-scan](session-duplication-scan.md).
