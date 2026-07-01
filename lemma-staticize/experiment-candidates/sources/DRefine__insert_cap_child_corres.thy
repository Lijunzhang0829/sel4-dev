(* lemma: insert_cap_child_corres
   thy: proof/drefine/CNode_DR.thy:259 (proof hot line 332)
   session: DRefine
   arm: search  source: db-scan  tier: sweet  tactic: auto
   REASON: db-scan: lemma total 19s, search 19s (frac 1.00), single classical line(s); hot = auto carrying 19s.
*)

lemma insert_cap_child_corres:
  "dcorres dc \<top>
        (\<lambda>s. cte_wp_at (\<lambda>cap'. should_be_parent_of cap' (is_original_cap s src)
                 cap (cap_insert_dest_original cap cap')) src s
              \<and> not_idle_thread (fst child) s \<and> valid_idle s \<and> valid_etcbs s
              \<and> valid_mdb s \<and> not_idle_thread (fst src) s \<and> valid_objs s \<and> cap_aligned cap)
        (insert_cap_child (transform_cap cap) (transform_cslot_ptr src) (transform_cslot_ptr child))
        (cap_insert cap src child)"
  supply revokable_cap_insert_dest_original[simp]
  supply if_cong[cong]
  apply (simp add: cap_insert_def[folded cap_insert_dest_original_def])
  apply (simp add: insert_cap_child_def insert_cap_orphan_def bind_assoc
                   option_return_modify_modify
                   gets_fold_into_modify update_cdt_modify
                   set_original_def modify_modify
                   cap_insert_ext_def update_cdt_list_def set_cdt_list_modify
             cong: option.case_cong)
  apply (rule stronger_corres_guard_imp)
    apply (rule corres_split[OF get_cap_corres], simp)+
        apply (rule_tac P="old_cap \<noteq> cdl_cap.NullCap" and P'="rv' \<noteq> cap.NullCap"
          in corres_symmetric_bool_cases)
          apply (clarsimp simp :transform_cap_def split:cap.splits arch_cap.splits)
         apply (simp add:assert_def)
         apply (rule corres_trivial)
         apply (simp add:corres_free_fail)
        apply (simp add:assert_def)
        apply (rule corres_split[OF dcorres_set_untyped_cap_as_full])
          apply (rule corres_split[OF set_cap_corres[OF refl refl]])
            apply (rule dcorres_set_parent_helper)
            apply (rule_tac P=\<top> and P'="(\<lambda>s. should_be_parent_of src_capa (orig s) cap orig')
              and cte_at src and cte_at child
              and (\<lambda>s. mdb_cte_at (swp cte_at s) (cdt s))" for orig orig'
              in corres_modify)
            apply (clarsimp split del: if_split)
            apply (subst if_P, assumption)+
            apply (clarsimp simp: opt_parent_def transform_def transform_asid_table_def
                               transform_objects_def transform_cdt_def
                               transform_current_thread_def)
            apply (clarsimp simp: fun_upd_def[symmetric] cong:if_cong)
            apply (subgoal_tac "inj_on transform_cslot_ptr ({src, child} \<union> dom (cdt s') \<union> ran (cdt s'))")
             apply (subst map_lift_over_f_eq map_lift_over_upd,
                 erule subset_inj_on, fastforce)+
             apply (simp add: fun_eq_iff)
            apply (rule_tac s=s' in transform_cdt_slot_inj_on_cte_at[where P=\<top>])
            apply (auto simp: swp_def dest: mdb_cte_atD
                      elim!: ranE)[1]
           apply (wp set_cap_caps_of_state2 get_cap_wp hoare_weak_lift_imp
                    | simp add: swp_def cte_wp_at_caps_of_state)+
         apply (wp set_cap_idle |
          simp add:set_untyped_cap_as_full_def split del:if_split)+
          apply (rule_tac Q = "\<lambda>r s. not_idle_thread (fst child) s
            \<and> should_be_parent_of src_capa (is_original_cap s child) cap (cap_insert_dest_original cap src_capa)
            \<and> mdb_cte_at (swp (cte_wp_at ((\<noteq>) cap.NullCap)) s) (cdt s)"
           in hoare_strengthen_post)
           apply (wp set_cap_mdb_cte_at | simp add:not_idle_thread_def)+
          apply (clarsimp simp:mdb_cte_at_def cte_wp_at_caps_of_state)
          apply fastforce
         apply (wp get_cap_wp set_cap_idle hoare_weak_lift_imp
           | simp split del:if_split add:set_untyped_cap_as_full_def)+
         apply (rule_tac Q = "\<lambda>r s. not_idle_thread (fst child) s
           \<and> (\<exists>cap. caps_of_state s src = Some cap)
           \<and> should_be_parent_of src_capa (is_original_cap s src) cap (cap_insert_dest_original cap src_capa)
           \<and> mdb_cte_at (swp (cte_wp_at ((\<noteq>) cap.NullCap)) s) (cdt s)"
       in hoare_strengthen_post)
          apply (wp set_cap_mdb_cte_at hoare_weak_lift_imp | simp add:not_idle_thread_def)+
         apply (clarsimp simp:mdb_cte_at_def cte_wp_at_caps_of_state)
         apply fastforce
        apply clarsimp
        apply (wp get_cap_wp |simp)+
  apply (clarsimp simp: not_idle_thread_def)
  apply (clarsimp simp: caps_of_state_transform_opt_cap cte_wp_at_caps_of_state
    transform_cap_def)+
  apply (clarsimp simp: valid_mdb_def cte_wp_at_cases dest!:invs_mdb)
  by (case_tac "cdt s' child", safe intro!: mdb_cte_atI;
         auto dest: mdb_cte_atD is_untyped_cap_eqD
              simp: valid_mdb_def swp_def cte_wp_at_caps_of_state not_idle_thread_def)
