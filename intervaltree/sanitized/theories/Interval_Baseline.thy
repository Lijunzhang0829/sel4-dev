theory Interval_Baseline
  imports Interval_Model
begin

definition covered_count :: "int \<Rightarrow> iv list \<Rightarrow> nat" where
  "covered_count x xs = length (filter (\<lambda>i. lo i \<le> x \<and> x < hi i) xs)"

definition spec_overlaps_symm :: bool where
  "spec_overlaps_symm \<longleftrightarrow> (\<forall>a b. overlaps a b = overlaps b a)"

definition spec_overlaps_self_nonempty :: bool where
  "spec_overlaps_self_nonempty \<longleftrightarrow> (\<forall>a. overlaps a a = (lo a < hi a))"

definition spec_overlaps_self_empty :: bool where
  "spec_overlaps_self_empty \<longleftrightarrow> (\<forall>a. \<not> lo a < hi a \<longrightarrow> \<not> overlaps a a)"

definition spec_overlaps_disjoint_false :: bool where
  "spec_overlaps_disjoint_false \<longleftrightarrow>
    (\<forall>a b. (\<not> overlaps a b) = (hi a \<le> lo b \<or> hi b \<le> lo a))"

definition spec_overlaps_iff_common_point :: bool where
  "spec_overlaps_iff_common_point \<longleftrightarrow>
    (\<forall>a b. lo a < hi a \<longrightarrow> lo b < hi b \<longrightarrow>
      overlaps a b = (\<exists>x. covered x [a] \<and> covered x [b]))"

definition spec_merge_coverage :: bool where
  "spec_merge_coverage \<longleftrightarrow> (\<forall>xs x. covered x (merge_overlaps xs) = covered x xs)"

definition spec_merge_disjoint_strict :: bool where
  "spec_merge_disjoint_strict \<longleftrightarrow> (\<forall>xs.
    (\<forall>i\<in>set (merge_overlaps xs). lo i < hi i) \<and>
    (\<forall>k. Suc k < length (merge_overlaps xs) \<longrightarrow>
      hi (merge_overlaps xs ! k) < lo (merge_overlaps xs ! Suc k)))"

definition spec_merge_nil :: bool where
  "spec_merge_nil \<longleftrightarrow> merge_overlaps [] = []"

definition spec_merge_singleton :: bool where
  "spec_merge_singleton \<longleftrightarrow> (\<forall>a.
    merge_overlaps [a] = (if lo a < hi a then [a] else []))"

definition spec_merge_all_empty_nil :: bool where
  "spec_merge_all_empty_nil \<longleftrightarrow> (\<forall>xs.
    (merge_overlaps xs = []) = (\<forall>a\<in>set xs. \<not> lo a < hi a))"

definition spec_merge_output_nonempty :: bool where
  "spec_merge_output_nonempty \<longleftrightarrow>
    (\<forall>xs. \<forall>a\<in>set (merge_overlaps xs). lo a < hi a)"

definition spec_merge_sorted_lo :: bool where
  "spec_merge_sorted_lo \<longleftrightarrow> (\<forall>xs k.
    Suc k < length (merge_overlaps xs) \<longrightarrow>
    lo (merge_overlaps xs ! k) < lo (merge_overlaps xs ! Suc k))"

definition spec_merge_length_le :: bool where
  "spec_merge_length_le \<longleftrightarrow> (\<forall>xs.
    length (merge_overlaps xs) \<le> length (filter (\<lambda>i. lo i < hi i) xs))"

definition spec_merge_idempotent :: bool where
  "spec_merge_idempotent \<longleftrightarrow> (\<forall>xs x.
    covered x (merge_overlaps (merge_overlaps xs)) = covered x (merge_overlaps xs))"

definition spec_merge_exact_idempotent :: bool where
  "spec_merge_exact_idempotent \<longleftrightarrow>
    (\<forall>xs. merge_overlaps (merge_overlaps xs) = merge_overlaps xs)"

definition spec_merge_totalLen_le :: bool where
  "spec_merge_totalLen_le \<longleftrightarrow>
    (\<forall>xs. total_len (merge_overlaps xs) \<le> clamped_total_len xs)"

definition spec_merge_totalLen_disjoint_eq :: bool where
  "spec_merge_totalLen_disjoint_eq \<longleftrightarrow> (\<forall>xs.
    canonical_intervals xs \<longrightarrow> total_len (merge_overlaps xs) = total_len xs)"

definition spec_merge_hull_lo :: bool where
  "spec_merge_hull_lo \<longleftrightarrow> (\<forall>xs out_iv rest.
    merge_overlaps xs = out_iv # rest \<longrightarrow>
    (\<forall>i\<in>set xs. lo i < hi i \<longrightarrow> lo out_iv \<le> lo i) \<and>
    (\<exists>i\<in>set xs. lo i < hi i \<and> lo out_iv = lo i))"

definition spec_merge_hull_hi :: bool where
  "spec_merge_hull_hi \<longleftrightarrow> (\<forall>xs pre last.
    merge_overlaps xs = pre @ [last] \<longrightarrow>
    (\<forall>i\<in>set xs. lo i < hi i \<longrightarrow> hi i \<le> hi last) \<and>
    (\<exists>i\<in>set xs. lo i < hi i \<and> hi last = hi i))"

definition spec_merge_chop_coverage :: bool where
  "spec_merge_chop_coverage \<longleftrightarrow> (\<forall>b e xs x.
    covered x (merge_overlaps (chop b e xs)) =
    (covered x (merge_overlaps xs) \<and> \<not> (b \<le> x \<and> x < e)))"

definition spec_merge_reverse_eq :: bool where
  "spec_merge_reverse_eq \<longleftrightarrow>
    (\<forall>xs. merge_overlaps xs = merge_overlaps (rev xs))"

definition spec_chop_removes :: bool where
  "spec_chop_removes \<longleftrightarrow> (\<forall>b e xs x.
    b \<le> x \<longrightarrow> x < e \<longrightarrow> \<not> covered x (chop b e xs))"

definition spec_chop_pointwise :: bool where
  "spec_chop_pointwise \<longleftrightarrow> (\<forall>b e xs x.
    covered x (chop b e xs) = (covered x xs \<and> \<not> (b \<le> x \<and> x < e)))"

definition spec_chop_empty_range_id :: bool where
  "spec_chop_empty_range_id \<longleftrightarrow> (\<forall>b e xs x.
    e \<le> b \<longrightarrow> covered x (chop b e xs) = covered x xs)"

definition spec_chop_idempotent :: bool where
  "spec_chop_idempotent \<longleftrightarrow> (\<forall>b e xs x.
    covered x (chop b e (chop b e xs)) = covered x (chop b e xs))"

definition spec_merge_canonical_unique :: bool where
  "spec_merge_canonical_unique \<longleftrightarrow> (\<forall>xs c.
    canonical_intervals c \<longrightarrow> (\<forall>x. covered x c = covered x xs) \<longrightarrow>
    merge_overlaps xs = c)"

definition spec_merge_coverage_determines :: bool where
  "spec_merge_coverage_determines \<longleftrightarrow> (\<forall>a b.
    (\<forall>x. covered x a = covered x b) \<longrightarrow> merge_overlaps a = merge_overlaps b)"

definition spec_merge_saturated_lo :: bool where
  "spec_merge_saturated_lo \<longleftrightarrow> (\<forall>xs. \<forall>out_iv\<in>set (merge_overlaps xs).
    \<exists>i\<in>set xs. lo i < hi i \<and> lo out_iv = lo i)"

definition spec_merge_concat_closure :: bool where
  "spec_merge_concat_closure \<longleftrightarrow> (\<forall>xs ys.
    merge_overlaps (merge_overlaps xs @ merge_overlaps ys) = merge_overlaps (xs @ ys))"

definition spec_merge_is_canonical :: bool where
  "spec_merge_is_canonical \<longleftrightarrow>
    (\<forall>xs. canonical_intervals (merge_overlaps xs))"

definition spec_merge_saturated_hi :: bool where
  "spec_merge_saturated_hi \<longleftrightarrow> (\<forall>xs. \<forall>out_iv\<in>set (merge_overlaps xs).
    \<exists>i\<in>set xs. lo i < hi i \<and> hi out_iv = hi i)"

definition spec_merge_run_solid :: bool where
  "spec_merge_run_solid \<longleftrightarrow> (\<forall>xs x. \<forall>out_iv\<in>set (merge_overlaps xs).
    lo out_iv \<le> x \<longrightarrow> x < hi out_iv \<longrightarrow> covered x xs)"

definition spec_merge_lo_pred_uncovered :: bool where
  "spec_merge_lo_pred_uncovered \<longleftrightarrow>
    (\<forall>xs. \<forall>out_iv\<in>set (merge_overlaps xs). \<not> covered (lo out_iv - 1) xs)"

definition spec_merge_hi_uncovered :: bool where
  "spec_merge_hi_uncovered \<longleftrightarrow>
    (\<forall>xs. \<forall>out_iv\<in>set (merge_overlaps xs). \<not> covered (hi out_iv) xs)"

definition spec_merge_append_coverage :: bool where
  "spec_merge_append_coverage \<longleftrightarrow> (\<forall>xs ys x.
    covered x (merge_overlaps (xs @ ys)) =
    (covered x (merge_overlaps xs) \<or> covered x (merge_overlaps ys)))"

definition spec_merge_concat_closure_right :: bool where
  "spec_merge_concat_closure_right \<longleftrightarrow> (\<forall>xs ys.
    merge_overlaps (xs @ merge_overlaps ys) = merge_overlaps (xs @ ys))"

definition spec_merge_self_union_idem :: bool where
  "spec_merge_self_union_idem \<longleftrightarrow> (\<forall>xs.
    merge_overlaps (merge_overlaps xs @ merge_overlaps xs) = merge_overlaps xs)"

definition spec_merge_chop_merge :: bool where
  "spec_merge_chop_merge \<longleftrightarrow> (\<forall>b e xs.
    merge_overlaps (chop b e (merge_overlaps xs)) = merge_overlaps (chop b e xs))"

definition spec_merge_chop_idempotent :: bool where
  "spec_merge_chop_idempotent \<longleftrightarrow> (\<forall>b e xs.
    merge_overlaps (chop b e (chop b e xs)) = merge_overlaps (chop b e xs))"

definition spec_merge_chop_coverage_determines :: bool where
  "spec_merge_chop_coverage_determines \<longleftrightarrow> (\<forall>b e a c.
    (\<forall>x. covered x a = covered x c) \<longrightarrow>
    merge_overlaps (chop b e a) = merge_overlaps (chop b e c))"

definition spec_merge_coveredCount_le_one :: bool where
  "spec_merge_coveredCount_le_one \<longleftrightarrow>
    (\<forall>xs x. covered_count x (merge_overlaps xs) \<le> 1)"

definition spec_merge_coveredCount_exact :: bool where
  "spec_merge_coveredCount_exact \<longleftrightarrow> (\<forall>xs x.
    covered_count x (merge_overlaps xs) = (if covered x xs then 1 else 0))"

definition spec_merge_loEndpoints_nodup :: bool where
  "spec_merge_loEndpoints_nodup \<longleftrightarrow>
    (\<forall>xs. distinct (map lo (merge_overlaps xs)))"

definition spec_merge_getElem_lo_split :: bool where
  "spec_merge_getElem_lo_split \<longleftrightarrow> (\<forall>xs k.
    k < length (merge_overlaps xs) \<longrightarrow>
    \<not> covered (lo (merge_overlaps xs ! k)) (take k (merge_overlaps xs)) \<and>
    covered (lo (merge_overlaps xs ! k)) (drop k (merge_overlaps xs)))"

definition spec_merge_perm_eq_any :: bool where
  "spec_merge_perm_eq_any \<longleftrightarrow>
    (\<forall>xs ys. (\<forall>i. count_list xs i = count_list ys i) \<longrightarrow>
      merge_overlaps xs = merge_overlaps ys)"

definition spec_merge_pairwise_strict_gap_all :: bool where
  "spec_merge_pairwise_strict_gap_all \<longleftrightarrow> (\<forall>xs i j.
    i < length (merge_overlaps xs) \<longrightarrow> j < length (merge_overlaps xs) \<longrightarrow>
    i < j \<longrightarrow> hi (merge_overlaps xs ! i) < lo (merge_overlaps xs ! j))"

definition spec_merge_consecutive_gap_witness :: bool where
  "spec_merge_consecutive_gap_witness \<longleftrightarrow> (\<forall>xs i.
    Suc i < length (merge_overlaps xs) \<longrightarrow> (\<exists>x.
    hi (merge_overlaps xs ! i) \<le> x \<and> x < lo (merge_overlaps xs ! Suc i) \<and>
    \<not> covered x (merge_overlaps xs)))"

definition spec_merge_coverage_monotone :: bool where
  "spec_merge_coverage_monotone \<longleftrightarrow> (\<forall>xs ys.
    (\<forall>x. covered x xs \<longrightarrow> covered x ys) \<longrightarrow>
    (\<forall>x. covered x (merge_overlaps xs) \<longrightarrow> covered x (merge_overlaps ys)))"

definition spec_overlaps_merge_same_run :: bool where
  "spec_overlaps_merge_same_run \<longleftrightarrow> (\<forall>a b.
    lo a < hi a \<longrightarrow> lo b < hi b \<longrightarrow> overlaps a b \<longrightarrow>
    (\<exists>out_iv\<in>set (merge_overlaps [a,b]).
      (\<forall>x. covered x [a] \<longrightarrow> covered x [out_iv]) \<and>
      (\<forall>x. covered x [b] \<longrightarrow> covered x [out_iv])))"

definition spec_chop_canonical_preserves_canonical :: bool where
  "spec_chop_canonical_preserves_canonical \<longleftrightarrow> (\<forall>b e xs.
    b < e \<longrightarrow> canonical_intervals xs \<longrightarrow>
    canonical_intervals (chop b e xs))"

definition spec_chop_canonical_sorted_lo :: bool where
  "spec_chop_canonical_sorted_lo \<longleftrightarrow> (\<forall>b e xs.
    b < e \<longrightarrow> canonical_intervals xs \<longrightarrow>
    (\<forall>i. Suc i < length (chop b e xs) \<longrightarrow>
      lo (chop b e xs ! i) < lo (chop b e xs ! Suc i)))"

definition spec_chop_canonical_pairwise_strict_gap :: bool where
  "spec_chop_canonical_pairwise_strict_gap \<longleftrightarrow> (\<forall>b e xs.
    b < e \<longrightarrow> canonical_intervals xs \<longrightarrow>
    (\<forall>i j. i < length (chop b e xs) \<longrightarrow> j < length (chop b e xs) \<longrightarrow>
      i < j \<longrightarrow> hi (chop b e xs ! i) < lo (chop b e xs ! j)))"

definition spec_chop_canonical_merge_fixpoint :: bool where
  "spec_chop_canonical_merge_fixpoint \<longleftrightarrow> (\<forall>b e xs.
    b < e \<longrightarrow> canonical_intervals xs \<longrightarrow>
    merge_overlaps (chop b e xs) = chop b e xs)"

definition spec_chop_totalLen_le_clamped :: bool where
  "spec_chop_totalLen_le_clamped \<longleftrightarrow> (\<forall>b e xs.
    b \<le> e \<longrightarrow> total_len (chop b e xs) \<le> clamped_total_len xs)"

definition spec_chop_append_coverage :: bool where
  "spec_chop_append_coverage \<longleftrightarrow> (\<forall>b e xs ys x.
    covered x (chop b e (xs @ ys)) = (covered x (chop b e xs) \<or> covered x (chop b e ys)))"

definition spec_chop_coveredCount_cut_complement :: bool where
  "spec_chop_coveredCount_cut_complement \<longleftrightarrow> (\<forall>b e xs x.
    b \<le> e \<longrightarrow> covered_count x (chop b e xs) =
      (if b \<le> x \<and> x < e then 0 else covered_count x xs))"

end
