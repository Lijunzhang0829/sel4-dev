---
name: isabelle-prover-regen
description: "Layer-level regeneration of seL4 refinement proofs. Fix two ADJACENT artifacts (abstract+haskell, or haskell+C), keep the connecting layer's lemma STATEMENTS as oracle, delete the proof BODIES, and have an LLM regenerate the whole layer in dependency order. Then benchmark the LLM-regenerated layer against the human one (correctness + proof size + wall + robustness). Not lemma-at-a-time synthesis — the unit is the LAYER. Use when studying whether an LLM can rebuild a refinement layer's proof engineering and whether the result is better or worse than the human original."
---

# Regen — regenerate a refinement layer, benchmark LLM vs human

seL4 is a four-layer stack: **abstract spec** (top) ⊒ **Haskell design
spec** (middle artifact) ⊒ **C** (bottom), with the **proof** being the
connective tissue that makes each ⊒ hold (AInvs invariants, Refine, CRefine).
That connective tissue is where the ~20 person-years went, and where the
perpetual maintenance tax is paid.

This sub-skill asks a question no prior work asks:

> **Fix two adjacent artifacts as ground truth. Delete the proof layer that
> connects them. Can an LLM regenerate that whole layer — and is the result
> better or worse than what the humans wrote?**

Prior seL4 work (Selene 27% → AutoReal 51.67% → Stepwise 77.6%) does
**per-lemma synthesis** and competes on **pass-rate**. This skill is
deliberately a level up on both axes:

- **Granularity = the LAYER, not the lemma.** We delete and regenerate a
  whole connected refinement layer in dependency order. Cherry-picking
  individual theorems is explicitly *not* this skill.
- **Metric = quality, not pass-rate.** The endpoints pin correctness (build
  green = correct), so quality becomes a free second axis. A rigorously
  established "the LLM's layer is **worse**, here is where and why" is a
  **first-class result**, exactly as valuable as "the LLM's layer is better."

## The two parts (do Part 1 first)

Split the stack at its two refinement seams. Each part fixes two adjacent
artifacts and regenerates the proof between them.

| Part | Fixed endpoints (ground truth, verbatim) | Regenerated layer | Session | Weight |
|---|---|---|---|---|
| **Part 1** | abstract spec **+** Haskell design spec | **Refine** proof (`corres`: abstract ⊒ executable) | `Refine` | lighter — **start here** |
| **Part 2** | Haskell design spec **+** C | **CRefine** proof (`ccorres`: executable ⊒ C) | `CRefine` | heavier (critical path 84%, hour-scale build) — only after Part 1 shows signal |

Do **Part 1** first. CRefine is the build critical path and hits the heavy
interactive-init wall; prove the idea on the lighter Refine seam before
paying CRefine's cost.

## What is kept, what is deleted (the oracle contract)

The defining move — and the reason this is tractable rather than
all-or-nothing — is **keep the lemma STATEMENTS, delete only the BODIES.**

| Kept (ground truth) | Deleted (to regenerate) |
|---|---|
| Both endpoint artifacts, verbatim | The proof **bodies** of the connecting layer |
| Every connecting-layer lemma's **statement** (the proof obligation) | (the tactic script after the statement) |
| The state relation / `corres`/`ccorres` framework, invariant statements | |

The kept statements do double duty:

1. **Oracle.** Each kept statement is an independent `check-theory` target —
   the regenerated body either closes that exact statement or it doesn't. This
   gives a **dense, per-lemma reward** instead of one all-or-nothing signal at
   the end of the layer.
2. **Trusted fact library.** A later lemma's regenerated proof may cite an
   earlier lemma **by its kept statement** even before that earlier body is
   regenerated — so the layer can be rebuilt in dependency (DAG topological)
   order, each lemma verified against the kept statements of its dependencies.

**Hard rule: never weaken, rename, or delete a kept statement to make a proof
close.** Weakening the obligation is cheating — the endpoints and statements
are the fixed ground truth. Inherited from the parent SKILL: no new
`sorry` / `oops` / `axiomatization`; `check-theory.sh` is the only gate.

## Memorization-gap discipline (mandatory — the reviewer defense)

l4v is public and almost certainly in every model's pretraining. The first
question a reviewer asks is: *"Did the LLM regenerate the proof, or did it
recall the public one?"* **Every result MUST ship with a memorization-gap
measurement.** This is not optional and not an afterthought — it is the
control variable that makes the whole study credible.

Regenerating a *whole layer coherently* is far less memorizable than a single
lemma (a person-scale artifact can't be reproduced verbatim), so the layer
granularity already helps. On top of that, apply at least two of:

| Control | How | Defeats the claim that… |
|---|---|---|
| **Temporal cutoff** | Prefer theories / commits **changed after the model's training cutoff**; report the cutoff. | …the exact proof was in pretraining. |
| **Perturbation** | Rename bound variables / reorder conjuncts / restructure the endpoint so a *verbatim* recalled script fails to apply; the LLM must re-derive. | …the model pattern-matched the original text. |
| **Fact-ablation** | Run with vs without feeding the model the surrounding proof hints; report the delta. | …success depended on being handed the answer. |
| **Divergence check** | Diff the regenerated proof against the human one; a *structurally different* proof that still checks is strong evidence of regeneration, not recall. | …it's the same proof. |

Report the gap as a headline number alongside every correctness/quality
result. A high pass-rate with an untested memorization gap is **not a result**
— it is the thing reviewers will reject.

## Workflow (reuses existing assets — little new infra)

The inner loop is the `spec-strengthen` loop with a different payload:
propose → `check-theory` trial → feedback → repair. Almost everything
transfers:

1. **Order the layer.** Build the connecting layer's dependency DAG with the
   theory-DAG tooling (the reusable asset from the cstr-2graph line) →
   topological order of lemmas.
2. **Strip bodies.** Produce the endpoint-fixed, statements-kept, bodies-
   deleted version of the target theory (bodies become the to-regenerate
   holes; dependencies referenced by kept statement).
3. **Regenerate in DAG order.** Per lemma, the proposer (`claude -p`, the
   spec-strengthen streaming + archiving harness) gets: the two endpoint
   artifacts, the kept statement, the kept statements of its dependencies, and
   (unless fact-ablating) surrounding hints. It emits a proof body.
4. **Verify (dense oracle).** `check-theory.sh --patch` on the theory with
   this body filled → green/red for this exact statement. On red, feed the
   Isabelle `***` diagnostic back to the proposer (closed-loop repair, as in
   `strengthen.sh`). The batch build is the oracle — do **not** rely on
   interactive REPL stepping at Refine/CRefine (it hits the 34-min heap-init +
   OOM wall; batch `check-theory` is the working path).
5. **Measure quality.** Once the layer (or module) is green, run it through
   the **proof-track measurement apparatus** (per-file `check-theory` wall vs
   `reports/golden-baseline/walls.json`, proof size, lemma count) — this is
   the ready-made instrument for "LLM vs human." Also run the perturbation /
   divergence controls from the memorization section.
6. **Record.** One experiment bundle per regenerated layer/module under
   `reports/regen/<NNNN>-<layer>/`: the stripped-body input, per-lemma
   proposer transcripts (audit trail), `check-theory` verdicts, the
   quality-delta table (LLM vs human on size/wall/robustness), and the
   **memorization-gap report**. The transcript IS the evidence that the proof
   was regenerated, not recalled.

## Result contract (what "done" means here)

Unlike the strengthen skills, the deliverable is **not** an applied source
patch — it is a **benchmarked comparison**. A layer regeneration is a
complete result iff all hold:

1. **Correct** — `check-theory` green, zero `sorry`/`oops`/`axiomatization`,
   **no kept statement weakened**.
2. **Whole-layer** — the entire target layer (or a clearly-scoped refinement
   module), regenerated in dependency order — not a cherry-picked subset.
3. **Quality-measured** — LLM-vs-human on ≥1 quality axis (proof size / build
   wall / robustness to endpoint perturbation), against the golden baseline.
4. **Memorization-controlled** — a reported gap using ≥2 controls above.

Both verdicts are publishable:
- **LLM better** (smaller/faster/more robust layer that still checks) → a
  positive result about machine-generated proof engineering.
- **LLM worse, rigorously localized** ("the LLM's layer checks but is 2.3×
  larger / blows up on module X because it can't reconstruct invariant Y") →
  an equally first-class result. A hand-waved "it didn't work well" is
  worthless; a replayable, per-lemma-attributed account of *where and why* the
  machine layer is worse is a real finding.

## Anti-patterns (DO NOT)

| Anti-pattern | Why |
|---|---|
| Competing on **pass-rate** | You'd race Stepwise's fine-tuned model + tree search at 77.6% with `claude -p` — a losing position. Your axis is layer-granularity + quality, not %. |
| **Lemma-at-a-time** cherry-picking | Collapses back into ordinary synthesis; the whole novelty is regenerating a *connected layer* in dependency order. |
| **Weakening a kept statement** to close a proof | The statement is fixed ground truth — weakening it is cheating and voids correctness. |
| Shipping a result **without a memorization gap** | It's the first thing a reviewer attacks; an untested gap is not a result. |
| Starting at **CRefine** | Critical-path, hour-scale build, heaviest init wall — prove the idea on Refine first. |
| Driving the inner loop through **interactive REPL** at Refine/CRefine | Hits the 34-min heap-init + OOM wall; use batch `check-theory` as the oracle. |
| Regenerating a **free/different middle** (reinventing the state relation, corres framework) | Cascades into rebuilding all of Refine/CRefine ≈ rebuilding l4v. This skill is *constrained* regeneration: interfaces fixed, bodies regenerated. |

## First cut

**Part 1, the smallest Refine theory** with few `corres` lemmas and shallow
cross-file dependencies. Fix abstract + Haskell, keep all `corres` statements
+ invariants, strip bodies, regenerate in DAG order via the spec-strengthen
loop, verify with `check-theory`, measure size/wall vs golden baseline, and
report the memorization gap (temporal + perturbation). If a signal exists
there, scale to bigger Refine theories, then Part 2 (CRefine).

## Tools (reusable assets + to-build glue)

| Tool | Role | Status |
|---|---|---|
| theory-DAG tooling (`tools/`, cstr-2graph line) | Dependency topological order of the layer's lemmas | **exists** |
| `spec-strengthen/strengthen.sh` loop + `spec_agent.py` (`claude -p` streaming + archiving + closed-loop repair) | The propose → trial → repair inner loop; repoint payload from "strengthen" to "regenerate body" | **exists, adapt** |
| `$ISA_SCRIPTS/check-theory.sh` | The only verification gate; dense per-lemma oracle via `--patch` | **exists** |
| proof-track measurement (per-file wall, `reports/golden-baseline/walls.json`, per-line timing) | The LLM-vs-human quality instrument (size / wall) | **exists** |
| body-stripper (fix endpoints, keep statements, delete bodies, wire dependencies-by-statement) | Produce the regeneration input from a theory | **to build** |
| memorization-gap harness (temporal filter / perturbation / fact-ablation / divergence-diff) | Mandatory reviewer-defense metric | **to build** |

## Inherited rules

The parent SKILL's five hard rules apply: no direct source edits (patch +
`check-theory`), no bypassing the prover (no `sorry`/`oops`/`axiomatization`,
`check-theory` is the only gate), per-file wall is truth, PR-tracked mainline
with a per-experiment record. This skill's experiments live under
`reports/regen/` on a topic branch, and the per-experiment record additionally
carries the **quality-delta table** and the **memorization-gap report**.

## References (read on demand)

| When | File |
|---|---|
| Why layer-granularity + quality (not pass-rate) is the empty cell | `literature/README.md`, `literature/ABSTRACTS-SUMMARY.md` (seL4 ∩ layer-regen = empty; competitors are per-lemma synthesis) |
| Proof-size as a quality axis | Matichuk et al. ICSE'15 (statement→proof quadratic law) — `literature/` |
| Why the middle layer exists (constrained, not free, regeneration) | seL4 SOSP'09 / TOCS'14 — `literature/02-sel4-optimization-verification/` |
| The inner loop this reuses | `.claude/skills/isabelle_prover_spec/SKILL.md` (strengthen.sh propose→trial→repair) |
| The quality instrument this reuses | `.claude/skills/isabelle_prover_proof/SKILL.md` (per-file wall vs golden baseline; "verified negative is first-class") |
