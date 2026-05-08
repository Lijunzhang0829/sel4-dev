"""
Change type: haskell

A change to the Haskell prototype (spec/haskell/**/*.hs / *.lhs).

Per SOSP'09 sect. 2.2, the Haskell prototype is the central artefact in the
seL4 design process. Changes propagate through:

    spec/haskell/SEL4/**.hs    (source)
        ↓  regenerated via spec/design/skel/ pipeline
    spec/design/**.thy         (executable spec — Isabelle)
        ↓  imported by
    proof/refine/**            (Refine session)
        ↓  imported by (via heap or duplicated processing — see P0.5)
    proof/crefine/**           (CBaseRefine, CRefine, CRefineSyscall)

Generated edge model: any change in spec/haskell/SEL4/Foo.hs may regenerate
spec/design/SEL4/Foo.thy (and possibly siblings). For closure we conservatively
treat ALL of spec/design/ as potentially changed.
"""
MANIFEST = {
    "name": "haskell",
    "description": (
        "changes to Haskell prototype (spec/haskell/**/*.hs); regenerates "
        "spec/design/**/*.thy → triggers ExecSpec / Refine / C* refinement"
    ),
    "entry_paths": [
        "verification/l4v/spec/haskell/**/*.hs",
        "verification/l4v/spec/haskell/**/*.lhs",
    ],
    "entry_session_hints": [
        # Sessions that contain regenerated theories — guaranteed in closure.
        "ExecSpec",
    ],
    "generated_edges": [
        {
            "from_pattern": "verification/l4v/spec/haskell/**/*.{hs,lhs}",
            "to_session": "ExecSpec",
            "rationale": (
                "spec/haskell/SEL4/Foo.hs → spec/design/SEL4/Foo.thy via "
                "spec/design/skel/ regeneration pipeline; ExecSpec session "
                "wraps spec/design/."
            ),
        },
        {
            "from_pattern": "verification/l4v/spec/haskell/**/*.{hs,lhs}",
            "to_session": "Refine",
            "rationale": (
                "Refine imports executable spec; any regen invalidates Refine."
            ),
        },
    ],
    "affected_layer_sessions": [
        "Refine",
        "BaseRefine",
        "CBaseRefine",
        "CRefine",
        "CRefineSyscall",
        # InfoFlow* sit on Refine
        "InfoFlowCBase",
        "InfoFlowC",
        # capDL refinement also depends on the executable spec via DSpec
        "DSpec",
        "DBaseRefine",
        "DRefine",
        "DPolicy",
        "SepDSpec",
        "DSpecProofs",
    ],
    "closure_excludes_unless_in_path": [
        # asm refinement chain operates on C, not Haskell
        "SimplExport",
        "SimplExportAndRefine",
        "AutoCorresCRefine",
        # camkes / sysinit are external products
        "CamkesGlueProofs",
        "CamkesAdlSpec",
    ],
}
