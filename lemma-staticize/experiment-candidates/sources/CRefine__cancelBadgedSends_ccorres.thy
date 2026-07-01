(* lemma: cancelBadgedSends_ccorres
   thy: proof/crefine/ARM/Recycle_C.thy:567 (proof hot line 765)
   session: CRefine
   arm: hard  source: db-scan  tier: hard  tactic: fastforce
   FLAGS: COMPOUND (multi-goal/combined method, not a clean single target)
   REASON: db-scan: lemma total 220s, search 94s (frac 0.43), 61 classical line(s); hot = fastforce carrying 49s.
*)

lemma cancelBadgedSends_ccorres:
  "ccorres dc xfdc (invs' and ep_at' ptr)
              (UNIV \<inter> {s. epptr_' s = Ptr ptr} \<inter> {s. badge_' s = bdg}) []
       (cancelBadgedSends ptr bdg) (Call cancelBadgedSends_'proc)"
  apply (cinit lift: epptr_' badge_' simp: whileAnno_def)
   apply (rule ccorres_stateAssert)
   apply (simp add: list_case_return
              cong: list.case_cong Structures_H.endpoint.case_cong call_ignore_cong
               del: Collect_const)
   apply (rule ccorres_pre_getEndpoint, rename_tac ep)
   apply (rule_tac R="ko_at' ep ptr" and xf'="ret__unsigned_'"
               and val="case ep of RecvEP q \<Rightarrow> scast EPState_Recv | IdleEP \<Rightarrow> scast EPState_Idle
                                | SendEP q \<Rightarrow> scast EPState_Send"
               in ccorres_symb_exec_r_known_rv_UNIV[where R'=UNIV])
      apply vcg
      apply clarsimp
      apply (erule cmap_relationE1 [OF cmap_relation_ep], erule ko_at_projectKO_opt)
      apply (clarsimp simp: typ_heap_simps cendpoint_relation_def Let_def
                     split: Structures_H.endpoint.split_asm)
     apply ceqv
    apply wpc
      apply (simp add: ccorres_cond_iffs)
      apply (rule ccorres_return_Skip)
     apply (simp add: ccorres_cond_iffs)
     apply (rule ccorres_return_Skip)
    apply (rename_tac list)
    apply (simp add: Collect_True Collect_False endpoint_state_defs
                     ccorres_cond_iffs
                del: Collect_const cong: call_ignore_cong)
    apply (rule ccorres_rhs_assoc)+
    apply (csymbr, csymbr)
    apply (drule_tac s = ep in sym, simp only:)
    apply (rule_tac P="ko_at' ep ptr and invs'" in ccorres_cross_over_guard)
    apply (rule ccorres_symb_exec_r)
      apply (rule ccorres_rhs_assoc2, rule ccorres_rhs_assoc2)
      apply (rule ccorres_split_nothrow[where r'=dc and xf'=xfdc, OF _ ceqv_refl])
         apply (rule_tac P="ko_at' ep ptr"
                    in ccorres_from_vcg[where P'=UNIV])
         apply (rule allI, rule conseqPre, vcg)
         apply clarsimp
         apply (rule cmap_relationE1[OF cmap_relation_ep], assumption)
          apply (erule ko_at_projectKO_opt)
         apply (clarsimp simp: typ_heap_simps setEndpoint_def)
         apply (rule rev_bexI)
          apply (rule setObject_eq; simp add: objBits_simps')[1]
         apply (clarsimp simp: rf_sr_def cstate_relation_def
                               Let_def carch_state_relation_def
                               cmachine_state_relation_def
                               )
         apply (clarsimp simp: cpspace_relation_def
                               update_ep_map_tos
                               typ_heap_simps')
         apply (erule(1) cpspace_relation_ep_update_ep2)
          apply (simp add: cendpoint_relation_def endpoint_state_defs)
         subgoal by simp
        apply (rule ccorres_symb_exec_r)
          apply (rule_tac xs=list in filterM_voodoo)
          apply (rule_tac P="\<lambda>xs s. (\<forall>x \<in> set xs \<union> set list.
                   st_tcb_at' (\<lambda>st. isBlockedOnSend st \<and> blockingObject st = ptr) x s)
                              \<and> distinct (xs @ list) \<and> ko_at' IdleEP ptr s
                              \<and> (\<forall>p. \<forall>x \<in> set (xs @ list). \<forall>rf. (x, rf) \<notin> {r \<in> state_refs_of' s p. snd r \<noteq> NTFNBound})
                              \<and> pspace_aligned' s \<and> pspace_distinct' s
                              \<and> sch_act_wf (ksSchedulerAction s) s \<and> valid_objs' s
                              \<and> ksReadyQueues_head_end s \<and> ksReadyQueues_head_end_tcb_at' s"
                     and P'="\<lambda>xs. {s. ep_queue_relation' (cslift s) (xs @ list)
                                         (head_C (queue_' s)) (end_C (queue_' s))}
                                \<inter> {s. thread_' s = (case list of [] \<Rightarrow> tcb_Ptr 0
                                                       | x # xs \<Rightarrow> tcb_ptr_to_ctcb_ptr x)}"
                      in ccorres_inst_voodoo)
          apply (induct_tac list)
           apply (rule allI)
           apply (rule iffD1 [OF ccorres_expand_while_iff_Seq])
           apply (rule ccorres_tmp_lift2 [OF _ _ Int_lower1])
            apply ceqv
           apply (simp add: ccorres_cond_iffs)
           apply (rule ccorres_rhs_assoc2)
           apply (rule ccorres_duplicate_guard, rule ccorres_split_nothrow_novcg_dc)
              apply (rule ccorres_from_vcg, rule allI, rule conseqPre, vcg)
              apply clarsimp
              apply (drule obj_at_ko_at', clarsimp)
              apply (rule cmap_relationE1[OF cmap_relation_ep], assumption)
               apply (erule ko_at_projectKO_opt)
              apply (clarsimp simp: typ_heap_simps tcb_queue_relation'_def)
              apply (case_tac x)
               apply (clarsimp simp: setEndpoint_def)
               apply (rule rev_bexI, rule setObject_eq,
                      (simp add: objBits_simps')+)
               apply (clarsimp simp: rf_sr_def cstate_relation_def Let_def
                 carch_state_relation_def
                 cmachine_state_relation_def
                 cpspace_relation_def typ_heap_simps'
                 update_ep_map_tos)
               apply (erule(1) cpspace_relation_ep_update_ep2)
                subgoal by (simp add: cendpoint_relation_def Let_def)
               subgoal by simp
              apply (clarsimp simp: tcb_at_not_NULL[OF pred_tcb_at']
                                    setEndpoint_def)
              apply (rule rev_bexI, rule setObject_eq,
                      (simp add: objBits_simps')+)
              apply (clarsimp simp: rf_sr_def cstate_relation_def Let_def
                                     carch_state_relation_def
                                     cmachine_state_relation_def
                                     cpspace_relation_def typ_heap_simps'
                                     update_ep_map_tos)
              apply (erule(1) cpspace_relation_ep_update_ep2)
               apply (simp add: cendpoint_relation_def Let_def)
               apply (subgoal_tac "tcb_at' (last (a # list)) \<sigma> \<and> tcb_at' a \<sigma>")
                apply (clarsimp simp: is_aligned_neg_mask_weaken[
                                        OF is_aligned_tcb_ptr_to_ctcb_ptr[where P=\<top>]])
                subgoal by (simp add: tcb_queue_relation'_def EPState_Send_def mask_def)
               subgoal by (auto split: if_split)
              subgoal by simp
             apply (ctac add: rescheduleRequired_ccorres)
            apply (rule hoare_pre, wp weak_sch_act_wf_lift_linear set_ep_valid_objs')
            apply (clarsimp simp: weak_sch_act_wf_def sch_act_wf_def)
            apply (fastforce simp: valid_ep'_def pred_tcb_at' split: list.splits)
           apply (simp add: guard_is_UNIV_def)
          apply (rule allI)
          apply (rename_tac a lista x)
          apply (rule iffD1 [OF ccorres_expand_while_iff_Seq])
          apply (rule ccorres_init_tmp_lift2, ceqv)
          apply (rule ccorres_guard_imp2)
           apply (simp add: bind_assoc
                       del: Collect_const)
           apply (rule ccorres_cond_true)
           apply (rule ccorres_rhs_assoc)+
           apply (rule ccorres_pre_threadGet[where f=tcbState, folded getThreadState_def])
           apply (rule ccorres_move_c_guard_tcb)
           apply csymbr
           apply (rule ccorres_abstract_cleanup)
           apply csymbr
           apply (rule ccorres_move_c_guard_tcb)
           apply (rule_tac P=\<top>
                      and P'="{s. ep_queue_relation' (cslift s) (x @ a # lista)
                                        (head_C (queue_' s)) (end_C (queue_' s))}"
                      and f'="\<lambda>s. s \<lparr> next___ptr_to_struct_tcb_C_' := (case lista of [] \<Rightarrow> tcb_Ptr 0
                                              | v # vs \<Rightarrow> tcb_ptr_to_ctcb_ptr v) \<rparr>"
                      and xf'="next___ptr_to_struct_tcb_C_'"
                           in ccorres_subst_basic_helper)
               apply (thin_tac "\<forall>x. P x" for P)
               apply (rule myvars.fold_congs, (rule refl)+)
