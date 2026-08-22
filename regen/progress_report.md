# Schedule_R regeneration progress report

Last updated: 2026-07-20 14:36 local

This file records the detailed repo-level rewrite progress for
`verification/l4v/proof/refine/ARM/Schedule_R.thy`. For the current campaign
dashboard and next action, start with `regen/regen-campaign-status.md`. This
file is intentionally separate from `HARNESS-EVOLUTION.md`: that file records
harness design changes; this file records proof-regeneration progress and the
evidence supporting it.

## Scope and current interpretation

The current productive harness is `regen/regen_incr.py`.

It is best classified as Mode R: repair / obligation-discovery harness. It has
been useful for discovering latent proof obligations, locking checked proofs,
and preserving prompt/response/check artifacts, but it is not yet the strict
Mode B benchmark required by `.claude/skills/isabelle_prover_regen/SKILL.md`.

Reason: the current plan is still `plan -> per-lemma`; it keeps many fixed
statements from the existing theory and asks the model to fill bodies or propose
helpers. The skill-compliant benchmark must instead keep only the op-level
`corres` contract surface plus external interface, delete private proof
interior statements, and measure the regenerated corpus as a whole.

Progress below is therefore progress of the current Mode R Schedule_R
regeneration, not a final skill-compliant benchmark result.

## Evidence sources checked

Structured run artifacts:

- `reports/regen/schedule_r-incr-*/measurement.json`
- `reports/regen/schedule_r-incr-*/locked.json`
- `reports/regen/schedule_r-incr-*/stuck.json`
- `reports/regen/schedule_r-incr-*/plan.json`
- `reports/regen/schedule_r-incr-*/NEEDS-HUMAN.md`
- Per-item audit files under `reports/regen/<run>/items/*/`

Design/history artifacts:

- `regen/HARNESS-EVOLUTION.md`
- `reports/proof-maintenance/EXPERIMENT-DESIGN-AND-PROGRESS-20260716.md`
- `regen/repo_metrics.py`
- `.claude/skills/isabelle_prover_regen/SKILL.md`

Claude project transcripts:

- `/home/lijun/.claude/projects/-home-lijun-seL4-docker-main/*.jsonl`
- `/home/lijun/.claude/projects/-tmp/*.jsonl`

The Claude transcript archive contains both early project-level conversations
and one-lemma / replan prompts. Representative searches found transcripts for
SubMonad_R whole-theory failures, Schedule_R per-item generation, replan prompts,
`setCurThread_corres`, `idleThreadNotQueued_def`, `tcbSchedAppend_*`, and
`chooseThread_corres`.

Audit caveat: the transcript archive is searchable and broadly consistent with
`HARNESS-EVOLUTION.md`, but this report does not claim every historical statement
has a stable transcript line reference. The per-run `reports/regen/...` artifacts
are the stronger source for current progress.

## Current latest run

Latest fully summarized baseline run:

`reports/regen/schedule_r-incr-20260720-021707`

Measurement:

- `final_green`: false
- `items_total`: 106
- `items_locked`: 147
- `items_stuck`: 26
- `machine_locked_lemmas`: 146
- `checks_used`: 2
- `human_supplied_bodies`: []
- `hint_assisted_items`: [`chooseThread_corres`]
- `helper_origin_split`: `plan=27`, `judgment=2`, `replan=43`
- `machine_lines`: 0
- `machine_lemmas`: 0

Important interpretation:

- `items_total=106` is the current plan size.
- `items_locked=147` includes generated helpers, so it can exceed the original
  plan size.
- `machine_lines=0` and `machine_lemmas=0` do not mean no proofs are locked.
  They mean the measurement script only fills final machine LOC/lemma counts
  after a final green corpus exists. This is a known progress-accounting gap.

Latest locked distribution from `locked.json`:

- fixed items: 74
- generated/helper items: 72
- verbatim definitions: 1

Latest plan distribution from `plan.json`:

- plan items: 106
- fixed items: 97
- helper items: 8
- verbatim definitions: 1
- fixed item names ending in `_corres`: 19

Note: the skill/design discussion records 26 op-level `corres` contracts for
Schedule_R. The current Mode R plan exposes 19 fixed names ending in `_corres`.
This discrepancy is expected to be resolved by a proper Mode B contract extractor;
the current Mode R plan is not a contract-surface oracle.

## Progress timeline

The following table uses only runs with `measurement.json`.

| Run | Green | Plan items | Locked items | Stuck items | Checks | Notes |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `schedule_r-incr-20260703-154356` | false | 137 | 89 | 63 | 270 | Early incremental Schedule_R run. |
| `schedule_r-incr-20260704-172305` | false | 131 | 106 | 67 | 166 | Replan helpers start appearing in measurement. |
| `schedule_r-incr-20260706-003655` | false | 107 | 116 | 51 | 30 | Post quota/transient handling era. |
| `schedule_r-incr-20260706-080609` | false | 104 | 117 | 49 | 13 | Incremental state preserved, fewer checks in this run. |
| `schedule_r-incr-20260711-114638` | false | 105 | 117 | 41 | 8 | Stuck count reduced. |
| `schedule_r-incr-20260711-225630` | false | 121 | 129 | 34 | 132 | Larger replan/helper expansion. |
| `schedule_r-incr-20260712-162837` | false | 109 | 138 | 31 | 54 | Basis for first repo-level metrics in HARNESS-EVOLUTION entry 26. |
| `schedule_r-incr-20260719-165007` | false | 103 | 146 | 26 | 0 | Resumed/assembled state; no new checks recorded. |
| `schedule_r-incr-20260720-011543` | false | 103 | 146 | 27 | 4 | Retry around chooseThread path. |
| `schedule_r-incr-20260720-012723` | false | 103 | 146 | 27 | 4 | Retry around chooseThread path. |
| `schedule_r-incr-20260720-014747` | false | 107 | 146 | 26 | 6 | Replan expansion. |
| `schedule_r-incr-20260720-015917` | false | 106 | 146 | 26 | 6 | `chooseThread_corres` marked hint-assisted. |
| `schedule_r-incr-20260720-021707` | false | 106 | 147 | 26 | 2 | Locked `chooseThread_ksReadyQueues_asrt_from_relation`. |

The monotonic improvement is visible in locked items: 89 -> 147. The corpus is
still not green.

## Latest focused run

Latest focused run:

`reports/regen/schedule_r-incr-20260720-122928`

Purpose:

- Continue the `chooseThread_corres` residual ready-queue cluster.
- Seed the missing internal helper `ready_qs_runnable_cross` after the prior
  focused run showed Codex cited this original ARM helper but it was absent from
  the regenerated prefix.

Measurement:

- `final_green`: false
- `items_total`: 108
- `items_locked`: 149
- `items_stuck`: 29
- `machine_locked_lemmas`: 148
- `checks_used`: 10
- `human_supplied_bodies`: []
- `hint_assisted_items`: [`chooseThread_corres`, `chooseThread_ready_qs_runnable_from_relation`, `ready_qs_runnable_cross`]
- `helper_origin_split`: `plan=27`, `judgment=3`, `replan=44`

Newly locked items:

- `ready_qs_runnable_cross`
- `chooseThread_ready_qs_runnable_from_relation`

New unresolved items introduced by replan:

- `chooseThread_ready_queues_empty_corres`
- `chooseThread_ready_queues_nonempty_corres`

Important interpretation:

- The ready-queue runnable bridge is now closed in Mode R.
- `chooseThread_ready_queues_corres` was too large; replan split it into idle
  and non-idle branch helpers.
- The idle branch immediately exposed missing dependency
  `switchToIdleThread_corres`.
- The non-idle branch remains a one-goal branch-corres obligation involving
  bitmap/highest-priority/queue-head/switch-to-thread alignment.
- `chooseThread_corres` is no longer simply stuck on missing
  `ready_qs_runnable_cross`; it is now gated by branch dependencies, including
  `switchToIdleThread_corres`, `guarded_switch_to_chooseThread_fragment_corres`,
  and ultimately `switchToThread_corres`.

Seed/audit files:

- `reports/regen/schedule_r-readyq-cross-seed/plan.json`
- `reports/regen/schedule_r-readyq-cross-hints.json`
- `reports/regen/schedule_r-incr-20260720-122928/items/91-ready_qs_runnable_cross/*`
- `reports/regen/schedule_r-incr-20260720-122928/items/92-chooseThread_ready_qs_runnable_from_relation/*`
- `reports/regen/schedule_r-incr-20260720-122928/items/93-chooseThread_ready_queues_corres/replan.ndjson.*`


Additional focused runs after `122928`:

- `reports/regen/schedule_r-incr-20260720-124612`: locked three switch-to-idle helper lemmas (`switchToIdleThread_getIdleThread_corres`, `arch_switch_to_idle_thread_invs_wp`, `getIdleThread_invs_no_cicd_wp`), raising stable locked proof records to 152. `switchToIdleThread_corres` still failed.
- `reports/regen/schedule_r-incr-20260720-142656`: after adding harness support for `verbatim_decl`, locked the original ARM proof method `add_ready_qs_runnable` as a declaration, raising stable locked records to 153.
- `reports/regen/schedule_r-incr-20260720-143057`: retried `switchToIdleThread_corres`, `chooseThread_ready_queues_empty_corres`, and `chooseThread_corres` with the method in the sealed prefix. They still failed; `switchToIdleThread_corres` is now blocked on six WP/corres_split schematic goals around `arch_switch_to_idle_thread`, `Arch.switchToIdleThread`, `gets idle_thread`, and `getIdleThread`.

Latest stable state:

- `items_locked`: 153
- `items_stuck`: 30
- `machine_locked_lemmas`: 151
- `checks_used` in latest retry: 5
- `human_supplied_bodies`: []

## Recently closed item

The focused run `schedule_r-incr-20260720-021707` locked this helper:

```isabelle
lemma chooseThread_ksReadyQueues_asrt_from_relation:
  "\<lbrakk>(s, s') \<in> state_relation; (invs and valid_sched) s;
    all_invs_but_ct_idle_or_in_cur_domain' s'\<rbrakk>
   \<Longrightarrow> ksReadyQueues_asrt s'"
  by (erule ksReadyQueues_asrt_cross)
```

Evidence:

- `reports/regen/schedule_r-incr-20260720-021707/locked.json`
- `reports/regen/schedule_r-incr-20260720-021707/items/90-chooseThread_ksReadyQueues_asrt_from_relation/gen-1.ndjson`
- matching `.prompt.txt`, `.final.txt`, and `.meta.json` files in the same item directory

This confirms that the `chooseThread_corres` retry/replan path discovered at
least one real latent bridge obligation and successfully locked it.

The later focused run `schedule_r-incr-20260720-122928` then locked:

```isabelle
lemma ready_qs_runnable_cross:
  "\<lbrakk>(s, s') \<in> state_relation; pspace_aligned s; pspace_distinct s; valid_queues s\<rbrakk>
   \<Longrightarrow> ready_qs_runnable s'"
```

and:

```isabelle
lemma chooseThread_ready_qs_runnable_from_relation:
  "\<lbrakk>(s, s') \<in> state_relation; (invs and valid_sched) s;
    all_invs_but_ct_idle_or_in_cur_domain' s'; ksReadyQueues_asrt s'\<rbrakk>
   \<Longrightarrow> ready_qs_runnable s'"
```

## Current unresolved stuck items

Baseline `stuck.json` contains 26 stuck records; latest focused run contains 29 after adding two branch helpers and re-parking `chooseThread_corres`:

- `switchToThread_invs`
- `chooseThread_it`
- `guarded_switch_to_corres`
- `guarded_switch_to_chooseThread_fragment_corres`
- helper `guarded_switch_to_corres`
- `scheduleChooseNewThread_corres`
- `schedule_ct_activatable'`
- `Arch_switchToThread_tcb_in_cur_domain'`
- `schedule_corres`
- `tcbSchedAppend_valid_objs'`
- `tcbSchedAppend_iflive'`
- helper `tcbSchedAppend_all_invs_but_ct_not_inQ'`
- `tcbSchedAppend_invs'`
- `tcbSchedDequeue_invs'`
- `tcbSchedDequeue_invs_no_cicd'`
- `arch_switch_thread_ksQ`
- helper `getQueue_corres`
- `isHighestPrio_corres`
- `tcbSchedAppend_corres`
- helper `ready_qs_runnable_switchToIdle`
- `setThreadState_rct`
- helper `chooseThread_invs_no_cicd'`
- `scheduleChooseNewThread_ct_activatable'`
- `possibleSwitchTo_corres`
- helper `tcbQueueAppend_corres`
- helper `invs_valid_sched_ksReadyQueues_asrt`
- helper `chooseThread_ready_queues_empty_corres`
- helper `chooseThread_ready_queues_nonempty_corres`
- `chooseThread_corres` re-parked in latest focused run

These stuck records are not all independent roots. Many are deferred because they depend
on an unresolved lower-level obligation.

## Current dependency view

This tree is a synthesis from `NEEDS-HUMAN.md`, `stuck.json`, recent item
artifacts, and the prior local analysis of Schedule_R blockers. It should be
treated as the current working dependency map, not as a mechanically extracted
proof DAG.

```text
schedule_corres
├─ scheduleChooseNewThread_corres
│  └─ scheduleChooseNewThread_fragment_corres
│     └─ chooseThread_corres
│        ├─ chooseThread_ksReadyQueues_asrt_from_relation  [GREEN]
│        ├─ ready_qs_runnable_cross                       [GREEN]
│        ├─ chooseThread_ready_qs_runnable_from_relation   [GREEN]
│        ├─ chooseThread_ready_queues_corres
│        │  ├─ chooseThread_ready_queues_empty_corres      [STUCK: needs switchToIdleThread_corres]
│        │  └─ chooseThread_ready_queues_nonempty_corres   [STUCK: one branch-corres goal]
│        ├─ guarded_switch_to_chooseThread_fragment_corres
│        │  └─ switchToThread_corres
│        ├─ switchToIdleThread_corres
│        ├─ isHighestPrio_corres
│        └─ getQueue / isRunnable / ready-queue relation path
├─ guarded_switch_to_corres
│  └─ switchToThread_corres
│     ├─ arch_switchToThread_corres
│     ├─ tcbSchedDequeue_corres
│     ├─ setCurThread_corres  [GREEN]
│     ├─ switchToThread_invs
│     │  └─ setCurThread_invs  [missing dependency in old artifact]
│     └─ arch_switch_thread_ksQ
├─ tcbSchedAppend_corres
│  ├─ tcbQueueAppend_corres
│  ├─ tcbSchedAppend_valid_objs'
│  ├─ tcbSchedAppend_iflive'
│  ├─ tcbSchedAppend_all_invs_but_ct_not_inQ'
│  │  └─ tcbSchedAppend_iflive'
│  └─ tcbSchedAppend_invs'
│     └─ tcbSchedAppend_all_invs_but_ct_not_inQ'
├─ possibleSwitchTo_corres
│  └─ unresolved schematic / wp orchestration
└─ schedule_ct_activatable'
   └─ scheduleChooseNewThread_ct_activatable'
```

Highest-priority unresolved leaf/root candidates:

- `switchToIdleThread_corres`
- `guarded_switch_to_chooseThread_fragment_corres`
- `chooseThread_ready_queues_empty_corres`
- `chooseThread_ready_queues_nonempty_corres`
- `tcbQueueAppend_corres`
- `tcbSchedAppend_iflive'`
- `tcbSchedAppend_valid_objs'`
- `arch_switch_thread_ksQ`
- `isHighestPrio_corres`
- `possibleSwitchTo_corres`

Reason: these are closer to actual proof failures or missing bridge lemmas;
many other stuck items are deferred consumers.

## Known solved structural issues

These are harness or proof-organization issues already addressed and recorded in
`HARNESS-EVOLUTION.md`:

- Whole-theory rewrite was replaced by incremental lock-in.
- Tool-less Claude invocation was enforced after detecting tool leakage.
- Theory header/body assembly was made deterministic.
- Transient API/quota handling was separated from proof failure.
- Timeout localization and strategy hints were added.
- Import fact retrieval was added and later broadened to full ancestor imports.
- Per-item stuck queue and auto-unpark logic were added.
- Alias declarations were converted into proof obligations where needed.
- Cluster budget guards were added to avoid one hard root consuming all checks.
- Incremental heap sealing reduced whole-prefix checking cost.
- Import-shadowing helper names were guarded against.
- `defs` commands such as `idleThreadNotQueued_def` are now kept as verbatim definitions.
- Codex relay provider support and per-call prompt/raw/final/meta audit capture were added.
- Historical cases from previous Claude/Codex attempts are now available to the harness.

## Quality metrics snapshot

Latest metrics command:

```bash
python3 regen/repo_metrics.py \
  reports/regen/schedule_r-incr-20260720-143057/locked.json \
  verification/l4v/proof/refine/ARM/Schedule_R.thy
```

Latest measured values:

- Machine helper reuse: `n=77`, median `0`, mean `0.2468`, max `2`, one-off `79.22%`.
- Human support reuse within Schedule_R: `n=141`, median `0`, mean `0.6454`, max `7`, one-off `63.83%`.
- Machine fixed fan-out: median `0`, mean `0.27`, max `4`.
- Human `corres` fan-out: median `2`, mean `2.16`, max `13`.
- Support/target ratio: machine `77/74 = 1.04`, human `141/19 = 7.42`.
- Machine helpers: `77`.
- Import name collisions / reinventions: `14`.
- Net-new reusable helpers: `12`.
- One-off scaffolding helpers: `51`.
- Net reuse verdict: negative, because import reinvention still exceeds net-new reusable helpers.

Interpretation:

The machine is still much more local and monolithic than the human proof corpus.
It locks many obligations, but its generated helper structure is less reusable
and less compositional than the human library-style development.

## repo_metrics.py coverage gap

`regen/repo_metrics.py` is a proof-engineering quality scoreboard, not a complete
progress tracker.

It currently records:

- generality via helper downstream reuse
- compositionality via local fan-out and support/target ratio
- dedup via helper name collisions with imports

It does not record:

- final/partial proof progress across runs
- op-level contract coverage
- unresolved stuck dependency tree
- audit completeness by item
- human assistance ledger
- wall-time and generation-time totals
- final machine LOC and lemma count before green completion
- maintainability metric
- memorization-gap controls
- Mode B skill-compliance status

Therefore progress must currently be reconstructed from multiple files. This
report is the consolidated manual record until a dedicated progress reporter is
implemented.

## Current assessment

The rewrite is substantially progressed but incomplete.

The latest Mode R state has 153 checked/locked records and 30 stuck records.
The main remaining blockers are not random individual lemmas; they are proof
obligation clusters around ready queues, heap-list queue correspondence,
bitmap/highest-priority reasoning, switch-to-thread invariants, and wp
orchestration.

Using `stuck` as the primary generation unit is not ideal. `stuck` is an audit
state: it records where the harness stopped. The better generation unit is a
residual proof-obligation cluster rooted at an op-level `corres` contract. A
single `stuck` item may be only a symptom of a missing lower-level bridge lemma,
and one generated helper can unlock multiple deferred stuck items.

## Immediate next work

Recommended next proof-generation order:

1. Stop treating `chooseThread_ready_queues_corres` as one large helper. Work on
   the branch dependencies exposed by replan.
2. Prioritize `switchToIdleThread_corres`, but do not keep retrying the whole body unchanged. The next unit is its six-goal WP/corres_split residual around `arch_switch_to_idle_thread`, `Arch.switchToIdleThread`, `gets idle_thread`, and `getIdleThread`.
3. Retry `chooseThread_ready_queues_empty_corres` only after `switchToIdleThread_corres` locks.
4. For the non-empty branch, keep using imported `TcbAcc_R.getQueue_corres` and
   existing bitmap facts; do not regenerate local `getQueue_corres` if the imported
   `TcbAcc_R.getQueue_corres` is sufficient.
5. Revisit `tcbQueueAppend_corres` as the likely root for the `tcbSchedAppend_*`
   family.
6. Revisit `arch_switch_thread_ksQ` and `isHighestPrio_corres` as independent
   hard roots.
7. Implement a dedicated progress reporter to make this file reproducible:
   `regen/progress_report.py` should emit JSON and Markdown from run artifacts.

## Required next harness step for skill compliance

Do not report the current Mode R run as the final `isabelle_prover_regen`
benchmark.

To make the experiment skill-compliant, add Mode B as a separate driver or
explicit mode:

- Extract only op-level `corres` contracts plus external interface.
- Keep code definitions and `defs`-style semantic definitions.
- Strip private helper statements from the oracle.
- Treat generated helpers as discovered children of contract roots.
- Emit aggregate corpus metrics only after a final green check.
- Record memorization-gap controls and audit completeness with the run.

## Update: switch idle closed, bitmap leaf blocker (2026-07-20 16:17 local)

The `switchToIdleThread_corres` blocker is now closed in Mode R. The successful path was:

- Add original ARM `crunches arch_switch_to_thread, arch_switch_to_idle_thread ...` as `arch_switch_to_idle_thread_crunches` (`verbatim_decl`).
- Update the `switchToIdleThread_corres` hint to remove the stale `state_relation_def/cdt_relation_def` step and use fully qualified guard facts (`ArchBits_AI.ARM.*`, `Invariants_AI.*`).
- Lock `switchToIdleThread_corres` in `reports/regen/schedule_r-incr-20260720-154143`.

The next root moved to `chooseThread_corres`. Directly retrying `chooseThread_ready_queues_empty_corres` exposed that the helper split is artificial: it has to prove an unreachable non-empty branch separately, whereas the original proof handles both branches together using `corres_gets_queues_getReadyQueuesL1Bitmap` and `corres_if2`.

Additional locked helpers:

- `ksReadyQueuesL1Bitmap_return_wp`: `reports/regen/schedule_r-incr-20260720-155653`
- `Max_prio_helper`: `reports/regen/schedule_r-incr-20260720-160436`
- `curDomain_or_return_0`: `reports/regen/schedule_r-incr-20260720-160436`

Current hard blocker:

- `bitmapL1_highest_lookup` / `lookupBitmapPriority_Max_eqI` and their arithmetic/bitmap children.
- Latest failed run: `reports/regen/schedule_r-incr-20260720-161749`.
- Replan introduced smaller child candidates: `prioToL1Index_less_index`, `bitmapQ_L1_bitD`, `bitmapQ_L2_bitD`, `lookupBitmapPriority_nonempty_ready_queue`, `ready_queue_prio_le_lookupBitmapPriority`.

Latest useful measured state: `items_locked=158`, `items_stuck=28`, `machine_locked_lemmas=155`, `final_green=false`.

## Update: unresolved leaf corres scan (2026-07-20)

Using `corres` as the top-level regeneration unit should not create new
spec-Haskell contract obligations. New generated `*_corres` names can appear,
but they should be treated as internal bridge/fragment helpers unless they are
part of the extracted op-level contract surface. This distinction matters:
`chooseThread_ready_queues_empty_corres` was a generated helper, not a true new
contract. It failed because it split the proof before the branch discriminator
`corres_gets_queues_getReadyQueuesL1Bitmap` had been established.

Latest scan base: `reports/regen/schedule_r-incr-20260720-161749`.

Current unresolved leaf/corres blockers:

- `chooseThread_corres`: active root. It is blocked on bitmap/non-empty queue
  selection, not on `switchToIdleThread_corres` anymore. Leaf chain:
  `prioToL1Index_less_index`, `bitmapQ_L1_bitD`, `bitmapQ_L2_bitD`,
  `lookupBitmapPriority_nonempty_ready_queue`,
  `ready_queue_prio_le_lookupBitmapPriority`, then
  `lookupBitmapPriority_Max_eqI` / `bitmapL1_highest_lookup`.
- `chooseThread_ready_queues_nonempty_corres`: artificial branch helper; still
  contains the full if-program and should not be the primary generation unit.
  Prefer proving the enclosing `chooseThread_corres` after discriminator and
  bitmap leaf helpers lock.
- `getQueue_corres`: latest failure leaves a single queue-relation goal with
  `tcbSchedNexts_of s'` / `tcbSchedPrevs_of s'` not connected to the concrete
  state variable introduced in the goal. This looks like a state-variable
  plumbing/helper issue, not a new contract.
- `isHighestPrio_corres`: latest failure is pure logic around empty ready queues
  and `Max {prio. ready_queues ...}`. This is a simplification/helper gap.
- `tcbQueueAppend_corres`: latest failure was an undefined helper
  `heap_path_append_single`; this is a missing heap-list queue helper/import
  issue.
- `tcbSchedAppend_corres`: blocked after entering the append path. Residuals are
  the core append correspondence and a `stateAssert ksReadyQueues_asrt` no-fail
  side condition. Depends on the queue append/getQueue helper family.
- `switchToThread_corres`: latest attempt hit the 600s search-explosion guard.
  The proof body composes `arch_switchToThread_corres`,
  `tcbSchedDequeue_corres`, and `setCurThread_corres`; the blocker is tactic
  granularity/search, likely requiring more explicit WP/intermediate lemmas.
- `guarded_switch_to_corres` and
  `guarded_switch_to_chooseThread_fragment_corres`: currently dependent on
  `switchToThread_corres`, not independent leaves.
- `possibleSwitchTo_corres`: latest generated body failed with `No such variable
  in theorem: ?Q`, a malformed schematic-instantiation script. This should be
  recoverable by tactic sanitization/retry; it is not evidence of a new contract.
- `scheduleChooseNewThread_fragment_corres`, `scheduleChooseNewThread_corres`,
  and `schedule_corres`: downstream of `chooseThread_corres`,
  `scheduleChooseNewThread_fragment_corres`, and `thread_get_comm`; not current
  leaf roots.

Current non-corres leaf blockers that still gate final green:

- `tcbSchedAppend_valid_objs'`, `tcbSchedAppend_iflive'`
- `tcbSchedDequeue_invs'`, `tcbSchedDequeue_invs_no_cicd'`
- `arch_switch_thread_ksQ`, `setThreadState_rct`
- `chooseThread_invs_no_cicd'`, `scheduleChooseNewThread_ct_activatable'`
- `invs_valid_sched_ksReadyQueues_asrt`, `ready_qs_runnable_switchToIdle`

Conclusion: the remaining failures are mostly recoverable engineering failures
(missing helper, bad branch split, undefined/import fact, tactic search
explosion, malformed generated script). The genuinely hard parts are the bitmap
word-arithmetic chain and heap-list queue correspondence chain.

## Update: generated `*_corres` helper policy and bitmap leaf attempts (2026-07-20)

Clarification: op-level `corres` lemmas are the task-defined spec-Haskell
contract surface. Replan may generate local correspondence-shaped bridge lemmas,
but these should not be treated as new ground-truth contracts. The helper
`chooseThread_ready_queues_empty_corres` was generated because the harness split
`chooseThread_corres` before the branch discriminator was available. Its function
was to prove the empty-ready-queue branch, but the statement still retained the
whole concrete `if l1 \<noteq> 0 then ... else ...`, forcing an unreachable
non-empty branch obligation. This is a harness/unit-selection bug, not a new
corres requirement.

Harness change made in `regen/regen_incr.py`: replan prompts now instruct the
model not to name generated helpers as contract-level `*_corres` facts unless
the name is already a fixed ground-truth item. Local correspondence helpers
should use role names such as `_bridge`, `_fragment`, `_branch`, or `_case`, and
branch splits should wait until the discriminator correspondence is established.

Bitmap leaf progress after this clarification:

- Locked `prioToL1Index_le_index` and `word_log2_priority_bound` in
  `reports/regen/schedule_r-incr-20260720-171907`.
- Corrected bad generated statements for `bitmapQ_L1_bitD` and
  `bitmapQ_L2_bitD`; both locked in
  `reports/regen/schedule_r-incr-20260720-172348`.
- Locked `prioToL1Index_le_mask` in
  `reports/regen/schedule_r-incr-20260720-173747`.
- Locked `bitmapQ_lookupBitmapPriority_simp` in
  `reports/regen/schedule_r-incr-20260720-181250`.

Current hard local blocker in the bitmap chain:

- `bitmapL1_highest_lookup` and `lookupBitmapPriority_Max_eqI` still do not lock
  after the above dependencies, hints, and multiple replan attempts.
- Latest evidence: `reports/regen/schedule_r-incr-20260720-182006`.
- Why this is hard: `bitmapL1_highest_lookup` is the semantic bridge from
  bitmap membership (`bitmapQ d p' s`) to priority ordering
  (`p' <= lookupBitmapPriority d s`). Its proof must show that
  `lookupBitmapPriority` chooses the highest set L1 bit and, in the same L1
  bucket, the highest set L2 bit. This crosses several fragile Isabelle word
  domains at once: `word_log2`, `of_nat`/`unat`, `priority` words, 32-bit bitmap
  words, `prioToL1Index`, `l1IndexToPrio`, `mask wordRadix`, `||`, and
  `&&`.
- The repeated residual goals are not ordinary missing simplifications. They
  include bounds such as
  `unat (word_of_nat (word_log2 (...)) :: priority) < 2 ^ wordRadix`, size goals
  for a casted `word_log2`, and the same-L1 low-bit comparison
  `p' && mask wordRadix <= lookupBitmapPriority d s && mask wordRadix`.
  These require a precise ordering of `word_log2_highest`, `word_log2_max`,
  `unat_of_nat_eq`, `less_mask_eq`, `prioToL1Index_l1IndexToPrio_or_id`, and
  bitmap orphan invariants.
- `lookupBitmapPriority_Max_eqI` is syntactically short but hard by dependency:
  it uses `Max_eqI` to identify `lookupBitmapPriority` with the maximum concrete
  non-empty ready queue priority. Its main obligation is exactly the
  `bitmapL1_highest_lookup` ordering fact, plus existence from
  `bitmapQ_from_bitmap_lookup` / `valid_bitmapQ_bitmapQ_simp`.
- Replan now proposes smaller bridge helpers:
  `bitmapQ_l1_lookup_index_bridge`, `bitmapQ_same_l1_lookup_mask_bridge`,
  `bitmapQ_lookup_priority_bridge`, `lookupBitmapPriority_bitmapQ_bridge`, and
  `bitmapQ_prio_le_lookupBitmapPriority_bridge`.
- This is the strongest current evidence of an LLM/proof-search limitation on
  the word-level bitmap arithmetic segment, not evidence that the overall
  `chooseThread_corres` contract is wrong.

Latest useful state: `items_locked=164`, `items_stuck=31`,
`machine_locked_lemmas=161`, `final_green=false`.

## Update: hard lemma mode added (2026-07-21)

The harness now treats difficult lemma failures differently from ordinary proof
retry. It classifies hard failures, injects category-specific hard-mode guidance
into item/replan prompts, and records attempts in `hard_lemma_pool.json`. This
is enabled by default; `--no-hard-lemma-mode` disables it for ablations.

Smoke test artifact: `reports/regen/hard-lemma-mode-smoke-20260721`.

Three historical unresolved cases were used as regression checks:

- `bitmapL1_highest_lookup`: classified as `word_bitmap_arithmetic`. This is the
  desired route because the blocker is the `word_log2`/`unat`/mask/priority
  ordering bridge, not a new corres contract.
- `switchToThread_corres`: classified as `timeout_search_explosion` and
  `corres_composition`. This is the desired route because the blocker is broad
  search while composing `arch_switchToThread_corres`, `tcbSchedDequeue_corres`,
  `setCurThread_corres`, and state assertions.
- `possibleSwitchTo_corres`: classified as `syntax_schematic_repair`. This is
  the desired route because the observed failure was malformed schematic tactic
  usage (`?Q`), so APOLLO-style repair should precede semantic lemma mining.

This change does not by itself prove the remaining leaves. It changes the next
run's behavior: repeated hard failures should now produce auditable hard-profile
records and smaller helper proposals tied to residual obligation classes.

## Update: structural `corres` composition failures and leaf diagnosis (2026-07-21)

A new failure class has been recorded separately from hard lemma mining:
structural `corres` composition failure. `possibleSwitchTo_corres` is the
clearest current example. The first failure looked like a schematic/syntax
repair issue, but after precheck repair the focused run
`reports/regen/schedule_r-incr-20260721-001730` still timed out while composing
already-known scheduler correspondences, cross guards, scheduler-action case
splits, and WP side-conditions.

Important policy: original human proof bodies may be read only after failure for
diagnosis. They must not be used as generation context, proof-order templates,
or replay scripts. The harness should use generic repair actions learned from
residual goals and checked probes instead.

Current leaf-style blockers by class:

- `possibleSwitchTo_corres`: structural composition/search-control failure, not
  currently a hard new lemma. It should be attacked by generic composition-mode
  search: smaller guard obligations, localized timeout probes, restricted WP,
  and one-combinator-at-a-time repair.
- `switchToThread_corres`: similar structural composition/search explosion. It
  composes existing scheduler/thread correspondences and state assertions; it is
  not locked yet.
- `getQueue_corres`: queue relation/state-variable plumbing around scheduler
  queue fields. This is likely solvable by the optimized harness once queue
  relation premises are force-retrieved.
- `tcbQueueAppend_corres`: heap-list queue append semantics. Likely solvable by
  heap-list hard mode plus forced positive premises/probes, but still unresolved.
- `isHighestPrio_corres`: mixed queue/bitmap/Max bridge. Hard mode made real
  progress by locking `ready_queues_l1_bitmap_empty_bridge`,
  `bitmapQ_nonempty_queue_bridge`, and `ready_queue_nonempty_bitmapQ_bridge`,
  but the main correspondence still fails on the Max/bitmap equivalence.
- `bitmapL1_highest_lookup` / `lookupBitmapPriority_Max_eqI`: genuinely hard
  word-arithmetic and Max-set chain. This remains the strongest evidence of a
  model/proof-search ceiling in the current harness.

The optimized hard-lemma harness can probably solve the queue-plumbing leaves,
but the latest run exposed a missing implementation step: textual hints alone
were not enough for `ready_queues_relation_nonempty_bridge`; the fact
`list_queue_relation_nil` must be force-retrieved or injected via a checked
positive exemplar. That change is still pending.

Latest useful state from the focused runs: hard mode increased locked items from
164 to 167, but no remaining leaf `corres` was fully locked. The failure is now
better classified rather than fully solved.

## Update: standalone structural-composition probe smoke (2026-07-21)

A separate module, `regen/structural_composition_probe.py`, was added for testing
structural `corres` composition diagnosis before merging anything into the main
harness. It checks prefixes of generated proof attempts, closes each prefix with
`oops`, and records the first deterministic error or timeout. It supports sealed
heap mode via `--seal-run` because late Schedule_R full-prefix checks are too
slow for prefix search.

Smoke results:

- `possibleSwitchTo_corres`: first timeout localized to the broad guard-side
  `fastforce` after `corres_cross_add_guard`. This is a composition/search-control
  issue; split guard obligations and avoid broad state-relation automation.
- `switchToThread_corres`: first timeout localized to a bare `apply fastforce`
  after `corres_stateAssert_ignore`. Earlier symbolic-execution/stateAssert
  prefixes execute quickly.
- `isHighestPrio_corres`: first deterministic error localized to `corres_gets`,
  exposing the known `ready_queues`/`Max`/`lookupBitmapPriority` equivalence. It
  should remain in bitmap-Max hard-lemma mode rather than structural mode.

Conclusion: the module is feasible and useful as a diagnostic layer. It should be
kept standalone until it can propose local repairs from checked-prefix results.

## Update: structural repair candidate smoke (2026-07-21)

The standalone structural probe now has local repair testing. It can generate
repair candidates for the first bad prefix and optionally append a few original
generated suffix steps to see whether the repair actually allows progress.

Observed results:

- `possibleSwitchTo_corres`: `fastforce_to_blast` is a real progress candidate.
  It replaces the timeout-heavy broad `fastforce` with `blast` over the same
  explicit `intro`/`dest` facts, and the repaired prefix supports a `+3` suffix
  continuation. `clarsimp`/`simp` only expose side-condition errors. Naive guard
  splitting still times out.
- `switchToThread_corres`: `clarsimp`/`simp` avoid the timeout at the bare
  `apply fastforce`, but continuation reaches a concrete error around
  `corres_split[OF tcbSchedDequeue_corres]`. This is useful for localization and
  future premise retrieval, not a direct proof repair yet.

A targeted reaper was added because timeout probes left PolyML child processes
running under the structural probe report path; those stale processes caused one
false step-1 timeout. The module now cleans up only its own stale probe PolyML
processes.

## Update: possibleSwitchTo fastforce-to-blast closure attempt (2026-07-21)

fastforce_to_blast is confirmed useful: it removes the first guard-side timeout. Full closure still fails at deterministic branch-structure issues around scheduler_action case splitting and schematic corres branch obligations. This is a proof-structure/instantiation blocker, not a hard semantic lemma blocker.


## Update: possibleSwitchTo branch-aware retry status (2026-07-21)

Clarification: a `branch` in the current harness discussion means an Isabelle
proof-state subgoal produced by `case`/`if` splitting, not a whole `.thy` rewrite
and not an arbitrary LLM generation call. The relevant unit is smaller than a
lemma attempt: it is one residual proof branch under local assumptions.

Focused run `reports/regen/schedule_r-incr-20260721-065301` retried
`possibleSwitchTo_corres` with the structural-composition harness enabled. It
locked three generated bridge helpers:

- `possibleSwitchTo_pspace_aligned_bridge`
- `possibleSwitchTo_pspace_distinct_bridge`
- `possibleSwitchTo_target_tcb_bridge`

The main `possibleSwitchTo_corres` did not lock. It progressed to a
scheduler-action branch-composition failure: the generated proof could enter the
`curDomain` / `thread_get` / scheduler-action structure, but left residual
branch `corres` and guard obligations after `corres_guard_imp` / `corres_if`.

Focused run `reports/regen/schedule_r-incr-20260721-072417` retried from the
167-locked prefix. Try 1 reached a 2-subgoal state: one `tcb_at' t` guard
obligation plus the remaining main `corres`. Try 2 regressed to a 3-subgoal
branch/schematic state. Final measurement remained:

- `final_green`: false
- `items_total`: 135
- `items_locked`: 167
- `items_stuck`: 31
- `machine_locked_lemmas`: 164
- `checks_used`: 2

Assessment: `possibleSwitchTo_corres` is still unresolved, but its failure class
has sharpened. It is no longer primarily the original broad `fastforce` timeout
or a missing semantic lemma. The current blocker is branch proof orchestration:
a promising prefix exists, but the harness lacks a real branch-subgoal search
layer that can capture each residual branch, retrieve local premises, prove the
branches independently, and then reassemble the final script.


## Update: possibleSwitchTo probe boundary after branch hints (2026-07-21)

Further focused retries:

- `reports/regen/schedule_r-incr-20260721-073655`: still not green. Try 1
  correctly moved toward bridge-intro style but cited nonexistent
  `st_tcb_at_tcb_at'`; try 2 removed that invented fact and returned to a
  scheduler-action branch residual.
- `reports/regen/schedule_r-incr-20260721-074901`: still not green. Both tries
  stabilized at 2 residual goals rather than timing out or expanding to many
  branch goals. This is progress in failure shape but not a lock.

Probe evidence:

- `reports/regen/structural-composition-probe/possibleSwitchTo-guard-blast-20260721`
  confirms `apply (blast intro: possibleSwitchTo_target_tcb_bridge)` is the
  right guard-side local repair. Prefixes through `corres_split[OF curDomain_corres]`
  check; the first deterministic failure is the next generated step
  `corres_split[OF thread_get_corres[where r="(=)"]]`, which leaves five
  schematic pre/postcondition subgoals.
- `reports/regen/structural-composition-probe/possibleSwitchTo-ethread-guard-blast-20260721`
  shows that a naive switch to `curDomain_corres'` + `ethread_get_when_corres`
  is not enough; it fails earlier at the cur-domain split with three residual
  goals.

Current status of `possibleSwitchTo_corres`: unresolved. The strongest diagnosis
is branch/composition orchestration, not lemma hardness. The proof needs a
checked prefix/branch search that can select among nearby composition rules,
inspect residual subgoals, and solve branch-local obligations independently.
Plain whole-body retry with stronger text hints is now producing stable but
insufficient 2-goal failures.

Latest useful state remains:

- `items_locked`: 167
- `items_stuck`: 31
- `machine_locked_lemmas`: 164
- `final_green`: false
## Update: Refine KHeap_R L7 seal (2026-07-22)

The session-DAG campaign has completed Refine.KHeap_R as
RegenRefineARM_L7. Five fixed contracts are green: getEndpoint_corres,
setObject_other_corres, setEndpoint_corres, setNotification_corres, and
getNotification_corres. The formal source has no sorry, oops, axiomatization,
or admit command. A child theory imported the L7 heap and resolved all five
roots.

KHeap exposed three repo-level planning requirements that the earlier structural
checkpoints did not test:

- Contract-local semantic declarations need explicit constant identity checks;
  otherwise Isabelle can parse an undeclared identifier as a free variable.
- Framework inventory needs the consumable theorem closure, including aliases,
  attributes, representation rewrites, and their downstream corollaries.
- A consumer-driven ancestor backpatch invalidates every generated descendant
  heap and requires a DAG-ordered rebuild plus an old/new identity ledger.

The L7 heap is 6,375,827 bytes with SHA-256
99af543c756a34d709f08afa03c51b51e3acff3504032f4233ac65c5a9058283.
No human Refine heap was loaded. Because human KHeap proof comparison was
explicitly requested during repair, this milestone is marked current-session
contaminated and is not an independent regeneration measurement.

The DAG now reports 12 sealed theories. Refine.Init_R and Refine.SubMonad_R are
ready next. EmptyFail and Machine_R remain structural interfaces that later
consumers may still invalidate.

## Update: Refine Init_R/SubMonad_R L8 wave (2026-07-22)

The next ready DAG layer, Refine.Init_R and Refine.SubMonad_R, completed as the
RegenRefineARM_L8 structural checkpoint. Both theories have zero local fixed
operation contracts. Their independent external Codex planner and critique calls
returned reviewed empty interfaces: Init_R had no reachable contract summaries;
SubMonad_R saw 24 nearest ArchAcc_R contracts, but those statements did not
establish a reusable theorem shape owned by SubMonad_R.

Both preflights checked the exact RegenRefineARM_L7 heap identity, found zero
human Refine premise leaks, and stayed below the 120,000-character limit. The
context builder filtered 15,246 frozen-parent/endpoint premises, then separately
extracted generated-ancestor declarations from the legal import closure. Init_R
received three ranked generated premises and may import generated KHeap_R;
SubMonad_R received four and may import generated EmptyFail plus KHeap_R.

The two theories checked and built independently, then built together as
RegenRefineARM_L8. The combined 4,288,835-byte heap has SHA-256
1613022c566b8533b7cbe039856257c3c2c5bb2611d082a6ee85b0201d89c1e7;
a child theory importing both nodes passed. Both immutable generations have two
successful real Codex calls, complete prompt/raw/final/stderr/meta/audit records,
no tool events, zero replay calls, and production audit seals.

The DAG now has 14 sealed theories. Refine.ArchAcc_R is the only ready node and
owns 24 fixed corres contracts.

## Update: L8 planning-quality correction (2026-07-22)

A post-seal review separated transcript integrity from planning quality. All
four Codex calls remain valid production evidence, and Init_R's empty interface
is terminal because it has no target-session descendants. SubMonad_R's empty
interface is now classified as provisional rather than accepted architecture:
the old prompt biased toward empty, omitted the direct consumer path and
contract attributes, and its critique did not adversarially test the result.
SubMonad_R has been added to `invalidatable_theories` until ArchAcc_R's 24 fixed
contracts are proof-green or produce a residual-driven backpatch.

The support planner now uses complete non-overlapping contract clusters, exact
candidate statements, consumer import paths, attributes, and per-contract
shared-symbol counts. Its critique explicitly searches for false-empty plans.
The immutable L8 generations were not changed; the hash-linked review is in
`planning-quality/L8-empty-interface-review.json`.
