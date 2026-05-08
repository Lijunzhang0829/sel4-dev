"""
Change-type manifests for the DAG critical-path analysis.

Each manifest module exports a top-level dict `MANIFEST` describing:

    entry_paths            list of glob patterns (relative to repo root) that
                           count as a "change" of this type — e.g.,
                           ["verification/l4v/proof/**/*.thy"]

    entry_session_hints    list of session names that are *guaranteed* to be
                           in the rebuild closure for this change type, even
                           when the entry doesn't lie under a .thy file (e.g.,
                           Haskell sources triggering regen of spec/design)

    generated_edges        list of dicts describing cross-toolchain edges that
                           are NOT visible from theory imports alone:
                             {"from_pattern": glob,
                              "to_session":   str,
                              "rationale":    str}
                           These are used to expand the closure when the
                           entry is on the from-side of a regen relationship.

    affected_layer_sessions    sessions that, while not directly imported by
                               the entry, get re-run because they sit on the
                               refinement layer downstream of the change.

The four manifests:

    proof.py     — changes to proof/*.thy  (the proof layer itself)
    spec.py      — changes to spec/abstract/, spec/invariant-abstract/, etc.
                   (the abstract/exec spec, but NOT the auto-regenerated
                   design.thy from Haskell — that's haskell.py's territory)
    haskell.py   — changes to spec/haskell/*.hs (Haskell prototype),
                   triggering spec/design/*.thy regeneration
    c.py         — changes to verification/seL4/src/**/*.{c,h},
                   triggering C-parser regeneration of
                   spec/cspec/c/build/ARM/*.thy

Manifests are read by tools/critical_path/dag.py.
"""
