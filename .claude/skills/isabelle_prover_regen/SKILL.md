---
name: isabelle-prover-regen
description: "LLM proof engineering for large proof repositories, on seL4. Split the stack at the Haskell pivot; fix adjacent CODE artifacts and the op-level corres/ccorres CONTRACT SURFACE, delete everything beneath it, and regenerate Refine then CRefine with session-global planning, theory-DAG scheduling, generated lemma DAGs, and premise-provenance auditing. Treat theories as scheduling/sealing units, not isolated infill tasks. Evaluate integration / generality / compositionality / dedup / maintainability against the human corpus; a rigorously-localized negative is a first-class result."
---

# Regen — regenerate the proof corpus between two code artifacts, benchmark LLM vs human

## Current executable architecture

Use `regen/proof_session_runner.py` for new Refine generation work. It
orchestrates independent `File Planner`, `Lemma Prover`, `Graph Revision
Agent`, and `Isabelle Checker` modules. File planning contains generated
theorem nodes only; external premises are retrieved at node execution time.
Only the revision agent may modify a certified graph, and Isabelle decides all
GREEN/RED states. M0-M3 provide only the sanitized contracts, clean Isabelle
environment, and legal premise service.

Read `regen/MODULAR-PROOF-PIPELINE.md` before starting or resuming a run.

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
  the lemma.** Delete and regenerate everything beneath the contract surface
  in dependency order. A theory or lemma may be a scheduling/checkpoint unit,
  but never an isolated benchmark boundary supplied with the original target-
  session interior. "Keep every human lemma statement and fill each body" is
  explicitly **not** this skill: it hands the machine the human decomposition
  and forces the output to hug the original.
- **Comparison = repo-level proof ENGINEERING, not lemma-level diff, not
  pass-rate.** The endpoints pin correctness (contracts green = correct), so the
  result is how the two corpora ENGINEER the proof — measured on the **five
  repo-level metrics** below (integration / generality / compositionality / dedup
  / maintainability), backed by system-level aggregates (total build wall, proof
  LOC, lemma/def count). The unit of result is "the machine's proofs are
  monolithic (fan-out 0.22 vs 2.16), its lemmas one-off (reuse ≤1 vs ≤7), it
  re-invents more than it reuses," not "lemma N passed." A rigorously localized
  "the machine engineers **worse**, and here is the gap and where it concentrates"
  is a **first-class result**, exactly as valuable as "the machine engineers better."

## Problem setting: LLM proof engineering for large proof repositories

The sharpened framing (supersedes "is the machine as good as 20 person-years"):

> **The core question is not whether an LLM can prove an isolated lemma. It is
> whether, dropped into a large, mature, structurally-complex proof repository,
> an LLM can REFACTOR, REUSE, ORGANIZE, and EXTEND the existing proofs.** All
> questions are framed around the REPO, never a single lemma. The machine does
> not have to WIN — it has to be CHARACTERIZED. "The machine engineers proofs
> differently, and less compositionally, and here are the numbers" is the result.

Evaluate on **five repo-level metrics** (each computable statically on the
machine corpus + the human theory; each names a concrete measurement):

| # | Metric | Question | Measurement |
|---|---|---|---|
| **1** | **Integration** | Does the machine proof fit the existing code organization? | # import-namespace collisions (shadow guard telemetry), respect for theory-DAG layering + the `[wp]`/`[simp]` attribute pool |
| **2** | **Generality** | Are its proposed lemmas general enough? | downstream **reuse count** of each machine helper vs each human helper (higher = more general) |
| **3** | **Compositionality** | Is the proof compositional or monolithic? | per-proof **local fan-out** (how many local lemmas each proof rests on) + support-lemmas-per-target |
| **4** | **Dedup** | Does it reduce duplicate proofs, or re-invent existing facts? | # helpers that re-derive an existing import (name/semantic collision) vs # net-new-reusable helpers |
| **5** | **Maintainability** | Is the proof script robust/maintainable? | ratio of targeted `rule`/projection tactics vs explosion-prone `fastforce`/`auto` over big defs; brittleness under endpoint perturbation |

**First-version numbers (Schedule_R, machine 138-locked contract arm vs human
theory, 2026-07 — the baseline that guides harness work; full record in
`regen/HARNESS-EVOLUTION.md` entry 26):**

- **Generality:** machine helper reuse mean **0.15, max 1**, 85% one-off; human
  **0.65, max 7**, 64% one-off → machine lemmas ~**4× less reused**, never
  reused more than once.
- **Compositionality:** machine per-proof local fan-out median **0** (mean 0.22);
  human median **2** (mean 2.16, max 13); support/target **0.89 vs 7.42** →
  machine proofs are near-**monolithic** (~8–10× less decomposed). NOTE: the
  earlier impression that the machine "over-factors" (e.g. `setCurThread_corres_helper`
  abstracting `modify`) was ONE example; systematically the machine **under-factors**.
- **Dedup:** of 65 machine helpers, **14 re-invent an existing import** (22%), only
  **7 are net-new-reusable** (11%), 44 are one-off scaffolding → **re-invention >
  net-new**; net reuse is NEGATIVE.

**Unifying finding: the machine does LOCAL, MONOLITHIC proof engineering** — it
closes each obligation in near-isolation, emits mostly throwaway scaffolding, and
frequently re-derives facts that already exist; the human does LIBRARY-level
engineering (deep decomposition into general, reused lemmas). **The LLM's deficit
is not single-point proving ability — it is proof ORGANIZATION.** This directly
sets harness direction (below): the harness must reward reuse of imports (shadow
guard, done) and of already-locked helpers, and reward general lemma design — not
just supply stronger tactics.

Caveats to carry with every number: (a) human reuse is counted WITHIN one theory
and is therefore UNDER-counted (human lemmas are reused downstream) → the true gap
is larger; (b) numbers are the contract-arm cut; recompute per corpus.

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

## Session-staged repo-level architecture

Keep the two settings distinct. Setting A hides the human target-session file
architecture and asks the model to create files, contract ownership, imports,
generated theorem statements, and proofs. It remains deferred.

Current Setting B fixes only the Proof/Support classification, Proof-theory file
slots, contract-to-file assignment, external endpoints, parents, and unchanged
Support theories. It does **not** preserve human Proof-theory imports or any
other human Proof command. The model generates each complete Proof theory,
including its imports, theorem statements, attributes, declaration order, and
proofs.

Generate Refine first. A generated Proof theory may import frozen environment
theories and already sealed generated Proof theories. Seal and publish each
successful theory before another file can retrieve its declarations. Once all
Refine contracts are green, clean-build and seal generated Refine; CRefine must
then use that exact generated heap.

The generated theorem DAG contains only nodes that must be proved by this
campaign: fixed contract roots, generated obligation-theorem nodes that perform
meaningful contract proof steps, and generated helper leaves that encapsulate
independently reusable facts. Existing premises are not graph nodes. They are
retrieved for each proof attempt and recorded as actual dependencies. A contract
may be proved directly, and a helper may be shared; do not create cosmetic
wrapper nodes.

The Session Planner emits one evidence-grounded layered Proof-file generation
list for each session, then tracks pending, sealed, failed, skipped, and retry
status while the runner traverses it. It may use public contract statements and
normalized frozen-Support unlock facts. It may record the M3 snapshot identity,
but it never reads premise records or human Proof imports to choose the order,
and it never proposes theorem statements. A single audited M4 model call may
propose evidence-citing priority edges; deterministic code validates them and
emits the final list. It still owns staging, Support unlock publication,
generated import acyclicity, sealing, and Refine-to-CRefine handoff.

The File Planner sees one file identity, all fixed contracts in that file, the
legal environment needed to understand them, and optional premise search
results. It outputs the initial complete generated-theorem DAG with exact
generated statements and generated-node edges, but no proof bodies and no
premise nodes.

The Premise Database indexes checked declarations from frozen parents, frozen
Support, sealed generated Proof theories, and green nodes in the current file.
It supports retrieval and provenance; it does not invent theorems or authorize
graph changes. Retrieval runs at node start and again from an Isabelle residual.
A retrieved cross-file fact proposes an import of its owner, which must pass
cycle/provenance checks before the fact becomes callable.

The Lemma Prover proves one certified graph node and may repair only its proof
body. The independent Graph Revision Agent reviews evidence-backed requests to
add, split, replace, reuse, or change the role of generated theorem nodes. A hard
helper does not recursively create helpers: normally split it into smaller
sibling leaves, or promote it to an obligation theorem only after approval.

**When designing, implementing, or evaluating the repo-level harness, read
[references/SESSION_HARNESS.md](references/SESSION_HARNESS.md) completely.**
It is the authoritative protocol for trust boundaries, role definitions,
stage-by-stage inputs/outputs, Premise Database publication/retrieval, generated
imports, graph revision, sealing, and the M0-M12 Refine-to-CRefine chain.

### Fresh-conversation startup

For a new conversation on the current Setting B campaign, read these files in
order before changing code or resuming a generation:

1. this `SKILL.md` for the research claim and contamination rules;
2. `references/SESSION_HARNESS.md` for normative role and artifact contracts;
3. `regen/REFINE-CREFINE-REPO-LEVEL-EXPERIMENT-PLAN.md` for current
   implementation status, work packages, and the next executable milestone;
4. `regen/STEPWISE-CODEX.md` only when working on the M7 node prover.

Do not resume historical Schedule_R or ArchAcc_R generations as if they were a
Setting B campaign. They are diagnostic evidence produced before the current
M0-M12 protocol. Start from the plan's current milestone and create fresh,
source-linked generation IDs for new evidence.

## What is kept, what is regenerated (the oracle contract)

Setting A keeps endpoints, external semantics, and all target contracts, but
regenerates the target theory architecture as well as every proof. It is
recorded for a later independent campaign.

Current Setting B keeps fixed parents, mechanically classified Support theories,
Proof-theory file slots, contract ownership, and exact target contracts. It does
not keep human Proof-theory imports. It regenerates every classified Proof theory
as a whole file and synthesizes imports from successful generated dependencies.

| Model-visible fixed input | Frozen but exposed through premise retrieval | Deleted and forbidden |
|---|---|---|
| Proof-file identity and every local target contract | Parent-session and Support declarations | Human Proof imports and original body |
| Current generation's Isabelle residuals | Sealed generated Proof declarations | Human helper names/statements/proofs/order |
| Current-file green generated theorems | Names, statements, attributes, provenance, and defining equations of eligible premises | Human consumers, dependency graph, export design, or inferred helper requests |

There is no target-session framework-declaration allowlist inside a Proof
theory. If such a file owns a target contract, every non-contract command is
deleted. If it owns no target contract, the entire file is frozen as Support.
A contract that cannot elaborate under this binary rule causes a versioned
scope failure; it never authorizes selective copying from the human body.

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
  approaching the whole Refine session. Consequence: two historical regen arms on
  the oracle-thinness dial — **thick oracle, single-theory**: keep the
  shared interface, regenerate the ~25–31% private structure **plus all bodies
  of the kept lemmas** (engineering-freedom modest but real; cheap via
  `isabelle process` against the stock heap); **thin oracle, whole-Refine**:
  regenerate the shared library too (interior → most of Refine; needs a session-
  heap **rebuild**, hour-scale). The `interior%` measures *how much proof
  structure the machine invents from scratch* (engineering-freedom), not total
  regenerated LOC (all bodies are regenerated in both modes). **Schedule_R is
  the thickest single-theory interior (31%, 58 private lemmas, 26 declarations
  under the historical broad `corres` classifier; 20 under the strict
  operation-name-first contract policy)** →
  the natural historical file-level first cut. Infrastructure/library theories (SubMonad_R
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

The outer loop freezes Setting B, extracts contracts and file ownership,
classifies Proof/Support theories, removes every human Proof import/body, builds
the Premise Database, creates one file-generation list, and then traverses it
while sealing every successful Proof theory. Failed or skipped files are
recorded without stopping later attempts. The resulting Proof-theory import graph
is generated, not copied.

Reuse the current `spec-strengthen` / incremental propose -> `check-theory` ->
feedback -> repair machinery as the node-proving inner loop. Execute it under
the control plane in `references/SESSION_HARNESS.md`.

1. **Freeze and sanitize.** Extract contracts and Proof/Support ownership in
   a trusted workspace, copy Support sources, and expose to generation only each
   Proof-file identity and its complete contract set.
2. **Schedule and retrieve.** The Session Planner outputs the complete layered
   file list from public contracts, normalized Support availability, and a
   frozen scheduling policy. It may bind the M3 identity but does not use
   premise records for ordering. Traverse the serial fallback even after a file
   fails, and publish newly buildable frozen Support after each successful seal.
   Search eligible Premise Database records only when planning/proving a theorem;
   retrieved cross-file facts propose checked generated imports.
3. **Plan and generate one whole Proof theory.** The File Planner creates only
   the generated theorem DAG: contracts, obligation theorems, and helper leaves.
   Prove one node at a time. Only the Graph Revision Agent can approve a theorem
   graph mutation from this generation's Isabelle evidence.
   The M7 prover may use the Codex whole-proof baseline or Stepwise-Codex,
   but it cannot change a statement or append nodes. The current Stepwise relay
   path is development-only until model file/tool access is mechanically
   disabled or fully audited.
4. **Audit, seal, and compose.** Synthesize/minimize imports from successful
   dependencies, seal the complete file, publish its exports, clean-build
   generated Refine, and then generate CRefine against that exact heap.
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
2. **Session-scoped** — for a repo-level headline, regenerate the complete
   selected Refine or CRefine theory closure. A subsystem/theory is a harness
   milestone, not the final repo-level result.
3. **Integrated** — build the session from a clean generated mirror using only
   frozen parents and generated ancestor theories. CRefine must consume the
   sealed generated Refine heap.
4. **Premise-audited** — every target-session fact dependency has legal
   provenance; no original private target fact is reachable through a source,
   stock heap, cache, alias, or automation pool.
5. **Aggregate-measured** — machine-vs-human on ≥1 **system-level** axis (total
   proof LOC / total build wall / lemma count / robustness), against the golden
   baseline. Not a lemma-level diff.
6. **Memorization-controlled** — a reported gap using ≥2 controls above.

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
| **Independent per-theory/per-lemma infill** ("keep every statement, fill each body") | Hands the machine the human decomposition and original target-session context. Theory/lemma units are legal only under one session-global strip, plan, registry, and premise firewall. |
| Building CRefine against the **stock human Refine heap** | Breaks the generated chain and turns two claimed repo runs into unrelated file/session experiments. |
| Keeping **only the single final theorem** | No oracle gradient — never converges, measures nothing. Keep the op-level contract surface. |
| **Lemma-level diff / pass-rate** as the comparison | Wrong axis. Compare system-level aggregate (total wall / LOC / count / robustness). |
| **Regenerating Haskell** (or any code artifact) | Haskell is human design, not proof; reinventing it isn't a proof task and destroys the comparison baseline. Only proof is regenerated. |
| **Weakening a contract statement** to close | The contract is fixed ground truth — weakening it is cheating and voids correctness. |
| Picking an **infrastructure/library theory** (thin interior) | ≈ lemma-level infill; pick per-operation theories where the private interior is thick. **HARD GATE: no headline run on a scope with measured interior < 25% LOC** — run the extractor first; thin-interior theories are harness-shakedown only (SubMonad_R 2026-07-02 is the cautionary case: all runs parked there while Schedule_R sat ready). |
| Reporting **human-assisted items inside the machine aggregate** | Human intervention is a FIRST-CLASS metric: per-scope body budget (`--human-budget`, exceed ⇒ failed(over-human-budget)), separate column for human-supplied bodies, assist-LEVEL ladder (L1 idiom hint / L2 explicit skeleton / L3 body) reported as a headline number. |
| Sweeping the oracle **only thinner** | Add the **template upper arm** (`--arm template`: human original bodies shown as templates, CertiCoq-style). template / contract-surface / thinner-oracle three arms bracket "structure invention" — the deltas are the quantity of interest. |
| Shipping a result **without a memorization gap** | First thing a reviewer attacks; an untested gap is not a result. |
| Starting at **CRefine**, or driving the loop through **interactive REPL** | Critical-path / hour-scale / 34-min init + OOM wall — prove on Refine, use batch `check-theory`. |

## Migration path

`Schedule_R.thy` remains a historical proving-backend shakedown, not a
Setting B input, milestone, or repo-level result. Its human interface and stock
Refine heap must never enter a production Setting B generation.

**Historical thick-oracle file-level arm, `Schedule_R.thy`** — the thickest single-
theory interior measured (31%, 58 private lemmas, 26 op-level `corres`
contracts), and single-theory so `isabelle process` against the stock Refine
heap suffices (no session rebuild). Fix abstract + Haskell; keep the 26
contract statements + the shared-interface statements (cited outside / attribute-
global) + framework, as the oracle; **strip the entire private interior +
all bodies**; regenerate the whole corpus via the spec-strengthen loop; verify
the contracts with batch `check-theory` (~85 s–minutes); measure the
**aggregate** wall / LOC / lemma-count of the machine corpus vs the human one
against the golden baseline. Report the memorization gap (temporal +
perturbation + oracle-thinning). Treat this only as historical development
evidence; the current production path is the M0-M12 Setting B protocol. Never mutate
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
| campaign/theory/lemma graph store + session/theory planners | Persist the planning control plane, exact helper statements, edges, state, budgets, and ownership | **to build** |
| statement certifier + premise firewall | Type-check plans and reject illegal/original-target dependencies, including namespace and attribute-pool leakage | **to build** |
| Setting B M0-M3 harness (`refine_campaign.py`, `refine_contracts.py`, `refine_m2_*.py`, `refine_m3_premises.py`) | Freeze campaigns, extract contracts plus minimal semantic contexts, enforce source/heap isolation, elaborate contract shells, and publish prover-native legal premises | **implemented; production replay required after frozen-input changes** |
| theory assembler/sealer + generated heap handoff | Deterministic source integration, clean generated ancestor contexts, and Refine-to-CRefine chain gate | **to build** |
| current `regen_incr.py` machinery as node-prover adapter | Prove certified graph nodes using existing triage, retrieval, hard-lemma, repair, and incremental-check experience | **exists, refactor/adapt** |
| `regen/stepwise_codex.py` + setup/runner | Public Stepwise tree-search controller with Codex 5.5 replacing the paper model; exact prompt/response/IsaREPL logging | **smoke green; development-only, non-sealable relay audit** |
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
| **Required for repo-level harness design or implementation** | `references/SESSION_HARNESS.md` — read completely; roles, stage inputs/outputs, Premise Database, generated imports, graph revision, sealing, and the M0-M12 chain |
| **Required to resume the current campaign** | `regen/REFINE-CREFINE-REPO-LEVEL-EXPERIMENT-PLAN.md` - implementation status, work packages, first milestone, stop conditions, and restart checklist |
| M7 Stepwise-Codex backend | `regen/STEPWISE-CODEX.md` - statement-to-proof interface, setup/run commands, generation evidence, and current no-tool audit limitation |
| Why corpus-granularity + system-level quality (not pass-rate) is the empty cell | `literature/README.md`, `literature/ABSTRACTS-SUMMARY.md` (seL4 ∩ layer-regen = empty; competitors are per-lemma synthesis) |
| Proof-size as a quality axis | Matichuk et al. ICSE'15 (statement→proof quadratic law) — `literature/` |
| Why the middle layer exists (constrained, not free, regeneration) | seL4 SOSP'09 / TOCS'14 — `literature/02-sel4-optimization-verification/` |
| The inner loop this reuses | `.claude/skills/isabelle_prover_spec/SKILL.md` (strengthen.sh propose→trial→repair) |
| The quality instrument this reuses | `.claude/skills/isabelle_prover_proof/SKILL.md` (per-file wall vs golden baseline; "verified negative is first-class") |
## Setting B contamination rule

Graph revision may use only residuals and artifacts produced by the current
sanitized generation. A later consumer cannot backpatch a sealed ancestor for
reuse; it proves a local helper. Correcting a defective generated ancestor
requires a source-linked generation, transitive invalidation, and DAG-ordered
resealing.

Inspect human Proof theories only after the headline campaign is frozen and only
in a separate evaluation workspace. Label every resulting artifact
`human-comparison-contaminated`; it may support post-hoc analysis but cannot be
fed back into generation or counted as independent regeneration evidence.
