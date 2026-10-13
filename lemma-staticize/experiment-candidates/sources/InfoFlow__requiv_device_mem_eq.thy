(* lemma: requiv_device_mem_eq
   thy: proof/infoflow/ARM/ArchUserOp_IF.thy:780 (proof hot line 811)
   session: InfoFlow
   arm: search  source: both  tier: sweet  tactic: fastforce
   REASON: wasted-classical: fastforce+simp: line at 10.5s, NO split/dest/elim/intro (classical likely wasted), file 1012L (baseline-able). Band of the 2 confirmed fastforce->clarsimp wins.  ||  wasted-classical: fastforce+simp: line at 4.7s, NO split/dest/elim/intro (classical likely wasted), file 1012L (baseline-able). Band of the 2 confirmed fastforce->clarsimp wins.
*)

lemma requiv_device_mem_eq:
  "\<lbrakk> reads_equiv aag s s'; globals_equiv s s'; invs s; invs s'; valid_pdpt_objs s;
     valid_pdpt_objs s'; is_subject aag (cur_thread s); AllowRead \<in> ptable_rights_s s x;
     ptable_lift_s s x = Some y; pas_refined aag s; pas_refined aag s' \<rbrakk>
     \<Longrightarrow> device_mem s (ptrFromPAddr y) = device_mem s' (ptrFromPAddr y)"
  apply (simp add: device_mem_def)
  apply (rule conjI)
   apply (erule reads_equivE)
   apply (clarsimp simp: in_device_frame_def)
   apply (rule exI)
   apply (rule device_frame_at_equiv)
     apply assumption+
   apply (erule_tac f="underlying_memory" in equiv_forE)
   apply (frule_tac auth=Read in user_op_access_data_at[where s=s])
         apply (fastforce simp: ptable_lift_s_def ptable_rights_s_def vspace_cap_rights_to_auth_def
                | intro typ_at_device_data_at)+
   apply (rule reads_read)
   apply (fastforce simp: ptrFromPAddr_mask_simp)
  apply clarsimp
  apply (frule requiv_ptable_rights_eq, fastforce+)
  apply (frule requiv_ptable_lift_eq, fastforce+)
  apply (clarsimp simp: globals_equiv_def)
  apply (erule notE)
  apply (erule reads_equivE)
  apply (clarsimp simp: in_device_frame_def)
  apply (rule exI)
  apply (rule device_frame_at_equiv)
    apply assumption+
   apply (erule_tac f="underlying_memory" in equiv_forE)
   apply (erule equiv_symmetric[THEN iffD1])
  apply (frule_tac auth=Read in user_op_access_data_at[where s=s'])
        apply (fastforce simp: ptable_lift_s_def ptable_rights_s_def vspace_cap_rights_to_auth_def
               | intro typ_at_device_data_at)+
  apply (rule reads_read)
  apply (fastforce simp: ptrFromPAddr_mask_simp)
  done
