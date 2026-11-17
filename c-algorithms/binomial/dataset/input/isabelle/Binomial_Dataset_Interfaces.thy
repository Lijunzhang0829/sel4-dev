theory Binomial_Dataset_Interfaces
 imports Binomial_Dataset_Model Binomial_Dataset_Finishes
begin

primrec bn_ds_tree_layout where
 "bn_ds_tree_layout s P (BN p cs) \<longleftrightarrow>
    c_guard p \<and> live_blocks (phantom_machine_state_' s) (ptr_val p) = Some (size_of TYPE(StrictC'_BinomialTree_C)) \<and>
    unat (order_C (bn_tree s p)) = length cs \<and>
    bn_payload P (value_C (bn_tree s p)) \<and> map bn_rank cs = [0..<length cs] \<and>
    (if cs = [] then subtrees_C (bn_tree s p) = NULL else
      c_guard (subtrees_C (bn_tree s p)) \<and>
      live_blocks (phantom_machine_state_' s) (ptr_val (subtrees_C (bn_tree s p))) =
        Some (size_of TYPE(StrictC'_BinomialTree_C ptr) * length cs)) \<and>
    (\<forall>i < length cs. c_guard (subtrees_C (bn_tree s p) +\<^sub>p int i) \<and>
      h_val (hrs_mem (t_hrs_' s)) (subtrees_C (bn_tree s p) +\<^sub>p int i) = bn_root (cs ! i)) \<and>
    list_all (bn_ds_tree_layout s P) cs \<and>
    (\<forall>q \<in> set (concat (map (bn_values s) cs)). bn_key s (value_C (bn_tree s p)) \<le> bn_key s q)"

definition bn_ds_slots where
 "bn_ds_slots a n = map (\<lambda>i. a +\<^sub>p int i) [0..<n]"

definition bn_ds_forest_view where
 "bn_ds_forest_view s h P F \<longleftrightarrow>
   bn_review_header_view s h F \<and> length F \<le> 32 \<and>
   block_bytes (ptr_val h) (size_of TYPE(StrictC'_BinomialHeap_C)) \<subseteq>
     readable_bytes (phantom_machine_state_' s) \<and>
   block_bytes (ptr_val (roots_C (bn_heap s h))) (8 * length F) \<subseteq>
     readable_bytes (phantom_machine_state_' s) \<and>
   (\<forall>i < length F. case F ! i of None \<Rightarrow> True |
      Some T \<Rightarrow> bn_rank T = i \<and> bn_ds_tree_layout s P T)"

definition bn_ds_graph_blocks where
 "bn_ds_graph_blocks s N = ptr_val ` N \<union>
   {ptr_val (subtrees_C (bn_tree s p)) | p. p \<in> N \<and> order_C (bn_tree s p) \<noteq> 0}"

definition bn_ds_graph_storage where
 "bn_ds_graph_storage s N slots \<longleftrightarrow>
  (\<forall>a \<in> set slots. block_bytes (ptr_val a) (size_of TYPE(StrictC'_BinomialTree_C ptr)) \<subseteq>
     readable_bytes (phantom_machine_state_' s)) \<and>
  (\<forall>p \<in> N. order_C (bn_tree s p) \<noteq> 0 \<longrightarrow>
    ptr_val (subtrees_C (bn_tree s p)) \<notin> ptr_val ` N \<and>
    set slots \<inter> set (bn_ds_slots (subtrees_C (bn_tree s p)) (unat (order_C (bn_tree s p)))) = {}) \<and>
  (\<forall>p \<in> N. \<forall>q \<in> N. p \<noteq> q \<longrightarrow>
    order_C (bn_tree s p) \<noteq> 0 \<longrightarrow> order_C (bn_tree s q) \<noteq> 0 \<longrightarrow>
    subtrees_C (bn_tree s p) \<noteq> subtrees_C (bn_tree s q))"

definition bn_ds_restore where
 "bn_ds_restore s t \<longleftrightarrow>
   phantom_machine_state_' t = phantom_machine_state_' s \<and>
   hrs_htd (t_hrs_' t) = hrs_htd (t_hrs_' s) \<and>
   (\<forall>a \<in> readable_bytes (phantom_machine_state_' s).
     hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a)"

definition bn_ds_storage_transition where
 "bn_ds_storage_transition s B t C \<longleftrightarrow>
   reserved_bytes (phantom_machine_state_' t) = reserved_bytes (phantom_machine_state_' s) \<and>
   (\<forall>p. p \<notin> C \<longrightarrow> live_blocks (phantom_machine_state_' t) p =
     (if p \<in> B then None else live_blocks (phantom_machine_state_' s) p)) \<and>
   (\<forall>p \<in> C - B. live_blocks (phantom_machine_state_' s) p = None) \<and>
   (\<forall>p n. live_blocks (phantom_machine_state_' s) p = Some n \<longrightarrow> p \<notin> B \<longrightarrow>
     (\<forall>a \<in> block_bytes p n. hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a))"

definition bn_ds_old_node_fields where
 "bn_ds_old_node_fields s N t M \<longleftrightarrow>
   (\<forall>p \<in> N \<inter> M. bn_tree t p =
     (bn_tree s p)\<lparr>refcount_C := refcount_C (bn_tree t p)\<rparr>)"

definition bn_ds_tree_merge_pre where
 "bn_ds_tree_merge_pre s h P T U N edges slots held \<longleftrightarrow>
   c_guard h \<and> live_blocks (phantom_machine_state_' s) (ptr_val h) = Some (size_of TYPE(StrictC'_BinomialHeap_C)) \<and>
   ptr_val h \<notin> bn_ds_graph_blocks s N \<and> heap_type_C (bn_heap s h) = BINOMIAL_HEAP_TYPE_MIN \<and>
   compare_func_C (bn_heap s h) = binomial_heap_input.int_compare \<and>
   bn_ds_tree_layout s P T \<and> bn_ds_tree_layout s P U \<and>
   distinct (bn_blocks s T @ bn_blocks s U) \<and>
   bn_rank T = bn_rank U \<and> bn_rank T < 65535 \<and>
   bn_root T \<in> N \<and> bn_root U \<in> N \<and>
   bn_ds_graph s P N edges slots held {} \<and> bn_ds_graph_storage s N slots \<and>
   (\<forall>p \<in> N. unat (refcount_C (bn_tree s p)) < 65535)"

definition bn_ds_merge_pre where
 "bn_ds_merge_pre s h other P F G ext held N edges \<longleftrightarrow>
   bn_ds_forest_view s h P F \<and> bn_ds_forest_view s other P G \<and>
   distinct (concat (map (case_option [] (bn_blocks s)) (F @ G))) \<and>
   live_blocks (phantom_machine_state_' s) (ptr_val h) = Some (size_of TYPE(StrictC'_BinomialHeap_C)) \<and>
   (roots_C (bn_heap s h) = NULL \<or> bn_ds_owned_array s (roots_C (bn_heap s h)) (length F)) \<and>
   ptr_val h \<notin> bn_ds_graph_blocks s N \<and>
   ptr_val (roots_C (bn_heap s h)) \<notin> bn_ds_graph_blocks s N \<and>
   h \<noteq> other \<and>
   bn_ds_graph s P N edges (bn_ds_slots (roots_C (bn_heap s h)) (length F) @ ext) held {} \<and>
   bn_ds_graph_storage s N (bn_ds_slots (roots_C (bn_heap s h)) (length F) @ ext) \<and>
   (\<forall>T. Some T \<in> set G \<longrightarrow> bn_root T \<in> N) \<and>
   length (bn_forest_values s F) + length (bn_forest_values s G) < 2^32 \<and>
   (\<forall>p \<in> N. unat (refcount_C (bn_tree s p)) < 65533)"

definition bn_ds_merge_success where
 "bn_ds_merge_success s h P F G ext held N t \<longleftrightarrow>
   (\<exists>H N' edges'. bn_ds_forest_view t h P H \<and> distinct (bn_owned t h H) \<and>
     mset (bn_forest_values t H) = mset (bn_forest_values s F) + mset (bn_forest_values s G) \<and>
     num_values_C (bn_heap t h) = num_values_C (bn_heap s h) \<and>
     bn_ds_graph t P N' edges' (bn_ds_slots (roots_C (bn_heap t h)) (length H) @ ext) held {} \<and>
     bn_ds_graph_storage t N' (bn_ds_slots (roots_C (bn_heap t h)) (length H) @ ext) \<and>
     bn_ds_old_node_fields s N t N' \<and>
     bn_ds_storage_transition s (insert (ptr_val h) (insert (ptr_val (roots_C (bn_heap s h))) (bn_ds_graph_blocks s N)))
       t (insert (ptr_val h) (insert (ptr_val (roots_C (bn_heap t h))) (bn_ds_graph_blocks t N')))) \<and>
   hrs_htd (t_hrs_' t) = hrs_htd (t_hrs_' s) \<and>
   (\<forall>a \<in> P. hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a)"

definition bn_ds_finish_base where
 "bn_ds_finish_base s h P G N edges slots held \<longleftrightarrow>
   bn_ds_forest_view s h P G \<and> distinct (bn_owned s h G) \<and>
   live_blocks (phantom_machine_state_' s) (ptr_val h) = Some (size_of TYPE(StrictC'_BinomialHeap_C)) \<and>
   (roots_C (bn_heap s h) = NULL \<or> bn_ds_owned_array s (roots_C (bn_heap s h)) (length G)) \<and>
   ptr_val h \<notin> bn_ds_graph_blocks s N \<and>
   ptr_val (roots_C (bn_heap s h)) \<notin> bn_ds_graph_blocks s N \<and>
   bn_ds_graph s P N edges (bn_ds_slots (roots_C (bn_heap s h)) (length G) @ slots) held {} \<and>
   bn_ds_graph_storage s N (bn_ds_slots (roots_C (bn_heap s h)) (length G) @ slots)"

definition bn_ds_roots_allocation where
 "bn_ds_roots_allocation s0 s a n \<longleftrightarrow>
   a \<noteq> NULL \<and> c_guard a \<and> 0 < n \<and>
   malloc_rel (size_of TYPE(StrictC'_BinomialTree_C ptr) * n)
     (hrs_mem (t_hrs_' s0)) (phantom_machine_state_' s0) (ptr_val a)
     (hrs_mem (t_hrs_' s)) (phantom_machine_state_' s) \<and>
   hrs_htd (t_hrs_' s) = hrs_htd (t_hrs_' s0)"

definition bn_ds_carry_done where
 "bn_ds_carry_done s0 h P F G ext held N a n result t \<longleftrightarrow>
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
   bn_ds_graph_storage t N'
     (bn_ds_slots (roots_C (bn_heap s0 h)) (length F) @ bn_ds_slots a n @ ext) \<and>
   N \<subseteq> N' \<and> bn_ds_old_node_fields s0 N t N' \<and>
   live_blocks (phantom_machine_state_' t) (ptr_val a) = Some (8*n) \<and>
   bn_ds_storage_transition s0 (bn_ds_graph_blocks s0 N)
      t (insert (ptr_val a) (bn_ds_graph_blocks t N')))"

end
