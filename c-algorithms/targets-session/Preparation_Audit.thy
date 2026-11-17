theory Preparation_Audit
  imports Binary_Heap_Targets Environment_Sanity
begin

context binary_heap_input_global_addresses
begin
thm ac_corres
thm \<P>_defs
thm fun_ptr_simps
ML \<open>
  val facts = @{thms ac_corres final_defs};
  val _ = if Thm_Deps.has_skip_proof facts then error "Skipped translation proof" else ();
  val _ = if null (Thm_Deps.all_oracles facts) then () else error "Translation uses an oracle";
  val _ = writeln ("CALG_TRANSLATION_FACTS_CHECKED " ^ string_of_int (length facts));
  val _ = Pretty.writeln (Thm_Deps.pretty_thm_oracles @{context} facts);
\<close>
end

ML \<open>
  val witnesses = @{thms empty_allocator_wf fresh_block_witness malloc_success_witness
    one_allocator_wf realloc_in_place_witness realloc_move_witness free_success_witness
    invalid_free_rejected malloc_failure_execution malloc_success_execution
    representation_witness insertion_precondition_witness};
  val _ = if Thm_Deps.has_skip_proof witnesses orelse not (null (Thm_Deps.all_oracles witnesses))
    then error "Preparation witness uses an unchecked proof" else ();
  val _ = writeln ("CALG_PREPARATION_WITNESSES_CHECKED " ^ string_of_int (length witnesses));
  val targets = ["bh_count_exact", "bh_insert_correct", "bh_insert_without_growth",
    "bh_pop_correct", "bh_free_owned_storage", "bh_new_empty_or_failure"];
  val exported_names = Facts.dest_static true [] (Global_Theory.facts_of @{theory}) |> map fst;
  val leaked = filter (fn name => member (op =) targets (Long_Name.base_name name)) exported_names;
  val _ = if null leaked then () else error ("Unproved target leaked as a fact: " ^ commas leaked);
  val _ = writeln "CALG_TARGETS_NOT_EXPORTED_AS_FACTS 6";
\<close>
end
