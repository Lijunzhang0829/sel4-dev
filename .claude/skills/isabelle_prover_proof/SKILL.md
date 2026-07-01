---
name: isabelle-prover-proof
description: "Per-lemma static-ization for seL4 proof build wall time. Rewrite automation tactics (auto, simp, simp add:, blast, fastforce, force, clarsimp, eval, presburger, arith, linarith, metis, smt, meson) into deterministic STATIC tactics. GATE FIRST: per-line time + classify search-vs-work via Isa-REPL; only attack lines that are hot AND classical-search-dominated. Rewrite via the A→B reconstruction agent (hammer gives FACTS only; agent searches a deterministic audit-passing path). Use on individual lemmas in `proof/**/*.thy`."
---

# Proof type — static-ization, gated by per-line timing

ONE axis: **shrink the search a tactic does at verify time** by replacing
automation with deterministic, named rule applications — for wall time.

Two hard lessons (see `reference/`) shape the method:

- **search-free ≠ faster.** A lemma can be fully static-ized and gain ~0 wall,
  because its cost was `simp`-rewriting *work*, not search. → gate before rewriting.
- **sledgehammer is not a static oracle.** It returns `metis`/`meson`/`smt`
  (themselves search) — a *fact set*, not a deterministic *path*. → use the
  **A→B reconstruction agent** to turn facts into an explicit static path.

## Core principle (hard contract for an accepted patch)

> An accepted patch contains **no** `auto`, `simp`, `simp add:`, `simp_all`,
> `blast`, `fastforce`, `force`, `clarsimp`, `eval`, `presburger`, `sos`,
> `arith`, `linarith`, `metis`, `smt`, or `meson` in the rewritten region.
> The only rewrite-style tactic permitted is `simp only:` with an explicit,
> complete rule set. Every other step is a named rule application. AND the
> target line's own wall must drop. If a site can't be made fully static, or
> the lemma isn't search-dominated, the lemma is **not a target — pivot.**

## A verified negative is a first-class result (why we record the process)

Every rewrite still serves ONE purpose: optimize the lemma **from the
search-elimination angle** for wall time. But the goal is not only "make it
static and faster." A rigorously-established **negative** — "this seL4 lemma
*cannot* be optimized from the search angle" — is itself a valuable result, not
a failure. That value is **conditional**: it only counts if the path that
reached it is **rigorous and reproducible**. A hand-waved "I tried, it didn't
work" is worthless; a replayable verification that the cost is work/structure
and not search is a real finding.

This is exactly why the generative rewriter (**GenStat**,
`tools/seL4-proof-search/Isa-Repl/rewrite_agent.py`) is driven through
`claude -p` with the **whole process recorded** — every generated static script,
the real `check-theory` build verdict, and the Isabelle `***` diagnostic fed
back each round. The transcript IS the audit trail: a reader can replay *why* a
lemma was judged search-irreducible instead of taking the claim on faith. So a
`pivot` / `inconclusive` outcome (below) is not a dead end to discard — it is a
recorded, reproducible verification that this lemma's cost is not search, and
must be captured as such (experiment record, kept transcript), with the same
rigor as an accepted patch.

## Search-space hierarchy

| Tier | Tactics | Status |
|---|---|---|
| ① | `auto`,`force`,`blast`,`fastforce`,`metis`,`smt`,`meson`,`clarsimp` | FORBIDDEN — classical/resolution backtracking |
| ② | `simp`,`simp add: X`,`simp_all` | FORBIDDEN — rewrites against the whole default simpset + conditional rewriting |
| ③ʳ | `simp only: <full named set>` | ALLOWED — rewriting limited to exactly the named rules |
| ④ | `rule/erule/drule/frule/intro/elim <named>`, `subst`/`subst (asm)`, `unfold`, `cases x rule:`, `case_tac x` | ALLOWED — single deterministic application |

`simp add:` is tier ② (drags in the default simpset) — NOT acceptable; only
`simp only:` (complete set) is permitted. If it won't close, drop to
`subst`/`unfold`/`rule`, else pivot.

## Tooling — Isa-REPL inner loop (built & validated)

`IsaREPL.jar` at `tools/seL4-proof-search/Isa-Repl/target/`; py4j installed;
driven inside the `sel4-l4v` container.

1. `IsaRepl(session="Refine")`; `init(thy)` loads the session heap ONCE (~25s).
   For session init do **NOT** call `_compile()`.
2. Reach a deep lemma cheaply: parse the file, **`sorry`-replace all preceding
   proofs** (an Isa-REPL exploration device — NEVER in a patch; the patch is
   verified `sorry`-free by `check-theory.sh`), step the prefix (~15s), step the
   lemma statement. Prefix paid once.
3. `clone("base")` checkpoints; `try_tactic(cmd)` = timed step (per-line wall +
   CPU-ablation signal); `focus("base")` rewinds. `goal()` reads the goal;
   `_prove_by_hammer` / `_extract_hammer_facts_with_thy_names` give candidate
   **facts only**.

| Also | role |
|---|---|
| `$ISA_SCRIPTS/check-theory.sh <file> <session> [--patch/--apply p]` | whole-file correctness gate (≈ file check time; ~7.5 min for a 3.5k-line Refine theory) |
| full session build | final downstream gate |

> Cost reality: `check-theory.sh`/`goal-at.sh` re-process the whole file per
> call — minutes for big Refine theories. Use Isa-REPL for iteration; reserve
> check-theory for the final file gate.

## Phase 0 — GATE (mandatory; most lemmas stop here)

1. **Per-line time** the lemma in Isa-REPL. Find the few lines that dominate.
2. **Classify each hot line — search vs work** by ablation (CPU-ish time):
   `clarsimp` vs `simp` (clarify search negligible?); `simp add:` vs
   `simp only: <obvious>` (no progress ⇒ default-simpset conditional rewriting =
   **work**); the classical tactic timed alone.
3. **Decide:** hot AND classical-search-dominated → Phase 1; hot but `simp`-work
   → **pivot**; cheap (<~50 ms classical) → **pivot**.

Empirically in refine proofs: per-line classical search is cheap (~10–65 ms);
the >500 ms lines are `simp`-over-big-terms (work). Expect to pivot often.

## Phase 1 — Rewrite via the A→B reconstruction agent

`tools/seL4-proof-search/Isa-Repl/ab_agent.py` — see
`reference/ab-reconstruction.md`. Per qualifying line:

1. Capture **A** (goal before the automation tactic) as a `clone_tls`
   checkpoint and **B** (the subgoal *signature* the original tactic leaves).
2. **Candidate facts** = `_prove_by_hammer` facts (PARSED OUT of the
   `meson/metis/using …` reconstruction — the tactic itself is discarded) ∪
   source hints (`intro:/dest:/elim:/simp:` args) ∪ a small background library
   (`r_into_trancl`, `trancl_into_trancl`, `r_r_into_trancl`, `domI`, `conjI`,
   `exI`, `refl`, `TrueI`).
3. **Bounded search** over allowed templates (`rule/erule/drule/frule F`,
   `intro F`, `assumption`, `erule conjE`, one `simp only: <facts>`) applied
   from the checkpoint with a timeout; success = resulting signature **equals
   B** (leaves the identical remaining subgoals). `focus_tls` backtracks.
4. If no path is found (the path stays hidden in `meson`, or it needs a goal the
   agent can't reach) → pivot. NEVER keep the `metis/meson` reconstruction — it
   fails the audit.

> **Sledgehammer returns automation, not a path.** It only seeds the fact pool.

## Phase 2 — Audit + measure + commit

1. **Audit grep** the rewritten region (any hit but `simp only:` = FAIL):
   ```bash
   grep -nE '\b(auto|blast|fastforce|force|clarsimp|eval|presburger|sos|arith|linarith|metis|smt|meson)\b' <region>
   grep -nE '\bsimp(_all)?\b' <region> | grep -vE '\bsimp only:'
   grep -nE '(\brule\s*$|\brule\s*\)|^\s*\.\.\s*$)' <region>   # implicit: bare rule / ..
   ```
2. **After per-line timing** (Isa-REPL): target line's own wall must drop.
3. **Authoritative verify + commit (parent Rule 1):** `check-theory.sh --patch`
   green → `--apply` (auto-writes `attempts/impact` logs, Rule 3/4) → full
   session build green. The **recorded** metric is the whole-file check-theory
   wall vs the golden baseline (per-line REPL time is only the Phase-0 signal).
4. **Submit (parent Rule 5):** topic branch `proof-strengthen` → experiment
   record `reports/experiments/<NNNN>-<name>/` (`patch.diff`, `command.sh`,
   `measurement.json` with `baseline_ref → reports/golden-baseline/walls.json`,
   `decision.md`; no `derivability.thy` — statement unchanged) → PR to `main`.

| Outcome | Action |
|---|---|
| audit clean + green + whole-file wall ↓ (≤95% of a ≥30s baseline) | **Accept** → experiment record + PR |
| audit clean + green + file wall flat | record `inconclusive`, **pivot** |
| any audit hit / can't make static | **Reject, pivot** |

## Anti-patterns (DO NOT)
| Anti-pattern | Why |
|---|---|
| Skipping Phase 0, rewriting by eye | You'll attack cheap or work-bound lines for 0 gain. |
| Treating `_prove_by_hammer` output as static | `metis/meson/smt` are automation — fail the audit; use as fact seed only. |
| Keeping `simp add:` / `simp_all` as "static" | Tier ②, fails the audit. |
| Partial-rollback of a stubborn line to automation | Leaves automation in → fails the contract; pivot. |
| Iterating the FULL session build per round | Use Isa-REPL (prefix once); full build only as final gate. |
| Whole-session wall as the per-lemma metric | Per-lemma payoff is invisible there; use per-line REPL timing. |

## References
- `reference/positive-cases.md` — the technique that works (confirmed full
  static-izations, replacement-pattern library, trap catalog).
- `reference/negative-cases.md` — when to pivot (per-line timing showing
  work-bound / cheap-search lemmas; the search-vs-work ablation).
- `reference/ab-reconstruction.md` — the A→B agent: algorithm, validated
  `n_tranclD` paths, v0 limits and v1 directions.
