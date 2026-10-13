(* lemma: sai_invs'
   thy: proof/refine/ARM/Ipc_R.thy:2869 (proof hot line 2949)
   session: Refine
   arm: search  source: db-scan  tier: sweet  tactic: fastforce
   REASON: db-scan: lemma total 12s, search 12s (frac 1.00), single classical line(s); hot = fastforce carrying 12s.
*)

lemma sai_invs'[wp]:
  "\<lbrace>invs' and ex_nonz_cap_to' ntfnptr\<rbrace>
     sendSignal ntfnptr badge \<lbrace>\<lambda>y. invs'\<rbrace>"
  unfolding sendSignal_def
  including classic_wp_pre
  apply (rule bind_wp[OF _ get_ntfn_sp'])
  apply (case_tac "ntfnObj nTFN", simp_all)
    prefer 3
    apply (rename_tac list)
    apply (case_tac list,
           simp_all split del: if_split
                          add: setMessageInfo_def)[1]
    apply (rule hoare_pre)
     apply (wp hoare_convert_imp [OF asUser_nosch]
               hoare_convert_imp [OF setMRs_sch_act])+
     apply (clarsimp simp:conj_comms)
     apply (simp add: invs'_def valid_state'_def)
     apply ((wp valid_irq_node_lift sts_valid_objs' setThreadState_ct_not_inQ
               set_ntfn_valid_objs' cur_tcb_lift sts_st_tcb'
               hoare_convert_imp [OF setNotification_nosch]
           | simp split del: if_split)+)[3]

    apply (intro conjI[rotated];
      (solves \<open>clarsimp simp: invs'_def valid_state'_def valid_pspace'_def\<close>)?)
           apply clarsimp
           apply (clarsimp simp: invs'_def valid_state'_def split del: if_split)
           apply (drule(1) ct_not_in_ntfnQueue, simp+)
          apply clarsimp
          apply (frule ko_at_valid_objs', clarsimp)
           apply (simp add: projectKOs)
          apply (clarsimp simp: valid_obj'_def valid_ntfn'_def
                         split: list.splits)
         apply (clarsimp simp: invs'_def valid_state'_def)
         apply (clarsimp simp: st_tcb_at_refs_of_rev' valid_idle'_def pred_tcb_at'_def idle_tcb'_def
                        dest!: sym_refs_ko_atD' sym_refs_st_tcb_atD' sym_refs_obj_atD'
                        split: list.splits)
        apply (clarsimp simp: invs'_def valid_state'_def valid_pspace'_def)
        apply (frule(1) ko_at_valid_objs')
         apply (simp add: projectKOs)
        apply (clarsimp simp: valid_obj'_def valid_ntfn'_def
                    split: list.splits option.splits)
       apply (clarsimp elim!: if_live_then_nonz_capE' simp:invs'_def valid_state'_def)
       apply (drule(1) sym_refs_ko_atD')
       apply (clarsimp elim!: ko_wp_at'_weakenE
                   intro!: refs_of_live')
      apply (clarsimp split del: if_split)+
      apply (frule ko_at_valid_objs', clarsimp)
       apply (simp add: projectKOs)
      apply (clarsimp simp: valid_obj'_def valid_ntfn'_def split del: if_split)
      apply (frule invs_sym')
      apply (drule(1) sym_refs_obj_atD')
      apply (clarsimp split del: if_split cong: if_cong
                         simp: st_tcb_at_refs_of_rev' ep_redux_simps' ntfn_bound_refs'_def)
      apply (frule st_tcb_at_state_refs_ofD')
      apply (erule delta_sym_refs)
       apply (fastforce simp: split: if_split_asm)
      apply (fastforce simp: tcb_bound_refs'_def set_eq_subset symreftype_inverse'
                      split: if_split_asm)
     apply (clarsimp simp:invs'_def)
     apply (frule ko_at_valid_objs')
       apply (clarsimp simp: valid_pspace'_def valid_state'_def)
      apply (simp add: projectKOs)
     apply (clarsimp simp: valid_obj'_def valid_ntfn'_def split del: if_split)
    apply (clarsimp simp:invs'_def valid_state'_def valid_pspace'_def)
    apply (frule(1) ko_at_valid_objs')
     apply (simp add: projectKOs)
    apply (clarsimp simp: valid_obj'_def valid_ntfn'_def
                  split: list.splits option.splits)
   apply (case_tac "ntfnBoundTCB nTFN", simp_all)
    apply (wp set_ntfn_minor_invs')
    apply (fastforce simp: valid_ntfn'_def invs'_def valid_state'_def
                    elim!: obj_at'_weakenE
                    dest!: global'_no_ex_cap)
   apply (wp add: hoare_convert_imp [OF asUser_nosch]
             hoare_convert_imp [OF setMRs_sch_act]
             setThreadState_nonqueued_state_update sts_st_tcb'
             del: cancelIPC_simple)
     apply (clarsimp | wp cancelIPC_ct')+
    apply (wp set_ntfn_minor_invs' gts_wp' | clarsimp)+
   apply (frule pred_tcb_at')
   by (wp set_ntfn_minor_invs'
        | rule conjI
        | clarsimp elim!: st_tcb_ex_cap''
        | fastforce simp: receiveBlocked_def projectKOs pred_tcb_at'_def obj_at'_def
                   dest!: invs_rct_ct_activatable'
                   split: thread_state.splits
        | fastforce simp: invs'_def valid_state'_def receiveBlocked_def projectKOs
                          valid_obj'_def valid_ntfn'_def
                   split: thread_state.splits
                   dest!: global'_no_ex_cap st_tcb_ex_cap'' ko_at_valid_objs')+
