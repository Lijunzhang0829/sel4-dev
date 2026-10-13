(* lemma: dmo_device_state_update_reads_respects_g
   thy: proof/infoflow/UserOp_IF.thy:144 (proof hot line 151)
   session: InfoFlow
   arm: work-suspect  source: both  tier: sweet  tactic: fastforce
   FLAGS: WORK-SUSPECT (cost may be simp-work, not search)
   REASON: db-scan: lemma total 14s, search 14s (frac 1.00), single classical line(s); hot = fastforce carrying 14s.  ||  wasted-classical: fastforce+simp: line at 17.7s, NO split/dest/elim/intro (classical likely wasted), file 165L (baseline-able). Band of the 2 confirmed fastforce->clarsimp wins.
*)

lemma dmo_device_state_update_reads_respects_g:
  "reads_respects_g aag l (\<lambda>s. dom um \<subseteq> device_region s) (do_machine_op (device_memory_update um))"
  apply (clarsimp simp: equiv_valid_def2 equiv_valid_2_def)
  apply (clarsimp simp: do_machine_op_def device_memory_update_def
                        gets_def get_def select_f_def bind_def in_monad)
  apply (clarsimp simp: reads_equiv_g_def globals_equiv_def split: option.splits)
  apply (subgoal_tac "reads_respects aag l \<top> (do_machine_op (device_memory_update um))")
   apply (fastforce simp: equiv_valid_def2 equiv_valid_2_def in_monad do_machine_op_def
                          device_memory_update_def select_f_def idle_equiv_def)
  apply (rule use_spec_ev)
  apply (simp add: device_memory_update_def)
  apply (rule do_machine_op_spec_reads_respects)
   apply (simp add: equiv_valid_def2)
   apply (rule modify_ev2)
   apply (fastforce intro: map_add_eq equiv_forI elim: equiv_forE split: option.splits)
  apply (wp | simp)+
  done
