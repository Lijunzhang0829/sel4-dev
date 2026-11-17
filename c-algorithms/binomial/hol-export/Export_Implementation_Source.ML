(* Trusted preparation. Reify existing conservative HOL definitions as source.
   No C frontend, new axiom or benchmark target proof is introduced here. *)
local
  val thy = Build.get_theory "Calg_Binomial_Foundation.Binomial_Heap_C";
  val ctxt = Proof_Context.init_global thy |> Config.put show_markup false
    |> Config.put show_types true |> Config.put show_sorts true
    |> Config.put show_consts true;
  val prefix = "Binomial_Heap_C.";
  val roots = ["binomial_heap_num_entries'", "binomial_tree_ref'", "binomial_tree_unref'",
    "binomial_tree_merge'", "binomial_heap_merge_undo'", "binomial_heap_merge'", "binomial_heap_cmp'",
    "binomial_heap_insert'", "binomial_heap_pop'", "binomial_heap_free'", "binomial_heap_new'",
    "calg_binomial_new'", "malloc'", "calloc'", "free'", "memcpy'", "int_compare'"];
  val allowed = Symtab.make_set (maps (fn n => map (fn x => prefix ^ x)
    [n, n ^ "_functional", "fixp_" ^ n]) roots);
  fun plain s = XML.content_of (YXML.parse_body s);
  fun pretty_typ T = plain (Print_Mode.setmp [] (Syntax.string_of_typ ctxt) T);
  fun pretty_term t = plain (Print_Mode.setmp [] (Syntax.string_of_term ctxt) t);
  fun definition (axiom_name, proposition) =
    (case try Logic.dest_equals proposition of
      SOME (Const (name, T), rhs) => SOME (name, (axiom_name, T, rhs))
    | _ => NONE);
  val definitions = Symtab.make (map_filter definition (Theory.all_axioms_of thy)
    |> filter (fn (name, _) => Symtab.defined allowed name));
  val visiting = Unsynchronized.ref Symtab.empty;
  val order = Unsynchronized.ref ([]: string list);
  fun visit name =
    (case Symtab.lookup (!visiting) name of
      SOME true => ()
    | SOME false => error ("recursive primitive definition without fixed-point combinator: " ^ name)
    | NONE =>
        let
          val (_, _, rhs) = the (Symtab.lookup definitions name)
            handle Option.Option => error ("no conservative primitive definition for " ^ name);
          val _ = if null (Term.add_vars rhs []) andalso null (Term.add_frees rhs []) then ()
            else error ("unclosed primitive definition " ^ name);
          val _ = Unsynchronized.change visiting (Symtab.update (name, false));
          val deps = Term.add_const_names rhs [] |> filter (Symtab.defined allowed);
          val _ = List.app visit deps;
          val _ = Unsynchronized.change visiting (Symtab.update (name, true));
          val _ = Unsynchronized.change order (cons name);
        in () end);
  val _ = List.app (fn name => visit (prefix ^ name)) roots;
  val names = rev (!order);
  fun new_name n = "hol_" ^ Long_Name.base_name n;
  fun rename (Const (n, T)) =
        if member (op =) names n then Free (new_name n, T) else Const (n, T)
    | rename t = t;
  fun outer_quote s = "\"" ^ String.translate (fn #"\"" => "\\\"" | c => str c) s ^ "\"";
  fun declaration changed name =
    let
      val (_, T, rhs) = the (Symtab.lookup definitions name);
      val n = if changed then new_name name else Long_Name.base_name name;
      val body = if changed then Term.map_aterms rename rhs else rhs;
    in "definition " ^ n ^ " :: " ^ outer_quote (pretty_typ T) ^ " where\n  " ^
      outer_quote (n ^ " \<equiv> " ^ pretty_term body) ^ "\n\n" end;
  val model_source = "(* Generated from exact kernel definitions; types/support pending independent export. *)\n" ^
    "theory Binomial_Heap_C imports HOL_C_Types begin\n\n" ^
    String.concat (map (declaration false) names) ^ "end\n";
  val probe_source = "(* Private equivalence probe; this import is not a final generation input. *)\n" ^
    "theory HOL_Program_Model imports Calg_Binomial_Foundation.Binomial_Heap_C begin\n\n" ^
    String.concat (map (declaration true) names) ^ "end\n";
  val _ = File.write (Path.explode "/export/Binomial_Heap_C.thy") model_source;
  val _ = File.write (Path.explode "/export/HOL_Program_Model.thy") probe_source;
  fun item name =
    let val (a, T, _) = the (Symtab.lookup definitions name)
    in "(" ^ commas (map ML_Syntax.print_string
      [name, "HOL_Program_Model." ^ new_name name, a,
       "HOL_Program_Model." ^ new_name name ^ "_def"]) ^ ")" end;
  val bridge =
    "theory HOL_Implementation_Bridge imports HOL_Program_Model begin\nML \<open>\n" ^
    "val thy = \<^theory>; val ctxt = Proof_Context.init_global thy;\n" ^
    "val entries = [" ^ commas (map item names) ^ "];\n" ^
    "val proved = Unsynchronized.ref ([]: thm list);\n" ^
    "fun check (old, fresh, axiom, equation) = let\n" ^
    " val T = Sign.the_const_type thy old;\n" ^
    " val proposition = HOLogic.mk_Trueprop (HOLogic.mk_eq (Const(fresh,T), Const(old,T)));\n" ^
    " val rules = Global_Theory.get_thm thy equation :: Thm.axiom thy axiom :: !proved;\n" ^
    " val th = Goal.prove ctxt [] [] proposition (fn {context,...} =>\n" ^
    "   simp_tac (put_simpset HOL_basic_ss context |> Simplifier.add_simps rules) 1);\n" ^
    " val _ = if Thm_Deps.has_skip_proof [th] orelse not (null (Thm_Deps.all_oracles [th]))\n" ^
    "   orelse not (null (Thm.hyps_of th)) then error (\"uncertified model bridge: \" ^ old) else ();\n" ^
    " val _ = Unsynchronized.change proved (cons th);\n" ^
    " val _ = writeln (\"HOL_DEFINITION_BRIDGE \" ^ old ^ \" clean\");\n" ^
    " in () end;\nval _ = List.app check entries;\n\<close>\nend\n";
  val _ = File.write (Path.explode "/export/HOL_Implementation_Bridge.thy") bridge;
  val _ = writeln ("HOL_IMPLEMENTATION_SOURCE_EXPORTED roots=" ^ string_of_int (length roots) ^
    " primitive_definitions=" ^ string_of_int (length names));
in end;
