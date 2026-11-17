theory Binomial_Carry_Bridge_Targets
 imports Binomial_Bridge_Targets
begin

definition bn_ds_carry_inv where
 "bn_ds_carry_inv s0 h other P F G ext held N a n state s \<longleftrightarrow>
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
   bn_ds_graph_storage s N'
     (bn_ds_slots (roots_C (bn_heap s0 h)) (length F) @ bn_ds_slots a (unat i) @ ext) \<and>
   N \<subseteq> N' \<and> bn_ds_old_node_fields s0 N s N' \<and>
   live_blocks (phantom_machine_state_' s) (ptr_val a) = Some (8*n) \<and>
   bn_ds_storage_transition s0 (bn_ds_graph_blocks s0 N)
     s (insert (ptr_val a) (bn_ds_graph_blocks s N')) \<and>
   hrs_htd (t_hrs_' s) = hrs_htd (t_hrs_' s0))"

context binomial_heap_input_global_addresses begin

end
end
