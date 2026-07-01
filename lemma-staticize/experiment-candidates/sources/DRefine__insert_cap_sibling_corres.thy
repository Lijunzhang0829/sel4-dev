(* lemma: insert_cap_sibling_corres
   thy: proof/drefine/CNode_DR.thy:166 (proof hot line 255)
   session: DRefine
   arm: search  source: db-scan  tier: sweet  tactic: auto
   REASON: db-scan: lemma total 15s, search 15s (frac 1.00), single classical line(s); hot = auto carrying 15s.
*)

lemma insert_cap_sibling_corres:
  "dcorres dc \<top>
        (\<lambda>s. cte_wp_at (\<lambda>cap'. \<not> should_be_parent_of cap' (is_original_cap s src)
                 cap (cap_insert_dest_original cap cap')) src s
              \<and> cte_wp_at ((=) cap.NullCap) sibling s
              \<and> cte_at src s
              \<and> not_idle_thread (fst sibling) s
              \<and> not_idle_thread (fst src) s \<and> valid_etcbs s
              \<and> valid_mdb s \<and> valid_idle s \<and> valid_objs s \<and> cap_aligned cap)
        (insert_cap_sibling (transform_cap cap) (transform_cslot_ptr src) (transform_cslot_ptr sibling))
        (cap_insert cap src sibling)"
  supply if_cong[cong]
  apply (simp add: cap_insert_def[folded cap_insert_dest_original_def])
  apply (simp add: insert_cap_sibling_def insert_cap_orphan_def bind_assoc
                   option_return_modify_modify
                   gets_fold_into_modify update_cdt_modify
                   set_original_def modify_modify
                   cap_insert_ext_def update_cdt_list_def set_cdt_list_modify
             cong: option.case_cong)
  apply (rule stronger_corres_guard_imp)
     apply (rule corres_split[OF get_cap_corres], simp)+
        apply (rule corres_assert_lhs corres_assert_rhs)+
        apply (rule_tac F = "src_cap = transform_cap src_capa" in corres_gen_asm)
        apply simp
        apply (rule corres_split[OF dcorres_set_untyped_cap_as_full])
          apply (rule corres_split[OF set_cap_corres[OF refl refl]])
            apply (rule dcorres_opt_parent_set_parent_helper)
            apply (clarsimp simp:gets_fold_into_modify dc_def[symmetric]
              option_return_modify_modify modify_modify bind_assoc
              cong:option.case_cong)
            apply (rule_tac P=\<top> and P'="(\<lambda>s. \<not> should_be_parent_of src_capa (is_original_cap s src) cap orig')
              and cte_at src and cte_at sibling
              and (\<lambda>s. mdb_cte_at (swp cte_at s) (cdt s))
              and (\<lambda>s. cdt s sibling = None)" for orig'
              in corres_modify)
            apply (clarsimp split del: if_split)
            apply (subst if_not_P, assumption)+
            apply (clarsimp simp: opt_parent_def transform_def
              transform_objects_def transform_cdt_def
              transform_current_thread_def
              transform_asid_table_def
              split: option.split)
            apply (clarsimp simp: fun_upd_def[symmetric] cong:if_cong)
            apply (subgoal_tac "inj_on transform_cslot_ptr ({src, sibling} \<union> dom (cdt s') \<union> ran (cdt s'))")
             apply (subst map_lift_over_f_eq map_lift_over_upd,
               erule subset_inj_on, fastforce)+
             apply (simp add: map_option_is_None[THEN trans [OF eq_commute]]
               fun_eq_iff del: inj_on_insert)
             apply (subst eq_commute [where a=None])
             apply (subst map_lift_over_f_eq map_lift_over_upd,
               erule subset_inj_on, fastforce)+
             apply clarsimp
            apply (rule_tac s=s' in transform_cdt_slot_inj_on_cte_at[where P=\<top>])
            apply (auto simp: swp_def dest: mdb_cte_atD
              elim!: ranE)[1]
           apply ((wp set_cap_caps_of_state2 get_cap_wp hoare_weak_lift_imp
             | simp add: swp_def cte_wp_at_caps_of_state)+)
         apply (wp set_cap_idle |
            simp add:set_untyped_cap_as_full_def split del: if_split)+
          apply (rule_tac Q = "\<lambda>r s. cdt s sibling = None
           \<and> \<not> should_be_parent_of src_capa (is_original_cap s sibling) cap (cap_insert_dest_original cap src_capa)
           \<and> mdb_cte_at (swp (cte_wp_at ((\<noteq>) cap.NullCap)) s) (cdt s)"
           in hoare_strengthen_post)
           apply (wp set_cap_mdb_cte_at arch_update_cap_valid_mdb)
          apply (clarsimp simp:mdb_cte_at_def should_be_parent_of_def
           cte_wp_at_caps_of_state has_parent_cte_at is_physical_def
           dest!:is_untyped_cap_eqD)
          apply fastforce
         apply (wp get_cap_wp set_cap_idle hoare_weak_lift_imp
           | simp add:set_untyped_cap_as_full_def
           split del: if_split)+
         apply (rule_tac Q = "\<lambda>r s. cdt s sibling = None
           \<and> (\<exists>cap. caps_of_state s src = Some cap)
           \<and> \<not> should_be_parent_of src_capa (is_original_cap s src) cap (cap_insert_dest_original cap src_capa)
           \<and> mdb_cte_at (swp (cte_wp_at ((\<noteq>) cap.NullCap)) s) (cdt s)"
           in hoare_strengthen_post)
          apply (wp set_cap_mdb_cte_at arch_update_cap_valid_mdb)
         apply (clarsimp simp:mdb_cte_at_def should_be_parent_of_def
           cte_wp_at_caps_of_state has_parent_cte_at is_physical_def
           dest!:is_untyped_cap_eqD)
         apply fastforce
        apply (wpsimp wp: get_cap_wp set_cap_idle)+
   apply (clarsimp simp: not_idle_thread_def)
   apply (clarsimp simp: caps_of_state_transform_opt_cap cte_wp_at_caps_of_state
     transform_cap_def)
  apply (clarsimp simp: not_idle_thread_def cte_wp_at_caps_of_state)
  apply (clarsimp simp: valid_mdb_def cte_wp_at_cases dest!:invs_mdb)
  apply (case_tac "cdt s' sibling", safe intro!: mdb_cte_atI)
  (* 145 subgoals *)
  by (auto dest: mdb_cte_atD is_untyped_cap_eqD
           simp: revokable_cap_insert_dest_original valid_mdb_def
                 swp_def cte_wp_at_caps_of_state not_idle_thread_def)
