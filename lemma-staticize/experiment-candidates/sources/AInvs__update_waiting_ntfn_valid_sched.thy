(* lemma: update_waiting_ntfn_valid_sched
   thy: proof/invariant-abstract/DetSchedSchedule_AI.thy:2215 (proof hot line 2229)
   session: AInvs
   arm: work-suspect  source: db-scan  tier: sweet  tactic: fastforce
   FLAGS: WORK-SUSPECT (cost may be simp-work, not search)
   REASON: db-scan: lemma total 48s, search 48s (frac 1.00), single classical line(s); hot = fastforce carrying 48s.
*)

lemma update_waiting_ntfn_valid_sched[wp]:
  "\<lbrace> \<lambda>s. valid_sched s \<and> hd queue \<noteq> idle_thread s \<and> (scheduler_action s = resume_cur_thread \<longrightarrow> hd queue \<noteq> cur_thread s)\<rbrace> update_waiting_ntfn ntfnptr queue badge val \<lbrace> \<lambda>_. valid_sched \<rbrace>"
  apply (simp add: update_waiting_ntfn_def)
  apply (wp sts_st_tcb_at' possible_switch_to_valid_sched_except
            set_thread_state_runnable_valid_sched
            set_thread_state_runnable_valid_queues
            set_thread_state_runnable_valid_sched_action
            set_thread_state_valid_blocked_except
            | clarsimp)+
  apply (clarsimp simp add: valid_sched_def not_cur_thread_def ct_not_in_q_def)
  done
