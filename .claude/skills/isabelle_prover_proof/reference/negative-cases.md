# Negative reference — when to pivot (Isa-REPL per-line timing)

Two lemmas measured with the Isa-REPL loop (session Refine, sorry-prefix,
per-line timing + ablation). Both are **pivots**; they define the Phase-0 gate
and correct two false assumptions.

## Case A — `n_dest_next` (CNodeInv_R.thy:2735): search-free-able but WORK-bound
Per-line timing (full proof 2759.7 ms, 59 steps): cost in **3 lines = 80.8%**.

| step | line | ms |
|---:|---|---:|
| 17 | `clarsimp simp add: modify_map_same modify_map_other` (neg_pos) | 628.1 |
| 44 | `simp add: modify_map_same modify_map_other del: dest2_parts` (pos_neg) | 854.7 |
| 55 | `clarsimp simp add: modify_map_same modify_map_other` (neg_neg) | 746.6 |

Everything else <35 ms — incl. the classical points the case study removed:
`by auto`=10.1, bare `rule`=10.5, `..`=6.2. **So removing search here is wall-neutral.**

Ablation at step 17: `simp only: [2 rules]`=17.7 ms FAIL; `simp add:`=637 ms closes;
`clarsimp simp add:`=688 ms closes; `clarsimp simp only:`=10.8 ms FAIL.
→ clarify search ≈ 7% (~50 ms); `simp only:` no progress ⇒ the default simpset
discharges conditional side-goals = **WORK**, not removable search. **PIVOT.**

## Case B — `n_tranclD` (Finalise_R.thy:808): classical search is CHEAP
Per-line (total 333 ms): no line dominates; classical-search lines ~25 ms each
(blast ×2 = 27/27, fastforce ×2 = 25/27); the 50/64 ms lines are
`clarsimp … split: if_split_asm` (work). `_prove_by_hammer` solved all 4 search
subgoals in 5–25 ms but returned `meson`/`blast` (automation — fail audit).
This lemma is where the **A→B agent** (reference/ab-reconstruction.md) found
real static paths for the cheap classical lines; the 2 clarsimp+split lines are
work-bound → pivot.

## Lessons (baked into the skill)
1. **search-free ≠ faster** — gate with per-line timing + search/work ablation
   BEFORE rewriting. In refine proofs hot lines are usually `simp`-over-big-terms.
2. **sledgehammer returns automation** — fact names only; get the path via the
   A→B agent (or `isar_proofs`); often not worth it.
3. **`simp only:` brittleness** — no progress ⇒ default simpset was doing
   necessary conditional rewriting (work); don't fall back to `simp add:`; pivot.
