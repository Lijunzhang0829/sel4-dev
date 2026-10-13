(* lemma: empty_slot_pas_refined_transferable
   thy: proof/access-control/CNode_AC.thy:1108 (proof hot line 1117)
   session: Access
   arm: search  source: db-scan  tier: sweet  tactic: fastforce
   REASON: db-scan: lemma total 27s, search 27s (frac 1.00), single classical line(s); hot = fastforce carrying 27s.
*)

lemma empty_slot_pas_refined_transferable[wp_transferable]:
  "\<lbrace>pas_refined aag and pspace_aligned and valid_vspace_objs and valid_arch_state
                    and valid_mdb and (\<lambda>s. is_transferable (caps_of_state s slot))\<rbrace>
   empty_slot slot irqopt
   \<lbrace>\<lambda>_. pas_refined aag\<rbrace>"
  apply (simp add: empty_slot_def post_cap_deletion_def)
  apply (wpsimp wp: set_cap_pas_refined get_cap_wp set_cdt_pas_refined
                    tcb_domain_map_wellformed_lift hoare_drop_imps)
  apply (strengthen aag_wellformed_delete_derived_trans[OF _ _ pas_refined_wellformed, mk_strg I _ _ A])
  by (fastforce simp: cte_wp_at_caps_of_state
                dest: all_childrenD is_transferable_all_children
                      pas_refined_mem[OF sta_cdt] pas_refined_mem[OF sta_cdt_transferable])
