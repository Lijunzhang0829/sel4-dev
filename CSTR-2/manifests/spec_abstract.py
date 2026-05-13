"""
Change type: spec_abstract (a sub-type of "spec change")

A change to the abstract specification (MA in SOSP'09 Fig. 2):
    spec/abstract/**/*.thy

Logical scope per the paper: only Theorem 1 (ME refines MA) and any
proof depending on MA's lemmas (AInvs, Refine) should need rebuild.

Real build scope is wider for two reasons:
  (1) CSTR — Refine theories are reprocessed by CBaseRefine due to
      `sessions Refine` ROOT pattern, so changes to MA → AInvs → Refine
      cascade into CBaseRefine + CRefine + CRefineSyscall.
  (2) ASpec→ExecSpec cross-cut — `spec/ROOT` declares
      `session ASpec ... sessions ExecSpec`, which makes ASpec depend
      on the executable-spec namespace. Combined with reverse traversal,
      this makes the BFS visit ExecSpec, CKernel, CSpec etc. when
      starting from ASpec. See critical-path-summary.md "Structural
      findings" for the recommended fix.

We let BFS expand naturally and report what it finds, but the per-type
notes call out the sources of width.
"""
MANIFEST = {
    "name": "spec_abstract",
    "description": (
        "changes to abstract spec (spec/abstract/**/*.thy); the MA layer "
        "in SOSP'09 Fig. 2"
    ),
    "entry_paths": [
        "verification/l4v/spec/abstract/**/*.thy",
    ],
    "entry_session_hints": [
        "ASpec",
    ],
    "generated_edges": [],
    "affected_layer_sessions": [
        # AInvs proves invariants on MA; without it Refine cannot complete.
        "AInvs",
    ],
    "closure_excludes_unless_in_path": [
        # asm refinement is C-side only.
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
