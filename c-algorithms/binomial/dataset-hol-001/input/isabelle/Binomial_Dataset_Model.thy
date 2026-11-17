theory Binomial_Dataset_Model
  imports Binomial_Intermediate_Draft
begin

definition bn_ds_slot_handles where
  "bn_ds_slot_handles s slots = mset (filter (\<lambda>p. p \<noteq> NULL)
    (map (h_val (hrs_mem (t_hrs_' s))) slots))"

definition bn_ds_graph where
  "bn_ds_graph s P N edges slots held pending \<longleftrightarrow>
    allocator_wf (phantom_machine_state_' s) \<and>
    P \<subseteq> reserved_bytes (phantom_machine_state_' s) \<and>
    finite N \<and> distinct slots \<and>
    (\<forall>a \<in> set slots. c_guard a) \<and>
    set_mset (bn_ds_slot_handles s slots + held) \<subseteq> N \<and>
    pending \<subseteq> N \<and>
    N = {p. \<exists>r \<in> set_mset (bn_ds_slot_handles s slots + held) \<union> pending.
      (r,p) \<in> {(q,r). q \<in> N \<and> r \<in> set (edges q)}\<^sup>*} \<and>
    (\<forall>p \<in> N. c_guard p \<and>
      live_blocks (phantom_machine_state_' s) (ptr_val p) =
        Some (size_of TYPE(StrictC'_BinomialTree_C)) \<and>
      bn_payload P (value_C (bn_tree s p)) \<and>
      unat (order_C (bn_tree s p)) = length (edges p) \<and>
      set (edges p) \<subseteq> N \<and>
      map (\<lambda>q. unat (order_C (bn_tree s q))) (edges p) = [0..<length (edges p)] \<and>
      (if edges p = [] then subtrees_C (bn_tree s p) = NULL else
        c_guard (subtrees_C (bn_tree s p)) \<and>
        live_blocks (phantom_machine_state_' s) (ptr_val (subtrees_C (bn_tree s p))) =
          Some (size_of TYPE(StrictC'_BinomialTree_C ptr) * length (edges p))) \<and>
      (\<forall>i < length (edges p). c_guard (subtrees_C (bn_tree s p) +\<^sub>p int i) \<and>
        h_val (hrs_mem (t_hrs_' s)) (subtrees_C (bn_tree s p) +\<^sub>p int i) = edges p ! i) \<and>
      (\<forall>q \<in> set (edges p). bn_key s (value_C (bn_tree s p)) \<le>
        bn_key s (value_C (bn_tree s q))) \<and>
      count (bn_ds_slot_handles s slots + held) p +
        (\<Sum>q \<in> N. count (mset (edges q)) p) < 2 ^ 16 \<and>
      unat (refcount_C (bn_tree s p)) =
        count (bn_ds_slot_handles s slots + held) p +
        (\<Sum>q \<in> N. count (mset (edges q)) p))"

definition bn_ds_heap_frame where
  "bn_ds_heap_frame W s t \<longleftrightarrow>
    hrs_htd (t_hrs_' t) = hrs_htd (t_hrs_' s) \<and>
    bytes_frame W (hrs_mem (t_hrs_' s)) (hrs_mem (t_hrs_' t))"

definition bn_ds_owned_array where
  "bn_ds_owned_array s a n \<longleftrightarrow>
    c_guard a \<and> (\<exists>m. live_blocks (phantom_machine_state_' s) (ptr_val a) = Some m \<and>
      size_of TYPE(StrictC'_BinomialTree_C ptr) * n \<le> m) \<and>
    (\<forall>i < n. c_guard (a +\<^sub>p int i))"

definition bn_ds_borrowed_array where
  "bn_ds_borrowed_array s owner a n \<longleftrightarrow>
    c_guard owner \<and> a = subtrees_C (bn_tree s owner) \<and>
    n = unat (order_C (bn_tree s owner)) \<and>
    (if n = 0 then a = NULL else bn_ds_owned_array s a n)"

definition bn_ds_detached where
  "bn_ds_detached entry s h i root \<longleftrightarrow>
    i < unat (roots_length_C (bn_heap entry h)) \<and>
    h_val (hrs_mem (t_hrs_' entry)) (roots_C (bn_heap entry h) +\<^sub>p int i) = root \<and>
    root \<noteq> NULL \<and> s = bn_review_clear_root entry h i"

end
