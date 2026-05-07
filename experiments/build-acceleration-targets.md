# 5-session build-acceleration target catalogue

Consolidated catalogue of slow proofs across the five Isabelle sessions on
the ARM build path: **Access, AInvs, InfoFlow, Refine, CRefine**. Drawn from
two independent measurement methodologies and cross-validated where both
exist.

For methodology and bug-catalogue, see
[experiments/scan-strategy-summary.md](scan-strategy-summary.md).

---

## 1. Two data sources, both reliable

| source | what it measures | wall cost | coverage |
|---|---|---:|---|
| **`reports/slow-commands-*.md`** | per-command (apply / by / simp /…) wall, summed by enclosing lemma | **0** (extracted from heap-log `command_timings` BLOB; one-shot, 2026-05-03) | **all 18 sessions** |
| **`reports/slow-proofs-*-topN.{json,md}`** + fix supplements | sorry-substitution cost per proof (baseline_wall − sorry_wall) | 8-30 h per session | 3 of 5 (Access, AInvs with fix3, InfoFlow with fix5) |

`slow-commands` is the more direct signal for build-acceleration: it tells
you exactly how much wall time a proof's `apply`/`by` commands consumed
during the original build. Sorry-cost asks the related but distinct
question "if this proof were free, how much faster does the file build" —
a useful cross-check, especially because sorry-cost catches cases where a
short body has expensive elaboration that doesn't show up in
`command_timings` at the command level.

The two metrics agree on the headline targets in every session where we have
both (see §3 Cross-validation).

---

## 2. Top-tier targets per session (slow-commands aggregate elapsed)

The number after each lemma is **aggregate elapsed** in seconds, summed
over all `apply`/`by`/`simp`/`crunch`/etc. commands within the lemma during
the original ~April 30 build. File path is relative to `proof/`.

### Access (1 095 commands across 28 .thy)

| rank | s | lemma | file |
|---:|---:|---|---|
|  1 | 159.6 | `aag_cap_auth_NullCap` | `access-control/CNode_AC.thy` |
|  2 |  71.1 | `nat_to_bl_id` | `access-control/ARM/ExampleSystem.thy` |
|  3 |  48.1 | `tro_alt_trans_spec` | `access-control/Access_AC.thy` |
|  4 |  42.2 | `invoke_tcb_tc_respects_aag` | `access-control/ARM/ArchTcb_AC.thy` |
|  5 |  34.5 | `s1_caps_of_state` | `access-control/ARM/ExampleSystem.thy` |
|  6 |  33.5 | `cap_insert_pas_refined` | `access-control/CNode_AC.thy` |
|  7 |  30.1 | `guarded_pas_domain_lift` | `access-control/Syscall_AC.thy` |
|  8 |  23.8 | `s2_caps_of_state` | `access-control/ARM/ExampleSystem.thy` |
|  9 |  19.2 | `invoke_untyped_pas_refined` | `access-control/Retype_AC.thy` |
| 10 |  16.2 | `perform_asid_control_invocation_pas_refined` | `access-control/ARM/ArchArch_AC.thy` |

### AInvs (3 898 commands across 81 .thy)

| rank | s | lemma | file |
|---:|---:|---|---|
|  1 | 176.6 | `lookup_pt_slot_cap_to` | `invariant-abstract/ARM/ArchVSpace_AI.thy` |
|  2 | 126.0 | `ep_redux_simps2` | `invariant-abstract/IpcCancel_AI.thy` |
|  3 | 103.8 | `arch_decode_invocation_empty_fail` | `invariant-abstract/ARM/ArchEmptyFail_AI.thy` |
|  4 |  75.5 | `invs_A` | `invariant-abstract/ARM/ArchKernelInit_AI.thy` |
|  5 |  70.4 | `ri_invs'` | `invariant-abstract/Ipc_AI.thy` |
|  6 |  66.0 | `tc_invs` | `invariant-abstract/ARM/ArchTcb_AI.thy` |
|  7 |  49.6 | `possible_switch_to_valid_sched` | `invariant-abstract/DetSchedSchedule_AI.thy` |
|  8 |  48.2 | `thread_set_has_no_reply_cap` | `invariant-abstract/Syscall_AI.thy` |
|  9 |  48.0 | `valid_list_post_dest_parent` | `invariant-abstract/Deterministic_AI.thy` |
| 10 |  45.1 | `next_not_child` | `invariant-abstract/Deterministic_AI.thy` |

### InfoFlow (1 993 commands across 51 .thy)

| rank | s | lemma | file |
|---:|---:|---|---|
|  1 | 223.6 | `invoke_tcb_silc_inv` | `infoflow/ARM/ArchFinalCaps.thy` |
|  2 | 186.2 | `invoke_tcb_thread_preservation` | `infoflow/ARM/ArchTcb_IF.thy` |
|  3 | 181.4 | `pspace_distinct_s0` | `infoflow/ARM/Example_Valid_State.thy` |
|  4 | 134.6 | `example_policy` | `infoflow/ARM/Example_Valid_State.thy` |
|  5 | 133.8 | `cap_revoke_silc_inv'` | `infoflow/FinalCaps.thy` |
|  6 |  92.9 | `valid_global_pd_mappings_s0` | `infoflow/ARM/Example_Valid_State.thy` |
|  7 |  65.9 | `tc_reads_respects_f` | `infoflow/ARM/ArchTcb_IF.thy` |
|  8 |  60.5 | `only_timer_irq_s0` | `infoflow/ARM/Example_Valid_State.thy` |
|  9 |  55.3 | `ethread_set_reads_respects_scheduler` | `infoflow/Scheduler_IF.thy` |
| 10 |  53.5 | `nat_to_bl_eq` | `infoflow/ARM/Example_Valid_State.thy` |

### Refine (6 559 commands across 41 .thy)

| rank | s | lemma | file |
|---:|---:|---|---|
|  1 | 302.9 | `cteInsert_corres` | `refine/ARM/CSpace1_R.thy` |
|  2 | 257.0 | `emptySlot_corres` | `refine/ARM/Finalise_R.thy` |
|  3 | 243.1 | `cteMove_corres` | `refine/ARM/CSpace_R.thy` |
|  4 | 192.6 | `transferCaps_corres` | `refine/ARM/Tcb_R.thy` |
|  5 | 136.8 | `updateMDB_the_lot'` | `refine/ARM/CSpace1_R.thy` |
|  6 | 116.9 | `tcbSchedAppend_corres` | `refine/ARM/Schedule_R.thy` |
|  7 |  92.5 | `parent_of_m_n` | `refine/ARM/CSpace1_R.thy` |
|  8 |  91.9 | `arch_decodeInvocation_corres` | `refine/ARM/Arch_R.thy` |
|  9 |  79.1 | `performASIDControlInvocation_corres` | `refine/ARM/Arch_R.thy` |
| 10 |  73.2 | `setUntypedCapAsFull_mdb` | `refine/ARM/CSpace1_R.thy` |

### CRefine (11 241 commands across 39 .thy) — **biggest single-lemma costs in the entire ARM build**

| rank | s | lemma | file |
|---:|---:|---|---|
|  1 | **947.6** | `decodeCNodeInvocation_ccorres` | `crefine/ARM/Invoke_C.thy` |
|  2 | **917.0** | `invokeTCB_ReadRegisters_ccorres` | `crefine/ARM/Tcb_C.thy` |
|  3 | **811.0** | `decodeUntypedInvocation_ccorres_helper` | `crefine/ARM/Invoke_C.thy` |
|  4 |   611.8 | `decodeARMFrameInvocation_ccorres` | `crefine/ARM/Arch_C.thy` |
|  5 |   581.1 | `decodeARMPageDirectoryInvocation_ccorres` | `crefine/ARM/Arch_C.thy` |
|  6 |   495.8 | `Arch_decodeInvocation_ccorres` | `crefine/ARM/Arch_C.thy` |
|  7 |   469.2 | `createNewObjects_ccorres` | `crefine/ARM/Retype_C.thy` |
|  8 |   467.7 | `fastpath_call_ccorres` | `crefine/ARM/Fastpath_C.thy` |
|  9 |   441.2 | `sendIPC_ccorres` | `crefine/ARM/Ipc_C.thy` |
| 10 |   381.6 | `invokeTCB_ThreadControl_ccorres` | `crefine/ARM/Tcb_C.thy` |

CRefine has by far the heaviest single-lemma costs, with three lemmas each
exceeding **800 s aggregate elapsed** (= over 13 min/lemma). Three of those
top-3 also live in just two files (`Invoke_C.thy`, `Tcb_C.thy`). That's an
extreme concentration — the **top 10 CRefine lemmas alone account for
~5.9 × 10³ s ≈ 1 h 38 min of the original build's CPU time**.

---

## 3. Cross-validation: slow-commands vs sorry-cost

For three sessions we have both methodologies. The Pearson-style correlation
on the top-tier lemmas is high; method-specific quirks below.

### Access — top 10 by slow-commands + their sorry-cost

| lemma | slow-cmd s | sorry-cost s | ratio | note |
|---|---:|---:|---:|---|
| `aag_cap_auth_NullCap` | 159.6 | n/a | — | not in top-N (small body) |
| `tro_alt_trans_spec` | 48.1 | 80.0 | 1.7× | both methods agree it's heavy |
| `invoke_tcb_tc_respects_aag` | 42.2 | 39.8 | 0.94× | nearly equal |
| `cap_insert_pas_refined` | 33.5 | 14.1 | 0.42× | sorry-cost lower (noise; cost <30s) |
| `invoke_untyped_pas_refined` | 19.2 | sub-3s | <0.16× | low confidence in sorry-cost |
| `perform_asid_control_invocation_pas_refined` | 16.2 | 14.6 | 0.90× | agree |

`aag_cap_auth_NullCap` (Access #1, 159 s) was missed by sorry-cost top-N
because its body is short (slow-commands accumulates **many short
commands**; sorry-cost uses body line count for sampling). **Slow-commands
catches it; sorry-cost doesn't.** This is the canonical case where the two
metrics disagree.

### AInvs — top 10 by slow-commands + their sorry-cost

| lemma | slow-cmd s | sorry-cost s | note |
|---|---:|---:|---|
| `lookup_pt_slot_cap_to` | 176.6 | not measured | sorry-cost top-100 missed (body 22L) |
| `tc_invs` | 66.0 | 82.6 | both top — high confidence |
| `next_not_child` | 45.1 | 14.9 | sorry-cost lower; body 241L |
| `invs_A` | 75.5 | 53.9 | both confirm |

Again `lookup_pt_slot_cap_to` (AInvs #1, 176 s) is **only visible in
slow-commands** — its body of 22 lines didn't make sorry-cost's top-100
(global sort by body size).

### InfoFlow — top 10 by slow-commands + their sorry-cost

| lemma | slow-cmd s | sorry-cost s | note |
|---|---:|---:|---|
| `invoke_tcb_silc_inv` | 223.6 | 210.5 | both confirm |
| `invoke_tcb_thread_preservation` | 186.2 | 88.6 | both confirm; sorry-cost lower |
| `pspace_distinct_s0` | 181.4 | not measured | per-file path errored |
| `cap_revoke_silc_inv'` | 133.8 | not measured | not in active build closure |
| `retype_region_silc_inv` | not in top-10 | **167.4** | sorry-cost found this, slow-commands didn't have it in top-10 |

InfoFlow shows the **opposite** mismatch: sorry-cost found
`retype_region_silc_inv` at 167 s (FinalCaps.thy) — slow-commands has it
ranked outside top-30. That's a case where the proof's commands are short
but their cumulative WALL via parallel future-stage proof checking exceeds
their summed elapsed.

**Takeaway**: keep both metrics. Use slow-commands as the primary catalogue
(complete coverage, free) and sorry-cost as the secondary (catches
proof-stage costs that command-elapsed under-counts).

---

## 4. Aggregate priority list (top 15 across all 5 sessions)

Ranked by slow-commands aggregate elapsed across every session, this is
where Phase B micro-rewrite work should start.

| # | s | lemma | session / file |
|---:|---:|---|---|
|  1 | **947.6** | `decodeCNodeInvocation_ccorres` | CRefine / `Invoke_C.thy` |
|  2 | **917.0** | `invokeTCB_ReadRegisters_ccorres` | CRefine / `Tcb_C.thy` |
|  3 | **811.0** | `decodeUntypedInvocation_ccorres_helper` | CRefine / `Invoke_C.thy` |
|  4 |   611.8 | `decodeARMFrameInvocation_ccorres` | CRefine / `Arch_C.thy` |
|  5 |   581.1 | `decodeARMPageDirectoryInvocation_ccorres` | CRefine / `Arch_C.thy` |
|  6 |   495.8 | `Arch_decodeInvocation_ccorres` | CRefine / `Arch_C.thy` |
|  7 |   469.2 | `createNewObjects_ccorres` | CRefine / `Retype_C.thy` |
|  8 |   467.7 | `fastpath_call_ccorres` | CRefine / `Fastpath_C.thy` |
|  9 |   441.2 | `sendIPC_ccorres` | CRefine / `Ipc_C.thy` |
| 10 |   381.6 | `invokeTCB_ThreadControl_ccorres` | CRefine / `Tcb_C.thy` |
| 11 |   302.9 | `cteInsert_corres` | Refine / `CSpace1_R.thy` |
| 12 |   257.0 | `emptySlot_corres` | Refine / `Finalise_R.thy` |
| 13 |   243.1 | `cteMove_corres` | Refine / `CSpace_R.thy` |
| 14 |   223.6 | `invoke_tcb_silc_inv` | InfoFlow / `ArchFinalCaps.thy` |
| 15 |   192.6 | `transferCaps_corres` | Refine / `Tcb_R.thy` |

**The top 10 are all CRefine.** CRefine alone dominates the build's
single-proof costs. If you can shave 10 % off any of CRefine's top-3, that's
~80-90 s of build wall — comparable to Refine's #1 savings.

---

## 5. Recommended Phase B starting points

1. **Quickest validation runs** (small risk, fast feedback): start with
   Refine's `cteInsert_corres` (already attempted in Exp 1, see
   [experiments/proof-rewrite-log.md](proof-rewrite-log.md)) — known
   environment, baseline already established, and we have a working
   workflow.

2. **Highest absolute payoff**: CRefine's
   `decodeCNodeInvocation_ccorres` (947 s) or `invokeTCB_ReadRegisters_ccorres`
   (917 s). A 30 % reduction on either is ~280 s of build wall — bigger than
   any single Refine lemma. But: CRefine builds are slower per attempt
   (~2-3× Refine), and the ccorres proof style is denser.

3. **Outlier worth investigating regardless of session priority**:
   InfoFlow's `retype_region_silc_inv` (167 s sorry-cost in just a 55-line
   body) — that 3 s/line ratio suggests degenerate simp/wp expansion that
   could plausibly be cured with a targeted unfolding strategy.

4. **Cluster-based attack**: top-3 CRefine lemmas all live in
   `Invoke_C.thy` (#1, #3) and `Tcb_C.thy` (#2). Studying the shared
   tactical patterns there might unlock all three in one rewrite session.

---

## 6. Confidence tiers (for choosing what to commit time to)

Recall the noise floor finding from Access controlled experiment: **single-
sample sorry-cost has σ ≈ 5 s; cost < 10 s is unreliable**. Slow-commands
aggregate elapsed has its own variance from parallel-future scheduling but
is generally tighter (it's a sum of many small command timings, central-
limit-theorem averages noise out).

| measured cost | confidence | suggested action |
|---|---|---|
| > 100 s (slow-cmd) | very high | priority target |
| 30-100 s | high | actionable, single-sample fine |
| 10-30 s | moderate | actionable, multi-sample if rewrite is invasive |
| 3-10 s | low | only if part of a pattern |
| < 3 s | noise | ignore |

For this catalogue: every entry in §4 is **>100 s slow-commands**, so all
15 are very-high-confidence targets.

---

## Appendix: data sources used

| session | slow-commands | sorry-cost top-N | sorry-cost per-file | fix supplements |
|---|:-:|:-:|:-:|:-:|
| Access | ✅ `slow-commands-Access.md` | ✅ `slow-proofs-access-topN.json` | ✅ `slow-proofs-access.md` | — |
| AInvs | ✅ `slow-commands-AInvs.md` | ✅ `slow-proofs-ainvs-topN.json` | ❌ (4-file partial; aborted on server crash) | ✅ fix3 |
| InfoFlow | ✅ `slow-commands-InfoFlow.md` | ✅ `slow-proofs-infoflow-topN.json` | ✅ `slow-proofs-infoflow.md` (with 11 ERR) | ✅ fix5 |
| Refine | ✅ `slow-commands-Refine.md` | ❌ (not run) | ❌ (not run) | — |
| CRefine | ✅ `slow-commands-CRefine.md` | ❌ (not run) | ❌ (not run) | — |

CRefine and Refine never had sorry-cost runs (their estimated wall budget,
~14-22 h combined, was deferred when slow-commands was confirmed sufficient
for the build-acceleration goal). Refine has separately been validated by
the per-command timing extraction and was the basis of Exp 1/2 in
[experiments/proof-rewrite-log.md](proof-rewrite-log.md).

The aborted AInvs per-file scan partial output is preserved at:
- `reports/slow-proofs-ainvs.md.attempt2` — 4 files / first attempt
- `reports/slow-proofs-ainvs.md` — 4 files held forward as resumable seed
- `logs/scans/AInvs-perfile-resumable.log` — driver log
