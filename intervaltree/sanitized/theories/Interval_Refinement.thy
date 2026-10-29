theory Interval_Refinement
  imports Interval_Tree
begin

definition tree_point_query_exact :: bool where
  "tree_point_query_exact \<longleftrightarrow>
    (\<forall>t p. routing_ok t \<longrightarrow>
      tree_at p t = {e \<in> tree_entries t. entry_hit e p})"

definition refine_payload_erasure_coverage :: bool where
  "refine_payload_erasure_coverage \<longleftrightarrow>
    (\<forall>t. coverage (map erase_payload (tree_entry_list t)) =
          coverage (tree_entry_list t))"

definition refine_point_coverage_bridge :: bool where
  "refine_point_coverage_bridge \<longleftrightarrow>
    (\<forall>t p. routing_ok t \<longrightarrow>
      (covered p (tree_entry_list t) \<longleftrightarrow> tree_at p t \<noteq> {}))"

definition tree_metadata_depth_exact :: bool where
  "tree_metadata_depth_exact \<longleftrightarrow>
    (\<forall>t. metadata_ok t \<longrightarrow> cached_depth t = actual_depth t)"

definition refine_state_point_query :: bool where
  "refine_state_point_query \<longleftrightarrow>
    (\<forall>s p. state_wf s \<longrightarrow>
      state_at p s = {e \<in> set (all_items s). entry_hit e p})"

definition refine_empty_state :: bool where
  "refine_empty_state \<longleftrightarrow> state_wf empty_state"

definition refine_add_abstract_content :: bool where
  "refine_add_abstract_content \<longleftrightarrow>
    (\<forall>fuel e s u. state_add fuel e s = Some u \<longrightarrow>
      set (all_items u) = insert e (set (all_items s)))"

definition refine_remove_abstract_content :: bool where
  "refine_remove_abstract_content \<longleftrightarrow>
    (\<forall>fuel e s u. state_remove fuel e s = Some u \<longrightarrow>
      set (all_items u) = set (all_items s) - {e})"

definition refine_duplicate_insert_noop :: bool where
  "refine_duplicate_insert_noop \<longleftrightarrow>
    (\<forall>fuel e s. e \<in> set (all_items s) \<longrightarrow> state_add fuel e s = Some s)"

definition refine_missing_discard_noop :: bool where
  "refine_missing_discard_noop \<longleftrightarrow>
    (\<forall>fuel e s. e \<notin> set (all_items s) \<longrightarrow> state_discard fuel e s = Some s)"

definition refine_missing_remove_error :: bool where
  "refine_missing_remove_error \<longleftrightarrow>
    (\<forall>fuel e s. e \<notin> set (all_items s) \<longrightarrow>
      state_remove_status fuel e s = MissingInterval)"

definition tree_singleton_insert_progress :: bool where
  "tree_singleton_insert_progress \<longleftrightarrow>
    (\<forall>e. entry_valid e \<longrightarrow> (\<exists>u. state_add 1 e empty_state = Some u))"

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

definition refine_envelop_query :: bool where
  "refine_envelop_query \<longleftrightarrow>
    (\<forall>s a b. state_wf s \<longrightarrow>
      tree_envelop a b s =
        {e \<in> set (all_items s). a < b \<and> a \<le> lo e \<and> hi e \<le> b})"

definition refine_successful_trace_invariant :: bool where
  "refine_successful_trace_invariant \<longleftrightarrow>
    (\<forall>fuel ops s u. state_wf s \<and> run_updates fuel ops s = Some u
       \<longrightarrow> state_wf u)"

definition refine_successful_trace_point_query :: bool where
  "refine_successful_trace_point_query \<longleftrightarrow>
    (\<forall>fuel ops s u p. state_wf s \<and> run_updates fuel ops s = Some u
      \<longrightarrow> state_at p u = {e \<in> set (all_items u). entry_hit e p})"

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
