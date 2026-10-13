(* lemma: s0H_pspace_distinct'
   thy: proof/infoflow/refine/ARM/Example_Valid_StateH.thy:1911 (proof hot line 1920)
   session: InfoFlowC
   arm: hard  source: db-scan  tier: hard  tactic: fastforce
   REASON: db-scan: lemma total 745s, search 692s (frac 0.93), 3 classical line(s); hot = fastforce carrying 692s.
*)

lemma s0H_pspace_distinct':
  notes pdeBits_def[simp] pteBits_def[simp] objBits_defs[simp]
  shows
  "pspace_distinct' s0H_internal"
  supply option.case_cong[cong] if_cong[cong]
  apply (clarsimp simp: pspace_distinct'_def ps_clear_def)
  apply (rule disjointI)
  apply clarsimp
  apply (drule kh0H_SomeD)+
  by (simp | erule disjE
        | clarsimp simp: kh0H_dom_sets_distinct[THEN orthD1]
        | clarsimp simp: kh0H_dom_sets_distinct[THEN orthD2]
        | fastforce simp: s0_ptr_defs objBitsKO_def pageBits_def kh0H_obj_def
        | clarsimp simp: irq_node_offs_range_def objBitsKO_def s0_ptr_defs,
          drule_tac x="0xF" in word_plus_strict_mono_right, fastforce, simp add: add.commute,
          drule(1) notE[rotated, OF less_trans, OF _ _ leD, rotated 2], fastforce, simp
        | clarsimp simp: pt_offs_range_def pd_offs_range_def irq_node_offs_range_def cnode_offs_range_def objBitsKO_def archObjSize_def s0_ptr_defs kh0H_obj_def,
          drule(1) aligned_le_sharp, simp add: mask_def,
          drule_tac x="0x3" in word_plus_mono_right, fastforce, simp add: add.commute,
          (drule(1) notE[rotated, OF le_less_trans, OF _ _ leD, rotated 2], fastforce, simp
          | drule(2) notE[rotated, OF le_less_trans, OF _ _ leD[OF order_trans], rotated 2], fastforce, simp)
        | clarsimp simp: objBitsKO_def pageBits_def cnode_offs_range_def pd_offs_range_def pt_offs_range_def irq_node_offs_range_def s0_ptr_defs kh0H_obj_def,
          drule(1) notE[rotated, OF le_less_trans, OF _ _ leD, rotated 2]
                   notE[rotated, OF le_less_trans, OF _ _ leD], fastforce, simp
        | (clarsimp simp: objBitsKO_def pageBits_def cnode_offs_range_def pd_offs_range_def pt_offs_range_def irq_node_offs_range_def s0_ptr_defs kh0H_obj_def Low_cte'_def Low_capsH_def cte_level_bits_def empty_cte_def High_cte'_def High_capsH_def Silc_cte'_def Silc_capsH_def split: if_split_asm,
         (drule(1) aligned_le_sharp, simp add: mask_def,
          drule_tac x="0xF" in word_plus_mono_right, fastforce, simp add: add.commute,
          (drule(1) notE[rotated, OF le_less_trans, OF _ _ leD, rotated 2]
                    notE[rotated, OF le_less_trans, OF _ _ leD], fastforce, simp
          | drule(2) notE[rotated, OF less_trans, OF _ _ leD[OF order_trans], rotated 2]
                     notE[rotated, OF le_less_trans, OF _ _ leD[OF order_trans], rotated 2],
               fastforce, simp))+)[1]
        | clarsimp simp: objBitsKO_def irq_node_offs_range_def cnode_offs_range_def pd_offs_range_def pt_offs_range_def cte_level_bits_def s0_ptr_defs,
          drule_tac x="0xF" in word_plus_strict_mono_right, fastforce, simp add: add.commute,
          drule(2) notE[rotated, OF less_trans, OF _ _ leD[OF order_trans], rotated 2]
                   notE[rotated, OF le_less_trans, OF _ _ leD[OF order_trans], rotated 2],
              fastforce, simp
        | (clarsimp simp: irq_node_offs_range_def cnode_offs_range_def pd_offs_range_def pt_offs_range_def s0_ptr_defs objBitsKO_def archObjSize_def kh0H_obj_def Low_cte'_def Low_capsH_def High_cte'_def High_capsH_def Silc_cte'_def Silc_capsH_def cte_level_bits_def empty_cte_def split: if_split_asm,
          (drule(1) aligned_le_sharp, simp add: mask_neg_add_aligned, fastforce simp: mask_def)+)[1])+
