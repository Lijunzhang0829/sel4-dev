"""
Change type: c

A change to seL4's C source (verification/seL4/src/**/*.{c,h}) that flows
into the C parser → SIMPL pipeline.

Pipeline:
    verification/seL4/src/**/*.c
        ↓ preprocessor → kernel_all.c_pp
    spec/cspec/c/build/ARM/kernel_all.c_pp
        ↓ C parser
    spec/cspec/c/build/ARM/*.thy   (generated; CSpec session loads these)
        ↓
    CKernel session                (compiled C semantics)
        ↓
    CBaseRefine / CRefine / CRefineSyscall

Plus the asm-refinement chain is also affected:
    spec/cspec/c → SimplExport → SimplExportAndRefine

Per P0.5: CSpec/CKernel are 0%/11% duplication, so the C-change closure has
relatively MORE actual work in the closure than proof/spec/haskell changes
would suggest from raw timings. SimplExport (646s) + SimplExportAndRefine
(2264s) take big absolute hits if asm-refinement is part of the workflow.
"""
MANIFEST = {
    "name": "c",
    "description": (
        "changes to seL4 kernel C source; regenerates "
        "spec/cspec/c/build/ARM/*.thy → triggers CSpec / CKernel / "
        "C* refinement / asm-refinement"
    ),
    "entry_paths": [
        "verification/seL4/src/**/*.c",
        "verification/seL4/src/**/*.h",
        "verification/seL4/include/**/*.h",
    ],
    "entry_session_hints": [
        "CSpec",
        "CKernel",
    ],
    "generated_edges": [
        {
            "from_pattern": "verification/seL4/src/**/*.{c,h}",
            "to_session": "CSpec",
            "rationale": (
                "seL4 C source preprocesses + parses into "
                "spec/cspec/c/build/ARM/*.thy, owned by CSpec session."
            ),
        },
        {
            "from_pattern": "verification/seL4/src/**/*.{c,h}",
            "to_session": "CKernel",
            "rationale": "CKernel session loads the parsed C theories.",
        },
    ],
    "affected_layer_sessions": [
        "CBaseRefine",
        "CRefine",
        "CRefineSyscall",
        "InfoFlowCBase",
        "InfoFlowC",
        # asm-refinement chain
        "SimplExport",
        "SimplExportAndRefine",
    ],
    "closure_excludes_unless_in_path": [
        # spec/proof/haskell-only sessions
        "ASpec",
        "AInvs",
        "Access",
        "BaseRefine",
        "Refine",
        "InfoFlow",
        "DSpec",
        "DBaseRefine",
        "DRefine",
        "DPolicy",
        "SepDSpec",
        "DSpecProofs",
        "Bisim",
    ],
}
