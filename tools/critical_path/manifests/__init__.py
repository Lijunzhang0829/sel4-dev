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

The seven manifests:

    proof.py            changes to proof/**/*.thy (the proof layer itself)
    spec_abstract.py    changes to spec/abstract/**/*.thy (MA layer)
    spec_invariant.py   changes to proof/invariant-abstract/**/*.thy (AInvs)
    spec_cspec.py       changes to spec/cspec/**/*.thy (CSpec, hand-written)
    spec_lib.py         changes to lib/**/*.thy (foundational utility)
    haskell.py          changes to spec/haskell/**/*.{hs,lhs}; triggers
                        spec/design/*.thy regen (ExecSpec)
    c.py                changes to verification/seL4/src/**/*.{c,h};
                        triggers spec/cspec/c/build/ARM/*.thy regen

Note on the 4-way `spec_*` split:
    The original `spec.py` lumped all of spec/abstract, spec/cspec, lib,
    and proof/invariant-abstract together. This produced a 1012-theory
    closure (~92% of the DAG) which obscured the much narrower closure
    of a pure abstract-spec change. The split lets us report what each
    sub-type actually invalidates.

Manifests are read by tools/critical_path/dag.py.
"""
