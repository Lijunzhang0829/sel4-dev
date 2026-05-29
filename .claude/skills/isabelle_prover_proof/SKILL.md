---
name: isabelle-prover-proof
description: "Per-lemma search-space elimination for seL4 proof build wall time. ONE strategy: record what the search tactic finds, replace it with deterministic rule application. Use when targeting individual lemmas in `proof/**/*.thy`."
---

# Proof type — search-space elimination via rule replacement

This skill targets ONE axis with ONE strategy: **eliminate the search a
tactic performs at verify time** by recording its successful resolution
and replacing the search tactic with deterministic rule application.

If this strategy yields < 5% file-wall drop, **DO NOT try other strategies**.
The reasoning: this strategy is the theoretical limit (drives T_search → 0).
If T_search elimination doesn't help, then T_search wasn't significant —
no other intervention will help more. Pivot to a different lemma.

## The model

For any single tactic invocation:

```
  T_total = T_search    +    T_work
            (试错回溯)        (kernel 必做的工作)
```

- **T_total**: directly measurable via `time_methods` wrapping
- **T_search**: the only part this skill targets
- **T_work**: rule resolution + simp loop + unification; this skill CANNOT reduce it

Empirical: on this codebase, `T_search` is often a small fraction of `T_total`
(cases 14/15: 0.3-3% of file wall). Set expectations accordingly.

## Threshold

| Patch scope | File-wall drop required |
|---|---|
| Single-lemma patch | **≥ 5%** |
| Multi-lemma batch (same file) | **≥ 20%** |

Below threshold: noise (±2-5% heap-cache drift). Discard the patch, pivot.

## Targeting

Pick a single `.thy` file. Optional helpers:

- **`heaps/db-archive-pre-swap/<SESSION>.db`** — `theory_timings` column gives
  reliable per-file relative ranking (8-thread parallel baseline, 2026-05-09,
  pre-experiment clean rebuild)
- **`reports/layer2-theory/theory-axis-2d.md`** — DAG-derived which files block
  downstream (structure 100% reliable, weights partially distorted)

Once a file is chosen, the rest is per-lemma work below.

## The strategy — record search, replace with rules

### Step 1 — Profile via time_methods

```
python3 tools/critical_path/lemma_search_profile.py <session> <file.thy> <lemma>
```

Output for each top-level `apply`/`by`/`subgoal by`:

- per-tactic real elapsed (sequential, via `time_methods` wrapping)
- tactic class (search-class = `auto`/`force`/`fastforce`/`blast`/`metis`/`smt`/`safe`/`fast`)
- lemma total wall + search-class fraction

Cost: ~3 min per lemma.

### Step 2 — Sanity check

From the output, compute:

```
theoretical_upper_bound_on_file_wall_savings
  = (search_class_total in lemma) / (file_wall)
```

| upper bound | Decision |
|---|---|
| < 5% | **Skip**. Even perfect search elimination won't clear threshold. Pivot. |
| ≥ 5% | Proceed to Step 3. |

### Step 3 — Find the rules each search-class tactic uses

For each search-class tactic in the lemma whose individual `time_methods`
elapsed is ≥ 1s:

```
bash $ISA_SCRIPTS/sledgehammer.sh <file.thy> <line> <session>
```

Wall: 30-180s per call. Output: candidate `by (metis X Y Z)` /
`by (smt (cvc4) X)` reconstructions. **Pick the shortest plausible one**
(fewer named lemmas = smaller patch, easier to maintain).

If sledgehammer finds nothing for a line, that line is not a candidate
under this strategy. Skip it. If NO line yields a sledgehammer
reconstruction, **the lemma is not attackable under this skill**. Pivot.

### Step 4 — Build and verify the patch

Replace each search-class tactic with the sledgehammer-found reconstruction:

```isabelle
(* original *)
apply (force simp: foo bar)

(* replacement, after sledgehammer *)
apply (metis foo bar rule_a rule_b)
```

Then:

```bash
# baseline
bash $ISA_SCRIPTS/check-theory.sh <file> <session>

# trial
bash $ISA_SCRIPTS/check-theory.sh <file> <session> --patch <patch>
```

Compute:
```
wall_delta_pct = (trial_wall - baseline_wall) / baseline_wall
```

### Step 5 — Decide

| Result | Action |
|---|---|
| `wall_delta_pct ≤ −5%` (single lemma) or `≤ −20%` (batch) | **Apply** |
| `−5% < wall_delta_pct ≤ 0%` | **Discard the patch. Pivot to another lemma.** Do NOT try other strategies on this lemma — see top of skill for why. |
| `wall_delta_pct > 0%` (regression) | **Discard. Pivot.** |
| FAIL (proof breaks) | Sledgehammer reconstruction was incomplete; try a longer suggestion. If still no go, pivot. |

```bash
bash $ISA_SCRIPTS/check-theory.sh <file> <session> --apply <patch>
```

### Step 6 — Record

Append to `reports/layer3-lemma/lemma-strengthen-<branch>-<YYYYMMDD>.md`:

| # | Lemma | File | Search command replaced | wall Δ% | Applied? |
|---:|---|---|---|---:|:---:|

With: lemma, file, what search-class command was replaced (e.g.
`apply (force simp: foo)` at L1950), baseline → trial wall, delta %,
patch sha.

## Tools (authoritative)

| Tool | Purpose | Cost |
|---|---|---|
| `tools/critical_path/lemma_search_profile.py` | Per-tactic real wall via `time_methods` wrapping. THE primary profiler. | ~3 min per lemma |
| `$ISA_SCRIPTS/sledgehammer.sh <file> <line> <session>` | Find `by (metis ...)` / `by (smt ...)` reconstruction for the goal at that line | 30-180s per call |
| `$ISA_SCRIPTS/check-theory.sh <file> <session>` | File baseline wall measurement | ~ file wall |
| `$ISA_SCRIPTS/check-theory.sh <file> <session> --patch <p>` | Trial: apply patch in sandbox | same |
| `$ISA_SCRIPTS/check-theory.sh <file> <session> --apply <p>` | Commit verified patch | same |

## ⚠ DO NOT USE — deprecated

| Item | Why |
|---|---|
| `tools/critical_path/lemma_profile.py` | Based on `command_timings`: 0.1s threshold drops fast tactics; `pos_of tr` offset misaligns to source keywords; records aggregate non-tactic work |
| `tools/critical_path/lemma_search_ratio.py` | Same problem as above |
| `reports/lemma-search-ratio-*.md` (13 files) | Generated from above tools; numbers wrong by 10-100× |
| `reports/layer3-lemma/lemma-optimization-targets.md` | Based on above; rankings unreliable |

Root cause: see `reports/layer3-lemma/lemma-optimization-master-log-20260526.md`
end sections.

## Known patterns — evidence summary

### Confirmed wins (historical, when a different strategy was available — DO NOT use those strategies under this simplified skill)

| Pattern | Δ file wall | Note |
|---|---:|---|
| `subgoal by fastforce → subgoal by force` cluster on state-relation premises (Finalise_R 1557-1565, CSpace_R 802-814) | −6% ~ −13% | Tactic-swap strategy, predates this simplified skill |

These are kept for historical reference. **Under the current simplified
skill, do not attempt tactic swaps.** The reasoning: tactic swap only
shifts WHICH search engine runs, not WHETHER. Sledgehammer→metis is the
clean test for "does eliminating search matter here at all".

### Confirmed non-wins (these prove search elimination has limited room)

| Pattern | Lemma | Δ | What it proved |
|---|---|---:|---|
| `apply safe → apply (rule iffI); ...; apply simp` | `eq_ucast_word8` (Tcb_R), 95% reported search | **−0.33%** | The 20s "search" in heap-log was mostly T_work; eliminating search saved 0.7s wall, sub-threshold |
| `auto/fastforce → force` on dual search command | `invs_A` (ArchKernelInit_AI), 25% reported search | −0.48% | Same lesson — search-time was barely there |

**These two negative results are why this skill is "single strategy, no
fallback".** If the cleanest possible test (full search elimination via
sledgehammer-found metis or rule chain) doesn't move wall ≥ 5%, then by
construction nothing weaker will.

## Anti-patterns (DO NOT)

| Anti-pattern | Why |
|---|---|
| Bulk `force → fastforce` whole-file sweeps | Empirically regress +7% to +160% (Tcb_AC, Syscall_AC, CNode_AC, InfoFlow Noninterference) — context-specific behavior |
| Trying multiple strategies on the same lemma after one fails | Wasted budget. The simplified strategy IS the upper-bound test. |
| Optimizing T_work (Isar decomposition, lemma splitting) | Out of scope — different optimization axis, different skill |
| Trusting `command_timings` / `lemma_profile.py` / search-ratio reports | Structurally unreliable — see deprecated list above |
