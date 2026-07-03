---
name: isabelle-prover-c
description: "Optimize seL4 kernel C source while keeping CRefine green. Use when modifying C files in the kernel and the change must still refine the Haskell Design Spec."
---

# C type — kernel implementation optimization

Optimize seL4 kernel C source while keeping the CRefine session green.

Inherits common contract from `isabelle_prover` (3 hard rules, common tools,
patch format, session mapping).

## Pipeline

```
seL4/src/**.c, **.h   ──edit──▶  c-parser  ──▶  Substitute.thy (generated)
                                                       │
                                                       ▼
                                                 CRefine proofs
                                                 (proof/crefine/**/*.thy)
```

C can **diverge from Haskell** — it only needs to refine (behave
equivalently with respect to the abstract spec). So C changes go through:

1. **c-parser step** — C → Isabelle term (mechanical)
2. **CRefine session** — verify Design ↔ C refinement still holds

No Abstract / Refine impact unless the C change reveals a missing Design
spec invariant.

## Status: minimal scope

This sub-skill currently does not have a full automated workflow tool.
Treat changes as **manual** — edit, run c-parser, fix CRefine breakage.

Read [`references/strengthen-guide.md`](../isabelle_prover/references/strengthen-guide.md)
for the type-by-type workflow, and
[`references/autocorres-guide.md`](../isabelle_prover/references/autocorres-guide.md)
for how the C lifting works.

## Key commands

| Step | Command |
|---|---|
| Run c-parser on changed C source | (mechanism depends on build setup — verify against current Makefile) |
| Verify a crefine proof file | `bash $ISA_SCRIPTS/check-theory.sh <file> CRefine` |

## TODO

- [ ] Add a `c-impact.sh` that, given a changed C file, prints which
  CRefine .thy files mention the affected function and need re-verification
- [ ] Document patterns for keeping CRefine stable across C refactors
  (extract helper functions, reorder branches, add asserts, …)
- [ ] Cross-link with `isabelle_prover_haskell` for paired Haskell + C
  changes
