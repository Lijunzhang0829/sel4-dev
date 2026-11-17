(* Trusted preparation: export the complete program signature, not target proofs. *)
local
  val thy = Build.get_theory "Calg_Binomial_Foundation.Binomial_Heap_C";
  val base = Build.get_theory "AutoCorres2.AutoCorres";
  val ctxt = Proof_Context.init_global thy
    |> Config.put show_markup false;
  fun elem name attrs body = XML.Elem ((name, attrs), body);
  fun text name value = elem name [] [XML.Text value];
  val digits = "0123456789abcdef";
  fun hex s = String.translate (fn c => let val n = Char.ord c in
    String.implode [String.sub (digits, n div 16), String.sub (digits, n mod 16)] end) s;
  fun raw_typ T = hex (YXML.string_of_body (Term_XML.Encode.typ T));
  fun raw_term t = hex (YXML.string_of_body (Term_XML.Encode.term_raw t));
  fun plain s = XML.content_of (YXML.parse_body s);
  fun typ_node name T = elem "typed_name" [("name", name), ("typ_yxml", raw_typ T)]
    [XML.Text (plain (Print_Mode.setmp [] (Syntax.string_of_typ ctxt) T))];
  fun term_node name t = elem "proposition" [("name", name), ("term_yxml", raw_term t)]
    [XML.Text (plain (Print_Mode.setmp [] (Syntax.string_of_term ctxt) t))];
  fun types_of t = Name_Space.dest_table (#types (Type.rep_tsig (Sign.tsig_of t)));
  val old_types = map fst (types_of base);
  val new_types = filter_out (member (op =) old_types o fst) (types_of thy);
  fun type_node (name, decl) =
    (case decl of
      Type.Logical_Type n => elem "logical_type" [("name", name), ("arity", string_of_int n)] []
    | Type.Abbreviation (args, T, _) => elem "type_abbreviation" [("name", name)]
        (map (text "argument") args @ [typ_node "rhs" T])
    | Type.Nonterminal => elem "nonterminal" [("name", name)] []);
  val old_consts = map fst (#constants (Consts.dest (Sign.consts_of base)));
  val new_consts = #constants (Consts.dest (Sign.consts_of thy))
    |> filter_out (member (op =) old_consts o fst);
  fun const_node (name, (T, rhs)) = elem "constant" [("name", name)]
    ([typ_node "type" T] @ map (term_node "abbreviation") (the_list rhs));
  val old_axioms = map fst (Theory.all_axioms_of base);
  val new_axioms = Theory.all_axioms_of thy
    |> filter_out (member (op =) old_axioms o fst);
  val old_locales = Locale.get_locales base;
  val new_locales = Locale.get_locales thy |> filter_out (member (op =) old_locales);
  fun locale_node name =
    let
      val (assumption, definitions) = Locale.specification_of thy name;
      val params = Locale.params_of thy name;
    in elem "locale" [("name", name)]
      (map (fn ((n, T), _) => typ_node n T) params @
       map (term_node "assumption") (the_list assumption) @
       map (term_node "definition") definitions)
    end;
  val old_records = map fst (Symtab.dest (RecursiveRecordPackage.get_info base));
  val new_records = Symtab.dest (RecursiveRecordPackage.get_info thy)
    |> filter_out (member (op =) old_records o fst);
  fun record_node (name, {constructor, fields, updates}) = elem "recursive_record" [("name", name)]
    [elem "constructor" [] [typ_node (fst constructor) (snd constructor)],
     elem "fields" [] (map (fn (n, T) => typ_node n T) fields),
     elem "updates" [] (map (fn (n, T) => typ_node n T) updates)];
  val final_names = ["binomial_heap_num_entries'", "binomial_tree_ref'", "binomial_tree_unref'",
    "binomial_tree_merge'", "binomial_heap_merge_undo'", "binomial_heap_merge'",
    "binomial_heap_cmp'", "binomial_heap_insert'", "binomial_heap_pop'", "binomial_heap_free'",
    "binomial_heap_new'", "calg_binomial_new'", "malloc'", "calloc'", "free'", "memcpy'", "int_compare'"];
  val facts = Facts.dest_static true [] (Global_Theory.facts_of thy);
  val final_facts = filter (fn (name, _) =>
    exists (fn n => Long_Name.base_name name = n ^ "_def") final_names) facts;
  fun fact_node (name, thms) = elem "final_definition" [("name", name)]
    (map_index (fn (i, th) => term_node (string_of_int i) (Thm.full_prop_of th)) thms);
  val out = TextIO.openOut "/export/model-inventory.xml";
  fun write s = TextIO.output (out, s);
  fun section name render items =
    (write ("<" ^ name ^ ">\n");
     List.app (fn x => write (XML.string_of (render x) ^ "\n")) items;
     write ("</" ^ name ^ ">\n"));
  val _ = write "<program_hol_inventory purpose=\"trusted model extraction; no target proof input\">\n";
  val _ = write (XML.string_of (text "source_theory" (Context.theory_name {long = true} thy)) ^ "\n");
  val _ = writeln ("MODEL_INVENTORY_COUNTS types=" ^ string_of_int (length new_types) ^
    " constants=" ^ string_of_int (length new_consts) ^ " axioms=" ^ string_of_int (length new_axioms));
  val _ = section "types" type_node new_types;
  val _ = section "constants" const_node new_consts;
  val _ = section "definition_axioms_for_inventory_only" (fn (n, t) => term_node n t) new_axioms;
  val _ = section "locales" locale_node new_locales;
  val _ = section "recursive_records" record_node new_records;
  val _ = section "final_definitions" fact_node final_facts;
  val _ = write "</program_hol_inventory>\n";
  val _ = TextIO.closeOut out;
  val _ = writeln ("MODEL_HOL_INVENTORY types=" ^ string_of_int (length new_types) ^
    " constants=" ^ string_of_int (length new_consts) ^
    " locales=" ^ string_of_int (length new_locales) ^
    " final_definitions=" ^ string_of_int (length final_facts));
in end;
