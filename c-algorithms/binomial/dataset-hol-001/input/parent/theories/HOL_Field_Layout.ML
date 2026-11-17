(* Pure HOL layout facts, adapted from the pinned AFP UMM_Proofs.ML.
   Copyright 2020 Data61, CSIRO; Copyright 2022 Apple Inc.
   SPDX-License-Identifier: BSD-2-Clause
   Input: already defined HOL record and type tag. No C parser or C environment. *)
structure HOL_Field_Layout =
struct

fun certify recname lthy =
  let
    val thy = Proof_Context.theory_of lthy;
    val full_name = Sign.intern_type thy recname;
    val T = Type (full_name, []);
    val {fields, ...} = the (Symtab.lookup (RecursiveRecordPackage.get_info thy) full_name);
    val fs = map (apfst Long_Name.base_name) fields;
    fun progress stage = writeln ("HOL_FIELD_STAGE " ^ recname ^ " " ^ stage);
    val typ_info = Proof_Context.get_thm lthy ("typ_info_t_" ^ recname ^ "_def");
    val tag = Proof_Context.get_thm lthy (recname ^ "_tag_def");
    fun clean th =
      (Thm.consolidate [th];
       if Thm_Deps.has_skip_proof [th] orelse not (null (Thm_Deps.all_oracles [th]))
         orelse not (null (Thm.hyps_of th))
       then error ("uncertified HOL layout fact: " ^ recname) else ());
    fun note suffix atts thms context =
      (List.app clean thms;
       snd (Local_Theory.note ((Binding.name (recname ^ suffix), atts), thms) context));

    val lookup_context = lthy
      |> fold Simplifier.del_proc [UMM_Proofs.field_lookup_simproc,
           UMM_Proofs.type_calculations_simproc, UMM_Proofs.typuinfo_calculations_simproc]
      |> Simplifier.add_simps
      ([typ_info, tag] @ @{thms fl_simps size_td_simps max_def});

    fun field_lookup (field, _) =
      let
        val _ = progress ("lookup=" ^ field);
        val lhs = UMM_TermsTypes.mk_field_lookup_nofs (T, field);
        val result = Simplifier.asm_full_rewrite lookup_context (Thm.cterm_of lthy lhs)
          |> Drule.export_without_context;
        val _ = (case Thm.term_of (Thm.rhs_of result) of
            Const ("Option.option.Some", _) $ _ => ()
          | _ => error ("field lookup did not compute to Some: " ^ recname ^ "." ^ field));
      in result end;
    val lookups = map field_lookup fs;
    val lthy = fold (fn ((field, _), th) => note ("_" ^ field ^ "_fl_Some")
      @{attributes [fl_Some_simps]} [th]) (fs ~~ lookups) lthy;
    val offsets = map (fn th => @{thm field_lookup_offset_eq} OF
      [@{thm meta_eq_to_obj_eq} OF [th]]) lookups;
    val lthy = note "_field_offsets" @{attributes [simp]} offsets lthy;
    val field_tis = map (fn th => @{thm field_lookup_field_ti} OF [th]) lookups;
    val lthy = fold (fn ((field, _), th) => note ("_" ^ field ^ "_fl_ti")
      @{attributes [fl_ti_simps, simp]} [th]) (fs ~~ field_tis) lthy;
    fun fg (field, fieldT) = Goal.prove lthy [] []
      (UMM_TermsTypes.mk_fg_cons_tm T fieldT field lthy |> HOLogic.mk_Trueprop)
      (fn {context, ...} => asm_full_simp_tac
        (context |> Simplifier.add_simps @{thms fg_cons_def comp_def}) 1);
    val fgs = map fg fs;
    val lthy = note "_fg_cons" @{attributes [fg_cons_simps, simp]} fgs lthy;

    fun read (field, fieldT) = Goal.prove lthy ["h", "p"] []
      (UMM_TermsTypes.mk_h_val_field_eq lthy T fieldT "h" "p" field)
      (fn {context, ...} => asm_full_simp_tac
        (context |> Simplifier.add_simps (field_tis @ @{thms h_val_field_from_bytes'})) 1);
    val reads = map read fs;
    val lthy = note "_h_val_fields" @{attributes [h_val_fields]} reads lthy;
    val root_conversions = lookups |> map (fn th =>
      @{thm heap_update_field_root_conv''} OF [@{thm meta_eq_to_obj_eq} OF [th]])
      |> map (Simplifier.simplify lthy);
    fun write (field, fieldT) = Goal.prove lthy ["h", "p", "v"] []
      (UMM_TermsTypes.mk_heap_update_field_eq lthy T fieldT "h" "p" "v" field)
      (fn {context, ...} => asm_full_simp_tac
        (context |> Simplifier.add_simps (root_conversions @ @{thms h_val_field_from_bytes'})) 1);
    val writes = map write fs;
    val lthy = note "_heap_update_fields" @{attributes [heap_update_fields]} writes lthy;
    val unfold = Goal.prove lthy ["h", "p"] []
      (UMM_TermsTypes.mk_h_val_unfold lthy T "h" "p" fs)
      (fn {context, ...} => asm_full_simp_tac
        (context |> Simplifier.add_simps reads) 1);
    val lthy = note "_h_val_unfold" @{attributes [h_val_unfold]} [unfold] lthy;
    fun zero th = (@{thm field_lookup_zero'} OF [th])
      |> Conv.fconv_rule (Simplifier.asm_full_rewrite lthy)
      |> (fn result => result OF @{thms refl})
      |> Conv.fconv_rule (Simplifier.asm_full_rewrite lthy);
    val zeros = map zero lookups;
    val lthy = note "_zero_simps" @{attributes [zero_simps]} zeros lthy;
    val _ = writeln ("HOL_FIELD_LAYOUT " ^ recname ^ " fields=" ^
      string_of_int (length fs) ^ " clean");
  in lthy end;

end;
