theory Interval_Extensions
  imports Interval_Model
begin

text \<open>Additional contracts, separate from VERO's 56 baseline contracts.
Only a lemma named verified_ext_* is evidence that a contract is proved.
The other ext_* definitions are fixed, well-typed goals, not assumptions.\<close>

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

text \<open>Graph vertices are input positions, not interval values: equal
intervals in different input positions remain distinct vertices. Empty
intervals are excluded. Edges express overlap or contact, since this model
implements merge_overlaps(strict=False).\<close>

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

lemma verified_ext_cut_all_coverage: ext_cut_all_coverage
  by (simp add: ext_cut_all_coverage_def cut_all_coverage)

lemma verified_ext_canonical_exact_measure: ext_canonical_exact_measure
  by (auto simp: ext_canonical_exact_measure_def intro: canonical_measure)

lemma verified_ext_merge_exact_measure: ext_merge_exact_measure
proof (unfold ext_merge_exact_measure_def, intro allI)
  fix xs
  have "coverage (merge_overlaps xs) = coverage xs"
    by (auto simp: merge_coverage)
  with canonical_measure[OF merge_is_canonical, of xs]
  show "total_len (merge_overlaps xs) = int (card (coverage xs))" by simp
qed

lemma finite_interval_vertices [simp]: "finite (interval_vertices xs)"
proof (rule finite_subset[of _ "{..<length xs}"])
  show "interval_vertices xs \<subseteq> {..<length xs}"
    by (auto simp: interval_vertices_def)
  show "finite {..<length xs}" by simp
qed

lemma finite_interval_components [simp]: "finite (interval_components xs)"
  by (simp add: interval_components_def)

lemma component_nonempty:
  "i \<in> interval_vertices xs \<Longrightarrow> interval_component xs i \<noteq> {}"
  by (auto simp: interval_component_def)

lemma component_subset:
  "interval_component xs i \<subseteq> interval_vertices xs"
  by (auto simp: interval_component_def)

lemma component_finite [simp]: "finite (interval_component xs i)"
  using component_subset finite_interval_vertices by (rule finite_subset)

lemma touch_edges_symmetric: "sym (touch_edges xs)"
  by (auto simp: sym_def touch_edges_def)

end
