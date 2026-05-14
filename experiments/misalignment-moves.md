# Misalignment Moves — 8 Haskell-axis theories out of cross-cut sessions

Date: 2026-05-14
Branch: `cstr-2graph`
Driven by: [reports/theory-axis-2d.md](../reports/theory-axis-2d.md) misalignment list

## Goal

Move 8 theories whose import closure does NOT touch the C pillar out of
their current cross-cut sessions (CRefine / InfoFlowC) into the
Haskell-axis sessions (Refine / InfoFlow). These theories pay CSTR
amplification today by living in CRefine/InfoFlowC's heavy ML state;
after the move they run in lighter Haskell-axis context.

Note from upstream: `Move_C.thy`'s own header comment reads
> "Arch generic lemmas that should be moved into theory files before CRefine"
— so this aligns with what the seL4 authors already intended.

## Scope

**ARM only.** The same theories exist as per-arch copies in `ARM_HYP/`,
`AARCH64/`, `RISCV64/`, `X64/`. We move only the ARM variants and update
ARM-side importers; other archs continue to use their existing copies
in `proof/crefine/<arch>/`.

## The 8 moves

### CRefine → Refine (5 theories, 115.6s wall)

| Source | Destination |
|---|---|
| `proof/crefine/ARM/IsolatedThreadAction.thy` | `proof/refine/ARM/IsolatedThreadAction.thy` |
| `proof/crefine/ARM/Fastpath_Equiv.thy`       | `proof/refine/ARM/Fastpath_Equiv.thy` |
| `proof/crefine/ARM/Fastpath_Defs.thy`        | `proof/refine/ARM/Fastpath_Defs.thy` |
| `proof/crefine/ARM/ArchMove_C.thy`           | `proof/refine/ARM/ArchMove_C.thy` |
| `proof/crefine/Move_C.thy`                   | `proof/refine/Move_C.thy` |

### InfoFlowC → InfoFlow (3 theories, 66.9s wall)

| Source | Destination |
|---|---|
| `proof/infoflow/refine/ADT_IF_Refine.thy`            | `proof/infoflow/ADT_IF_Refine.thy` |
| `proof/infoflow/refine/ARM/ArchADT_IF_Refine.thy`    | `proof/infoflow/ARM/ArchADT_IF_Refine.thy` |
| `proof/infoflow/refine/ARM/Example_Valid_StateH.thy` | `proof/infoflow/ARM/Example_Valid_StateH.thy` |

## External importers to update (ARM only)

After the moves, these ARM-side files lose access to the moved theories
via their bare-name imports. They need fully-qualified imports.

| File | Old import | New import |
|---|---|---|
| `proof/crefine/ARM/Ipc_C.thy` | `IsolatedThreadAction` | `Refine.IsolatedThreadAction` |
| `proof/crefine/ARM/Refine_C.thy` | `Fastpath_Equiv` | `Refine.Fastpath_Equiv` |
| `proof/crefine/ARM/Fastpath_C.thy` | `Fastpath_Defs` | `Refine.Fastpath_Defs` |
| `proof/crefine/ARM/CLevityCatch.thy` | `ArchMove_C` | `Refine.ArchMove_C` |
| `proof/infoflow/refine/ADT_IF_Refine_C.thy` | `ArchADT_IF_Refine` | `InfoFlow.ArchADT_IF_Refine` |

## ROOT changes

- `proof/ROOT` — Refine session: add 5 theories to its `theories` clause
  so the session builds them as entry points (no existing Refine theory
  imports them; they'd otherwise be orphan files).
- `proof/ROOT` — InfoFlow session: add 3 theories to its `theories` clause
  similarly.
- `proof/ROOT` — InfoFlowC session: remove `"Example_Valid_StateH"` from
  its theories clause (the file moved out of InfoFlowC's directories).

## Internal imports within the moving set

These DO NOT need updating because all moving theories end up in the same
target session. Bare names resolve within the new session's search path:

- IsolatedThreadAction imports `ArchMove_C` → both in Refine
- ArchMove_C imports `Move_C` → both in Refine
- Fastpath_Defs imports `ArchMove_C` → both in Refine
- Fastpath_Equiv imports `Fastpath_Defs`, `IsolatedThreadAction`, `Refine.RAB_FN` → first two same-session; last is FQ
- ADT_IF_Refine imports `InfoFlow.ArchADT_IF`, `Refine.EmptyFail_H` → already FQ
- ArchADT_IF_Refine imports `ADT_IF_Refine` → same session post-move
- Example_Valid_StateH imports `InfoFlow.Example_Valid_State`, `ArchADT_IF_Refine` → first FQ, second same-session

## Validation plan

1. `isabelle build -n` dry-run on Refine + InfoFlow + InfoFlowCBase + CRefine
2. Full build of Refine
3. Full build of InfoFlow
4. Full build of InfoFlowC + CRefine to confirm downstream still works
5. Measure: per-session wall before/after to confirm CSTR-amplification
   differential

## Reversibility

The moves use `git mv` (preserves history) and edit a small set of files
(~8 file moves + 5 importer edits + 3 ROOT edits). A `git checkout`
restores cleanly if validation fails.

## Risk caveats

The DAG-based misalignment detection only tracks `imports` declarations.
If any of the 8 theories uses Isar-level features (locale instances,
ML setup, `crunch`-generated facts) that are only loaded in the original
cross-cut session's ML state, the move can break compilation even though
the imports look clean. This is what the Isabelle build validation is
for. If any of the 8 fails, we revert that specific one and document why.

---

## Execution log

(to be filled in as moves happen)
