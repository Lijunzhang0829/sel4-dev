theory Interval_Goals
  imports Intervaltree_Definitions.Interval_Input
begin

ML \<open>
  val ctxt = @{context};
  fun check_expansion (id, original, expanded, equation) =
    let
      val conversion = Simplifier.rewrite
        (put_simpset HOL_basic_ss ctxt addsimps [equation])
        (Thm.cterm_of ctxt original);
      val actual = Thm.term_of (Thm.rhs_of conversion);
      val _ = if actual aconv expanded then ()
        else error ("Spec expansion changed the proposition: " ^ id);
      val _ = if null (Thm.hyps_of conversion) andalso
                 null (Thm_Deps.all_oracles [conversion]) then ()
        else error ("Untrusted spec expansion: " ^ id);
    in writeln ("INTERVALTREE_SPEC_EXPANSION " ^ id) end;
  val _ = List.app check_expansion [
    ("spec_overlaps_symm", @{prop "Interval_Baseline.spec_overlaps_symm"}, @{prop "(\<forall>a b. overlaps a b = overlaps b a)"}, @{thm Interval_Baseline.spec_overlaps_symm_def}),
    ("spec_overlaps_self_nonempty", @{prop "Interval_Baseline.spec_overlaps_self_nonempty"}, @{prop "(\<forall>a. overlaps a a = (lo a < hi a))"}, @{thm Interval_Baseline.spec_overlaps_self_nonempty_def}),
    ("spec_overlaps_self_empty", @{prop "Interval_Baseline.spec_overlaps_self_empty"}, @{prop "(\<forall>a. \<not> lo a < hi a \<longrightarrow> \<not> overlaps a a)"}, @{thm Interval_Baseline.spec_overlaps_self_empty_def}),
    ("spec_overlaps_disjoint_false", @{prop "Interval_Baseline.spec_overlaps_disjoint_false"}, @{prop "(\<forall>a b. (\<not> overlaps a b) = (hi a \<le> lo b \<or> hi b \<le> lo a))"}, @{thm Interval_Baseline.spec_overlaps_disjoint_false_def}),
    ("spec_overlaps_iff_common_point", @{prop "Interval_Baseline.spec_overlaps_iff_common_point"}, @{prop "(\<forall>a b. lo a < hi a \<longrightarrow> lo b < hi b \<longrightarrow>
      overlaps a b = (\<exists>x. covered x [a] \<and> covered x [b]))"}, @{thm Interval_Baseline.spec_overlaps_iff_common_point_def}),
    ("spec_merge_coverage", @{prop "Interval_Baseline.spec_merge_coverage"}, @{prop "(\<forall>xs x. covered x (merge_overlaps xs) = covered x xs)"}, @{thm Interval_Baseline.spec_merge_coverage_def}),
    ("spec_merge_disjoint_strict", @{prop "Interval_Baseline.spec_merge_disjoint_strict"}, @{prop "(\<forall>xs.
    (\<forall>i\<in>set (merge_overlaps xs). lo i < hi i) \<and>
    (\<forall>k. Suc k < length (merge_overlaps xs) \<longrightarrow>
      hi (merge_overlaps xs ! k) < lo (merge_overlaps xs ! Suc k)))"}, @{thm Interval_Baseline.spec_merge_disjoint_strict_def}),
    ("spec_merge_nil", @{prop "Interval_Baseline.spec_merge_nil"}, @{prop "merge_overlaps [] = []"}, @{thm Interval_Baseline.spec_merge_nil_def}),
    ("spec_merge_singleton", @{prop "Interval_Baseline.spec_merge_singleton"}, @{prop "(\<forall>a.
    merge_overlaps [a] = (if lo a < hi a then [a] else []))"}, @{thm Interval_Baseline.spec_merge_singleton_def}),
    ("spec_merge_all_empty_nil", @{prop "Interval_Baseline.spec_merge_all_empty_nil"}, @{prop "(\<forall>xs.
    (merge_overlaps xs = []) = (\<forall>a\<in>set xs. \<not> lo a < hi a))"}, @{thm Interval_Baseline.spec_merge_all_empty_nil_def}),
    ("spec_merge_output_nonempty", @{prop "Interval_Baseline.spec_merge_output_nonempty"}, @{prop "(\<forall>xs. \<forall>a\<in>set (merge_overlaps xs). lo a < hi a)"}, @{thm Interval_Baseline.spec_merge_output_nonempty_def}),
    ("spec_merge_sorted_lo", @{prop "Interval_Baseline.spec_merge_sorted_lo"}, @{prop "(\<forall>xs k.
    Suc k < length (merge_overlaps xs) \<longrightarrow>
    lo (merge_overlaps xs ! k) < lo (merge_overlaps xs ! Suc k))"}, @{thm Interval_Baseline.spec_merge_sorted_lo_def}),
    ("spec_merge_length_le", @{prop "Interval_Baseline.spec_merge_length_le"}, @{prop "(\<forall>xs.
    length (merge_overlaps xs) \<le> length (filter (\<lambda>i. lo i < hi i) xs))"}, @{thm Interval_Baseline.spec_merge_length_le_def}),
    ("spec_merge_idempotent", @{prop "Interval_Baseline.spec_merge_idempotent"}, @{prop "(\<forall>xs x.
    covered x (merge_overlaps (merge_overlaps xs)) = covered x (merge_overlaps xs))"}, @{thm Interval_Baseline.spec_merge_idempotent_def}),
    ("spec_merge_exact_idempotent", @{prop "Interval_Baseline.spec_merge_exact_idempotent"}, @{prop "(\<forall>xs. merge_overlaps (merge_overlaps xs) = merge_overlaps xs)"}, @{thm Interval_Baseline.spec_merge_exact_idempotent_def}),
    ("spec_merge_totalLen_le", @{prop "Interval_Baseline.spec_merge_totalLen_le"}, @{prop "(\<forall>xs. total_len (merge_overlaps xs) \<le> clamped_total_len xs)"}, @{thm Interval_Baseline.spec_merge_totalLen_le_def}),
    ("spec_merge_totalLen_disjoint_eq", @{prop "Interval_Baseline.spec_merge_totalLen_disjoint_eq"}, @{prop "(\<forall>xs.
    canonical_intervals xs \<longrightarrow> total_len (merge_overlaps xs) = total_len xs)"}, @{thm Interval_Baseline.spec_merge_totalLen_disjoint_eq_def}),
    ("spec_merge_hull_lo", @{prop "Interval_Baseline.spec_merge_hull_lo"}, @{prop "(\<forall>xs out_iv rest.
    merge_overlaps xs = out_iv # rest \<longrightarrow>
    (\<forall>i\<in>set xs. lo i < hi i \<longrightarrow> lo out_iv \<le> lo i) \<and>
    (\<exists>i\<in>set xs. lo i < hi i \<and> lo out_iv = lo i))"}, @{thm Interval_Baseline.spec_merge_hull_lo_def}),
    ("spec_merge_hull_hi", @{prop "Interval_Baseline.spec_merge_hull_hi"}, @{prop "(\<forall>xs pre last.
    merge_overlaps xs = pre @ [last] \<longrightarrow>
    (\<forall>i\<in>set xs. lo i < hi i \<longrightarrow> hi i \<le> hi last) \<and>
    (\<exists>i\<in>set xs. lo i < hi i \<and> hi last = hi i))"}, @{thm Interval_Baseline.spec_merge_hull_hi_def}),
    ("spec_merge_chop_coverage", @{prop "Interval_Baseline.spec_merge_chop_coverage"}, @{prop "(\<forall>b e xs x.
    covered x (merge_overlaps (chop b e xs)) =
    (covered x (merge_overlaps xs) \<and> \<not> (b \<le> x \<and> x < e)))"}, @{thm Interval_Baseline.spec_merge_chop_coverage_def}),
    ("spec_merge_reverse_eq", @{prop "Interval_Baseline.spec_merge_reverse_eq"}, @{prop "(\<forall>xs. merge_overlaps xs = merge_overlaps (rev xs))"}, @{thm Interval_Baseline.spec_merge_reverse_eq_def}),
    ("spec_chop_removes", @{prop "Interval_Baseline.spec_chop_removes"}, @{prop "(\<forall>b e xs x.
    b \<le> x \<longrightarrow> x < e \<longrightarrow> \<not> covered x (chop b e xs))"}, @{thm Interval_Baseline.spec_chop_removes_def}),
    ("spec_chop_pointwise", @{prop "Interval_Baseline.spec_chop_pointwise"}, @{prop "(\<forall>b e xs x.
    covered x (chop b e xs) = (covered x xs \<and> \<not> (b \<le> x \<and> x < e)))"}, @{thm Interval_Baseline.spec_chop_pointwise_def}),
    ("spec_chop_empty_range_id", @{prop "Interval_Baseline.spec_chop_empty_range_id"}, @{prop "(\<forall>b e xs x.
    e \<le> b \<longrightarrow> covered x (chop b e xs) = covered x xs)"}, @{thm Interval_Baseline.spec_chop_empty_range_id_def}),
    ("spec_chop_idempotent", @{prop "Interval_Baseline.spec_chop_idempotent"}, @{prop "(\<forall>b e xs x.
    covered x (chop b e (chop b e xs)) = covered x (chop b e xs))"}, @{thm Interval_Baseline.spec_chop_idempotent_def}),
    ("spec_merge_canonical_unique", @{prop "Interval_Baseline.spec_merge_canonical_unique"}, @{prop "(\<forall>xs c.
    canonical_intervals c \<longrightarrow> (\<forall>x. covered x c = covered x xs) \<longrightarrow>
    merge_overlaps xs = c)"}, @{thm Interval_Baseline.spec_merge_canonical_unique_def}),
    ("spec_merge_coverage_determines", @{prop "Interval_Baseline.spec_merge_coverage_determines"}, @{prop "(\<forall>a b.
    (\<forall>x. covered x a = covered x b) \<longrightarrow> merge_overlaps a = merge_overlaps b)"}, @{thm Interval_Baseline.spec_merge_coverage_determines_def}),
    ("spec_merge_saturated_lo", @{prop "Interval_Baseline.spec_merge_saturated_lo"}, @{prop "(\<forall>xs. \<forall>out_iv\<in>set (merge_overlaps xs).
    \<exists>i\<in>set xs. lo i < hi i \<and> lo out_iv = lo i)"}, @{thm Interval_Baseline.spec_merge_saturated_lo_def}),
    ("spec_merge_concat_closure", @{prop "Interval_Baseline.spec_merge_concat_closure"}, @{prop "(\<forall>xs ys.
    merge_overlaps (merge_overlaps xs @ merge_overlaps ys) = merge_overlaps (xs @ ys))"}, @{thm Interval_Baseline.spec_merge_concat_closure_def}),
    ("spec_merge_is_canonical", @{prop "Interval_Baseline.spec_merge_is_canonical"}, @{prop "(\<forall>xs. canonical_intervals (merge_overlaps xs))"}, @{thm Interval_Baseline.spec_merge_is_canonical_def}),
    ("spec_merge_saturated_hi", @{prop "Interval_Baseline.spec_merge_saturated_hi"}, @{prop "(\<forall>xs. \<forall>out_iv\<in>set (merge_overlaps xs).
    \<exists>i\<in>set xs. lo i < hi i \<and> hi out_iv = hi i)"}, @{thm Interval_Baseline.spec_merge_saturated_hi_def}),
    ("spec_merge_run_solid", @{prop "Interval_Baseline.spec_merge_run_solid"}, @{prop "(\<forall>xs x. \<forall>out_iv\<in>set (merge_overlaps xs).
    lo out_iv \<le> x \<longrightarrow> x < hi out_iv \<longrightarrow> covered x xs)"}, @{thm Interval_Baseline.spec_merge_run_solid_def}),
    ("spec_merge_lo_pred_uncovered", @{prop "Interval_Baseline.spec_merge_lo_pred_uncovered"}, @{prop "(\<forall>xs. \<forall>out_iv\<in>set (merge_overlaps xs). \<not> covered (lo out_iv - 1) xs)"}, @{thm Interval_Baseline.spec_merge_lo_pred_uncovered_def}),
    ("spec_merge_hi_uncovered", @{prop "Interval_Baseline.spec_merge_hi_uncovered"}, @{prop "(\<forall>xs. \<forall>out_iv\<in>set (merge_overlaps xs). \<not> covered (hi out_iv) xs)"}, @{thm Interval_Baseline.spec_merge_hi_uncovered_def}),
    ("spec_merge_append_coverage", @{prop "Interval_Baseline.spec_merge_append_coverage"}, @{prop "(\<forall>xs ys x.
    covered x (merge_overlaps (xs @ ys)) =
    (covered x (merge_overlaps xs) \<or> covered x (merge_overlaps ys)))"}, @{thm Interval_Baseline.spec_merge_append_coverage_def}),
    ("spec_merge_concat_closure_right", @{prop "Interval_Baseline.spec_merge_concat_closure_right"}, @{prop "(\<forall>xs ys.
    merge_overlaps (xs @ merge_overlaps ys) = merge_overlaps (xs @ ys))"}, @{thm Interval_Baseline.spec_merge_concat_closure_right_def}),
    ("spec_merge_self_union_idem", @{prop "Interval_Baseline.spec_merge_self_union_idem"}, @{prop "(\<forall>xs.
    merge_overlaps (merge_overlaps xs @ merge_overlaps xs) = merge_overlaps xs)"}, @{thm Interval_Baseline.spec_merge_self_union_idem_def}),
    ("spec_merge_chop_merge", @{prop "Interval_Baseline.spec_merge_chop_merge"}, @{prop "(\<forall>b e xs.
    merge_overlaps (chop b e (merge_overlaps xs)) = merge_overlaps (chop b e xs))"}, @{thm Interval_Baseline.spec_merge_chop_merge_def}),
    ("spec_merge_chop_idempotent", @{prop "Interval_Baseline.spec_merge_chop_idempotent"}, @{prop "(\<forall>b e xs.
    merge_overlaps (chop b e (chop b e xs)) = merge_overlaps (chop b e xs))"}, @{thm Interval_Baseline.spec_merge_chop_idempotent_def}),
    ("spec_merge_chop_coverage_determines", @{prop "Interval_Baseline.spec_merge_chop_coverage_determines"}, @{prop "(\<forall>b e a c.
    (\<forall>x. covered x a = covered x c) \<longrightarrow>
    merge_overlaps (chop b e a) = merge_overlaps (chop b e c))"}, @{thm Interval_Baseline.spec_merge_chop_coverage_determines_def}),
    ("spec_merge_coveredCount_le_one", @{prop "Interval_Baseline.spec_merge_coveredCount_le_one"}, @{prop "(\<forall>xs x. covered_count x (merge_overlaps xs) \<le> 1)"}, @{thm Interval_Baseline.spec_merge_coveredCount_le_one_def}),
    ("spec_merge_coveredCount_exact", @{prop "Interval_Baseline.spec_merge_coveredCount_exact"}, @{prop "(\<forall>xs x.
    covered_count x (merge_overlaps xs) = (if covered x xs then 1 else 0))"}, @{thm Interval_Baseline.spec_merge_coveredCount_exact_def}),
    ("spec_merge_loEndpoints_nodup", @{prop "Interval_Baseline.spec_merge_loEndpoints_nodup"}, @{prop "(\<forall>xs. distinct (map lo (merge_overlaps xs)))"}, @{thm Interval_Baseline.spec_merge_loEndpoints_nodup_def}),
    ("spec_merge_getElem_lo_split", @{prop "Interval_Baseline.spec_merge_getElem_lo_split"}, @{prop "(\<forall>xs k.
    k < length (merge_overlaps xs) \<longrightarrow>
    \<not> covered (lo (merge_overlaps xs ! k)) (take k (merge_overlaps xs)) \<and>
    covered (lo (merge_overlaps xs ! k)) (drop k (merge_overlaps xs)))"}, @{thm Interval_Baseline.spec_merge_getElem_lo_split_def}),
    ("spec_merge_perm_eq_any", @{prop "Interval_Baseline.spec_merge_perm_eq_any"}, @{prop "(\<forall>xs ys. (\<forall>i. count_list xs i = count_list ys i) \<longrightarrow>
      merge_overlaps xs = merge_overlaps ys)"}, @{thm Interval_Baseline.spec_merge_perm_eq_any_def}),
    ("spec_merge_pairwise_strict_gap_all", @{prop "Interval_Baseline.spec_merge_pairwise_strict_gap_all"}, @{prop "(\<forall>xs i j.
    i < length (merge_overlaps xs) \<longrightarrow> j < length (merge_overlaps xs) \<longrightarrow>
    i < j \<longrightarrow> hi (merge_overlaps xs ! i) < lo (merge_overlaps xs ! j))"}, @{thm Interval_Baseline.spec_merge_pairwise_strict_gap_all_def}),
    ("spec_merge_consecutive_gap_witness", @{prop "Interval_Baseline.spec_merge_consecutive_gap_witness"}, @{prop "(\<forall>xs i.
    Suc i < length (merge_overlaps xs) \<longrightarrow> (\<exists>x.
    hi (merge_overlaps xs ! i) \<le> x \<and> x < lo (merge_overlaps xs ! Suc i) \<and>
    \<not> covered x (merge_overlaps xs)))"}, @{thm Interval_Baseline.spec_merge_consecutive_gap_witness_def}),
    ("spec_merge_coverage_monotone", @{prop "Interval_Baseline.spec_merge_coverage_monotone"}, @{prop "(\<forall>xs ys.
    (\<forall>x. covered x xs \<longrightarrow> covered x ys) \<longrightarrow>
    (\<forall>x. covered x (merge_overlaps xs) \<longrightarrow> covered x (merge_overlaps ys)))"}, @{thm Interval_Baseline.spec_merge_coverage_monotone_def}),
    ("spec_overlaps_merge_same_run", @{prop "Interval_Baseline.spec_overlaps_merge_same_run"}, @{prop "(\<forall>a b.
    lo a < hi a \<longrightarrow> lo b < hi b \<longrightarrow> overlaps a b \<longrightarrow>
    (\<exists>out_iv\<in>set (merge_overlaps [a,b]).
      (\<forall>x. covered x [a] \<longrightarrow> covered x [out_iv]) \<and>
      (\<forall>x. covered x [b] \<longrightarrow> covered x [out_iv])))"}, @{thm Interval_Baseline.spec_overlaps_merge_same_run_def}),
    ("spec_chop_canonical_preserves_canonical", @{prop "Interval_Baseline.spec_chop_canonical_preserves_canonical"}, @{prop "(\<forall>b e xs.
    b < e \<longrightarrow> canonical_intervals xs \<longrightarrow>
    canonical_intervals (chop b e xs))"}, @{thm Interval_Baseline.spec_chop_canonical_preserves_canonical_def}),
    ("spec_chop_canonical_sorted_lo", @{prop "Interval_Baseline.spec_chop_canonical_sorted_lo"}, @{prop "(\<forall>b e xs.
    b < e \<longrightarrow> canonical_intervals xs \<longrightarrow>
    (\<forall>i. Suc i < length (chop b e xs) \<longrightarrow>
      lo (chop b e xs ! i) < lo (chop b e xs ! Suc i)))"}, @{thm Interval_Baseline.spec_chop_canonical_sorted_lo_def}),
    ("spec_chop_canonical_pairwise_strict_gap", @{prop "Interval_Baseline.spec_chop_canonical_pairwise_strict_gap"}, @{prop "(\<forall>b e xs.
    b < e \<longrightarrow> canonical_intervals xs \<longrightarrow>
    (\<forall>i j. i < length (chop b e xs) \<longrightarrow> j < length (chop b e xs) \<longrightarrow>
      i < j \<longrightarrow> hi (chop b e xs ! i) < lo (chop b e xs ! j)))"}, @{thm Interval_Baseline.spec_chop_canonical_pairwise_strict_gap_def}),
    ("spec_chop_canonical_merge_fixpoint", @{prop "Interval_Baseline.spec_chop_canonical_merge_fixpoint"}, @{prop "(\<forall>b e xs.
    b < e \<longrightarrow> canonical_intervals xs \<longrightarrow>
    merge_overlaps (chop b e xs) = chop b e xs)"}, @{thm Interval_Baseline.spec_chop_canonical_merge_fixpoint_def}),
    ("spec_chop_totalLen_le_clamped", @{prop "Interval_Baseline.spec_chop_totalLen_le_clamped"}, @{prop "(\<forall>b e xs.
    b \<le> e \<longrightarrow> total_len (chop b e xs) \<le> clamped_total_len xs)"}, @{thm Interval_Baseline.spec_chop_totalLen_le_clamped_def}),
    ("spec_chop_append_coverage", @{prop "Interval_Baseline.spec_chop_append_coverage"}, @{prop "(\<forall>b e xs ys x.
    covered x (chop b e (xs @ ys)) = (covered x (chop b e xs) \<or> covered x (chop b e ys)))"}, @{thm Interval_Baseline.spec_chop_append_coverage_def}),
    ("spec_chop_coveredCount_cut_complement", @{prop "Interval_Baseline.spec_chop_coveredCount_cut_complement"}, @{prop "(\<forall>b e xs x.
    b \<le> e \<longrightarrow> covered_count x (chop b e xs) =
      (if b \<le> x \<and> x < e then 0 else covered_count x xs))"}, @{thm Interval_Baseline.spec_chop_coveredCount_cut_complement_def}),
    ("ext_merge_exact_measure", @{prop "Interval_Extensions.ext_merge_exact_measure"}, @{prop "(\<forall>xs. total_len (merge_overlaps xs) = int (card (coverage xs)))"}, @{thm Interval_Extensions.ext_merge_exact_measure_def}),
    ("ext_canonical_exact_measure", @{prop "Interval_Extensions.ext_canonical_exact_measure"}, @{prop "(\<forall>xs::iv list. canonical_intervals xs \<longrightarrow>
      total_len xs = int (card (coverage xs)))"}, @{thm Interval_Extensions.ext_canonical_exact_measure_def}),
    ("ext_cut_all_coverage", @{prop "Interval_Extensions.ext_cut_all_coverage"}, @{prop "(\<forall>rs xs. coverage (cut_all rs xs) = coverage xs - coverage rs)"}, @{thm Interval_Extensions.ext_cut_all_coverage_def}),
    ("ext_cut_all_exact_measure", @{prop "Interval_Extensions.ext_cut_all_exact_measure"}, @{prop "(\<forall>rs xs. proper_cuts rs \<longrightarrow> canonical_intervals xs \<longrightarrow>
      total_len (cut_all rs xs) = int (card (coverage xs - coverage rs)))"}, @{thm Interval_Extensions.ext_cut_all_exact_measure_def}),
    ("ext_one_cut_fragment_bound", @{prop "Interval_Extensions.ext_one_cut_fragment_bound"}, @{prop "(\<forall>b e xs. b < e \<longrightarrow> canonical_intervals xs \<longrightarrow>
      length (chop b e xs) \<le> length xs + 1)"}, @{thm Interval_Extensions.ext_one_cut_fragment_bound_def}),
    ("ext_cut_all_fragment_bound", @{prop "Interval_Extensions.ext_cut_all_fragment_bound"}, @{prop "(\<forall>rs xs. proper_cuts rs \<longrightarrow> canonical_intervals xs \<longrightarrow>
      length (cut_all rs xs) \<le> length xs + length rs)"}, @{thm Interval_Extensions.ext_cut_all_fragment_bound_def}),
    ("ext_merge_component_count", @{prop "Interval_Extensions.ext_merge_component_count"}, @{prop "(\<forall>xs. length (merge_overlaps xs) = card (interval_components xs))"}, @{thm Interval_Extensions.ext_merge_component_count_def}),
    ("ext_merge_component_hulls", @{prop "Interval_Extensions.ext_merge_component_hulls"}, @{prop "(\<forall>xs. set (merge_overlaps xs) = component_hull xs ` interval_components xs)"}, @{thm Interval_Extensions.ext_merge_component_hulls_def}),
    ("ext_merge_component_bijection", @{prop "Interval_Extensions.ext_merge_component_bijection"}, @{prop "(\<forall>xs. bij_betw (component_hull xs) (interval_components xs)
      (set (merge_overlaps xs)))"}, @{thm Interval_Extensions.ext_merge_component_bijection_def}),
    ("tree_point_query_exact", @{prop "Interval_Refinement.tree_point_query_exact"}, @{prop "(\<forall>t p. routing_ok t \<longrightarrow>
      tree_at p t = {e \<in> tree_entries t. entry_hit e p})"}, @{thm Interval_Refinement.tree_point_query_exact_def}),
    ("refine_payload_erasure_coverage", @{prop "Interval_Refinement.refine_payload_erasure_coverage"}, @{prop "(\<forall>t. coverage (map erase_payload (tree_entry_list t)) =
          coverage (tree_entry_list t))"}, @{thm Interval_Refinement.refine_payload_erasure_coverage_def}),
    ("refine_point_coverage_bridge", @{prop "Interval_Refinement.refine_point_coverage_bridge"}, @{prop "(\<forall>t p. routing_ok t \<longrightarrow>
      (covered p (tree_entry_list t) \<longleftrightarrow> tree_at p t \<noteq> {}))"}, @{thm Interval_Refinement.refine_point_coverage_bridge_def}),
    ("tree_metadata_depth_exact", @{prop "Interval_Refinement.tree_metadata_depth_exact"}, @{prop "(\<forall>t. metadata_ok t \<longrightarrow> cached_depth t = actual_depth t)"}, @{thm Interval_Refinement.tree_metadata_depth_exact_def}),
    ("refine_state_point_query", @{prop "Interval_Refinement.refine_state_point_query"}, @{prop "(\<forall>s p. state_wf s \<longrightarrow>
      state_at p s = {e \<in> set (all_items s). entry_hit e p})"}, @{thm Interval_Refinement.refine_state_point_query_def}),
    ("refine_empty_state", @{prop "Interval_Refinement.refine_empty_state"}, @{prop "state_wf empty_state"}, @{thm Interval_Refinement.refine_empty_state_def}),
    ("refine_add_abstract_content", @{prop "Interval_Refinement.refine_add_abstract_content"}, @{prop "(\<forall>fuel e s u. state_add fuel e s = Some u \<longrightarrow>
      set (all_items u) = insert e (set (all_items s)))"}, @{thm Interval_Refinement.refine_add_abstract_content_def}),
    ("refine_remove_abstract_content", @{prop "Interval_Refinement.refine_remove_abstract_content"}, @{prop "(\<forall>fuel e s u. state_remove fuel e s = Some u \<longrightarrow>
      set (all_items u) = set (all_items s) - {e})"}, @{thm Interval_Refinement.refine_remove_abstract_content_def}),
    ("refine_duplicate_insert_noop", @{prop "Interval_Refinement.refine_duplicate_insert_noop"}, @{prop "(\<forall>fuel e s. e \<in> set (all_items s) \<longrightarrow> state_add fuel e s = Some s)"}, @{thm Interval_Refinement.refine_duplicate_insert_noop_def}),
    ("refine_missing_discard_noop", @{prop "Interval_Refinement.refine_missing_discard_noop"}, @{prop "(\<forall>fuel e s. e \<notin> set (all_items s) \<longrightarrow> state_discard fuel e s = Some s)"}, @{thm Interval_Refinement.refine_missing_discard_noop_def}),
    ("refine_missing_remove_error", @{prop "Interval_Refinement.refine_missing_remove_error"}, @{prop "(\<forall>fuel e s. e \<notin> set (all_items s) \<longrightarrow>
      state_remove_status fuel e s = MissingInterval)"}, @{thm Interval_Refinement.refine_missing_remove_error_def}),
    ("tree_singleton_insert_progress", @{prop "Interval_Refinement.tree_singleton_insert_progress"}, @{prop "(\<forall>e. entry_valid e \<longrightarrow> (\<exists>u. state_add 1 e empty_state = Some u))"}, @{thm Interval_Refinement.tree_singleton_insert_progress_def}),
    ("tree_rotation_content", @{prop "Interval_Refinement.tree_rotation_content"}, @{prop "(\<forall>fuel t u. routing_ok t \<and> metadata_ok t \<and>
      tree_rotate fuel t = Some u \<longrightarrow> tree_entries u = tree_entries t)"}, @{thm Interval_Refinement.tree_rotation_content_def}),
    ("tree_add_content", @{prop "Interval_Refinement.tree_add_content"}, @{prop "(\<forall>fuel e t u. tree_wf t \<and> entry_valid e \<and>
      tree_add fuel e t = Some u \<longrightarrow>
      tree_entries u = insert e (tree_entries t))"}, @{thm Interval_Refinement.tree_add_content_def}),
    ("tree_remove_content", @{prop "Interval_Refinement.tree_remove_content"}, @{prop "(\<forall>fuel e t u. tree_wf t \<and> e \<in> tree_entries t \<and>
      tree_remove fuel e t = Some u \<longrightarrow>
      tree_entries u = tree_entries t - {e})"}, @{thm Interval_Refinement.tree_remove_content_def}),
    ("tree_add_preserves_wf", @{prop "Interval_Refinement.tree_add_preserves_wf"}, @{prop "(\<forall>fuel e t u. tree_wf t \<and> entry_valid e \<and>
      tree_add fuel e t = Some u \<longrightarrow> tree_wf u)"}, @{thm Interval_Refinement.tree_add_preserves_wf_def}),
    ("tree_remove_preserves_wf", @{prop "Interval_Refinement.tree_remove_preserves_wf"}, @{prop "(\<forall>fuel e t u. tree_wf t \<and> e \<in> tree_entries t \<and>
      tree_remove fuel e t = Some u \<longrightarrow> tree_wf u)"}, @{thm Interval_Refinement.tree_remove_preserves_wf_def}),
    ("tree_add_fuel_adequacy", @{prop "Interval_Refinement.tree_add_fuel_adequacy"}, @{prop "(\<forall>t e. tree_wf t \<and> entry_valid e \<longrightarrow>
      (\<exists>fuel u. tree_add fuel e t = Some u))"}, @{thm Interval_Refinement.tree_add_fuel_adequacy_def}),
    ("tree_remove_fuel_adequacy", @{prop "Interval_Refinement.tree_remove_fuel_adequacy"}, @{prop "(\<forall>t e. tree_wf t \<and> e \<in> tree_entries t \<longrightarrow>
      (\<exists>fuel u. tree_remove fuel e t = Some u))"}, @{thm Interval_Refinement.tree_remove_fuel_adequacy_def}),
    ("refine_add_state_invariant", @{prop "Interval_Refinement.refine_add_state_invariant"}, @{prop "(\<forall>fuel e s u. state_wf s \<and> entry_valid e \<and>
      state_add fuel e s = Some u \<longrightarrow> state_wf u)"}, @{thm Interval_Refinement.refine_add_state_invariant_def}),
    ("refine_remove_state_invariant", @{prop "Interval_Refinement.refine_remove_state_invariant"}, @{prop "(\<forall>fuel e s u. state_wf s \<and> e \<in> set (all_items s) \<and>
      state_remove fuel e s = Some u \<longrightarrow> state_wf u)"}, @{thm Interval_Refinement.refine_remove_state_invariant_def}),
    ("refine_overlap_query", @{prop "Interval_Refinement.refine_overlap_query"}, @{prop "(\<forall>s a b. state_wf s \<longrightarrow>
      tree_overlap a b s =
        {e \<in> set (all_items s). a < b \<and> lo e < b \<and> a < hi e})"}, @{thm Interval_Refinement.refine_overlap_query_def}),
    ("refine_envelop_query", @{prop "Interval_Refinement.refine_envelop_query"}, @{prop "(\<forall>s a b. state_wf s \<longrightarrow>
      tree_envelop a b s =
        {e \<in> set (all_items s). a < b \<and> a \<le> lo e \<and> hi e \<le> b})"}, @{thm Interval_Refinement.refine_envelop_query_def}),
    ("refine_successful_trace_invariant", @{prop "Interval_Refinement.refine_successful_trace_invariant"}, @{prop "(\<forall>fuel ops s u. state_wf s \<and> run_updates fuel ops s = Some u
       \<longrightarrow> state_wf u)"}, @{thm Interval_Refinement.refine_successful_trace_invariant_def}),
    ("refine_successful_trace_point_query", @{prop "Interval_Refinement.refine_successful_trace_point_query"}, @{prop "(\<forall>fuel ops s u p. state_wf s \<and> run_updates fuel ops s = Some u
      \<longrightarrow> state_at p u = {e \<in> set (all_items u). entry_hit e p})"}, @{thm Interval_Refinement.refine_successful_trace_point_query_def}),
    ("refine_trace_abstract_content", @{prop "Interval_Refinement.refine_trace_abstract_content"}, @{prop "(\<forall>fuel ops s u. state_wf s \<and> run_updates fuel ops s = Some u
      \<longrightarrow> abstract_run_updates ops (set (all_items s)) =
          Some (tree_entries (root_node u)))"}, @{thm Interval_Refinement.refine_trace_abstract_content_def})
  ];
\<close>

lemma spec_overlaps_symm: "(\<forall>a b. overlaps a b = overlaps b a)"
  oops

lemma spec_overlaps_self_nonempty: "(\<forall>a. overlaps a a = (lo a < hi a))"
  oops

lemma spec_overlaps_self_empty: "(\<forall>a. \<not> lo a < hi a \<longrightarrow> \<not> overlaps a a)"
  oops

lemma spec_overlaps_disjoint_false: "(\<forall>a b. (\<not> overlaps a b) = (hi a \<le> lo b \<or> hi b \<le> lo a))"
  oops

lemma spec_overlaps_iff_common_point: "(\<forall>a b. lo a < hi a \<longrightarrow> lo b < hi b \<longrightarrow>
      overlaps a b = (\<exists>x. covered x [a] \<and> covered x [b]))"
  oops

lemma spec_merge_coverage: "(\<forall>xs x. covered x (merge_overlaps xs) = covered x xs)"
  oops

lemma spec_merge_disjoint_strict: "(\<forall>xs.
    (\<forall>i\<in>set (merge_overlaps xs). lo i < hi i) \<and>
    (\<forall>k. Suc k < length (merge_overlaps xs) \<longrightarrow>
      hi (merge_overlaps xs ! k) < lo (merge_overlaps xs ! Suc k)))"
  oops

lemma spec_merge_nil: "merge_overlaps [] = []"
  oops

lemma spec_merge_singleton: "(\<forall>a.
    merge_overlaps [a] = (if lo a < hi a then [a] else []))"
  oops

lemma spec_merge_all_empty_nil: "(\<forall>xs.
    (merge_overlaps xs = []) = (\<forall>a\<in>set xs. \<not> lo a < hi a))"
  oops

lemma spec_merge_output_nonempty: "(\<forall>xs. \<forall>a\<in>set (merge_overlaps xs). lo a < hi a)"
  oops

lemma spec_merge_sorted_lo: "(\<forall>xs k.
    Suc k < length (merge_overlaps xs) \<longrightarrow>
    lo (merge_overlaps xs ! k) < lo (merge_overlaps xs ! Suc k))"
  oops

lemma spec_merge_length_le: "(\<forall>xs.
    length (merge_overlaps xs) \<le> length (filter (\<lambda>i. lo i < hi i) xs))"
  oops

lemma spec_merge_idempotent: "(\<forall>xs x.
    covered x (merge_overlaps (merge_overlaps xs)) = covered x (merge_overlaps xs))"
  oops

lemma spec_merge_exact_idempotent: "(\<forall>xs. merge_overlaps (merge_overlaps xs) = merge_overlaps xs)"
  oops

lemma spec_merge_totalLen_le: "(\<forall>xs. total_len (merge_overlaps xs) \<le> clamped_total_len xs)"
  oops

lemma spec_merge_totalLen_disjoint_eq: "(\<forall>xs.
    canonical_intervals xs \<longrightarrow> total_len (merge_overlaps xs) = total_len xs)"
  oops

lemma spec_merge_hull_lo: "(\<forall>xs out_iv rest.
    merge_overlaps xs = out_iv # rest \<longrightarrow>
    (\<forall>i\<in>set xs. lo i < hi i \<longrightarrow> lo out_iv \<le> lo i) \<and>
    (\<exists>i\<in>set xs. lo i < hi i \<and> lo out_iv = lo i))"
  oops

lemma spec_merge_hull_hi: "(\<forall>xs pre last.
    merge_overlaps xs = pre @ [last] \<longrightarrow>
    (\<forall>i\<in>set xs. lo i < hi i \<longrightarrow> hi i \<le> hi last) \<and>
    (\<exists>i\<in>set xs. lo i < hi i \<and> hi last = hi i))"
  oops

lemma spec_merge_chop_coverage: "(\<forall>b e xs x.
    covered x (merge_overlaps (chop b e xs)) =
    (covered x (merge_overlaps xs) \<and> \<not> (b \<le> x \<and> x < e)))"
  oops

lemma spec_merge_reverse_eq: "(\<forall>xs. merge_overlaps xs = merge_overlaps (rev xs))"
  oops

lemma spec_chop_removes: "(\<forall>b e xs x.
    b \<le> x \<longrightarrow> x < e \<longrightarrow> \<not> covered x (chop b e xs))"
  oops

lemma spec_chop_pointwise: "(\<forall>b e xs x.
    covered x (chop b e xs) = (covered x xs \<and> \<not> (b \<le> x \<and> x < e)))"
  oops

lemma spec_chop_empty_range_id: "(\<forall>b e xs x.
    e \<le> b \<longrightarrow> covered x (chop b e xs) = covered x xs)"
  oops

lemma spec_chop_idempotent: "(\<forall>b e xs x.
    covered x (chop b e (chop b e xs)) = covered x (chop b e xs))"
  oops

lemma spec_merge_canonical_unique: "(\<forall>xs c.
    canonical_intervals c \<longrightarrow> (\<forall>x. covered x c = covered x xs) \<longrightarrow>
    merge_overlaps xs = c)"
  oops

lemma spec_merge_coverage_determines: "(\<forall>a b.
    (\<forall>x. covered x a = covered x b) \<longrightarrow> merge_overlaps a = merge_overlaps b)"
  oops

lemma spec_merge_saturated_lo: "(\<forall>xs. \<forall>out_iv\<in>set (merge_overlaps xs).
    \<exists>i\<in>set xs. lo i < hi i \<and> lo out_iv = lo i)"
  oops

lemma spec_merge_concat_closure: "(\<forall>xs ys.
    merge_overlaps (merge_overlaps xs @ merge_overlaps ys) = merge_overlaps (xs @ ys))"
  oops

lemma spec_merge_is_canonical: "(\<forall>xs. canonical_intervals (merge_overlaps xs))"
  oops

lemma spec_merge_saturated_hi: "(\<forall>xs. \<forall>out_iv\<in>set (merge_overlaps xs).
    \<exists>i\<in>set xs. lo i < hi i \<and> hi out_iv = hi i)"
  oops

lemma spec_merge_run_solid: "(\<forall>xs x. \<forall>out_iv\<in>set (merge_overlaps xs).
    lo out_iv \<le> x \<longrightarrow> x < hi out_iv \<longrightarrow> covered x xs)"
  oops

lemma spec_merge_lo_pred_uncovered: "(\<forall>xs. \<forall>out_iv\<in>set (merge_overlaps xs). \<not> covered (lo out_iv - 1) xs)"
  oops

lemma spec_merge_hi_uncovered: "(\<forall>xs. \<forall>out_iv\<in>set (merge_overlaps xs). \<not> covered (hi out_iv) xs)"
  oops

lemma spec_merge_append_coverage: "(\<forall>xs ys x.
    covered x (merge_overlaps (xs @ ys)) =
    (covered x (merge_overlaps xs) \<or> covered x (merge_overlaps ys)))"
  oops

lemma spec_merge_concat_closure_right: "(\<forall>xs ys.
    merge_overlaps (xs @ merge_overlaps ys) = merge_overlaps (xs @ ys))"
  oops

lemma spec_merge_self_union_idem: "(\<forall>xs.
    merge_overlaps (merge_overlaps xs @ merge_overlaps xs) = merge_overlaps xs)"
  oops

lemma spec_merge_chop_merge: "(\<forall>b e xs.
    merge_overlaps (chop b e (merge_overlaps xs)) = merge_overlaps (chop b e xs))"
  oops

lemma spec_merge_chop_idempotent: "(\<forall>b e xs.
    merge_overlaps (chop b e (chop b e xs)) = merge_overlaps (chop b e xs))"
  oops

lemma spec_merge_chop_coverage_determines: "(\<forall>b e a c.
    (\<forall>x. covered x a = covered x c) \<longrightarrow>
    merge_overlaps (chop b e a) = merge_overlaps (chop b e c))"
  oops

lemma spec_merge_coveredCount_le_one: "(\<forall>xs x. covered_count x (merge_overlaps xs) \<le> 1)"
  oops

lemma spec_merge_coveredCount_exact: "(\<forall>xs x.
    covered_count x (merge_overlaps xs) = (if covered x xs then 1 else 0))"
  oops

lemma spec_merge_loEndpoints_nodup: "(\<forall>xs. distinct (map lo (merge_overlaps xs)))"
  oops

lemma spec_merge_getElem_lo_split: "(\<forall>xs k.
    k < length (merge_overlaps xs) \<longrightarrow>
    \<not> covered (lo (merge_overlaps xs ! k)) (take k (merge_overlaps xs)) \<and>
    covered (lo (merge_overlaps xs ! k)) (drop k (merge_overlaps xs)))"
  oops

lemma spec_merge_perm_eq_any: "(\<forall>xs ys. (\<forall>i. count_list xs i = count_list ys i) \<longrightarrow>
      merge_overlaps xs = merge_overlaps ys)"
  oops

lemma spec_merge_pairwise_strict_gap_all: "(\<forall>xs i j.
    i < length (merge_overlaps xs) \<longrightarrow> j < length (merge_overlaps xs) \<longrightarrow>
    i < j \<longrightarrow> hi (merge_overlaps xs ! i) < lo (merge_overlaps xs ! j))"
  oops

lemma spec_merge_consecutive_gap_witness: "(\<forall>xs i.
    Suc i < length (merge_overlaps xs) \<longrightarrow> (\<exists>x.
    hi (merge_overlaps xs ! i) \<le> x \<and> x < lo (merge_overlaps xs ! Suc i) \<and>
    \<not> covered x (merge_overlaps xs)))"
  oops

lemma spec_merge_coverage_monotone: "(\<forall>xs ys.
    (\<forall>x. covered x xs \<longrightarrow> covered x ys) \<longrightarrow>
    (\<forall>x. covered x (merge_overlaps xs) \<longrightarrow> covered x (merge_overlaps ys)))"
  oops

lemma spec_overlaps_merge_same_run: "(\<forall>a b.
    lo a < hi a \<longrightarrow> lo b < hi b \<longrightarrow> overlaps a b \<longrightarrow>
    (\<exists>out_iv\<in>set (merge_overlaps [a,b]).
      (\<forall>x. covered x [a] \<longrightarrow> covered x [out_iv]) \<and>
      (\<forall>x. covered x [b] \<longrightarrow> covered x [out_iv])))"
  oops

lemma spec_chop_canonical_preserves_canonical: "(\<forall>b e xs.
    b < e \<longrightarrow> canonical_intervals xs \<longrightarrow>
    canonical_intervals (chop b e xs))"
  oops

lemma spec_chop_canonical_sorted_lo: "(\<forall>b e xs.
    b < e \<longrightarrow> canonical_intervals xs \<longrightarrow>
    (\<forall>i. Suc i < length (chop b e xs) \<longrightarrow>
      lo (chop b e xs ! i) < lo (chop b e xs ! Suc i)))"
  oops

lemma spec_chop_canonical_pairwise_strict_gap: "(\<forall>b e xs.
    b < e \<longrightarrow> canonical_intervals xs \<longrightarrow>
    (\<forall>i j. i < length (chop b e xs) \<longrightarrow> j < length (chop b e xs) \<longrightarrow>
      i < j \<longrightarrow> hi (chop b e xs ! i) < lo (chop b e xs ! j)))"
  oops

lemma spec_chop_canonical_merge_fixpoint: "(\<forall>b e xs.
    b < e \<longrightarrow> canonical_intervals xs \<longrightarrow>
    merge_overlaps (chop b e xs) = chop b e xs)"
  oops

lemma spec_chop_totalLen_le_clamped: "(\<forall>b e xs.
    b \<le> e \<longrightarrow> total_len (chop b e xs) \<le> clamped_total_len xs)"
  oops

lemma spec_chop_append_coverage: "(\<forall>b e xs ys x.
    covered x (chop b e (xs @ ys)) = (covered x (chop b e xs) \<or> covered x (chop b e ys)))"
  oops

lemma spec_chop_coveredCount_cut_complement: "(\<forall>b e xs x.
    b \<le> e \<longrightarrow> covered_count x (chop b e xs) =
      (if b \<le> x \<and> x < e then 0 else covered_count x xs))"
  oops

lemma ext_merge_exact_measure: "(\<forall>xs. total_len (merge_overlaps xs) = int (card (coverage xs)))"
  oops

lemma ext_canonical_exact_measure: "(\<forall>xs::iv list. canonical_intervals xs \<longrightarrow>
      total_len xs = int (card (coverage xs)))"
  oops

lemma ext_cut_all_coverage: "(\<forall>rs xs. coverage (cut_all rs xs) = coverage xs - coverage rs)"
  oops

lemma ext_cut_all_exact_measure: "(\<forall>rs xs. proper_cuts rs \<longrightarrow> canonical_intervals xs \<longrightarrow>
      total_len (cut_all rs xs) = int (card (coverage xs - coverage rs)))"
  oops

lemma ext_one_cut_fragment_bound: "(\<forall>b e xs. b < e \<longrightarrow> canonical_intervals xs \<longrightarrow>
      length (chop b e xs) \<le> length xs + 1)"
  oops

lemma ext_cut_all_fragment_bound: "(\<forall>rs xs. proper_cuts rs \<longrightarrow> canonical_intervals xs \<longrightarrow>
      length (cut_all rs xs) \<le> length xs + length rs)"
  oops

lemma ext_merge_component_count: "(\<forall>xs. length (merge_overlaps xs) = card (interval_components xs))"
  oops

lemma ext_merge_component_hulls: "(\<forall>xs. set (merge_overlaps xs) = component_hull xs ` interval_components xs)"
  oops

lemma ext_merge_component_bijection: "(\<forall>xs. bij_betw (component_hull xs) (interval_components xs)
      (set (merge_overlaps xs)))"
  oops

lemma tree_point_query_exact: "(\<forall>t p. routing_ok t \<longrightarrow>
      tree_at p t = {e \<in> tree_entries t. entry_hit e p})"
  oops

lemma refine_payload_erasure_coverage: "(\<forall>t. coverage (map erase_payload (tree_entry_list t)) =
          coverage (tree_entry_list t))"
  oops

lemma refine_point_coverage_bridge: "(\<forall>t p. routing_ok t \<longrightarrow>
      (covered p (tree_entry_list t) \<longleftrightarrow> tree_at p t \<noteq> {}))"
  oops

lemma tree_metadata_depth_exact: "(\<forall>t. metadata_ok t \<longrightarrow> cached_depth t = actual_depth t)"
  oops

lemma refine_state_point_query: "(\<forall>s p. state_wf s \<longrightarrow>
      state_at p s = {e \<in> set (all_items s). entry_hit e p})"
  oops

lemma refine_empty_state: "state_wf empty_state"
  oops

lemma refine_add_abstract_content: "(\<forall>fuel e s u. state_add fuel e s = Some u \<longrightarrow>
      set (all_items u) = insert e (set (all_items s)))"
  oops

lemma refine_remove_abstract_content: "(\<forall>fuel e s u. state_remove fuel e s = Some u \<longrightarrow>
      set (all_items u) = set (all_items s) - {e})"
  oops

lemma refine_duplicate_insert_noop: "(\<forall>fuel e s. e \<in> set (all_items s) \<longrightarrow> state_add fuel e s = Some s)"
  oops

lemma refine_missing_discard_noop: "(\<forall>fuel e s. e \<notin> set (all_items s) \<longrightarrow> state_discard fuel e s = Some s)"
  oops

lemma refine_missing_remove_error: "(\<forall>fuel e s. e \<notin> set (all_items s) \<longrightarrow>
      state_remove_status fuel e s = MissingInterval)"
  oops

lemma tree_singleton_insert_progress: "(\<forall>e. entry_valid e \<longrightarrow> (\<exists>u. state_add 1 e empty_state = Some u))"
  oops

lemma tree_rotation_content: "(\<forall>fuel t u. routing_ok t \<and> metadata_ok t \<and>
      tree_rotate fuel t = Some u \<longrightarrow> tree_entries u = tree_entries t)"
  oops

lemma tree_add_content: "(\<forall>fuel e t u. tree_wf t \<and> entry_valid e \<and>
      tree_add fuel e t = Some u \<longrightarrow>
      tree_entries u = insert e (tree_entries t))"
  oops

lemma tree_remove_content: "(\<forall>fuel e t u. tree_wf t \<and> e \<in> tree_entries t \<and>
      tree_remove fuel e t = Some u \<longrightarrow>
      tree_entries u = tree_entries t - {e})"
  oops

lemma tree_add_preserves_wf: "(\<forall>fuel e t u. tree_wf t \<and> entry_valid e \<and>
      tree_add fuel e t = Some u \<longrightarrow> tree_wf u)"
  oops

lemma tree_remove_preserves_wf: "(\<forall>fuel e t u. tree_wf t \<and> e \<in> tree_entries t \<and>
      tree_remove fuel e t = Some u \<longrightarrow> tree_wf u)"
  oops

lemma tree_add_fuel_adequacy: "(\<forall>t e. tree_wf t \<and> entry_valid e \<longrightarrow>
      (\<exists>fuel u. tree_add fuel e t = Some u))"
  oops

lemma tree_remove_fuel_adequacy: "(\<forall>t e. tree_wf t \<and> e \<in> tree_entries t \<longrightarrow>
      (\<exists>fuel u. tree_remove fuel e t = Some u))"
  oops

lemma refine_add_state_invariant: "(\<forall>fuel e s u. state_wf s \<and> entry_valid e \<and>
      state_add fuel e s = Some u \<longrightarrow> state_wf u)"
  oops

lemma refine_remove_state_invariant: "(\<forall>fuel e s u. state_wf s \<and> e \<in> set (all_items s) \<and>
      state_remove fuel e s = Some u \<longrightarrow> state_wf u)"
  oops

lemma refine_overlap_query: "(\<forall>s a b. state_wf s \<longrightarrow>
      tree_overlap a b s =
        {e \<in> set (all_items s). a < b \<and> lo e < b \<and> a < hi e})"
  oops

lemma refine_envelop_query: "(\<forall>s a b. state_wf s \<longrightarrow>
      tree_envelop a b s =
        {e \<in> set (all_items s). a < b \<and> a \<le> lo e \<and> hi e \<le> b})"
  oops

lemma refine_successful_trace_invariant: "(\<forall>fuel ops s u. state_wf s \<and> run_updates fuel ops s = Some u
       \<longrightarrow> state_wf u)"
  oops

lemma refine_successful_trace_point_query: "(\<forall>fuel ops s u p. state_wf s \<and> run_updates fuel ops s = Some u
      \<longrightarrow> state_at p u = {e \<in> set (all_items u). entry_hit e p})"
  oops

lemma refine_trace_abstract_content: "(\<forall>fuel ops s u. state_wf s \<and> run_updates fuel ops s = Some u
      \<longrightarrow> abstract_run_updates ops (set (all_items s)) =
          Some (tree_entries (root_node u)))"
  oops

end
