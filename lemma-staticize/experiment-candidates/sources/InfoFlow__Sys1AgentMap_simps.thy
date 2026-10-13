(* lemma: Sys1AgentMap_simps
   thy: proof/infoflow/ARM/Example_Valid_State.thy:893 (proof hot line 910)
   session: InfoFlow
   arm: work-suspect  source: db-scan  tier: sweet  tactic: auto
   FLAGS: WORK-SUSPECT (cost may be simp-work, not search)
   REASON: db-scan: lemma total 22s, search 21s (frac 0.96), single classical line(s); hot = auto carrying 21s.
*)

lemma Sys1AgentMap_simps:
  "Sys1AgentMap Low_cnode_ptr = partition_label Low"
      "Sys1AgentMap High_cnode_ptr = partition_label High"
      "Sys1AgentMap ntfn_ptr = partition_label High"
      "Sys1AgentMap irq_cnode_ptr = partition_label IRQ0"
      "Sys1AgentMap Silc_cnode_ptr = SilcLabel"
      "Sys1AgentMap Low_pd_ptr = partition_label Low"
      "Sys1AgentMap High_pd_ptr = partition_label High"
      "Sys1AgentMap Low_pt_ptr = partition_label Low"
      "Sys1AgentMap High_pt_ptr = partition_label High"
      "Sys1AgentMap Low_tcb_ptr = partition_label Low"
      "Sys1AgentMap High_tcb_ptr = partition_label High"
      "Sys1AgentMap idle_tcb_ptr = partition_label Low"
      "\<And>p. p \<in> ptr_range shared_page_ptr_virt pageBits
          \<Longrightarrow> Sys1AgentMap p = partition_label Low"
  unfolding Sys1AgentMap_def
  apply simp_all
  by (auto simp: s0_ptr_defs ptr_range_def pageBits_def)
