# Evaluating the current spec-strengthen experiments against the Spec optimization criteria

Branch: `bridge-consumer-trace`. Date: 2026-06-16.
Applies the Spec-module metric (`thesis1-optimization-metrics.md`) to the real
`spec-strengthen/experiments/` content.

## What the experiments actually are

- **33 applied entries, verdict `additive` across the board** — none is an in-place
  postcondition strengthening that is wired into the proof.
- **Pattern G (majority)**: `set_X_field[wp]` frame lemmas (operation preserves a
  state field). These are *new auxiliary facts*, not stronger versions of any
  existing lemma — the "strictly-stronger" frame does not even apply.
- **Pattern A (the one genuine strengthening — FAILED)**: exp 0029 strengthened
  `lsfco_cte_at`'s postcondition from `cte_at` to `real_cte_at` (strictly stronger:
  `real_cte_at ⟹ cte_at`, not conversely).

## The 0029 → 0054 story (the core diagnosis)

- **0029 (real strengthening) `trial_failed`.** `lsfco_cte_at` has a real consumer,
  `lsfco_cte_wp_at_univ`, whose `clarsimp simp: cte_wp_at_def` recipe was tuned to
  the *weaker* `cte_at` antecedent. Strengthening to `real_cte_at` makes that
  consumer's proof fail at L407. **Strengthening a consumed lemma necessarily
  breaks the consumer**, and the project's single-file gate does no downstream
  repair → rejected.
- **0054 (workaround) `applied`.** Instead of strengthening in place, *add a
  parallel* `lsfco_real_cte_at` and leave the old lemma untouched. It is strictly
  stronger AND green — *because nothing consumes it*. The stronger guarantee is
  proved but **never wired into the proof**.

## Verdict: the experiments do NOT meet the Spec optimization requirement

| Criterion | Status |
|---|---|
| strictly stronger (binary) | partial — Pattern A/0054 yes; Pattern G n/a (additive facts) |
| **consumed downstream** | **NO** — bridge-consumer-trace: 0/52 explicit consumers; 0054's shadow lemma has 0 consumers |
| closes a named gap | **NO** — mechanical field-frames / shadow strengthenings |

All 33 applied are strictly-stronger shadow lemmas or frame facts that are **proved
but unwired** → realized optimization effect ≈ 0 (the real Spec effect is
*consumption*, not "a lemma was added").

## Structural diagnosis (the important part)

**The single-file gate + no-downstream-repair structurally forces
speculative-additive output:**
- any real strengthening of a *consumed* lemma breaks its consumer (0029) → rejected;
- so the only thing that passes is an *unconsumed* shadow strengthening (0054);
- the project therefore systematically avoids exactly the consumed lemmas — the only
  ones where strengthening has an effect.

The experiments achieved "strictly stronger" but structurally excluded "consumed",
which is the dimension that carries the optimization effect.

## Implication

0029's `cte_at → real_cte_at` is a textbook-good optimization (clean, strictly
stronger, real consumer); its only blocker is re-tuning the consumer's `clarsimp`
recipe for the stronger antecedent — a standard type-1/4 proof-repair task.
**Real, effect-bearing spec strengthening requires the repair loop / consumer-first
generation**: strengthen the consumed lemma AND repair its consumers. This welds the
Spec-optimization track to the refinement-repair main line. It also means the
current 33 "additive" results, by the consumption metric, score ≈ 0 effect and
should not be cited as successful optimizations.

(Pending: deep-research pass on how the literature quantifies spec strength —
to be folded in. Task wodz137rl.)
