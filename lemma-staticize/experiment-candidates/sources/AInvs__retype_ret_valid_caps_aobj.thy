(* lemma: retype_ret_valid_caps_aobj
   thy: proof/invariant-abstract/ARM/ArchUntyped_AI.thy:166 (proof hot line 175)
   session: AInvs
   arm: search  source: wasted-classical  tier: sweet  tactic: fastforce
   REASON: wasted-classical: fastforce+simp: line at 5.3s, NO split/dest/elim/intro (classical likely wasted), file 597L (baseline-able). Band of the 2 confirmed fastforce->clarsimp wins.
*)

lemma retype_ret_valid_caps_aobj[Untyped_AI_assms]:
  "\<And>ptr sz (s::'state_ext::state_ext state) x6 us n.
  \<lbrakk>pspace_no_overlap_range_cover ptr sz s \<and> x6 \<noteq> ASIDPoolObj \<and>
  range_cover ptr sz (obj_bits_api (ArchObject x6) us) n \<and> ptr \<noteq> 0\<rbrakk>
            \<Longrightarrow> \<forall>y\<in>{0..<n}. s
                   \<lparr>kheap := foldr (\<lambda>p kh. kh(p \<mapsto> default_object (ArchObject x6) dev us)) (map (\<lambda>p. ptr_add ptr (p * 2 ^ obj_bits_api (ArchObject x6) us)) [0..<n])
                              (kheap s)\<rparr> \<turnstile> ArchObjectCap (ARM_A.arch_default_cap x6 (ptr_add ptr (y * 2 ^ obj_bits_api (ArchObject x6) us)) us dev)"
  apply (rename_tac aobject_type us n)
  apply (case_tac aobject_type)
  by (clarsimp simp: valid_cap_def default_object_def cap_aligned_def
                     cte_level_bits_def is_obj_defs well_formed_cnode_n_def empty_cnode_def
                     dom_def arch_default_cap_def ptr_add_def
      | intro conjI obj_at_foldr_intro
        imageI valid_vm_rights_def
      | rule is_aligned_add_multI[OF _ le_refl]
      | fastforce simp:range_cover_def obj_bits_api_def
        default_arch_object_def valid_vm_rights_def  word_bits_def a_type_def)+
