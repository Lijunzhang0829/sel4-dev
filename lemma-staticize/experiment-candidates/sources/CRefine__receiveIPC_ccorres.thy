(* lemma: receiveIPC_ccorres
   thy: proof/crefine/ARM/Ipc_C.thy:5252 (proof hot line 5503)
   session: CRefine
   arm: hard  source: db-scan  tier: hard  tactic: fastforce
   FLAGS: WORK-SUSPECT (cost may be simp-work, not search); COMPOUND (multi-goal/combined method, not a clean single target)
   REASON: db-scan: lemma total 1216s, search 509s (frac 0.42), 139 classical line(s); hot = fastforce carrying 204s.
*)

lemma receiveIPC_ccorres [corres]:
  notes option.case_cong_weak [cong]
  shows
  "ccorres dc xfdc (invs' and st_tcb_at' simple' thread and sch_act_not thread
                          and valid_cap' cap and K (isEndpointCap cap))
     (UNIV \<inter> \<lbrace>\<acute>thread = tcb_ptr_to_ctcb_ptr thread\<rbrace>
           \<inter> \<lbrace>ccap_relation cap \<acute>cap\<rbrace>
           \<inter> \<lbrace>\<acute>isBlocking = from_bool isBlocking\<rbrace>) hs
     (receiveIPC thread cap isBlocking)
     (Call receiveIPC_'proc)"
  unfolding K_def
  apply (rule ccorres_gen_asm)
  apply (cinit lift: thread_' cap_' isBlocking_')
   apply (rule ccorres_pre_getEndpoint)
   apply (rename_tac ep)
   apply (simp only: ccorres_seq_skip)
   apply (rule_tac xf'=ret__unsigned_'
               and val="capEPPtr cap"
               and R=\<top>
                in ccorres_symb_exec_r_known_rv_UNIV[where R'=UNIV])
      apply vcg
      apply (clarsimp simp: cap_get_tag_isCap isCap_simps)
      apply (frule cap_get_tag_isCap_unfolded_H_cap)
      apply (simp add: cap_endpoint_cap_lift ccap_relation_def cap_to_H_def)
     apply ceqv
    apply csymbr
    apply (rule ccorres_move_c_guard_tcb)
    apply (rule_tac xf'=ntfnPtr_'
                and r'="\<lambda>rv rv'. rv' = option_to_ptr rv \<and> rv \<noteq> Some 0"
                in ccorres_split_nothrow_novcg)
        apply (simp add: getBoundNotification_def)
        apply (rule_tac P="no_0_obj' and valid_objs'" in threadGet_vcg_corres_P)
        apply (rule allI, rule conseqPre, vcg)
        apply clarsimp
        apply (drule obj_at_ko_at', clarsimp)
        apply (drule spec, drule(1) mp, clarsimp)
        apply (clarsimp simp: typ_heap_simps ctcb_relation_def)
        apply (drule(1) ko_at_valid_objs', simp add: projectKOs)
        apply (clarsimp simp: option_to_ptr_def option_to_0_def projectKOs
                              valid_obj'_def valid_tcb'_def)
       apply ceqv
      apply (rename_tac ntfnptr ntfnptr')
      apply (simp del: Collect_const split del: if_split cong: call_ignore_cong)
      apply (rule ccorres_rhs_assoc2)
      apply (rule_tac xf'=ret__int_'
                   and r'="\<lambda>rv rv'. (rv' = 0) = (ntfnptr = None \<or> \<not> isActive rv)"
                    in ccorres_split_nothrow_novcg)
          apply wpc
           apply (rule ccorres_from_vcg[where P=\<top> and P'=UNIV])
           apply (rule allI, rule conseqPre, vcg)
           apply (clarsimp simp: option_to_ptr_def option_to_0_def in_monad Bex_def)
          apply (rule ccorres_pre_getNotification[where f=return, simplified])
          apply (rule_tac P="\<lambda>s. ko_at' rv (the ntfnptr) s"
                     in ccorres_from_vcg[where P'=UNIV])
          apply (rule allI, rule conseqPre, vcg)
          apply (clarsimp simp: option_to_ptr_def option_to_0_def in_monad Bex_def)
          apply (erule cmap_relationE1[OF cmap_relation_ntfn])
           apply (erule ko_at_projectKO_opt)
          apply (clarsimp simp: typ_heap_simps cnotification_relation_def Let_def
                                notification_state_defs isActive_def
                         split: Structures_H.ntfn.split_asm Structures_H.notification.splits)
         apply ceqv
        apply (rule ccorres_cond[where R=\<top>])
          apply (simp add: Collect_const_mem)
         apply (ctac add: completeSignal_ccorres)
        apply (rule_tac xf'=ret__unsigned_'
                    and val="case ep of IdleEP \<Rightarrow> scast EPState_Idle
                            | RecvEP _ \<Rightarrow> scast EPState_Recv
                            | SendEP _ \<Rightarrow> scast EPState_Send"
                    and R="ko_at' ep (capEPPtr cap)"
                    in ccorres_symb_exec_r_known_rv_UNIV[where R'=UNIV])
           apply (vcg, clarsimp)
           apply (erule cmap_relationE1 [OF cmap_relation_ep])
            apply (erule ko_at_projectKO_opt)
           apply (clarsimp simp: typ_heap_simps cendpoint_relation_def Let_def
                          split: endpoint.split_asm)
          apply ceqv
         apply (rule_tac A="invs' and st_tcb_at' simple' thread
                                  and sch_act_not thread
                                  and ko_at' ep (capEPPtr cap)"
                     in ccorres_guard_imp2 [where A'=UNIV])
           apply wpc
            \<comment> \<open>RecvEP case\<close>
            apply (rule ccorres_cond_true)
            apply csymbr
            apply (simp only: case_bool_If from_bool_neq_0)
            apply (rule ccorres_Cond_rhs, simp cong: Collect_cong split del: if_split)
             apply (intro ccorres_rhs_assoc)
             apply (rule ccorres_rhs_assoc2)
             apply (rule ccorres_rhs_assoc2)
             apply (rule ccorres_rhs_assoc2)
             apply (rule ccorres_rhs_assoc2)
             apply (rule ccorres_split_nothrow_novcg)
                 apply (rule receiveIPC_block_ccorres_helper[unfolded ptr_val_def, simplified])
                apply ceqv
               apply simp
               apply (rename_tac list NOo)
               apply (rule_tac ep="RecvEP list" in receiveIPC_enqueue_ccorres_helper[simplified])
              apply (simp add: valid_ep'_def)
              apply (wp sts_st_tcb')
             apply (rename_tac list)
             apply (clarsimp simp: obj_at'_def ko_wp_at'_def projectKOs)
             apply (clarsimp simp: guard_is_UNIV_def)
            apply simp
             apply (ctac add: doNBRecvFailedTransfer_ccorres)
           \<comment> \<open>IdleEP case\<close>
           apply (rule ccorres_cond_true)
           apply csymbr
           apply (simp only: case_bool_If from_bool_neq_0)
           apply (rule ccorres_Cond_rhs, simp cong: Collect_cong split del: if_split)
            apply (intro ccorres_rhs_assoc)
            apply (rule ccorres_rhs_assoc2)
            apply (rule ccorres_rhs_assoc2)
            apply (rule ccorres_rhs_assoc2)
            apply (rule ccorres_rhs_assoc2)
            apply (rule ccorres_split_nothrow_novcg)
                apply (rule receiveIPC_block_ccorres_helper[unfolded ptr_val_def, simplified])
               apply ceqv
              apply simp
              apply (rule_tac ep=IdleEP in receiveIPC_enqueue_ccorres_helper[simplified])
             apply (simp add: valid_ep'_def)
             apply (wp sts_st_tcb')
            apply (clarsimp simp: obj_at'_def ko_wp_at'_def projectKOs)
            apply (clarsimp simp: guard_is_UNIV_def)
           apply simp
            apply (ctac add: doNBRecvFailedTransfer_ccorres)
          \<comment> \<open>SendEP case\<close>
          apply (thin_tac "isBlockinga = from_bool P" for P)
          apply (rule ccorres_cond_false)
          apply (rule ccorres_cond_true)
          apply (intro ccorres_rhs_assoc)
          apply (csymbr, csymbr, csymbr, csymbr, csymbr)
          apply wpc
           apply (simp only: haskell_fail_def)
           apply (rule ccorres_fail)
          apply (rename_tac sender rest)
          apply csymbr
          apply (rule ccorres_rhs_assoc2)
          apply (rule ccorres_rhs_assoc2)
          apply (rule ccorres_rhs_assoc2)
          apply (rule ccorres_rhs_assoc2)
