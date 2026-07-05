---
name: isabelle-prover-coevolve
description: "Automated co-evolution / repair of seL4 refinement proofs after an upstream artifact change. Given a spec/haskell/C change that breaks the proof chain (abstract-invariant → Refine → CRefine), an agent localizes the breaks across files/sessions, repairs the proof layer to restore an end-to-end green build (no weakened statements, no sorry), and applies. Narrow single-lemma repair is the fan-out=1 special case; the real target (grounded in l4v git history) is artifact-triggered, multi-file, sometimes cross-session evolution. Use when studying or building proof-maintenance automation for a verified system as it changes."
---

# Co-evolve — repair the proof chain after an artifact change

seL4's ~20 person-years of proof was a one-time cost. The **perpetual tax**
is maintenance: every time the abstract spec, the Haskell design spec, or the
C changes, the refinement chain (AInvs invariants → Refine → CRefine) breaks
and a proof engineer fixes it by hand. This skill automates that.

> **Given (a passing proof chain + an upstream change that breaks it +
> the post-break errors/goals), produce the proof changes that restore an
> end-to-end green build — matching or improving on the human fix.**

- **Repair** = fan-out 1: one change, a local break, fix it.
- **Co-evolution** = the general case: a change fans out across files and
  possibly across refinement sessions; propagate the fix to a fixpoint.

Narrow single-lemma, statement-preserving, proof-internal-trigger repair is
the *done* corner (Baldur self-error, CoqDev/Adapt on Coq, Sisyphus algorithmic).
The **empty cell** — confirmed by `literature/` and by the OSDI 3-year
verification thread — is **LLM-driven, artifact-triggered, multi-file,
refinement-level** co-evolution on seL4/Isabelle. Nobody has done it.

## Empirical grounding (preliminary exploration — 2026-06/07)

Measured on **694 recent l4v non-merge commits** (2023-01 → 2024-07, the
AArch64-verification window; file-level classification — reproduce with
`reports/repair/classify_commits.py`). These numbers **set the task shape**;
re-measure on a wider window before publishing.

| Finding | Number | Consequence for the task |
|---|---|---|
| commits that touch `proof/` | **52%** | proof maintenance is ~half of all activity |
| **co-change** (artifact+proof in **one** commit) | **4.5%** | "change+fix in one diff" is RARE — do not assume it |
| proof-only commits | 47.6% | repairs are mostly **separate** commits |
| artifact-only commit **followed** by a proof-only fix within 3 commits | **76%** | the (change,repair) unit is a **commit PAIR/sequence**, not one commit |
| proof-touching commits that are **single-session** | **86%** (56% single-file) | **L1 covers the majority** — not a toy |
| **cross-session** (≥2 sessions) | 14% (≥4-session ≈ 1/month) | real cascades exist; rare but huge |
| dominant repair site | **CRefine (180) > Refine (149) > AInvs (79)** | **value is in CRefine**; infra says develop on AInvs/Refine |
| co-change trigger layer | abstract 42% · design 35% · haskell 32% · machine 29% · **cspec/C 10%** | model the "change" as **abstract/design/haskell** edits (C enters via design regen) |

**Archetype seed commits** (real l4v, use as first benchmark instances):
- **L1 clean** — `0e8048b4` *aspec+ainvs: sync user_vtop check with C* · `df5e1611` *machine+ainvs: update clearMemory to match C* · `c4390d8e` *spec+proof: update armvVCPUSave to match C* (2–6 files, single session).
- **L3 cascade** — `e89813ec` *proofs: updates for monad refactor* (155 files / 7 sessions) · `d5fa6043` *update for physBase-dependent defs* (25 files / 5 sessions).
- **big single-session sweep** — `1f068023` *crefine: update for new ccorres cong rules* (86 files) · `d87f5e13` *crefine: update for no_name_eta* (42 files).

## The difficulty ladder (one task, calibrated by the numbers above)

This is **one** task — "artifact change → multi-file breakpoints → agent
repairs each → validate → apply" — stratified for de-scoping, not three
separate tasks. The ideal is the top; climb to it.

| Level | = which subset of the ideal | Oracle | Real freq |
|---|---|---|---|
| **L1** | artifact-triggered, breaks contained in **one** session, mostly body fixes | that session builds green | **~86%** |
| **L2** | change forces **statement/invariant evolution**, propagate to consumers along the DAG to a fixpoint | evolved statement closes **AND** consumers stay green | mid |
| **L3** | breaks **cascade across sessions** (abstract→Refine→CRefine); multiple heaps | **end-to-end chain** green | ~14% |

L1 is the majority of the real maintenance tax → **start here**. L3 is rare
(~1/month) but each is enormous human effort → high value, stretch goal.
Value lives in **CRefine**; develop the method on **AInvs/Refine** (lighter,
avoids the Refine/CRefine heap-init + OOM wall — see [[infoflow-repl-init-wall]]).

> **Fork-① decision (C, below) makes this a SCOPE ladder only.** Statement
> evolution is permitted from day one at every level — whenever Δ demands it
> the agent may evolve the statement, and the fixpoint obligation (consumers
> stay green) applies wherever that happens, even in a single-session run.
> The L-levels stratify session fan-out, not statement mutability.

## Two orthogonal axes — measure BOTH (do not conflate)

**Fan-out (breadth) ≠ repair-depth.** A 155-file `monad refactor` repair may
be 155 near-identical mechanical edits (shallow each, tedious in bulk — the
human-labor tax); a 1-file `physBase` change may need a deep re-proof (narrow
but hard). The benchmark must report:

- **Breadth** — #files / #sessions / #lemmas touched (the labor saved).
- **Depth** — per-lemma proof-delta / how much genuine re-proving each break
  needed (the research difficulty).

Never advertise "repaired 155 files" as difficulty — it may be easy-but-many.
The hard result is deep per-lemma re-proof; the *valuable* result is also the
broad tedious sweep. They are different contributions; keep them separate.

## Benchmark construction (two sources, complementary)

**Primary — commit-pair replay (ecological).** For a real artifact change
`C_a` and its human proof-fix `C_p`: reconstruct the broken state by putting
`C_a`'s artifact diff on top of the pre-`C_a` (green) proofs → the build is
broken exactly as history had it; `C_p` is the human reference fix. The 4.5%
co-change commits give a ready-made (break,fix) in one diff — split it into
the artifact part (trigger) and the proof part (reference). Contamination
control: prefer `C_a` **after the model's cutoff**; report it.

**Secondary — synthetic perturbation (decontaminated, controlled).** Apply a
mechanical change to a green theory that provably breaks proofs; you know the
ground-truth break set and it (likely) never happened in history, so recall is
impossible. **Draw the perturbation taxonomy from the observed change types:**
`sync/​match-C`, rename (lemma / method / constant), `update for new
cong/simp rules`, abstraction introduction (e.g. `physBase`), monad/def
refactor. This keeps synthetic breaks on the real distribution.

Primary tests ecological validity + gives the human baseline; secondary tests
decontaminated capability.

## Resolved design decisions (2026-07, operator-confirmed)

| Fork | Decision | Consequence |
|---|---|---|
| **① Statement mutability** | **C — fully open.** Statements may evolve from day one. An **agent** performs both RED-set collection (reads build errors + Δ + DAG into the work-list, decides which statements must evolve) and the semantic half of the gate. | Viable ONLY with a proper **harness**: the real case library (archetype seeds + the 4.5% co-change set, with each human `C_p` as ground truth) is the calibration set — tune the localizer/gate agents against known human fixes until they localize & judge those correctly, before trusting them on new breaks. Archive every transcript. The deterministic gate half (below) stays mechanical and can never be overridden. |
| **② Repair granularity** | **per-FILE** (check-theory's native unit; serial rolling repair within a file). | Zero new scaffolding. Upgrade to per-lemma ONLY if context precision proves sufficient — a sorry-scaffold isolator would be needed; build it only when large fan-out seeds demand it. |
| **③ Cost accounting** | **Machine-side only** (wall, tokens, repair rounds). | Human-cost proxies (LOC / commit-interval / person-month anchors) are OUT of the metric set — a pipeline with a good restore-green rate can fill that gap later. `C_p` stays as a QUALITY reference (divergence-diff), not a cost baseline. |

**Build order implied by ①: the fork-① harness is the FIRST standalone
deliverable** — agent-driven break localization + evolution-aware gate,
calibrated on the case library — built and validated *before* the repair
driver is attached. The remaining pipeline stages are then filled in
incrementally until the task is complete.

## Pipeline (reuses existing assets — little new infra)

```
[green chain @ C_a's parent]
  → apply Δ (real C_a artifact diff  OR  synthetic perturbation)
  → BUILD (check-theory) + theory-DAG + LOCALIZER AGENT → the RED set:
        work-list {(file, lemmas, Δ-context, Isabelle errors, goals, deps,
                     statements-that-must-evolve)}
  → REPAIR DRIVER (per FILE, DAG order; serial rolling within a file):
        propose (claude -p, the spec-strengthen streaming+repair loop)
        → check-theory --patch → green?  ↑ feed *** back, bounded rounds
        → GATE (anti-cheat, below)
  → for L2/L3: recompute RED set after each fix, iterate to a FIXPOINT
        (build green end-to-end); DAG bounds the propagation
  → VALIDATE end-to-end + [--apply]
  → MEASURE (breadth + depth + vs C_p + memorization gap + wall)
  → RECORD bundle + transcripts + ledger + PR
```

The inner loop is the `spec-strengthen` propose→trial→repair loop with a
different payload. Batch `check-theory` is the oracle — do **not** rely on
interactive REPL stepping at Refine/CRefine (heap-init + OOM wall).

## Oracle & anti-cheat gate (hybrid: deterministic core + calibrated agent judgment)

A repair is valid iff `check-theory` green **and** it passes BOTH gate halves:

**Deterministic half (mechanical, non-negotiable, agent can never override):**
- No `sorry`/`oops`/`axiomatization`; no deleting a lemma to dodge the break.
- Every consumer of an evolved statement still closes — statement evolution
  is a **fixpoint** obligation at every level, not a local edit.
- End-to-end: the whole affected scope builds (the session for single-session
  runs; the downstream chain for cross-session runs).

**Agent half (fork-① decision C):**
- "Is the evolved statement a faithful co-evolution of the old one, rather
  than a weakening to triviality?" — with fully-open statement evolution no
  regex can decide this, so a **gate agent** judges it. Discipline: the gate
  agent is **calibrated on the case library first** (real human `C_p` fixes =
  ground truth for what faithful evolution looks like); its verdict + written
  rationale are archived per candidate; disagreement with the deterministic
  half always resolves to reject.

## Memorization-gap discipline (mandatory — reviewer defense)

l4v is public. Every result ships a memorization gap (≥2 controls): temporal
cutoff (`C_a` after cutoff), perturbation (synthetic breaks that never
happened), fact-ablation (with/without surrounding hints), and
**divergence-diff vs the human `C_p`** (a structurally different fix that still
restores green is strong evidence of repair, not recall). An untested gap is
not a result.

## Metrics

1. **Restore-green success rate** — per Level and per change-type.
2. **Breadth & depth** — files/sessions/lemmas repaired; per-lemma proof-delta.
3. **vs `C_p` (quality reference, NOT cost)** — better / worse /
   different-but-valid (edit-distance + structural diff).
4. **Machine-side cost** — wall, tokens, repair rounds per repair. Human-cost
   estimation is deliberately out of scope (fork-③): a pipeline with a good
   restore-green rate can fill that comparison later.
5. **Memorization gap** — headline number, from the controls above.

## Significance & positioning (OSDI, 3-year verification thread)

OSDI'24 IronSpec (spec reliability) / Kondo (protocol proof automation) ·
OSDI'25 Basilisk (best-paper protocol proof automation) / TrainCheck
(invariant inference) · OSDI'26 Stepwise (seL4 forward proof **synthesis**,
77.6%). The thread covers spec reliability, protocol automation, and initial
synthesis — **not proof maintenance under change.** Closest is OSDI'26
Stepwise (same seL4 substrate, neuro-symbolic) doing *forward synthesis*; this
skill is the **orthogonal, unsolved** direction:

> Stepwise scales *initial* proof; we scale proof *maintenance* — the
> perpetual cost its 77.6% never touches.

OSDI wants systems impact + real data, not another proof LLM. The motivation
is the commit study above ("proof repair is half of l4v commits; 76% of
changes trigger separate repair; CRefine dominates"), and the payoff is
**landing real l4v repairs** — a systems contribution, not a pass-rate.

## First cut

**Step 1 — the fork-① harness, standalone.** Agent-driven RED-set
collection + evolution-aware gate, tuned on the archetype case library
(`0e8048b4`/`df5e1611`/`c4390d8e` and the 4.5% co-change set). Input: a
broken tree + Δ. Output: the work-list (which files / lemmas / statements
must evolve) + gate verdicts — validated against the known human answers
(`C_p`). The harness is DONE when it localizes and judges the known cases
correctly.

**Step 2 — attach the repair driver** (per-file, single-session scope,
AInvs/Refine, commit-pair replay from the same seeds): reconstruct the
break, repair, restore green, measure breadth+depth vs `C_p` + machine cost,
report the temporal+perturbation gap. Signal there → scale fan-out →
cross-session / CRefine.

## Anti-patterns (DO NOT)

| Anti-pattern | Why |
|---|---|
| Framing as narrow single-lemma **repair** | That corner is done (Baldur/CoqDev/Sisyphus); the data shows artifact-triggered multi-file evolution — that's the empty cell. |
| Assuming **change+fix in one commit** | Only 4.5%; the real unit is a commit **pair**. |
| Advertising **fan-out as difficulty** | 155 files may be easy-but-many; report breadth and depth separately. |
| **Weakening a statement** to close | Voids correctness; statements may evolve at any level but not trivialize (agent-gated, `C_p`-calibrated), and consumers must stay green. |
| Competing on **pass-rate** vs Stepwise | Wrong axis — the contribution is maintenance + systems impact, not synthesis %. |
| Shipping without a **memorization gap** | First thing a reviewer attacks. |
| Driving the loop through **interactive REPL** at Refine/CRefine | Hits the heap-init + OOM wall; use batch `check-theory`. |

## Tools (reusable assets + to-build glue)

| Tool | Role | Status |
|---|---|---|
| `reports/repair/classify_commits.py` | Repair-distribution classifier over l4v git log (the preliminary exploration) | **exists** |
| theory-DAG: `tools/theory_dag/` (`parse_theory_imports.py` + `dag_query.py`; restored 2026-07 from the cstr-2graph line after cleanup 9d51c97 dropped it; arch-parameterized via `L4V_ARCH`; recompute once per campaign, read-only during it) | Blast-radius upper bound (`dependents` = reverse closure, topo repair order); the build oracle stays the only truth | **exists (restored)** |
| `spec-strengthen/strengthen.sh` loop + `spec_agent.py` | propose→trial→repair inner loop; repoint payload to "repair the break" | **exists, adapt** |
| `$ISA_SCRIPTS/check-theory.sh` | The only verification gate; dense per-lemma oracle via `--patch` | **exists** |
| proof-track measurement (per-file wall, golden baseline) | Cost / wall side of the metric | **exists** |
| **fork-① harness**: localizer agent (RED-set work-list) + gate agent (evolution-vs-weakening), calibrated on the case library | **The FIRST standalone deliverable** | **to build (first)** |
| commit-pair miner (reconstruct broken state from `C_a`, pair with `C_p`) | Benchmark builder — the primary source | **to build** |
| perturbation generator (taxonomy from observed change types) | Decontaminated secondary benchmark | **to build** |
| memorization-gap harness (temporal / perturbation / ablation / divergence-diff) | Mandatory reviewer-defense metric | **to build** |

## Relation to the regen direction (maintained elsewhere)

Same axis, different blast radius: **co-evolve** repairs the few proofs a
change breaks; **regen** — the sibling corpus-regeneration study, maintained
on the other machine and **not present on this coevolve-only checkout** —
deletes and rebuilds a whole layer. They share the same lower stack
(check-theory oracle, theory-DAG, the spec-strengthen propose→trial→repair
loop, memorization discipline). regen is co-evolve's maximal case (the
"change" is "delete everything").

## Inherited rules

Parent SKILL's five hard rules apply (no direct source edits; no
`sorry`/`oops`/`axiomatization`; `check-theory` is the only gate; per-file
wall is truth; PR-tracked mainline). Experiments live under `reports/repair/`
on a topic branch; each record additionally carries the **breadth/depth
table**, the **vs-`C_p` comparison**, and the **memorization-gap report**.

## References (read on demand)

| When | File |
|---|---|
| The repair distribution + archetype seeds (preliminary exploration) | `reports/repair/survey-l4v-repair-distribution.md` |
| Why repair/co-evolution is the empty cell (seL4 ∩ repair = ∅) | `literature/README.md`, `literature/ABSTRACTS-SUMMARY.md` |
| Nearest non-seL4 repair precedents | CoqDev/Adapt, ExVerus, PUMPKIN Pi, Sisyphus — `literature/01…`, `literature/02…` |
| Maintenance cost baselines | seL4 SOSP'09 (17%/<5%), fastpath (~5pm) — `literature/02-sel4-optimization-verification/` |
| The inner loop this reuses (propose→trial→repair) | `spec-strengthen/strengthen.sh`, `spec-strengthen/scripts/spec_agent.py` |
