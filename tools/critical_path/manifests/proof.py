"""
Change type: proof-only

A change to a .thy file in proof/ — most direct refinement-of-proof workflow.
The closure is computed from the theory imports DAG: the changed thy + every
theory transitively importing it (across session boundaries, due to CSTR
finding that downstream sessions reprocess parent theories).

No generated_edges: proof changes don't trigger regeneration of any other
artifact.
"""
MANIFEST = {
    "name": "proof",
    "description": "proof-only changes (proof/**/*.thy)",
    "entry_paths": [
        "verification/l4v/proof/**/*.thy",
    ],
    "entry_session_hints": [],
    "generated_edges": [],
    "affected_layer_sessions": [
        # The "if you change anything in proof, these sessions can be in the
        # closure even without a direct import path" set. For proof-only this
        # is empty since the import DAG handles it.
    ],
    # Sessions that DO NOT need to be in the closure regardless of which proof
    # thy was changed (e.g., asm refinement is a separate branch — only fires
    # if the changed thy is under proof/asmrefine/*).
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
