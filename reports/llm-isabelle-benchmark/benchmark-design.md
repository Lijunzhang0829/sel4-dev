# seL4/l4v as a benchmark substrate for LLM incremental improvement on Isabelle/HOL

Branch: `bridge-consumer-trace`. Date: 2026-06-16. Status: design draft v1.

## 0. Reframe & scope

**Positioning shift.** The project is NO LONGER "make seL4 better." It is: *use
seL4/l4v as a proving ground to measure how well LLMs perform **incremental
improvement** tasks on a large, real-world Isabelle/HOL corpus.*

**This is:** a benchmark + harness for LLM capability on synthesis / repair /
strengthening / shortening / generalization of Isabelle proofs and statements,
graded by a sound oracle.

**This is NOT:** about build wall time, about whether a change helps the kernel,
or about whether an added lemma has downstream consumers. All of that
("usefulness") is **out of scope** under this framing.

**Consequence for prior session work.** The bridge cost-economics (re-establishment
labor, blast radius, consumer-first lemmas, wall) becomes out of scope. What
survives and is reused: the sound-kernel gates (→ anti-gaming rules), the failure
taxonomy (→ difficulty strata + perturbation operators), `check-theory.sh` (→
oracle), and the literature gap (→ novelty claim). The `bridge-consumer-trace`
experiment specifically becomes irrelevant here (it asked "is the lemma useful").

## 1. Core principle: drop usefulness, keep soundness

The single load-bearing idea. "Does the lemma 奏效" splits in two:

| Meaning | In this benchmark | Why |
|---|---|---|
| **useful** (has consumers, keeps the bridge green, helps the kernel) | **DROP** | a kernel-improvement concern, orthogonal to capability |
| **sound / valid** (kernel accepts it AND it is a legitimate instance of the task) | **KEEP — non-negotiable** | the sound LCF kernel is the only free, ungameable label |

If "produce something the kernel accepts" were the bar, `lemma foo: "True" by simp`
scores full marks. The benchmark's entire value is the **sound oracle**
(`check-theory.sh`) **plus a pinned target** so the LLM cannot win by weakening.
"奏效" therefore becomes a **machine-checkable formal predicate**, not a judgment.

## 2. The oracle & the anti-gaming rule set

**Oracle.** `check-theory.sh <file> <session> --patch` — single-file, sound LCF
gate. Every task's success predicate is a conjunction of oracle calls + cheap
syntactic checks. **No LLM-as-judge anywhere in grading.**

**Anti-gaming rules (enforced mechanically on every submission):**
1. **No admits**: grep-reject any new `sorry` / `oops` / `axiomatization` / `cheat`.
2. **Statement pinning**: for synthesis/repair/shorten, the lemma *statement* must
   be AST-identical to the target. For strengthen/generalize, the statement may
   change but only via a harness-generated, kernel-checked relation obligation.
3. **No narrowing**: a "strengthening" must be *strictly* stronger — checked by an
   obligation the **harness** (not the LLM) emits and `check-theory` discharges.
4. **Auxiliary obligations are harness-authored**: the LLM never writes its own
   success criterion.

## 3. The four surfaces × task families × success criteria

Surfaces reuse the old skill's spec/proof/haskell/C split, but the **axis changes
from "wall optimization" to "incremental-improvement task with a kernel-checkable
success predicate."** Notation: a lemma `L : ⦃P⦄ f ⦃Q⦄`; `⊢ X` means "check-theory
green on X". Contamination risk noted per task (see §4).

### 3.1 SPEC surface — `spec/abstract/`, `proof/invariant-abstract/` (session AInvs/ASpec; NO bridge)

| Task | Ask | Success predicate (all machine-checked) | Contam. |
|---|---|---|---|
| **SP1 strengthen-postcondition** | given `L`, produce `Q'` | `⊢ (Q' ⟹ Q)` ∧ `⊢ ⦃P⦄ f ⦃Q'⦄` ∧ `¬⊢(Q ⟹ Q')` (strictly stronger) ∧ `Q'≢Q` | **LOW** — answer is *beyond* current l4v, not in corpus |
| **SP2 invariant-discover** | propose `I` preserved by op `g` | `⊢ ⦃I⦄ g ⦃I⦄` ∧ `I` not implied by existing invariants (harness obligation) | LOW-MED |

Cheapest surface (self-contained on the abstract spec, no refinement chain). Good
warm-up tier. SP1's "beat the existing lemma" framing is **inherently
contamination-resistant**: a strictly-stronger statement cannot be memorized.

### 3.2 PROOF surface — `proof/**/*.thy` (single session; NO bridge)

This surface was **miscategorized** in the old skill as "build-wall static-ization."
Recast as capability probes:

| Task | Ask | Success predicate | Contam. |
|---|---|---|---|
| **PR1 synthesis** | hide proof of existing `L`, reprove | `⊢ L` ∧ statement identical | **HIGH** — proof is public; use only as a *memorization-baseline* |
| **PR2 repair** | harness perturbs context → `L` breaks → fix | `⊢ L` ∧ statement identical | LOW-MED — perturbation is harness-authored |
| **PR3 shorten** | shorter proof of same `L` | `⊢ L` ∧ statement identical ∧ steps < original | **LOW** — beating the proof is not in corpus |
| **PR4 de-automate** | replace an automation-tactic span with explicit static tactics | `⊢ L` ∧ statement identical ∧ no automation tokens (`auto/simp/blast/…`) in span | LOW |

PR4 is the old falsified wall-optimization task **reborn as a valid capability
probe** — it has no wall payoff but is a clean measurable skill.

### 3.3 HASKELL surface — `spec/haskell/` → Refine + CRefine (THE BRIDGE)

The unique, novel, contamination-resistant family. The bridge is repurposed from
"cost to pay" to **task generator**.

| Task | Ask | Success predicate | Contam. |
|---|---|---|---|
| **HK1 refinement-repair after design change** | harness applies a Haskell diff, re-translates, `corres` (and ccorres) break → repair | affected `*_corres` ⊢ green ∧ statements preserved (behavior-preserving diff) **or** match gold (behavior-changing) | **LOW** — diff is harness-authored |

Stratify by diff shape using the real `setThreadState` scenarios: reorder /
add-case / swap-sub-op / guard-change. "Given a C/Haskell diff, repair the broken
refinement proof" is **absent from every existing LLM-proof benchmark** (all are
greenfield math). This is the headline contribution.

### 3.4 C surface — kernel C → CRefine (THE BRIDGE)

| Task | Ask | Success predicate | Contam. |
|---|---|---|---|
| **C1 ccorres-repair after C refactor** | harness applies a behavior-preserving C diff (c-parser), `ccorres` breaks → repair | affected `*_ccorres` ⊢ green ∧ statement preserved | LOW |

Stratify by AutoCorres absorption tier (T1 re-search / T2 ctac-rewrite / T3
relation). **Out of scope:** the C→binary translation-validation step (SMT/infra,
not an LLM task) — noted only so it is explicitly excluded.

## 4. Task generation & contamination control (the critical methodology)

**l4v is public and almost certainly in LLM training data.** Without contamination
control this benchmark measures memorization, not capability, and is worthless to
an ML audience. Three task sources, in priority order:

1. **Harness-authored perturbation (primary).** Apply the failure-taxonomy
   operators to real l4v code to synthesize `(break, gold-fix)` pairs:
   *rename · insert-case · swap-sub-op · alter-precond · alter-relation.* The
   perturbation and gold are generated by us → not in any training set.
2. **"Beat the corpus" tasks (inherently clean).** SP1 strengthen, PR3 shorten,
   generalization — the target *does not exist* in l4v, so it cannot be recalled.
3. **Git-history `(break, fix)` pairs (secondary).** Real fixup commits — realistic
   but public; use to corroborate, not headline.

**Mandatory control metric:** the **memorization gap** = `pass@k(verbatim-existing)
− pass@k(perturbed/novel)`. A large gap means contamination dominates; the gap
itself must be reported.

## 5. Difficulty stratification

- **By failure shape** (repair tasks): the 5 taxonomy types (rename → insert-case →
  swap-sub-op → alter-precond → alter-relation), increasing structural depth;
  type-5 (relation) is the *abstention* class (correct answer may be "unrepairable
  locally / escalate").
- **By size** (Matichuk ICSE'15 quadratic statement→proof law): bucket by statement
  size and original proof size — a principled hardness axis.
- **By surface / bridge span**: spec (1 session) < proof (1 session) < C (CRefine) <
  haskell (Refine+CRefine). Bridge tasks score highest difficulty.

## 6. Metrics & baselines

**Metrics:**
- **Primary:** `pass@k` (sound-green ∧ valid) per (surface, task-family, stratum).
- **Memorization gap** (§4) — the contamination control, reported always.
- **Abstention correctness** — on type-5/unrepairable tasks, did the model correctly
  decline rather than emit a weakened "fix"?
- **Improvement magnitude** — shorten: step-compression ratio; strengthen: a
  coarse strength proxy (e.g., how many existing call-sites the stronger lemma
  also discharges — kept as a *secondary* signal, NOT a usefulness gate).

**Baselines (must beat to claim capability):** `sledgehammer` alone; re-run the
original tactic; `try0`/`auto`; the project's `ab_agent` (internal 14% synthesis
baseline); a portable off-the-shelf LLM prover (Baldur/DSP-style) where feasible.

## 7. Reused assets / harness

- **Oracle:** `check-theory.sh` (+ `--patch`), the only grader.
- **Fast inner loop:** IsaREPL / IsarLite (per-line timing, no full-session
  rebuild) for cheap iterate-and-check.
- **Synthesis baseline:** `ab_agent` (its 14% on real l4v is itself a data point,
  not a failure).
- **Perturbation operators & strata:** the failure taxonomy (this session).
- **Ledger:** reuse the `candidate-ledger.jsonl` event format for task/result
  provenance.

## 8. Validity threats (honest)

1. **Contamination (primary).** Mitigated by harness-authored perturbation +
   beat-the-corpus tasks + the memorization-gap control metric. Cannot be fully
   eliminated with a public corpus — must be measured, not assumed away.
2. **Synthetic vs real task realism.** Perturbations may not match the distribution
   of real human edits; corroborate with git-history pairs (source 3).
3. **Single-file oracle vs whole-session soundness.** A repair that greens one file
   may break downstream. For non-bridge tasks, scope success to the file. For
   bridge tasks (HK1/C1), success must run the **affected downstream set**, not
   just the edited file.
4. **Incompleteness of the strict-stronger check.** A truly-stronger `Q'` whose
   strength obligation is true-but-unproved by the harness would be wrongly failed;
   accepted as a task-design limitation (false negatives, never false positives).
5. **No LLM-as-judge** — keeps grading sound but means fuzzy "improvement quality"
   is only partially captured.

## 9. Phasing / first milestone

- **Phase 0 (cheap, no bridge):** build the harness — oracle wrapper + perturbation
  operators + ledger — and run **SP1 / PR2 / PR3 / PR4** on AInvs and one Refine
  file. Deliverable: first `pass@k` + **memorization-gap** numbers. Validates the
  whole methodology with zero heap-volatility risk.
- **Phase 1 (the headline):** add **HK1 / C1** bridge-repair on harness-authored
  Haskell/C diffs, stratified by failure shape. Deliverable: the first measured
  LLM performance on **refinement-proof repair after code change** — the literature
  gap this whole investigation identified.

## 10. One-line thesis

*Keep the sound kernel as a free, ungameable oracle; drop "usefulness"; redefine
"奏效" as a machine-checked predicate; turn the refinement bridge from a cost into
the one task generator no math benchmark can offer — and fight contamination as the
first-class design constraint.*
