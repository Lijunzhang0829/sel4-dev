(* Generic HOL record-layout certification, adapted from the pinned AFP
   AutoCorres2/c-parser/UMM_Proofs.ML (umm_mem_type and COMPONENT UPDATE).
   Copyright 2020 Data61, CSIRO; Copyright 2022 Apple Inc.
   SPDX-License-Identifier: BSD-2-Clause

   This module consumes existing HOL record/type-tag definitions. It does not
   parse C, run AutoCorres, add axioms, or skip a proof. *)
structure HOL_Record_Memory =
struct

fun certify recname field_count thy =
  let
    val ctxt = Proof_Context.init_global thy;
    val T = Syntax.read_typ ctxt recname;
    val typ_info = Global_Theory.get_thm thy ("typ_info_t_" ^ recname ^ "_def");
    val tag = Global_Theory.get_thm thy (recname ^ "_tag_def");
    val defs = [typ_info, tag] @ @{thms align_of_def size_of_def};
    fun ss context = context |> Simplifier.add_simps defs;

    val component = UMM_TermsTypes.mk_field_update_component_desc_apply T "bs" "v";
    val component_eq = Simplifier.asm_full_rewrite
      (ctxt |> put_simpset (Simplifier.merge_ss
        (HOL_basic_ss, RecursiveRecordPackage.get_simpset thy))
        |> Simplifier.add_simps ([typ_info, tag] @
          @{thms field_update_typ_combinators_simps aggregate_typ_combinators_simps comp_apply}))
      (Thm.cterm_of ctxt component) |> Drule.export_without_context;
    val bs = Thm.cterm_of ctxt (Free ("bs", @{typ "byte list"}));
    val v = Thm.cterm_of ctxt (Free ("v", T));
    val w = Thm.cterm_of ctxt (Free ("w", T));
    val entire_update =
      (Drule.infer_instantiate ctxt [(("bs",0),bs), (("v",0),v), (("w",0),w)] @{thm eq_comp}
        OF [Drule.infer_instantiate ctxt [(("bs",0),bs), (("v",0),v)] component_eq,
            Drule.infer_instantiate ctxt [(("bs",0),bs), (("v",0),w)] component_eq])
      |> Drule.export_without_context;

    val size_simps = @{thms size_td_simps typ_info_array array_tag_def align_td_array_tag};
    fun deep_simp context = SUBGOAL (fn (goal, i) =>
      SOLVED' (asm_full_simp_tac (context |> Config.map Simplifier.simp_depth_limit
        (K (strip_comb_depth_of_term goal + 20)))) i);
    val memory = Goal.prove ctxt [] [] (Logic.mk_of_class (T, @{class xmem_contained_type}))
      (fn {context, ...} =>
        resolve_tac context @{thms tuned_xmem_contained_type_class_intro} 1 THEN
        force_tac (ss context addIs @{thms wf_desc_final_pad wf_desc_ti_typ_pad_combine}) 1 THEN
        force_tac (ss context addIs @{thms wf_size_desc_ti_typ_pad_combine wf_size_desc_final_pad}) 1 THEN
        asm_full_simp_tac (ss context) 1 THEN
        simp_tac (ss context) 1 THEN
        deep_simp (context |> Simplifier.add_simps
          @{thms align_td_array_tag typ_info_array array_tag_def wf_align_field_simps}) 1 THEN
        simp_tac (ss context) 1 THEN
        deep_simp (context |> Simplifier.add_simps (size_simps @
          @{thms addr_card align_of_def size_of_def align_of_final_pad})) 1 THEN
        resolve_tac context [entire_update] 1 THEN
        simp_tac (ss context) 1 THEN UMM_Tools.wf_component_descs_tac context [] THEN
        simp_tac (ss context) 1 THEN UMM_Tools.component_descs_independent_tac context [] THEN
        simp_tac (ss context) 1 THEN UMM_Tools.wf_field_descs_tac context [] THEN
        simp_tac (ss context) 1 THEN UMM_Tools.contained_field_descs_tac context [] THEN
        asm_full_simp_tac (ss context |> Simplifier.add_simps
          @{thms wf_padding_combinator_simps wf_padding}
          |> Config.map Simplifier.simp_depth_limit (K (field_count + 20))) 1);
    val stack = Goal.prove ctxt [] [] (Logic.mk_of_class (T, @{class stack_type}))
      (fn {context, ...} =>
        Class.intro_classes_tac context [] THEN
        simp_tac (ss context) 1 THEN
        deep_simp (context |> Simplifier.add_simps
          @{thms stack_typ_info_intros stack_byte_name_def}) 1);
    fun clean th =
      if Thm_Deps.has_skip_proof [th] orelse not (null (Thm_Deps.all_oracles [th]))
        orelse not (null (Thm.hyps_of th))
      then error ("uncertified HOL memory instance: " ^ recname) else ();
    val _ = List.app clean [memory, stack];
    val _ = writeln ("HOL_RECORD_MEMORY " ^ recname ^ " clean");
  in thy |> Axclass.add_arity memory |> Axclass.add_arity stack end;

end;
