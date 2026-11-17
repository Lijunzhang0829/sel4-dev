theory Binomial_Preparation_Audit
  imports Binomial_Heap_Targets
begin

context binomial_heap_input_global_addresses begin
ML \<open>
  val facts = @{thms ac_corres final_defs};
  val _ = if Thm_Deps.has_skip_proof facts then error "Skipped translation proof" else ();
  val _ = if null (Thm_Deps.all_oracles facts) then () else error "Translation uses an oracle";
  val _ = writeln ("CALG_BINOMIAL_TRANSLATION_FACTS_CHECKED " ^ string_of_int (length facts));
  val _ = writeln ("CALG_BINOMIAL_STACK_TERM " ^ ML_Syntax.print_term @{term "\<S>"});
\<close>
end

ML \<open>
  val targets = ["bn_count_exact", "bn_insert_correct", "bn_pop_correct", "bn_free_owned_storage",
    "bn_new_empty_or_failure", "bn_ref_null", "bn_ref_increment", "bn_merge_leaves_nonfailure"];
  val names = Facts.dest_static true [] (Global_Theory.facts_of @{theory}) |> map fst;
  val leaked = filter (fn name => member (op =) targets (Long_Name.base_name name)) names;
  val _ = if null leaked then () else error ("Unproved target leaked: " ^ commas leaked);
  val _ = writeln "CALG_BINOMIAL_TARGETS_NOT_EXPORTED_AS_FACTS 8";
\<close>
end
