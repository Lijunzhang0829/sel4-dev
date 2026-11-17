theory Repair_Phase_Model imports Revised_Ownership_Model begin

context binomial_heap_input_global_addresses begin

definition r2_dec where
 "r2_dec s p = t_hrs_'_update (hrs_mem_update
   (heap_update p ((bn_tree s p)\<lparr>refcount_C:=refcount_C (bn_tree s p)-1\<rparr>))) s"

end

context binomial_heap_input_global_addresses begin

definition r3_count where
 "r3_count s h result = (if result=0 then s else
  t_hrs_'_update (hrs_mem_update (heap_update h
   ((bn_heap s h)\<lparr>num_values_C:=num_values_C (bn_heap s h)+1\<rparr>))) s)"

end

definition bn_r3_rollback_entry where
 "bn_r3_rollback_entry s0 h other P F G ext held N0 e0 a n (i::32 word) bn_carry s N edges \<longleftrightarrow>
  bn_r2_merge_pre s0 h other P F G ext held N0 e0 \<and>
  n \<le> 33 \<and> unat i < n \<and> bn_ds_owned_array s a n \<and>
  bn_heap s h = bn_heap s0 h \<and>
  bn_ds_graph s P N edges
    (bn_ds_slots (roots_C (bn_heap s0 h)) (length F) @ bn_ds_slots a (unat i+1) @ ext)
    (held + mset (filter (\<lambda>p. p\<noteq>NULL) [bn_carry])) {} \<and>
  bn_r2_graph_storage s N
    (bn_ds_slots (roots_C (bn_heap s0 h)) (length F) @ bn_ds_slots a (unat i+1) @ ext) \<and>
  N0 \<subseteq> N \<and> bn_r3_old_node_fields s0 N0 s N \<and>
  bn_ds_storage_transition s0 (bn_ds_graph_blocks s0 N0)
    s (insert (ptr_val a) (bn_ds_graph_blocks s N))"

context binomial_heap_input_global_addresses begin

definition bn_r3_rollback_throw where
 "bn_r3_rollback_throw new_roots i bn_carry = do {
    ret \<leftarrow> liftE (binomial_heap_merge_undo' new_roots i);
    ret \<leftarrow> liftE (binomial_tree_unref' bn_carry);
    throw (0::32 signed word)}"

end

context binomial_heap_input_global_addresses begin

definition bn_r3_carry_loop_step :: "StrictC'_BinomialHeap_C ptr \<Rightarrow> StrictC'_BinomialHeap_C ptr \<Rightarrow> 32 word \<Rightarrow> StrictC'_BinomialTree_C ptr ptr \<Rightarrow> StrictC'_BinomialTree_C ptr \<times> 32 word \<times> 32 word \<times> StrictC'_BinomialTree_C ptr[3] \<Rightarrow> (32 signed word option, StrictC'_BinomialTree_C ptr \<times> 32 word \<times> 32 word \<times> StrictC'_BinomialTree_C ptr[3], globals) spec_monad" where
 "bn_r3_carry_loop_step heap other bn_max new_roots = (\<lambda>(bn_carry, i, new_roots_length, vals).
                 do {
                   ret \<leftarrow>
                     gets
                      (\<lambda>xg.
                          i < roots_length_C
                               (h_val (hrs_mem (t_hrs_' xg)) heap));
                   ret \<leftarrow>
                     liftE
                      (do {
                         guard
                          (\<lambda>xi.
                              ret \<longrightarrow>
                              c_guard
                               (roots_C
                                 (h_val (hrs_mem (t_hrs_' xi))
                                   heap) +\<^sub>p
                                uint i));
                         gets
                          (\<lambda>xi.
                              if ret \<and>
                                 h_val (hrs_mem (t_hrs_' xi))
                                  (roots_C
                                    (h_val (hrs_mem (t_hrs_' xi))
heap) +\<^sub>p
                                   uint i) \<noteq>
                                 NULL
                              then (1 :: 32 signed word) else 0)
                       });
                   (num_vals, vals) \<leftarrow>
                     liftE
                      (condition (\<lambda>s. ret \<noteq> 0)
                         (do {
                            guard
                             (\<lambda>s.
                                 c_guard
                                  (roots_C
                                    (h_val (hrs_mem (t_hrs_' s))
heap) +\<^sub>p
                                   uint i));
                            vals \<leftarrow>
                              gets
                               (\<lambda>xj.
                                   Arrays.update vals 0
                                    (h_val (hrs_mem (t_hrs_' xj))
(roots_C (h_val (hrs_mem (t_hrs_' xj)) heap) +\<^sub>p uint i)));
                            return ((1 :: 32 signed word), vals)
                          })
                         (return ((0 :: 32 signed word), vals)));
                   ret \<leftarrow>
                     gets
                      (\<lambda>xi.
                          i < roots_length_C
                               (h_val (hrs_mem (t_hrs_' xi)) other));
                   ret \<leftarrow>
                     liftE
                      (do {
                         guard
                          (\<lambda>xk.
                              ret \<longrightarrow>
                              c_guard
                               (roots_C
                                 (h_val (hrs_mem (t_hrs_' xk))
                                   other) +\<^sub>p
                                uint i));
                         gets
                          (\<lambda>xk.
                              if ret \<and>
                                 h_val (hrs_mem (t_hrs_' xk))
                                  (roots_C
                                    (h_val (hrs_mem (t_hrs_' xk))
other) +\<^sub>p
                                   uint i) \<noteq>
                                 NULL
                              then (1 :: 32 signed word) else 0)
                       });
                   (num_vals, vals) \<leftarrow>
                     liftE
                      (condition (\<lambda>s. ret \<noteq> 0)
                         (do {
                            guard (\<lambda>s. num_vals <s 3);
                            guard (\<lambda>s. 0 \<le>s num_vals);
                            guard
                             (\<lambda>s.
                                 c_guard
                                  (roots_C
                                    (h_val (hrs_mem (t_hrs_' s))
other) +\<^sub>p
                                   uint i));
                            guard (\<lambda>s. sint num_vals < INT_MAX);
                            vals \<leftarrow>
                              gets
                               (\<lambda>xl.
                                   Arrays.update vals (unat num_vals)
                                    (h_val (hrs_mem (t_hrs_' xl))
(roots_C (h_val (hrs_mem (t_hrs_' xl)) other) +\<^sub>p uint i)));
                            return (num_vals + 1, vals)
                          })
                         (return (num_vals, vals)));
                   (num_vals, vals) \<leftarrow>
                     liftE
                      (condition (\<lambda>s. bn_carry \<noteq> NULL)
                         (do {
                            guard (\<lambda>s. num_vals <s 3);
                            guard (\<lambda>s. 0 \<le>s num_vals);
                            guard (\<lambda>s. sint num_vals < INT_MAX);
                            return
                             (num_vals + 1,
                              Arrays.update vals (unat num_vals) bn_carry)
                          })
                         (return (num_vals, vals)));
                   new_roots_length \<leftarrow>
                     liftE
                      (condition (\<lambda>s. num_vals && 1 = 1)
                         (do {
                            guard
                             (\<lambda>s.
                                 c_guard (new_roots +\<^sub>p uint i));
                            guard (\<lambda>s. num_vals - 1 <s 3);
                            guard (\<lambda>s. 0 \<le>s num_vals - 1);
                            guard
                             (\<lambda>s. - INT_MAX - 1 < sint num_vals);
                            guard
                             (\<lambda>_. sint num_vals \<le> INT_MAX + 1);
                            modify
                             (t_hrs_'_update
                               (hrs_mem_update
                                 (heap_update (new_roots +\<^sub>p uint i)
                                   (vals.[unat (num_vals - 1)]))));
                            tree \<leftarrow>
                              gets
                               (\<lambda>xm.
                                   h_val (hrs_mem (t_hrs_' xm))
                                    (new_roots +\<^sub>p uint i));
                            ret \<leftarrow> binomial_tree_ref' tree;
                            return (i + 1)
                          })
                         (do {
                            guard
                             (\<lambda>_.
                                 c_guard (new_roots +\<^sub>p uint i));
                            modify
                             (t_hrs_'_update
                               (hrs_mem_update
                                 (heap_update (new_roots +\<^sub>p uint i)
                                   NULL)));
                            return new_roots_length
                          }));
                   new_carry \<leftarrow>
                     condition (\<lambda>s. num_vals && 2 \<noteq> 0)
                       (do {
                          new_carry \<leftarrow>
                            liftE
                             (binomial_tree_merge' heap (vals.[0])
                               (vals.[1]));
                          ret \<leftarrow>
                            when (new_carry = NULL)
                             (bn_r3_rollback_throw new_roots i bn_carry);
                          return new_carry
                        })
                       (return NULL);
                   liftE
                    (do {
                       ret \<leftarrow> binomial_tree_unref' bn_carry;
                       ret \<leftarrow> binomial_tree_ref' new_carry;
                       return (new_carry, i + 1, new_roots_length, vals)
                     })
                 })"

end

end