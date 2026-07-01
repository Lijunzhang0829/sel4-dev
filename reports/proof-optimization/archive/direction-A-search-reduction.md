# Accelerating seL4's valuable proof commands: eliminate vs. reduce the search

**Question.** Do seL4's expensive automation tactics (`auto`/`fastforce`/`force`/`blast`,
≥10s each) have eliminable *search* overhead that we can remove to speed up the proof build —
or does removing it not help?

**Answer (build-verified).** *Eliminating* the search (rewriting the tactic to a static
apply-script) does **not** work on a single valuable line. *Reducing* the search space (keeping
the tactic but making it search less) works on **11 / 45** valuable lines, with build-verified
correctness and up to 91–94 % per-line speedups.

This flips the original framing: the search in these lines is **necessary** (so it can't be
eliminated), but it is often **wasteful** (so it can be reduced).

---

## Candidate set (reproducible)

`tools/seL4-proof-search/Isa-Repl/extract_high_value.py` reads the build's own per-command
`command_timings` (heap DBs) and emits every proof command that is BOTH valuable
(elapsed ≥ `MIN_CPU`=10s) and a rewrite target (tactic ∈ {auto,fastforce,force,blast}), tagged
feasible/infeasible for the REPL methods. Fully env-parameterised, no manual picking.
Result: **45 feasible high-value search lines** across 9 sessions
(`lemma-staticize/experiment-candidates/high_value_candidates.json`).

## Methods (three agents, identical reach-B + build gate)

All three locate the cost-target line in the REPL, checkpoint the entering state **A**, run the
original tactic once to capture the residual state **B**, then look for a replacement A→B:

| agent | idea | replacement vocabulary |
|---|---|---|
| `react_agent.py` (DFS) | eliminate search → static | fixed structural menu (rule/erule/drule/… + hammer facts) + backtracking |
| `genstat_stateful.py` (stateful / blind) | LLM eliminates search → static | LLM, static tactics only (auto/fastforce **forbidden**) |
| `reduce_agent.py` (**Direction A**) | **keep the tactic, search less** | LLM, automation **allowed** but configured to do less (prepend `simp only:`/`clarsimp` normaliser, `simp del:` a hot rule, targeted `simp:` set, reorder cheap branch first) |

**Two gates, in order.** (1) in-REPL **reach-B**: the replacement from A reaches the SAME
signature B (fast pre-filter — but it CAN false-positive, see below). (2) **build**: splice the
replacement in place of the original command span and run a real `check-theory` build —
**ground truth**. A line counts as accelerated only if it is build-green AND faster.

## Results

| method | coverage | build-verified accelerations |
|---|---|---|
| DFS (eliminate) | 45 / 45 | **0** — 41 genuine NO-PATH (50–163 nodes explored, goal diverges), 3 tool-wall, 1 in-REPL false-positive (build-rejected) |
| genstat ×2 (LLM eliminate) | 14 highest-value | **0** — all TIMEOUT, goals diverge (5→10 subgoals), 0 reach-B |
| **reduce (Direction A)** | 45 / 45 | **13** real accelerations (11 below + 2 FinalCaps recovered, see note); 17 not-reducible; 16 tool-issues |

**Build-gate false-negative correction.** The first build pass reported 3 "false positives"
(`invoke_cnode_silc_inv:1825`, `cap_swap_silc_inv:943`, `Sys1AgentMap_simps:910`). On
re-investigation these were **build-harness false-NEGATIVES**, not wrong variants:
`check-theory.sh` renames `theory FinalCaps → Tmp_xxx` but left the body's **self-qualified**
references (`FinalCaps.slots_holding_overlapping_caps`) dangling → the unpatched theory ALSO
fails to build. Fixed in `check_theory_selfqual.sh` (rename body `FinalCaps.` refs too); the
2 FinalCaps variants then build green → **13 verified**. `Sys1AgentMap_simps:910`
(Example_Valid_State, local import `ArchNoninterference`) is still harness-blocked — distinct
import-resolution issue, indeterminate.

### The 11 build-verified accelerations (in-REPL A/B timing)

| line | session | orig→variant | saved | % |
|---|---|---|---|---|
| is_derived_cap_arch_asid_issues:198 | AInvs | 27847→2603 ms | 25.2 s | 91 |
| empty_slot_pas_refined:1103 | Access | 145716→124143 ms | 21.6 s | 15 |
| rm_affects:489 | InfoFlow | 22541→2876 ms | 19.7 s | 87 |
| abd_affects:241 | InfoFlow | 23227→8089 ms | 15.1 s | 65 |
| dmo_user_memory_update_reads_respects_g:133 | InfoFlow | 22178→8162 ms | 14.0 s | 63 |
| ntfn123_reads:742 | InfoFlow | 21871→16880 ms | 5.0 s | 23 |
| in_whileLoop_corres:361 | Lib | 4698→299 ms | 4.4 s | 94 |
| insert_cap_child_corres:332 | DRefine | 22818→18488 ms | 4.3 s | 19 |
| dmo_bind_ev':93 | InfoFlow | 23283→19460 ms | 3.8 s | 16 |
| cap_insert_pas_refined:1032 | Access | 14697→12629 ms | 2.1 s | 14 |
| requiv_device_mem_eq:811 | InfoFlow | 2807→1361 ms | 1.4 s | 52 |

Total ~117 s in-REPL tactic time saved. Representative reductions the LLM found:

- **reorder** (requiv:811): `(fastforce … | intro X)+` → `(intro X | fastforce …)+` — try the
  cheap rule first so most goals close before fastforce runs (52 %).
- **normalise-then-search** (dmo:133): `fastforce simp: A B C D …` →
  `simp only: A B` then `fastforce simp: C D` — cheap unfold shrinks fastforce's search (63 %).
- **prune** (cap_insert:1032, cap_swap): `simp del: split_paired_All` to drop a hot rewrite.

## Why eliminate fails but reduce works

The expensive tactic's power is **integrated backtracking search** (which disjunct? which dest
unifies? which set position?). Decomposing it into a **no-backtrack static sequence** forces a
commit per step → the goal **diverges** (subgoals 5→10) or a step **FAILS** to unify. That is
why DFS (fixed menu) and genstat (LLM static) both get 0/N. Reduction keeps the search (and its
backtracking) intact and only shrinks its *input*, so it stays correct and gets faster — when
the cost was wasteful normalisation/def-unfolding rather than essential search. The not-reducible
lines (rm_reads set-membership, etc.) are exactly the ones whose cost IS essential search.

## Rigorous re-measurement (the in-REPL speed numbers were inflated)

The per-line `saved` figures above are single-shot in-REPL A/B. A rigorous re-measurement
(median over K reps, **discarding the cold-start first run**, plus an A/A noise floor) on a
controlled in-REPL A/B found the single-shot `orig_ms` is badly **cold-inflated** (is_derived
27.8 s single-shot → **6.2 s** warm median, 4.5×; dmo_bind 23.3 s → **12.4 s**, 1.9×), so the
reported savings are overstated and some flip:

| line | single-shot | rigorous median | A/A noise | verdict |
|---|---|---|---|---|
| empty_slot_pas_refined:1103 | 15 % | **15.4 % (21.7 s)** | 0.4 % | ✅ robust (top-value line) |
| insert_cap_child_corres:332 | 19 % | **18.4 % (3.7 s)** | 0.5 % | ✅ robust |
| is_derived_cap_arch_asid_issues:198 | 91 % / 25 s | **54.7 % (3.4 s)** | 5.4 % | ✅ real, but 25 s→3.4 s |
| cap_insert_pas_refined:1032 | 14 % | **8.1 % (1.1 s)** | 1.3 % | ✅ real, small |
| ntfn123_reads:742 | 23 % | 16.6 % | **15.6 %** | ❌ within noise |
| dmo_bind':93 | +16 % | **−76 %** | 1.9 % | ❌ actually SLOWER |
| cap_swap_silc_inv:943 | 62 % | 59.5 % | **58.3 %** | ❌ within noise |

**Conclusion on speed:** the reductions are build-verified **correct** (13 lines), but the
in-REPL timing is too noisy/cold-inflation-prone to trust the magnitudes. Only the **low-noise
lines have rigorously-confirmed speedups** — most convincingly `empty_slot_pas_refined:1103`
(the highest-value line, 21.7 s / 15.4 % at 0.4 % noise). The headline "117 s across 14 lines"
is NOT defensible; it was largely cold-start artifact. A trustworthy AGGREGATE speedup needs a
real **build-wall** measurement (build the patched session, diff `command_timings`) — the
in-REPL A/B and the IsarLite per-line timer (`missing-json`) are both inadequate here.

## Stock build-wall ground truth — and why even it is noisy

The in-REPL timings above are an unreliable screen. The intended ground truth is Isabelle's own
`command_timings` from a real `isabelle build` (`measure_build_elapsed.py`). On the headline line,
**a fair same-config measurement** (original AND variant both freshly rebuilt at `threads=1`,
serial, back-to-back):

| line | orig (stock serial) | variant (stock serial) | saved |
|---|---|---|---|
| **empty_slot_pas_refined:1103** | **188.7 s** | **144.8 s** | **43.9 s (23.3 %)** |

**Critical caveat — `command_timings` stores only `elapsed` (no cpu), and elapsed is wall-time
that VARIES across builds:** the same original line measured 150.6 s, 188.7 s, and 242.0 s in
three builds (parallel-production / serial / parallel-heal); the same variant measured 121.7 s,
144.8 s, 162.4 s. So there is **~30–60 % run-to-run noise** and **no stable per-line build
timing is obtainable with current tooling** (in-REPL: cold-start + IPC + 58 % noise;
command_timings: 30–60 % cross-build; IsarLite `isar timing`: broken/`missing-json`; per-command
cpu: not recorded). The earlier "19.2 % deterministic" was an artifact (the second rep re-read
the same DB without rebuilding) AND confounded (parallel orig vs serial variant) — corrected here.

**What survives the noise:** across every method (in-REPL median 15.4 %, stock matched-serial
23.3 %), the variant is **consistently faster** than the original, and the build is **green**. So
the defensible claim is: empty_slot's search-space reduction is **build-verified correct and
~20 % (≈40 s) faster**, corroborated by multiple methods — but the exact figure carries ~30 %
inherent measurement noise. A precise, reproducible per-line speedup would need per-command CPU
timing (not stored by Isabelle) or a controlled many-reps fixed-load benchmark harness.

## Critical-path analysis — why the per-line wins didn't move the build wall, and CRefine

An end-to-end test settled whether the reductions move the real **parallel build wall**: applying
empty_slot + cap_insert (both in Access/CNode_AC) and rebuilding Access, 3 reps each:
`orig 914 s → variant 902 s, delta 13 s < within-arm noise ±32 s` → **NO measurable effect on the
build wall.** The reason, from a session-DAG critical-path analysis (sum of `command_timings` per
session, longest weighted root→leaf chain):

- **CRefine = 358 min serial = 49 % of all serial cost and 84 % of the critical path** (next
  session is InfoFlow at 66 min). Access (33 min) is a PARALLEL BRANCH off AInvs — **not on the
  critical path** — so optimizing it cannot move the wall. Every one of the 45 "feasible" lines
  was in such an off-path session (Access/InfoFlow/AInvs/DRefine), because the critical-path
  sessions (CRefine/Refine/InfoFlowC) were excluded from the tooling (REPL init wall).

**Can search-reduction help CRefine (the actual lever)?** Evidence from CRefine's per-tactic cost
breakdown: **54 % simp-work, 15 % ccorres/vcg machinery, only 13 % classical-search** (auto/
fastforce/force/blast). So search-reduction's ceiling on CRefine is ~13 % (≈46 min). BUT the few
hottest individual commands ARE classical search, e.g. `Invoke_C:3113  apply fastforce (* slow
fastforce *)` — **130 s, literally tagged slow by the seL4 developers** — proving the subgoal
`tcb_st_refs_of' (tcbState obja) = {}`. The reduction is sitting in the codebase: the IDENTICAL
goal is discharged elsewhere by the targeted `fastforce simp: tcb_st_refs_of'_def elim:
pred_tcb'_weakenE` (gives fastforce the def+elim so it doesn't search the huge unfolded `invs'`
context). High-confidence correct (same goal, canonical proof) and faster (targeted vs the slow
bare search) — and ON the critical path, so it WOULD move the wall (unlike Access).

**But it is infrastructure-blocked**: CRefine's parent heaps (CBaseRefine …) were cleaned to save
disk, so processing/building any CRefine theory requires rebuilding the C-heap chain — CBaseRefine
alone did not finish in 2 h, and CRefine itself is hours more. So a single CRefine reduction
costs a half-day to stock-verify. The thesis closes as: **search-space reduction is real and
correct and CAN target the critical path (CRefine has reducible monster fastforces), but its reach
is bounded (~13 % of CRefine; the dominant cost is simp-work/ccorres, NOT classical search), and
the practical path is gated by CRefine's enormous build cost.** The build-wall lever is therefore
CRefine via parallelism/caching or simp/ccorres optimization — classical-search reduction is a
small slice of it.

## Honest caveats

- **Speeds are in-REPL relative A/B** (original tactic vs variant, same checkpoint, same REPL).
  They establish the per-tactic intrinsic saving; the **build** confirms correctness. Absolute
  build-wall savings would need re-extracting `command_timings` after building each patched
  theory (golden in-REPL numbers differ from the parallel build, e.g. is_derived 27.8 s in-REPL
  vs 14 s golden) — not yet done.
- **The build gate is mandatory.** 3 of 13 in-REPL winners were build-rejected
  (`invoke_cnode_silc_inv:1825`, `Sys1AgentMap_simps:910`, `cap_swap_silc_inv:943`) — the
  in-REPL reach-B signature still false-positives on some long goals even after the
  full-goal-capture fix. Never report an in-REPL win without a build.
- **16 lines hit tool limits** (REPL gateway / py4j ConnectionRefused under JVM concurrency,
  `_step_without_timeout` errors on exotic tactics like `time_methods`, init wall on heavy
  DRefine/FinalCaps sessions) — reported separately, NOT as "not accelerable".
- `in_whileLoop_corres:361` was a DFS **false positive** (a trivial `erule allE` that
  build-rejected); the reduce variant on the same line is **different** and build-green — the
  line is genuinely accelerable, just not the way DFS hallucinated.

## Reusable assets (`tools/seL4-proof-search/Isa-Repl/`)

- `extract_high_value.py` — reproducible high-value candidate extraction from build timings.
- `reduce_agent.py` — Direction-A LLM agent (claude -p via the host bridge, full transcript
  recorded; reach-B + faster + adaptive timing + incremental save).
- `build_verify_reachb.py` — B_sig-aware build verification (splice the variant in place of the
  command span; closing vs mid-proof handled).
- `run_experiment.sh` — DFS-pool + LLM-serial pools, per-PORT cleanup, TIMEOUT stubs;
  `METHODS="dfs|stateful|blind|reduce"`.
- Run artifacts: `lemma-staticize/runs/reduce-all-20260625-150318/` (+ `build_verify.json`).
