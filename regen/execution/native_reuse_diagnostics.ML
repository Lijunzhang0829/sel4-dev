(* Read-only observations after a failed explicit rule command.  This module
   never replaces a proof state, evaluates fact attributes, or certifies a fact. *)
structure Regen_Reuse_Diagnostics =
struct
  fun element name text = XML.Elem ((name, []), [XML.Text text]);
  fun envelope status body = XML.Elem (("reuse_diagnostics",
    [("schema", "explicit-rule-observation-v1"), ("status", status),
     ("semantic_scope", "current_goal_and_context_only"), ("method", "rule")]), body);
  fun skipped status reason = envelope status [element "reason" reason];

  (* Tokenize only after failure. Match the native transition's offset, rather
     than guessing a fact from an error string or a neighbouring source line. *)
  fun command_tokens theory position text tr =
    let
      val tokens = Token.explode (Thy_Header.get_keywords theory) position text;
      val wanted = Position.offset_of (Toplevel.pos_of tr);
      fun starts tok = Token.is_command tok andalso
        Position.offset_of (Token.pos_of tok) = wanted;
    in case wanted of NONE => [] | SOME _ =>
      (case drop_prefix (not o starts) tokens of [] => [] | first :: rest =>
        filter Token.is_proper (first :: take_prefix (not o Token.is_command) rest))
    end;

  fun explicit_rule tokens =
    let
      val body = (case tokens of [] => [] | _ :: rest => rest);
      val method = (case body of first :: rest =>
        if Token.content_of first = "(" andalso not (null rest)
          andalso Token.content_of (List.last rest) = ")"
        then take (length rest - 1) rest else body | [] => []);
    in case method of first :: rest =>
      if Token.content_of first <> "rule" then NONE
      else SOME (Scan.read Token.stopper (Parse.thm --| Scan.ahead Parse.eof) rest)
    | [] => NONE end;

  fun payload context name proposition =
    let
      val typed = Config.put show_sorts true (Config.put show_types true context);
      fun plain pretty = YXML.content_of pretty;
      fun sort_body sort = map (element "class") sort;
      val free_types = map (fn (name, sort) => XML.Elem (("type_variable",
        [("kind", "free"), ("name", name)]), sort_body sort))
        (Term.add_tfrees proposition []);
      val schematic_types = map (fn ((name, index), sort) => XML.Elem (("type_variable",
        [("kind", "schematic"), ("name", name), ("index", Int.toString index)]), sort_body sort))
        (Term.add_tvars proposition []);
      val free_terms = map (fn (name, typ) => XML.Elem (("variable", [("name", name)]),
        [element "type" (plain (Syntax.string_of_typ typed typ))]))
        (Term.add_frees proposition []);
      val schematic_terms = map (fn ((name, index), typ) => XML.Elem (("variable",
        [("name", name), ("index", Int.toString index)]),
        [element "type" (plain (Syntax.string_of_typ typed typ))]))
        (Term.add_vars proposition []);
    in XML.Elem ((name, []),
      [element "proposition" (plain (Syntax.string_of_term typed proposition)),
       XML.Elem (("type_variables", []), free_types @ schematic_types),
       XML.Elem (("variables", []), free_terms @ schematic_terms)]) end;

  fun snapshot position text tr st =
    if not (member (op =) ["proof", "apply", "by"] (Toplevel.name_of tr))
      orelse not (Toplevel.is_proof st) then NONE
    else
      let
        val tokens = command_tokens (Toplevel.theory_of st) position text tr;
      in case explicit_rule tokens of NONE => NONE
       | SOME NONE => SOME (skipped "unsupported" "Only a single named rule reference is observed; composite methods are not re-executed")
       | SOME (SOME (_, _ :: _)) => SOME (skipped "unsupported" "Fact attributes are not re-evaluated by failure diagnostics")
       | SOME (SOME (Facts.Fact _, _)) => SOME (skipped "unsupported" "Literal facts are outside named-rule observation")
       | SOME (SOME (reference, [])) =>
          let
            val {context, goal, facts} = Proof.goal (Toplevel.proof_of st);
            val rules = Proof_Context.get_fact context reference;
            val premises = Thm.prems_of goal;
          in if null premises then SOME (skipped "unavailable" "No current subgoal")
           else if null rules orelse length rules > 16 then
             SOME (skipped "unsupported" "Named rule collection is empty or exceeds 16 facts")
           else
             let
               val matched = Seq.pull (Method.rule_tac context rules facts 1 goal);
               val matching = XML.Elem (("matching",
                 [("kind", "Method.rule_tac"),
                  ("status", if is_some matched then "matched" else "no_match")]),
                 (case matched of NONE => [] | SOME (next, _) =>
                   [element "remaining_subgoals" (Int.toString (Thm.nprems_of next))]));
             in SOME (envelope "observed"
               [element "reference" (Facts.string_of_ref reference),
                payload context "goal" (hd premises),
                XML.Elem (("facts", []), map (payload context "fact" o Thm.prop_of) rules),
                XML.Elem (("chained_facts", []), map (payload context "fact" o Thm.prop_of) facts),
                matching]) end
          end
      end;
end;
