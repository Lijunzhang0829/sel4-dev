(* Trusted preparation: export already proved, public code equations as closed HOL
   definitions. The private clone checks every exported body against the original
   function in the original native theory. No benchmark proof is exported. *)
local
  val thy = Build.get_theory "Calg_Binomial_Foundation.Binomial_Heap_C";
  val ctxt = Proof_Context.init_global thy |> Config.put show_markup false
    |> Config.put show_types true |> Config.put show_sorts true
    |> Config.put show_consts true;
  val prefix = "Binomial_Heap_C.";
  val roots = ["binomial_heap_num_entries'", "binomial_tree_ref'",
    "binomial_tree_merge'", "binomial_heap_merge_undo'", "binomial_heap_merge'",
    "binomial_heap_cmp'", "binomial_heap_insert'", "binomial_heap_pop'",
    "binomial_heap_free'", "binomial_heap_new'", "calg_binomial_new'",
    "malloc'", "calloc'", "free'", "memcpy'", "int_compare'"];
  val pointer_definitions = Theory.all_axioms_of thy |> map_filter (fn (n, prop) =>
    case try Logic.dest_equals prop of
      SOME (Const (c, _), rhs) =>
        if String.isSubstring "\<P>" c then SOME (c, (n, rhs)) else NONE
    | _ => NONE) |> Symtab.make;
  fun expand_pointers (t as Const (n, _)) =
        (case Symtab.lookup pointer_definitions n of
          SOME (_, body) => expand_pointers body | NONE => t)
    | expand_pointers (f $ x) = expand_pointers f $ expand_pointers x
    | expand_pointers (Abs (n, T, body)) = Abs (n, T, expand_pointers body)
    | expand_pointers t = t;
  fun equation name =
    let
      val th = Global_Theory.get_thm thy (prefix ^ name ^ "_def");
      val _ = Thm.consolidate [th];
      val _ = if Thm_Deps.has_skip_proof [th] orelse
          not (null (Thm_Deps.all_oracles [th])) orelse not (null (Thm.hyps_of th))
        then error ("uncertified public equation: " ^ name) else ();
      val prop = Thm.prop_of th;
      val (lhs, rhs) = (case try Logic.dest_equals prop of
          SOME p => p | NONE => HOLogic.dest_eq (HOLogic.dest_Trueprop prop));
      val (head, args) = strip_comb lhs;
      val _ = if head = Const (prefix ^ name, Sign.the_const_type thy (prefix ^ name))
        then () else error ("unexpected public equation head: " ^ name);
      val closed = fold_rev Term.lambda args rhs |> expand_pointers;
      val _ = if null (Term.add_frees closed []) andalso null (Term.add_vars closed [])
        then () else error ("unclosed public equation: " ^ name);
    in (Sign.the_const_type thy (prefix ^ name), closed, length args) end;
  val equations = Symtab.make (map (fn n => (n, equation n)) roots);
  val visiting = Unsynchronized.ref Symtab.empty;
  val order = Unsynchronized.ref ([]: string list);
  fun visit n = (case Symtab.lookup (!visiting) n of
      SOME true => ()
    | SOME false => error ("recursive public equation: " ^ n)
    | NONE => let
        val (_, body, _) = the (Symtab.lookup equations n);
        val deps = Term.add_const_names body [] |> map_filter (try (unprefix prefix))
          |> filter (Symtab.defined equations);
        val _ = Unsynchronized.change visiting (Symtab.update (n, false));
        val _ = List.app visit deps;
        val _ = Unsynchronized.change visiting (Symtab.update (n, true));
      in Unsynchronized.change order (cons n) end);
  val _ = List.app visit roots;
  val names = rev (!order);
  fun plain s = XML.content_of (YXML.parse_body s);
  fun pretty_typ T = plain (Print_Mode.setmp [] (Syntax.string_of_typ ctxt) T);
  fun pretty_term t = plain (Print_Mode.setmp [] (Syntax.string_of_term ctxt) t);
  fun quote s = "\"" ^ String.translate (fn #"\"" => "\\\"" | c => str c) s ^ "\"";
  fun rename (Const (n, T)) =
        (case try (unprefix prefix) n of
          SOME b => if member (op =) names b then Free ("public_" ^ b, T) else Const (n, T)
        | NONE => Const (n, T))
    | rename t = t;
  fun declaration changed n =
    let
      val (T, body, _) = the (Symtab.lookup equations n);
      val fresh = if changed then "public_" ^ n else n;
      val body = if changed then Term.map_aterms rename body else body;
    in "definition " ^ fresh ^ " :: " ^ quote (pretty_typ T) ^ " where\n  " ^
      quote (fresh ^ " \<equiv> " ^ pretty_term body) ^ "\n\n" end;
  val _ = File.write (Path.explode "/export/HOL_Public_Definitions.thy.inc")
    (String.concat (map (declaration false) names));
  fun item n = let val (_, _, arity) = the (Symtab.lookup equations n)
    in "(" ^ quote n ^ ", " ^ string_of_int arity ^ ")" end;
  val probe = "theory HOL_Public_Bridge imports Calg_Binomial_Foundation.Binomial_Heap_C begin\n" ^
    String.concat (map (declaration true) names) ^
    "ML \<open>\nval thy = \<^theory>; val ctxt = Proof_Context.init_global thy;\n" ^
    "val entries = [" ^ commas (map item names) ^ "];\n" ^
    "val proved = Unsynchronized.ref ([]: thm list);\n" ^
    "fun check (n, arity) = let\n" ^
    " val old = \"Binomial_Heap_C.\" ^ n; val fresh = \"HOL_Public_Bridge.public_\" ^ n;\n" ^
    " val T = Sign.the_const_type thy old;\n" ^
    " val prop = HOLogic.mk_Trueprop (HOLogic.mk_eq (Const(fresh,T),Const(old,T)));\n" ^
    " val rules = Global_Theory.get_thm thy (fresh ^ \"_def\") ::\n" ^
    "   Global_Theory.get_thm thy (old ^ \"_def\") :: !proved @ map (Thm.axiom thy) [" ^
    commas (map (quote o fst o snd) (Symtab.dest pointer_definitions)) ^ "];\n" ^
    " val th = Goal.prove ctxt [] [] prop (fn {context,...} =>\n" ^
    "   REPEAT_DETERM_N arity (resolve_tac context [@{thm ext}] 1) THEN\n" ^
    "   simp_tac (put_simpset HOL_basic_ss context |> Simplifier.add_simps rules) 1);\n" ^
    " val _ = Thm.consolidate [th];\n" ^
    " val _ = if Thm_Deps.has_skip_proof [th] orelse not (null(Thm_Deps.all_oracles [th]))\n" ^
    "   orelse not (null(Thm.hyps_of th)) then error (\"uncertified public bridge: \" ^ n) else ();\n" ^
    " val _ = Unsynchronized.change proved (cons th);\n" ^
    " in writeln (\"HOL_PUBLIC_BRIDGE \" ^ n ^ \" clean\") end;\n" ^
    "val _ = List.app check entries;\n\<close>\nend\n";
  val _ = File.write (Path.explode "/export/HOL_Public_Bridge.thy") probe;
  val _ = writeln ("HOL_PUBLIC_SOURCE_EXPORTED count=" ^ string_of_int (length names));
in end;
