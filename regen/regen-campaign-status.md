# Regen campaign status

Last updated: 2026-07-31

## Current Setting B campaign (2026-07-31)

- Branch: `regen-refine`.
- Latest fully executed predecessor:
  `sel4-refine-setting-b-c55be4ed1c2f8c1b`.
- Production continuation: temporarily unset. M1 aggregate artifact sealing and
  strict M2 evidence/cache binding changed frozen programs after `c55b...`;
  therefore a fresh successor campaign must replay M0-M4 before M5 starts.
- The new M2 gate binds M1/preflight/isolation/elaboration/cache evidence by
  path, size, and SHA-256 and has a self-hash. Cache reuse records also bind the
  source campaign ID and exact source M2 gate hash. M3/M4 now invoke the strict
  verifier instead of trusting a standalone PASS field.
- M3 replay now reconstructs canonical records from the raw fact/pool exports,
  compares every field with SQLite, recomputes `records_payload_sha256`, and
  verifies that `snapshot_id` is derived from it. M4 generation IDs remain
  absent at revision zero and must be introduced by a replayable
  `pending/retry -> generating` event.
- Contract-shell prefixes remain temporary Isabelle validation namespaces, not
  source-leakage controls. The isolation boundary is target-scoped to the 21
  M1-classified ARM Refine Proof sources and stock target heaps; legal frozen
  Support and out-of-target repository proof material are not hidden.
- Historical predecessor M0: complete and verified.
- M1: complete and verified.
- M2: complete and verified.
- M3: complete, published, and replay-verified.
- M4: complete, PASS, and reconstruction-verified.
- Refine inventory under `operation-corres-v1`: 41 theory slots, 21 Proof,
  20 Support, and 287 selected contracts.
- M0 freezes the actual `verification/l4v` trusted source repository identity:
  revision `00d9073f70d064b31b4b1fad8a0e7086bb258007`, with `proof/ROOT` and
  `proof/refine` clean at freeze/verify time. M4 session and post-freeze
  comparison programs plus scheduling policy are content-hashed inputs.
- M1 verifies all 41 trusted sources and publishes 21 contract-only Proof
  packages plus the exact 21-source forbidden inventory. Public leakage audit is
  PASS for all 287 contracts.
- M2 content audit, mechanical isolation, and Isabelle elaboration are PASS. All
  21 classified Proof-source reads and both stock target-heap reads are denied;
  two controls pass; all 287 declaration shells elaborate using frozen parents
  and eight initially legal Support theories.
- M3 snapshot: `m3-eedb806f991eeb648859`, 76,663 facts from 382 owners: 74,893
  frozen-parent and 1,770 frozen-Support facts. Automation membership remains
  135 `simp`, 3,788 `wp`, and 13 `corres` records. M4 invokes the complete M3
  verifier before generation and replay.
- M4 used one audited Codex 5.5 call over a 154,600-character sanitized prompt.
  It recorded no tool events, accepted 24 of 49 candidate edges, produced three
  layers, covered all 21 Proof files and 287 contracts exactly once, and sealed
  list hash `28b7d326db5b6d8f9d56ff1d92c7894f41bb8c3d57ae44c752f325f07d6886e7`.
- Rejected candidates comprise 16 duplicate reports, two self-edges, and nine
  candidates with at least one non-exact identifier. No operation SCC,
  precedence violation, or composition override was observed.
- M4 replay returns `reconstructed-and-unchanged` without another provider call.
  It reconstructs the prompt and all immutable M4 artifacts from M1/M2/M3,
  policy, and transcript final; the initial mutable ledger is gate-bound and its
  status changes must replay through legal events.
- Unit tests: the combined M0-M4, M4 comparison, and ROOT-parser suite passes 54
  tests, including rehashed-audit, missing-file/contract, and invalid-ledger
  attacks.
- Quality caveat: M4 PASS does not identify the true Refine proof DAG. The
  post-freeze comparison agrees with 10/22 direct human Proof imports and 13/29
  Support-collapsed relations. This is contaminated engineering evidence and
  cannot be fed back into generation.
- Next action: issue a successor campaign, replay M0-M4 under the new frozen
  identities, then start WP4/M5-M6 from the first pending entry while treating the
  immutable schedule as retry-capable execution priority, not a proof dependency
  oracle.

Current artifacts live under:

`reports/regen/refine-setting-b/sel4-refine-setting-b-c55be4ed1c2f8c1b/`

Primary gates and snapshot:

- `M2/m2-gate.json`
- `M2/source-access-isolation-results.json`
- `M2/contract-elaboration-results.json`
- `M3/m3-gate.json`
- `M3/premise-snapshot.json`
- `M3/premises.sqlite3`
- `M4/dependency-evidence.json`
- `M4/file-generation-list.json`
- `M4/session-progress.json`
- `M4/m4-audit.json`
- `M4/m4-gate.json`
- `M4/transcript/session-planner.{prompt.txt,ndjson,final.txt,stderr.txt,meta.json,audit.json`
- `M4-comparison/human-import-comparison.json`

## Superseded Setting B campaign evidence

Campaign `sel4-refine-setting-b-f84ff4ae434e3c30` reached a complete M4 model
call and deterministic schedule, but its immutable gate is FAIL because the
first audit implementation passed three desired false negative-control values
to `all()`. Its transcript and artifacts are retained as lifecycle-failure
evidence; they were not rewritten or promoted. Its regenerable M2/M3 isolated
heap copies were pruned after the corrected successor sealed, while manifests,
gates, premise database, and the complete M4 transcript remain.

Campaign `sel4-refine-setting-b-70faa08a3822a1f9` is superseded M4 development
evidence. Its 21-file/287-contract schedule was complete, but review proved that
the replay verifier accepted self-consistently rehashed audit/list tampering and
invalid mutable-ledger state; several trust-boundary audit values were constants.
Its transcript and artifacts remain immutable and are not continuation inputs.

Campaign `sel4-refine-setting-b-6e0d35cf78b48cb5` is the short-lived M0-M3
successor created while hardening M4. It was superseded before M4 because the
final implementation additionally invokes the complete M3 publication verifier.
Its regenerable M2 parent/isolated heaps and M3 isolated heap were pruned after
the final successor passed; manifests, gates, audits, raw exports, database, and
snapshot remain.

Campaign `sel4-refine-setting-b-67ae972e07f9c329` is the verified M0-M3
predecessor. M4 policy and code were not frozen there, so it is no longer the
continuation point.

Campaign `sel4-refine-setting-b-739cfcb1ec51da4e` preserves the earlier M2
workspace-only PASS and `Word_Lib` timeout. Intermediate campaigns produced
while repairing contract contexts, cache replay, and M3 scaling/provenance are
development evidence only. Their immutable artifacts are retained; the sole
latest completed predecessor is `sel4-refine-setting-b-c55be4ed1c2f8c1b`;
there is no production continuation until the strict-gate successor replays.

Campaign `sel4-refine-setting-b-89ad2fd1fbfa667e` is superseded because
`regen/refine_contracts.py` drifted during M2 development after its M0 freeze.
It reached M1 verification and produced M2 development attempts, but cannot be
continued once the frozen extractor hash no longer matches.

The previous campaign `sel4-refine-setting-b-360fdf9a7f7eb738` is now
superseded bootstrap-development evidence, not an M2 continuation point. Its
M0/M1 determinism, replay, and artifact counts were useful, but its M1 public
package audit was later found to have false negatives: human proof-context
commands such as `notes`, `using`, `including`, `(is ...)`, and related comments
were exposed in public contract packages. It also did not provide the current
full 41-theory trusted-source verification evidence.

The attempted campaign `sel4-refine-setting-b-95519811175cefe6` is also not a
continuation point: `regen/refine_contracts.py` drifted immediately after M0,
and M1 correctly rejected it at frozen-input verification.

The earlier `sel4-refine-setting-b-fb3af355025b5d09` and
`sel4-refine-setting-b-e5306f8128269b55` directories are retained
bootstrap-development artifacts and are not campaign continuation points.

## Superseded dashboard notice

The Schedule_R state below is retained as historical Mode R evidence. It is no
longer the restart point for the current Setting B repo-level campaign.

Current restart order:

1. `.claude/skills/isabelle_prover_regen/SKILL.md`;
2. `.claude/skills/isabelle_prover_regen/references/SESSION_HARNESS.md`;
3. `regen/REFINE-CREFINE-REPO-LEVEL-EXPERIMENT-PLAN.md`.

Do not resume old Schedule_R or ArchAcc_R generation IDs as production evidence.
Use `c55be...` only as completed predecessor evidence. Create and replay the
strict-gate successor through M4 before WP4/M5-M6; no File Planner or lemma
generation has yet started.

This is the historical campaign-level status page for the repo-level Isabelle proof
regeneration effort. It is retained for Schedule_R evidence; use the restart order above for new work.

Detailed Schedule_R run history and evidence live in
`regen/progress_report.md`. Harness design history lives in
`regen/HARNESS-EVOLUTION.md`.

## Historical Schedule_R campaign state

Target currently under active regeneration:

- `verification/l4v/proof/refine/ARM/Schedule_R.thy`

Current harness mode:

- Mode R: repair / obligation-discovery harness.
- Driver: `regen/regen_incr.py`.
- Provider path: local `codex-relay` with recorded prompt/raw/final/meta files.
- Verification: Isabelle batch checks, currently with incremental heap sealing.

Current latest stable run:

- `reports/regen/schedule_r-incr-20260720-143057`

Latest stable numbers:

- `final_green`: false
- `items_locked`: 153
- `items_stuck`: 30
- `machine_locked_lemmas`: 151
- `human_supplied_bodies`: []

Recent locked progress:

- `chooseThread_ksReadyQueues_asrt_from_relation`
- `ready_qs_runnable_cross`
- `chooseThread_ready_qs_runnable_from_relation`
- `switchToIdleThread_getIdleThread_corres`
- `arch_switch_to_idle_thread_invs_wp`
- `getIdleThread_invs_no_cicd_wp`
- `add_ready_qs_runnable` as a locked `verbatim_decl`

Current blocker:

- `switchToIdleThread_corres` remains stuck.
- The missing-method issue has been removed: `add_ready_qs_runnable` is now in
  the sealed prefix.
- The remaining failure is six WP / `corres_split` schematic goals around
  `arch_switch_to_idle_thread`, `Arch.switchToIdleThread`, `gets idle_thread`,
  and `getIdleThread`.

Immediate next proof unit:

- Do not retry the full `switchToIdleThread_corres` unchanged.
- Target the six-goal residual exposed in
  `reports/regen/schedule_r-incr-20260720-143057/items/82-switchToIdleThread_corres/check-*.log`.
- Only after `switchToIdleThread_corres` locks should
  `chooseThread_ready_queues_empty_corres` be retried.

## Harness status

Recent harness changes:

- Added `verbatim_decl` support to `regen/regen_incr.py`.
- Fixed declaration locking persistence: successful `verbatim_def` /
  `verbatim_decl` checks now call `save_locked()`.

Reason:

- Proof methods such as `add_ready_qs_runnable` are neither lemmas nor `defs`,
  but they are reusable proof orchestration declarations.
- Treating them as `verbatim_def` was wrong because the old validation required
  presence in `defs_by_name`.
- Without `save_locked()`, stdout could report a declaration as locked while
  the next resume lost it.

Risk:

- `verbatim_decl` is a Mode R repair feature. It should not be silently counted
  as a strict Mode B benchmark result unless Mode B explicitly defines which
  non-lemma declarations are part of the allowed interface.

## Mode R vs Mode B

Mode R is the current productive workflow:

- Keep the current `plan -> per-item -> lock -> replan` harness.
- Use it to discover true residual proof-obligation clusters.
- Record all prompts, replies, checks, helper origins, and hints.
- It is allowed to seed repair declarations when the local proof language needs
  them, as long as this is recorded.

Mode B is still required for a skill-compliant final benchmark:

- Keep only the op-level `corres` contract surface plus external interface.
- Keep code definitions and semantic `defs`.
- Strip private helper statements.
- Treat generated helpers as children of contract roots.
- Report aggregate corpus metrics only after a final green check.
- Record memorization-gap controls.

Current judgment:

- Continue Mode R until the main Schedule_R proof-obligation clusters are mapped
  more clearly.
- Do not report current Mode R results as the final benchmark.
- Use Mode R evidence to design Mode B, not as a substitute for Mode B.

## Document roles

### `regen/regen-campaign-status.md`

Purpose:

- Entry point / dashboard.
- Current state, latest run, immediate blocker, next action.
- Cross-document map and redundancy policy.

Should contain:

- One latest stable state.
- One next-action recommendation.
- Pointers to evidence files.
- A short document inventory.

Should not contain:

- Full run-by-run timeline.
- Full stuck dependency tree.
- Full harness evolution narrative.
- Full paper/protocol background.

### `regen/progress_report.md`

Purpose:

- Detailed Schedule_R progress ledger.
- Run timeline, locked/stuck counts, dependency tree, metrics snapshot, audit
  paths, and current residual proof-obligation structure.

Difference from this file:

- `progress_report.md` is evidence-heavy and Schedule_R-specific.
- `regen-campaign-status.md` is decision-heavy and campaign-level.

### `regen/HARNESS-EVOLUTION.md`

Purpose:

- Chronological defect -> fix -> evidence record for harness design.
- Explains why harness features exist.

Difference from this file:

- It records engineering causality over time.
- It should not be used as the current progress dashboard.

### `reports/regen/<run>/NEEDS-HUMAN.md`

Purpose:

- Per-run generated stuck report.
- Local diagnostic artifact for that run.

Difference from this file:

- It is raw run output, not a campaign summary.
- It should not be hand-edited.

### `reports/proof-maintenance/EXPERIMENT-DESIGN-AND-PROGRESS-20260716.md`

Purpose:

- Higher-level proof-maintenance experiment control sheet.
- Covers zero-human maintainability measurement and related paper direction.

Difference from this file:

- It is broader than regeneration.
- It should mention repo-level regeneration only as an experimental line or
  lesson, not track per-run Schedule_R proof progress.

### `reports/regen/submonad_r-CLOSEOUT.md`

Purpose:

- Closeout for the earlier SubMonad_R regeneration track.

Difference from this file:

- Historical case study.
- Not the active Schedule_R campaign status.

## Redundancy assessment

No file should be deleted solely because topics overlap. The overlap is mostly
between status, progress, and harness rationale, and those should remain
separate.

Functional redundancies to avoid:

- Do not duplicate the full Schedule_R stuck tree in both
  `regen-campaign-status.md` and `regen/progress_report.md`.
- Do not copy the full harness defect history into `progress_report.md`; link
  to `HARNESS-EVOLUTION.md`.
- Do not copy per-run `NEEDS-HUMAN.md` contents into campaign status except as
  summarized blocker categories.
- Do not make `EXPERIMENT-DESIGN-AND-PROGRESS-20260716.md` a second Schedule_R
  status page.

Recommended ownership:

- Current action and latest state: `regen/regen-campaign-status.md`.
- Detailed Schedule_R evidence: `regen/progress_report.md`.
- Harness design rationale: `regen/HARNESS-EVOLUTION.md`.
- Raw run diagnostics: `reports/regen/<run>/NEEDS-HUMAN.md`.
- Paper/protocol framing: `reports/proof-maintenance/EXPERIMENT-DESIGN-AND-PROGRESS-20260716.md`.

## Latest update (2026-07-20 16:17 local)

Latest useful run: `reports/regen/schedule_r-incr-20260720-160436`.

Current checked state:

- `items_locked`: 158
- `items_stuck`: 28
- `machine_locked_lemmas`: 155
- `final_green`: false

Newly locked since the previous dashboard:

- `arch_switch_to_idle_thread_crunches` as `verbatim_decl` in `reports/regen/schedule_r-incr-20260720-152504`
- `switchToIdleThread_corres` in `reports/regen/schedule_r-incr-20260720-154143`
- `ksReadyQueuesL1Bitmap_return_wp` in `reports/regen/schedule_r-incr-20260720-155653`
- `Max_prio_helper` and `curDomain_or_return_0` in `reports/regen/schedule_r-incr-20260720-160436`

Current blocker:

- The active root is now the `chooseThread_corres` bitmap/non-empty queue leaf chain, not `switchToIdleThread_corres`.
- `chooseThread_ready_queues_empty_corres` remains an awkward artificial split: its proof has to discharge an unreachable non-empty branch. The original human proof does not isolate this branch; it proves both branches together inside `chooseThread_corres`.
- The failing leaf chain is `prioToL1Index_le_index` -> `bitmapL1_highest_lookup` -> `lookupBitmapPriority_Max_eqI` -> bitmap queue selection helpers.

Most recent failed run: `reports/regen/schedule_r-incr-20260720-161749`. It inserted smaller replan helpers (`prioToL1Index_less_index`, `bitmapQ_L1_bitD`, `bitmapQ_L2_bitD`, `lookupBitmapPriority_nonempty_ready_queue`, `ready_queue_prio_le_lookupBitmapPriority`) but none were locked in that run.

## Next action

Do not retry `switchToIdleThread_corres`; it is locked. Work on the bitmap lookup leaf chain needed by the non-empty branch of `chooseThread_corres`.

Concrete next unit:

- Retry the newly inserted smaller helpers from `reports/regen/schedule_r-incr-20260720-161749/plan.json`, starting with `prioToL1Index_less_index`, `bitmapQ_L1_bitD`, and `bitmapQ_L2_bitD`.
- If those fail, decide whether to keep decomposing bit arithmetic or seed the original local proof of `bitmapL1_highest_lookup` as a Mode R diagnostic body.
- After `lookupBitmapPriority_Max_eqI` locks, retry `bitmap_lookup_priority_non_empty`, `bitmap_lookup_priority_no_higher_non_empty`, then `guarded_switch_to_chooseThread_fragment_corres`, then the whole `chooseThread_corres`.
