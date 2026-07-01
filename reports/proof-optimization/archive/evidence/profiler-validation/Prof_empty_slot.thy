theory Prof_empty_slot
  imports "Access.CNode_AC"
begin

(* Fix B snippet (faithful; == time_profile.snippet.thy) *)
method_setup time_profile = \<open>
  Method.text_closure >> (fn m => fn ctxt =>
    (fn facts => fn cst =>
      Seq.of_list (ML_Profiling.profile_time
        (fn () => Seq.list_of (Method.evaluate m ctxt facts cst)) ())))
\<close>

context CNode_AC_3 begin

(* re-test the IMPORT context (full CNode_AC in scope) with the FAITHFUL Fix B
   wrapper, and a PLAIN copy. If both build, the import-context proof is genuinely
   easy here (NOT a wrapper artifact). *)
lemma empty_slot_PROF:
  "\<lbrace>pas_refined aag and pspace_aligned and valid_vspace_objs and valid_arch_state
                    and valid_mdb and K (is_subject aag (fst slot))\<rbrace>
   empty_slot slot irqopt
   \<lbrace>\<lambda>_. pas_refined aag\<rbrace>"
  apply (simp add: empty_slot_def post_cap_deletion_def)
  apply (wpsimp wp: set_cap_pas_refined get_cap_wp set_cdt_pas_refined
                    tcb_domain_map_wellformed_lift hoare_drop_imps)
  apply (strengthen aag_wellformed_delete_derived_trans[OF _ _ pas_refined_wellformed, mk_strg I _ _ A])
  by (time_profile \<open>(fastforce dest: all_childrenD is_transferable_all_children
                      pas_refined_mem[OF sta_cdt] pas_refined_mem[OF sta_cdt_transferable]
                      pas_refined_Control)\<close>)

lemma empty_slot_PLAIN:
  "\<lbrace>pas_refined aag and pspace_aligned and valid_vspace_objs and valid_arch_state
                    and valid_mdb and K (is_subject aag (fst slot))\<rbrace>
   empty_slot slot irqopt
   \<lbrace>\<lambda>_. pas_refined aag\<rbrace>"
  apply (simp add: empty_slot_def post_cap_deletion_def)
  apply (wpsimp wp: set_cap_pas_refined get_cap_wp set_cdt_pas_refined
                    tcb_domain_map_wellformed_lift hoare_drop_imps)
  apply (strengthen aag_wellformed_delete_derived_trans[OF _ _ pas_refined_wellformed, mk_strg I _ _ A])
  by (fastforce dest: all_childrenD is_transferable_all_children
                      pas_refined_mem[OF sta_cdt] pas_refined_mem[OF sta_cdt_transferable]
                      pas_refined_Control)

end
end
