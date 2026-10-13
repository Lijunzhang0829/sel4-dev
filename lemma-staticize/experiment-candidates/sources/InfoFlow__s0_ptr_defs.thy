(* lemma: s0_ptr_defs
   thy: proof/infoflow/ARM/Example_Valid_State.thy:233 (proof hot line 249)
   session: InfoFlow
   arm: work-suspect  source: db-scan  tier: mid  tactic: auto
   FLAGS: WORK-SUSPECT (cost may be simp-work, not search)
   REASON: db-scan: lemma total 126s, search 126s (frac 1.00), single classical line(s); hot = auto carrying 126s.
*)

lemmas s0_ptr_defs =
  Low_cnode_ptr_def High_cnode_ptr_def Silc_cnode_ptr_def ntfn_ptr_def irq_cnode_ptr_def
  Low_pd_ptr_def High_pd_ptr_def Low_pt_ptr_def High_pt_ptr_def Low_tcb_ptr_def
  High_tcb_ptr_def idle_tcb_ptr_def timer_irq_def Low_prio_def High_prio_def Low_time_slice_def
  Low_domain_def High_domain_def init_irq_node_ptr_def init_globals_frame_def init_global_pd_def
  kernel_base_def shared_page_ptr_virt_def

(* Distinctness proof of kernel pointers. *)

distinct ptrs_distinct [simp]:
  Low_tcb_ptr High_tcb_ptr idle_tcb_ptr
  Low_pt_ptr High_pt_ptr
  shared_page_ptr_virt ntfn_ptr
  Low_pd_ptr High_pd_ptr
  Low_cnode_ptr High_cnode_ptr Silc_cnode_ptr irq_cnode_ptr
  init_globals_frame init_global_pd
  by (auto simp: s0_ptr_defs)
