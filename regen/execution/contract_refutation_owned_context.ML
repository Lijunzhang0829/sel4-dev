(* Versioned owned-context negative obligations.  The statement context is
   separated from its owned environment at Expression.read_statement, before
   either is exported.  No theorem, hypothesis or target fact is installed. *)
local
val baseline_axioms = sort_strings (map #1 (Thm.all_axioms_of \<^theory>));
val captured_target = Unsynchronized.ref
  (NONE: (string * term * XML.tree list) option);

fun hol_prop (Const ("Pure.imp", _) $ a $ b) = HOLogic.mk_imp (hol_prop a, hol_prop b)
  | hol_prop (Const ("Pure.all", _) $ Abs (x, T, b)) =
      HOLogic.all_const T $ Abs (x, T, hol_prop b)
  | hol_prop (Const ("Pure.conjunction", _) $ a $ b) = HOLogic.mk_conj (hol_prop a, hol_prop b)
  | hol_prop (Const ("Pure.eq", _) $ a $ b) = HOLogic.mk_eq (a, b)
  | hol_prop (Const ("HOL.Trueprop", _) $ p) = p
  | hol_prop _ = error "REGEN_REFUTATION_UNSUPPORTED: meta-proposition";

val hex_digits = "0123456789abcdef";
fun hex_char char =
  let val byte = Char.ord char
  in String.implode [String.sub (hex_digits, byte div 16),
                     String.sub (hex_digits, byte mod 16)] end;
fun hex text = String.translate hex_char text;
fun term_xml term = hex (YXML.string_of_body (Term_XML.Encode.term_raw term));
fun field name value = XML.Elem ((name, []), [XML.Text value]);
fun close_frees predicate term =
  fold_rev (fn v => fn t => Logic.all (Free v) t)
    (filter predicate (Term.add_frees term [])) term;
fun conjunction [] = HOLogic.mk_Trueprop \<^term>\<open>True\<close>
  | conjunction xs = Logic.mk_conjunction_list xs;
fun plain text = XML.content_of (YXML.parse_body text);

fun audit id expected scope_fields results lthy =
  let
    val thms = flat results;
    val thm = (case thms of [thm] => thm | _ => error "Refutation audit: expected one theorem");
    val _ = if Thm_Deps.has_skip_proof thms then error "Refutation audit: skip proof" else ();
    val _ = if null (Thm_Deps.all_oracles thms) then () else error "Refutation audit: oracle";
    val _ = if null (Thm.hyps_of thm) then () else error "Refutation audit: local hypotheses";
    val thy = Proof_Context.theory_of lthy;
    val _ = if sort_strings (map #1 (Thm.all_axioms_of thy)) = baseline_axioms
      then () else error "Refutation audit: changed axioms";
    val actual = Thm.full_prop_of thm;
    val _ = if actual aconv expected orelse actual aconv Logic.varify_global expected
      then () else error "Refutation audit: theorem does not match owned-context negation";
    val _ = Thm.consolidate thms;
    val deps = Thm_Deps.thm_deps thy thms;
    val evidence = XML.Elem (("contract_refutation", [("version", "3"), ("contract_id", id)]),
      [field "expected_term" (term_xml expected), field "proved_term" (term_xml actual),
       field "skip_proof" "false", field "oracles" "0", field "hypotheses" "0",
       field "changed_axioms" "false", field "exact_owned_context_negation" "true",
       XML.Elem (("scope_evidence", []), scope_fields),
       XML.Elem (("dependencies", []), map (fn (_, (name, index)) =>
         XML.Elem (("fact", [("name", name), ("index", string_of_int index)]), [])) deps)]);
    val _ = File.write (Path.explode REGEN_AUDIT_PATH) (XML.string_of evidence);
  in lthy end;

val long_keyword = Parse_Spec.includes >> K "" || Parse_Spec.long_statement_keyword;
val long_statement =
  Scan.optional (Parse_Spec.opt_thm_name ":" --| Scan.ahead long_keyword) Binding.empty_atts --
  Scan.optional Parse_Spec.includes [] -- Parse_Spec.long_statement
    >> (fn ((binding, includes), (elems, concl)) => (binding, includes, elems, concl));
val short_statement = Parse_Spec.statement -- Parse_Spec.if_statement -- Parse.for_fixes
  >> (fn ((shows, assumes), fixes) =>
    (Binding.empty_atts, [], [Element.Fixes fixes, Element.Assumes assumes], Element.Shows shows));

fun capture id (_, includes, elems, concl) lthy =
  let
    val _ = if null includes then () else error "REGEN_REFUTATION_UNSUPPORTED: includes";
    val _ = (case concl of Element.Shows _ => ()
      | _ => error "REGEN_REFUTATION_UNSUPPORTED: obtains");
    val outer = Proof_Context.init_global (Proof_Context.theory_of lthy);
    (* This boundary is authoritative: assumptions added by the lemma are A,
       never owned E, even when they have the same spelling or proposition. *)
    val environment = map Thm.term_of (Assumption.local_assms_of lthy outer);
    val (statements, ctxt) = Expression.read_statement elems concl lthy;
    val assumptions = map Thm.term_of (Assumption.local_assms_of ctxt lthy);
    val goals = maps (map fst o snd) statements;
    val local_terms = [conjunction assumptions,
      Logic.list_implies (assumptions, conjunction goals)]
      |> map (Assumption.export_term ctxt lthy)
      |> Variable.export_terms ctxt lthy;
    val ([assumptions_local, target_local], statement_ctxt) =
      Variable.import_terms true local_terms lthy;
    val statement_parameters = filter (not o Variable.is_fixed lthy o #1)
      (Term.add_frees target_local []);
    val close_statement = close_frees (not o Variable.is_fixed lthy o #1);
    val local_target = close_statement target_local;
    val local_assumptions = close_statement assumptions_local;
    val local_negative = HOLogic.mk_Trueprop (HOLogic.mk_not (hol_prop local_target));
    val exported = [conjunction environment, local_assumptions, local_target,
        Logic.list_implies (environment, local_target),
        Logic.list_implies (environment, local_negative)]
      |> map (Assumption.export_term lthy outer)
      |> Variable.export_terms statement_ctxt outer;
    (* Import the complete tuple together: environment and goal parameters
       cannot silently receive different fresh names or type instantiations. *)
    val ([env, assms, target, positive, negative], global_ctxt) =
      Variable.import_terms true exported outer;
    (* Keep the complete obligation under one HOL judgment. Specification.theorem
       may otherwise turn outer Pure binders into schematic variables before
       its callback, obscuring exact closed-goal comparison. *)
    val closed_positive = HOLogic.mk_Trueprop (hol_prop (close_frees (K true) positive));
    val closed_negative = HOLogic.mk_Trueprop (hol_prop (close_frees (K true) negative));
    val _ = if null (Term.add_frees closed_negative [])
        andalso null (Term.add_vars closed_negative [])
      then () else error "REGEN_REFUTATION_UNSUPPORTED: unclosed owned-context obligation";
    val _ = List.app (fn term => (Thm.cterm_of global_ctxt term; ()))
      [env, assms, target, closed_positive, closed_negative];
    fun term_fields name term = [field (name ^ "_term") (term_xml term),
      field (name ^ "_text") (plain (Syntax.string_of_term global_ctxt term))];
    val scope_fields = [field "result_scope" "owned-context-outcomes-v1",
        field "environment_evidence_status" "not_established_by_negative_certificate",
        field "assumption_boundary" "Expression.read_statement:ctxt-to-owned-lthy",
        field "statement_parameters" (commas (map (fn (name, T) =>
          name ^ " :: " ^ plain (Syntax.string_of_typ statement_ctxt T)) statement_parameters))]
      @ term_fields "environment" env
      @ term_fields "statement_assumptions" assms
      @ term_fields "local_target" target
      @ term_fields "positive_obligation" closed_positive
      @ term_fields "negative_obligation" closed_negative;
    val _ = captured_target := SOME (id, closed_negative, scope_fields);
    val observation = XML.Elem (("contract_scope", [("version", "1"),
      ("contract_id", id)]), scope_fields);
    val _ = writeln ("REGEN_REFUTATION_SCOPE " ^ hex id ^ " " ^ hex (XML.string_of observation));
  in lthy end;

fun negative id name int lthy =
  let
    val (expected, scope_fields) = (case ! captured_target of SOME (saved_id, term, fields) =>
        if saved_id = id then (term, fields) else error "Refutation target identity mismatch"
      | NONE => error "Refutation target was not exported");
    val _ = if null (Assumption.all_assms_of lthy) then ()
      else error "Refutation proof must run outside the owned assumptions";
  in Specification.theorem false Thm.theoremK NONE (audit id expected scope_fields)
       (name, []) [] [] (Element.Shows [(Binding.empty_atts, [(expected, [])])]) int lthy end;

val _ = Outer_Syntax.local_theory \<^command_keyword>\<open>regen_refute_capture\<close>
  "separate fixed statement assumptions from the owned environment and export both obligations"
  (Parse.name -- (long_statement || short_statement) >> (fn (id, spec) => capture id spec));
val _ = Outer_Syntax.local_theory_to_proof' \<^command_keyword>\<open>regen_refute_exported\<close>
  "prove the environment-relative negation of a fixed target"
  (Parse.name -- Parse.binding >> (fn (id, name) => negative id name));
in end
