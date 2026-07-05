# Co-evolution repair — Phase-1 summary (2026-06-30 → 07-05)

> seL4/l4v proof co-evolution as a **semi-tooled pipeline**: an upstream
> artifact change (spec / haskell / C) breaks the proof chain; the tool
> DETECTS the broken downstream, REPAIRS it, and VALIDATES the repair — with
> seL4's ~12-year history as the validation corpus. This document summarizes
> Phase 1: substrate, method, the pilot capability result (3/3), the metric
> registry, and the structural gaps to the tool.
>
> Running audit log (chronological, incl. retractions): `coevolve/README.md`.
> Raw data + scripts: `coevolve/` on branch `coevolve` (server B).

## 1. Result in one line

On 3 build-adjudicated **forced** breaks (proof genuinely fails to compile),
the closed-loop LLM repair pipeline restored a green build on **3/3**, each
with a fix that **diverges from the human's** yet is equally valid — zero
capability failures. This is a pilot (n=3 of 15 seeds); scale-up is the next
phase.

## 2. What the pipeline is (four stages, asset status)

| Stage | Mechanism | Status |
|---|---|---|
| **Detect** | build oracle = **only truth**; theory-DAG scopes blast-radius + repair order; static LLM localizer = optional triage only (measured fileP/R 0.22/0.38 — never a gate) | pieces built; entrypoint + multi-file RED discovery not yet assembled |
| **Repair** | `repair_driver_tree.py` v4.1: claude -p closed loop (propose SEARCH/REPLACE → tree-apply → session build → feed error back), cumulative rounds, full token/cost accounting | works, single-file |
| **Validate** | session build (sound oracle) + anti-cheat gate (no sorry / no weakened statement / consumers green) + quality vector | works |
| **Evaluate** | commit-pair replay corpus (15 seeds) + forced/chosen adjudication + agent-vs-human quality comparison | works |

**Division of labor (forced by data, see §5):** the build oracle locates and
judges; the DAG only bounds/orders; the LLM does only the semantic work
(interpret the break, evolve statements, invent helpers, draft the fix).

## 3. Substrate: the corpus

- l4v full history recovered (5706 commits, 2014–2024). Repair-distribution
  survey (`survey-l4v-repair-distribution.md`, 694-commit window):
  **52%** of commits touch `proof/`; artifact+proof in ONE commit is only
  **4.5%** — the real (change,repair) unit is a **commit PAIR** (76% of
  artifact-only commits are followed by a proof-fix within 3 commits);
  **86%** of proof-touching commits are single-session; dominant repair site
  **CRefine > Refine > AInvs**; triggers are **abstract/design/haskell**, not
  raw C.
- 15 co-change L1 seeds extracted deterministically (`case_extract.py`): each
  = artifact-Δ (trigger) + held-out human proof-fix (`C_p`) + per-lemma
  ground truth. Human-response mix across seeds: **35 added / 27 body-only /
  13 stmt-evolved / 14 deleted** — the dominant human action is ADD-helper +
  evolve-statement, NOT re-prove-body. 11/15 involve statement evolution →
  validates the fork-① decision to let the agent evolve statements freely.

## 4. Key measured results

### 4.1 Localization cannot be the LLM's job (why the oracle-centric design)
Static LLM localizer over 15 seeds: **fileP=0.22, fileR=0.38, evoAcc=0.57**;
worse, the agent's "this proof survives" judgment was **build-refuted 2/2**
even when its reasoning cited exact line numbers. Conclusion: detection is
statically undecidable and there is a free exact oracle (the build) — the
agent is re-scoped to semantic repair only.

### 4.2 Forced vs chosen ground-truth cleaning (a method contribution)
"Human fix = correct answer" conflates **compiler-forced** edits with
**maintainer-chosen** ones (forward refactors, symbolization, style). The
build oracle splits them: build-RED = forced; human-fix ∖ forced = chosen.
Adjudicated 4 seeds — **3 forced** (df5e1611, 83ddb4def, 0e8048b49) + **1
pure chosen** (8f6373c7e: the human-edited file build-passes untouched).
No prior repair work build-cleans its ground truth this way.

### 4.3 Repair scorecard — 3/3, all divergent-yet-valid
| seed | break shape | rounds | vs human |
|---|---|---|---|
| df5e1611 | crunches + decl-order relocation | 1 | isomorphic, smaller; **session-green validated** |
| 83ddb4def | obsolete-fact repair | 2 | **divergent & stronger** — kept a fact the human DELETED |
| 0e8048b49 | boundary flip → statement evolution + **new helper** | 3 eff. | same insertion point as human's helper, **different mathematics** |

The 0e8048b49 trajectory reproduces the full human mental sequence: evolve
statement to track semantics → discover a missing fact → **invent that fact**
→ fix plumbing → green.

### 4.4 Quality vs human (`quality-comparison.json`)
| seed | minimality (+lines) | fragility (searchy/named) | verdict |
|---|---|---|---|
| df5e1611 | human 25 / **agent 22** | identical 3/4 | ~tie (human's extra = comment+symbolization = chosen) |
| 83ddb4def | **human 0** (delete) / agent 4 | 0/0 vs 1/0 | trade-off: minimality-human, strength-agent |
| 0e8048b49 | human 10 / agent 11 | human 1/2 / **agent 3/1** | ~size-tie, human less fragile |
Net: agent repairs tie human on correctness+size, sometimes stronger on
fact-preservation, slightly behind on style discipline (comments, low-fragility
discharge). "LLM better OR worse" is refuted — the honest answer is a
multi-axis vector with no single winner.

## 5. Why the results are trustworthy (method spine)

1. **Build is the only oracle.** DAG/localizer are heuristics; every verdict
   is a real session build. Discovered the hard way: `check-theory --patch` is
   UNSOUND for `arch_global_naming` theories (no-op patch → false RED,
   probe-proven) → sound oracle = tree-apply + session build.
2. **Sound oracle everywhere.** Repairs write real file content (no patch
   format → the coordinate/format bug class is gone), build the full session,
   revert. A single mis-anchored patch once produced a *false* GREEN that even
   a session build validated — caught by a routine applied-patch text audit,
   now a standing gate step.
3. **Divergence = anti-memorization evidence.** All 3 fixes differ from the
   public `C_p` while restoring green — stronger than any decontamination
   protocol for the un-diverged case. (Headline success-rate claims still keep
   temporal/perturbation controls; single non-diverged samples need them.)
4. **13-item harness taxonomy.** Every failure was infrastructure (argv limit,
   patch-parser `---`, stdout SIGPIPE truncation, ephemeral heap, cross-machine
   script drift, --patch unsoundness, …), each turned into a deterministic
   fix. Zero failures attributable to proof reasoning.

## 6. Metric registry (evaluation section)

| # | Metric | Status |
|---|---|---|
| M1 | restore-green rate (per shape/level) | live 3/3 |
| M2 | end-to-end wall + LLM cost/tokens/rounds | live (v4.1 accounting) |
| M3 | quality-vs-human vector (minimality/strength/fragility/fan-in) | live |
| M4 | downstream session-green rate post-repair | 1/3 done, 2 queued |
| M5 | detect efficiency (DAG upper bound vs actual RED, e.g. 67→1) | data exists, unaggregated |
| M6 | **autonomy rate + human-review queue** (the "semi" measure) | to add |
| M7 | **fragility-validation regression** (searchy-ratio vs did-it-break under real Δ) — upgrades the fragility proxy from lore to validated metric | spec'd; needs batch adjudication |
| M8 | repair latency vs human commit-lag (C_a→C_p) | cheap, to add |

**On the fragility metric (M3/M7):** it counts search-heavy tactics
(`auto/fastforce/blast/clarsimp/metis/smt/simp add:`) vs named
(`rule/erule/…/simp only:`) in the ADDED lines. Concept = proof-engineering
lore (QED-at-Large brittleness; l4v style discourages fragile automation) but
**no prior work quantifies it against real breakage** — M7 does exactly that
using our corpus's break/survive labels, upgrading it from intuition to a
validated (or refuted) predictor.

## 7. Paper framing & structural gaps

**Framing: a semi-tooled pipeline paper.** Contribution = the
detect→repair→validate pipeline + the history-corpus validation methodology
(forced/chosen cleaning, quality vector, divergence evidence). Capability/
quality are EVALUATION, not the headline — so we do not compete with
synthesis SOTA (Stepwise 77.6%) on model power. **"Semi" is a feature:** the
forced/chosen split IS the automation boundary (forced = auto-repair; chosen =
suggest + human-review) — M6 measures it.

**Gaps to the tool — ALL CLOSED + e2e-validated (2026-07-06):** the four
assembly gaps (entrypoint coevolve_pipeline.sh; detect-from-build RED
discovery; report assembler; M6 autonomy accounting) are built and proven by a
first uninterrupted full run: --seed df5e1611 -> SESSION-GREEN, autonomy 1.0,
1 round, $1.48, 39min (README milestone). The pipeline IS the tool; the
single-session L1 path is validated end to end. Remaining: artifact-diff
deployment mode (currently seed mode) and multi-file/cross-session paths
(coded, untested at scale) — these fall out of batch scale-up, not new research.

## 8. Next phase (one path serves both tooling and statistics)

Implement gap-2 (build→RED discovery) → gap-1 (chain) → **batch-run the 12
remaining seeds through the pipeline** (feeds M1–M8 AND M7's break/survive
labels at once) → assemble gaps 3–4 alongside. This simultaneously matures the
tool and turns the 3/3 pilot into a statistically meaningful N/M table.

## 9. Reproduce

Server B, branch `coevolve`, dir `coevolve/`:
`scripts/case_extract.py` (seed extraction) · `scripts/adjudicate.py`
(forced/chosen) · `scripts/repair_driver_tree.py` (repair, v4.1) ·
`scripts/quality_metrics.py` (quality vector) · `scripts/gt_distribution.py`
(L1/L2 distribution) · `cases/<hash>/` (per-seed bundles: Δ, C_p, ground
truth, adjudication, repair transcripts, quality). AARCH64 heap in isolated
home; sound oracle = tree-apply + `isabelle build AInvs`. Connection params in
repo `.env` (`$SSH_B`).
