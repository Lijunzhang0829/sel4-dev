theory Binomial_Heap_Model
  imports Calg_Binomial_Foundation.HOL_Layout_Facts "HOL-Library.Multiset"
begin



datatype bn_shape = BN "StrictC'_BinomialTree_C ptr" "bn_shape list"
fun bn_root where "bn_root (BN p cs) = p"
fun bn_rank where "bn_rank (BN p cs) = length cs"
definition bn_tree where "bn_tree s p = (h_val (hrs_mem (t_hrs_' s)) p :: StrictC'_BinomialTree_C)"
definition bn_heap where "bn_heap s h = (h_val (hrs_mem (t_hrs_' s)) h :: StrictC'_BinomialHeap_C)"
definition bn_key where "bn_key s p = sint (h_val (hrs_mem (t_hrs_' s)) (ptr_coerce p :: 32 signed word ptr))"
definition bn_payload where
  "bn_payload P p \<longleftrightarrow> c_guard (ptr_coerce p :: 32 signed word ptr) \<and> block_bytes (ptr_val p) 4 \<subseteq> P"
primrec bn_values where
  "bn_values s (BN p cs) = value_C (bn_tree s p) # concat (map (bn_values s) cs)"
primrec bn_blocks where
  "bn_blocks s (BN p cs) = ptr_val p #
    (if cs = [] then [] else [ptr_val (subtrees_C (bn_tree s p))]) @ concat (map (bn_blocks s) cs)"
primrec bn_valid where
  "bn_valid s P (BN p cs) \<longleftrightarrow>
    c_guard p \<and> live_blocks (phantom_machine_state_' s) (ptr_val p) = Some (size_of TYPE(StrictC'_BinomialTree_C)) \<and>
    unat (order_C (bn_tree s p)) = length cs \<and> refcount_C (bn_tree s p) = 1 \<and>
    bn_payload P (value_C (bn_tree s p)) \<and> map bn_rank cs = [0..<length cs] \<and>
    (if cs = [] then subtrees_C (bn_tree s p) = NULL else
      c_guard (subtrees_C (bn_tree s p)) \<and>
      live_blocks (phantom_machine_state_' s) (ptr_val (subtrees_C (bn_tree s p))) =
        Some (size_of TYPE(StrictC'_BinomialTree_C ptr) * length cs)) \<and>
    (\<forall>i < length cs. c_guard (subtrees_C (bn_tree s p) +\<^sub>p int i) \<and>
      h_val (hrs_mem (t_hrs_' s)) (subtrees_C (bn_tree s p) +\<^sub>p int i) = bn_root (cs ! i)) \<and>
    list_all (bn_valid s P) cs \<and>
    (\<forall>q \<in> set (concat (map (bn_values s) cs)). bn_key s (value_C (bn_tree s p)) \<le> bn_key s q)"
definition bn_forest_values where
  "bn_forest_values s F = concat (map (case_option [] (bn_values s)) F)"
definition bn_owned where
  "bn_owned s h F = ptr_val h #
    (if roots_C (bn_heap s h) = NULL then [] else [ptr_val (roots_C (bn_heap s h))]) @
    concat (map (case_option [] (bn_blocks s)) F)"
definition bn_rep where
  "bn_rep s h P F M \<longleftrightarrow>
    allocator_wf (phantom_machine_state_' s) \<and> P \<subseteq> reserved_bytes (phantom_machine_state_' s) \<and>
    c_guard h \<and> live_blocks (phantom_machine_state_' s) (ptr_val h) = Some (size_of TYPE(StrictC'_BinomialHeap_C)) \<and>
    heap_type_C (bn_heap s h) = BINOMIAL_HEAP_TYPE_MIN \<and>
    compare_func_C (bn_heap s h) = binomial_heap_input.int_compare \<and>
    unat (roots_length_C (bn_heap s h)) = length F \<and> length F \<le> 32 \<and>
    (if roots_C (bn_heap s h) = NULL then F = [] else
      c_guard (roots_C (bn_heap s h)) \<and>
      (\<exists>n. live_blocks (phantom_machine_state_' s) (ptr_val (roots_C (bn_heap s h))) = Some n \<and>
        size_of TYPE(StrictC'_BinomialTree_C ptr) * length F \<le> n)) \<and>
    (\<forall>i < length F. c_guard (roots_C (bn_heap s h) +\<^sub>p int i) \<and>
      h_val (hrs_mem (t_hrs_' s)) (roots_C (bn_heap s h) +\<^sub>p int i) =
        case_option NULL bn_root (F ! i) \<and>
      (case F ! i of None \<Rightarrow> True | Some T \<Rightarrow> bn_rank T = i \<and> bn_valid s P T)) \<and>
    distinct (bn_owned s h F) \<and> M = mset (bn_forest_values s F) \<and>
    unat (num_values_C (bn_heap s h)) = size M"

definition bn_frame where
  "bn_frame s h P F t \<longleftrightarrow>
    reserved_bytes (phantom_machine_state_' t) = reserved_bytes (phantom_machine_state_' s) \<and>
    (\<forall>a \<in> P. hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a) \<and>
    (\<forall>p n. live_blocks (phantom_machine_state_' s) p = Some n \<longrightarrow> p \<notin> set (bn_owned s h F) \<longrightarrow>
      live_blocks (phantom_machine_state_' t) p = Some n \<and>
      (\<forall>a \<in> block_bytes p n. hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a))"

definition bn_stack_ready where
  "bn_stack_ready S s \<longleftrightarrow>
    S \<subseteq> reserved_bytes (phantom_machine_state_' s) \<and>
    stack_allocs 1 S TYPE(StrictC'_BinomialHeap_C) (hrs_htd (t_hrs_' s)) \<noteq> {} \<and>
    (\<forall>p d. (p,d) \<in> stack_allocs 1 S TYPE(StrictC'_BinomialHeap_C) (hrs_htd (t_hrs_' s)) \<longrightarrow>
      stack_allocs 1 S TYPE(StrictC'_BinomialTree_C ptr) d \<noteq> {})"

definition bn_storage_transition where
  "bn_storage_transition s h F t G \<longleftrightarrow>
    (\<forall>p. p \<notin> set (bn_owned t h G) \<longrightarrow>
      live_blocks (phantom_machine_state_' t) p =
        (if p \<in> set (bn_owned s h F) then None else live_blocks (phantom_machine_state_' s) p)) \<and>
    (\<forall>p \<in> set (bn_owned t h G) - set (bn_owned s h F).
      live_blocks (phantom_machine_state_' s) p = None)"

definition bn_contract ::
  "('e::default, 'a, 's) spec_monad \<Rightarrow> 's \<Rightarrow> ('a \<Rightarrow> 's \<Rightarrow> bool) \<Rightarrow> bool" where
  "bn_contract f s Q \<longleftrightarrow>
    runs_to f s (\<lambda>r t. \<exists>v. r = Result v \<and> Q v t) \<and>
    (\<exists>v t. reaches f s (Result v) t)"

end
