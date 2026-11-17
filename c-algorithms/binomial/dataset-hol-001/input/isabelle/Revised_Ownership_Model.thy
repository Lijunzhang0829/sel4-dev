theory Revised_Ownership_Model
 imports Repair_Field_Model Frozen_Model Environment_Spec_Review Refutation_Helper_Definitions
begin

definition bn_r2_allocation_bytes where
 "bn_r2_allocation_bytes s b =
   (case live_blocks (phantom_machine_state_' s) b of
      None \<Rightarrow> {} | Some n \<Rightarrow> block_bytes b n)"

definition bn_r2_released_bytes where
 "bn_r2_released_bytes s B = (\<Union>b\<in>B. bn_r2_allocation_bytes s b)"

definition bn_r2_span_supported where
 "bn_r2_span_supported s W B \<longleftrightarrow>
    W \<subseteq> reserved_bytes (phantom_machine_state_' s) \<or>
    (\<exists>b n. live_blocks (phantom_machine_state_' s) b = Some n \<and>
       b \<notin> B \<and> W \<subseteq> block_bytes b n)"

definition bn_r2_slot_supported ::
 "globals \<Rightarrow> StrictC'_BinomialTree_C ptr ptr \<Rightarrow> addr set \<Rightarrow> bool" where
 "bn_r2_slot_supported s q B \<longleftrightarrow>
    c_guard q \<and> bn_r2_span_supported s (ptr_span q) B"

definition bn_r2_slots_supported where
 "bn_r2_slots_supported s qs B \<longleftrightarrow>
    (\<forall>q\<in>set qs. bn_r2_slot_supported s q B)"

definition bn_r2_graph_storage where
 "bn_r2_graph_storage s N slots \<longleftrightarrow>
    bn_ds_graph_storage s N slots \<and>
    bn_r2_slots_supported s slots (bn_ds_graph_blocks s N)"

definition bn_r2_tree_merge_pre where
 "bn_r2_tree_merge_pre s h P T U N edges slots held \<longleftrightarrow>
   c_guard h \<and> live_blocks (phantom_machine_state_' s) (ptr_val h) = Some (size_of TYPE(StrictC'_BinomialHeap_C)) \<and>
   ptr_val h \<notin> bn_ds_graph_blocks s N \<and> heap_type_C (bn_heap s h) = BINOMIAL_HEAP_TYPE_MIN \<and>
   compare_func_C (bn_heap s h) = binomial_heap_input.int_compare \<and>
   bn_ds_tree_layout s P T \<and> bn_ds_tree_layout s P U \<and>
   distinct (bn_blocks s T @ bn_blocks s U) \<and>
   bn_rank T = bn_rank U \<and> bn_rank T < 65535 \<and>
   bn_root T \<in> N \<and> bn_root U \<in> N \<and>
   bn_ds_graph s P N edges slots held {} \<and> bn_r2_graph_storage s N slots \<and>
   (\<forall>p \<in> N. unat (refcount_C (bn_tree s p)) < 65535)"

definition bn_r2_merge_pre where
 "bn_r2_merge_pre s h other P F G ext held N edges \<longleftrightarrow>
   bn_ds_forest_view s h P F \<and> bn_ds_forest_view s other P G \<and>
   distinct (concat (map (case_option [] (bn_blocks s)) (F @ G))) \<and>
   live_blocks (phantom_machine_state_' s) (ptr_val h) = Some (size_of TYPE(StrictC'_BinomialHeap_C)) \<and>
   (roots_C (bn_heap s h) = NULL \<or> bn_ds_owned_array s (roots_C (bn_heap s h)) (length F)) \<and>
   ptr_val h \<notin> bn_ds_graph_blocks s N \<and>
   ptr_val (roots_C (bn_heap s h)) \<notin> bn_ds_graph_blocks s N \<and>
   bn_r2_slots_supported s ext
     (insert (ptr_val h) (insert (ptr_val (roots_C (bn_heap s h))) (bn_ds_graph_blocks s N))) \<and>
   bn_r2_span_supported s (ptr_span other)
     (insert (ptr_val h) (insert (ptr_val (roots_C (bn_heap s h))) (bn_ds_graph_blocks s N))) \<and>
   (roots_C (bn_heap s h) = NULL \<or> ptr_val (roots_C (bn_heap s h)) \<noteq> ptr_val h) \<and>
   h \<noteq> other \<and>
   bn_ds_graph s P N edges (bn_ds_slots (roots_C (bn_heap s h)) (length F) @ ext) held {} \<and>
   bn_r2_graph_storage s N (bn_ds_slots (roots_C (bn_heap s h)) (length F) @ ext) \<and>
   (\<forall>T. Some T \<in> set G \<longrightarrow> bn_root T \<in> N) \<and>
   length (bn_forest_values s F) + length (bn_forest_values s G) < 2^32 \<and>
   (\<forall>p \<in> N. unat (refcount_C (bn_tree s p)) < 65533)"

definition bn_r2_merge_success where
 "bn_r2_merge_success s h P F G ext held N t \<longleftrightarrow>
   (\<exists>H N' edges'. bn_ds_forest_view t h P H \<and> distinct (bn_owned t h H) \<and>
     mset (bn_forest_values t H) = mset (bn_forest_values s F) + mset (bn_forest_values s G) \<and>
     num_values_C (bn_heap t h) = num_values_C (bn_heap s h) \<and>
     bn_ds_graph t P N' edges' (bn_ds_slots (roots_C (bn_heap t h)) (length H) @ ext) held {} \<and>
     bn_r2_graph_storage t N' (bn_ds_slots (roots_C (bn_heap t h)) (length H) @ ext) \<and>
     bn_r3_old_node_fields s N t N' \<and>
     bn_ds_storage_transition s (insert (ptr_val h) (insert (ptr_val (roots_C (bn_heap s h))) (bn_ds_graph_blocks s N)))
       t (insert (ptr_val h) (insert (ptr_val (roots_C (bn_heap t h))) (bn_ds_graph_blocks t N')))) \<and>
   hrs_htd (t_hrs_' t) = hrs_htd (t_hrs_' s) \<and>
   (\<forall>a \<in> P. hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a)"

definition bn_r2_finish_base where
 "bn_r2_finish_base s h P G N edges slots held \<longleftrightarrow>
   bn_ds_forest_view s h P G \<and> distinct (bn_owned s h G) \<and>
   live_blocks (phantom_machine_state_' s) (ptr_val h) = Some (size_of TYPE(StrictC'_BinomialHeap_C)) \<and>
   (roots_C (bn_heap s h) = NULL \<or> bn_ds_owned_array s (roots_C (bn_heap s h)) (length G)) \<and>
   ptr_val h \<notin> bn_ds_graph_blocks s N \<and>
   ptr_val (roots_C (bn_heap s h)) \<notin> bn_ds_graph_blocks s N \<and>
   bn_ds_graph s P N edges (bn_ds_slots (roots_C (bn_heap s h)) (length G) @ slots) held {} \<and>
   bn_r2_graph_storage s N (bn_ds_slots (roots_C (bn_heap s h)) (length G) @ slots)"

definition bn_r2_carry_done where
 "bn_r2_carry_done s0 h P F G ext held N a n result t \<longleftrightarrow>
  (\<exists>i len vals H N' edges'. result = ((NULL :: StrictC'_BinomialTree_C ptr),(i :: 32 word),(len :: 32 word),(vals :: StrictC'_BinomialTree_C ptr[3])) \<and> unat i = n \<and>
   length H = n \<and> distinct (concat (map (case_option [] (bn_blocks t)) H)) \<and> unat len \<le> n \<and>
   (\<forall>k \<in> {unat len..<n}. H ! k = None) \<and>
   bn_review_root_array_view t a H \<and>
   (\<forall>k < n. case H ! k of None \<Rightarrow> True | Some T \<Rightarrow>
      bn_rank T = k \<and> bn_ds_tree_layout t P T) \<and>
   mset (bn_forest_values t H) = mset (bn_forest_values s0 F) + mset (bn_forest_values s0 G) \<and>
   bn_heap t h = bn_heap s0 h \<and>
   bn_review_root_array_view t (roots_C (bn_heap s0 h)) F \<and>
   bn_ds_graph t P N' edges'
     (bn_ds_slots (roots_C (bn_heap s0 h)) (length F) @ bn_ds_slots a n @ ext) held {} \<and>
   bn_r2_graph_storage t N'
     (bn_ds_slots (roots_C (bn_heap s0 h)) (length F) @ bn_ds_slots a n @ ext) \<and>
   N \<subseteq> N' \<and> bn_r3_old_node_fields s0 N t N' \<and>
   live_blocks (phantom_machine_state_' t) (ptr_val a) = Some (8*n) \<and>
   bn_ds_storage_transition s0 (bn_ds_graph_blocks s0 N)
      t (insert (ptr_val a) (bn_ds_graph_blocks t N')))"

definition bn_r2_carry_inv where
 "bn_r2_carry_inv s0 h other P F G ext held N a n state s \<longleftrightarrow>
  (\<exists>bn_carry i len vals prefix C N' edges'.
   state = (bn_carry,(i :: 32 word),(len :: 32 word),(vals :: StrictC'_BinomialTree_C ptr[3])) \<and>
   unat i \<le> n \<and> unat len \<le> unat i \<and> length prefix = unat i \<and>
   bn_review_root_array_view s a prefix \<and>
   (\<forall>k \<in> {unat len..<unat i}. prefix ! k = None) \<and>
   (\<forall>k < unat i. case prefix ! k of None \<Rightarrow> True | Some T \<Rightarrow>
      bn_rank T = k \<and> bn_ds_tree_layout s P T) \<and>
   distinct (concat (map (case_option [] (bn_blocks s)) prefix) @ case_option [] (bn_blocks s) C) \<and>
   bn_carry = case_option NULL bn_root C \<and>
   (case C of None \<Rightarrow> True | Some T \<Rightarrow>
      bn_rank T = unat i \<and> bn_ds_tree_layout s P T) \<and>
   mset (bn_forest_values s0 F) + mset (bn_forest_values s0 G) =
      mset (bn_forest_values s prefix) + mset (case_option [] (bn_values s) C) +
      mset (bn_forest_values s0 (drop (unat i) F)) + mset (bn_forest_values s0 (drop (unat i) G)) \<and>
   bn_heap s h = bn_heap s0 h \<and> bn_heap s other = bn_heap s0 other \<and>
   bn_review_root_array_view s (roots_C (bn_heap s0 h)) F \<and>
   bn_review_root_array_view s (roots_C (bn_heap s0 other)) G \<and>
   bn_ds_graph s P N' edges'
     (bn_ds_slots (roots_C (bn_heap s0 h)) (length F) @ bn_ds_slots a (unat i) @ ext)
     (held + mset (filter (\<lambda>p. p \<noteq> NULL) [bn_carry])) {} \<and>
   bn_r2_graph_storage s N'
     (bn_ds_slots (roots_C (bn_heap s0 h)) (length F) @ bn_ds_slots a (unat i) @ ext) \<and>
   N \<subseteq> N' \<and> bn_r3_old_node_fields s0 N s N' \<and>
   live_blocks (phantom_machine_state_' s) (ptr_val a) = Some (8*n) \<and>
   bn_ds_storage_transition s0 (bn_ds_graph_blocks s0 N)
     s (insert (ptr_val a) (bn_ds_graph_blocks s N')) \<and>
   hrs_htd (t_hrs_' s) = hrs_htd (t_hrs_' s0))"

end
