(* lemma: set_scheduler_action_cnt_valid_blocked_except
   thy: proof/invariant-abstract/DetSchedSchedule_AI.thy:335 (proof hot line 344)
   session: AInvs
   arm: work-suspect  source: db-scan  tier: sweet  tactic: force
   FLAGS: WORK-SUSPECT (cost may be simp-work, not search)
   REASON: db-scan: lemma total 37s, search 37s (frac 1.00), single classical line(s); hot = force carrying 37s.
*)

lemma set_scheduler_action_cnt_valid_blocked_except:
  "\<lbrace>valid_blocked_except target and (\<lambda>s. \<forall>t. scheduler_action s = switch_thread t \<longrightarrow>
      (\<exists>d p. t \<in> set (ready_queues s d p)))\<rbrace>
   set_scheduler_action choose_new_thread  \<lbrace>\<lambda>_. valid_blocked_except target\<rbrace>"
  apply (simp add: valid_blocked_except_def, wp set_scheduler_action_wp)
  apply clarsimp
  apply (erule_tac x=t in allE)
  apply (erule impCE)
   apply force
  apply (force simp: not_queued_def)
  done
