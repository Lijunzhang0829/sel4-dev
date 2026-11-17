(* Trusted preparation: retain environment assumptions, never assert that the
   uninterpreted deployment environment holds. No target proof is exported. *)
local
  val thy = Build.get_theory "Calg_Binomial_Foundation.Binomial_Heap_C";
  val ctxt = Proof_Context.init_global thy |> Config.put show_markup false;
  fun plain s = XML.content_of (YXML.parse_body s);
  fun term t = plain (Print_Mode.setmp [] (Syntax.string_of_term ctxt) t);
  fun typ T = plain (Print_Mode.setmp [] (Syntax.string_of_typ ctxt) T);
  fun quote s = "\"" ^ String.translate (fn #"\"" => "\\\"" | c => str c) s ^ "\"";
  val constants = #constants (Consts.dest (Sign.consts_of thy));
  val addresses = constants |> map_filter (fn (name, (T, _)) =>
    if String.isPrefix "Binomial_Heap_C.binomial_heap_input." name
      andalso T = @{typ "unit ptr"} then SOME (Long_Name.base_name name) else NONE);
  val declarations = "setup \<open>fold (fn n =>\n" ^
    "  Theory.specify_const ((Binding.qualify true \"binomial_heap_input\" (Binding.name n),\n" ^
    "    \<^typ>\<open>unit ptr\<close>), NoSyn) #> snd)\n  " ^
    ML_Syntax.print_list ML_Syntax.print_string addresses ^ "\<close>\n\n";
  val SOME {fields, ...} = Record.get_info thy "Binomial_Heap_C.globals";
  val record = "record globals =\n  " ^ space_implode "\n  "
    (map (fn (name, T) => Long_Name.base_name name ^ " :: " ^ quote (typ T)) fields) ^ "\n\n";
  val specifications = Locale.hyp_spec_of thy "Binomial_Heap_C.binomial_heap_input_global_addresses";
  val groups = maps (fn Element.Assumes xs => xs | _ => error "unexpected deployment locale element") specifications;
  fun group ((binding, attributes), propositions) =
    plain (Pretty.string_of (Pretty.block
      (Attrib.pretty_binding ctxt (binding, attributes) ":"))) ^ " " ^ space_implode "\n    "
      (map (quote o term o fst) propositions);
  val locale_source = "locale binomial_heap_input_global_addresses =\n  assumes " ^
    space_implode "\n  and " (map group groups) ^ "\nbegin\nend\n\n";
  val _ = File.write (Path.explode "/export/HOL_Globals_Record.thy.inc") record;
  val _ = File.write (Path.explode "/export/HOL_Deployment_Context.thy.inc") (declarations ^ locale_source);
  val rec_eq = Global_Theory.get_thm thy "binomial_tree_unref'.simps";
  val typed = ctxt |> Config.put show_types true |> Config.put show_sorts true |> Config.put show_consts true;
  val proposition = (case try Logic.dest_equals (Thm.prop_of rec_eq) of
      SOME pair => HOLogic.mk_Trueprop (HOLogic.mk_eq pair)
    | NONE => Thm.prop_of rec_eq);
  val ([proposition], typed) = Variable.import_terms true [proposition] typed;
  val equation = plain (Print_Mode.setmp [] (Syntax.string_of_term typed) proposition);
  val _ = File.write (Path.explode "/export/HOL_Unref_Fixed_Point.thy.inc")
    ("fixed_point (spec_monad_gfp) binomial_tree_unref' ::\n  " ^
     quote (typ (Sign.the_const_type thy "Binomial_Heap_C.binomial_tree_unref'")) ^
     "\nwhere\n  " ^ quote equation ^ "\n  by simp_all\n\n");
  (* Reconstruct the original function equation, rather than introducing an
     argument pattern or relying on the separately polished .simps theorem. *)
  val functional = Thm.axiom thy "Binomial_Heap_C.binomial_tree_unref'_functional_def"
    |> Thm.prop_of |> Logic.dest_equals |> snd;
  val Abs (_, function_type, functional_body) = functional;
  fun extract (Abs (_, T, Const ("HOL.eq", _) $ Bound 0 $ body)) bodies =
        if T = function_type andalso not (Term.loose_bvar1 (body, 0))
        then body :: bodies else extract body bodies
    | extract (Abs (_, _, body)) bodies = extract body bodies
    | extract (f $ x) bodies = extract x (extract f bodies)
    | extract _ bodies = bodies;
  val [body] = extract functional_body [];
  val body = Term.subst_bound (Free ("unref_replay", function_type), Term.incr_boundvars ~1 body);
  val body = plain (Print_Mode.setmp [] (Syntax.string_of_term typed) body);
  val _ = File.write (Path.explode "/export/HOL_Unref_Raw_Fixed_Point.thy.inc")
    ("fixed_point (spec_monad_gfp) unref_replay ::\n  " ^ quote (typ function_type) ^
      "\nwhere\n  " ^ quote ("unref_replay = (" ^ body ^ ")") ^ "\n  by simp_all\n\n");
  val _ = writeln ("HOL_CONTEXT_SOURCE_EXPORTED addresses=" ^ string_of_int (length addresses) ^
    " assumption_groups=" ^ string_of_int (length groups));
in end;
