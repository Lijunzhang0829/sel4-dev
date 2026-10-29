theory Interval_Baseline_Proofs
  imports Interval_Baseline
begin

text \<open>Preparation proofs, not model-generated evaluation results.
The sanitized input exporter excludes this theory and all preparation lemmas.\<close>

lemma verified_spec_overlaps_symm: spec_overlaps_symm
  by (auto simp: spec_overlaps_symm_def overlaps_def)

lemma verified_spec_overlaps_self_nonempty: spec_overlaps_self_nonempty
  by (simp add: spec_overlaps_self_nonempty_def overlaps_def)

lemma verified_spec_overlaps_self_empty: spec_overlaps_self_empty
  by (simp add: spec_overlaps_self_empty_def overlaps_def)

lemma verified_spec_overlaps_disjoint_false: spec_overlaps_disjoint_false
  by (auto simp: spec_overlaps_disjoint_false_def overlaps_def)

lemma verified_spec_overlaps_iff_common_point: spec_overlaps_iff_common_point
  unfolding spec_overlaps_iff_common_point_def overlaps_def
  by (auto simp: covered_def; presburger)

lemma verified_spec_merge_nil: spec_merge_nil
  by (simp add: spec_merge_nil_def merge_overlaps_def sort_by_lo_def)

lemma verified_spec_merge_singleton: spec_merge_singleton
  by (simp add: spec_merge_singleton_def merge_overlaps_def sort_by_lo_def)

lemma verified_spec_chop_removes: spec_chop_removes
  by (simp add: spec_chop_removes_def chop_pointwise)

lemma verified_spec_chop_pointwise: spec_chop_pointwise
  by (simp add: spec_chop_pointwise_def chop_pointwise)

lemma verified_spec_chop_empty_range_id: spec_chop_empty_range_id
  by (auto simp: spec_chop_empty_range_id_def chop_pointwise)

lemma verified_spec_chop_idempotent: spec_chop_idempotent
  by (simp add: spec_chop_idempotent_def chop_pointwise)

lemma verified_spec_chop_append_coverage: spec_chop_append_coverage
  by (auto simp: spec_chop_append_coverage_def chop_pointwise)

lemma insert_by_lo_length:
  "length (insert_by_lo i xs) = length xs + (if lo i < hi i then 1 else 0)"
  by (induction xs) auto

lemma sort_by_lo_length:
  "length (sort_by_lo xs) = length (filter (\<lambda>i. lo i < hi i) xs)"
  by (induction xs) (auto simp: sort_by_lo_def insert_by_lo_length)

lemma coalesce_length_le:
  "length (coalesce xs) \<le> length xs"
  by (induction xs rule: coalesce.induct) (auto split: if_splits)

lemma verified_spec_merge_length_le: spec_merge_length_le
  unfolding spec_merge_length_le_def merge_overlaps_def
  using coalesce_length_le sort_by_lo_length by metis

end
