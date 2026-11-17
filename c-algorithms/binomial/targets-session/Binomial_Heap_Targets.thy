theory Binomial_Heap_Targets
  imports Binomial_Heap_Model
begin

context binomial_heap_input_global_addresses begin

lemma bn_count_exact:
  "bn_rep s h P F M \<Longrightarrow> bn_contract (binomial_heap_num_entries' h) s
    (\<lambda>r t. unat r = size M \<and> t = s)"
oops

lemma bn_insert_correct:
  "bn_rep s h P F M \<Longrightarrow> bn_payload P v \<Longrightarrow>
    size M + 1 < 2 ^ 32 \<Longrightarrow> bn_stack_ready \<S> s \<Longrightarrow> P \<inter> \<S> = {} \<Longrightarrow>
    bn_contract (binomial_heap_insert' h v) s (\<lambda>r t.
      bn_frame s h P F t \<and>
      (if r = 0 then (\<exists>F'. bn_rep t h P F' M) \<and>
        live_blocks (phantom_machine_state_' t) = live_blocks (phantom_machine_state_' s)
       else \<exists>F'. bn_rep t h P F' (add_mset v M) \<and> bn_storage_transition s h F t F'))"
oops

lemma bn_pop_correct:
  "bn_rep s h P F M \<Longrightarrow> bn_stack_ready \<S> s \<Longrightarrow> P \<inter> \<S> = {} \<Longrightarrow>
    bn_contract (binomial_heap_pop' h) s (\<lambda>r t. bn_frame s h P F t \<and>
      (if r = NULL then (\<exists>F'. bn_rep t h P F' M) \<and>
        live_blocks (phantom_machine_state_' t) = live_blocks (phantom_machine_state_' s)
       else r \<in># M \<and> (\<forall>q \<in># M. bn_key s r \<le> bn_key s q) \<and>
         (\<exists>F'. bn_rep t h P F' (M - {#r#}) \<and> bn_storage_transition s h F t F')))"
oops

lemma bn_free_owned_storage:
  "bn_rep s h P F M \<Longrightarrow> bn_contract (binomial_heap_free' h) s (\<lambda>r t.
    bn_frame s h P F t \<and> allocator_wf (phantom_machine_state_' t) \<and>
    live_blocks (phantom_machine_state_' t) =
      (\<lambda>p. if p \<in> set (bn_owned s h F) then None else live_blocks (phantom_machine_state_' s) p))"
oops

lemma bn_new_empty_or_failure:
  "allocator_wf (phantom_machine_state_' s) \<Longrightarrow>
    P \<subseteq> reserved_bytes (phantom_machine_state_' s) \<Longrightarrow>
    bn_contract calg_binomial_new' s (\<lambda>h t.
      (\<forall>a \<in> P. hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a) \<and>
      (\<forall>p n. live_blocks (phantom_machine_state_' s) p = Some n \<longrightarrow>
        live_blocks (phantom_machine_state_' t) p = Some n \<and>
        (\<forall>a \<in> block_bytes p n. hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a)) \<and>
      (if h = NULL then phantom_machine_state_' t = phantom_machine_state_' s else
        bn_rep t h P [] {#} \<and> roots_C (bn_heap t h) = NULL \<and>
        phantom_machine_state_' t = (phantom_machine_state_' s)\<lparr>live_blocks :=
          (live_blocks (phantom_machine_state_' s))(ptr_val h := Some (size_of TYPE(StrictC'_BinomialHeap_C)))\<rparr>))"
oops

lemma bn_ref_null:
  "bn_contract (binomial_tree_ref' NULL) s (\<lambda>r t. t = s)"
oops

lemma bn_ref_increment:
  "c_guard p \<Longrightarrow> unat (refcount_C (bn_tree s p)) < 65535 \<Longrightarrow>
    bn_contract (binomial_tree_ref' p) s (\<lambda>r t.
      bn_tree t p = (bn_tree s p)\<lparr>refcount_C := refcount_C (bn_tree s p) + 1\<rparr> \<and>
      phantom_machine_state_' t = phantom_machine_state_' s \<and>
      bytes_frame (block_bytes (ptr_val p) (size_of TYPE(StrictC'_BinomialTree_C)))
        (hrs_mem (t_hrs_' s)) (hrs_mem (t_hrs_' t)))"
oops

text \<open>Original implementation challenge, not an asserted true lemma. Under
strict memcpy preconditions, the order-zero path is a candidate for refutation.
Allocation failure remains allowed; a universal nonfailure claim must also cover
successful allocation branches.\<close>
lemma bn_merge_leaves_nonfailure:
  "allocator_wf (phantom_machine_state_' s) \<Longrightarrow> c_guard h \<Longrightarrow>
    heap_type_C (bn_heap s h) = BINOMIAL_HEAP_TYPE_MIN \<Longrightarrow>
    compare_func_C (bn_heap s h) = binomial_heap_input.int_compare \<Longrightarrow>
    P \<subseteq> reserved_bytes (phantom_machine_state_' s) \<Longrightarrow>
    bn_valid s P (BN p []) \<Longrightarrow> bn_valid s P (BN q []) \<Longrightarrow> p \<noteq> q \<Longrightarrow>
    live_blocks (phantom_machine_state_' s) (ptr_val h) = Some (size_of TYPE(StrictC'_BinomialHeap_C)) \<Longrightarrow>
    ptr_val h \<noteq> ptr_val p \<Longrightarrow> ptr_val h \<noteq> ptr_val q \<Longrightarrow>
    bn_contract (binomial_tree_merge' h p q) s (\<lambda>r t. True)"
oops

end
end
