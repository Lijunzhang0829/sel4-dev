# Block 2 — script audit (measurement validity)

Every script that fed a number into the conclusion, what it actually measures,
how it was validated, and where it could be wrong.

| script | lines | role |
|--|--:|--|
| `scan_db_timings.py` | 199 | decode session build-DB `command_timings` → per-command elapsed, group into lemmas, compute total_s/search_frac |
| `compute_coverage.py` | 75 | honest per-theory coverage (n_timed/matched) for the IsarLite re-timing |
| `gate.py` | 161 | reliable gate: check-theory build (correctness) + `isar timing --lemma` orig-vs-patched (speed) |
| `ab_agent.py` | 362 | A→B search: Isa-REPL proof state per step + proposer + bounded search |
| `run_loop.sh` / `run_loop_batch.sh` | 85 / 17 | drive search→gate per candidate, resumable |
| `proposer_host.py` | 78 | host-side LLM proposer (claude CLI, haiku) over a file bridge |
| `scan_candidates.py` / `select_top30.py` | 124 / 59 | the earlier IsarLite-based pool (superseded by the DB scan for the final run) |

## 2.1 Timing — two independent sources, both prover-internal

**(a) IsarLite `isar timing`** — reads the prover's own `command_timing` PIDE
messages (elapsed/cpu), no Python clock, no IPC in the number.
- **Validated**: a 150 ms `ML sleep` → 150.0 ms; and the per-line sum on
  `CSpaceInv_AI` = **33.0 s** vs `isabelle process` ground-truth build **31.6 s**
  (~4% — faithful). This is a real end-to-end check, not an assertion.

**(b) DB `command_timings`** — the *build's own* per-command elapsed, stored
zstd-compressed in `<session>.db / isabelle_session_info`. Same `command_timing`
mechanism, captured by the real build. Used to find the >10 s lines across all
sessions without re-running.
- **Validated**: offset→line mapping (symbol-aware) spot-checked on
  `CNode_AC:1103` and `IpcCancel_AI:369` — both map to the expected source line.

**Verdict (measurement)**: timing is trustworthy. Two independent prover-internal
sources agree in kind, and (a) matches an external ground truth. The old unreliable
Isa-REPL `time.monotonic` timer (which baked ~6–8 ms IPC into every step) was
**removed** from the gate — see block 4.

## 2.2 Known measurement limitations (honest)

1. **`search_frac` from the DB is COMMAND-level, not intra-tactic.** It measures
   "what fraction of the lemma's time is in classical-tactic *lines*", not "what
   fraction of a fastforce is search vs simp." So a lemma can have frac=1.0 yet its
   single `fastforce simp:` be mostly rewriting. We mitigated by a separate
   **no-simp** tactic filter (block 3), but the genuine search-vs-rewrite split is
   never directly measured — only the loop (does a static path exist + is it faster)
   probes it operationally.
2. **DB threshold.** The build's `command_timing` only records commands above
   ~0.1 s, so a lemma's "total" from the DB omits sub-0.1 s steps. Negligible for
   the >10 s targets, but it means totals are slight under-counts.
3. **Lemma grouping is heuristic** (`scan_db_timings.py` STMT/DECL keyword walk).
   A `subgoal`/`instantiation`/structured proof can mis-group commands, so a few
   `total_s` values may merge or split lemmas. The per-lemma logs (block 5) record
   the actual proof line, which was re-derived and spot-checked.
4. **`gate.py` was validated end-to-end but not stress-tested.** Confirmed on
   `ex_tupleI` (find_proof_span, patch, check-theory build OK, isar timing
   orig/patched) and `empty_slot` etc. Its noise guard (GATE_MIN_MS=50) was added
   *after* the `ex_tupleI` ACCEPT/REJECT verdict flipped across two runs — i.e. the
   gate is only trusted above a 50 ms floor (below that, deltas are timer noise).

## 2.3 Reproducibility
All inputs persisted: `runs/db_candidates.json` (122), `runs/db_loop_run.json` (8),
`runs/loop-results.jsonl` (verdicts), `runs/ab-<lemma>.json` (every search attempt),
`runs/timing/coverage.json`. Scripts are in the Isa-Repl dir. The DB scan is
deterministic (reads static build DBs). The loop is **not** fully deterministic —
the LLM proposer (haiku) and timing have run-to-run variation — so re-running may
change attempt sequences and ms values, though the NO-PATH verdicts are stable in
kind.
