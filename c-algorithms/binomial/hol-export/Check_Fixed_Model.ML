(* Trusted audit of the source-loaded HOL model; no benchmark target is proved. *)
local
  val thy = \<^theory>;
  val ctxt = Proof_Context.init_global thy;
  val _ = if exists (Sign.declared_const thy)
      ["Binomial_Heap_C.binomial_heap_input.\<Gamma>", "Binomial_Heap_C.l1_binomial_heap_insert'",
       "Binomial_Heap_C.l2_binomial_heap_insert'"]
    then error "old C/SIMPL translation context leaked into the fixed HOL model" else ();
  val roots = ["binomial_heap_num_entries'", "binomial_tree_ref'", "binomial_tree_unref'",
    "binomial_tree_merge'", "binomial_heap_merge_undo'", "binomial_heap_merge'", "binomial_heap_cmp'",
    "binomial_heap_insert'", "binomial_heap_pop'", "binomial_heap_free'", "binomial_heap_new'",
    "calg_binomial_new'", "malloc'", "calloc'", "free'", "memcpy'", "int_compare'"];
  val digits = "0123456789abcdef";
  fun hex s = String.translate (fn c => let val n = Char.ord c in
    String.implode [String.sub (digits, n div 16), String.sub (digits, n mod 16)] end) s;
  fun raw_typ T = hex (YXML.string_of_body (Term_XML.Encode.typ T));
  fun raw_term t = hex (YXML.string_of_body (Term_XML.Encode.term_raw t));
  fun type_node (name, T) = XML.Elem (("constant", [("name", name), ("typ_yxml", raw_typ T)]), []);
  val function_signature = map (fn n => let val name = "Binomial_Heap_C." ^ n
    in type_node (name, Sign.the_const_type thy name) end) roots;
  fun clean name =
    let
      val th = Global_Theory.get_thm thy name;
      val _ = Thm.consolidate [th];
      val _ = if Thm_Deps.has_skip_proof [th] orelse not (null (Thm_Deps.all_oracles [th]))
        orelse not (null (Thm.hyps_of th)) then error ("uncertified fixed-model support: " ^ name) else ();
    in XML.Elem (("support_certificate", [("name", name), ("skip_proof", "false"),
      ("oracles", "0"), ("hypotheses", "0"), ("term_yxml", raw_term (Thm.full_prop_of th))]), []) end;
  val certificates = map clean ["Binomial_Heap_C.unref_replay_eq", "binomial_tree_unref'.simps"];
  val document = XML.Elem (("fixed_hol_model", [("c_translation_invoked", "false"),
      ("program_functions", string_of_int (length roots)), ("benchmark_targets_proved", "0")]),
    function_signature @ certificates);
  val _ = File.write (Path.explode "/export/fixed-model-audit.xml") (XML.string_of document);
  val _ = writeln "FIXED_HOL_MODEL_AUDIT functions=17 support_certificates=2 clean";
in end;
