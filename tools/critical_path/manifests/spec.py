"""
Change type: spec

A change to the abstract / executable / invariant spec at the Isabelle level
(NOT auto-regenerated from Haskell — that's `haskell` manifest territory).

Entry paths cover:
  - spec/abstract/**     (abstract spec ASpec session)
  - spec/sep-abstract/** (separation logic abstract spec)
  - spec/cspec/**        (C spec, mostly the .thy parts that aren't
                          regenerated from C source — those are in `c.py`)
  - proof/invariant-abstract/**  (invariant proofs that ARE the spec layer
                                  for AInvs session)
  - lib/**               (utility lemmas / spec primitives — heavy fan-out)

A spec change doesn't have generated edges (it's already at .thy level), but
its closure is defined by who consumes spec output.
"""
MANIFEST = {
    "name": "spec",
    "description": (
        "changes to abstract/exec spec or invariant-abstract proofs "
        "(spec/abstract/, spec/sep-abstract/, spec/cspec/ non-generated, "
        "proof/invariant-abstract/, lib/)"
    ),
    "entry_paths": [
        "verification/l4v/spec/abstract/**/*.thy",
        "verification/l4v/spec/sep-abstract/**/*.thy",
        "verification/l4v/spec/cspec/**/*.thy",
        "verification/l4v/proof/invariant-abstract/**/*.thy",
        "verification/l4v/lib/**/*.thy",
    ],
    "entry_session_hints": [
        # Sessions that are *unambiguously* in the closure of any spec change,
        # regardless of which entry file was touched, because spec changes
        # ripple through every refinement layer that imports the spec.
        "ASpec",
        "AInvs",
    ],
    "generated_edges": [],
    "affected_layer_sessions": [
        # Sessions on the refinement chain downstream of spec
        "Access",
        "BaseRefine",
        "Refine",
        "CBaseRefine",
        "CRefine",
        "CRefineSyscall",
        "InfoFlow",
        "InfoFlowCBase",
        "InfoFlowC",
        "DBaseRefine",
        "DRefine",
        "DPolicy",
        "SepDSpec",
        "DSpecProofs",
        "Bisim",
    ],
    "closure_excludes_unless_in_path": [
        # asm refinement is on its own chain (CSpec → SimplExport →
        # SimplExportAndRefine), only enter closure if entry touched
        # spec/cspec or proof/asmrefine specifically.
        "SimplExportAndRefine",
        "AutoCorresCRefine",
        "AutoCorresQuickstart",
        "AutoCorresSEL4",
        "AsmRefineTest",
        "CamkesGlueProofs",
        "CamkesAdlSpec",
    ],
}
