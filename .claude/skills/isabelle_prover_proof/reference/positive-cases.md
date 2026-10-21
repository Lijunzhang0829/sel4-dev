# Positive reference — confirmed full static-izations (the technique)

Two lemmas in `mdb_swap` (proof/refine/ARM/CNodeInv_R.thy) rewritten to zero
classical-search tactics, verified by full Refine build. Canonical worked
example for the rewrite mechanics.

> Caveat: these eliminated *classical search* (auto, bare `rule`, `..`,
> clarsimp-clarify) but retained `simp add:` in places (tier ②, not `simp
> only:`) and were not per-line wall-verified. Under the current contract
> `simp add:` still needs tightening, and Phase-0 gating must confirm the
> rewrite actually saves wall — see `negative-cases.md` (n_dest_next's cost was
> exactly those retained simp lines = work, not the eliminated search).

## Lemma 1 — `cteSwap_chain` (~1000 lines, ~45 search tactics, 12 build iters)

Replacement-pattern library (the "招式表"):

| original (search) | static replacement |
|---|---|
| `by auto` = applying a known lemma | `by (rule lemma[OF …])` |
| `clarsimp dest!: X` | `obtain … by (elim exE conjE)` + named `rule` |
| `by (auto dest: R simp: S)` | `subst S` + `rule X` + `simp only: S` + `drule R[OF …]` |
| `clarsimp dest!: X_next[where p=…,simplified]` proving `mdbNext _ = 0` | `proof (rule ccontr) … obtain by (elim exE conjE) … rule domI … qed` |
| `clarsimp simp: next_unfold'` proving `p ∈ dom m` | `obtain cte … using F[unfolded next_unfold'] by (elim exE conjE)` + `rule domI` |
| `clarsimp` over if-then-else | `subst if_not_P, rule cond` + `subst if_P` + `rule refl` |
| `clarsimp simp: A B` (rewrite-only) | `simp only: A B` (complete set; NOT `simp add:`) |
| `clarsimp dest!: tranclD2` | `from tranclD2[OF trancl(2)] obtain … by (elim exE conjE)` |
| nested `cases X; cases Y; auto` | `by (cases X; cases Y; rule …)` |
| bare `rule` (auto-selected intro) | name it: `rule notI` / `rule conjI` |
| `..` (default intro) | name it: `rule the_intro_rule` |

## Lemma 2 — `n_dest_next` (51 lines, 5 search points incl. 2 implicit, 5 iters)

| line | original | static replacement |
|---|---|---|
| `mdbPrev src_node ≠ dest` | `by - (rule, simp add: next_dest_prev_src_sym)` (bare `rule`→notI) | `by - (rule notI, simp add: next_dest_prev_src_sym)` |
| neg_pos close | `clarsimp simp add: modify_map_same modify_map_other` | `case_tac cte` + `simp add: …` |
| `m ⊢ mdbPrev src_node ↝ src` | `..` (searches `src_prev_next[intro?]`) | `by (rule src_prev_next)` |
| `m ⊢ src ↝ dest` | `by auto` | `by (rule next_fold, rule src, simp add: a)` |

## Trap catalog
- **HOU multiple unifiers** → don't pre-instantiate; bind step-by-step
  (`rule next_fold, rule src` not `rule next_fold[OF src]`).
- **`[OF case]` grabs the whole bundle** → use the projection `trancl(2)`.
- **`apply` doesn't insert chained facts** → prefix `apply -`.
- **if-then-else over-expansion** → `subst if_P`/`if_not_P` + explicit condition.
- **schematic var desync across subgoals** (`intro conjI`) → `rule exI` + single simp.
- **constructor not reduced** (`…_update` nesting) → `case_tac cte` first.
- **implicit search easiest to miss** (bare `rule`, `..`) → dedicated final sweep.
