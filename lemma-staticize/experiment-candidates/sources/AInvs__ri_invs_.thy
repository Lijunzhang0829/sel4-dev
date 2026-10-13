(* lemma: ri_invs'
   thy: proof/invariant-abstract/Ipc_AI.thy:2707 (proof hot line 2756)
   session: AInvs
   arm: search  source: db-scan  tier: sweet  tactic: fastforce
   REASON: db-scan: lemma total 25s, search 25s (frac 1.00), single classical line(s); hot = fastforce carrying 25s.
*)

lemma ri_invs':
  fixes Q t cap is_blocking
  notes if_split[split del]
  notes hyp_refs_of_simps[simp del]
  assumes set_endpoint_Q[wp]: "\<And>a b.\<lbrace>Q\<rbrace> set_endpoint a b \<lbrace>\<lambda>_.Q\<rbrace>"
  assumes set_notification_Q[wp]: "\<And>a b.\<lbrace>Q\<rbrace> complete_signal a b \<lbrace>\<lambda>_.Q\<rbrace>"
  assumes sts_Q[wp]: "\<And>a b. \<lbrace>Q\<rbrace> set_thread_state a b \<lbrace>\<lambda>_.Q\<rbrace>"
  assumes ext_Q[wp]: "\<And>a (s::'a::state_ext state). \<lbrace>Q and valid_objs\<rbrace> do_extended_op (possible_switch_to a) \<lbrace>\<lambda>_.Q\<rbrace>"
  assumes scc_Q[wp]: "\<And>a b c. \<lbrace>valid_mdb and Q\<rbrace> setup_caller_cap a b c \<lbrace>\<lambda>_.Q\<rbrace>"
  assumes dit_Q[wp]: "\<And>a b c d e. \<lbrace>valid_mdb and valid_objs and Q\<rbrace> do_ipc_transfer a b c d e \<lbrace>\<lambda>_.Q\<rbrace>"
  assumes failed_transfer_Q[wp]: "\<And>a. \<lbrace>Q\<rbrace> do_nbrecv_failed_transfer a \<lbrace>\<lambda>_. Q\<rbrace>"
  notes dxo_wp_weak[wp del]
  shows
  "\<lbrace>(invs::'state_ext state \<Rightarrow> bool) and Q and st_tcb_at active t and ex_nonz_cap_to t
         and cte_wp_at ((=) cap.NullCap) (t, tcb_cnode_index 3)
         and (\<lambda>s. \<forall>r\<in>zobj_refs cap. ex_nonz_cap_to r s)\<rbrace>
     receive_ipc t cap is_blocking \<lbrace>\<lambda>r s. invs s \<and> Q s\<rbrace>" (is "\<lbrace>?pre\<rbrace> _ \<lbrace>_\<rbrace>")
  apply (simp add: receive_ipc_def split_def)
  apply (cases cap, simp_all)
  apply (rename_tac ep badge rights)
  apply (rule bind_wp[OF _ get_simple_ko_sp])
  apply (rule bind_wp[OF _ gbn_sp])
  apply (rule bind_wp)
  (* set up precondition for old proof *)
   apply (rule_tac R="ko_at (Endpoint rv) ep and ?pre" in hoare_vcg_if_split)
    apply (wp complete_signal_invs)
   apply (case_tac rv)
     apply (wp | rule hoare_pre, wpc | simp)+
           apply (simp add: invs_def valid_state_def valid_pspace_def)
           apply (rule hoare_pre, wp valid_irq_node_typ valid_ioports_lift)
           apply (simp add: valid_ep_def)
          apply (wp valid_irq_node_typ sts_only_idle sts_ep_at_inv[simplified ep_at_def2, simplified]
                    failed_transfer_Q[simplified do_nbrecv_failed_transfer_def, simplified]
                 | simp add: live_def do_nbrecv_failed_transfer_def)+
     apply (clarsimp simp: st_tcb_at_tcb_at valid_tcb_state_def invs_def valid_state_def valid_pspace_def)
     apply (rule conjI, clarsimp elim!: obj_at_weakenE simp: is_ep_def)
     apply (rule conjI, clarsimp simp: st_tcb_at_reply_cap_valid)
     apply (rule conjI)
      apply (subgoal_tac "ep \<noteq> t")
       apply (drule obj_at_state_refs_ofD)
       apply (drule active_st_tcb_at_state_refs_ofD)
       apply (erule delta_sym_refs)
        apply (clarsimp split: if_split_asm)
       apply (clarsimp split: if_split_asm if_split)
       apply (fastforce dest!: symreftype_inverse'
                         simp: pred_tcb_at_def2 tcb_bound_refs_def2)
      apply (clarsimp simp: obj_at_def st_tcb_at_def)
     apply (simp add: obj_at_def is_ep_def)
     apply (fastforce dest!: idle_no_ex_cap valid_reply_capsD
                      simp: st_tcb_def2)
    apply (simp add: invs_def valid_state_def valid_pspace_def)
    apply (wp hoare_drop_imps valid_irq_node_typ hoare_post_imp[OF disjI1]
              sts_only_idle
         | simp add: valid_tcb_state_def cap_range_def
         | strengthen reply_cap_doesnt_exist_strg | wpc
         | (wp hoare_vcg_conj_lift | wp dxo_wp_weak | simp)+
         | wp valid_ioports_lift)+
    apply (clarsimp simp: st_tcb_at_tcb_at neq_Nil_conv)
    apply (frule(1) sym_refs_obj_atD)
    apply (frule(1) hyp_sym_refs_obj_atD)
    apply (frule ko_at_state_refs_ofD)
    apply (frule ko_at_state_hyp_refs_ofD)
    apply (erule(1) obj_at_valid_objsE)
    apply (clarsimp simp: st_tcb_at_refs_of_rev st_tcb_at_tcb_at
                          valid_obj_def ep_redux_simps
                    cong: list.case_cong if_cong)
    apply (frule(1) st_tcb_ex_cap[where P="\<lambda>ts. \<exists>pl. ts = st pl" for st],
           clarsimp+)
    apply (clarsimp simp: valid_ep_def)
    apply (frule active_st_tcb_at_state_refs_ofD)
    apply (frule st_tcb_at_state_refs_ofD
                 [where P="\<lambda>ts. \<exists>pl. ts = st pl" for st])
    apply (subgoal_tac "y \<noteq> t \<and> y \<noteq> idle_thread s \<and> t \<noteq> idle_thread s \<and>
                        idle_thread s \<notin> set ys")
     apply (clarsimp simp: st_tcb_def2 is_ep_def
       conj_comms tcb_at_cte_at_2)
     apply (clarsimp simp: obj_at_def)
     apply (erule delta_sym_refs)
      apply (clarsimp split: if_split_asm)
     apply (clarsimp split: if_split_asm if_split) (* FIXME *)
       apply ((fastforce simp: pred_tcb_at_def2 tcb_bound_refs_def2 is_tcb
                       dest!: symreftype_inverse')+)[3]
    apply (rule conjI)
     apply (clarsimp simp: pred_tcb_at_def2 tcb_bound_refs_def2
                     split: if_split_asm)
     apply (simp add: set_eq_subset)

    apply (rule conjI, clarsimp dest!: idle_no_ex_cap)+
    apply (simp add: idle_not_queued')
   apply (simp add: invs_def valid_state_def valid_pspace_def)
   apply (rule hoare_pre)
    apply (wp hoare_vcg_const_Ball_lift valid_irq_node_typ sts_only_idle
              sts_ep_at_inv[simplified ep_at_def2, simplified] valid_ioports_lift
              failed_transfer_Q[unfolded do_nbrecv_failed_transfer_def, simplified]
              | simp add: live_def valid_ep_def do_nbrecv_failed_transfer_def
              | wpc)+
   apply (clarsimp simp: valid_tcb_state_def st_tcb_at_tcb_at)
   apply (rule conjI, clarsimp elim!: obj_at_weakenE simp: is_ep_def)
   apply (rule conjI, fastforce simp: st_tcb_def2)
   apply (frule ko_at_state_refs_ofD)
   apply (frule active_st_tcb_at_state_refs_ofD)
   apply (frule(1) sym_refs_ko_atD)
   apply (rule obj_at_valid_objsE, assumption+)
   apply (clarsimp simp: valid_obj_def valid_ep_def)
   apply (rule context_conjI)
    apply (rule notI, (drule(1) bspec)+, (drule obj_at_state_refs_ofD)+, clarsimp)
    apply (clarsimp simp: pred_tcb_at_def2 tcb_bound_refs_def2)
    apply (blast intro: reftype.simps)
   apply (rule conjI, erule delta_sym_refs)
     apply (clarsimp split: if_split_asm if_split)
     apply (rule conjI, rule impI)
      apply (clarsimp simp: pred_tcb_at_def2 obj_at_def)
     apply (fastforce simp: pred_tcb_at_def2 tcb_bound_refs_def2
                     dest!: symreftype_inverse')
    apply (clarsimp split: if_split_asm if_split)
    apply (fastforce simp: pred_tcb_at_def2 tcb_bound_refs_def2
                    dest!: symreftype_inverse')
   apply (fastforce simp: obj_at_def is_ep pred_tcb_at_def2 dest!: idle_no_ex_cap valid_reply_capsD)
  apply (rule hoare_pre)
   apply (wp get_simple_ko_wp | wpc | clarsimp)+
  apply (clarsimp simp: pred_tcb_at_tcb_at)
  done
