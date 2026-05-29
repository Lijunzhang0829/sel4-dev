---
name: isabelle-prover-haskell
description: "Optimize the Haskell Design Spec under spec/haskell/ and propagate through the refine/crefine proof chain. Use when changing .hs/.lhs algorithms in the kernel design spec."
---

# Haskell type — design-spec optimization

Optimize Haskell Design Spec algorithms (`spec/haskell/**`) while keeping the
downstream verification chain green.

Inherits common contract from `isabelle_prover` (3 hard rules, common tools,
patch format, session mapping). Wall-drop threshold for this type is set
per-change (no universal default — depends on whether the change is for
correctness, clarity, or performance).

## Pipeline

```
spec/haskell/**.hs   ──edit──▶  re-translate  ──▶  spec/design/**.thy
                                                       │
                                                       ▼
                                                 Refine proofs
                                                 (proof/refine/**/*.thy)
                                                       │
                                                       ▼
                                                 CRefine proofs
                                                 (proof/crefine/**/*.thy)
```

A Haskell change has **3-tier downstream verification cost**:

1. **Translation step** — Haskell → Isabelle (mechanical, via `make exec`)
2. **Refine session** — Abstract ↔ Design refinement proofs
3. **CRefine session** — Design ↔ C refinement proofs

You must keep all three green.

## Status: minimal scope

This sub-skill currently does not have a full automated workflow tool
(unlike `isabelle_prover_proof`'s `lemma_profile.py`). Treat changes as
**manual** — edit, retranslate, run each session, fix what breaks.

Read [`references/strengthen-guide.md`](../isabelle_prover/references/strengthen-guide.md)
for the type-by-type workflow.

## Key commands

| Step | Command |
|---|---|
| Re-translate Haskell → design spec | `make -C verification/l4v/spec/haskell exec` (verify against current build setup) |
| Verify a refine proof file | `bash $ISA_SCRIPTS/check-theory.sh <file> Refine` |
| Verify a crefine proof file | `bash $ISA_SCRIPTS/check-theory.sh <file> CRefine` |

## TODO

- [ ] Add a `translate-and-verify.sh` wrapper that detects which proof
  files import the changed design spec module and runs `check-theory.sh`
  on each
- [ ] Document representative algorithm-change patterns (data structure
  swap, loop unroll, …) with the typical proof-chain impact
- [ ] Cross-link with `isabelle_prover_c` since C changes often pair with
  Haskell changes
