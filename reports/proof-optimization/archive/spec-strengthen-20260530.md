# Lemma static-ization log — spec-strengthen — 2026-05-30

Tooling: Isa-REPL (built 2026-05-30), session `Refine`, sorried-prefix + clone_tls,
per-step timing. Inner loop validated.

## Case 1 — `n_dest_next` (in mdb_swap), CNodeInv_R.thy:2735

### Before — per-step timing (REPL replay, sorried prefix, 59 steps, total 2759.7ms)

Cost concentrated in 3 lines (80.8% of the lemma); everything else < 35ms.

| step | tactic | ms |
|---:|---|---:|
| 17 | `apply (clarsimp simp add: modify_map_same modify_map_other)` (neg_pos) | 628.1 |
| 44 | `apply (simp add: modify_map_same modify_map_other del: dest2_parts)` (pos_neg) | 854.7 |
| 55 | `apply (clarsimp simp add: modify_map_same modify_map_other)` (neg_neg) | 746.6 |
| — | the other 56 steps (incl. `by auto`=10.1, bare `rule`=10.5, `..`=6.2, all `by simp`≈8-12) | ≈530 total |

**Targeting correction:** the case-study framing ("5 search points incl. by auto, bare
rule, ..") is misleading for SPEED. Those classical points are all ≈6-12ms. The cost is
3 `simp`/`clarsimp` lines rewriting a stack of 8 nested `modify_map`.

### Ablation at point 17 (search vs work)

| variant | ms | result |
|---|---:|---|
| `simp only: modify_map_same modify_map_other` | 17.7 | FAIL (no progress) |
| `simp add: …` (default simpset, no clarify) | 637.2 | closes |
| `clarsimp simp add: …` (ORIGINAL) | 687.8 | closes |
| `clarsimp simp only: …` | 10.8 | FAIL |

Interpretation:
- `clarsimp` vs plain `simp`: 688 vs 637 → the classical **clarify search is only ~7% (≈50ms)**.
- Removing the default simpset (`simp only:`) → **zero progress**: `modify_map_other` is
  conditional (`p≠a ⟹ …`); its address-inequality side-conditions are discharged by the
  default simpset. The 637ms is **default-simpset conditional rewriting = WORK**, not
  classical search.

### Decision — PIVOT (per skill)
- Tier-① (classical/clarify) elimination — the skill's upper-bound test — would save ≈50ms
  = **1.8% of the lemma**, sub-threshold.
- Strict full static-ization is **blocked**: the hot lines need the default simpset; a
  closing `simp only:` would require enumerating the full side-condition discharger set
  (not extractable here — scala-isabelle drops the simp_trace channel), which is exactly
  the `simp only:` brittleness the skill warns against.
- **n_dest_next is work-bound, not search-bound → not a productive or feasible static-ization
  target.** Pivot.

### Methodology validated
- Per-step REPL timing localized the cost to 3 lines precisely (vs 45/5 "search points").
- The ablation differential (`simp only:` vs default; `clarsimp` vs `simp`, in CPU-ish time)
  is the decisive search-vs-work classifier — answers the open targeting question. Here it
  reads LOW search / HIGH work.
- Inner-loop cost: Refine heap load ~25s (once) + sorried prefix ~14s (once) + each tactic
  variant ≈10-850ms. vs `check-theory.sh` whole-file re-check (~15-20min/round for this file).

## Case 2 — `n_tranclD` (locale mdb_empty), Finalise_R.thy:808  [search-bound candidate]

Picked via scout (terminal-classical fan-out + per-line timing). Total 333ms / 12 steps.

### Before — per-step timing
| step | tactic | ms | class |
|---:|---|---:|---|
| 0 | `apply (erule trancl_induct)` | 20.9 | structural |
| 1 | `apply (clarsimp simp add: n_next_eq split: if_split_asm)` | 50.2 | work (simp+split) |
| 2 | `apply (rule mdb_chain_0D)` | 22.9 | static |
| 3 | `apply (rule chain)` | 22.4 | static |
| 4 | `apply (clarsimp simp: slot)` | 23.9 | work |
| 5 | `apply (blast intro: trancl_trans prev_slot_next)` | 26.8 | **classical search** |
| 6 | `apply fastforce` | 24.7 | **classical search** |
| 7 | `apply (clarsimp simp: n_next_eq split: if_split_asm)` | 63.6 | work |
| 8 | `apply (erule trancl_trans)` | 19.4 | static |
| 9 | `apply (blast intro: trancl_trans prev_slot_next)` | 27.2 | **classical search** |
| 10 | `apply (fastforce intro: trancl_trans)` | 26.5 | **classical search** |
| 11 | `done` | 10.5 | — |

No single line dominates (max 63ms); cost spread. Classical-search lines ≈ 25ms each.

### sledgehammer (via REPL `_prove_by_hammer`) at the 4 search lines — all solved in 5-25ms:
- L5: `meson m_slot_next prev_p_next transitive_closure_trans(1)`
- L6: `using to_slot_eq apply blast`
- L9: `meson m_slot_next prev_slot_next r_r_into_trancl`
- L10: `meson to_slot_eq trancl.simps`

### Strict static rewrite — DID NOT LAND (automated attempt)
Tried rule-chain candidates (from the hammer-named facts) + simp-only for the clarsimp lines,
gated on signature-equivalence with the original. **All 7 fell back to original**; after=339ms.
AUDIT still shows banned `blast/clarsimp/fastforce`.

Why it failed / what it proves:
1. **Hammer reconstructions are themselves automation** (`meson`/`blast`), not static rule chains.
   "sledgehammer → static" is NOT clean: meson runs a resolution *search*; converting it to a
   deterministic `rule` chain requires replicating that resolution (correct lemma forms + arg
   order), which blind candidates didn't hit.
2. **clarsimp+split lines are work-bound** (same blocker as Case 1): `simp only: n_next_eq
   split: if_split_asm` does not reproduce clarsimp's full effect (default simpset + safe steps).
3. **No wall benefit even if landed**: the classical searches are ~25ms each; the proof's cost
   is spread, not search-dominated.

### Conclusion
Across BOTH lemmas: in seL4 refine proofs (a) per-line classical search is cheap (~10-65ms);
(b) the >500ms lines are simp-over-big-terms (work); (c) the strict "zero automation except
simp only:" bar is very hard to clear — sledgehammer output is automation, and simp→simp-only
needs the full side-condition set. The strict skill will **pivot on most real lemmas**. The
search-elimination axis has little headroom here (reproduces the old skill's "T_search is a
small fraction" + "confirmed non-wins").
