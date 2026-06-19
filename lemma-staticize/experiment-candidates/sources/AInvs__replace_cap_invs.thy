(* lemma: replace_cap_invs
   thy: proof/invariant-abstract/ARM/ArchCSpaceInv_AI.thy:51 (proof hot line 123)
   session: AInvs
   arm: search  source: db-scan  tier: sweet  tactic: fastforce
   REASON: db-scan: lemma total 10s, search 10s (frac 1.00), single classical line(s); hot = fastforce carrying 10s.
*)

lemma replace_cap_invs:
  "\<lbrace>\<lambda>s. invs s \<and> cte_wp_at (replaceable s p cap) p s
        \<and> cap \<noteq> cap.NullCap
        \<and> ex_cte_cap_wp_to (appropriate_cte_cap cap) p s
        \<and> s \<turnstile> cap\<rbrace>
     set_cap cap p
   \<lbrace>\<lambda>rv s. invs s\<rbrace>"
  apply (simp add: invs_def valid_state_def valid_mdb_def2)
  apply (rule hoare_pre)
   apply (wp replace_cap_valid_pspace
             set_cap_caps_of_state2 set_cap_idle
             replace_cap_ifunsafe valid_irq_node_typ
             set_cap_typ_at set_cap_irq_handlers
             set_cap_valid_arch_caps
             set_cap_cap_refs_respects_device_region_replaceable)
  apply (clarsimp simp: valid_pspace_def cte_wp_at_caps_of_state replaceable_def)
  apply (rule conjI)
   apply (fastforce simp: tcb_cap_valid_def
                   dest!: cte_wp_tcb_cap_valid [OF caps_of_state_cteD])
  apply (rule conjI)
   apply (erule_tac P="\<lambda>cps. mdb_cte_at cps (cdt s)" in rsubst)
   apply (rule ext)
   apply (safe del: disjE)[1]
    apply (simp add: gen_obj_refs_empty final_NullCap)+
  apply (rule conjI)
   apply (simp add: untyped_mdb_def is_cap_simps)
   apply (erule disjE)
    apply (clarsimp, rule conjI, clarsimp+)[1]
   apply (erule allEI, erule allEI)
   apply (drule_tac x="fst p" in spec, drule_tac x="snd p" in spec)
   apply (clarsimp simp: gen_obj_refs_subset)
   apply (drule(1) disjoint_subset, erule (1) notE)
  apply (rule conjI)
   apply (erule descendants_inc_minor)
    apply simp
   apply (elim disjE)
    apply clarsimp
   apply clarsimp
  apply (rule conjI)
   apply (erule disjE)
    apply (simp add: fun_upd_def[symmetric] fun_upd_idem)
   apply (simp add: untyped_inc_def not_is_untyped_no_range)
  apply (rule conjI)
   apply (erule disjE)
    apply (simp add: fun_upd_def[symmetric] fun_upd_idem)
   apply (simp add: ut_revocable_def)
  apply (rule conjI)
   apply (erule disjE)
    apply (clarsimp simp: irq_revocable_def)
   apply clarsimp
   apply (clarsimp simp: irq_revocable_def)
  apply (rule conjI)
   apply (erule disjE)
    apply (simp add: fun_upd_def[symmetric] fun_upd_idem)
   apply (simp add: reply_master_revocable_def)
  apply (rule conjI)
   apply (erule disjE)
    apply (simp add: fun_upd_def[symmetric] fun_upd_idem)
   apply (clarsimp simp add: reply_mdb_def)
   apply (thin_tac "\<forall>a b. (a, b) \<in> cte_refs cp nd \<and> Q a b\<longrightarrow> R a b" for cp nd Q R)
   apply (thin_tac "is_pt_cap cap \<longrightarrow> P" for cap P)+
   apply (thin_tac "is_pd_cap cap \<longrightarrow> P" for cap P)+
   apply (rule conjI)
    apply (unfold reply_caps_mdb_def)[1]
    apply (erule allEI, erule allEI)
    apply (clarsimp split: if_split simp add: is_cap_simps
                 simp del: split_paired_Ex split_paired_All)
    apply (rename_tac ptra ptrb rights')
    apply (rule_tac x="(ptra,ptrb)" in exI)
    apply fastforce
   apply (unfold reply_masters_mdb_def)[1]
   apply (erule allEI, erule allEI)
   apply (fastforce split: if_split_asm simp: is_cap_simps)
  apply (rule conjI)
   apply (erule disjE)
    apply (clarsimp simp add: is_reply_cap_to_def)
    apply (drule caps_of_state_cteD)
    apply (subgoal_tac "cte_wp_at (is_reply_cap_to t) p s")
     apply (erule(1) valid_reply_capsD [OF has_reply_cap_cte_wpD])
    apply (erule cte_wp_at_lift)
    apply(fastforce simp add:is_reply_cap_to_def)
   apply (simp add: is_cap_simps)
  apply (frule(1) valid_global_refsD2)
  apply (frule(1) cap_refs_in_kernel_windowD)
  apply (rule conjI)
   apply (erule disjE)
    apply (clarsimp simp: valid_reply_masters_def cte_wp_at_caps_of_state)
    apply (cases p,fastforce simp:is_master_reply_cap_to_def)
   apply (simp add: is_cap_simps)
  apply (elim disjE)
   apply simp
   apply (clarsimp simp: valid_table_capsD[OF caps_of_state_cteD]
                    valid_arch_caps_def unique_table_refs_no_cap_asidE)
  apply simp
  apply (rule Ball_emptyI, simp add: gen_obj_refs_subset)
  done
