(* lemma: kh0_pspace_dom
   thy: proof/infoflow/refine/ARM/Example_Valid_StateH.thy:3056 (proof hot line 3096)
   session: InfoFlowC
   arm: hard  source: db-scan  tier: hard  tactic: clarsimp
   REASON: db-scan: lemma total 181s, search 82s (frac 0.45), 49 classical line(s); hot = clarsimp carrying 20s.
*)

lemma kh0_pspace_dom:
  "pspace_dom kh0 = {init_globals_frame, idle_tcb_ptr, High_tcb_ptr, Low_tcb_ptr,
              irq_cnode_ptr, ntfn_ptr} \<union>
             irq_node_offs_range \<union>
             cnode_offs_range Silc_cnode_ptr \<union>
             cnode_offs_range High_cnode_ptr \<union>
             cnode_offs_range Low_cnode_ptr \<union>
             pd_offs_range init_global_pd \<union>
             pd_offs_range High_pd_ptr \<union>
             pd_offs_range Low_pd_ptr \<union>
             pt_offs_range High_pt_ptr \<union>
             pt_offs_range Low_pt_ptr"
  supply nonzero_gt_zero[simp] gt_zero_nonzero[simp]
  apply (rule equalityI)
   apply (simp add: dom_def pspace_dom_def)
   apply clarsimp
   apply (clarsimp simp: kh0_def obj_relation_cuts_def pd_offs_in_range pt_offs_in_range
                         cnode_offs_in_range irq_node_offs_in_range s0_ptrs_aligned pageBits_def
                         kh0_obj_def cte_map_def' caps_dom_length_10
                  split: if_split_asm)
  apply (clarsimp simp: pspace_dom_def dom_def)
  apply (rule conjI)
   apply (rule_tac x=init_globals_frame in exI)
   apply (clarsimp simp: kh0_def kh0_obj_def s0_ptr_defs image_def)
   apply (rule_tac x=0 in exI)
   apply simp
  apply (rule conjI)
   apply (rule_tac x=idle_tcb_ptr in exI)
   apply (clarsimp simp: kh0_def kh0_obj_def s0_ptr_defs image_def)
  apply (rule conjI)
   apply (rule_tac x=High_tcb_ptr in exI)
   apply (clarsimp simp: kh0_def kh0_obj_def s0_ptr_defs image_def)
  apply (rule conjI)
   apply (rule_tac x=Low_tcb_ptr in exI)
   apply (clarsimp simp: kh0_def kh0_obj_def s0_ptr_defs image_def)
  apply (rule conjI)
   apply (rule_tac x=irq_cnode_ptr in exI)
   apply (clarsimp simp: kh0_def kh0_obj_def s0_ptr_defs image_def cte_map_def)
  apply (rule conjI)
   apply (rule_tac x=ntfn_ptr in exI)
   apply (clarsimp simp: kh0_def kh0_obj_def s0_ptr_defs image_def)
  apply (rule conjI)
   apply clarsimp
   apply (rule_tac x=x in exI)
   apply (drule offs_range_correct)
   apply clarsimp
   apply (force simp: kh0_def kh0_obj_def image_def cte_map_def')
  apply (rule conjI)
   apply clarsimp
   apply (rule_tac x=Silc_cnode_ptr in exI)
   apply (drule offs_range_correct)
   apply (force simp: kh0_def kh0_obj_def image_def s0_ptr_defs cte_map_def' dom_caps)
  apply (rule conjI)
   apply clarsimp
   apply (rule_tac x=High_cnode_ptr in exI)
   apply (drule offs_range_correct)
   apply (force simp: kh0_def kh0_obj_def image_def s0_ptr_defs cte_map_def' dom_caps)
  apply (rule conjI)
   apply clarsimp
   apply (rule_tac x=Low_cnode_ptr in exI)
   apply (drule offs_range_correct)
   apply (force simp: kh0_def kh0_obj_def image_def s0_ptr_defs cte_map_def' dom_caps)
  apply (rule conjI)
   apply clarsimp
   apply (rule_tac x=init_global_pd in exI)
   apply (drule offs_range_correct)
   apply (force simp: kh0_def kh0_obj_def image_def s0_ptr_defs)
  apply (rule conjI)
   apply clarsimp
   apply (rule_tac x=High_pd_ptr in exI)
   apply (drule offs_range_correct)
   apply (force simp: kh0_def kh0_obj_def image_def s0_ptr_defs)
  apply (rule conjI)
   apply clarsimp
   apply (rule_tac x=Low_pd_ptr in exI)
   apply (drule offs_range_correct)
   apply (force simp: kh0_def kh0_obj_def image_def s0_ptr_defs)
  apply (rule conjI)
   apply clarsimp
   apply (rule_tac x=High_pt_ptr in exI)
   apply (drule offs_range_correct)
   apply (force simp: kh0_def kh0_obj_def image_def s0_ptr_defs)
  apply clarsimp
  apply (rule_tac x=Low_pt_ptr in exI)
  apply (drule offs_range_correct)
  apply (force simp: kh0_def kh0_obj_def image_def s0_ptr_defs)
  done
