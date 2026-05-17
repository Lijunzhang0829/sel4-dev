# External .thy import rewrites

Every external .thy that references a merge-group theory
by qualified name (`"Refine.X"` etc.) must change to
`"HaskellMega.X"`.

Affected files: 38

### `verification/l4v/sys-init/InitCSpace_SI.thy` (session `SysInit`)
- `DSpecProofs.CNode_DP` → `HaskellMega.CNode_DP`

### `verification/l4v/sys-init/CreateObjects_SI.thy` (session `SysInit`)
- `DSpecProofs.Retype_DP` → `HaskellMega.Retype_DP`

### `verification/l4v/sys-init/SysInit_SI.thy` (session `SysInit`)
- `DSpecProofs.Kernel_DP` → `HaskellMega.Kernel_DP`

### `verification/l4v/sys-init/InitTCB_SI.thy` (session `SysInit`)
- `DSpecProofs.KHeap_DP` → `HaskellMega.KHeap_DP`
- `DSpecProofs.TCB_DP` → `HaskellMega.TCB_DP`

### `verification/l4v/sys-init/InitIRQ_SI.thy` (session `SysInit`)
- `DSpecProofs.IRQ_DP` → `HaskellMega.IRQ_DP`

### `verification/l4v/sys-init/DuplicateCaps_SI.thy` (session `SysInit`)
- `DSpecProofs.CNode_DP` → `HaskellMega.CNode_DP`

### `verification/l4v/sys-init/InitVSpace_SI.thy` (session `SysInit`)
- `DSpecProofs.Invocation_DP` → `HaskellMega.Invocation_DP`
- `DSpecProofs.Arch_DP` → `HaskellMega.Arch_DP`

### `verification/l4v/sys-init/CreateIRQCaps_SI.thy` (session `SysInit`)
- `DSpecProofs.IRQ_DP` → `HaskellMega.IRQ_DP`

### `verification/l4v/sys-init/WellFormed_SI.thy` (session `SysInit`)
- `DSpecProofs.Kernel_DP` → `HaskellMega.Kernel_DP`
- `SepDSpec.Separation_SD` → `HaskellMega.Separation_SD`

### `verification/l4v/camkes/cdl-refine/Policy_CAMKES_CDL.thy` (session `CamkesCdlRefine`)
- `Access.Access` → `HaskellMega.Access`

### `verification/l4v/camkes/cdl-refine/Eval_CAMKES_CDL.thy` (session `CamkesCdlRefine`)
- `DPolicy.Dpolicy` → `HaskellMega.Dpolicy`

### `verification/l4v/camkes/adl-spec/Types_CAMKES.thy` (session `CamkesAdlSpec`)
- `Access.Access` → `HaskellMega.Access`

### `verification/l4v/proof/infoflow/refine/ADT_IF_Refine.thy` (session `InfoFlowC`)
- `InfoFlow.ArchADT_IF` → `HaskellMega.ArchADT_IF`
- `Refine.EmptyFail_H` → `HaskellMega.EmptyFail_H`

### `verification/l4v/proof/infoflow/refine/Noninterference_Refinement.thy` (session `InfoFlowC`)
- `InfoFlow.ArchNoninterference` → `HaskellMega.ArchNoninterference`
- `InfoFlow.Noninterference_Base_Refinement` → `HaskellMega.Noninterference_Base_Refinement`

### `verification/l4v/proof/infoflow/refine/RISCV64/Example_Valid_StateH.thy` (session `InfoFlowC`)
- `InfoFlow.Example_Valid_State` → `HaskellMega.Example_Valid_State`

### `verification/l4v/proof/infoflow/refine/ARM/Example_Valid_StateH.thy` (session `InfoFlowC`)
- `InfoFlow.Example_Valid_State` → `HaskellMega.Example_Valid_State`

### `verification/l4v/proof/infoflow/refine/base/Include_IF_C.thy` (session `InfoFlowCBase`)
- `InfoFlow.ArchNoninterference` → `HaskellMega.ArchNoninterference`
- `InfoFlow.Noninterference_Base_Refinement` → `HaskellMega.Noninterference_Base_Refinement`
- `InfoFlow.Example_Valid_State` → `HaskellMega.Example_Valid_State`

### `verification/l4v/proof/crefine/RISCV64/CSpaceAcc_C.thy` (session `CRefine`)
- `Refine.EmptyFail` → `HaskellMega.EmptyFail`

### `verification/l4v/proof/crefine/RISCV64/SR_lemmas_C.thy` (session `CRefine`)
- `Refine.Invariants_H` → `HaskellMega.Invariants_H`

### `verification/l4v/proof/crefine/RISCV64/Refine_C.thy` (session `CRefine`)
- `Refine.RAB_FN` → `HaskellMega.RAB_FN`

### `verification/l4v/proof/crefine/X64/CSpaceAcc_C.thy` (session `CRefine`)
- `Refine.EmptyFail` → `HaskellMega.EmptyFail`

### `verification/l4v/proof/crefine/X64/SR_lemmas_C.thy` (session `CRefine`)
- `Refine.Invariants_H` → `HaskellMega.Invariants_H`

### `verification/l4v/proof/crefine/X64/Refine_C.thy` (session `CRefine`)
- `Refine.RAB_FN` → `HaskellMega.RAB_FN`

### `verification/l4v/proof/crefine/ARM/CSpaceAcc_C.thy` (session `CRefine`)
- `Refine.EmptyFail` → `HaskellMega.EmptyFail`

### `verification/l4v/proof/crefine/ARM/SR_lemmas_C.thy` (session `CRefine`)
- `Refine.Invariants_H` → `HaskellMega.Invariants_H`

### `verification/l4v/proof/crefine/ARM/Fastpath_C.thy` (session `CRefine`)
- `Refine.Fastpath_Defs` → `HaskellMega.Fastpath_Defs`

### `verification/l4v/proof/crefine/ARM/Ipc_C.thy` (session `CRefine`)
- `Refine.IsolatedThreadAction` → `HaskellMega.IsolatedThreadAction`

### `verification/l4v/proof/crefine/ARM/CLevityCatch.thy` (session `CRefine`)
- `Refine.ArchMove_C` → `HaskellMega.ArchMove_C`

### `verification/l4v/proof/crefine/ARM/Refine_C.thy` (session `CRefine`)
- `Refine.Fastpath_Equiv` → `HaskellMega.Fastpath_Equiv`

### `verification/l4v/proof/crefine/ARM_HYP/CSpaceAcc_C.thy` (session `CRefine`)
- `Refine.EmptyFail` → `HaskellMega.EmptyFail`

### `verification/l4v/proof/crefine/ARM_HYP/SR_lemmas_C.thy` (session `CRefine`)
- `Refine.Invariants_H` → `HaskellMega.Invariants_H`

### `verification/l4v/proof/crefine/ARM_HYP/Fastpath_Equiv.thy` (session `CRefine`)
- `Refine.RAB_FN` → `HaskellMega.RAB_FN`

### `verification/l4v/proof/crefine/base/Include_C.thy` (session `CBaseRefine`)
- `Refine.Refine` → `HaskellMega.Refine`

### `verification/l4v/proof/crefine/AARCH64/CSpaceAcc_C.thy` (session `CRefine`)
- `Refine.EmptyFail` → `HaskellMega.EmptyFail`

### `verification/l4v/proof/crefine/AARCH64/SR_lemmas_C.thy` (session `CRefine`)
- `Refine.Invariants_H` → `HaskellMega.Invariants_H`

### `verification/l4v/proof/crefine/AARCH64/Fastpath_Equiv.thy` (session `CRefine`)
- `Refine.RAB_FN` → `HaskellMega.RAB_FN`

### `verification/l4v/lib/test/WPTutorial.thy` (session `LibTest`)
- `Refine.Bits_R` → `HaskellMega.Bits_R`

### `verification/l4v/lib/test/CorresK_Test.thy` (session `LibTest`)
- `Refine.VSpace_R` → `HaskellMega.VSpace_R`
