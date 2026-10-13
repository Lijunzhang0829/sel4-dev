(* lemma: fastpath_enqueue_ccorres
   thy: proof/crefine/ARM/Fastpath_C.thy:1287 (proof hot line 1418)
   session: CRefine
   arm: hard  source: db-scan  tier: hard  tactic: auto
   REASON: db-scan: lemma total 214s, search 68s (frac 0.32), 52 classical line(s); hot = auto carrying 15s.
*)

lemma fastpath_enqueue_ccorres:
  "\<lbrakk> epptr' = ep_Ptr epptr \<rbrakk> \<Longrightarrow>
   ccorres dc xfdc
         (ko_at' ep epptr and (\<lambda>s. thread = ksCurThread s)
                and (\<lambda>s. sym_refs ((state_refs_of' s) (thread := {r \<in> state_refs_of' s thread. snd r = TCBBound})))
                and K (\<not> isSendEP ep) and valid_pspace' and cur_tcb')
         UNIV hs
     (setEndpoint epptr (case ep of IdleEP \<Rightarrow> RecvEP [thread] | RecvEP ts \<Rightarrow> RecvEP (ts @ [thread])))
     (\<acute>ret__unsigned :== CALL endpoint_ptr_get_epQueue_tail(epptr');;
      \<acute>endpointTail :== tcb_Ptr \<acute>ret__unsigned;;
      IF \<acute>endpointTail = tcb_Ptr 0 THEN
         (Guard C_Guard \<lbrace>hrs_htd \<acute>t_hrs \<Turnstile>\<^sub>t \<acute>ksCurThread\<rbrace>
            (ptr_basic_update (\<lambda>s. tcb_Ptr_Ptr &((ksCurThread_' (globals s))\<rightarrow>[''tcbEPPrev_C''])) (\<lambda>_. NULL)));;
         (Guard C_Guard \<lbrace>hrs_htd \<acute>t_hrs \<Turnstile>\<^sub>t \<acute>ksCurThread\<rbrace>
          (ptr_basic_update (\<lambda>s. tcb_Ptr_Ptr &((ksCurThread_' (globals s))\<rightarrow>[''tcbEPNext_C''])) (\<lambda>_. NULL)));;
           (CALL endpoint_ptr_set_epQueue_head_np(epptr',ucast (ptr_val \<acute>ksCurThread)));;
           (CALL endpoint_ptr_mset_epQueue_tail_state(epptr',ucast (ptr_val \<acute>ksCurThread),
            scast EPState_Recv))
      ELSE
        Guard C_Guard \<lbrace>hrs_htd \<acute>t_hrs \<Turnstile>\<^sub>t \<acute>endpointTail\<rbrace>
          (ptr_basic_update (\<lambda>s. tcb_Ptr_Ptr &((endpointTail_' s)\<rightarrow>[''tcbEPNext_C'']))
                      (ksCurThread_' o globals));;
         (Guard C_Guard \<lbrace>hrs_htd \<acute>t_hrs \<Turnstile>\<^sub>t \<acute>ksCurThread\<rbrace>
          (ptr_basic_update (\<lambda>s. tcb_Ptr_Ptr &((ksCurThread_' (globals s))\<rightarrow>[''tcbEPPrev_C'']))
                      endpointTail_'));;
         (Guard C_Guard \<lbrace>hrs_htd \<acute>t_hrs \<Turnstile>\<^sub>t \<acute>ksCurThread\<rbrace>
          (ptr_basic_update (\<lambda>s. tcb_Ptr_Ptr &((ksCurThread_' (globals s))\<rightarrow>[''tcbEPNext_C'']))
                      (\<lambda>_. NULL)));;
           (CALL endpoint_ptr_mset_epQueue_tail_state(epptr',ucast (ptr_val \<acute>ksCurThread),
            scast EPState_Recv))
      FI)"
  unfolding setEndpoint_def
  apply clarsimp
  apply (rule setObject_ccorres_helper[rotated])
    apply simp
   apply (simp add: objBits_simps')
  apply (rule conseqPre, vcg)
  apply clarsimp
  apply (drule(1) ko_at_obj_congD')
  apply (frule ko_at_valid_ep', clarsimp)
  apply (rule cmap_relationE1[OF cmap_relation_ep], assumption,
         erule ko_at_projectKO_opt)
  apply (simp add: cur_tcb'_def)
  apply (drule(1) obj_at_cslift_tcb)
  apply (clarsimp simp: typ_heap_simps' valid_ep'_def rf_sr_ksCurThread)
  apply (cases ep,
         simp_all add: isSendEP_def cendpoint_relation_def Let_def
                       tcb_queue_relation'_def)
   apply (rename_tac list)
   apply (clarsimp simp: NULL_ptr_val[symmetric] tcb_queue_relation_last_not_NULL
                         ct_in_state'_def
                  dest!: trans [OF sym [OF ptr_val_def] arg_cong[where f=ptr_val]])
   apply (frule obj_at_cslift_tcb[rotated], erule(1) bspec[OF _ last_in_set])
   apply clarsimp
   apply (drule(2) sym_refs_upd_tcb_sD)
   apply clarsimp
   apply (frule st_tcb_at_not_in_ep_queue,
          fastforce, simp+)
   apply (subgoal_tac "ksCurThread \<sigma> \<noteq> last list")
    prefer 2
    apply clarsimp
   apply (clarsimp simp: typ_heap_simps' EPState_Recv_def mask_def
                         is_aligned_weaken[OF is_aligned_tcb_ptr_to_ctcb_ptr])
   apply (clarsimp simp: rf_sr_def cstate_relation_def Let_def)
   apply (rule conjI)
    apply (clarsimp simp: cpspace_relation_def update_ep_map_tos
                          typ_heap_simps')
    apply (rule conjI, erule ctcb_relation_null_ep_ptrs)
     apply (rule ext, simp add: tcb_null_ep_ptrs_def
                         split: if_split)
    apply (rule conjI)
     apply (rule_tac S="tcb_ptr_to_ctcb_ptr ` set (ksCurThread \<sigma> # list)"
                   in cpspace_relation_ep_update_an_ep,
                 assumption+)
         apply (simp add: cendpoint_relation_def Let_def EPState_Recv_def
                          tcb_queue_relation'_def)
         apply (drule_tac qend="tcb_ptr_to_ctcb_ptr (last list)"
                     and qend'="tcb_ptr_to_ctcb_ptr (ksCurThread \<sigma>)"
                     and tn_update="tcbEPNext_C_update"
                     and tp_update="tcbEPPrev_C_update"
                     in tcb_queue_relation_append,
                    clarsimp+, simp_all)[1]
           apply (rule sym, erule init_append_last)
          apply (fastforce simp: tcb_at_not_NULL)
         apply (clarsimp simp add: tcb_at_not_NULL[OF obj_at'_weakenE[OF _ TrueI]])
        apply clarsimp+
     apply (subst st_tcb_at_not_in_ep_queue, assumption, blast, clarsimp+)
     apply (drule(1) ep_ep_disjoint[rotated -1, where epptr=epptr],
            blast, blast,
            simp_all add: Int_commute endpoint_not_idle_cases image_image)[1]
    apply (erule iffD1 [OF cmap_relation_cong, OF refl refl, rotated -1])
    apply simp
    apply (rule cntfn_relation_double_fun_upd)
     apply (rule cnotification_relation_ep_queue, assumption+)
        apply fastforce
       apply (simp add: isRecvEP_def)
      apply simp
     apply (fastforce dest!: map_to_ko_atI)

    apply (rule cnotification_relation_q_cong)
    apply (clarsimp split: if_split)
    apply (clarsimp simp: restrict_map_def ntfn_q_refs_of'_def
                   split: if_split Structures_H.notification.split_asm Structures_H.ntfn.split_asm)
    apply (erule notE[rotated], erule_tac ntfnptr=p and ntfn=a in st_tcb_at_not_in_ntfn_queue,
           auto dest!: map_to_ko_atI)[1]
   apply (simp add: carch_state_relation_def typ_heap_simps' update_ep_map_tos
                    cmachine_state_relation_def h_t_valid_clift_Some_iff)
  apply (clarsimp simp: typ_heap_simps' EPState_Recv_def mask_def
                        is_aligned_weaken[OF is_aligned_tcb_ptr_to_ctcb_ptr])
  apply (clarsimp simp: rf_sr_def cstate_relation_def Let_def)
  apply (drule(2) sym_refs_upd_tcb_sD)
  apply (rule conjI)
   apply (clarsimp simp: cpspace_relation_def update_ep_map_tos
                         typ_heap_simps' ct_in_state'_def)
   apply (rule conjI, erule ctcb_relation_null_ep_ptrs)
    apply (rule ext, simp add: tcb_null_ep_ptrs_def
                        split: if_split)
   apply (rule conjI)
    apply (rule_tac S="{tcb_ptr_to_ctcb_ptr (ksCurThread \<sigma>)}"
                 in cpspace_relation_ep_update_an_ep, assumption+)
        apply (simp add: cendpoint_relation_def Let_def EPState_Recv_def
                         tcb_queue_relation'_def)
       apply clarsimp+
    apply (erule notE[rotated], erule st_tcb_at_not_in_ep_queue,
           auto)[1]
   apply (erule iffD1 [OF cmap_relation_cong, OF refl refl, rotated -1])
   apply simp
   apply (rule cnotification_relation_q_cong)
   apply (clarsimp split: if_split)
   apply (clarsimp simp: restrict_map_def ntfn_q_refs_of'_def
                  split: if_split Structures_H.notification.split_asm Structures_H.ntfn.split_asm)
   apply (erule notE[rotated], rule_tac ntfnptr=p and ntfn=a in st_tcb_at_not_in_ntfn_queue,
          assumption+, auto dest!: map_to_ko_atI)[1]
  apply (simp add: carch_state_relation_def typ_heap_simps' update_ep_map_tos
                   cmachine_state_relation_def h_t_valid_clift_Some_iff)
  done
