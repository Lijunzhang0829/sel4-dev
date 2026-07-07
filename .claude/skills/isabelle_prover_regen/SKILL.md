---
name: isabelle-prover-regen
description: "Corpus-level regeneration of seL4 refinement proofs. Split the stack at the Haskell pivot into two halves (abstract+haskell, or haskell+C); fix the TWO adjacent CODE artifacts verbatim, keep only the op-level refinement CONTRACT SURFACE (the top-level corres/ccorres statements) as the verification oracle, delete everything beneath it, and have an LLM regenerate the WHOLE proof corpus that connects the two artifacts. Then benchmark the machine corpus against the human one at SYSTEM-LEVEL AGGREGATE (total build wall, proof LOC, lemma count, robustness) — not lemma-level diff, not pass-rate. Use when studying whether an LLM can rebuild a refinement layer's proof engineering and whether the machine corpus is better or worse than the ~20-person-year human one."
---

# Regen — regenerate the proof corpus between two code artifacts, benchmark LLM vs human

seL4 is a stack of **three code artifacts** — **abstract spec** (top) ⊒
**Haskell design spec** (middle) ⊒ **C** (bottom) — plus the **proof** that
connects and checks them (AInvs invariants, Refine, CRefine). That proof is
where the ~20 person-years went, and where the perpetual maintenance tax is
paid.

This sub-skill asks a question prior work does not answer (positioning per the
2026-07 scan: seL4 LLM work is per-lemma synthesis on pass-rate — Selene/
AutoReal/Stepwise/PROMISE; proof-quality work is informal-math or Lean
refactoring — ProofRank/Proof-Refactor/Lean Refactor; template-style whole-block
mechanization exists in Coq — CertiCoq-line; lemma-STATEMENT generation exists.
None combine corpus-granularity + real refinement substrate + human-engineering
comparison + memorization control — that conjunction is the gap, and claims
must be phrased as the conjunction, never as "nobody does X" for any single axis):

> **Fix two adjacent CODE artifacts as ground truth. Delete the whole proof
> corpus that connects them, keeping only the op-level refinement contract
> surface as the oracle. Can an LLM regenerate that entire corpus — and, at
> system-level aggregate, is the machine's proof engineering better or worse
> than what the humans wrote?**

Prior seL4 work (Selene 27% → AutoReal 51.67% → Stepwise 77.6%) does
**per-lemma synthesis** and competes on **pass-rate**. This skill is
deliberately a level up on both axes:

- **Granularity = the whole proof corpus between two fixed code artifacts, not
  the lemma.** We delete and regenerate everything beneath the contract
  surface in dependency order. Operating per-theory or per-lemma — "keep every
  lemma statement, fill each body one at a time" — is explicitly **not** this
  skill: it collapses back into lemma-level infill, hands the LLM the human's
  proof decomposition, and forces the machine output to hug the original.
- **Comparison = system-level aggregate, not lemma-level diff, not pass-rate.**
  The endpoints pin correctness (contracts green = correct), so the result is a
  macro comparison of the two corpora: **total build wall vs the golden
  baseline, total proof LOC, lemma/def count, robustness to endpoint
  perturbation.** The unit of result is "the machine rebuilt the abstract⊒haskell
  proof; in aggregate it is X% larger / Y% slower / found Z fewer abstractions,"
  not "lemma N passed." A rigorously localized "the machine corpus is **worse**,
  and here is the aggregate gap and where it concentrates" is a **first-class
  result**, exactly as valuable as "the machine corpus is better."

## Split at the Haskell pivot — two halves, each regenerates only PROOF

The Haskell design spec is a **human design artifact**, not proof. Deleting it
and asking an LLM to bridge abstract→C directly would make it reinvent the
design (not a proof task) and destroy the human comparison baseline. So
**Haskell is a fixed endpoint in both halves; only proof is ever regenerated.**

| Part | Fixed code endpoints (ground truth, verbatim) | Regenerated proof corpus | Session | Weight |
|---|---|---|---|---|
| **Part 1** | abstract spec **+** Haskell design spec | **Refine** (`corres`: abstract ⊒ executable) | `Refine` | lighter — **start here** |
| **Part 2** | Haskell design spec **+** C | **CRefine** (`ccorres`: executable ⊒ C) | `CRefine` | heavier (critical path 84%, hour-scale build) — only after Part 1 shows signal |

Do **Part 1** first. CRefine is the build critical path and hits the heaviest
init wall; prove the idea on the lighter Refine seam before paying CRefine's
cost.

## What is kept, what is regenerated (the oracle contract)

The defining move is **not** "keep statements, delete bodies" (that is
lemma-level infill). It is: **keep the two code artifacts + the op-level
refinement contract surface; regenerate the entire proof corpus beneath it.**

| Kept (ground truth — never regenerated, never weakened) | Regenerated (deleted → the LLM invents it) |
|---|---|
| **Both code endpoints**, verbatim (abstract op defs + Haskell op defs; or Haskell + C) | **All proof bodies** of the corpus |
| **The refinement framework / definitions** — `state_relation`, `corres`/`corresK`/`ccorres` defs, the `submonad` locale, invariant definitions. This is the *definition of what "refines" means*, not proof work. | **All private machinery** — helper/`wp`/`crunch`/`simp`/`split` lemmas, proof-local defs and reformulations, locale interpretations that exist only to prove the contracts |
| **The op-level refinement contract surface** — the top-level `corres`/`ccorres` statement per kernel operation. This is the deliberate verification interface **and** the human-comparison anchor. Statement kept; body regenerated. | (the LLM decides *whether it even needs* each helper and invents its own decomposition) |

The kept contract surface does double duty:

1. **Oracle.** Each contract is an independent `check-theory` target — the
   regenerated corpus either closes that exact statement or it doesn't.
2. **Comparison anchor.** The human and machine corpora prove the *same*
   contracts, so they are directly comparable in aggregate.

### The oracle-gradient problem (why the contract surface, not a single theorem)

The tempting extreme — keep **only** the single final refinement theorem and
delete everything else — has **no gradient**: the machine must regenerate
thousands of interdependent lemmas correctly before anything at all checks
(all-or-nothing), so it never converges in one shot and you measure nothing.
The **op-level contract surface** is the right granularity: `O(#operations)`
checkpoints, each a *system-level contract* (not lemma-level plumbing), enough
gradient to converge while keeping the framing macro. On a red contract, the
Isabelle `***` diagnostic localizes which invented lemma failed → closed-loop
repair still works, just coarser than per-lemma.

**"How thin the kept oracle is" is a sweepable independent variable** —
per-op contract surface (thick, converges) → per-subsystem → final-theorem-only
(thin, may not converge). Where regeneration stops converging is itself a
difficulty result *and* a memorization control (the thinner the oracle, the
less the machine could be recalling). This sweep is macro, not a retreat to
lemma level — it dials the *verification scaffolding*, not the unit of work.

**Hard rule: never weaken, rename, or delete a kept contract statement, and
never keep a private helper's statement to hand the machine the human's
decomposition.** Inherited from the parent SKILL: no new
`sorry`/`oops`/`axiomatization`; `check-theory.sh` is the only gate.

## Empirical grounding (measured this line — 2026-07)

- **The inner loop is viable at Refine.** `check-theory` verifies a single
  Refine theory by loading the **prebuilt `Refine` heap** (29 heaps prebuilt in
  the container, incl. `Refine`/`CRefine`) and running batch `isabelle process
  -l Refine -T <theory>` — measured **~85 s** for a small theory (SubMonad_R).
  The 34-min interactive-init + OOM wall (see [[infoflow-repl-init-wall]]) does
  **not** bite in batch mode → propose→check→repair is minutes-scale, not
  hours. Always pass `L4V_DIR=/sel4-project/verification/l4v` (heap-consistent
  mount; see [[l4v-dual-mount-heap-churn]]).
- **The private interior is thin at single-theory scope (measured 11–31%).**
  l4v Refine proof engineering is a **densely cross-shared global web** — a
  helper proved in one theory is cited across many others, and a large slab is
  `[wp]`/`[simp]` **attribute-global** facts (exported by attribute regardless
  of scope). So "cited outside the scope = interface" leaves only ~10–31% truly
  scope-private. **Enlarging a 2-theory subsystem barely internalizes the
  sharing** (interrupt path 11%→9%, Tcb 25%→22%); real thickening needs scope
  approaching the whole Refine session. Consequence: two honest regen modes on
  the oracle-thinness dial — **(A) thick oracle, single-theory**: keep the
  shared interface, regenerate the ~25–31% private structure **plus all bodies
  of the kept lemmas** (engineering-freedom modest but real; cheap via
  `isabelle process` against the stock heap); **(B) thin oracle, whole-Refine**:
  regenerate the shared library too (interior → most of Refine; needs a session-
  heap **rebuild**, hour-scale). The `interior%` measures *how much proof
  structure the machine invents from scratch* (engineering-freedom), not total
  regenerated LOC (all bodies are regenerated in both modes). **Schedule_R is
  the thickest single-theory interior (31%, 58 private lemmas, 26 contracts)** →
  the natural mode-A first cut. Infrastructure/library theories (SubMonad_R
  ~90% exported, InterruptAcc_R 0% interior) are pure interface — bad targets.

## Memorization-gap discipline (mandatory — the reviewer defense)

l4v is public and almost certainly in every model's pretraining. The first
question a reviewer asks is: *"Did the LLM regenerate the proof, or recall the
public one?"* **Every result MUST ship a memorization-gap measurement.**

Corpus-level regeneration already helps enormously: reproducing a whole
coherent proof corpus (thousands of interdependent lemmas, human decomposition
deleted) is far less memorizable than a single lemma body. On top of that,
apply at least two of:

| Control | How | Defeats the claim that… |
|---|---|---|
| **Temporal cutoff** | Prefer artifacts / commits **changed after the model's training cutoff**; report the cutoff. | …the exact proof was in pretraining. |
| **Perturbation** | Rename bound variables / reorder conjuncts / restructure the endpoint so a *verbatim* recalled script fails to apply; the LLM must re-derive. | …the model pattern-matched the original text. |
| **Oracle-thinning** | Sweep the contract surface thinner (per-op → subsystem → final theorem); a machine that still converges with less scaffolding is regenerating, not recalling. | …success depended on the human decomposition being handed over. |
| **Divergence check** | Diff the regenerated corpus against the human one; a *structurally different* corpus (different helper set, decomposition) that still checks is strong evidence of regeneration. | …it's the same proof. |

Report the gap as a headline number alongside every correctness/quality
result. A high pass-rate with an untested memorization gap is **not a result**.

## Workflow (reuses existing assets — little new infra)

The inner loop is the `spec-strengthen` loop with a different payload:
propose → `check-theory` trial → feedback → repair. Almost everything
transfers.

1. **Scope + fix the contract surface.** Pick the target scope (a self-
   contained kernel subsystem, or one Part in full). Fix the two code
   artifacts verbatim. Enumerate the scope's **op-level `corres`/`ccorres`
   contracts** (the kept oracle) and the **external interface** the scope
   consumes from untouched theories (kept, so untouched downstream still links).
2. **Strip beneath the contract surface.** Produce the input: two code
   endpoints + framework + contract statements + external interface kept;
   **everything private beneath — all bodies AND all private helper/def
   statements — deleted.** (This is the body-stripper, re-scoped from
   "delete bodies" to "delete the whole private interior.")
3. **Regenerate the corpus in dependency order.** The proposer (`claude -p`,
   the spec-strengthen streaming + archiving harness) gets: the two code
   artifacts, the contract statements it must close, the external interface,
   and (unless fact-ablating) surrounding hints. It emits **whole proof
   machinery** (invented helpers + bodies), not one body at a time.
4. **Verify against the contract checkpoints.** Batch `check-theory.sh
   --patch` (~85 s/theory against the prebuilt heap) → green/red per contract.
   On red, feed the Isabelle `***` diagnostic back (closed-loop repair). **Do
   not** drive the loop through interactive REPL at Refine/CRefine (34-min
   heap-init + OOM wall); batch `check-theory` is the working oracle.
5. **Measure SYSTEM-LEVEL aggregate.** Once the scope's contracts are green,
   run the **proof-track measurement apparatus** (per-file `check-theory` wall
   vs `reports/golden-baseline/walls.json`, proof LOC, lemma/def count) over
   the whole regenerated corpus vs the human one — plus the perturbation /
   oracle-thinning / divergence controls. The result is a macro table, not a
   per-lemma pass list.
6. **Record.** One bundle per regenerated scope under
   `reports/regen/<NNNN>-<scope>/`: the stripped input, per-contract proposer
   transcripts (audit trail = evidence it was regenerated, not recalled),
   `check-theory` verdicts, the **aggregate quality-delta table** (machine vs
   human on total wall / LOC / count / robustness), and the **memorization-gap
   report**.

## Result contract (what "done" means here)

Unlike the strengthen skills, the deliverable is **not** an applied source
patch — it is a **benchmarked, system-level comparison**. A regeneration is a
complete result iff all hold:

1. **Correct** — every kept contract `check-theory` green, zero
   `sorry`/`oops`/`axiomatization`, **no contract statement weakened**, no
   private helper statement kept from the human to fake the decomposition.
2. **Corpus-scoped** — the entire proof interior beneath the contract surface
   of a clearly-scoped subsystem (or a full Part), regenerated — not a
   cherry-picked lemma subset.
3. **Aggregate-measured** — machine-vs-human on ≥1 **system-level** axis (total
   proof LOC / total build wall / lemma count / robustness), against the golden
   baseline. Not a lemma-level diff.
4. **Memorization-controlled** — a reported gap using ≥2 controls above.

Both verdicts are publishable:
- **Machine better** (smaller / faster / more robust corpus that still checks)
  → a positive result about machine-generated proof engineering.
- **Machine worse, rigorously localized** ("the machine corpus checks but is
  2.3× larger in aggregate / needs N more helper lemmas / blows up on subsystem
  X because it can't reconstruct abstraction Y") → an equally first-class result.

## Anti-patterns (DO NOT)

| Anti-pattern | Why |
|---|---|
| Competing on **pass-rate** | You'd race Stepwise's fine-tuned model + tree search at 77.6% with `claude -p`. Your axis is corpus-granularity + system-level quality, not %. |
| **Per-theory / per-lemma surgery** ("keep every statement, fill each body") | Collapses to lemma-level infill; hands the machine the human decomposition; output hugs the original. The unit is the whole interior beneath the contract surface. |
| Keeping **only the single final theorem** | No oracle gradient — never converges, measures nothing. Keep the op-level contract surface. |
| **Lemma-level diff / pass-rate** as the comparison | Wrong axis. Compare system-level aggregate (total wall / LOC / count / robustness). |
| **Regenerating Haskell** (or any code artifact) | Haskell is human design, not proof; reinventing it isn't a proof task and destroys the comparison baseline. Only proof is regenerated. |
| **Weakening a contract statement** to close | The contract is fixed ground truth — weakening it is cheating and voids correctness. |
| Picking an **infrastructure/library theory** (thin interior) | ≈ lemma-level infill; pick per-operation theories where the private interior is thick. **HARD GATE: no headline run on a scope with measured interior < 25% LOC** — run the extractor first; thin-interior theories are harness-shakedown only (SubMonad_R 2026-07-02 is the cautionary case: all runs parked there while Schedule_R sat ready). |
| Reporting **human-assisted items inside the machine aggregate** | Human intervention is a FIRST-CLASS metric: per-scope body budget (`--human-budget`, exceed ⇒ failed(over-human-budget)), separate column for human-supplied bodies, assist-LEVEL ladder (L1 idiom hint / L2 explicit skeleton / L3 body) reported as a headline number. |
| Sweeping the oracle **only thinner** | Add the **template upper arm** (`--arm template`: human original bodies shown as templates, CertiCoq-style). template / contract-surface / thinner-oracle three arms bracket "structure invention" — the deltas are the quantity of interest. |
| Shipping a result **without a memorization gap** | First thing a reviewer attacks; an untested gap is not a result. |
| Starting at **CRefine**, or driving the loop through **interactive REPL** | Critical-path / hour-scale / 34-min init + OOM wall — prove on Refine, use batch `check-theory`. |

## First cut

**Part 1, mode-A (thick oracle), `Schedule_R.thy`** — the thickest single-
theory interior measured (31%, 58 private lemmas, 26 op-level `corres`
contracts), and single-theory so `isabelle process` against the stock Refine
heap suffices (no session rebuild). Fix abstract + Haskell; keep the 26
contract statements + the shared-interface statements (cited outside / attribute-
global) + framework, as the oracle; **strip the entire private interior +
all bodies**; regenerate the whole corpus via the spec-strengthen loop; verify
the contracts with batch `check-theory` (~85 s–minutes); measure the
**aggregate** wall / LOC / lemma-count of the machine corpus vs the human one
against the golden baseline. Report the memorization gap (temporal +
perturbation + oracle-thinning). If a signal exists, widen scope toward whole-
Refine (mode B, session-heap rebuild) and then Part 2 (CRefine). Never mutate
l4v source — stripped input + regenerated theory live under
`reports/regen/<scope>/`; the pre/post `git status --porcelain` of the l4v tree
must stay empty.

## Tools (reusable assets + to-build glue)

| Tool | Role | Status |
|---|---|---|
| theory-DAG tooling (`tools/`, cstr-2graph line) | Dependency topological order + separating the contract surface (out-of-scope-cited / attributed) from the private interior | **exists** |
| `spec-strengthen/strengthen.sh` loop + `spec_agent.py` (`claude -p` streaming + archiving + closed-loop repair) | The propose → trial → repair inner loop; repoint payload from "strengthen" to "regenerate the interior" | **exists, adapt** |
| `$ISA_SCRIPTS/check-theory.sh` | The only verification gate; contract-checkpoint oracle via `--patch` (~85 s/theory vs prebuilt heap) | **exists** |
| proof-track measurement (per-file wall, `reports/golden-baseline/walls.json`, per-line timing) | The **system-level aggregate** LLM-vs-human instrument (total wall / LOC / count) | **exists** |
| interior-stripper (fix code endpoints, keep contract surface + external interface, delete the whole private interior beneath) | Produce the regeneration input from a scope | **to build** |
| contract-surface + interface extractor (op-level corres set; out-of-scope-cited / globally-attributed = kept interface; rest = private interior) | Compute keep-set vs regenerate-set from the theory-DAG + attribute scan | **to build** |
| memorization-gap harness (temporal / perturbation / oracle-thinning / divergence-diff) | Mandatory reviewer-defense metric | **to build** |

## Inherited rules

The parent SKILL's five hard rules apply: no direct source edits (patch +
`check-theory`), no bypassing the prover (no `sorry`/`oops`/`axiomatization`,
`check-theory` is the only gate), per-file wall is truth, PR-tracked mainline
with a per-experiment record. This skill's experiments live under
`reports/regen/` on a topic branch, and the per-experiment record additionally
carries the **aggregate quality-delta table** and the **memorization-gap
report**.

## References (read on demand)

| When | File |
|---|---|
| Why corpus-granularity + system-level quality (not pass-rate) is the empty cell | `literature/README.md`, `literature/ABSTRACTS-SUMMARY.md` (seL4 ∩ layer-regen = empty; competitors are per-lemma synthesis) |
| Proof-size as a quality axis | Matichuk et al. ICSE'15 (statement→proof quadratic law) — `literature/` |
| Why the middle layer exists (constrained, not free, regeneration) | seL4 SOSP'09 / TOCS'14 — `literature/02-sel4-optimization-verification/` |
| The inner loop this reuses | `.claude/skills/isabelle_prover_spec/SKILL.md` (strengthen.sh propose→trial→repair) |
| The quality instrument this reuses | `.claude/skills/isabelle_prover_proof/SKILL.md` (per-file wall vs golden baseline; "verified negative is first-class") |
