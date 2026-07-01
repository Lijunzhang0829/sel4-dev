(* lemma: tcbSchedEnqueue_corres
   thy: proof/refine/ARM/TcbAcc_R.thy:2475 (proof hot line 2670)
   session: Refine
   arm: search  source: db-scan  tier: sweet  tactic: auto
   REASON: db-scan: lemma total 14s, search 14s (frac 1.00), single classical line(s); hot = auto carrying 14s.
*)

lemma tcbSchedEnqueue_corres:
  "tcb_ptr = tcbPtr \<Longrightarrow>
   corres dc
     (in_correct_ready_q and ready_qs_distinct and valid_etcbs and st_tcb_at runnable tcb_ptr
      and pspace_aligned and pspace_distinct)
     (sym_heap_sched_pointers and valid_sched_pointers and valid_tcbs')
     (tcb_sched_action tcb_sched_enqueue tcb_ptr) (tcbSchedEnqueue tcbPtr)"
  supply if_split[split del]
         heap_path_append[simp del] fun_upd_apply[simp del] distinct_append[simp del]
  apply (rule_tac Q'="st_tcb_at' runnable' tcbPtr" in corres_cross_add_guard)
   apply (fastforce intro!: st_tcb_at_runnable_cross simp: obj_at_def is_tcb_def)
  apply (rule_tac Q="tcb_at tcb_ptr" in corres_cross_add_abs_guard)
   apply (fastforce dest: st_tcb_at_tcb_at)
  apply (rule_tac Q'=pspace_aligned' in corres_cross_add_guard)
   apply (fastforce dest: pspace_aligned_cross)
  apply (rule_tac Q'=pspace_distinct' in corres_cross_add_guard)
   apply (fastforce dest: pspace_distinct_cross)
  apply (clarsimp simp: tcb_sched_action_def tcb_sched_enqueue_def get_tcb_queue_def
                        tcbSchedEnqueue_def getQueue_def unless_def when_def)
  apply (rule corres_symb_exec_l[OF _ _ ethread_get_sp]; (solves wpsimp)?)
  apply (rename_tac domain)
  apply (rule corres_symb_exec_l[OF _ _ ethread_get_sp]; (solves wpsimp)?)
  apply (rename_tac priority)
  apply (rule corres_symb_exec_l[OF _ _ gets_sp]; (solves wpsimp)?)
  apply (rule corres_stateAssert_ignore)
   apply (fastforce intro: ksReadyQueues_asrt_cross)
  apply (rule corres_symb_exec_r[OF _ isRunnable_sp]; (solves wpsimp)?)
  apply (rule corres_symb_exec_r[OF _ assert_sp, rotated]; (solves wpsimp)?)
   apply wpsimp
   apply (fastforce simp: st_tcb_at'_def runnable_eq_active' obj_at'_def)
  apply (rule corres_symb_exec_r[OF _ threadGet_sp]; (solves wpsimp)?)
  apply (subst if_distrib[where f="set_tcb_queue domain prio" for domain prio])
  apply (rule corres_if_strong')
    apply (frule state_relation_ready_queues_relation)
    apply (frule in_ready_q_tcbQueued_eq[where t=tcbPtr])
    subgoal
      by (fastforce dest: tcb_at_ekheap_dom pred_tcb_at_tcb_at
                    simp: obj_at'_def opt_pred_def opt_map_def obj_at_def is_tcb_def
                          in_correct_ready_q_def etcb_at_def is_etcb_at_def projectKOs)
   apply (find_goal \<open>match conclusion in "corres _ _ _ _ (return ())" \<Rightarrow> \<open>-\<close>\<close>)
   apply (rule monadic_rewrite_corres_l[where P=P and Q=P for P, simplified])
    apply (clarsimp simp: set_tcb_queue_def)
    apply (rule monadic_rewrite_guard_imp)
     apply (rule monadic_rewrite_modify_noop)
    apply (prop_tac "(\<lambda>d p. if d = domain \<and> p = priority
                            then ready_queues s domain priority
                            else ready_queues s d p)
                     = ready_queues s")
     apply (fastforce split: if_splits)
    apply fastforce
   apply clarsimp
  apply (rule corres_symb_exec_r[OF _ threadGet_sp]; (solves wpsimp)?)
  apply (rule corres_symb_exec_r[OF _ threadGet_sp]; (solves wpsimp)?)
  apply (rule corres_symb_exec_r[OF _ gets_sp]; (solves wpsimp)?)

  \<comment> \<open>break off the addToBitmap\<close>
  apply (rule corres_add_noop_lhs)
  apply (rule corres_underlying_split[rotated 2,
                                      where Q="\<lambda>_. P" and P=P and Q'="\<lambda>_. P'" and P'=P' for P P'])
     apply wpsimp
    apply (wpsimp wp: hoare_vcg_if_lift hoare_vcg_ex_lift)
   apply (corres corres: addToBitmap_if_null_noop_corres)

  apply (rule corres_from_valid_det)
    apply (fastforce intro: det_wp_modify det_wp_pre simp: set_tcb_queue_def)
   apply (wpsimp simp: tcbQueuePrepend_def wp: hoare_vcg_if_lift2 | drule Some_to_the)+
   apply (clarsimp simp: ex_abs_underlying_def split: if_splits)
   apply (frule state_relation_ready_queues_relation)
   apply (clarsimp simp: ready_queues_relation_def ready_queue_relation_def Let_def)
   apply (drule_tac x="tcbDomain tcb" in spec)
   apply (drule_tac x="tcbPriority tcb" in spec)
   subgoal by (force dest!: obj_at'_tcbQueueHead_ksReadyQueues simp: obj_at'_def projectKOs)

  apply (rename_tac s rv t)
  apply (clarsimp simp: state_relation_def)
  apply (intro hoare_vcg_conj_lift_pre_fix;
         (solves \<open>frule singleton_eqD, frule set_tcb_queue_projs_inv, wpsimp simp: swp_def\<close>)?)

  \<comment> \<open>ready_queues_relation\<close>
  apply (clarsimp simp: ready_queues_relation_def ready_queue_relation_def Let_def)
  apply (intro hoare_allI)
  apply (drule singleton_eqD)
  apply (drule set_tcb_queue_new_state)
  apply (wpsimp wp: threadSet_wp getObject_tcb_wp simp: setQueue_def tcbQueuePrepend_def)
  apply normalise_obj_at'
  apply (frule (1) tcb_at_is_etcb_at)
  apply (clarsimp simp: obj_at_def is_etcb_at_def etcb_at_def)
  apply (rename_tac s d p s' tcb' tcb etcb)
  apply (frule_tac t=tcbPtr in ekheap_relation_tcb_domain_priority)
    apply (force simp: obj_at_def)
   apply (force simp: obj_at'_def projectKOs)
  apply (clarsimp split: if_splits)
  apply (cut_tac ts="ready_queues s d p" in list_queue_relation_nil)
   apply (force dest!: spec simp: list_queue_relation_def)
  apply (cut_tac ts="ready_queues s (tcb_domain etcb) (tcb_priority etcb)"
              in list_queue_relation_nil)
   apply (force dest!: spec simp: list_queue_relation_def)
  apply (cut_tac ts="ready_queues s (tcb_domain etcb) (tcb_priority etcb)" and s'=s'
              in obj_at'_tcbQueueEnd_ksReadyQueues)
      apply fast
     apply auto[1]
    apply fastforce
   apply fastforce
  apply (cut_tac xs="ready_queues s d p" and st="tcbQueueHead (ksReadyQueues s' (d, p))"
              in heap_path_head')
   apply (auto dest: spec simp: list_queue_relation_def tcbQueueEmpty_def)[1]
  apply (cut_tac xs="ready_queues s (tcb_domain etcb) (tcb_priority etcb)"
             and st="tcbQueueHead (ksReadyQueues s' (tcb_domain etcb, tcb_priority etcb))"
              in heap_path_head')
   apply (auto dest: spec simp: list_queue_relation_def tcbQueueEmpty_def)[1]
  apply (clarsimp simp: list_queue_relation_def)

  apply (case_tac "\<not> (d = tcb_domain etcb \<and> p = tcb_priority etcb)")
   apply (cut_tac d=d and d'="tcb_domain etcb" and p=p and p'="tcb_priority etcb"
               in ready_queues_disjoint)
      apply force
     apply fastforce
    apply fastforce
   apply (prop_tac "tcbPtr \<notin> set (ready_queues s d p)")
    apply (clarsimp simp: obj_at'_def opt_pred_def opt_map_def projectKOs)
    apply (metis inQ_def option.simps(5) tcb_of'_TCB)
   apply (intro conjI impI; simp)

         \<comment> \<open>the ready queue was originally empty\<close>
         apply (rule heap_path_heap_upd_not_in)
          apply (clarsimp simp: fun_upd_apply split: if_splits)
         apply fastforce
        apply (clarsimp simp: queue_end_valid_def fun_upd_apply split: if_splits)
       apply (rule prev_queue_head_heap_upd)
        apply (clarsimp simp: fun_upd_apply split: if_splits)
       apply (case_tac "ready_queues s d p";
              clarsimp simp: fun_upd_apply tcbQueueEmpty_def split: if_splits)
      apply (clarsimp simp: inQ_def in_opt_pred fun_upd_apply obj_at'_def split: if_splits)
     apply (clarsimp simp: fun_upd_apply split: if_splits)
    apply (clarsimp simp: fun_upd_apply split: if_splits)

   \<comment> \<open>the ready queue was not originally empty\<close>
   apply (clarsimp simp: etcb_at_def obj_at'_def)
   apply (prop_tac "the (tcbQueueHead (ksReadyQueues s' (tcb_domain etcb, tcb_priority etcb)))
                    \<notin> set (ready_queues s d p)")
    apply (erule orthD2)
