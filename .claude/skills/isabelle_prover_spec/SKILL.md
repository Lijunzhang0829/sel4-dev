---
name: isabelle-prover-spec
description: "Strengthen seL4 Abstract Spec postconditions and invariant-abstract guarantees. Use when adding stronger statements to spec/abstract/ or proof/invariant-abstract/."
---

# Spec type — strengthen the specification

This sub-skill strengthens **statements about** existing kernel
operations — not the definitions themselves. The goal isn't faster
build; it's a specification that catches more real bugs, exposes more
usable postconditions, and removes redundant noise.

Inherits the common contract from `isabelle_prover` (5 hard rules,
common tools, patch format, session mapping). The text below is
structured **theory first, then patterns, then workflow** — that order
matters: Patterns are instances of the Taxonomy, not first-class
concepts.

## §1 — What is a Stronger Spec?

For a Hoare triple `⟨P_old⟩ f ⟨Q_old⟩`, a new triple `⟨P_new⟩ f ⟨Q_new⟩`
is **strictly stronger** iff both:

```
  P_old  ⟹  P_new     (new accepts every old precondition; pre is weaker)
  Q_new  ⟹  Q_old     (new yields every old postcondition fact; post is stronger)
```

Equivalent statement: any consumer that used `_old` can substitute
`_new` and still get what they needed.

This is mechanically witnessed in Step 4.5 (Derivability check) by
constructing an aux lemma `<name>_old` whose body uses `<name>_new`
plus a monotonicity rule. If derivability fails to construct, the
change is **not a strengthening** — it's an alternative statement.

The §10 anti-pattern boundary (no spec-definition changes here) is
unchanged.

## §2 — Strengthening Taxonomy

The taxonomy classifies WHAT changed; Patterns (§3) classify HOW we
found the opportunity. Acceptance gates attach to the taxonomy tier,
not to the Pattern.

### Tier 1 — Logical Strengthening

The new statement is logically stronger. Information content changes.

| Move | Direction |
|---|---|
| **Premise Weakening** | `P_old ⟹ P_new`, `Q_old = Q_new` |
| **Postcondition Strengthening** | `P_old = P_new`, `Q_new ⟹ Q_old` |
| **Monotone Strengthening** | both directions strictly |

**Derivability check (§4.5) is mandatory.** A Tier 1 change without a
working derivability witness is not actually a strengthening.

### Tier 2 — Automation Strengthening

The statement is **additive** — a brand-new lemma whose existence
doesn't logically subsume any old lemma. Information is reorganized;
wp surface area grows. Example: a compound `op_invs` lemma that
composes existing per-component preservation lemmas.

**Derivability check (§4.5) is SKIPPED** — there is no `_old` form
that the new lemma replaces. Verified by Exp 2 (2026-05-31):
building-block lemmas with weak preconditions and compound `_invs`
with strong preconditions are mutually independent surfaces; neither
derives the other.

### Tier 3 — Packaging Strengthening

Knowledge reorganization, **not a true strengthening**. Example: a
weak-form alias whose body is `(strengthen <weak>, wp)` — purely a
unification helper.

**Pattern A is here, not in Tier 1.** Promote to Tier 1 only when
consumer analysis proves the weak form has no users. Otherwise the
move is API cleanup at most.

### Why this matters operationally

The §4.5 gate, §6 acceptance criteria, and §5 ROI weighting all key
off this tier label. Misclassifying a change (e.g. treating a
Pattern A "dedup" as Tier 1) makes the gate misfire.

## §3 — Pattern Index (positional heuristics)

Patterns are *how the scanner finds the opportunity*. Each maps to a
Tier so the workflow knows which gates apply.

| Pattern | Tier | What it finds | Detector |
|---|---|---|---|
| **C** Premise removal | T1 pre-weak | `valid_objs`/`invs`/`sym_refs` conjunct in precondition that proof body doesn't actually use | `spec_strengthen_scan.py --pattern C` |
| **B** Functional postcond | T1 post-strg | `set_X v` op has only preservation lemmas, no `⟨⊤⟩ set_X v ⟨λ_ s. X s = v⟩` | `spec_strengthen_scan.py --pattern B` |
| **D** Loose bound → exact | T1 post-strg | `≤` / `⊆` in postcond where operation forces `=` | manual |
| **G** Frame preservation | T1 post-strg | Op doesn't touch accessor X, but lemma `⟨λs. P (X s)⟩ op _ ⟨λ_ s. P (X s)⟩` is missing | manual; future scanner |
| **E** Compound `_invs` | T2 auto | Op has many `op_<inv>` preservation lemmas, no compound `op_invs` | `spec_coverage_matrix.py` (Pattern E preflight) |
| **A** Paired weak/strong | T3 pkg | Two lemmas about same op with `Q' ⟹ Q` chain; weak may be redundant | `spec_strengthen_scan.py --pattern A` |

### Pattern F note (state delta) — empirically rejected

User-proposed Pattern F was: combine `changed-fact ∧ unchanged-facts`
in one triple. Exp 3 (2026-05-31) confirmed l4v idiom keeps these
**decomposed** as B (one functional lemma) + G (one+ frame lemma) per
accessor. Theoretically same family; ergonomically separate. The
SKILL keeps B and G distinct.

### ⚠ Pattern A false-positive warning (Tier 3)

Scanner-flagged Pattern A pairs are wrong ~70-80% of the time. Two
failure modes:

1. **Wrong direction** — the "weak" lemma is the granular building
   block, the "strong" lemma is a compound built FROM it. Deleting
   building blocks breaks the compound.
2. **Cross-op incomparable preconditions** — two lemmas share a
   Hoare-body identifier but have incomparable preconditions
   (`valid_pspace` vs `valid_objs`+extras). Not substitutable.

Always read both lemmas + grep cross-file usages before acting on A.
Pattern A is the LAST pattern to try.

### ⚠ Pattern E preflight checklist (Tier 2)

Before attempting `op_invs`:

1. Does `op_invs_minor`, `op_invs_<variant>`, or `op_<state>_invs`
   already exist? Check `grep '^lemma op.*invs' verification/l4v/proof/`.
   If yes, skip — the gap is filled.
2. Does `op` have a `[wp]` preservation rule for **every** component
   of `invs`? Roughly 25 components including arch-specific ones
   (`valid_arch_state`, `valid_machine_state`, `valid_vspace_objs`,
   `valid_global_refs`, `valid_irq_handlers`,
   `pspace_in_kernel_window`, …). Missing any single component blocks
   the compound proof. Verified by `set_cdt_invs` failure (Exp 2,
   2026-05-26 log).

## §4 — Targeting (read the candidates file)

```
python3 tools/critical_path/rank_candidates.py --limit 30 \
  --out reports/spec-strengthen/candidates-<YYYYMMDD>.md
```

The candidates file is grouped by Tier order (T1: C → G → D → B; T2: E;
T3: A) and within each group ranked by **ROI** = consumer_count ×
tier_weight, with `[done]` deduplication against past
`reports/spec-strengthen/AInvs-*.md` logs.

### ROI weights (rank_candidates.py default)

```
pre-weak (C)            = 5
monotone-strengthen     = 5
frame preservation (G)  = 4
state-delta (B / D)     = 3
compound (E)            = 2
packaging (A)           = 1
```

Weights are constants at the head of `rank_candidates.py`; move them
to `tools/critical_path/roi_weights.json` if more tuning becomes
needed.

### Scanner reliability (calibration from 2026-05-25/26/30 sessions)

| Pattern | Scanner accuracy | Recommended validation |
|---|---|---|
| C | ~50% (premise may be load-bearing) | `check-theory.sh --patch` ~60s |
| B | ~95% detect, low downstream ROI | Manual review of consumer proofs |
| A | ~20-30% real | Read both lemmas + grep cross-file |
| G | scanner doesn't detect; manual only | Inspect op definition vs missing accessor frames |
| E | preflight matrix gates | `spec_coverage_matrix.py` |

The scanner ranks; the human judges; `check-theory.sh` is the only
acceptance gate. Treat scanner output as a work queue, not a verdict.

## §5 — Workflow (per candidate)

### Step 1 — pick from candidates file

Open the dated candidates file. Default = top un-done T1 entry. Read
±20 lines around `file:line`.

For Pattern A: also `grep -rln 'name' proof/` on both names; confirm
the pairing is real and direction is right (see §3 A warning).

For Pattern E: run the preflight checklist (§3 E warning) FIRST.

### Step 2 — draft the patch

Patch goes under `logs/spec-strengthen-<file>-<pattern>-<YYYYMMDD>.patch`.
Format described in `isabelle_prover` SKILL.md.

| Pattern | Move |
|---|---|
| C | Edit lemma; remove suspect conjunct from precondition. Proof body usually unchanged. |
| B | Insert new lemma below the existing preservation lemma. |
| D | Replace `≤` / `\<subseteq>` with `=` in postcondition. |
| G | Insert frame lemma `⟨λs. P (acc s)⟩ op _ ⟨λ_ s. P (acc s)⟩` next to existing `op_*` lemmas. |
| E | Add compound lemma below the last preservation lemma. |
| A | Inline strong form, OR tag weak `[wp del]` (conservative), OR delete weak (only if `grep` confirmed no cross-file users). |

### Step 2.5 — REPL Preflight (Pattern C only, currently grep-based)

**Why**: Pattern C false-positives waste ~60s per `check-theory.sh
--patch` cycle. A static analysis can predict load-bearing premises.

**Initial heuristic (current)**: grep the lemma's proof body for the
target premise name. If `valid_objs` appears as an argument to any
`wp`/`rule`/`dest`/`elim` tactic, the premise is likely load-bearing.
If it doesn't appear at all, premise is likely-removable.

**Future enhancement** (verified feasible — Exp 4b, 2026-05-31):
connect to Isa-REPL via `tools/seL4-proof-search/Isa-Repl/`, init the
target `.thy` file, step through the existing proof, and capture the
subgoal at each step. If `valid_objs` appears in no subgoal of any
step, it's strongly removable. The `_compile(snippet)` API hit
"context too complex" for AInvs — production integration needs
`_step()` over the existing theory, not isolated `_compile` of a
fragment. Effort estimate: ~200 lines wrapping
`tools/seL4-proof-search/Isa-Repl/run.sh` + py4j client.

This step is **optional today, mandatory once the REPL wrapper
lands**.

### Step 3 — verify in isolation

```
bash $ISA_SCRIPTS/check-theory.sh <file.thy> <session>                  # baseline
bash $ISA_SCRIPTS/check-theory.sh <file.thy> <session> --patch <patch>  # trial
```

Patch must reach `OK`. **No `sorry` / `oops` / `axiomatization`.**

Wall times: baseline/trial differ ±5% from noise (JVM, disk cache).
Single-run timing is informational only.

### Step 4 — measure impact (MANDATORY)

```
python3 tools/critical_path/spec_impact.py <patch> <file.thy> \
  --baseline-wall <ms> --trial-wall <ms> \
  --tree verification/l4v/proof \
  --append-to reports/spec-strengthen/<session>-<YYYYMMDD>.md
```

Report auto-appends to the dated strengthen-log. Hard gates:

| Gate | Required |
|---|---|
| No `weakening` verdict | HARD STOP |
| ≥ 1 lemma with `premise-weaken` / `monotone-strengthen` / `postcond-strengthen` / `additive` | yes |
| Trial wall ≤ baseline × 1.30 | yes (noise tolerance) |

**Verdict → Tier mapping (drives §4.5 gate applicability):**

| Verdict | Tier | Tactic for §4.5 |
|---|---|---|
| `premise-weaken` | T1 | per §4.5 table |
| `monotone-strengthen` | T1 | per §4.5 table |
| `postcond-strengthen` | T1 | per §4.5 table |
| `additive` | T2 | **SKIP §4.5** (no old to derive) |
| `rewrite` | manual | escalate to human |
| `removal` | T3 | manual derivability proof (trivial) |
| `weakening` | — | **abandon** |

### Step 4.5 — Derivability check (Tier 1 only)

A Tier 1 strengthening is only consumable in place of the old form if
A_new ⟹ A_old can be machine-proven. Construct a `<name>_old` aux
lemma whose statement is the **verbatim old triple** and whose body
uses the new lemma + monotonicity. Place the aux *immediately after*
the strengthened lemma in the patch so it's verified in the same
`check-theory.sh --patch` pass.

Tactic table (use the column matching the triple shape):

| Triple shape | Old → New | Derivability tactic |
|---|---|---|
| Plain valid `⟨P⟩ f ⟨Q⟩` | premise weakened | `by (rule hoare_weaken_pre[OF <new>]) simp` |
| ValidE `⟨P⟩ f ⟨Q⟩,⟨E⟩` | premise weakened | `by (rule hoare_pre[OF <new>]) simp` |
| ValidE_R `⟨P⟩ f ⟨Q⟩,-` | premise weakened | `by (rule hoare_pre[OF <new>]) simp` |
| Plain valid | post strengthened | `by (rule hoare_strengthen_post[OF <new>]) <Q_new→Q_old rule>` |
| ValidE_R | post strengthened | `by (rule hoare_strengthen_postE_R[OF <new>]) <rule>` |
| Plain valid | `≤` → `=` (D) | `by (rule hoare_strengthen_post[OF <new>]) simp` |

**`hoare_pre` vs `hoare_weaken_pre` distinction matters** (Exp 1,
2026-05-31): l4v's `hoare_pre` is the validE version; using it on a
plain valid triple errors with "type mismatch". Use the table.

Acceptance: the patch with appended `<name>_old` must `check-theory.sh`
to OK. Cost is ~+9% wall vs the patch alone (one extra simple
proof). Verified on `unbind_maybe_notification_not_bound`:
baseline 41s, with aux 45s — well within noise.

### Step 5 — apply

Both §4 and §4.5 (if Tier 1) must have passed.

```
bash $ISA_SCRIPTS/check-theory.sh <file.thy> <session> --apply <patch>
```

`--apply` writes the source file but does NOT rebuild the session heap.
Downstream wp-engine work reduction (the real ROI) materializes only
after the next full `isabelle build`. Don't measure single-patch
downstream impact — batch 5-10 strengthenings, then one session
rebuild, then compare session-total wall vs the pre-batch baseline.

After `--apply`, prepend one short paragraph to the auto-appended impact
block in the strengthen-log:
- Pattern letter, Tier
- One sentence on what the strengthening enables downstream
- Session rebuild status (default: not yet)

The `<name>_old` aux lemma stays in source as **permanent witness of
strengthening**. Don't delete it later.

### Step 6 — PR construction (parent SKILL rule 5)

Every accepted patch produces a per-experiment record under
`reports/experiments/<NNNN>-<short-name>/`. Skeleton at
[`reports/experiments/_template/`](../../../reports/experiments/_template/):

| File | Content |
|---|---|
| `patch.diff` | The exact source change (output of `git -C verification/l4v diff <file>`). Includes the `_old` witness lemma for Tier 1 changes. |
| `derivability.thy` | Standalone snippet of the `<name>_old` lemma. Skipped for Tier 2 patches. |
| `command.sh` | The exact `check-theory.sh --patch` / `--apply` / `spec_impact.py` invocation. Re-runnable. |
| `measurement.json` | `{"baseline_wall_ms": …, "trial_wall_ms": …, "delta_pct": …, "baseline_ref": "reports/golden-baseline/walls.json#<session>", "tier": "T1\|T2\|T3", "consumers": …, "verdict": "…", "strength_score": …}` |

PR template at
[`.github/PULL_REQUEST_TEMPLATE/spec-strengthen.md`](../../../.github/PULL_REQUEST_TEMPLATE/spec-strengthen.md).

**Bundling rule**: one PR may cover multiple experiments if all are in
the same session and the batch is logically coherent (e.g. "all
`cte_at → real_cte_at` ports").

## §6 — Acceptance Gates

Apply iff **all** of the following hold:

1. **Step 3 — `check-theory.sh --patch` returns `OK`** on the
   strengthened lemma.
2. **Step 4 — impact verdict** is `premise-weaken`,
   `monotone-strengthen`, `postcond-strengthen`, or `additive`. **No
   `weakening` verdict.**
3. **Step 4.5 — derivability check passes** (Tier 1 only; skip for
   T2 additive lemmas).
4. Statement is strictly more informative (matches §1 definition).
5. The 5 hard rules from `isabelle_prover` are inherited.

**Semantic Strength Score (SSS) is NOT a gate.** It's a ranking
metric reported by `spec_impact.py` to drive the candidate file's
sort order. SSS = `#removed_premises + #added_post_facts + 0.5 ×
#added_frame_facts`. A high SSS without a passing verdict means
nothing; a verdict with low SSS still passes if all gates do.

Spec strengthening trades build time for verification strength —
slightly slower build with stronger spec is the intended direction.

## §7 — Tools

| Tool | Status | Purpose |
|---|---|---|
| `$ISA_SCRIPTS/check-theory.sh` | ✓ existing | Only verification gate. |
| `$ISA_SCRIPTS/goal-at.sh` | ✓ existing | Inspect proof state at line. |
| `$ISA_SCRIPTS/sledgehammer.sh` | ✓ existing | Find `by (metis …)` reconstruction. |
| `tools/critical_path/spec_strengthen_scan.py` | new | Pattern A/B/C scanner. |
| `tools/critical_path/spec_impact.py` | new | MANDATORY Step 4. Verdict + SSS. |
| `tools/critical_path/rank_candidates.py` | new | Ranked candidates file via ROI = consumers × tier_weight. |
| `tools/critical_path/spec_coverage_matrix.py` | future | Pattern E preflight matrix. |
| `tools/critical_path/repl_premise_probe.py` | TODO | Step 2.5 REPL-driven preflight; see §5 Step 2.5. |
| `verification/l4v/tools/proofcount/` (upstream) | ✓ existing | Heavyweight ground-truth metrics (full session rebuild). |

## §8 — References (read on demand)

| When | Read |
|---|---|
| Current candidates | [`reports/spec-strengthen/candidates-<YYYYMMDD>.md`](../../../reports/spec-strengthen/) |
| Worked case studies per pattern | [`references/spec-strengthen-patterns.md`](../isabelle_prover/references/spec-strengthen-patterns.md) |
| Session dependency map | [`references/spec-downstream-map.md`](../isabelle_prover/references/spec-downstream-map.md) |
| Refinement (corres/ccorres) | [`references/refinement-proofs.md`](../isabelle_prover/references/refinement-proofs.md) |
| Past strengthen logs (precedents) | [`reports/spec-strengthen/AInvs-*.md`](../../../reports/spec-strengthen/) |

## §9 — Experiment Calibration (2026-05-31)

| Exp | Question | Result | SKILL impact |
|---|---|---|---|
| 1 | Pattern C derivability cost mandatory feasible? | ✓ +9% wall on `unbind_maybe_notification_not_bound` | §4.5 mandatory for T1, tactic table by triple shape |
| 2 | Pattern E derivability proves _component from compound? | ✗ Building blocks NOT derivable from compound — mutually independent surfaces | §4.5 SKIPPED for T2; Pattern E lives in Tier 2, not Tier 1 |
| 3 | Is Pattern F (composite changed ∧ unchanged) used in l4v? | ✗ Rarely; idiom decomposes into separate B and G lemmas | Keep B and G distinct; Pattern F not added |
| 4a | Is Pattern G a real gap? | ✓ `set_cdt_machine_state[wp]` is genuinely missing and verifies | Pattern G is a first-class Tier 1 entry |
| 4b | REPL preflight feasible? | ✓ Partial: init/load works; `_compile(snippet)` errors on complex sessions; `_step()` path TBD | §5 Step 2.5: future REPL path scoped, current uses grep heuristic |

## §10 — Anti-pattern (unchanged from prior versions)

If the strengthening involves changing the **definition** of a kernel
operation in `spec/abstract/**`, you're no longer strengthening —
you're modifying observable kernel behaviour. That belongs to the
`isabelle_prover_haskell` or `isabelle_prover_c` flow, not here. This
sub-skill changes **statements about** existing definitions only.

The only exception: adding a *derived* `definition` (e.g. naming an
expression that already appears unfolded throughout the file) is fine
— it's a renaming, not a behaviour change.
