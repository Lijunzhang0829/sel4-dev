(* lemma: set_cap_slots_holding_overlapping_caps_other
   thy: proof/infoflow/FinalCaps.thy:657 (proof hot line 666)
   session: InfoFlow
   arm: work-suspect  source: db-scan  tier: sweet  tactic: fastforce
   FLAGS: WORK-SUSPECT (cost may be simp-work, not search)
   REASON: db-scan: lemma total 30s, search 30s (frac 1.00), single classical line(s); hot = fastforce carrying 30s.
*)

lemma set_cap_slots_holding_overlapping_caps_other:
  "\<lbrace>\<lambda>s. x \<in> slots_holding_overlapping_caps capa s \<and>
        pasObjectAbs aag (fst x) \<noteq> pasObjectAbs aag (fst slot)\<rbrace>
   set_cap cap slot
   \<lbrace>\<lambda>rv s. x \<in> slots_holding_overlapping_caps capa s\<rbrace>"
  unfolding set_cap_def
  apply (wpsimp wp: set_object_wp get_object_wp)+
  apply (case_tac "obj_refs capa = {} \<and> cap_irqs capa = {}")
   apply (clarsimp simp: slots_holding_overlapping_caps_def)
   apply (fastforce simp: get_cap_def get_object_def bind_def split_def gets_def get_def
                          return_def assert_def assert_opt_def fail_def
                   split: kernel_object.splits if_splits option.splits)
  apply (subgoal_tac "fst x \<noteq> fst slot")
   apply (intro allI impI conjI)
        apply (clarsimp simp: slots_holding_overlapping_caps_def)
        apply (rule_tac x=cap' in exI)
        apply clarsimp
        apply (subst get_cap_cte_wp_at')
        apply (rule upd_other_cte_wp_at)
         apply (simp add: cte_wp_at_def)
        apply assumption
        apply (clarsimp simp: get_cap_caps_of_state)
       apply ((drule set_cap_slots_holding_overlapping_caps_helper[where slot=slot], simp+)+)[5]
  apply clarsimp
  done
