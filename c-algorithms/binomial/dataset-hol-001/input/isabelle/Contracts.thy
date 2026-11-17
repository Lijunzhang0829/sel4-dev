theory Contracts imports Repair_Phase_Model Environment_Parameters begin

context binomial_heap_input_global_addresses begin

lemma spec_0001:
  "c_guard h \<Longrightarrow>
   heap_type_C (bn_heap s h) = BINOMIAL_HEAP_TYPE_MIN \<Longrightarrow>
   compare_func_C (bn_heap s h) = binomial_heap_input.int_compare \<Longrightarrow>
   c_guard (ptr_coerce v :: 32 signed word ptr) \<Longrightarrow>
   runs_to (binomial_heap_cmp' h v v) s Q = Q (Result 0) s"

oops

end

lemma spec_0002:
  "fresh_block A p n \<Longrightarrow> live_blocks A q = Some m \<Longrightarrow>
    block_bytes p n \<inter> block_bytes q m = {}"

oops

context Calg_Binomial_Reviewed_Stack begin

lemma spec_0003:
 "bn_rep (env_pop_s\<lparr>t_hrs_' := (hrs_mem (t_hrs_' env_pop_s),d)\<rparr>)
   (Ptr 16) (block_bytes 256 8) env_pop_F {#(Ptr 260 :: unit ptr), Ptr 256, Ptr 256#}"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0004:
  "bn_rep s h P F M \<Longrightarrow> unat i < length F \<Longrightarrow>
  bn_review_scan_prefix s F (unat i) (if j = 0xFFFFFFFF then None else Some (unat j)) \<Longrightarrow>
  bn_contract (bn_ds_scan_step h (i,j)) s (\<lambda>(i',j') t.
    t = s \<and> i' = i + 1 \<and>
    bn_review_scan_prefix s F (unat i') (if j' = 0xFFFFFFFF then None else Some (unat j')))"

oops

end

context Calg_Binomial_Reviewed_Stack begin

lemma spec_0005:
 assumes hh: "h_val (hrs_mem (t_hrs_' s)) (Ptr 16 :: StrictC'_BinomialHeap_C ptr) = env_insert_heap"
 and hf: "h_val (hrs_mem (t_hrs_' s)) (Ptr 4096 :: StrictC'_BinomialHeap_C ptr) = StrictC'_BinomialHeap_C BINOMIAL_HEAP_TYPE_MIN binomial_heap_input.int_compare 1 (Ptr 4144) 1"
 and t1: "h_val (hrs_mem (t_hrs_' s)) (Ptr 64 :: StrictC'_BinomialTree_C ptr) = env_insert_leaf"
 and t2: "h_val (hrs_mem (t_hrs_' s)) (Ptr 160 :: StrictC'_BinomialTree_C ptr) = env_insert_leaf"
 and r1: "h_val (hrs_mem (t_hrs_' s)) (Ptr 96 :: StrictC'_BinomialTree_C ptr ptr) = Ptr 64"
 and r2: "h_val (hrs_mem (t_hrs_' s)) (Ptr 4144 :: StrictC'_BinomialTree_C ptr ptr) = Ptr 160"
 and f1: "fresh_block (phantom_machine_state_' s) 192 16"
 and f2: "fresh_block ((phantom_machine_state_' s)\<lparr>live_blocks := (live_blocks (phantom_machine_state_' s))(192 := Some 16)\<rparr>) 224 24"
 and f3: "fresh_block ((phantom_machine_state_' s)\<lparr>live_blocks := (live_blocks (phantom_machine_state_' s))(192 := Some 16, 224 := Some 24)\<rparr>) 256 8"
 shows "\<not> runs_to (binomial_heap_merge' (Ptr 16) (Ptr 4096)) s Q"

oops

end

context Calg_Binomial_Reviewed_Stack begin

lemma spec_0006:
 fixes p :: "'a::mem_type ptr"
 assumes "c_guard p" "ptr_span p \<subseteq> S"
   "ptr_span p \<subseteq> stack_free d"
   "typ_uinfo_t TYPE('a) \<noteq> typ_uinfo_t TYPE(stack_byte)"
 shows "(p, ptr_force_type p d) \<in> stack_allocs 1 S TYPE('a) d"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0007:
  "bn_r2_tree_merge_pre s h P T U N edges slots held \<Longrightarrow>
  bn_contract (binomial_tree_merge' h (bn_root T) (bn_root U)) s (\<lambda>r t.
    if r = NULL then bn_ds_restore s t else
    (\<exists>V N' edges'. bn_root V = r \<and> bn_rank V = bn_rank T + 1 \<and>
      bn_ds_tree_layout t P V \<and> distinct (bn_blocks t V) \<and>
      mset (bn_values t V) = mset (bn_values s T) + mset (bn_values s U) \<and>
      refcount_C (bn_tree t r) = 0 \<and>
      bn_ds_graph t P N' edges' slots held {r} \<and> bn_r2_graph_storage t N' slots \<and>
      N' = insert r N \<and> bn_r3_old_node_fields s N t N' \<and>
      bn_ds_storage_transition s (bn_ds_graph_blocks s N) t (bn_ds_graph_blocks t N')) \<and>
    hrs_htd (t_hrs_' t) = hrs_htd (t_hrs_' s) \<and>
    (\<forall>a \<in> P. hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a))"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0008:
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

lemma spec_0009:
  "bn_stack_ready S s \<Longrightarrow>
   \<exists>p d q e.
     (p,d) \<in> stack_allocs 1 S TYPE(StrictC'_BinomialHeap_C) (hrs_htd (t_hrs_' s)) \<and>
     (q,e) \<in> stack_allocs 1 S TYPE(StrictC'_BinomialTree_C ptr) d"

oops

context binomial_heap_input_global_addresses begin

lemma spec_0010:
  "c_guard p \<Longrightarrow> 1 < unat (refcount_C (bn_tree s p)) \<Longrightarrow>
    bn_contract (binomial_tree_unref' p) s (\<lambda>r t.
      bn_tree t p = (bn_tree s p)\<lparr>refcount_C := refcount_C (bn_tree s p) - 1\<rparr> \<and>
      phantom_machine_state_' t = phantom_machine_state_' s \<and>
      bytes_frame (block_bytes (ptr_val p) (size_of TYPE(StrictC'_BinomialTree_C)))
        (hrs_mem (t_hrs_' s)) (hrs_mem (t_hrs_' t)))"

oops

end

lemma spec_0011:
  "allocator_wf A \<Longrightarrow>
   live_blocks A (ptr_val (p :: 'a::mem_type ptr)) = Some n \<Longrightarrow>
   size_of TYPE('a) * k \<le> n \<Longrightarrow> i < k \<Longrightarrow>
   ptr_span (p +\<^sub>p int i) \<subseteq> block_bytes (ptr_val p) n"

oops

context binomial_heap_input_global_addresses begin

lemma spec_0012:
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

end

lemma spec_0013:
  "bn_valid s P T \<Longrightarrow> refcount_C (bn_tree s (bn_root T)) = 1"

oops

lemma spec_0014:
  "bn_rep s h P F M \<Longrightarrow>
    length F \<le> 32 \<and> unat (roots_length_C (bn_heap s h)) = length F"

oops

lemma spec_0015:
  "bn_r2_finish_base s h P F N edges [] {#} \<Longrightarrow>
  unat (num_values_C (bn_heap s h)) = size M \<Longrightarrow>
  M = mset (bn_forest_values s F) \<Longrightarrow> bn_rep s h P F M"

oops

context binomial_heap_input_global_addresses begin

lemma spec_0016:
  "c_guard h \<Longrightarrow>
    heap_type_C (bn_heap s h) = BINOMIAL_HEAP_TYPE_MIN \<Longrightarrow>
    compare_func_C (bn_heap s h) = binomial_heap_input.int_compare \<Longrightarrow>
    bn_payload P x \<Longrightarrow> bn_payload P y \<Longrightarrow>
    bn_contract (binomial_heap_cmp' h x y) s (\<lambda>r t.
      t = s \<and> ((r <s 0) \<longleftrightarrow> bn_key s x < bn_key s y) \<and>
      ((r = 0) \<longleftrightarrow> bn_key s x = bn_key s y))"

oops

end

lemma spec_0017:
  "memcpy_ok A dst src 0 \<Longrightarrow> dst \<noteq> 0 \<and> src \<noteq> 0"

oops

lemma spec_0018:
  "free_rel p hp A hp' B \<Longrightarrow> q \<noteq> p \<Longrightarrow>
    live_blocks B q = live_blocks A q"

oops

context Calg_Binomial_Reviewed_Stack begin

lemma spec_0019:
 assumes heap: "h_val (hrs_mem (t_hrs_' s)) (Ptr 16) = env_pop_heap"
 and fake: "roots_C (h_val (hrs_mem (t_hrs_' s)) (Ptr 4096)) = Ptr 224"
   "roots_length_C (h_val (hrs_mem (t_hrs_' s)) (Ptr 4096)) = 1"
 and r1: "h_val (hrs_mem (t_hrs_' s)) (Ptr 192) = (Ptr 64 :: StrictC'_BinomialTree_C ptr)"
 and r2: "h_val (hrs_mem (t_hrs_' s)) (Ptr 224) = (Ptr 160 :: StrictC'_BinomialTree_C ptr)"
 and t1: "h_val (hrs_mem (t_hrs_' s)) (Ptr 64) = env_pop_leaf 260"
 and t2: "h_val (hrs_mem (t_hrs_' s)) (Ptr 160) = env_pop_leaf 256"
 and k1: "h_val (hrs_mem (t_hrs_' s)) (Ptr 260) = (1 :: 32 signed word)"
 and k2: "h_val (hrs_mem (t_hrs_' s)) (Ptr 256) = (0 :: 32 signed word)"
 and alloc: "phantom_machine_state_' s = env_pop_A"
 shows "\<not> runs_to (binomial_heap_merge' (Ptr 16) (Ptr 4096)) s Q"

oops

end

lemma spec_0020:
  "bytes_frame W hp hp' \<Longrightarrow> W \<subseteq> V \<Longrightarrow> bytes_frame V hp hp'"

oops

context binomial_heap_input_global_addresses begin

lemma spec_0021:
  "bn_rep s h P F M \<Longrightarrow> M \<noteq> {#} \<Longrightarrow>
  bn_contract (bn_ds_scan h) s (\<lambda>(i,j) t.
   t = s \<and> unat i = length F \<and> unat j < length F \<and>
   (\<exists>T. F ! unat j = Some T \<and> value_C (bn_tree s (bn_root T)) \<in># M \<and>
    (\<forall>q \<in># M. bn_key s (value_C (bn_tree s (bn_root T))) \<le> bn_key s q)))"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0022:
  "bn_rep s h P F M \<Longrightarrow> M \<noteq> {#} \<Longrightarrow>
    \<exists>i T. i < length F \<and> F ! i = Some T \<and>
      value_C (bn_tree s (bn_root T)) \<in># M \<and>
      (\<forall>q \<in># M. bn_key s (value_C (bn_tree s (bn_root T))) \<le> bn_key s q)"

oops

end

lemma spec_0023:
  "bytes_frame W hp hp"

oops

lemma spec_0024:
  "c_guard (p :: 'a::mem_type ptr) \<Longrightarrow>
   c_guard (q :: 'b::mem_type ptr) \<Longrightarrow>
   ptr_span p \<inter> ptr_span q = {} \<Longrightarrow>
   h_val (heap_update p v m) q = h_val m q"

oops

lemma spec_0025:
  "allocator_wf A \<Longrightarrow> fresh_block A p n \<Longrightarrow>
   allocator_wf (A\<lparr>live_blocks := (live_blocks A)(p := Some n)\<rparr>)"

oops

lemma spec_0026:
  "malloc_rel n hp A p hp' B \<Longrightarrow> p \<noteq> 0 \<Longrightarrow>
    live_blocks B p = Some n \<and> reserved_bytes B = reserved_bytes A \<and>
    (\<forall>q. q \<noteq> p \<longrightarrow> live_blocks B q = live_blocks A q)"

oops

lemma spec_0027:
  "bytes_frame W hp hp' \<Longrightarrow> a \<notin> W \<Longrightarrow> hp' a = hp a"

oops

context Calg_Binomial_Reviewed_Stack begin

lemma spec_0028:
 "runs_to (f >>= k) s Q \<Longrightarrow> reaches f s (Result v) t \<Longrightarrow> runs_to (k v) t Q"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0029:
  "bn_rep s h P F M \<Longrightarrow> bn_contract (binomial_heap_free' h) s (\<lambda>r t.
    bn_frame s h P F t \<and> allocator_wf (phantom_machine_state_' t) \<and>
    live_blocks (phantom_machine_state_' t) =
      (\<lambda>p. if p \<in> set (bn_owned s h F) then None else live_blocks (phantom_machine_state_' s) p))"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0030:
  "bn_rep s h P F M \<Longrightarrow> bn_stack_ready \<S> s \<Longrightarrow>
    P \<inter> \<S> = {} \<Longrightarrow>
    (\<And>p. \<not> fresh_block (phantom_machine_state_' s) p (size_of TYPE(StrictC'_BinomialTree_C))) \<Longrightarrow>
    bn_contract (binomial_heap_insert' h v) s (\<lambda>r t.
      r = 0 \<and> bn_rep t h P F M \<and> bn_frame s h P F t \<and>
      phantom_machine_state_' t = phantom_machine_state_' s)"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0031:
  "bn_rep s h P F M \<Longrightarrow> bn_review_scan_prefix s F 0 None"

oops

end

lemma spec_0032:
  "length (bn_values s (BN p cs)) = Suc (sum_list (map (\<lambda>T. length (bn_values s T)) cs))"

oops

context Calg_Binomial_Reviewed_Stack begin

lemma spec_0033:
  "bn_rep s h P F M \<Longrightarrow> bn_payload P v \<Longrightarrow>
    size M + 1 < 2 ^ 32 \<Longrightarrow> bn_stack_ready \<S> s \<Longrightarrow> P \<inter> \<S> = {} \<Longrightarrow>
    bn_contract (binomial_heap_insert' h v) s (\<lambda>r t.
      bn_frame s h P F t \<and>
      (if r = 0 then (\<exists>F'. bn_rep t h P F' M) \<and>
        live_blocks (phantom_machine_state_' t) = live_blocks (phantom_machine_state_' s)
       else \<exists>F'. bn_rep t h P F' (add_mset v M) \<and> bn_storage_transition s h F t F'))"

oops

end

context Calg_Binomial_Reviewed_Stack begin

lemma spec_0034:
 "reaches (bn_ds_scan (Ptr 16))
   (env_pop_s\<lparr>t_hrs_' := (hrs_mem (t_hrs_' env_pop_s),d)\<rparr>) (Result (2,1))
   (env_pop_s\<lparr>t_hrs_' := (hrs_mem (t_hrs_' env_pop_s),d)\<rparr>)"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0035:
  "runs_to (malloc' n >>= k) s Q \<Longrightarrow>
   malloc_rel (unat n) (hrs_mem (t_hrs_' s)) (phantom_machine_state_' s) p m A \<Longrightarrow>
   runs_to (k (Ptr p))
     ((t_hrs_'_update (hrs_mem_update (\<lambda>_. m)) s)
       \<lparr>phantom_machine_state_' := A\<rparr>) Q"

oops

end

lemma spec_0036:
  "calloc_rel item_count item_width hp A p hp' B \<Longrightarrow> p \<noteq> 0 \<Longrightarrow>
    i < item_count * item_width \<Longrightarrow> hp' (p + of_nat i) = 0"

oops

lemma spec_0037:
  "allocator_wf A \<Longrightarrow>
   live_blocks A (ptr_val (p :: 'a::mem_type ptr)) = Some n \<Longrightarrow>
   size_of TYPE('a) * k \<le> n \<Longrightarrow> i < k \<Longrightarrow>
   S \<subseteq> reserved_bytes A \<Longrightarrow> bytes_frame S hp hp' \<Longrightarrow>
   h_val hp' (p +\<^sub>p int i) = h_val hp (p +\<^sub>p int i)"

oops

context binomial_heap_input_global_addresses begin

lemma spec_0038:
  "bn_rep s h P F M \<Longrightarrow> M \<noteq> {#} \<Longrightarrow>
  bn_review_scan_prefix s F (length F) (Some j) \<Longrightarrow>
  \<exists>T. F ! j = Some T \<and> value_C (bn_tree s (bn_root T)) \<in># M \<and>
   (\<forall>q \<in># M. bn_key s (value_C (bn_tree s (bn_root T))) \<le> bn_key s q)"

oops

end

lemma spec_0039:
  "bn_rep s h P F {#} \<Longrightarrow>
   phantom_machine_state_' t = phantom_machine_state_' s \<Longrightarrow>
   S \<subseteq> reserved_bytes (phantom_machine_state_' s) \<Longrightarrow>
   bytes_frame S (hrs_mem (t_hrs_' s)) (hrs_mem (t_hrs_' t)) \<Longrightarrow>
   bn_rep t h P F {#}"

oops

lemma spec_0040:
  "i < length F \<Longrightarrow>
    mset (bn_forest_values s (F[i := None])) +
      mset (case_option [] (bn_values s) (F ! i)) = mset (bn_forest_values s F)"

oops

context binomial_heap_input_global_addresses begin

lemma spec_0041:
  "runs_to (malloc' n) s Q \<Longrightarrow>
   fresh_block (phantom_machine_state_' s) p (unat n) \<Longrightarrow>
   Q (Result (Ptr p))
     ((t_hrs_'_update (hrs_mem_update (\<lambda>_. hrs_mem (t_hrs_' s))) s)
       \<lparr>phantom_machine_state_' :=
         (phantom_machine_state_' s)\<lparr>live_blocks :=
           (live_blocks (phantom_machine_state_' s))(p := Some (unat n))\<rparr>\<rparr>)"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0042:
  "bn_r2_finish_base s h P G N edges [slot] {#} \<Longrightarrow>
  bn_r2_slot_supported s slot (insert (ptr_val h) (bn_ds_graph_blocks s N)) \<Longrightarrow>
  c_guard slot \<Longrightarrow> h_val (hrs_mem (t_hrs_' s)) slot = tree \<Longrightarrow>
  bn_ds_tree_layout s P (BN tree []) \<Longrightarrow> value_C (bn_tree s tree) = v \<Longrightarrow>
  unat (num_values_C (bn_heap s h)) = size M \<Longrightarrow> size M + 1 < 2^32 \<Longrightarrow>
  mset (bn_forest_values s G) = (if result = 0 then M else add_mset v M) \<Longrightarrow>
  bn_contract (bn_ds_insert_finish h slot result) s (\<lambda>r t.
    r = result \<and> bn_rep t h P G (if result = 0 then M else add_mset v M) \<and>
    bn_ds_storage_transition s
      (insert (ptr_val h) (insert (ptr_val (roots_C (bn_heap s h))) (bn_ds_graph_blocks s N)))
      t (set (bn_owned t h G)) \<and>
    hrs_htd (t_hrs_' t) = hrs_htd (t_hrs_' s))"

oops

end

context Calg_Binomial_Reviewed_Stack begin

lemma spec_0043:
 assumes hh: "h_val (hrs_mem (t_hrs_' s)) (Ptr 16 :: StrictC'_BinomialHeap_C ptr) = env_insert_heap"
 and t1: "h_val (hrs_mem (t_hrs_' s)) (Ptr 64 :: StrictC'_BinomialTree_C ptr) = env_insert_leaf"
 and t2: "h_val (hrs_mem (t_hrs_' s)) (Ptr 160 :: StrictC'_BinomialTree_C ptr) = env_insert_leaf"
 and f1: "fresh_block (phantom_machine_state_' s) 224 24"
 and f2: "fresh_block ((phantom_machine_state_' s)\<lparr>live_blocks := (live_blocks (phantom_machine_state_' s))(224 := Some 24)\<rparr>) 256 8"
 shows "\<not> runs_to (binomial_tree_merge' (Ptr 16) (Ptr 64) (Ptr 160)) s Q"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0044:
  "stack_allocs 1 \<S> TYPE('a::mem_type) (hrs_htd (t_hrs_' s)) \<noteq> {} \<Longrightarrow>
   (\<And>p d v bs.
      (p,d) \<in> stack_allocs 1 \<S> TYPE('a) (hrs_htd (t_hrs_' s)) \<Longrightarrow>
      length bs = size_of TYPE('a) \<Longrightarrow>
      bn_contract ((c :: 'a ptr \<Rightarrow> ('e::default, 'b, globals) spec_monad) p)
        (t_hrs_'_update (hrs_mem_update (heap_update_padding p v bs))
          (t_hrs_'_update (hrs_htd_update (\<lambda>_. d)) s))
        (\<lambda>r t. \<forall>bs'. length bs' = size_of TYPE('a) \<longrightarrow>
          Q r (t_hrs_'_update (hrs_mem_update (heap_update_list (ptr_val p) bs'))
            (t_hrs_'_update (hrs_htd_update (stack_releases 1 p)) t)))) \<Longrightarrow>
   bn_contract (globals.with_fresh_stack_ptr 1 (\<lambda>_. UNIV) c) s Q"

oops

end

lemma spec_0045:
  "free_rel 0 hp A hp' B \<Longrightarrow> hp' = hp \<and> B = A"

oops

lemma spec_0046:
  "bytes_frame W hp hp' \<Longrightarrow> P \<inter> W = {} \<Longrightarrow>
    \<forall>a \<in> P. hp' a = hp a"

oops

lemma spec_0047:
  "hd (bn_values s T) = value_C (bn_tree s (bn_root T))"

oops

context binomial_heap_input_global_addresses begin

lemma spec_0048:
  "bn_r2_merge_pre s h other P F G ext held N edges \<Longrightarrow>
  bn_contract (binomial_heap_merge' h other) s (\<lambda>r t.
   if r = 0 then bn_ds_restore s t
   else bn_r2_merge_success s h P F G ext held N t)"

oops

end

lemma spec_0049:
  "allocator_wf (phantom_machine_state_' s) \<Longrightarrow>
   S \<subseteq> reserved_bytes (phantom_machine_state_' s) \<Longrightarrow>
   P \<inter> S = {} \<Longrightarrow>
   phantom_machine_state_' t = phantom_machine_state_' s \<Longrightarrow>
   bytes_frame S (hrs_mem (t_hrs_' s)) (hrs_mem (t_hrs_' t)) \<Longrightarrow>
   bn_frame s h P F t"

oops

context binomial_heap_input_global_addresses begin

lemma spec_0050:
  "\<not> runs_to (memcpy' dst NULL n >>= k) s Q"

oops

end

lemma spec_0051:
  "bn_rep s h P F M \<Longrightarrow> distinct (bn_owned s h F) \<and>
    ptr_val h \<in> set (bn_owned s h F)"

oops

context Calg_Binomial_Reviewed_Stack begin

lemma spec_0052:
  "bn_rep s h P F M \<Longrightarrow> bn_stack_ready \<S> s \<Longrightarrow> P \<inter> \<S> = {} \<Longrightarrow>
    bn_contract (binomial_heap_pop' h) s (\<lambda>r t. bn_frame s h P F t \<and>
      (if r = NULL then (\<exists>F'. bn_rep t h P F' M) \<and>
        live_blocks (phantom_machine_state_' t) = live_blocks (phantom_machine_state_' s)
       else r \<in># M \<and> (\<forall>q \<in># M. bn_key s r \<le> bn_key s q) \<and>
         (\<exists>F'. bn_rep t h P F' (M - {#r#}) \<and> bn_storage_transition s h F t F')))"

oops

end

lemma spec_0053:
  "bn_rep s h P F M \<Longrightarrow>
  bn_ds_graph s P (bn_ds_forest_nodes F) (bn_ds_physical_edges s)
    (bn_ds_slots (roots_C (bn_heap s h)) (length F)) {#} {} \<and>
  bn_r2_graph_storage s (bn_ds_forest_nodes F)
    (bn_ds_slots (roots_C (bn_heap s h)) (length F))"

oops

context binomial_heap_input_global_addresses begin

(* DISABLED spec_0054: not part of the active benchmark.
User-requested exclusion while shared-DAG cleanup acceptance is incomplete.
This is neither a proof, a refutation, nor an admitted premise.
lemma spec_0054:
  "unat i < 33 \<Longrightarrow> bn_ds_owned_array s a (unat i + 1) \<Longrightarrow>
  bn_ds_graph s P N edges (bn_ds_slots a (unat i + 1) @ ext)
    (held + mset (filter (\<lambda>p. p \<noteq> NULL) [bn_carry])) {} \<Longrightarrow>
  bn_r2_graph_storage s N (bn_ds_slots a (unat i + 1) @ ext) \<Longrightarrow>
  bn_r2_slots_supported s ext (insert (ptr_val a) (bn_ds_graph_blocks s N)) \<Longrightarrow>
  ptr_val a \<notin> bn_ds_graph_blocks s N \<Longrightarrow>
  bn_contract (do {binomial_heap_merge_undo' a i; binomial_tree_unref' bn_carry}) s (\<lambda>r t.
   \<exists>N' edges'. bn_ds_graph t P N' edges' ext held {} \<and>
    bn_r2_graph_storage t N' ext \<and> N' \<subseteq> N \<and>
    bn_r3_old_node_fields s N t N' \<and>
    bn_ds_storage_transition s (insert (ptr_val a) (bn_ds_graph_blocks s N))
      t (bn_ds_graph_blocks t N') \<and>
    hrs_htd (t_hrs_' t) = hrs_htd (t_hrs_' s) \<and>
    (\<forall>a \<in> P. hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a))"

oops
*)

end

lemma spec_0055:
  "bn_contract f s P \<Longrightarrow>
   (\<And>v t. P v t \<Longrightarrow> bn_contract (g v) t Q) \<Longrightarrow>
   bn_contract (f >>= g) s Q"

oops

context binomial_heap_input_global_addresses begin

lemma spec_0056:
  "allocator_wf (phantom_machine_state_' s) \<Longrightarrow>
    P \<subseteq> reserved_bytes (phantom_machine_state_' s) \<Longrightarrow>
    bn_valid s P T \<Longrightarrow> distinct (bn_blocks s T) \<Longrightarrow>
    bn_contract (binomial_tree_unref' (bn_root T)) s (\<lambda>r t.
      allocator_wf (phantom_machine_state_' t) \<and>
      reserved_bytes (phantom_machine_state_' t) = reserved_bytes (phantom_machine_state_' s) \<and>
      (\<forall>a \<in> P. hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a) \<and>
      live_blocks (phantom_machine_state_' t) =
        (\<lambda>p. if p \<in> set (bn_blocks s T) then None else live_blocks (phantom_machine_state_' s) p) \<and>
      (\<forall>p n. live_blocks (phantom_machine_state_' s) p = Some n \<longrightarrow>
        p \<notin> set (bn_blocks s T) \<longrightarrow>
        (\<forall>a \<in> block_bytes p n. hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a)))"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0057:
  "bn_valid s P T \<Longrightarrow> q \<in> set (bn_values s T) \<Longrightarrow>
    bn_key s (value_C (bn_tree s (bn_root T))) \<le> bn_key s q"

oops

end

lemma spec_0058:
  "ptr_val (bn_root T) \<in> set (bn_blocks s T)"

oops

context Calg_Binomial_Reviewed_Stack begin

lemma spec_0059:
 assumes heap: "h_val (hrs_mem (t_hrs_' s)) (Ptr 16) = env_pop_heap"
 and t1: "h_val (hrs_mem (t_hrs_' s)) (Ptr 64) = env_pop_leaf 260"
 and t2: "h_val (hrs_mem (t_hrs_' s)) (Ptr 160) = env_pop_leaf 256"
 and k1: "h_val (hrs_mem (t_hrs_' s)) (Ptr 260) = (1 :: 32 signed word)"
 and k2: "h_val (hrs_mem (t_hrs_' s)) (Ptr 256) = (0 :: 32 signed word)"
 and alloc: "phantom_machine_state_' s = env_pop_A\<lparr>live_blocks := (live_blocks env_pop_A)(288 := Some 24)\<rparr>"
 shows "\<not> runs_to (binomial_tree_merge' (Ptr 16) (Ptr 64) (Ptr 160)) s Q"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0060:
  "bn_rep entry h P F M \<Longrightarrow> i < length F \<Longrightarrow> F ! i = Some T \<Longrightarrow>
  bn_ds_restore (bn_review_clear_root entry h i) s \<Longrightarrow>
  bn_contract (bn_ds_pop_restore h (of_nat i) (bn_root T)) s (\<lambda>r t.
   r = NULL \<and> bn_rep t h P F M \<and> bn_frame entry h P F t \<and>
   phantom_machine_state_' t = phantom_machine_state_' entry \<and>
   hrs_htd (t_hrs_' t) = hrs_htd (t_hrs_' entry))"

oops

end

lemma spec_0061:
  "bytes_frame W hp hp' \<Longrightarrow> bytes_frame V hp' hp'' \<Longrightarrow>
    bytes_frame (W \<union> V) hp hp''"

oops

context binomial_heap_input_global_addresses begin

lemma spec_0062:
  "bn_r2_merge_pre s0 h other P F G ext held N edges \<Longrightarrow>
  n = max (length F) (length G) + 1 \<Longrightarrow>
  bn_r2_carry_inv s0 h other P F G ext held N a n (bn_carry,i,len,vals) s \<Longrightarrow>
  unat i < n \<Longrightarrow>
  runs_to (bn_ds_carry_loop_step h other (of_nat n) a (bn_carry,i,len,vals)) s
   (\<lambda>r t. case r of Result state \<Rightarrow>
       bn_r2_carry_inv s0 h other P F G ext held N a n state t \<and>
       (\<exists>c l vs. state = (c,i+1,l,vs))
     | Exn e \<Rightarrow> e = 0 \<and> bn_ds_restore s0 t) \<and>
  (\<exists>r t. reaches (bn_ds_carry_loop_step h other (of_nat n) a (bn_carry,i,len,vals)) s r t)"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0063:
  "bn_r2_merge_pre s0 h other P F G ext held N edges \<Longrightarrow>
  n = max (length F) (length G) + 1 \<Longrightarrow>
  bn_ds_roots_allocation s0 s a n \<Longrightarrow>
  bn_r2_carry_inv s0 h other P F G ext held N a n (NULL,0,0,vals) s"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0064:
  "runs_to (globals.with_fresh_stack_ptr 1 (\<lambda>_. UNIV) f) s Q \<Longrightarrow>
   ((p :: 'a::xmem_type ptr),d) \<in>
     stack_allocs 1 \<S> TYPE('a) (hrs_htd (t_hrs_' s)) \<Longrightarrow>
   runs_to (f p) (s\<lparr>t_hrs_' := (hrs_mem (t_hrs_' s), d)\<rparr>) (\<lambda>_ _. True)"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0065:
  "bn_contract (binomial_tree_ref' NULL) s (\<lambda>r t. t = s)"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0066:
  "bn_rep s h P F M \<Longrightarrow> bn_stack_ready \<S> s \<Longrightarrow> P \<inter> \<S> = {} \<Longrightarrow>
  bn_contract (globals.with_fresh_stack_ptr 1 (\<lambda>_. UNIV)
    (\<lambda>(p :: StrictC'_BinomialHeap_C ptr). return p)) s
    (\<lambda>p t. bn_rep t h P F M \<and> bn_frame s h P F t \<and>
      phantom_machine_state_' t = phantom_machine_state_' s \<and>
      hrs_htd (t_hrs_' t) = hrs_htd (t_hrs_' s))"

oops

end

lemma spec_0067:
  "bn_contract f s P \<Longrightarrow>
   (\<And>v t. P v t \<Longrightarrow> \<exists>u. (t,u) \<in> R) \<Longrightarrow>
   (\<And>v t u. P v t \<Longrightarrow> (t,u) \<in> R \<Longrightarrow> Q v u) \<Longrightarrow>
   bn_contract (on_exit f R) s Q"

oops

context binomial_heap_input_global_addresses begin

lemma spec_0068:
  "bn_r2_merge_pre s0 h other P F G ext held N edges \<Longrightarrow>
  n = max (length F) (length G) + 1 \<Longrightarrow>
  bn_ds_roots_allocation s0 s a n \<Longrightarrow>
  runs_to (bn_ds_carry_loop h other (of_nat n) a vals) s
    (\<lambda>r t. case r of Result result \<Rightarrow> bn_r2_carry_done s0 h P F G ext held N a n result t
      | Exn e \<Rightarrow> e = 0 \<and> bn_ds_restore s0 t) \<and>
  (\<exists>r t. reaches (bn_ds_carry_loop h other (of_nat n) a vals) s r t)"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0069:
  "bn_rep entry h P F M \<Longrightarrow> i < length F \<Longrightarrow> F ! i = Some T \<Longrightarrow>
  c_guard fake \<Longrightarrow> root_ptr_valid (hrs_htd (t_hrs_' entry)) fake \<Longrightarrow>
  ptr_span fake \<subseteq> \<S> \<Longrightarrow> \<S> \<subseteq> reserved_bytes (phantom_machine_state_' entry) \<Longrightarrow>
  P \<inter> \<S> = {} \<Longrightarrow>
  bn_contract (bn_ds_pop_detach h fake (of_nat i)) entry (\<lambda>r t.
   r = bn_root T \<and> phantom_machine_state_' t = phantom_machine_state_' entry \<and>
   hrs_htd (t_hrs_' t) = hrs_htd (t_hrs_' entry) \<and>
   (\<exists>N edges. bn_r2_merge_pre t h fake P (F[i := None]) (map Some (bn_ds_children T)) [] {#r#} N edges))"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0070:
  "bn_rep s h P F M \<Longrightarrow> unat i < length F \<Longrightarrow>
   F ! unat i = None \<Longrightarrow>
   bn_contract (bn_ds_scan_step h (i,j)) s (\<lambda>r t. r = (i + 1,j) \<and> t = s)"

oops

end

lemma spec_0071:
  "bytes_frame W hp hp' \<Longrightarrow> bytes_frame W hp' hp'' \<Longrightarrow>
    bytes_frame W hp hp''"

oops

lemma spec_0072:
  "bn_rep s h P F M \<Longrightarrow> bn_ds_forest_view s h P F"

oops

lemma spec_0073:
  "allocator_wf A \<Longrightarrow> fresh_block A p n \<Longrightarrow> live_blocks A p = None"

oops

lemma spec_0074:
  "bn_valid s P T \<Longrightarrow> bn_ds_tree_layout s P T"

oops

context binomial_heap_input_global_addresses begin

lemma spec_0075:
  "bn_rep s h P F {#} \<Longrightarrow> bn_stack_ready \<S> s \<Longrightarrow>
    P \<inter> \<S> = {} \<Longrightarrow>
    bn_contract (binomial_heap_pop' h) s (\<lambda>r t.
      r = NULL \<and> bn_rep t h P F {#} \<and> bn_frame s h P F t \<and>
      phantom_machine_state_' t = phantom_machine_state_' s)"

oops

end

lemma spec_0076:
  "malloc_rel n hp A 0 hp' B \<Longrightarrow> hp' = hp \<and> B = A"

oops

lemma spec_0077:
  "mset (bn_forest_values s (F @ G)) = mset (bn_forest_values s F) + mset (bn_forest_values s G)"

oops

lemma spec_0078:
  "bn_values s T \<noteq> []"

oops

context Calg_Binomial_Reviewed_Stack begin

lemma spec_0079:
 assumes pd: "(p,d) \<in> stack_allocs 1 \<S> TYPE(StrictC'_BinomialHeap_C) stack_byte_typing"
 shows "stack_allocs 1 \<S> TYPE(StrictC'_BinomialTree_C ptr) d \<noteq> {}"

oops

end

lemma spec_0080:
  "bn_contract f s P \<Longrightarrow>
   (\<And>v t. P v t \<Longrightarrow> Q v t) \<Longrightarrow>
   bn_contract f s Q"

oops

lemma spec_0081:
  "bn_valid s P T \<Longrightarrow> unat (order_C (bn_tree s (bn_root T))) = bn_rank T"

oops

context binomial_heap_input_global_addresses begin

lemma spec_0082:
  "bn_r2_tree_merge_pre s h P T U N edges slots held \<Longrightarrow>
  bn_contract (binomial_tree_merge' h (bn_root T) (bn_root U)) s
    (\<lambda>r t. r = NULL \<longrightarrow> bn_ds_restore s t)"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0083:
  "bn_r2_merge_pre s0 h other P F G ext held N edges \<Longrightarrow>
  n = max (length F) (length G) + 1 \<Longrightarrow>
  bn_r2_carry_inv s0 h other P F G ext held N a n (bn_carry,i,len,vals) s \<Longrightarrow>
  unat i = n \<Longrightarrow>
  bn_r2_carry_done s0 h P F G ext held N a n (bn_carry,i,len,vals) s"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0084:
  "bn_rep s h P F M \<Longrightarrow> unat i < length F \<Longrightarrow>
   F ! unat i = Some T \<Longrightarrow>
   bn_contract (bn_ds_scan_step h (i,0xFFFFFFFF)) s
     (\<lambda>r t. r = (i + 1,i) \<and> t = s)"

oops

end

lemma spec_0085:
  "ptr_span (p :: 'a::mem_type ptr) \<subseteq> S \<Longrightarrow>
   length bs = size_of TYPE('a) \<Longrightarrow>
   bytes_frame S hp (heap_update_padding p v bs hp) \<and>
   bytes_frame S hp (heap_update_list (ptr_val p) bs hp)"

oops

lemma spec_0086:
  "R s \<noteq> {} \<Longrightarrow>
   (\<And>v t. (v,t) \<in> R s \<Longrightarrow> Q v t) \<Longrightarrow>
   bn_contract (assume_result_and_state R) s Q"

oops

context binomial_heap_input_global_addresses begin

lemma spec_0087:
  "bn_rep s h P F M \<Longrightarrow> bn_contract (binomial_heap_num_entries' h) s
    (\<lambda>r t. unat r = size M \<and> t = s)"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0088:
  "c_guard p \<Longrightarrow> unat (refcount_C (bn_tree s p)) < 65535 \<Longrightarrow>
    bn_contract (binomial_tree_ref' p) s (\<lambda>r t.
      bn_tree t p = (bn_tree s p)\<lparr>refcount_C := refcount_C (bn_tree s p) + 1\<rparr> \<and>
      phantom_machine_state_' t = phantom_machine_state_' s \<and>
      bytes_frame (block_bytes (ptr_val p) (size_of TYPE(StrictC'_BinomialTree_C)))
        (hrs_mem (t_hrs_' s)) (hrs_mem (t_hrs_' t)))"

oops

end

lemma spec_0089:
 assumes G: "bn_ds_graph s P N edges slots held pending"
 and PN: "p \<in> N" and QE: "q \<in> set (edges p)"
 shows "q \<in> N \<and> unat (order_C (bn_tree s q)) < unat (order_C (bn_tree s p))"

oops

lemma spec_0090:
 "bn_ds_graph s P N edges slots held pending \<Longrightarrow>
  acyclic {(x,y). x \<in> N \<and> y \<in> set (edges x)}"

oops

lemma spec_0091:
 assumes G: "bn_ds_graph s P N edges slots held {}" and PN: "p \<in> N"
 shows "0 < unat (refcount_C (bn_tree s p))"

oops

lemma spec_0092:
 "bn_ds_graph s P N edges (xs @ ys @ zs) held pending =
  bn_ds_graph s P N edges (ys @ xs @ zs) held pending"

oops

lemma spec_0093:
 assumes support: "bn_r2_span_supported s W B"
 and transition: "bn_ds_storage_transition s B t C"
 shows "bn_r2_span_supported t W C"

oops

lemma spec_0094:
 assumes ENTRY: "bn_r3_rollback_entry s0 h other P F G ext held N0 e0 a n i bn_carry s N edges"
 shows "unat i < 33 \<and> bn_ds_owned_array s a (unat i+1) \<and>
  bn_ds_graph s P N edges
    (bn_ds_slots a (unat i+1) @ (bn_ds_slots (roots_C (bn_heap s0 h)) (length F) @ ext))
    (held + mset (filter (\<lambda>p. p\<noteq>NULL) [bn_carry])) {} \<and>
  bn_r2_graph_storage s N
    (bn_ds_slots a (unat i+1) @ (bn_ds_slots (roots_C (bn_heap s0 h)) (length F) @ ext)) \<and>
  bn_r2_slots_supported s (bn_ds_slots (roots_C (bn_heap s0 h)) (length F) @ ext)
    (insert (ptr_val a) (bn_ds_graph_blocks s N)) \<and>
  ptr_val a\<notin>bn_ds_graph_blocks s N"

oops

context binomial_heap_input_global_addresses begin

lemma spec_0095:
 "bn_r3_carry_loop_step h other n a state = bn_ds_carry_loop_step h other n a state"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0096:
 assumes G: "c_guard p" and R: "1 < unat (refcount_C (bn_tree s p))"
 shows "bn_contract (binomial_tree_unref' p) s (\<lambda>r t. t = r2_dec s p)"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0097:
 assumes W: "allocator_wf (phantom_machine_state_' s)"
 and G: "c_guard p" and LP: "live_blocks (phantom_machine_state_' s) (ptr_val p)=Some 24"
 and ORDER: "order_C (bn_tree s p)=0" and ARRAY: "subtrees_C (bn_tree s p)=NULL"
 and POS: "0 < unat (refcount_C (bn_tree s p))"
 shows "bn_contract (binomial_tree_unref' p) s (\<lambda>r t.
   if refcount_C (bn_tree s p)=1 then
     bn_r3_release (r2_dec s p) {ptr_val p} {ptr_val p} t
   else t=r2_dec s p)"

oops

end

lemma spec_0098:
 assumes FB: "bn_r2_finish_base s h P F N edges [slot] {#}"
 and READ: "h_val (hrs_mem (t_hrs_' s)) slot = tree"
 and LEAF: "bn_ds_tree_layout s P (BN tree [])"
 shows "N = insert tree (bn_ds_forest_nodes F) \<and>
  (\<forall>q\<in>N. unat (refcount_C (bn_tree s q)) =
    (if q\<in>bn_ds_forest_nodes F then 1 else 0) + (if q=tree then 1 else 0))"

oops

context binomial_heap_input_global_addresses begin

lemma spec_0099:
  assumes L: "bn_ds_tree_layout s P T"
    and A: "\<forall>b\<in>set (bn_blocks s T). live_blocks (phantom_machine_state_' t) b = live_blocks (phantom_machine_state_' s) b"
    and HN: "\<forall>p \<in> set (bn_ds_node_list T). value_C (bn_tree t p)=value_C (bn_tree s p) \<and>
      order_C (bn_tree t p)=order_C (bn_tree s p) \<and> subtrees_C (bn_tree t p)=subtrees_C (bn_tree s p)"
    and HE: "\<forall>p \<in> set (bn_ds_node_list T). \<forall>i<unat (order_C (bn_tree s p)).
      h_val (hrs_mem (t_hrs_' t)) (subtrees_C (bn_tree s p) +\<^sub>p int i) =
      h_val (hrs_mem (t_hrs_' s)) (subtrees_C (bn_tree s p) +\<^sub>p int i)"
    and KEY: "\<forall>v :: unit ptr. bn_payload P v \<longrightarrow> bn_key t v = bn_key s v"
  shows "bn_ds_tree_layout t P T \<and> bn_values t T = bn_values s T \<and> bn_blocks t T = bn_blocks s T"

oops

end

context binomial_heap_input_global_addresses begin

lemma spec_0100:
 assumes W: "allocator_wf (phantom_machine_state_' s)"
 and GH: "c_guard h" and GP: "c_guard p"
 and LH: "live_blocks (phantom_machine_state_' s) (ptr_val h)=Some 40"
 and LP: "live_blocks (phantom_machine_state_' s) (ptr_val p)=Some 24"
 and NE: "ptr_val p\<noteq>ptr_val h"
 and SLOT: "bn_r2_slot_supported s slot {ptr_val h}"
 and READ: "h_val (hrs_mem (t_hrs_' s)) slot=p"
 and ORDER: "order_C (bn_tree s p)=0"
 and ARRAY: "subtrees_C (bn_tree s p)=NULL"
 and POS: "0 < unat (refcount_C (bn_tree s p))"
 shows "bn_contract (bn_ds_insert_finish h slot result) s (\<lambda>r t.
  r=result \<and> (if refcount_C (bn_tree s p)=1 then
   bn_r3_release (r2_dec (r3_count s h result) p) {ptr_val p} {ptr_val p} t
  else t=r2_dec (r3_count s h result) p))"

oops

end

lemma spec_0101:
 "bn_r3_old_node_fields s N t M = bn_ds_old_node_fields s N t M"

oops

end
