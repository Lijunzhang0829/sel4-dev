theory Interval_Goals
  imports Intervaltree_Definitions.Interval_Input
begin

lemma spec_overlaps_symm: "Interval_Baseline.spec_overlaps_symm"
  oops

lemma spec_overlaps_self_nonempty: "Interval_Baseline.spec_overlaps_self_nonempty"
  oops

lemma spec_overlaps_self_empty: "Interval_Baseline.spec_overlaps_self_empty"
  oops

lemma spec_overlaps_disjoint_false: "Interval_Baseline.spec_overlaps_disjoint_false"
  oops

lemma spec_overlaps_iff_common_point: "Interval_Baseline.spec_overlaps_iff_common_point"
  oops

lemma spec_merge_coverage: "Interval_Baseline.spec_merge_coverage"
  oops

lemma spec_merge_disjoint_strict: "Interval_Baseline.spec_merge_disjoint_strict"
  oops

lemma spec_merge_nil: "Interval_Baseline.spec_merge_nil"
  oops

lemma spec_merge_singleton: "Interval_Baseline.spec_merge_singleton"
  oops

lemma spec_merge_all_empty_nil: "Interval_Baseline.spec_merge_all_empty_nil"
  oops

lemma spec_merge_output_nonempty: "Interval_Baseline.spec_merge_output_nonempty"
  oops

lemma spec_merge_sorted_lo: "Interval_Baseline.spec_merge_sorted_lo"
  oops

lemma spec_merge_length_le: "Interval_Baseline.spec_merge_length_le"
  oops

lemma spec_merge_idempotent: "Interval_Baseline.spec_merge_idempotent"
  oops

lemma spec_merge_exact_idempotent: "Interval_Baseline.spec_merge_exact_idempotent"
  oops

lemma spec_merge_totalLen_le: "Interval_Baseline.spec_merge_totalLen_le"
  oops

lemma spec_merge_totalLen_disjoint_eq: "Interval_Baseline.spec_merge_totalLen_disjoint_eq"
  oops

lemma spec_merge_hull_lo: "Interval_Baseline.spec_merge_hull_lo"
  oops

lemma spec_merge_hull_hi: "Interval_Baseline.spec_merge_hull_hi"
  oops

lemma spec_merge_chop_coverage: "Interval_Baseline.spec_merge_chop_coverage"
  oops

lemma spec_merge_reverse_eq: "Interval_Baseline.spec_merge_reverse_eq"
  oops

lemma spec_chop_removes: "Interval_Baseline.spec_chop_removes"
  oops

lemma spec_chop_pointwise: "Interval_Baseline.spec_chop_pointwise"
  oops

lemma spec_chop_empty_range_id: "Interval_Baseline.spec_chop_empty_range_id"
  oops

lemma spec_chop_idempotent: "Interval_Baseline.spec_chop_idempotent"
  oops

lemma spec_merge_canonical_unique: "Interval_Baseline.spec_merge_canonical_unique"
  oops

lemma spec_merge_coverage_determines: "Interval_Baseline.spec_merge_coverage_determines"
  oops

lemma spec_merge_saturated_lo: "Interval_Baseline.spec_merge_saturated_lo"
  oops

lemma spec_merge_concat_closure: "Interval_Baseline.spec_merge_concat_closure"
  oops

lemma spec_merge_is_canonical: "Interval_Baseline.spec_merge_is_canonical"
  oops

lemma spec_merge_saturated_hi: "Interval_Baseline.spec_merge_saturated_hi"
  oops

lemma spec_merge_run_solid: "Interval_Baseline.spec_merge_run_solid"
  oops

lemma spec_merge_lo_pred_uncovered: "Interval_Baseline.spec_merge_lo_pred_uncovered"
  oops

lemma spec_merge_hi_uncovered: "Interval_Baseline.spec_merge_hi_uncovered"
  oops

lemma spec_merge_append_coverage: "Interval_Baseline.spec_merge_append_coverage"
  oops

lemma spec_merge_concat_closure_right: "Interval_Baseline.spec_merge_concat_closure_right"
  oops

lemma spec_merge_self_union_idem: "Interval_Baseline.spec_merge_self_union_idem"
  oops

lemma spec_merge_chop_merge: "Interval_Baseline.spec_merge_chop_merge"
  oops

lemma spec_merge_chop_idempotent: "Interval_Baseline.spec_merge_chop_idempotent"
  oops

lemma spec_merge_chop_coverage_determines: "Interval_Baseline.spec_merge_chop_coverage_determines"
  oops

lemma spec_merge_coveredCount_le_one: "Interval_Baseline.spec_merge_coveredCount_le_one"
  oops

lemma spec_merge_coveredCount_exact: "Interval_Baseline.spec_merge_coveredCount_exact"
  oops

lemma spec_merge_loEndpoints_nodup: "Interval_Baseline.spec_merge_loEndpoints_nodup"
  oops

lemma spec_merge_getElem_lo_split: "Interval_Baseline.spec_merge_getElem_lo_split"
  oops

lemma spec_merge_perm_eq_any: "Interval_Baseline.spec_merge_perm_eq_any"
  oops

lemma spec_merge_pairwise_strict_gap_all: "Interval_Baseline.spec_merge_pairwise_strict_gap_all"
  oops

lemma spec_merge_consecutive_gap_witness: "Interval_Baseline.spec_merge_consecutive_gap_witness"
  oops

lemma spec_merge_coverage_monotone: "Interval_Baseline.spec_merge_coverage_monotone"
  oops

lemma spec_overlaps_merge_same_run: "Interval_Baseline.spec_overlaps_merge_same_run"
  oops

lemma spec_chop_canonical_preserves_canonical: "Interval_Baseline.spec_chop_canonical_preserves_canonical"
  oops

lemma spec_chop_canonical_sorted_lo: "Interval_Baseline.spec_chop_canonical_sorted_lo"
  oops

lemma spec_chop_canonical_pairwise_strict_gap: "Interval_Baseline.spec_chop_canonical_pairwise_strict_gap"
  oops

lemma spec_chop_canonical_merge_fixpoint: "Interval_Baseline.spec_chop_canonical_merge_fixpoint"
  oops

lemma spec_chop_totalLen_le_clamped: "Interval_Baseline.spec_chop_totalLen_le_clamped"
  oops

lemma spec_chop_append_coverage: "Interval_Baseline.spec_chop_append_coverage"
  oops

lemma spec_chop_coveredCount_cut_complement: "Interval_Baseline.spec_chop_coveredCount_cut_complement"
  oops

lemma ext_merge_exact_measure: "Interval_Extensions.ext_merge_exact_measure"
  oops

lemma ext_canonical_exact_measure: "Interval_Extensions.ext_canonical_exact_measure"
  oops

lemma ext_cut_all_coverage: "Interval_Extensions.ext_cut_all_coverage"
  oops

lemma ext_cut_all_exact_measure: "Interval_Extensions.ext_cut_all_exact_measure"
  oops

lemma ext_one_cut_fragment_bound: "Interval_Extensions.ext_one_cut_fragment_bound"
  oops

lemma ext_cut_all_fragment_bound: "Interval_Extensions.ext_cut_all_fragment_bound"
  oops

lemma ext_merge_component_count: "Interval_Extensions.ext_merge_component_count"
  oops

lemma ext_merge_component_hulls: "Interval_Extensions.ext_merge_component_hulls"
  oops

lemma ext_merge_component_bijection: "Interval_Extensions.ext_merge_component_bijection"
  oops

lemma tree_point_query_exact: "Interval_Refinement.tree_point_query_exact"
  oops

lemma refine_payload_erasure_coverage: "Interval_Refinement.refine_payload_erasure_coverage"
  oops

lemma refine_point_coverage_bridge: "Interval_Refinement.refine_point_coverage_bridge"
  oops

lemma tree_metadata_depth_exact: "Interval_Refinement.tree_metadata_depth_exact"
  oops

lemma refine_state_point_query: "Interval_Refinement.refine_state_point_query"
  oops

lemma refine_empty_state: "Interval_Refinement.refine_empty_state"
  oops

lemma refine_add_abstract_content: "Interval_Refinement.refine_add_abstract_content"
  oops

lemma refine_remove_abstract_content: "Interval_Refinement.refine_remove_abstract_content"
  oops

lemma refine_duplicate_insert_noop: "Interval_Refinement.refine_duplicate_insert_noop"
  oops

lemma refine_missing_discard_noop: "Interval_Refinement.refine_missing_discard_noop"
  oops

lemma refine_missing_remove_error: "Interval_Refinement.refine_missing_remove_error"
  oops

lemma tree_singleton_insert_progress: "Interval_Refinement.tree_singleton_insert_progress"
  oops

lemma tree_rotation_content: "Interval_Refinement.tree_rotation_content"
  oops

lemma tree_add_content: "Interval_Refinement.tree_add_content"
  oops

lemma tree_remove_content: "Interval_Refinement.tree_remove_content"
  oops

lemma tree_add_preserves_wf: "Interval_Refinement.tree_add_preserves_wf"
  oops

lemma tree_remove_preserves_wf: "Interval_Refinement.tree_remove_preserves_wf"
  oops

lemma tree_add_fuel_adequacy: "Interval_Refinement.tree_add_fuel_adequacy"
  oops

lemma tree_remove_fuel_adequacy: "Interval_Refinement.tree_remove_fuel_adequacy"
  oops

lemma refine_add_state_invariant: "Interval_Refinement.refine_add_state_invariant"
  oops

lemma refine_remove_state_invariant: "Interval_Refinement.refine_remove_state_invariant"
  oops

lemma refine_overlap_query: "Interval_Refinement.refine_overlap_query"
  oops

lemma refine_envelop_query: "Interval_Refinement.refine_envelop_query"
  oops

lemma refine_successful_trace_invariant: "Interval_Refinement.refine_successful_trace_invariant"
  oops

lemma refine_successful_trace_point_query: "Interval_Refinement.refine_successful_trace_point_query"
  oops

lemma refine_trace_abstract_content: "Interval_Refinement.refine_trace_abstract_content"
  oops

end
