"""
Change type: spec_cspec (a sub-type of "spec change")

A change to the C-side abstract spec:
    spec/cspec/**/*.thy   (excluding generated/ subtree)

This covers hand-written .thy files in the CSpec session that are NOT
auto-generated from C source. (Generated theories — kernel_all.c_pp,
structures_defs, shared_types, etc. — live in c.py's territory because
they regenerate from .c/.h.)

Closure scope: CSpec, then CKernel (which imports CSpec), then the
C-side refinement chain (CBaseRefine, CRefine, CRefineSyscall) and
asm-refinement (SimplExport, SimplExportAndRefine).

Importantly, this should NOT propagate to ASpec / AInvs / Access
/ InfoFlow — those are on the abstract side.
"""
MANIFEST = {
    "name": "spec_cspec",
    "description": (
        "changes to hand-written C-side abstract spec "
        "(spec/cspec/**/*.thy, excluding c/build/ARM/generated/)"
    ),
    "entry_paths": [
        "verification/l4v/spec/cspec/**/*.thy",
    ],
    "entry_session_hints": [
        "CSpec",
    ],
    "generated_edges": [],
    "affected_layer_sessions": [
        "CKernel",
        "CBaseRefine",
        "CRefine",
        "CRefineSyscall",
        "SimplExport",
        "SimplExportAndRefine",
    ],
    "closure_excludes_unless_in_path": [
        # capDL / camkes are NOT downstream of cspec changes
        "CamkesGlueProofs",
        "CamkesAdlSpec",
        "CamkesCdlBase",
        "CamkesCdlRefine",
    ],
}
