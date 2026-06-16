# Two theses, compared: "LLM optimizes seL4" vs "Benchmark LLM Isabelle on seL4"

Branch: `bridge-consumer-trace`. Date: 2026-06-16.
Source: deep-research pass 4 (23 adversarially-confirmed claims) + established
findings from passes 1-3. Confidence/caveats noted.

## The pivot fact: FVELER

The whole comparison turns on one finding (high confidence, 3-0):

**FVEL / FVELER (Lin, Hu et al., NeurIPS 2024 Datasets & Benchmarks)** extracts its
Isabelle substrate **directly from the real seL4/l4v corpus** (C→SIMPL→AutoCorres→
Isabelle; **758 theories, ~29.1–29.3k lemmas, ~200k proof steps, max dependency
depth 156**; the `FVELER`/`l4v_FVEL` GitHub fork mirrors `seL4/l4v`). Paper: "Since
the open-source seL4 verification contains high-quality and multi-level proof
following human reasoning, we choose seL4 as FVELER data source."

But critically:
- It uses the seL4 data **only for FINE-TUNING**, then **benchmarks on EXTERNAL
  datasets (Code2Inv, SV-COMP)** — **no held-out seL4 evaluation** (+17.4% on
  SV-COMP for Llama3-8B, +12% for Mistral-7B).
- It poses **only greenfield synthesis** (Statement Prediction + step-wise Proof
  Generation with prover error feedback). **No refinement-proof repair after a
  code change, no proof strengthening, no perturbation tasks.**
- **No contamination/decontamination methodology** reported.

→ FVELER **de-risks** the substrate (seL4→Isabelle extraction is proven feasible
and valuable) while **leaving the evaluation space wide open**: seL4 as a *held-out
target*, the incremental-improvement/repair tasks they skip, and contamination
control. That gap is exactly our benchmark design (v1).

## Thesis 1 — "Use LLMs to optimize seL4"

**Literature (mostly established passes 1-3; pass 4 added no new verified claims —
flag).**
- **No precedent** for ML/LLMs optimizing or maintaining a large *real* verified
  system (confirmed absence across 3 passes).
- Closest adjacent line: **SWE-bench / SWE-agent** (LLM does real software
  engineering on real repos) — but *none target verified / proof-carrying code*
  (not independently verified this pass; strongly suggested, flag as open).
- Methodological neighbors: Coq proof repair (PUMPKIN Pi, Sisyphus) — not
  seL4/Isabelle. The human-tuned **fastpath** (5 pm, assembly-parity) is the
  baseline any "optimization" must beat.

**Research value — candid verdict: WEAK as a standalone thesis.**
- **Acceptance bar is brutal.** It is a *systems* contribution: the bar is "did you
  make seL4 *actually better* than expert humans already did." The kernel is
  heavily hand-tuned (fastpath at assembly parity); the likely outcome is "LLM
  cannot beat the experts" — a weak/negative systems result.
- **Novelty ≠ value.** "Nobody used LLM on seL4" is true but doesn't carry a paper
  without a demonstrated win, which the human baseline makes unlikely.
- **Audience (SOSP/OSDI/PLDI/CAV/ITP)** wants a real improvement to a real system —
  hard to clear, high risk.

## Thesis 2 — "Benchmark LLM Isabelle/HOL coding on seL4"

**Literature (well-covered, high confidence).**
- **Substrate/landscape**: **PISA** (Jiang et al., gRPC REPL, 2.49M datapoints,
  95/1/4 split, 3,000 held-out theorems) + **miniF2F-Isabelle** is the standard
  pairing. **Thor** (39→57%), **Magnushammer**+Thor (**71%**, 4× fewer params,
  ICLR'24), **Baldur** (47.9% whole-proof / 65.7% +Thor). **All draw substrate from
  the AFP, NOT seL4**, and pose only **greenfield whole-proof generation + repair
  of the model's OWN failed attempt** — never refinement-repair-after-code-change
  or strengthening. Metric throughout: proof-checks-in-Isabelle, pass@k.
- **FVELER** (above) is the *only* seL4-derived Isabelle benchmark — but training-
  only, external-eval, synthesis-only, no contamination handling.
- **Contamination methodology** to borrow: the HumanEval/MBPP/GSM8k/miniF2F
  benchmark-leakage literature (held-out / perturbation / decontamination).

**Research value — candid verdict: STRONG.**
- **Concrete, named, unoccupied gap.** "seL4 as *held-out evaluation* + incremental
  tasks FVELER omits (refinement-repair after code change, strengthening,
  perturbation) + contamination control." FVELER's existence *proves the substrate*
  yet *defines the gap by what it skipped*.
- **Ungameable oracle.** The sound Isabelle kernel = free, objective grading; pass@k
  is the field-standard metric — directly comparable to PISA/Baldur numbers.
- **Valued venue.** NeurIPS Datasets & Benchmarks / ICLR / ACL reward benchmarks,
  and a **large real systems-verification substrate (vs math competition problems)**
  is a genuine differentiator — exactly FVELER's pitch, which we extend from
  training to *evaluation*.
- **Risk is known & addressable.** Contamination (l4v public on GitHub) is the one
  real threat — but handling it is itself a contribution (the memorization-gap
  protocol in our design v1). Note: FVELER's *external* eval may itself be an
  implicit dodge of train-on-seL4/test-on-seL4 leakage — a held-out-seL4 benchmark
  must control pretraining contamination, not just avoid fine-tune leakage.

## Head-to-head

| Axis | T1 "optimize seL4" | T2 "benchmark LLM on seL4" |
|---|---|---|
| Gap real? | Yes (no LLM work) | Yes (FVELER skips eval + repair/strengthen) |
| Closest prior | (none direct); SWE-agent; Sisyphus | FVELER (NeurIPS'24), PISA/Baldur |
| Oracle / grading | wall + green; baseline = expert humans | sound kernel pass@k; baseline = SOTA provers |
| Acceptance bar | beat hand-tuned experts (brutal) | fill a named benchmark gap (clearable) |
| Audience | systems (SOSP/OSDI/PLDI) | ML eval (NeurIPS D&B/ICLR) + verification |
| Main risk | LLM can't beat experts → negative result | contamination → but measurable/addressable |
| Verdict | **WEAK standalone** | **STRONG** |

## Recommendation

**Pursue Thesis 2; treat Thesis 1 as a possible bonus, not the thesis.**
- T2 fills a concrete, venue-valued, FVELER-adjacent gap with an ungameable oracle;
  the **bridge-repair task (refinement-proof repair after Haskell/C change)** is the
  headline — it is the one task neither FVELER nor PISA/Baldur pose, and the one
  only seL4 can offer.
- The two are not exclusive in *execution* (the same repair loop serves both), but
  as a *research narrative* T2 dominates: a positive benchmark result (LLMs *can*
  repair seL4 refinement proofs) incidentally advances T1; a negative result is
  still a publishable benchmark finding. T1 alone has no such safety net.

## Honest caveats

1. Pass 4's verified set is **Thesis-2-heavy**; T1's specific precedent questions
   (SWE-agent-on-verified-code; verified-systems+ML) were **not independently
   verified this pass** — the WEAK verdict rests on the acceptance-bar argument +
   the 3-pass no-LLM-work finding, not new claims.
2. The **venue/citation-impact** sub-question (benchmark vs application paper
   impact) was requested but **not covered by verified claims** — the "benchmarks
   are high-value" framing here is general field knowledge, not freshly verified.
3. FVELER stats vary by source version (29,125 vs 29,304 lemmas; 200,646 vs 201,498
   steps) — dataset versioning; theory count (758) and depth (156) are stable.
4. PISA/SOTA numbers (71%, 65.7%) are 2022-24 framing, not necessarily 2026 SOTA.
