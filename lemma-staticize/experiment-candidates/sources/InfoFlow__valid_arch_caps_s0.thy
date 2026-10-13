(* lemma: valid_arch_caps_s0
   thy: proof/infoflow/ARM/Example_Valid_State.thy:1608 (proof hot line 1622)
   session: InfoFlow
   arm: search  source: db-scan  tier: sweet  tactic: auto
   REASON: db-scan: lemma total 16s, search 16s (frac 1.00), single classical line(s); hot = auto carrying 16s.
*)

lemma valid_arch_caps_s0[simp]:
  "valid_arch_caps s0_internal"
  apply (clarsimp simp: valid_arch_caps_def)
  apply (intro conjI)
     apply (clarsimp simp: valid_vs_lookup_def vs_lookup_pages_def vs_asid_refs_def
                           s0_internal_def arch_state0_def)
    apply (clarsimp simp: valid_table_caps_def is_pd_cap_def is_pt_cap_def)
    apply (drule s0_caps_of_state)
    apply (erule disjE | simp)+
   apply (clarsimp simp: unique_table_caps_def is_pd_cap_def is_pt_cap_def)
   apply (drule s0_caps_of_state)+
   apply (erule disjE | simp)+
  apply (clarsimp simp: unique_table_refs_def table_cap_ref_def)
  apply (drule s0_caps_of_state)+
  by auto
