(* lemma: inv_untyped_corres'
   thy: proof/refine/ARM/Untyped_R.thy:4601 (proof hot line 5002)
   session: Refine
   arm: search  source: db-scan  tier: sweet  tactic: auto
   REASON: db-scan: lemma total 24s, search 24s (frac 1.00), single classical line(s); hot = auto carrying 24s.
*)

lemma inv_untyped_corres':
  "\<lbrakk> untypinv_relation ui ui' \<rbrakk> \<Longrightarrow>
   corres (dc \<oplus> (=))
     (einvs and valid_untyped_inv ui and ct_active and schact_is_rct)
     (invs' and valid_untyped_inv' ui' and ct_active')
     (invoke_untyped ui) (invokeUntyped ui')"
  apply (cases ui)
  apply (rule corres_name_pre)
  apply (clarsimp simp only: valid_untyped_inv_wcap
           valid_untyped_inv_wcap'
           Invocations_A.untyped_invocation.simps
           Invocations_H.untyped_invocation.simps
           untypinv_relation.simps)
  apply (rename_tac cref oref reset ptr ptr' dc us slots dev s s' ao' sz sz' idx idx')
  proof -
    fix cref reset ptr ptr_base us slots dev ao' sz sz' idx idx' s s'

    let ?ui = "Invocations_A.Retype cref reset ptr_base ptr (APIType_map2 (Inr ao')) us slots dev"
    let ?ui' = "Invocations_H.untyped_invocation.Retype
                  (cte_map cref) reset ptr_base ptr ao' us (map cte_map slots) dev"

    assume invs: "invs (s :: det_state)" "ct_active s" "valid_list s" "valid_sched s"
                 "schact_is_rct s"
    and   invs': "invs' s'" "ct_active' s'"
    and      sr: "(s, s') \<in> state_relation"
    and     vui: "valid_untyped_inv_wcap ?ui (Some (cap.UntypedCap dev (ptr && ~~ mask sz) sz idx)) s"
                 (is "valid_untyped_inv_wcap _ (Some ?cap) s")
    and    vui': "valid_untyped_inv_wcap' ?ui' (Some (UntypedCap dev (ptr && ~~ mask sz') sz' idx')) s'"
    assume   ui: "ui = ?ui" and ui': "ui' = ?ui'"

    have cte_at: "cte_wp_at ((=) ?cap) cref s" (is "?cte_cond s")
       using vui by (simp add:cte_wp_at_caps_of_state)

    have ptr_sz_simp[simp]: "ptr_base = ptr && ~~ mask sz \<and> sz' = sz \<and> idx' = idx \<and> 2 \<le> sz"
       using cte_at vui vui' sr invs
       apply (clarsimp simp: cte_wp_at_ctes_of)
       apply (drule pspace_relation_cte_wp_atI'[OF state_relation_pspace_relation])
         apply (simp add:cte_wp_at_ctes_of)
        apply (simp add:invs_valid_objs)
       apply (clarsimp simp:is_cap_simps isCap_simps)
       apply (frule cte_map_inj_eq)
        apply ((erule cte_wp_at_weakenE | simp
          | clarsimp simp: cte_wp_at_caps_of_state)+)[5]
       apply (clarsimp simp:cte_wp_at_caps_of_state cte_wp_at_ctes_of)
       apply (drule caps_of_state_valid_cap,fastforce)
       apply (clarsimp simp:valid_cap_def untyped_min_bits_def)
       done

    have obj_bits_low_bound[simp]:
      "minUntypedSizeBits \<le> obj_bits_api (APIType_map2 (Inr ao')) us"
      using vui
      apply clarsimp
      apply (cases ao')
      apply (simp_all add: obj_bits_api_def slot_bits_def arch_kobj_size_def default_arch_object_def
                           APIType_map2_def untyped_min_bits_def minUntypedSizeBits_def
                    split: apiobject_type.splits)
      done

    have cover: "range_cover ptr sz
        (obj_bits_api (APIType_map2 (Inr ao')) us) (length slots)"
     and vslot: "slots\<noteq> []"
      using vui
      by (auto simp: cte_wp_at_caps_of_state)

    have misc'[simp]:
      "distinct (map cte_map slots)"
      using vui'
      by (auto simp: cte_wp_at_ctes_of)

    have intvl_eq[simp]:
    "ptr && ~~ mask sz = ptr \<Longrightarrow> {ptr + of_nat k |k. k < 2 ^ sz} = {ptr..ptr + 2 ^ sz - 1}"
      using cover
      apply (subgoal_tac "is_aligned (ptr &&~~ mask sz) sz")
       apply (rule intvl_range_conv)
        apply (simp)
       apply (drule range_cover.sz)
       apply simp
      apply (rule is_aligned_neg_mask,simp)
      done

    have delete_objects_rewrite:
      "ptr && ~~ mask sz = ptr \<Longrightarrow> delete_objects ptr sz =
      do y \<leftarrow> modify (clear_um {ptr + of_nat k |k. k < 2 ^ sz});
              modify (detype {ptr && ~~ mask sz..ptr + 2 ^ sz - 1})
      od"
      using cover
      apply (clarsimp simp:delete_objects_def freeMemory_def word_size_def)
      apply (subgoal_tac "is_aligned (ptr &&~~ mask sz) sz")
       apply (subst mapM_storeWord_clear_um[simplified word_size_def word_size_bits_def];
              clarsimp simp: range_cover_def word_bits_def)
      apply (rule is_aligned_neg_mask)
      apply simp
      done

    have of_nat_length: "(of_nat (length slots)::word32) - (1::word32) < (of_nat (length slots)::word32)"
       using vslot
       using range_cover.range_cover_le_n_less(1)[OF cover,where p = "length slots"]
       apply -
       apply (case_tac slots)
       apply clarsimp+
       apply (subst add.commute)
       apply (subst word_le_make_less[symmetric])
       apply (rule less_imp_neq)
       apply (simp add:word_bits_def minus_one_norm)
       apply (rule word_of_nat_less)
       apply auto
       done
    have not_0_ptr[simp]: "ptr\<noteq> 0"
       using cte_at invs
      apply (clarsimp simp:cte_wp_at_caps_of_state)
      apply (drule(1) caps_of_state_valid)+
      apply (simp add:valid_cap_def)
      done
    have size_eq[simp]: "APIType_capBits ao' us = obj_bits_api (APIType_map2 (Inr ao')) us"
      apply (case_tac ao')
        apply (rename_tac apiobject_type)
        apply (case_tac apiobject_type)
        apply (clarsimp simp: APIType_capBits_def objBits_simps' arch_kobj_size_def default_arch_object_def
                              obj_bits_api_def APIType_map2_def slot_bits_def pageBitsForSize_def)+
      done

    have non_reset_idx_le[simp]: "\<not> reset \<Longrightarrow> idx < 2^sz"
       using vui
       apply (clarsimp simp: cte_wp_at_caps_of_state )
       apply (erule le_less_trans)
       apply (rule unat_less_helper)
       apply simp
       apply (rule and_mask_less')
       using cover
       apply (clarsimp simp:range_cover_def)
       done

    note blah[simp del] = untyped_range.simps usable_untyped_range.simps atLeastAtMost_iff atLeastatMost_subset_iff atLeastLessThan_iff
          Int_atLeastAtMost atLeastatMost_empty_iff split_paired_Ex usableUntypedRange.simps

    have vc'[simp] : "s' \<turnstile>' capability.UntypedCap dev (ptr && ~~ mask sz) sz idx"
      using vui' invs'
      apply (clarsimp simp:cte_wp_at_ctes_of)
      apply (case_tac cte)
      apply clarsimp
      apply (erule ctes_of_valid_cap')
