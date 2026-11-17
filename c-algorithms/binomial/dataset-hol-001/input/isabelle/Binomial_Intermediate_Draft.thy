theory Binomial_Intermediate_Draft
  imports "Calg_Project_Base.Project_Input"
begin

definition bn_review_stack_frame :: "addr set \<Rightarrow> globals \<Rightarrow> globals \<Rightarrow> bool" where
  "bn_review_stack_frame S s t \<longleftrightarrow>
    phantom_machine_state_' t = phantom_machine_state_' s \<and>
    (\<forall>a. a \<notin> S \<longrightarrow> hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a)"

definition bn_review_root_array_view ::
  "globals \<Rightarrow> StrictC'_BinomialTree_C ptr ptr \<Rightarrow> bn_shape option list \<Rightarrow> bool" where
  "bn_review_root_array_view s a F \<longleftrightarrow>
    (\<forall>i < length F. c_guard (a +\<^sub>p int i) \<and>
      h_val (hrs_mem (t_hrs_' s)) (a +\<^sub>p int i) = case_option NULL bn_root (F ! i))"

definition bn_review_header_view ::
  "globals \<Rightarrow> StrictC'_BinomialHeap_C ptr \<Rightarrow> bn_shape option list \<Rightarrow> bool" where
  "bn_review_header_view s h F \<longleftrightarrow>
    c_guard h \<and>
    heap_type_C (bn_heap s h) = BINOMIAL_HEAP_TYPE_MIN \<and>
    compare_func_C (bn_heap s h) = binomial_heap_input.int_compare \<and>
    unat (roots_length_C (bn_heap s h)) = length F \<and>
    bn_review_root_array_view s (roots_C (bn_heap s h)) F"

definition bn_review_reference_ledger ::
  "globals \<Rightarrow> StrictC'_BinomialTree_C ptr set \<Rightarrow>
    (StrictC'_BinomialTree_C ptr \<Rightarrow> StrictC'_BinomialTree_C ptr list) \<Rightarrow>
    StrictC'_BinomialTree_C ptr multiset \<Rightarrow> bool" where
  "bn_review_reference_ledger s N bn_edges H \<longleftrightarrow>
    finite N \<and> set_mset H \<subseteq> N \<and>
    (\<forall>p \<in> N. c_guard p \<and>
      live_blocks (phantom_machine_state_' s) (ptr_val p) =
        Some (size_of TYPE(StrictC'_BinomialTree_C)) \<and>
      set (bn_edges p) \<subseteq> N \<and>
      count H p + (\<Sum>q \<in> N. count (mset (bn_edges q)) p) < 2 ^ 16 \<and>
      unat (refcount_C (bn_tree s p)) =
        count H p + (\<Sum>q \<in> N. count (mset (bn_edges q)) p))"

definition bn_review_scan_prefix ::
  "globals \<Rightarrow> bn_shape option list \<Rightarrow> nat \<Rightarrow> nat option \<Rightarrow> bool" where
  "bn_review_scan_prefix s F i j \<longleftrightarrow>
    i \<le> length F \<and>
    (case j of None \<Rightarrow> (\<forall>k < i. F ! k = None)
     | Some k \<Rightarrow> k < i \<and> (\<exists>T. F ! k = Some T \<and>
         (\<forall>l < i. \<forall>U. F ! l = Some U \<longrightarrow>
           bn_key s (value_C (bn_tree s (bn_root T))) \<le>
           bn_key s (value_C (bn_tree s (bn_root U))))))"

definition bn_review_clear_root ::
  "globals \<Rightarrow> StrictC'_BinomialHeap_C ptr \<Rightarrow> nat \<Rightarrow> globals" where
  "bn_review_clear_root s h i =
    t_hrs_'_update (hrs_mem_update
      (heap_update (roots_C (bn_heap s h) +\<^sub>p int i)
        (NULL :: StrictC'_BinomialTree_C ptr))) s"

definition bn_review_detached_view ::
  "globals \<Rightarrow> globals \<Rightarrow> StrictC'_BinomialHeap_C ptr \<Rightarrow> addr set \<Rightarrow>
    bn_shape option list \<Rightarrow> unit ptr multiset \<Rightarrow> nat \<Rightarrow> bn_shape \<Rightarrow> bool" where
  "bn_review_detached_view s0 s h P F M i T \<longleftrightarrow>
    bn_rep s0 h P F M \<and> i < length F \<and> F ! i = Some T \<and>
    s = bn_review_clear_root s0 h i"

definition bn_review_merge_inventory ::
  "globals \<Rightarrow> bn_shape option list \<Rightarrow> bn_shape option list \<Rightarrow> globals \<Rightarrow>
    bn_shape option list \<Rightarrow> bn_shape option \<Rightarrow> unit ptr multiset \<Rightarrow> bool" where
  "bn_review_merge_inventory s0 F G s bn_prefix bn_carry bn_remaining \<longleftrightarrow>
    mset (bn_forest_values s0 F) + mset (bn_forest_values s0 G) =
      mset (bn_forest_values s bn_prefix) +
      mset (case_option [] (bn_values s) bn_carry) + bn_remaining"

end
