# Literature baseline — optimizing seL4 Haskell/C while preserving verification

Branch: `bridge-consumer-trace`. Date: 2026-06-15.
Source: two adversarially-verified deep-research passes (pass 1: 22 sources / 24
confirmed claims; pass 2: 23 sources / 25 confirmed claims). Each claim survived
3-vote adversarial verification. Confidence + caveats noted per item.

## Bottom line

The seL4 corpus documents a **mature, well-quantified methodology for
code-and-proof co-evolution**, and the literature **confirms our cost framing**.
On automation: proof-repair exists **only for Coq** (PUMPKIN, Sisyphus) or
**push-button SMT kernels** (Serval/Hyperkernel) — **none repair Isabelle/HOL
refinement proofs**. **No published work** applies ML/LLMs to optimize seL4 code
or to repair/regenerate its refinement proofs after Haskell/C changes. The gap is
real (honest caveat: absence-of-evidence, not an exhaustive negative sweep).

## Area 1 — Design methodology & cost (FILLED, high confidence)

- **Klein et al., SOSP'09 + ACM TOCS'14**; **Cock/Klein/Sewell, TPHOLs'08**:
  Haskell prototype → auto-translated Isabelle **design spec** mediating manual
  abstract spec ↔ hand-written C; two-step transitive refinement
  (abstract→design→C). **Haskell translation is NOT correctness-critical; the
  Haskell prototype is not in the proof chain.**
- **C chosen over auto-translated Haskell *specifically to preserve
  micro-optimization*** (TOCS'14 verbatim): auto-translation "would have
  simplified verification" but "would have lost most opportunities to
  micro-optimise the kernel, which we consider necessary for adequate microkernel
  performance." → primary-source backing for **"C is where optimization belongs."**
- **Cost**: kernel 2.2 py vs proof ~20 py; first refinement ~8 py (80% on
  invariants), second ~3 py. **Cost of change** (SOSP'09): adding reply caps
  changed <5% of code but cost **~1 py = 17% of total proof effort** to re-verify.
  → primary-source backing for **"bridge re-establishment dominates cost."**
- **Matichuk et al., ICSE'15**: quadratic statement→proof-size, linear
  effort→proof-size (15,018 lemmas / ~215k lines).
- **REFUTED (pass 1, 1-2 vote)**: do NOT claim the two-refinement structure
  "cleanly isolates optimizations to the C refinement with low design impact."
  Pure C changes hit only CRefine *by construction*, but real optimizations
  (fastpath) needed extra structure — isolation is not clean.

## Area 2 — IPC fastpath = the existing "optimize then re-verify" template (FILLED)

- **Klein et al. TOCS'14 + Blackham & Heiser APSys'12**: the fastpath is the
  canonical re-establish-after-optimization instance. Method: prove C fastpath
  implements an Isabelle fastpath spec, then a **"sideways" equivalence** proof
  that this spec is behaviourally equal to the normal IPC path ("same behaviour,
  just faster" — the natural correctness criterion for an optimization, no
  abstract-level fastpath spec). **Cost: ~5 person-months / 5913 lines, one
  expert.** C-only tuning gave **35% speedup**, parity with hand assembly;
  fastpath is in C *because the verification infra can only verify C, not asm*.
- → This 5-pm figure is the human price of one optimization's bridge rebuild —
  exactly what an LLM repair loop would aim to compress, and a ready-made pattern.

## Area 3 — C→binary translation validation: a THIRD re-establishment layer (FILLED, NEW)

This is the most decision-relevant *new* finding for `isabelle_prover_c`.

- **Sewell, Myreen, Klein, PLDI'13 + `seL4/graph-refine`**: C→binary correctness
  is **per-binary SMT translation validation** (Z3 + SONOLAR over a common CFG
  language, SydTV-GL), **not** verified compilation. The proof is for **one
  specific binary** → any C change that changes the binary forces **re-running the
  whole decompile-and-prove pipeline (~6–8 h on 2013 hw, mostly SMT).**
- **Optimization-sensitive & fragile**: full validation at **gcc -O1**, only
  **~98% at -O2** (loop unrolling / LICM / interprocedural opts break
  function-boundary correspondence); stack heuristic is **gcc-version-sensitive,
  manually re-tuned per gcc version**. **-O2 is seL4's standard setting; -O1
  costs 15–20% performance.** 2025 seL4 Summit: toolchain being rewritten
  (coliasgroup WIP), original still lacks **AArch64 and -O2**; "changes to seL4"
  and "GCC progress" are named live re-run drivers.
- → **Sharpens the C direction**: a C optimization must be (a) ccorres-repairable
  AND (b) within the translation-validation compiler envelope. Optimizations that
  *rely on aggressive -O2 compiler behaviour may not even be binary-validatable*.
  The "safe" C-optimization zone is narrower than CRefine alone implies.

## Area 4 — Proof-repair automation (FILLED for Coq; GAP for seL4/Isabelle)

- **PUMPKIN PATCH (Ringer et al., CPP'18)** + **PUMPKIN Pi (PLDI'21)**: Coq proof
  repair after type/definition change via transport across equivalences +
  decompile-to-tactics. **Coq only; not Isabelle, not refinement.** PUMPKIN Pi
  **names seL4 as the motivating exemplar** (>1M lines, 20+ py) but **does not
  apply to it** (8 Coq case studies). → the seL4 repair gap is *explicitly named
  but unfilled*.
- **Sisyphus (Gopinathan, Keoliya, Sergey et al., PLDI'23)** — **closest prior
  art**: first proof repair for **arbitrary implementation-logic changes with
  unchanged spec**, higher-order imperative OCaml verified in Coq/CFML separation
  logic; repaired **all 10** benchmarks. **Coq/CFML, not seL4/Isabelle, not
  refinement** — but it validates that "change the code, keep the spec, auto-repair
  the proof" *works* in a comparable setting. The seL4/Isabelle/refinement (ccorres)
  instantiation is the open gap.
- **Serval (Nelson et al., SOSP'19) / Hyperkernel (SOSP'17)**: push-button SMT
  re-verification, fully automatic after code change — but **provably does not
  extend to seL4** (needs finite interfaces / no unbounded loops / decidable FOL;
  seL4 uses unbounded interactive Isabelle). Serval is itself optimization-sensitive
  (-O1/-O2 ~5× slower than -O0, citing the PLDI'13 TV work).

## Area 5 — ML/LLM × seL4 refinement repair (GAP IS REAL, medium confidence)

- **No verified published work** applies ML/LLM to optimize seL4 code or to
  repair/regenerate its Isabelle refinement proofs (corres/ccorres) after
  Haskell/C changes.
- All LLM proof tooling found is **greenfield synthesis**, orthogonal to
  refinement repair: Baldur (FSE'23, LLM whole-proof gen+repair, Isabelle/Coq but
  not refinement), Draft-Sketch-Prove, Thor, Magnushammer, COPRA, LeanDojo/ReProver;
  and the seL4/Isabelle-adjacent line (Selene, AutoReal, Stepwise tree-search,
  PROMISE, FVELER) — all proof *productivity / synthesis*, none refinement-repair.
- **Honest caveat**: absence-of-evidence assembled from the verified set, not an
  exhaustive negative sweep. Corroborated by project memory
  (`sel4-llm-proof-synthesis-landscape`, `staticize-*`): the whole LLM-proof field
  is greenfield synthesis, orthogonal to refinement repair after code change.

## Under-covered (do not over-claim)

- **Area 3-MCS/multi-arch port cost**: neither pass produced surviving quantified
  claims on ARM→x64→RISC-V→AArch64 / MCS proof-porting cost or arch-split reuse
  fractions. **Evidence gap, not confirmed absence** — needs a dedicated pass.
- **Current (2025-26) translation-validation economics** under the coliasgroup
  graph-refine rewrite (AArch64? -O2? incremental re-run?) — open.

## Implications for the project

1. **C is the right optimization layer** (primary-source confirmed) — but it now
   carries **two** machine re-establishment layers (CRefine ccorres + ~6–8 h
   binary TV) and the TV layer is **optimization-level-sensitive**. Tighten the
   `isabelle_prover_c` Tier model with a translation-validation gate.
2. **The fastpath sideways-equivalence (5 pm) and Sisyphus (Coq, all-10 repaired)
   are the two existing templates** — the LLM-repair pitch is to compress the
   former by instantiating the latter's idea in seL4/Isabelle refinement.
3. **The niche is genuinely open**: LLM-driven repair of seL4 refinement proofs
   after Haskell/C changes is unaddressed; the idea is *named* (PUMPKIN Pi) and
   *validated in an analogue* (Sisyphus), but **never built for seL4/Isabelle**.
