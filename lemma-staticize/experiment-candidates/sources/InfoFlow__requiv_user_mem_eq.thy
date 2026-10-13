(* lemma: requiv_user_mem_eq
   thy: proof/infoflow/ARM/ArchUserOp_IF.thy:817 (proof hot line 859)
   session: InfoFlow
   arm: search  source: both  tier: sweet  tactic: fastforce
   REASON: wasted-classical: fastforce+simp: line at 10.8s, NO split/dest/elim/intro (classical likely wasted), file 1012L (baseline-able). Band of the 2 confirmed fastforce->clarsimp wins.  ||  wasted-classical: fastforce+simp: line at 4.5s, NO split/dest/elim/intro (classical likely wasted), file 1012L (baseline-able). Band of the 2 confirmed fastforce->clarsimp wins.
*)

lemma requiv_user_mem_eq:
  "\<lbrakk> reads_equiv aag s s'; globals_equiv s s'; invs s; invs s'; valid_pdpt_objs s;
     valid_pdpt_objs s'; is_subject aag (cur_thread s); AllowRead \<in> ptable_rights_s s x;
     ptable_lift_s s x = Some y; pas_refined aag s; pas_refined aag s' \<rbrakk>
     \<Longrightarrow> user_mem s (ptrFromPAddr y) = user_mem s' (ptrFromPAddr y)"
  apply (simp add: user_mem_def)
  apply (rule conjI)
   apply clarsimp
   apply (rule context_conjI')
    apply (erule reads_equivE)
    apply (clarsimp simp: in_user_frame_def)
    apply (rule exI)
    apply (rule user_frame_at_equiv)
      apply assumption+
    apply (erule_tac f="underlying_memory" in equiv_forE)
    apply (frule_tac auth=Read in user_op_access_data_at[where s = s])
          apply (fastforce simp: ptable_lift_s_def ptable_rights_s_def vspace_cap_rights_to_auth_def
                 | intro typ_at_user_data_at)+
    apply (rule reads_read)
    apply (fastforce simp: ptrFromPAddr_mask_simp)
   apply clarsimp
   apply (subgoal_tac "aag_can_read aag (ptrFromPAddr y)")
    apply (erule reads_equivE)
    apply clarsimp
    apply (erule_tac f="underlying_memory" in equiv_forE)
    apply simp
   apply (frule_tac auth=Read in user_op_access)
       apply (fastforce simp: ptable_lift_s_def ptable_rights_s_def vspace_cap_rights_to_auth_def)+
   apply (rule reads_read)
   apply simp
  apply (frule requiv_ptable_rights_eq, fastforce+)
  apply (frule requiv_ptable_lift_eq, fastforce+)
  apply (clarsimp simp: globals_equiv_def)
  apply (erule notE)
  apply (erule reads_equivE)
  apply (clarsimp simp: in_user_frame_def)
  apply (rule exI)
  apply (rule user_frame_at_equiv)
    apply assumption+
   apply (erule_tac f="underlying_memory" in equiv_forE)
   apply (erule equiv_symmetric[THEN iffD1])
  apply (frule_tac auth=Read in user_op_access_data_at[where s=s'])
        apply (fastforce simp: ptable_lift_s_def ptable_rights_s_def vspace_cap_rights_to_auth_def
               | intro typ_at_user_data_at)+
  apply (rule reads_read)
  apply (fastforce simp: ptrFromPAddr_mask_simp)
  done
