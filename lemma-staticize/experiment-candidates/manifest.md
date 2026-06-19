# Experiment candidate set (frozen) — search-space rewrite benchmark

- seed: 42  ·  selected: **46**  ·  pool: 142 target-tactic lemmas
- sources: (A) per-lemma DB scan total_s>=10 & search-dominated; (B) wasted-classical 2-60s band, file<=1600L
- source mix: {'db-scan': 41, 'wasted-classical': 2, 'both': 3}
- **arms: {'hard': 6, 'search': 30, 'work-suspect': 10}**
  - `search` = clean single-line genuine-search target (coverage headline)
  - `work-suspect` = auto/ff/force+simp: with NO rule-chain → cost may be simp-WORK; **counted separately, never as search-coverage**
  - `hard` = slow because complex (many search lines) → negative-result arm
- flags across set: work_suspect=**11**, compound=**2**
- contaminated (already touched in a prior run): **8/46**
- original source of each lemma in `sources/`; selection reason + flags in each file header

## strata

| tier | tactic | n |
|---|---|--:|
| hard | auto | 2 |
| hard | clarsimp | 1 |
| hard | fastforce | 3 |
| mid | auto | 2 |
| mid | fastforce | 2 |
| sweet | auto | 13 |
| sweet | blast | 1 |
| sweet | fastforce | 16 |
| sweet | force | 6 |

## random-10 lightweight test set (file-wall only, small files <=1200L)

| session | lemma | thy:line | tactic | total_s | file_L | tier |
|---|---|---|---|--:|--:|---|
| AInvs | `device_update_invs` | AInvs.thy:71 | fastforce | 10 | 102 | sweet |
| AInvs | `set_cap_valid_arch_caps` | ArchCSpaceInvPre_AI.thy:241 | blast | 11 | 325 | sweet |
| InfoFlow | `abd_affects` | PolicySystemSAC.thy:241 | auto | 13 | 962 | sweet |
| AInvs | `replace_cap_invs` | ArchCSpaceInv_AI.thy:123 | fastforce | 10 | 257 | sweet |
| InfoFlow | `requiv_device_mem_eq` | ArchUserOp_IF.thy:811 | fastforce | 10 | 1012 | sweet |
| InfoFlow | `requiv_user_mem_eq` | ArchUserOp_IF.thy:859 | fastforce | 11 | 1012 | sweet |
| InfoFlow | `invoke_cnode_reads_respects_f` | Syscall_IF.thy:248 | auto | 15 | 1035 | sweet |
| AInvs | `retype_ret_valid_caps_aobj` | ArchUntyped_AI.thy:175 | fastforce | 5 | 597 | sweet |

## all 46 candidates

| ★ | arm | session | lemma | thy:line | tactic | #s | total_s | frac | flags |
|:--:|---|---|---|---|---|--:|--:|--:|---|
|  | hard | CRefine | `receiveIPC_ccorres` | Ipc_C.thy:5503 | fastforce | 139 | 1216 | 0.42 | WS CMPD |
|  | hard | CRefine | `checkCapAt_ccorres` | Tcb_C.thy:447 | auto | 93 | 569 | 0.30 |  |
|  | hard | CRefine | `cancelBadgedSends_ccorres` | Recycle_C.thy:765 | fastforce | 61 | 220 | 0.43 | CMPD |
|  | hard | CRefine | `fastpath_enqueue_ccorres` | Fastpath_C.thy:1418 | auto | 52 | 214 | 0.32 |  |
|  | hard | InfoFlowC | `s0H_pspace_distinct'` | Example_Valid_StateH.thy:1920 | fastforce | 3 | 745 | 0.93 |  |
|  | hard | InfoFlowC | `kh0_pspace_dom` | Example_Valid_StateH.thy:3096 | clarsimp | 49 | 181 | 0.45 |  |
|  | search | AInvs | `blocked_cancel_ipc_invs` | IpcCancel_AI.thy:369 | auto | 1 | 125 | 1.00 |  |
|  | search | AInvs | `ri_invs'` | Ipc_AI.thy:2756 | fastforce | 1 | 25 | 1.00 |  |
| ★ | search | AInvs | `set_cap_valid_arch_caps` | ArchCSpaceInvPre_AI.thy:241 | blast | 1 | 11 | 1.00 |  |
| ★ | search | AInvs | `device_update_invs` | AInvs.thy:71 | fastforce | 1 | 10 | 1.00 |  |
|  | search | AInvs | `complete_signal_invs` | Ipc_AI.thy:2685 | fastforce | 1 | 10 | 1.00 |  |
| ★ | search | AInvs | `replace_cap_invs` | ArchCSpaceInv_AI.thy:123 | fastforce | 1 | 10 | 1.00 |  |
| ★ | search | AInvs | `retype_ret_valid_caps_aobj` | ArchUntyped_AI.thy:175 | fastforce | 1 | 5 | 1.00 |  |
|  | search | Access | `empty_slot_pas_refined` | CNode_AC.thy:1103 | fastforce | 1 | 148 | 1.00 |  |
|  | search | Access | `empty_slot_pas_refined_transferable` | CNode_AC.thy:1117 | fastforce | 1 | 27 | 1.00 |  |
|  | search | Access | `cap_insert_pas_refined` | CNode_AC.thy:1032 | fastforce | 1 | 21 | 1.00 |  |
|  | search | Access | `cte_wp_at_weak_derived_domain_sep_inv_cap` | DomainSepInv.thy:230 | force | 1 | 10 | 1.00 |  |
|  | search | DRefine | `insert_cap_child_corres` | CNode_DR.thy:332 | auto | 1 | 19 | 1.00 |  |
|  | search | DRefine | `insert_cap_sibling_corres` | CNode_DR.thy:255 | auto | 1 | 15 | 1.00 |  |
|  | search | DRefine | `send_sync_ipc_corres` | Ipc_DR.thy:2654 | fastforce | 1 | 14 | 1.00 |  |
|  | search | InfoFlow | `reads_respects_scheduler_invisible_no_domain_switch` | Scheduler_IF.thy:1721 | fastforce | 1 | 61 | 1.00 |  |
|  | search | InfoFlow | `arm_asid_table_delete_ev2` | ArchArch_IF.thy:872 | auto | 1 | 19 | 1.00 |  |
|  | search | InfoFlow | `valid_arch_caps_s0` | Example_Valid_State.thy:1622 | auto | 1 | 16 | 1.00 |  |
| ★ | search | InfoFlow | `invoke_cnode_reads_respects_f` | Syscall_IF.thy:248 | auto | 1 | 15 | 1.00 |  |
| ★ | search | InfoFlow | `abd_affects` | PolicySystemSAC.thy:241 | auto | 1 | 13 | 1.00 |  |
|  | search | InfoFlow | `gets_apply_ready_queues_reads_respects` | Finalise_IF.thy:523 | force | 1 | 12 | 1.00 |  |
| ★ | search | InfoFlow | `requiv_user_mem_eq` | ArchUserOp_IF.thy:859 | fastforce | 1 | 11 | 1.00 |  |
| ★ | search | InfoFlow | `requiv_device_mem_eq` | ArchUserOp_IF.thy:811 | fastforce | 1 | 10 | 1.00 |  |
|  | search | Refine | `inv_untyped_corres'` | Untyped_R.thy:5002 | auto | 1 | 24 | 1.00 |  |
|  | search | Refine | `tcbSchedEnqueue_valid_sched_pointers` | TcbAcc_R.thy:5393 | force | 1 | 16 | 1.00 |  |
|  | search | Refine | `tcbSchedEnqueue_corres` | TcbAcc_R.thy:2670 | auto | 1 | 14 | 1.00 |  |
|  | search | Refine | `setEndpoint_ksDomScheduleIdx` | IpcCancel_R.thy:870 | auto | 1 | 13 | 1.00 |  |
|  | search | Refine | `arch_decodeInvocation_corres` | Arch_R.thy:988 | fastforce | 1 | 12 | 1.00 |  |
|  | search | Refine | `sai_invs'` | Ipc_R.thy:2949 | fastforce | 1 | 12 | 1.00 |  |
|  | search | Refine | `invs_asid_update_strg'` | Finalise_R.thy:2339 | auto | 1 | 10 | 1.00 |  |
|  | search | Refine | `ri_invs'` | Ipc_R.thy:3725 | fastforce | 1 | 10 | 1.00 |  |
|  | work-suspect | AInvs | `update_waiting_ntfn_valid_sched` | DetSchedSchedule_AI.thy:2229 | fastforce | 1 | 48 | 1.00 | WS |
|  | work-suspect | AInvs | `set_scheduler_action_cnt_valid_blocked_except` | DetSchedSchedule_AI.thy:344 | force | 1 | 37 | 1.00 | WS |
|  | work-suspect | AInvs | `simpler_store_pde_def` | ArchVSpace_AI.thy:2766 | auto | 1 | 34 | 1.00 | WS |
|  | work-suspect | InfoFlow | `s0_ptr_defs` | Example_Valid_State.thy:249 | auto | 1 | 126 | 1.00 | WS |
|  | work-suspect | InfoFlow | `set_cap_slots_holding_overlapping_caps_other` | FinalCaps.thy:666 | fastforce | 1 | 30 | 1.00 | WS |
|  | work-suspect | InfoFlow | `SAC_partsSubjectAffects_exceptT` | PolicySystemSAC.thy:921 | auto | 1 | 22 | 1.00 | WS |
|  | work-suspect | InfoFlow | `Sys1AgentMap_simps` | Example_Valid_State.thy:910 | auto | 1 | 22 | 0.96 | WS |
|  | work-suspect | InfoFlow | `dmo_device_state_update_reads_respects_g` | UserOp_IF.thy:151 | fastforce | 1 | 14 | 1.00 | WS |
|  | work-suspect | InfoFlow | `valid_pspace_s0` | Example_Valid_State.thy:1414 | force | 1 | 12 | 1.00 | WS |
|  | work-suspect | Refine | `emptySlot_corres` | Finalise_R.thy:1576 | force | 1 | 20 | 1.00 | WS |
