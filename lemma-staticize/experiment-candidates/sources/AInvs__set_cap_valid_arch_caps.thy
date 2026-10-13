(* lemma: set_cap_valid_arch_caps
   thy: proof/invariant-abstract/ARM/ArchCSpaceInvPre_AI.thy:218 (proof hot line 241)
   session: AInvs
   arm: search  source: db-scan  tier: sweet  tactic: blast
   REASON: db-scan: lemma total 11s, search 11s (frac 1.00), single classical line(s); hot = blast carrying 11s.
*)

lemma set_cap_valid_arch_caps:
  "\<lbrace>\<lambda>s. valid_arch_caps s
      \<and> (\<forall>vref cap'. cte_wp_at ((=) cap') ptr s
                \<longrightarrow> vs_cap_ref cap' = Some vref
                \<longrightarrow> (vs_cap_ref cap = Some vref \<and> obj_refs cap = obj_refs cap')
                 \<or> (\<not> is_final_cap' cap' s \<and> \<not> reachable_pg_cap cap' s)
                 \<or> (\<forall>oref \<in> obj_refs cap'. \<not> (vref \<unrhd> oref) s))
      \<and> no_cap_to_obj_with_diff_ref cap {ptr} s
      \<and> ((is_pt_cap cap \<or> is_pd_cap cap) \<longrightarrow> cap_asid cap = None
            \<longrightarrow> (\<forall>r \<in> obj_refs cap. obj_at (empty_table (set (second_level_tables (arch_state s)))) r s))
      \<and> ((is_pt_cap cap \<or> is_pd_cap cap)
             \<longrightarrow> (\<forall>oldcap. caps_of_state s ptr = Some oldcap \<longrightarrow>
                  (is_pt_cap cap \<and> is_pt_cap oldcap \<or> is_pd_cap cap \<and> is_pd_cap oldcap)
                    \<longrightarrow> (cap_asid cap = None \<longrightarrow> cap_asid oldcap = None)
                    \<longrightarrow> obj_refs oldcap \<noteq> obj_refs cap)
             \<longrightarrow> (\<forall>ptr'. cte_wp_at (\<lambda>cap'. obj_refs cap' = obj_refs cap
                                              \<and> (is_pd_cap cap \<and> is_pd_cap cap' \<or> is_pt_cap cap \<and> is_pt_cap cap')
                                              \<and> (cap_asid cap = None \<or> cap_asid cap' = None)) ptr' s \<longrightarrow> ptr' = ptr))\<rbrace>
     set_cap cap ptr
   \<lbrace>\<lambda>rv. valid_arch_caps\<rbrace>"
  apply (simp add: valid_arch_caps_def pred_conj_def)
  apply (wp set_cap_valid_vs_lookup set_cap_valid_table_caps
            set_cap_unique_table_caps set_cap_unique_table_refs)
  by simp_all blast+
