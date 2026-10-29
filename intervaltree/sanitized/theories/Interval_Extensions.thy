theory Interval_Extensions
  imports Interval_Model
begin

definition proper_cuts :: "iv list \<Rightarrow> bool" where
  "proper_cuts rs \<longleftrightarrow> (\<forall>r\<in>set rs. lo r < hi r)"

definition ext_merge_exact_measure :: bool where
  "ext_merge_exact_measure \<longleftrightarrow>
    (\<forall>xs. total_len (merge_overlaps xs) = int (card (coverage xs)))"

definition ext_canonical_exact_measure :: bool where
  "ext_canonical_exact_measure \<longleftrightarrow>
    (\<forall>xs::iv list. canonical_intervals xs \<longrightarrow>
      total_len xs = int (card (coverage xs)))"

definition ext_cut_all_coverage :: bool where
  "ext_cut_all_coverage \<longleftrightarrow>
    (\<forall>rs xs. coverage (cut_all rs xs) = coverage xs - coverage rs)"

definition ext_cut_all_exact_measure :: bool where
  "ext_cut_all_exact_measure \<longleftrightarrow>
    (\<forall>rs xs. proper_cuts rs \<longrightarrow> canonical_intervals xs \<longrightarrow>
      total_len (cut_all rs xs) = int (card (coverage xs - coverage rs)))"

definition ext_one_cut_fragment_bound :: bool where
  "ext_one_cut_fragment_bound \<longleftrightarrow>
    (\<forall>b e xs. b < e \<longrightarrow> canonical_intervals xs \<longrightarrow>
      length (chop b e xs) \<le> length xs + 1)"

definition ext_cut_all_fragment_bound :: bool where
  "ext_cut_all_fragment_bound \<longleftrightarrow>
    (\<forall>rs xs. proper_cuts rs \<longrightarrow> canonical_intervals xs \<longrightarrow>
      length (cut_all rs xs) \<le> length xs + length rs)"

definition interval_vertices :: "iv list \<Rightarrow> nat set" where
  "interval_vertices xs = {k. k < length xs \<and> lo (xs ! k) < hi (xs ! k)}"

definition touch_edges :: "iv list \<Rightarrow> (nat \<times> nat) set" where
  "touch_edges xs = {(i,j). i \<in> interval_vertices xs \<and>
    j \<in> interval_vertices xs \<and>
    lo (xs ! i) \<le> hi (xs ! j) \<and> lo (xs ! j) \<le> hi (xs ! i)}"

definition interval_component :: "iv list \<Rightarrow> nat \<Rightarrow> nat set" where
  "interval_component xs i = {j \<in> interval_vertices xs. (i,j) \<in> (touch_edges xs)\<^sup>*}"

definition interval_components :: "iv list \<Rightarrow> nat set set" where
  "interval_components xs = interval_component xs ` interval_vertices xs"

definition component_hull :: "iv list \<Rightarrow> nat set \<Rightarrow> iv" where
  "component_hull xs c =
    \<lparr>lo = Min ((\<lambda>i. lo (xs ! i)) ` c), hi = Max ((\<lambda>i. hi (xs ! i)) ` c)\<rparr>"

definition ext_merge_component_count :: bool where
  "ext_merge_component_count \<longleftrightarrow>
    (\<forall>xs. length (merge_overlaps xs) = card (interval_components xs))"

definition ext_merge_component_hulls :: bool where
  "ext_merge_component_hulls \<longleftrightarrow>
    (\<forall>xs. set (merge_overlaps xs) = component_hull xs ` interval_components xs)"

definition ext_merge_component_bijection :: bool where
  "ext_merge_component_bijection \<longleftrightarrow>
    (\<forall>xs. bij_betw (component_hull xs) (interval_components xs)
      (set (merge_overlaps xs)))"

end
