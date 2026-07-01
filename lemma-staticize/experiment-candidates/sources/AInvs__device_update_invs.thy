(* lemma: device_update_invs
   thy: proof/invariant-abstract/AInvs.thy:52 (proof hot line 71)
   session: AInvs
   arm: search  source: db-scan  tier: sweet  tactic: fastforce
   REASON: db-scan: lemma total 10s, search 10s (frac 1.00), single classical line(s); hot = fastforce carrying 10s.
*)

lemma device_update_invs:
  "\<lbrace>invs and (\<lambda>s. (dom ds) \<subseteq>  (device_region s))\<rbrace> do_machine_op (device_memory_update ds)
   \<lbrace>\<lambda>_. invs\<rbrace>"
  apply (simp add: do_machine_op_def device_memory_update_def simpler_modify_def select_f_def
                   gets_def get_def bind_def valid_def return_def)
  apply (clarsimp simp: invs_def valid_state_def valid_irq_states_def valid_machine_state_def
                        cur_tcb_def pspace_respects_device_region_def cap_refs_respects_device_region_def
                  cong: user_mem_dom_cong
              simp del: split_paired_All)
  apply (clarsimp cong: device_mem_dom_cong simp:cap_range_respects_device_region_def
              simp del: split_paired_All split_paired_Ex)
  apply (intro conjI)
    apply fastforce
   apply fastforce
  apply (clarsimp simp del: split_paired_All split_paired_Ex)
  apply (drule_tac x = "(a,b)" in spec)
  apply (erule notE)
  apply (erule cte_wp_at_weakenE)
  apply clarsimp
  by (fastforce split: if_splits) (* takes 20 secs *)
