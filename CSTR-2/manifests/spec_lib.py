"""
Change type: spec_lib (a sub-type of "spec change")

A change to shared utility lemmas / spec primitives:
    lib/**/*.thy

Lib is foundational infrastructure (Word_Lib, Monads, Lib, CorresK,
SepTactics, etc.). Almost every l4v session imports something from lib/
either directly or transitively. Therefore this is the WORST-CASE
"spec change" — its closure naturally spans nearly the entire DAG.

We keep it as a separate sub-type (rather than rolling into spec_abstract)
because:
  - It's structurally distinct (non-spec content; it's tactics + utility)
  - Treating it as the same as spec/abstract/ would over-estimate the
    closure of pure abstract-spec changes
  - The optimization strategy differs: lib/ changes are tested via
    fast incremental rebuild + LibTest session, NOT full proof rebuild
"""
MANIFEST = {
    "name": "spec_lib",
    "description": (
        "changes to shared utility theories (lib/**/*.thy); foundational "
        "infrastructure with the widest possible closure"
    ),
    "entry_paths": [
        "verification/l4v/lib/**/*.thy",
    ],
    "entry_session_hints": [
        "Lib",
        "Word_Lib",
        "Eisbach_Tools",
        "CorresK",
    ],
    "generated_edges": [],
    "affected_layer_sessions": [],
    "closure_excludes_unless_in_path": [
        # Even for lib/ changes, leave camkes / sysinit out unless
        # explicitly touched.
        "CamkesGlueProofs",
        "CamkesAdlSpec",
    ],
}
