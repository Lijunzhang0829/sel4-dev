(* lemma: valid_pspace_s0
   thy: proof/infoflow/ARM/Example_Valid_State.thy:1404 (proof hot line 1414)
   session: InfoFlow
   arm: work-suspect  source: db-scan  tier: sweet  tactic: force
   FLAGS: WORK-SUSPECT (cost may be simp-work, not search)
   REASON: db-scan: lemma total 12s, search 12s (frac 1.00), single classical line(s); hot = force carrying 12s.
*)

lemma valid_pspace_s0[simp]:
  "valid_pspace s0_internal"
  apply (simp add: valid_pspace_def pspace_distinct_s0 pspace_aligned_s0 valid_objs_s0)
  apply (rule conjI)
   apply (clarsimp simp: if_live_then_nonz_cap_def)
   apply (subst(asm) s0_internal_def)
   apply (clarsimp simp: live_def hyp_live_def obj_at_def kh0_def kh0_obj_def s0_ptr_defs split: if_split_asm)
     apply (clarsimp simp: ex_nonz_cap_to_def)
     apply (rule_tac x="High_cnode_ptr" in exI)
     apply (rule_tac x="the_nat_to_bl_10 1" in exI)
     apply (force simp: cte_wp_at_cases s0_internal_def kh0_def kh0_obj_def s0_ptr_defs tcb_cap_cases_def High_caps_def the_nat_to_bl_def nat_to_bl_def well_formed_cnode_n_def dom_empty_cnode)
    apply (clarsimp simp: ex_nonz_cap_to_def)
    apply (rule_tac x="Low_cnode_ptr" in exI)
    apply (rule_tac x="the_nat_to_bl_10 1" in exI)
    apply (force simp: cte_wp_at_cases s0_internal_def kh0_def kh0_obj_def s0_ptr_defs tcb_cap_cases_def Low_caps_def the_nat_to_bl_def nat_to_bl_def well_formed_cnode_n_def dom_empty_cnode)
   apply (clarsimp simp: ex_nonz_cap_to_def)
   apply (rule_tac x="High_cnode_ptr" in exI)
   apply (rule_tac x="the_nat_to_bl_10 318" in exI)
   apply (force simp: cte_wp_at_cases s0_internal_def kh0_def kh0_obj_def s0_ptr_defs tcb_cap_cases_def High_caps_def the_nat_to_bl_def nat_to_bl_def well_formed_cnode_n_def dom_empty_cnode)
  apply (rule conjI)
   apply (simp add: Invariants_AI.cte_wp_at_caps_of_state zombies_final_def)
   apply (force dest: s0_caps_of_state simp: is_zombie_def)
  apply (rule conjI)
   apply (clarsimp simp: sym_refs_def state_refs_of_def state_hyp_refs_of_def s0_internal_def)
   apply (subst(asm) kh0_def)
   apply (clarsimp split: if_split_asm)
   apply (simp add: refs_of_def kh0_def s0_ptr_defs kh0_obj_def)+
  apply (clarsimp simp: sym_refs_def state_hyp_refs_of_def s0_internal_def)
  apply (subst(asm) kh0_def)
  apply (clarsimp split: if_split_asm)
             by (simp add: refs_of_def kh0_def s0_ptr_defs kh0_obj_def)+
