# Proof-rewrite experiment log

Branch: `proof-optimization-lemma-inventory`
Run id: `proof-rewrite-20260503-1014`
Targets: top-3 slow lemmas in Refine session
- `cteInsert_corres` (302.9s aggregate, proof/refine/ARM/CSpace1_R.thy:5062)
- `cteMove_corres` (243.1s, proof/refine/ARM/CSpace_R.thy:737)
- `emptySlot_corres` (257.0s, proof/refine/ARM/Finalise_R.thy:1500)

Each experiment lifecycle:
1. **Hypothesis** — what's slow and why
2. **Edit** — patch description
3. **Validation** — `check-theory.sh --patch` outcome (must pass)
4. **Timing** — whole-file check-theory wall before/after
5. **Decision** — apply / revert / iterate
6. **Recorded** — `logs/attempts-<run_id>.jsonl` (auto) and below

Inventory diff is run after every applied edit:
- Statement-level: must show 0 `removed_lemmas`, 0 `new_sorry`
- `changed_statements` is allowed only for the lemma we explicitly retypped

---

## Experiment 1 — `cteInsert_corres` schematic-pattern inline (L5075)

**Hypothesis**: The `(is "corres _ (?P and ...) (?P' and ...) _ _")` pattern at L5075
costs 35.4 s in the Apr-30 heap-log. That's higher-order pattern unification —
Isabelle binding `?P`/`?P'` to subexpressions of a 200-line statement. The
schematic vars are reused at L5084-5089 (`corres_assert_assume`) and L5099-5104
(`corres_split[where r'=dc]`); inlining them eliminates the unification step
entirely and replaces the references with verbatim text.

**Risk**: Mechanical replacement — `?P` and `?P'` map to specific concrete
prefixes of the precondition. Wrong inlining would either change the proof
state shape (failure on a later `apply`) or change the lemma's elaborated form
(unlikely since `(is)` is purely metalanguage syntax). Mitigated by
check-theory.sh --patch.

**Patch**: [experiments/patches/exp1-cteInsert-schematic-inline.patch](../experiments/patches/exp1-cteInsert-schematic-inline.patch)
- Block 1: L5075 → blank (drop the `(is "...")` line)
- Block 2: L5084-5090 → inline `?P` and `?P'` in `corres_assert_assume`
- Block 3: L5099-5105 → inline `?P` and `?P'` in `corres_split[where r'=dc]`

**Result**: **NEGATIVE — pessimization, did not apply.**

| run | wall (s) | verdict | log |
|---|---:|---|---|
| baseline      | 952.2 | pass | `/tmp/baseline-csp1.log`, single-theory elapsed 103.7s, cpu 471.9s |
| exp1 --patch  | 975.9 | pass | `/tmp/exp1-patch.log` |
| **delta**     | **+23.7s (+2.5%)** | — | — |

**Lesson**: The 35.4s heap-log cost on L5075 was the _bind_ step for the
schematic vars `?P` / `?P'`. After binding, every reference at L5084-89 and
L5099-5104 just substitutes the bound term — cheap. Inlining replaced the
single bind with three textual occurrences that Isabelle re-elaborates from
scratch each time. **Net cost: +23.7s wall on a lemma that only had 35s of
"savings" available.** The schematic pattern was an optimisation, not a
liability; my hypothesis was inverted.

**Decision**: do not apply. Revert mental model: `(is "…")` is the right
default when the bound subterm is used ≥2 times.

**Implication for the other 49 `(is "…")` patterns in proof/refine/ARM/**:
do NOT batch-inline them. Most likely each is a similar bind/reuse trade-off.
Only candidate worth trying would be a `(is)` whose bound subterm is used
**only once** in the proof — but that's rare (people don't write `is` for
single-use, since there'd be no point).

---

## Methodological note after Exp 1

- Each whole-file check-theory pass on CSpace1_R.thy is ~16 min wall.
  3-5 experiments × 1 baseline + 1 patched run each = ~3-5 hours wall.
- Single-sample timings have ±2-3% noise (need 3+ runs to distinguish a real
  signal at this scale). Currently we trust direction-of-delta only when |Δ|
  exceeds a few percent.
- Hypothesis-driven proof rewriting is prone to **inverted assumptions** as
  in Exp 1. The heap-log "X took N seconds" does NOT imply "removing X saves N
  seconds" — it could be a load-bearing precomputation for many later steps.
- A more reliable next step is **measure-then-modify**: pick a slow command,
  apply a near-equivalent variant, re-time to confirm direction BEFORE
  committing.

---

---

## Experiment 2 — `cteMove_corres` L863 chained-OF split (REOPENED)

**Hypothesis**: The chained `frule(2) use_valid [OF _ updateCap_no_0, OF _ use_valid [OF _ updateCap_no_0]]` at L863 takes 51.0 s in the heap-log. The `[OF _ updateCap_no_0, OF _ use_valid [OF _ updateCap_no_0]]` is nested OF resolution; the inner `use_valid [OF _ updateCap_no_0]` substitutes its conclusion into the outer's third premise via higher-order unification — a known-slow Isabelle pattern.

**Edit**: Replace L863 with two `frule(1) use_valid [OF _ updateCap_no_0]` calls back-to-back. Each one handles one `updateCap` independently using the previous `no_0` as the precondition. Same final fact set, no nesting.

**Why this differs from Exp 1**: The schematic-pattern case (Exp 1) optimised by AVOIDING re-elaboration via a let-binding; inlining was a pessimization because it forced re-elaboration. The OF chain case is the opposite — each step does one logical step, and the chain forces *one* call to do *two* logical steps via nested unification, which is slower than two independent simple frules. So this rewrite goes WITH the grain of the unifier rather than against it.

**Risk**: Medium. Nuance is whether `frule(1)` picks the "next" updateCap in resolution order without re-using the one already handled at L862. If it picks the wrong one, downstream `apply (drule (N) ...)` may see a different premise pattern.

**Patch**: [experiments/patches/exp2-cteMove-OF-split.patch](../experiments/patches/exp2-cteMove-OF-split.patch)

**Result**: **NEGATIVE — proof broken, did not apply.**

| run | wall (s) | verdict | log |
|---|---:|---|---|
| baseline       | 254.6 | pass | `/tmp/baseline-csR.log` |
| exp2 --patch   | 200.9 | **fail** | `/tmp/exp2-patch.log` (errored at line 866 of patched theory) |

The patch-applied check stopped early (200.9 s) with `Exception- TOPLEVEL_ERROR raised` at the `apply` on patched-line 866 — that's the original L865 (`apply (drule (5) updateMDB_the_lot', elim conjE)`) shifted by the +1 line my patch inserted at L863. The error means the goal state after my two `frule(1) use_valid [OF _ updateCap_no_0]` calls has a different premise structure than the original chained `frule(2) use_valid [OF _ updateCap_no_0, OF _ use_valid [OF _ updateCap_no_0]]` produced — so the downstream `(drule (5) …)` no longer finds 5 matching premises.

**Lesson**: The chained `OF` is doing real semantic work (composing two `use_valid` instances into a single fact about a 2-step `updateCap` chain). Splitting it into two independent `frule` calls produces TWO new facts (intermediate + final no_0) instead of ONE — and the next tactic is brittle to that. A correct split would have to manually `thin_tac` the intermediate fact OR use `drule` to consume rather than preserve. Both are doable but require careful state-tracking that effectively re-derives the chained-OF semantics by hand.

**Decision**: do not iterate further on this lemma. The chained-OF cost (51 s) is real but extracting it cheaply would require either (a) restructuring `updateCap_no_0` itself to handle the chain natively, or (b) a custom dedicated `use_valid_chain2` lemma — both are higher-cost interventions than this session can afford to validate.

---

## Session decision: stop here, write up

After two negative experiments, the empirical signal is clear:

1. **Hypothesis-driven micro-rewrites at the lemma level have low success rate on this codebase** (~0/2 here; Access run cited in SKILL.md saw similar — the author called out "Agents who target WHICH FILES are slow, but then bulk-patch them without proof-level data, regress on most files").

2. **The cost per attempt is high**: 4-16 min wall per check-theory pass, plus my analysis time. With ~2-3% noise floor on whole-file timing, individual lemma savings of ≤30 s on a 250 s+ baseline are at the edge of detectability — meaning even a "correct" rewrite may show no measurable win.

3. **The two failure modes were instructive**, not just chance:
   - Exp 1 inverted the cost model — `(is)` patterns are **bind-then-reuse**; inlining adds work, doesn't remove it.
   - Exp 2 inverted the semantic model — chained `OF` does **multi-step composition in one tactic call**; naive splitting changes the fact set.
   - Both lessons are GENERAL and will guide future agents away from the same mistakes.

4. **The infrastructure built this session (inventory + diff + check-theory wrapper) IS the durable deliverable**, regardless of the experiment outcomes. Future per-lemma rewrite attempts have a working baseline → patch → diff → apply pipeline.

5. **Recommended next direction**, if the user wants to continue:
   - Run `bash .claude/skills/isabelle_prover/scripts-container/scan-slow-proofs.sh` (1-3 h wall) to get sorry-cost data — that gives "how much faster does the file build if this proof is replaced by sorry", which is a much more reliable target signal than aggregate command time.
   - Try the SKILL's `propose-tactic.sh` for empirical tactic substitution (priors-driven candidates, ~50-500 ms per proposal, no isabelle invocation).
   - Or pivot to **structural** changes (theory file splitting for parallelism) instead of per-lemma micro-rewrites.
