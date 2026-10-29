theory Interval_Audit
  imports Interval_Baseline_Proofs Interval_Extensions Interval_Refinement
begin
ML \<open>
  val ctxt = @{context};
  fun audit (id, target, theorem_name) =
    let
      val th = Proof_Context.get_thm ctxt theorem_name;
      val expected = Syntax.read_prop ctxt target;
      val _ = if Thm.prop_of th aconv expected then ()
        else error ("Wrong preparation theorem: " ^ id);
      val _ = if null (Thm.hyps_of th) then ()
        else error ("Undischarged assumptions: " ^ id);
      val _ = if null (Thm_Deps.all_oracles [th]) then ()
        else error ("Oracle dependency: " ^ id);
    in writeln ("INTERVALTREE_VERIFIED " ^ id) end;
  val _ = List.app audit [
    ("spec_overlaps_symm", "Interval_Baseline.spec_overlaps_symm", "Interval_Baseline_Proofs.verified_spec_overlaps_symm"),
    ("spec_overlaps_self_nonempty", "Interval_Baseline.spec_overlaps_self_nonempty", "Interval_Baseline_Proofs.verified_spec_overlaps_self_nonempty"),
    ("spec_overlaps_self_empty", "Interval_Baseline.spec_overlaps_self_empty", "Interval_Baseline_Proofs.verified_spec_overlaps_self_empty"),
    ("spec_overlaps_disjoint_false", "Interval_Baseline.spec_overlaps_disjoint_false", "Interval_Baseline_Proofs.verified_spec_overlaps_disjoint_false"),
    ("spec_overlaps_iff_common_point", "Interval_Baseline.spec_overlaps_iff_common_point", "Interval_Baseline_Proofs.verified_spec_overlaps_iff_common_point"),
    ("spec_merge_nil", "Interval_Baseline.spec_merge_nil", "Interval_Baseline_Proofs.verified_spec_merge_nil"),
    ("spec_merge_singleton", "Interval_Baseline.spec_merge_singleton", "Interval_Baseline_Proofs.verified_spec_merge_singleton"),
    ("spec_merge_length_le", "Interval_Baseline.spec_merge_length_le", "Interval_Baseline_Proofs.verified_spec_merge_length_le"),
    ("spec_chop_removes", "Interval_Baseline.spec_chop_removes", "Interval_Baseline_Proofs.verified_spec_chop_removes"),
    ("spec_chop_pointwise", "Interval_Baseline.spec_chop_pointwise", "Interval_Baseline_Proofs.verified_spec_chop_pointwise"),
    ("spec_chop_empty_range_id", "Interval_Baseline.spec_chop_empty_range_id", "Interval_Baseline_Proofs.verified_spec_chop_empty_range_id"),
    ("spec_chop_idempotent", "Interval_Baseline.spec_chop_idempotent", "Interval_Baseline_Proofs.verified_spec_chop_idempotent"),
    ("spec_chop_append_coverage", "Interval_Baseline.spec_chop_append_coverage", "Interval_Baseline_Proofs.verified_spec_chop_append_coverage"),
    ("ext_merge_exact_measure", "Interval_Extensions.ext_merge_exact_measure", "Interval_Extensions.verified_ext_merge_exact_measure"),
    ("ext_canonical_exact_measure", "Interval_Extensions.ext_canonical_exact_measure", "Interval_Extensions.verified_ext_canonical_exact_measure"),
    ("ext_cut_all_coverage", "Interval_Extensions.ext_cut_all_coverage", "Interval_Extensions.verified_ext_cut_all_coverage"),
    ("tree_point_query_exact", "Interval_Refinement.tree_point_query_exact", "Interval_Refinement.verified_tree_point_query_exact"),
    ("refine_payload_erasure_coverage", "Interval_Refinement.refine_payload_erasure_coverage", "Interval_Refinement.verified_refine_payload_erasure_coverage"),
    ("refine_point_coverage_bridge", "Interval_Refinement.refine_point_coverage_bridge", "Interval_Refinement.verified_refine_point_coverage_bridge"),
    ("tree_metadata_depth_exact", "Interval_Refinement.tree_metadata_depth_exact", "Interval_Refinement.verified_tree_metadata_depth_exact"),
    ("refine_state_point_query", "Interval_Refinement.refine_state_point_query", "Interval_Refinement.verified_refine_state_point_query"),
    ("refine_empty_state", "Interval_Refinement.refine_empty_state", "Interval_Refinement.verified_refine_empty_state"),
    ("refine_add_abstract_content", "Interval_Refinement.refine_add_abstract_content", "Interval_Refinement.verified_refine_add_abstract_content"),
    ("refine_remove_abstract_content", "Interval_Refinement.refine_remove_abstract_content", "Interval_Refinement.verified_refine_remove_abstract_content"),
    ("refine_duplicate_insert_noop", "Interval_Refinement.refine_duplicate_insert_noop", "Interval_Refinement.verified_refine_duplicate_insert_noop"),
    ("refine_missing_discard_noop", "Interval_Refinement.refine_missing_discard_noop", "Interval_Refinement.verified_refine_missing_discard_noop"),
    ("refine_missing_remove_error", "Interval_Refinement.refine_missing_remove_error", "Interval_Refinement.verified_refine_missing_remove_error"),
    ("tree_singleton_insert_progress", "Interval_Refinement.tree_singleton_insert_progress", "Interval_Refinement.verified_tree_singleton_insert_progress"),
    ("refine_overlap_query", "Interval_Refinement.refine_overlap_query", "Interval_Refinement.verified_refine_overlap_query"),
    ("refine_envelop_query", "Interval_Refinement.refine_envelop_query", "Interval_Refinement.verified_refine_envelop_query")
  ];
\<close>
end
