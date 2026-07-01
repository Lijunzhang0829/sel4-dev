(* lemma: complete_signal_invs
   thy: proof/invariant-abstract/Ipc_AI.thy:2684 (proof hot line 2685)
   session: AInvs
   arm: search  source: db-scan  tier: sweet  tactic: fastforce
   REASON: db-scan: lemma total 10s, search 10s (frac 1.00), single classical line(s); hot = fastforce carrying 10s.
*)

lemma complete_signal_invs:
  "\<lbrace>invs and tcb_at tcb\<rbrace>
     complete_signal ntfnptr tcb
   \<lbrace>\<lambda>_. invs\<rbrace>"
  apply (simp add: complete_signal_def)
  apply (rule bind_wp[OF _ get_simple_ko_sp])
  apply (rule hoare_pre)
   apply (wp set_ntfn_minor_invs | wpc | simp)+
   apply (rule_tac Q="\<lambda>_ s. (state_refs_of s ntfnptr = ntfn_bound_refs (ntfn_bound_tcb ntfn))
                      \<and> (\<exists>T. typ_at T ntfnptr s) \<and> valid_ntfn (ntfn_set_obj ntfn IdleNtfn) s
                      \<and> ((\<exists>y. ntfn_bound_tcb ntfn = Some y) \<longrightarrow> ex_nonz_cap_to ntfnptr s)"
                      in hoare_strengthen_post)
    apply (wp hoare_vcg_all_lift hoare_weak_lift_imp hoare_vcg_ex_lift | wpc
         | simp add: live_def valid_ntfn_def valid_bound_tcb_def split: option.splits)+
    apply ((clarsimp simp: obj_at_def state_refs_of_def)+)[2]
  apply (rule_tac obj_at_valid_objsE[OF _ invs_valid_objs]; clarsimp)
    apply assumption+
  by (fastforce simp: ko_at_state_refs_ofD valid_ntfn_def valid_obj_def obj_at_def is_ntfn live_def elim: if_live_then_nonz_capD[OF invs_iflive])
