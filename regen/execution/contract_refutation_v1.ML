(* Trusted negative-goal construction adapted from
   vero-isabelle/src/vero/isabelle/Vero_Support.thy (negative/hol_prop).
   No benchmark proof or generated result is imported. *)
local
val baseline_axioms = sort_strings (map #1 (Thm.all_axioms_of \<^theory>));

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

fun audit id expected results lthy =
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
    val evidence = XML.Elem (("contract_refutation", [("version", "1"), ("contract_id", id)]),
      [field "expected_term" (term_xml expected), field "proved_term" (term_xml actual),
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

fun negative id (binding, includes, elems, concl) int lthy =
  let
    val _ = if null includes then () else error "REGEN_REFUTATION_UNSUPPORTED: includes";
    val _ = (case concl of Element.Shows _ => ()
      | _ => error "REGEN_REFUTATION_UNSUPPORTED: obtains");
    val (statements, ctxt) = Expression.read_statement elems concl lthy;
    val goals = maps (map fst o snd) statements;
    val premises = map Thm.term_of (Assumption.local_assms_of ctxt lthy);
    val proposition = Logic.list_implies (premises, Logic.mk_conjunction_list goals);
    val frees = Term.add_frees proposition []
      |> filter_out (fn (name, _) => Variable.is_fixed lthy name);
    val closed = fold_rev (fn v => fn t => Logic.all (Free v) t) frees proposition;
    val negation = HOLogic.mk_Trueprop (HOLogic.mk_not (hol_prop closed));
    val name = if Binding.is_empty (#1 binding)
      then (case statements of (b, _) :: _ => #1 b | _ => Binding.empty)
      else #1 binding;
  in Specification.theorem false Thm.theoremK NONE (audit id negation)
       (name, []) [] [] (Element.Shows [(Binding.empty_atts, [(negation, [])])]) int lthy end;

val _ = Outer_Syntax.local_theory_to_proof' \<^command_keyword>\<open>regen_refute\<close>
  "prove the negation of a frozen universally closed contract"
  (Parse.name -- (long_statement || short_statement) >> (fn (id, spec) => negative id spec));
in end
