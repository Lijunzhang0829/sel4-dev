(* Trusted negative-goal construction adapted from
   vero-isabelle/src/vero/isabelle/Vero_Support.thy (negative/hol_prop).
   No benchmark proof or generated result is imported. *)
local
val baseline_axioms = sort_strings (map #1 (Thm.all_axioms_of \<^theory>));
val captured_target = Unsynchronized.ref (NONE: (string * term) option);

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
(* YXML uses control bytes that are not legal XML text; preserve it as hex. *)
fun term_xml term = String.translate hex_char
  (YXML.string_of_body (Term_XML.Encode.term_raw term));
fun field name value = XML.Elem ((name, []), [XML.Text value]);

fun audit id closed expected results lthy =
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
      then () else error "Refutation audit: theorem does not match closed negation";
    val _ = Thm.consolidate thms;
    val deps = Thm_Deps.thm_deps thy thms;
    val evidence = XML.Elem (("contract_refutation", [("version", "2"), ("contract_id", id)]),
      [field "expected_term" (term_xml expected), field "proved_term" (term_xml actual),
       field "closed_target_term" (term_xml closed), field "context_exported" "true",
       field "skip_proof" "false", field "oracles" "0", field "hypotheses" "0",
       field "changed_axioms" "false", field "exact_closed_negation" "true",
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
    val (statements, ctxt) = Expression.read_statement elems concl lthy;
    val goals = maps (map fst o snd) statements;
    val outer = Proof_Context.init_global (Proof_Context.theory_of ctxt);
    (* Ordinary assumption term exports are identity functions: add every owned
       assumption explicitly, then apply native exports (including local defs).
       No target theorem is proved, assumed, or installed in the fact namespace. *)
    val premises = map Thm.term_of (Assumption.local_assms_of ctxt outer);
    val proposition = Logic.list_implies (premises, Logic.mk_conjunction_list goals);
    val exported = proposition |> Assumption.export_term ctxt outer
      |> singleton (Variable.export_terms ctxt outer);
    val ([imported], global_ctxt) = Variable.import_terms true [exported] outer;
    val frees = Term.add_frees imported [];
    val closed = fold_rev (fn v => fn t => Logic.all (Free v) t) frees imported;
    val _ = if null (Term.add_frees closed []) andalso null (Term.add_vars closed [])
      then () else error "REGEN_REFUTATION_UNSUPPORTED: unclosed contextual target";
    val _ = Thm.cterm_of global_ctxt closed;
    val _ = Thm.cterm_of global_ctxt (HOLogic.mk_Trueprop (HOLogic.mk_not (hol_prop closed)));
    val _ = captured_target := SOME (id, closed);
    val _ = writeln ("REGEN_REFUTATION_TARGET " ^ String.translate hex_char id ^ " " ^
      SHA1.rep (SHA1.digest (term_xml closed)));
  in lthy end;

fun negative id name int lthy =
  let
    val closed = (case ! captured_target of SOME (saved_id, term) =>
        if saved_id = id then term else error "Refutation target identity mismatch"
      | NONE => error "Refutation target was not exported");
    val _ = if null (Assumption.all_assms_of lthy) then ()
      else error "Refutation proof must run outside the owned assumptions";
    val negation = HOLogic.mk_Trueprop (HOLogic.mk_not (hol_prop closed));
  in Specification.theorem false Thm.theoremK NONE (audit id closed negation)
       (name, []) [] [] (Element.Shows [(Binding.empty_atts, [(negation, [])])]) int lthy end;

val _ = Outer_Syntax.local_theory \<^command_keyword>\<open>regen_refute_capture\<close>
  "export the complete contextual fixed target without proving it"
  (Parse.name -- (long_statement || short_statement) >> (fn (id, spec) => capture id spec));
val _ = Outer_Syntax.local_theory_to_proof' \<^command_keyword>\<open>regen_refute_exported\<close>
  "prove the negation of an exported, universally closed fixed target"
  (Parse.name -- Parse.binding >> (fn (id, name) => negative id name));
in end
