(* lemma: ri_invs'
   thy: proof/refine/ARM/Ipc_R.thy:3620 (proof hot line 3725)
   session: Refine
   arm: search  source: db-scan  tier: sweet  tactic: fastforce
   REASON: db-scan: lemma total 10s, search 10s (frac 1.00), single classical line(s); hot = fastforce carrying 10s.
*)

lemma ri_invs' [wp]:
  "\<lbrace>invs' and sch_act_not t
          and ct_in_state' simple'
          and st_tcb_at' simple' t
          and ex_nonz_cap_to' t
          and (\<lambda>s. \<forall>r \<in> zobj_refs' cap. ex_nonz_cap_to' r s)\<rbrace>
  receiveIPC t cap isBlocking
  \<lbrace>\<lambda>_. invs'\<rbrace>" (is "\<lbrace>?pre\<rbrace> _ \<lbrace>_\<rbrace>")
  apply (clarsimp simp: receiveIPC_def)
  apply (rule bind_wp [OF _ get_ep_sp'])
  apply (rule bind_wp [OF _ gbn_sp'])
  apply (rule bind_wp)
  (* set up precondition for old proof *)
   apply (rule_tac R="ko_at' ep (capEPPtr cap) and ?pre" in hoare_vcg_if_split)
    apply (wp completeSignal_invs)
   apply (case_tac ep)
     \<comment> \<open>endpoint = RecvEP\<close>
     apply (simp add: invs'_def valid_state'_def)
     apply (rule hoare_pre, wpc, wp valid_irq_node_lift)
      apply (simp add: valid_ep'_def)
      apply (wp sts_sch_act' hoare_vcg_const_Ball_lift valid_irq_node_lift
                setThreadState_ct_not_inQ
                asUser_urz
           | simp add: doNBRecvFailedTransfer_def cteCaps_of_def)+
     apply (clarsimp simp: valid_tcb_state'_def pred_tcb_at' o_def)
     apply (rule conjI, clarsimp elim!: obj_at'_weakenE)
     apply (frule obj_at_valid_objs')
      apply (clarsimp simp: valid_pspace'_def)
     apply (drule(1) sym_refs_ko_atD')
     apply (drule simple_st_tcb_at_state_refs_ofD')
     apply (drule bound_tcb_at_state_refs_ofD')
     apply (clarsimp simp: st_tcb_at_refs_of_rev' valid_ep'_def
                           valid_obj'_def projectKOs tcb_bound_refs'_def
                    dest!: isCapDs)
     apply (rule conjI, clarsimp)
      apply (drule (1) bspec)
      apply (clarsimp dest!: st_tcb_at_state_refs_ofD')
      apply (clarsimp simp: set_eq_subset)
     apply (rule conjI, erule delta_sym_refs)
       apply (clarsimp split: if_split_asm)
        apply (rename_tac list one two three fur five six seven eight nine ten eleven)
        apply (subgoal_tac "set list \<times> {EPRecv} \<noteq> {}")
         apply (safe ; solves \<open>auto\<close>)
        apply fastforce
       apply fastforce
      apply (clarsimp split: if_split_asm)
     apply (fastforce simp: valid_pspace'_def global'_no_ex_cap idle'_not_queued)
   \<comment> \<open>endpoint = IdleEP\<close>
    apply (simp add: invs'_def valid_state'_def)
    apply (rule hoare_pre, wpc, wp valid_irq_node_lift)
     apply (simp add: valid_ep'_def)
     apply (wp sts_sch_act' valid_irq_node_lift
               setThreadState_ct_not_inQ
               asUser_urz
          | simp add: doNBRecvFailedTransfer_def cteCaps_of_def)+
    apply (clarsimp simp: pred_tcb_at' valid_tcb_state'_def o_def)
    apply (rule conjI, clarsimp elim!: obj_at'_weakenE)
    apply (subgoal_tac "t \<noteq> capEPPtr cap")
     apply (drule simple_st_tcb_at_state_refs_ofD')
     apply (drule ko_at_state_refs_ofD')
     apply (drule bound_tcb_at_state_refs_ofD')
     apply (clarsimp dest!: isCapDs)
     apply (rule conjI, erule delta_sym_refs)
       apply (clarsimp split: if_split_asm)
      apply (clarsimp simp: tcb_bound_refs'_def
                      dest: symreftype_inverse'
                     split: if_split_asm)
     apply (fastforce simp: global'_no_ex_cap)
    apply (clarsimp simp: obj_at'_def pred_tcb_at'_def projectKOs)
   \<comment> \<open>endpoint = SendEP\<close>
   apply (simp add: invs'_def valid_state'_def)
   apply (rename_tac list)
   apply (case_tac list, simp_all split del: if_split)
   apply (rename_tac sender queue)
   apply (rule hoare_pre)
    apply (wp valid_irq_node_lift hoare_drop_imps setEndpoint_valid_mdb'
              set_ep_valid_objs' sts_st_tcb' sts_sch_act'
              setThreadState_ct_not_inQ
              possibleSwitchTo_ct_not_inQ hoare_vcg_all_lift
              setEndpoint_ksQ setEndpoint_ct'
         | simp add: valid_tcb_state'_def case_bool_If
                     case_option_If
              split del: if_split cong: if_cong
        | wp (once) sch_act_sane_lift hoare_vcg_conj_lift hoare_vcg_all_lift
                  untyped_ranges_zero_lift)+
   apply (clarsimp split del: if_split simp: pred_tcb_at')
   apply (frule obj_at_valid_objs')
    apply (clarsimp simp: valid_pspace'_def)
   apply (frule(1) ct_not_in_epQueue, clarsimp, clarsimp)
   apply (drule(1) sym_refs_ko_atD')
   apply (drule simple_st_tcb_at_state_refs_ofD')
   apply (clarsimp simp: projectKOs valid_obj'_def valid_ep'_def
                         st_tcb_at_refs_of_rev' conj_ac
              split del: if_split
                   cong: if_cong)
   apply (subgoal_tac "sch_act_not sender s")
    prefer 2
    apply (clarsimp simp: pred_tcb_at'_def obj_at'_def)
   apply (drule st_tcb_at_state_refs_ofD')
   apply (simp only: conj_ac(1, 2)[where Q="sym_refs R" for R])
   apply (subgoal_tac "distinct (ksIdleThread s # capEPPtr cap # t # sender # queue)")
    apply (rule conjI)
     apply (clarsimp simp: ep_redux_simps' cong: if_cong)
     apply (erule delta_sym_refs)
      apply (clarsimp split: if_split_asm)
     apply (fastforce simp: tcb_bound_refs'_def
                      dest: symreftype_inverse'
                     split: if_split_asm)
    apply (clarsimp simp: singleton_tuple_cartesian split: list.split
            | rule conjI | drule(1) bspec
            | drule st_tcb_at_state_refs_ofD' bound_tcb_at_state_refs_ofD'
            | clarsimp elim!: if_live_state_refsE)+
    apply (case_tac cap, simp_all add: isEndpointCap_def)
    apply (clarsimp simp: global'_no_ex_cap)
   apply (rule conjI
           | clarsimp simp: singleton_tuple_cartesian split: list.split
           | clarsimp elim!: if_live_state_refsE
           | clarsimp simp: global'_no_ex_cap idle'_not_queued' idle'_no_refs tcb_bound_refs'_def
           | drule(1) bspec | drule st_tcb_at_state_refs_ofD'
           | clarsimp simp: set_eq_subset dest!: bound_tcb_at_state_refs_ofD' )+
  apply (rule hoare_pre)
   apply (wp getNotification_wp | wpc | clarsimp)+
  done

(* t = ksCurThread s *)
