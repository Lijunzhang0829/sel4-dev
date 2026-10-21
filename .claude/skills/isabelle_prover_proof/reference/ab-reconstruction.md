# A→B reconstruction agent — replacing sledgehammer→metis with a static path

Tool: `tools/seL4-proof-search/Isa-Repl/ab_agent.py` (durable copy + fuller
notes at `tools/seL4-proof-search/Isa-Repl/AB-RECONSTRUCTION.md`).

**Why.** `_prove_by_hammer` returns `by (metis|meson|smt …)` — itself a search
at replay. It yields a *sufficient fact set*, not a deterministic path, so it
never passes the zero-automation audit. This agent takes those facts and
*searches a deterministic, audit-passing tactic path* between the state before
an automation tactic (A) and the state it produces (B), using only allowed
static tactics. The agent's search replaces metis's hidden search with an
explicit, checkable chain.

## Algorithm (v0)
1. Reach the lemma (sorry-prefix); per classical-search line capture **A**
   (goal before) as a `clone_tls` checkpoint and **B** (subgoal *signature* the
   original tactic leaves).
2. **Candidate facts** = parsed hammer facts ∪ source hints
   (`intro:/dest:/elim:/simp:`) ∪ background lib (`r_into_trancl`,
   `trancl_into_trancl`, `r_r_into_trancl`, `domI`, `conjI`, `exI`, `refl`,
   `TrueI`). The `metis/meson` tactic itself is discarded.
3. **Bounded DFS** over `rule/erule/drule/frule F`, `intro F`, `assumption`,
   `erule conjE`, one `simp only: <facts>`; each applied from the checkpoint
   with a 30 s timeout; success = signature **equals B**. `focus_tls` backtracks.
4. **Audit** the path; verify signature-equivalence (rest of proof still closes).

Run: `PORT=2557x python3 ab_agent.py` (env `THY`, `LEMMA`, `SESSION`, `DEPTH`).

## Validated — `n_tranclD` (Finalise_R, Refine), 2026-05-31

| line | original | agent's static path | audit |
|---|---|---|:---:|
| 4 | `clarsimp simp: slot` | `rule domI` ∘ `rule slot` | ✅ |
| 5 | `blast intro: trancl_trans prev_slot_next` | `rule transitive_closure_trans(1)` ∘ `erule prev_slot_next` ∘ `simp only: …` | ✅ |
| 9 | `blast intro: trancl_trans prev_slot_next` | `rule r_r_into_trancl` ∘ `erule prev_slot_next` ∘ `rule m_slot_next` | ✅ clean (rule-only) |
| 1,7 | `clarsimp … split: if_split_asm` | none — work-bound → pivot | — |
| 6,10 | `fastforce` (conjunction goal `… ↝⁺ … ∧ … ≠ slot`) | none — v0 depth/branching | — |

Proves the concept: hammer facts → deterministic audit-passing static path on
real seL4 `blast` goals (line 9 = fully `rule`-only).

## v0 limits → v1
- **Conjunction / multi-subgoal** (6,10): need `rule conjI` split + per-subgoal
  recursion; v0 single-subgoal DFS + shallow depth misses these.
- **Fact parsing** regex occasionally clips a name (`p_next` for `prev_p_next`).
- **simp only: kitchen-sink** (line 5) passes audit but isn't minimal.
- **Crash-survival**: `unfold`/`subst` on non-defs crashed the WIP IsarLite
  server; v0 excludes them + 30 s timeout. Re-add when server error handling is robust.
- **Engine**: Isa-REPL now; swap to IsarLite-runtime when its 2024 STATE-markup
  issue lands.
