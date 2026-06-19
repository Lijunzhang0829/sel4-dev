(* lemma: blocked_cancel_ipc_invs
   thy: proof/invariant-abstract/IpcCancel_AI.thy:349 (proof hot line 369)
   session: AInvs
   arm: search  source: db-scan  tier: mid  tactic: auto
   REASON: db-scan: lemma total 125s, search 125s (frac 1.00), single classical line(s); hot = auto carrying 125s.
*)

lemma blocked_cancel_ipc_invs:
  "\<lbrace>invs and st_tcb_at ((=) st) t\<rbrace> blocked_cancel_ipc st t \<lbrace>\<lambda>rv. invs\<rbrace>"
  apply (simp add: blocked_cancel_ipc_def)
  apply (rule bind_wp [OF _ gbi_ep_sp])
  apply (rule bind_wp [OF _ get_simple_ko_sp])
  apply (rule bind_wp [OF _ get_epq_sp])
  apply (simp add: invs_def valid_state_def valid_pspace_def)
  apply (rule hoare_pre, wp valid_irq_node_typ sts_only_idle)
   apply (simp add: valid_tcb_state_def)
   apply (strengthen reply_cap_doesnt_exist_strg)
   apply simp
   apply (wp valid_irq_node_typ valid_ioports_lift)
  apply (subgoal_tac "ep \<noteq> Structures_A.IdleEP")
   apply (clarsimp simp: ep_redux_simps2 cong: if_cong)
   apply (frule(1) if_live_then_nonz_capD, (clarsimp simp: live_def)+)
   apply (frule ko_at_state_refs_ofD)
   apply (erule(1) obj_at_valid_objsE, clarsimp simp: valid_obj_def)
   apply (frule st_tcb_at_state_refs_ofD)
   apply (subgoal_tac "epptr \<notin> set (remove1 t queue)")
    apply (case_tac ep, simp_all add: valid_ep_def)[1]
     apply (auto elim!: delta_sym_refs pred_tcb_weaken_strongerE
                 simp: obj_at_def is_ep_def2 idle_not_queued refs_in_tcb_bound_refs
                 dest: idle_no_refs
                 split: if_split_asm)[2]
   apply (case_tac ep, simp_all add: valid_ep_def)[1]
    apply (clarsimp, drule(1) bspec, clarsimp simp: obj_at_def is_tcb_def)+
  apply fastforce
  done
