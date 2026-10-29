theory Interval_Refinement
  imports Interval_Tree
begin

text \<open>
  A target definition is a proposition, not an established fact. Only the
  verified_* lemmas below carry checked evidence. In particular no update,
  rotation or fuel-adequacy property is introduced as a locale assumption.
\<close>

lemma tree_entries_finite [simp]: "finite (tree_entries t)"
  by (induction t) auto

lemma tree_entries_list [simp]: "set (tree_entry_list t) = tree_entries t"
  by (induction t) auto

lemma routing_entries_valid:
  "routing_ok t \<Longrightarrow> e \<in> tree_entries t \<Longrightarrow> entry_valid e"
  by (induction t) auto

lemma tree_at_sound:
  "tree_at p t \<subseteq> {e \<in> tree_entries t. entry_hit e p}"
  by (induction t) auto

definition tree_point_query_exact :: bool where
  "tree_point_query_exact \<longleftrightarrow>
    (\<forall>t p. routing_ok t \<longrightarrow>
      tree_at p t = {e \<in> tree_entries t. entry_hit e p})"

lemma tree_at_exact:
  assumes "routing_ok t"
  shows "tree_at p t = {e \<in> tree_entries t. entry_hit e p}"
  using assms
proof (induction t)
  case Empty
  then show ?case by simp
next
  case (Branch c s l r h b)
  have left: "tree_at p l = {e \<in> tree_entries l. entry_hit e p}"
    using Branch.IH(1) Branch.prems by simp
  have right: "tree_at p r = {e \<in> tree_entries r. entry_hit e p}"
    using Branch.IH(2) Branch.prems by simp
  show ?case using Branch.prems left right
    by (auto simp: entry_hit_def split: if_splits; fastforce)
qed

lemma verified_tree_point_query_exact: tree_point_query_exact
  unfolding tree_point_query_exact_def using tree_at_exact by blast

definition refine_payload_erasure_coverage :: bool where
  "refine_payload_erasure_coverage \<longleftrightarrow>
    (\<forall>t. coverage (map erase_payload (tree_entry_list t)) =
          coverage (tree_entry_list t))"

lemma verified_refine_payload_erasure_coverage: refine_payload_erasure_coverage
  by (auto simp: refine_payload_erasure_coverage_def coverage_def iv_points_def
      erase_payload_def)

definition refine_point_coverage_bridge :: bool where
  "refine_point_coverage_bridge \<longleftrightarrow>
    (\<forall>t p. routing_ok t \<longrightarrow>
      (covered p (tree_entry_list t) \<longleftrightarrow> tree_at p t \<noteq> {}))"

lemma verified_refine_point_coverage_bridge: refine_point_coverage_bridge
  unfolding refine_point_coverage_bridge_def
  using tree_at_exact
  by (auto simp: covered_def entry_hit_def)

definition tree_metadata_depth_exact :: bool where
  "tree_metadata_depth_exact \<longleftrightarrow>
    (\<forall>t. metadata_ok t \<longrightarrow> cached_depth t = actual_depth t)"

lemma metadata_depth_exact:
  "metadata_ok t \<Longrightarrow> cached_depth t = actual_depth t"
  by (induction t) auto

lemma verified_tree_metadata_depth_exact: tree_metadata_depth_exact
  unfolding tree_metadata_depth_exact_def using metadata_depth_exact by blast

definition refine_state_point_query :: bool where
  "refine_state_point_query \<longleftrightarrow>
    (\<forall>s p. state_wf s \<longrightarrow>
      state_at p s = {e \<in> set (all_items s). entry_hit e p})"

lemma verified_refine_state_point_query: refine_state_point_query
  unfolding refine_state_point_query_def state_at_def state_wf_def tree_wf_def
  using tree_at_exact by blast

definition refine_empty_state :: bool where
  "refine_empty_state \<longleftrightarrow> state_wf empty_state"

lemma verified_refine_empty_state: refine_empty_state
  by (simp add: refine_empty_state_def empty_state_def state_wf_def tree_wf_def
      boundary_count_def endpoint_count_def)

definition refine_add_abstract_content :: bool where
  "refine_add_abstract_content \<longleftrightarrow>
    (\<forall>fuel e s u. state_add fuel e s = Some u \<longrightarrow>
      set (all_items u) = insert e (set (all_items s)))"

lemma verified_refine_add_abstract_content: refine_add_abstract_content
  unfolding refine_add_abstract_content_def state_add_def
  by (auto split: if_splits option.splits)

definition refine_remove_abstract_content :: bool where
  "refine_remove_abstract_content \<longleftrightarrow>
    (\<forall>fuel e s u. state_remove fuel e s = Some u \<longrightarrow>
      set (all_items u) = set (all_items s) - {e})"

lemma verified_refine_remove_abstract_content: refine_remove_abstract_content
  unfolding refine_remove_abstract_content_def state_remove_def
  by (auto split: if_splits option.splits)

definition refine_duplicate_insert_noop :: bool where
  "refine_duplicate_insert_noop \<longleftrightarrow>
    (\<forall>fuel e s. e \<in> set (all_items s) \<longrightarrow> state_add fuel e s = Some s)"

lemma verified_refine_duplicate_insert_noop: refine_duplicate_insert_noop
  by (simp add: refine_duplicate_insert_noop_def state_add_def)

definition refine_missing_discard_noop :: bool where
  "refine_missing_discard_noop \<longleftrightarrow>
    (\<forall>fuel e s. e \<notin> set (all_items s) \<longrightarrow> state_discard fuel e s = Some s)"

lemma verified_refine_missing_discard_noop: refine_missing_discard_noop
  by (simp add: refine_missing_discard_noop_def state_discard_def)

definition refine_missing_remove_error :: bool where
  "refine_missing_remove_error \<longleftrightarrow>
    (\<forall>fuel e s. e \<notin> set (all_items s) \<longrightarrow>
      state_remove_status fuel e s = MissingInterval)"

lemma verified_refine_missing_remove_error: refine_missing_remove_error
  by (simp add: refine_missing_remove_error_def state_remove_status_def)

definition tree_singleton_insert_progress :: bool where
  "tree_singleton_insert_progress \<longleftrightarrow>
    (\<forall>e. entry_valid e \<longrightarrow> (\<exists>u. state_add 1 e empty_state = Some u))"

lemma verified_tree_singleton_insert_progress: tree_singleton_insert_progress
  by (simp add: tree_singleton_insert_progress_def state_add_def empty_state_def
      tree_add_def)

text \<open>
  Targets without a verified_* lemma remain OPEN. They must be supplied as
  goals, never imported as facts. Partial correctness and successful termination
  are separate targets so that an implementation returning None everywhere
  cannot satisfy the full suite.
\<close>

definition tree_rotation_content :: bool where
  "tree_rotation_content \<longleftrightarrow>
    (\<forall>fuel t u. routing_ok t \<and> metadata_ok t \<and>
      tree_rotate fuel t = Some u \<longrightarrow> tree_entries u = tree_entries t)"

definition tree_add_content :: bool where
  "tree_add_content \<longleftrightarrow>
    (\<forall>fuel e t u. tree_wf t \<and> entry_valid e \<and>
      tree_add fuel e t = Some u \<longrightarrow>
      tree_entries u = insert e (tree_entries t))"

definition tree_remove_content :: bool where
  "tree_remove_content \<longleftrightarrow>
    (\<forall>fuel e t u. tree_wf t \<and> e \<in> tree_entries t \<and>
      tree_remove fuel e t = Some u \<longrightarrow>
      tree_entries u = tree_entries t - {e})"

definition tree_add_preserves_wf :: bool where
  "tree_add_preserves_wf \<longleftrightarrow>
    (\<forall>fuel e t u. tree_wf t \<and> entry_valid e \<and>
      tree_add fuel e t = Some u \<longrightarrow> tree_wf u)"

definition tree_remove_preserves_wf :: bool where
  "tree_remove_preserves_wf \<longleftrightarrow>
    (\<forall>fuel e t u. tree_wf t \<and> e \<in> tree_entries t \<and>
      tree_remove fuel e t = Some u \<longrightarrow> tree_wf u)"

definition tree_add_fuel_adequacy :: bool where
  "tree_add_fuel_adequacy \<longleftrightarrow>
    (\<forall>t e. tree_wf t \<and> entry_valid e \<longrightarrow>
      (\<exists>fuel u. tree_add fuel e t = Some u))"

definition tree_remove_fuel_adequacy :: bool where
  "tree_remove_fuel_adequacy \<longleftrightarrow>
    (\<forall>t e. tree_wf t \<and> e \<in> tree_entries t \<longrightarrow>
      (\<exists>fuel u. tree_remove fuel e t = Some u))"

definition refine_add_state_invariant :: bool where
  "refine_add_state_invariant \<longleftrightarrow>
    (\<forall>fuel e s u. state_wf s \<and> entry_valid e \<and>
      state_add fuel e s = Some u \<longrightarrow> state_wf u)"

definition refine_remove_state_invariant :: bool where
  "refine_remove_state_invariant \<longleftrightarrow>
    (\<forall>fuel e s u. state_wf s \<and> e \<in> set (all_items s) \<and>
      state_remove fuel e s = Some u \<longrightarrow> state_wf u)"

definition refine_overlap_query :: bool where
  "refine_overlap_query \<longleftrightarrow>
    (\<forall>s a b. state_wf s \<longrightarrow>
      tree_overlap a b s =
        {e \<in> set (all_items s). a < b \<and> lo e < b \<and> a < hi e})"

lemma positive_boundary_is_key:
  assumes "0 < boundary_count bs k"
  shows "k \<in> set (boundary_keys bs)"
proof -
  obtain n where n: "map_of bs k = Some n" "0 < n"
    using assms unfolding boundary_count_def by (cases "map_of bs k") auto
  have member: "(k,n) \<in> set bs" by (rule map_of_SomeD[OF n(1)])
  have image: "fst (k,n) \<in> fst ` {x \<in> set bs. 0 < snd x}"
  proof (rule imageI)
    show "(k,n) \<in> {x \<in> set bs. 0 < snd x}" using member n(2) by simp
  qed
  show ?thesis using image unfolding boundary_keys_def by simp
qed

lemma state_begin_is_boundary:
  assumes wf: "state_wf s" and member: "e \<in> set (all_items s)"
  shows "lo e \<in> set (boundary_keys (boundaries s))"
proof -
  have nonempty: "filter (\<lambda>x. lo x = lo e) (all_items s) \<noteq> []"
    using member by (auto simp: filter_empty_conv)
  have "0 < endpoint_count (all_items s) (lo e)"
    using nonempty unfolding endpoint_count_def by simp
  then have "0 < boundary_count (boundaries s) (lo e)"
    using wf unfolding state_wf_def by auto
  then show ?thesis by (rule positive_boundary_is_key)
qed

lemma state_entry_valid:
  "state_wf s \<Longrightarrow> e \<in> set (all_items s) \<Longrightarrow> entry_valid e"
  unfolding state_wf_def tree_wf_def using routing_entries_valid by blast

lemma state_tree_at_exact:
  assumes "state_wf s"
  shows "tree_at p (root_node s) = {e \<in> set (all_items s). entry_hit e p}"
  using assms tree_at_exact unfolding state_wf_def tree_wf_def by blast

lemma overlap_query_exact:
  assumes wf: "state_wf s"
  shows "tree_overlap a b s =
    {e \<in> set (all_items s). a < b \<and> lo e < b \<and> a < hi e}"
proof (cases "a < b")
  case False
  then show ?thesis by (auto simp: tree_overlap_def)
next
  case True
  have point: "tree_at p (root_node s) = {e \<in> set (all_items s). entry_hit e p}" for p
    using state_tree_at_exact wf by blast
  show ?thesis
  proof (rule set_eqI, rule iffI)
    fix e
    assume "e \<in> tree_overlap a b s"
    then obtain p where p: "e \<in> set (all_items s)" "entry_hit e p" "a \<le> p" "p < b"
      using True by (auto simp: tree_overlap_def point)
    then show "e \<in> {e \<in> set (all_items s). a < b \<and> lo e < b \<and> a < hi e}"
      using True unfolding entry_hit_def by auto
  next
    fix e
    assume "e \<in> {e \<in> set (all_items s). a < b \<and> lo e < b \<and> a < hi e}"
    then have e: "e \<in> set (all_items s)" "lo e < b" "a < hi e" by auto
    show "e \<in> tree_overlap a b s"
    proof (cases "lo e \<le> a")
      case True
      then show ?thesis using e \<open>a < b\<close>
        by (auto simp: tree_overlap_def point entry_hit_def)
    next
      case False
      have key: "lo e \<in> set (boundary_keys (boundaries s))"
        using state_begin_is_boundary wf e(1) by blast
      have valid: "lo e < hi e" using state_entry_valid wf e(1)
        unfolding entry_valid_def by blast
      have hit: "e \<in> tree_at (lo e) (root_node s)"
        using e(1) valid by (simp add: point entry_hit_def)
      show ?thesis using key hit False e(2) \<open>a < b\<close>
        unfolding tree_overlap_def by auto
    qed
  qed
qed

lemma verified_refine_overlap_query: refine_overlap_query
  unfolding refine_overlap_query_def using overlap_query_exact by blast

definition refine_envelop_query :: bool where
  "refine_envelop_query \<longleftrightarrow>
    (\<forall>s a b. state_wf s \<longrightarrow>
      tree_envelop a b s =
        {e \<in> set (all_items s). a < b \<and> a \<le> lo e \<and> hi e \<le> b})"

lemma verified_refine_envelop_query: refine_envelop_query
  unfolding refine_envelop_query_def
proof (intro allI impI)
  fix s a b
  assume wf: "state_wf s"
  have overlap: "tree_overlap a b s =
      {e \<in> set (all_items s). a < b \<and> lo e < b \<and> a < hi e}"
    by (rule overlap_query_exact[OF wf])
  have valid: "lo e < hi e" if "e \<in> set (all_items s)" for e
    using state_entry_valid[OF wf that] unfolding entry_valid_def .
  show "tree_envelop a b s =
      {e \<in> set (all_items s). a < b \<and> a \<le> lo e \<and> hi e \<le> b}"
    unfolding tree_envelop_def overlap
    using valid by (auto; fastforce)
qed

definition refine_successful_trace_invariant :: bool where
  "refine_successful_trace_invariant \<longleftrightarrow>
    (\<forall>fuel ops s u. state_wf s \<and> run_updates fuel ops s = Some u
       \<longrightarrow> state_wf u)"

definition refine_successful_trace_point_query :: bool where
  "refine_successful_trace_point_query \<longleftrightarrow>
    (\<forall>fuel ops s u p. state_wf s \<and> run_updates fuel ops s = Some u
      \<longrightarrow> state_at p u = {e \<in> set (all_items u). entry_hit e p})"

text \<open>
  Independent abstract trace semantics. These functions only inspect the input
  set and the next public operation; they do not call the tree implementation,
  read a concrete state, or use its cached all_items as a final-state oracle.
\<close>

fun abstract_apply_update :: "tree_update \<Rightarrow> entry set \<Rightarrow> entry set option" where
  "abstract_apply_update (Insert e) es =
    (if e \<in> es then Some es
     else if entry_valid e then Some (insert e es) else None)"
| "abstract_apply_update (Delete e) es =
    (if e \<in> es then Some (es - {e}) else None)"
| "abstract_apply_update (Discard e) es = Some (es - {e})"

fun abstract_run_updates :: "tree_update list \<Rightarrow> entry set \<Rightarrow> entry set option" where
  "abstract_run_updates [] es = Some es"
| "abstract_run_updates (op#ops) es =
    (case abstract_apply_update op es of None \<Rightarrow> None
       | Some es' \<Rightarrow> abstract_run_updates ops es')"

definition refine_trace_abstract_content :: bool where
  "refine_trace_abstract_content \<longleftrightarrow>
    (\<forall>fuel ops s u. state_wf s \<and> run_updates fuel ops s = Some u
      \<longrightarrow> abstract_run_updates ops (set (all_items s)) =
          Some (tree_entries (root_node u)))"

end
