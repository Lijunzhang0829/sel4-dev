"""
Change type: spec_invariant (a sub-type of "spec change")

A change to invariant proofs over the abstract spec:
    proof/invariant-abstract/**/*.thy

These are theories in the AInvs session — they prove invariants like
"all kernel objects have valid types", "the idle thread is always idle",
etc. Per SOSP'09 §4.5, ~80% of refinement-proof effort goes to
maintaining these invariants.

Closure scope: AInvs itself, then everything downstream that imports
AInvs invariants — Refine (via parent), Access (parent), Bisim (parent),
DBaseRefine (parent), and anything that depends on those.

This is narrower than spec_abstract because we start AT the AInvs
layer, NOT at ASpec — so the ASpec→ExecSpec cross-cut does not apply.
"""
MANIFEST = {
    "name": "spec_invariant",
    "description": (
        "changes to invariant proofs over abstract spec "
        "(proof/invariant-abstract/**/*.thy)"
    ),
    "entry_paths": [
        "verification/l4v/proof/invariant-abstract/**/*.thy",
    ],
    "entry_session_hints": [
        "AInvs",
    ],
    "generated_edges": [],
    "affected_layer_sessions": [],
    "closure_excludes_unless_in_path": [
        "SimplExport",
        "SimplExportAndRefine",
        "AutoCorresCRefine",
        "AutoCorresQuickstart",
        "AutoCorresSEL4",
        "AsmRefineTest",
        "CamkesGlueProofs",
        "CamkesAdlSpec",
    ],
}
