(* lemma: send_sync_ipc_corres
   thy: proof/drefine/Ipc_DR.thy:2570 (proof hot line 2654)
   session: DRefine
   arm: search  source: db-scan  tier: sweet  tactic: fastforce
   REASON: db-scan: lemma total 14s, search 14s (frac 1.00), single classical line(s); hot = fastforce carrying 14s.
*)

lemma send_sync_ipc_corres:
  "\<lbrakk>ep_id = epptr;tcb_id_sender= thread\<rbrakk> \<Longrightarrow>
    dcorres dc
     \<top>
  (not_idle_thread thread and (\<lambda>s. not_idle_thread (cur_thread s) s)
      and st_tcb_at active thread
      and valid_state and valid_etcbs)
     (Endpoint_D.send_ipc block call badge can_grant can_grant_reply tcb_id_sender ep_id)
     (Ipc_A.send_ipc block call badge can_grant can_grant_reply thread epptr)"
  apply (clarsimp simp:gets_def Endpoint_D.send_ipc_def Ipc_A.send_ipc_def
                  split del:if_split)
  apply (rule dcorres_absorb_get_l)
  apply (clarsimp split del:if_split)
  apply (rule_tac Q' = "\<lambda>r. (=) s' and ko_at (kernel_object.Endpoint r) epptr" in corres_symb_exec_r[rotated])
     apply (wp get_simple_ko_ko_at |simp split del:if_split)+
  apply (rule dcorres_expand_pfx)
  apply (clarsimp split del:if_split)
  apply (frule_tac get_endpoint_pick)
   apply (simp add:obj_at_def)
  apply (case_tac rv)
    apply (clarsimp split del:if_split)
    apply (subst ep_waiting_set_recv_lift)
     apply (simp add:valid_state_def)
     apply simp
    apply (clarsimp simp:valid_ep_abstract_def none_is_receiving_ep_def option_select_def)
    apply (rule corres_dummy_return_l)
    apply (rule corres_guard_imp)
      apply (rule corres_split[OF set_thread_state_block_on_send_corres corres_dummy_set_sync_ep])
       apply wp
     apply simp
    apply (wp TrueI |clarsimp simp: split del:if_split)+
(* SendEP *)
   apply (subst ep_waiting_set_recv_lift)
    apply (simp add:valid_state_def)
    apply simp
    apply (clarsimp simp:valid_ep_abstract_def none_is_receiving_ep_def option_select_def)
    apply (rule corres_dummy_return_l)
    apply (rule corres_guard_imp)
      apply (rule corres_split[OF set_thread_state_block_on_send_corres corres_dummy_set_sync_ep])
       apply wp
     apply simp
    apply (wp TrueI|clarsimp simp: split del:if_split)+
(* RecvEP *)
  apply (subst ep_waiting_set_recv_lift,simp add:valid_state_def)
  apply simp
  apply (clarsimp simp:valid_ep_abstract_def split del:if_split)
  apply (subst option_select_not_empty)
   apply (clarsimp simp: dest!: not_empty_list_not_empty_set)
  apply (rename_tac list)
  apply (drule_tac s = "set list" in sym)
  apply (clarsimp simp: bind_assoc neq_Nil_conv split del:if_split)
  apply (rule_tac P1="\<top>" and P'="(=) s'" and x1 = y
         in dcorres_absorb_pfx[OF select_pick_corres[OF dcorres_expand_pfx]])
      defer
      apply (simp+)[3]
  apply (simp split del:if_split)
  apply (drule_tac x1 = y in iffD2[OF eqset_imp_iff], simp)
  apply (clarsimp simp:obj_at_def dc_def[symmetric] split del:if_split)
  apply (subst when_def)+
  apply (rule corres_guard_imp)
    apply (rule dcorres_symb_exec_r)
      apply (simp only: liftM_def)
      apply (rule corres_split[OF dcorres_get_thread_state])
        apply (clarsimp, rename_tac recv_state')
        apply (case_tac recv_state'; simp add: corres_free_fail split del: if_split)
        apply (rule corres_split)
           apply (rule corres_complete_ipc_transfer; simp)
          apply (rule corres_split[OF set_thread_state_corres])
            apply (rule dcorres_rhs_noop_above[OF possible_switch_to_dcorres])
              apply (rule dcorres_if_rhs)
               apply (rule dcorres_if_rhs)
                apply (clarsimp simp only: if_True)
                apply (rule corres_alternate1)+
                apply (rule corres_setup_caller_cap)
                apply (clarsimp simp:ep_waiting_set_recv_def pred_tcb_at_def obj_at_def generates_pending_def)
               apply (rule corres_alternate1[OF corres_alternate2])
               apply (rule set_thread_state_corres)
              apply (rule corres_alternate2)
              apply (rule corres_return_trivial)
             apply wp
            apply (rule_tac Q="\<lambda>r. valid_mdb and valid_idle and valid_objs
                          and not_idle_thread thread and not_idle_thread y and tcb_at thread and tcb_at y
                          and st_tcb_at runnable thread and valid_etcbs"
                          in hoare_strengthen_post[rotated])
             apply (clarsimp simp:pred_tcb_at_def obj_at_def,
                    simp split:Structures_A.thread_state.splits, fastforce)
            apply ((wp sts_st_tcb_at' sts_st_tcb_at_neq |clarsimp simp add:not_idle_thread_def)+)
        apply (wp hoare_vcg_conj_lift)
         apply (rule hoare_disjI1)
         apply (wp do_ipc_transfer_pred_tcb | wpc | simp)+
     apply (clarsimp simp:conj_comms not_idle_thread_def)
     apply (wp | wps)+
    apply (simp add:not_idle_thread_def)
    apply (clarsimp simp:ep_waiting_set_recv_def obj_at_def st_tcb_at_def)+
    apply (frule_tac a = "y" and list = "y # ys" in pending_thread_in_recv_not_idle)
       apply (simp add:valid_state_def)
      apply (fastforce simp:obj_at_def)
     apply (simp add:insertI1 not_idle_thread_def)+
    apply (rule dcorres_to_wp[where Q=\<top> ,simplified])
    apply (rule corres_dummy_set_sync_ep)
   apply simp
  apply (clarsimp simp:valid_state_def not_idle_thread_def
                       valid_pspace_def st_tcb_at_def obj_at_def is_ep_def)
  apply (drule(1) valid_objs_valid_ep_simp)
  apply (clarsimp simp:valid_ep_def tcb_at_def
    valid_idle_def pred_tcb_at_def obj_at_def
    dest!:get_tcb_SomeD
    split:Structures_A.endpoint.splits list.splits)
  apply (clarsimp simp:ep_waiting_set_recv_def runnable_eq_active)
  apply (intro conjI impI)
    apply clarsimp+
  apply fastforce
  done
