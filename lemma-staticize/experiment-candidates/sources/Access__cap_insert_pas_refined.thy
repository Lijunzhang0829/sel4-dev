(* lemma: cap_insert_pas_refined
   thy: proof/access-control/CNode_AC.thy:1014 (proof hot line 1032)
   session: Access
   arm: search  source: db-scan  tier: sweet  tactic: fastforce
   REASON: db-scan: lemma total 21s, search 21s (frac 1.00), single classical line(s); hot = fastforce carrying 21s.
*)

lemma cap_insert_pas_refined:
  "\<lbrace>pas_refined aag and pspace_aligned and valid_vspace_objs and valid_arch_state and valid_mdb and
    (\<lambda>s. (is_transferable_in src_slot s \<and> (\<not> Option.is_none (cdt s src_slot)))
         \<longrightarrow> is_transferable_cap new_cap) and
    K (is_subject aag (fst dest_slot) \<and> is_subject aag (fst src_slot)
                                      \<and> pas_cap_cur_auth aag new_cap) \<rbrace>
   cap_insert new_cap src_slot dest_slot
   \<lbrace>\<lambda>rv. pas_refined aag\<rbrace>"
  apply (rule hoare_gen_asm)
  apply (simp add: cap_insert_def)
  apply (rule hoare_pre)
  apply (wp set_cap_pas_refined set_cdt_pas_refined update_cdt_pas_refined hoare_vcg_imp_lift
            hoare_weak_lift_imp hoare_vcg_all_lift set_cap_caps_of_state2
            set_untyped_cap_as_full_cdt_is_original_cap get_cap_wp
            tcb_domain_map_wellformed_lift hoare_vcg_disj_lift
            set_untyped_cap_as_full_is_transferable'
         | simp split del: if_split del: split_paired_All fun_upd_apply
         | strengthen update_one_strg)+
  by (fastforce split: if_split_asm
                 simp: cte_wp_at_caps_of_state pas_refined_refl F[symmetric]
                       valid_mdb_def2 mdb_cte_at_def Option.is_none_def
             simp del: split_paired_All
                 dest: aag_cdt_link_Control aag_cdt_link_DeleteDerived cap_auth_caps_of_state)
