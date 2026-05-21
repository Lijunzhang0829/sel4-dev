# Theory Classification Baseline — 2026-05-14

> **⚠ 2026-05-15 correction note.** The "Change-locality matrix" section
> at the end of this document was originally framed as "sessions that
> rebuild on pillar-X change". Per maintainer correction (Klein,
> devel@sel4.systems), Isabelle's invalidation is **per-theory content-
> hash**, not session-level dep-set. The matrix has been re-titled
> "Static-dependency reach" and re-interpreted as an upper bound; do
> NOT read those numbers as "real" rebuild scope. The 2D classification
> (source × dep_class) is a STATIC dependency fact and remains accurate.

Frozen snapshot of the seL4 verification theory universe as of commit
`a2e0f17` (post-CSTR swap, before any pillar-decoupling refactor).

This document is the **reference point** against which all subsequent
structural refactors (target b, target c, target d, or any pillar-
decoupling work) should be compared. The simulator outputs and the
2D classification numbers below are the **invariants we want to track**.

Anyone re-running [`tools/critical_path/theory_axis_2d.py`](../tools/critical_path/theory_axis_2d.py)
should get THESE numbers; deviations indicate either the refactor took
effect or DAG inputs drifted.

---

## Environment

| Field | Value |
|---|---|
| Branch | `cstr-2graph` |
| Commit | `a2e0f17` |
| `L4V_ARCH` | `ARM` |
| Isabelle | 2024 (from container) |
| Heap data source | `heaps/db-archive/*.db` (post-swap canonical build) |
| Theory DAG source | `reports/theory-dag.json` (1094 nodes, 1961 edges) |

## Theory universe

| Bucket | Count |
|---|---:|
| Unique theories observed (DAG ∪ BLOB) | **1243** |
| In theory-DAG (verification/l4v/ scan) | 1094 |
| In a BLOB (actually built in canonical ARM build) | 846 |
| BLOB-only (no DAG record; Isabelle distribution / CAMKES) | 149 |

## Source-pillar distribution (Dimension 1)

Where the `.thy` file physically lives → which pillar's source edit
directly invalidates it.

| Pillar | Theory count | Σ wall (s) | Path patterns |
|---|---:|---:|---|
| `spec` | 81 | 1045 | `/spec/abstract/`, `/spec/cspec/` (Isabelle wrappers; excludes c/build/), other `/spec/` |
| `haskell` | 69 | 316 | `/spec/haskell/` (source), `/spec/design/` (generated) |
| `c` | 4 | 60 | `/spec/cspec/c/build/ARM/` (generated C-parser output) |
| `proof` | 304 | 8683 | all `/proof/*` subdirs |
| `lib` | 785 | 1032 | `/lib/`, `/tools/`, Isabelle distribution |
| **TOTAL** | **1243** | **11136** | |

## Dep-class distribution (Dimension 2)

Which non-universal pillars the theory's `imports` closure transitively
touches. spec/lib are universal foundations and excluded from distinction.

| Class | Theory count | Σ wall (s) | Definition |
|---|---:|---:|---|
| `needs-H-AND-C` | 47 | 1933 | closure touches haskell **and** c |
| `needs-H-NOT-C` | 390 | 7976 | touches haskell, **not** c |
| `needs-C-NOT-H` | **0** | **0** | touches c, **not** haskell |
| `needs-spec-only` | 30 | 110 | touches spec only |
| `lib-only` | 776 | 1117 | self-contained / lib only |

## 2D matrix (source × dep)

Counts and walls per cell:

| Source ↓ \ Dep → | needs-H-AND-C | needs-H-NOT-C | needs-C-NOT-H | needs-spec-only | lib-only |
|---|---|---|---|---|---|
| `spec` | 1 / 1s | **49 / 960s** | — | 25 / 81s | 6 / 4s |
| `haskell` | — | 66 / 291s | — | 3 / 25s | — |
| `c` | — | — | — | — | 4 / 60s |
| `proof` | **45 / 1931s** | 248 / 6725s | **0 / 0s** | 2 / 4s | 9 / 23s |
| `lib` | 1 / 0s | 27 / 5s | — | — | 757 / 1027s |

## Key invariants to preserve

These are the structural facts to **maintain or improve** through future
refactors:

1. **`proof × needs-C-NOT-H = 0`**
   No proof theory needs only C content (without haskell). Reason: every
   C-side proof in seL4 is a `ccorres` (C function ⟷ haskell function).
   If a refactor introduces a c-only proof, that's structural news worth
   flagging.

2. **45 cross-cut proof theories (1931s wall)**
   The irreducible refinement bridge. Subject to optimization at the
   lemma level (Phase C) but not removable by structural means.
   Names in `proof × needs-H-AND-C` cell — see [theory-axis-2d.md](theory-axis-2d.md).

3. **9 direct spec→haskell back-edges via 5 targets** (the (a) targets)
   The spec pillar transitively depends on haskell via these specific
   imports. Fix (a) aims to break this coupling.

   | Haskell target imported by spec | Spec importers |
   |---|---|
   | `ExecSpec.MachineTypes` | `CKernel.Kernel_C`, `CSpec.Kernel_C`, `ExecSpec.MachineMonad` |
   | `ExecSpec.InvocationLabels_H` | `ASpec.ArchDecode_A`, `ASpec.Decode_A` |
   | `ExecSpec.Event_H` | `ASpec.Syscall_A`, `DSpec.Syscall_D` |
   | `ExecSpec.Arch_Structs_B` | `ASpec.Arch_Structs_A` |
   | `ExecSpec.ArchLabelFuns_H` | `ASpec.InvocationLabels_A` |

   After fix (a): expected to reduce `spec × needs-H-NOT-C` from 49 → near 0.

4. **Residual CSTR cost ≤ 1768s** (per [session-duplication-scan.json](session-duplication-scan.json))
   The two ROOT swaps (CBaseRefine, CRefineSyscall) brought this down
   from 5585s pre-swap. The new floor is 1768s after target (a) and
   beyond should bring it lower.

5. **Canonical TUNED build wall: 16,942s** (per [heaps/build_log.txt](../heaps/build_log.txt))
   The post-swap canonical wall. Refactors should hold this or improve.

## Cross-cut theory list (proof × needs-H-AND-C, 45 entries)

These 45 theories are the "irreducible cross-cut" — Phase C lemma
optimization targets them; structural refactors don't move them.

Sorted by origin-session wall:

| Theory | Wall (s) | Session |
|---|---:|---|
| `CRefine.ADT_C` | 207.6 | CRefine |
| `CRefine.Retype_C` | 154.0 | CRefine |
| `CRefine.Ipc_C` | 150.1 | CRefine |
| `CRefine.StateRelation_C` | 118.0 | CRefine |
| `CRefine.VSpace_C` | 94.3 | CRefine |
| `CRefine.Finalise_C` | 82.1 | CRefine |
| `CRefine.SyscallArgs_C` | 81.0 | CRefine |
| `InfoFlowC.Noninterference_Refinement` | 65.7 | InfoFlowC |
| `CRefine.Wellformed_C` | 62.5 | CRefine |
| `CRefine.Recycle_C` | 56.4 | CRefine |
| `CRefine.Corres_C` | 55.6 | CRefine |
| `InfoFlowC.ADT_IF_Refine_C` | 55.1 | InfoFlowC |
| `CRefine.Fastpath_C` | 54.4 | CRefine |
| `CRefine.Invoke_C` | 51.5 | CRefine |
| `CRefine.Refine_C` | 47.3 | CRefine |
| `CBaseRefine.Include_C` | 44.5 | CBaseRefine |
| `CRefine.Machine_C` | 38.2 | CRefine |
| `CRefine.PSpace_C` | 36.8 | CRefine |
| `CRefine.Syscall_C` | 35.9 | CRefine |
| `CRefine.CSpace_C` | 33.6 | CRefine |
| `CRefine.Arch_C` | 33.5 | CRefine |
| `CRefine.Tcb_C` | 32.1 | CRefine |
| `CRefine.Delete_C` | 27.4 | CRefine |
| `CRefine.SR_lemmas_C` | 27.4 | CRefine |
| `CRefine.CLevityCatch` | 26.4 | CRefine |
| `CRefine.Detype_C` | 25.6 | CRefine |
| `CRefine.DetWP` | 24.0 | CRefine |
| `CRefine.Interrupt_C` | 22.2 | CRefine |
| `CRefine.Schedule_C` | 20.9 | CRefine |
| `CRefine.AutoCorres_C` | 19.9 | CRefine |
| `CRefine.CSpaceAcc_C` | 19.2 | CRefine |
| `CRefine.TcbAcc_C` | 18.7 | CRefine |
| `CRefine.Ctac` | 18.0 | CRefine |
| `InfoFlowC.ArchADT_IF_Refine_C` | 15.9 | InfoFlowC |
| `CRefine.IpcCancel_C` | 15.6 | CRefine |
| `CRefine.CSpace_RAB_C` | 14.8 | CRefine |
| `CRefine.Ctac_lemmas_C` | 13.1 | CRefine |
| `CRefine.CSpace_All` | 10.7 | CRefine |
| `CRefine.StoreWord_C` | 10.1 | CRefine |
| `CRefine.TcbQueue_C` | 8.0 | CRefine |
| `CRefine.Boolean_C` | 2.2 | CRefine |
| `CRefineSyscall.Intermediate_C` | 0.9 | CRefineSyscall |
| `CRefine.Init_C` | 0.3 | CRefine |
| `AutoCorresCRefine.AutoCorresTest` | 0.0 | AutoCorresCRefine |
| `CRefine.Refine_nondet_C` | 0.0 | CRefine |

## Misalignment list (12 candidates)

True candidates (~180s wall) — moveable to Haskell-axis sessions:

| Theory | Current session | Wall (s) | File |
|---|---|---:|---|
| `CRefine.IsolatedThreadAction` | CRefine | 36.1 | `proof/crefine/ARM/IsolatedThreadAction.thy` |
| `InfoFlowC.Example_Valid_StateH` | InfoFlowC | 35.7 | `proof/infoflow/refine/ARM/Example_Valid_StateH.thy` |
| `CRefine.Fastpath_Equiv` | CRefine | 32.8 | `proof/crefine/ARM/Fastpath_Equiv.thy` |
| `InfoFlowC.ADT_IF_Refine` | InfoFlowC | 21.3 | `proof/infoflow/refine/ADT_IF_Refine.thy` |
| `CRefine.Fastpath_Defs` | CRefine | 20.8 | `proof/crefine/ARM/Fastpath_Defs.thy` |
| `CRefine.ArchMove_C` | CRefine | 19.6 | `proof/crefine/ARM/ArchMove_C.thy` |
| `InfoFlowC.ArchADT_IF_Refine` | InfoFlowC | 9.9 | `proof/infoflow/refine/ARM/ArchADT_IF_Refine.thy` |
| `CRefine.CToCRefine` | CRefine | 7.2 | `proof/crefine/lib/CToCRefine.thy` |
| `CRefine.Move_C` | CRefine | 6.3 | `proof/crefine/Move_C.thy` |

Likely false positives (~2280s, but ML-level deps not tracked):

| Theory | Current session | Wall (s) | Why suspicious |
|---|---|---:|---|
| `SimplExportAndRefine.SEL4GraphRefine` | SimplExportAndRefine | 2260.3 | huge ML chunk processes C semantics |
| `SimplExportAndRefine.SEL4GlobalsSwap` | SimplExportAndRefine | 11.9 | likely same family |
| `SimplExportAndRefine.TestGraphRefine` | SimplExportAndRefine | 0.0 | likely same family |

## Spec-side back-coupling list (49 entries, the target (a) focus)

These spec theories transitively depend on haskell content through one
of the 9 direct edges listed above. After target (a) is implemented,
this set should shrink dramatically.

Top entries by wall:

| Theory | Wall (s) | Path |
|---|---:|---|
| `CKernel.Kernel_C` | 706.8 | `/spec/cspec/ARM/Kernel_C.thy` (note: namespace CKernel, file in /spec/cspec/) |
| `CSpec.Substitute` | 84.8 | `/spec/cspec/Substitute.thy` |
| `ASpec.Structures_A` | 20.5 | `/spec/abstract/Structures_A.thy` |
| `ASpec.Invocations_A` | 16.7 | `/spec/abstract/Invocations_A.thy` |
| `ASpec.Arch_Structs_A` | 15.2 | `/spec/abstract/ARM/Arch_Structs_A.thy` |
| `ASpec.ArchInvocation_A` | 11.5 | `/spec/abstract/ARM/ArchInvocation_A.thy` |
| `ASpec.CSpace_A` | 11.3 | `/spec/abstract/CSpace_A.thy` |
| `ASpec.Syscall_A` | 7.8 | `/spec/abstract/Syscall_A.thy` |
| `ASpec.Deterministic_A` | 5.5 | `/spec/abstract/Deterministic_A.thy` |
| `CSpec.KernelState_C` | 5.4 | `/spec/cspec/KernelState_C.thy` |
| _(40 more — see [theory-axis-2d.json](theory-axis-2d.json) for full list)_ | | |

## Static-dependency reach matrix (NOT a true change-locality measure)

> **⚠ Methodological correction 2026-05-15.** An earlier version of this
> section was titled "Change-locality matrix" and claimed it answered
> "which sessions rebuild when pillar X changes". **That framing was
> wrong.** Per maintainer feedback (Klein, devel@sel4.systems thread),
> Isabelle uses **per-theory content-hash invalidation**, not session-
> level "if dep_set contains X, rebuild." A Haskell change to a file
> whose regenerated `.thy` is NOT in a session's actual imports leaves
> that session's content hash unchanged → no rebuild.
>
> The numbers below count **sessions whose static dep set touches the
> pillar**. That is a strict **upper bound** on rebuild scope (the
> "worst case if you happened to change the most-imported theory") —
> NOT a typical-case rebuild estimate. For real change-locality, what
> matters is which SPECIFIC theory in pillar X you edit, and which
> downstream theories transitively import its regenerated form.

| Pillar change (static dep reach) | Sessions in reach | Σ own wall (s) |
|---|---:|---:|
| `spec` | 34 | 12045 |
| `haskell` | 33 | 12045 |
| `c` | 10 | 7085 |
| `proof` | 24 | 9874 |

The near-identical `spec` and `haskell` rows reflect that almost all
sessions have BOTH spec and haskell content in their static dep set.
Whether either pillar's edits actually trigger any of those rebuilds
depends on **content-hash gating**, not on this table.

## How to regenerate

```bash
python3 tools/critical_path/theory_axis_2d.py
# produces reports/theory-axis-2d.{md,json}
```

Cross-check against this file's numbers; diff indicates either drift in
inputs or an effective refactor.
