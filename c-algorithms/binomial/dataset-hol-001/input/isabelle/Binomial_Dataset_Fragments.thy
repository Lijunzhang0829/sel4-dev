theory Binomial_Dataset_Fragments
 imports "Calg_Project_Base.Project_Input"
begin
context binomial_heap_input_global_addresses begin

definition bn_ds_scan where
  "bn_ds_scan heap =
whileLoop (\<lambda>(i, least_index) s.
                            i < roots_length_C
                                 (h_val (hrs_mem (t_hrs_' s)) heap))
                (\<lambda>(i, least_index).
                    do {
                      guard
                       (\<lambda>s.
                           c_guard
                            (roots_C
                              (h_val (hrs_mem (t_hrs_' s)) heap) +\<^sub>p
                             uint i));
                      least_index \<leftarrow>
                        condition (\<lambda>s.
h_val (hrs_mem (t_hrs_' s))
 (roots_C (h_val (hrs_mem (t_hrs_' s)) heap) +\<^sub>p uint i) =
NULL)
                          (return least_index)
                          (do {
                             ret \<leftarrow>
                               condition (\<lambda>s.
       least_index = 0xFFFFFFFF)
                                 (return (1 :: 32 signed word))
                                 (do {
                                    guard
                                     (\<lambda>s.
   c_guard
    (h_val (hrs_mem (t_hrs_' s))
      (roots_C (h_val (hrs_mem (t_hrs_' s)) heap) +\<^sub>p uint i)));
                                    x \<leftarrow>
guard
 (\<lambda>s.
     c_guard
      (h_val (hrs_mem (t_hrs_' s))
        (roots_C (h_val (hrs_mem (t_hrs_' s)) heap) +\<^sub>p
         uint least_index)));
                                    guard
                                     (\<lambda>s.
   c_guard
    (roots_C (h_val (hrs_mem (t_hrs_' s)) heap) +\<^sub>p
     uint least_index));
                                    data1 \<leftarrow>
gets
 (\<lambda>xe.
     value_C
      (h_val (hrs_mem (t_hrs_' xe))
        (h_val (hrs_mem (t_hrs_' xe))
          (roots_C (h_val (hrs_mem (t_hrs_' xe)) heap) +\<^sub>p
           uint i))));
                                    data2 \<leftarrow>
gets
 (\<lambda>xf.
     value_C
      (h_val (hrs_mem (t_hrs_' xf))
        (h_val (hrs_mem (t_hrs_' xf))
          (roots_C (h_val (hrs_mem (t_hrs_' xf)) heap) +\<^sub>p
           uint least_index))));
                                    ret'int'2 \<leftarrow>
binomial_heap_cmp' heap data1 data2;
                                    return (if ret'int'2 <s 0 then (1 :: 32 signed word) else 0)
                                  });
                             return
                              (if ret \<noteq> 0 then i else least_index)
                           });
                      return (i + 1, least_index)
                    })
               (0, 0xFFFFFFFF)"

definition bn_ds_binomial_heap_pop_expanded where
  "bn_ds_binomial_heap_pop_expanded heap =
globals.with_fresh_stack_ptr 1 (\<lambda>a. UNIV)
 (\<lambda>fake_heap\<^sub>p.
     do {
       guard (\<lambda>s. c_guard heap);
       condition (\<lambda>s.
                     num_values_C (h_val (hrs_mem (t_hrs_' s)) heap) = 0)
         (return binomial_heap_null_value)
         (do {
            (i, least_index) \<leftarrow>
              bn_ds_scan heap;
            guard
             (\<lambda>s.
                 c_guard
                  (roots_C (h_val (hrs_mem (t_hrs_' s)) heap) +\<^sub>p
                   uint least_index));
            least_tree \<leftarrow>
              gets
               (\<lambda>xd.
                   h_val (hrs_mem (t_hrs_' xd))
                    (roots_C (h_val (hrs_mem (t_hrs_' xd)) heap) +\<^sub>p
                     uint least_index));
            modify
             (t_hrs_'_update
               (\<lambda>a.
                   hrs_mem_update
                    (heap_update
                      (roots_C (h_val (hrs_mem a) heap) +\<^sub>p
                       uint least_index)
                      NULL)
                    a));
            guard (\<lambda>s. c_guard fake_heap\<^sub>p);
            modify
             (t_hrs_'_update
               (\<lambda>a.
                   hrs_mem_update
                    (heap_update fake_heap\<^sub>p
                      (heap_type_C_update
                        (\<lambda>b. heap_type_C (h_val (hrs_mem a) heap))
                        (h_val (hrs_mem a) fake_heap\<^sub>p)))
                    a));
            modify
             (t_hrs_'_update
               (\<lambda>a.
                   hrs_mem_update
                    (heap_update fake_heap\<^sub>p
                      (compare_func_C_update
                        (\<lambda>b.
                            compare_func_C (h_val (hrs_mem a) heap))
                        (h_val (hrs_mem a) fake_heap\<^sub>p)))
                    a));
            guard (\<lambda>s. c_guard least_tree);
            modify
             (t_hrs_'_update
               (\<lambda>a.
                   hrs_mem_update
                    (heap_update fake_heap\<^sub>p
                      (roots_C_update
                        (\<lambda>b.
                            subtrees_C (h_val (hrs_mem a) least_tree))
                        (h_val (hrs_mem a) fake_heap\<^sub>p)))
                    a));
            modify
             (t_hrs_'_update
               (\<lambda>a.
                   hrs_mem_update
                    (heap_update fake_heap\<^sub>p
                      (roots_length_C_update
                        (\<lambda>b.
                            UCAST(16 \<rightarrow> 32)
                             (order_C (h_val (hrs_mem a) least_tree)))
                        (h_val (hrs_mem a) fake_heap\<^sub>p)))
                    a));
            ret'int'1 \<leftarrow>
              binomial_heap_merge' heap fake_heap\<^sub>p;
            condition (\<lambda>s. ret'int'1 \<noteq> 0)
              (do {
                 result \<leftarrow>
                   gets
                    (\<lambda>xm.
                        value_C (h_val (hrs_mem (t_hrs_' xm)) least_tree));
                 ret \<leftarrow> binomial_tree_unref' least_tree;
                 modify
                  (t_hrs_'_update
                    (\<lambda>a.
                        hrs_mem_update
                         (heap_update heap
                           (num_values_C_update (\<lambda>a. a - 1)
                             (h_val (hrs_mem a) heap)))
                         a));
                 return result
               })
              (do {
                 guard
                  (\<lambda>s.
                      c_guard
                       (roots_C
                         (h_val (hrs_mem (t_hrs_' s)) heap) +\<^sub>p
                        uint least_index));
                 modify
                  (t_hrs_'_update
                    (\<lambda>a.
                        hrs_mem_update
                         (heap_update
                           (roots_C (h_val (hrs_mem a) heap) +\<^sub>p
                            uint least_index)
                           least_tree)
                         a));
                 return binomial_heap_null_value
               })
          })
     })"

definition bn_ds_carry_loop :: "StrictC'_BinomialHeap_C ptr \<Rightarrow> StrictC'_BinomialHeap_C ptr \<Rightarrow> 32 word \<Rightarrow> StrictC'_BinomialTree_C ptr ptr \<Rightarrow> StrictC'_BinomialTree_C ptr[3] \<Rightarrow> (32 signed word option, StrictC'_BinomialTree_C ptr \<times> 32 word \<times> 32 word \<times> StrictC'_BinomialTree_C ptr[3], globals) spec_monad" where
  "bn_ds_carry_loop heap other bn_max new_roots vals =
whileLoop (\<lambda>(carry, i, new_roots_length, vals) s.
                         i < bn_max)
             (\<lambda>(carry, i, new_roots_length, vals).
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
                      (condition (\<lambda>s. carry \<noteq> NULL)
                         (do {
                            guard (\<lambda>s. num_vals <s 3);
                            guard (\<lambda>s. 0 \<le>s num_vals);
                            guard (\<lambda>s. sint num_vals < INT_MAX);
                            return
                             (num_vals + 1,
                              Arrays.update vals (unat num_vals) carry)
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
                             (do {
                                ret \<leftarrow>
                                  liftE
                                   (binomial_heap_merge_undo' new_roots i);
                                ret \<leftarrow>
                                  liftE (binomial_tree_unref' carry);
                                throw 0
                              });
                          return new_carry
                        })
                       (return NULL);
                   liftE
                    (do {
                       ret \<leftarrow> binomial_tree_unref' carry;
                       ret \<leftarrow> binomial_tree_ref' new_carry;
                       return (new_carry, i + 1, new_roots_length, vals)
                     })
                 })
            (NULL, 0, 0, vals)"

definition bn_ds_binomial_heap_merge_expanded where
  "bn_ds_binomial_heap_merge_expanded heap other =
do {
  vals \<leftarrow> unknown;
  guard (\<lambda>s. c_guard heap);
  guard (\<lambda>s. c_guard other);
  bn_max \<leftarrow>
    condition (\<lambda>s.
                  roots_length_C (h_val (hrs_mem (t_hrs_' s)) other)
                  < roots_length_C (h_val (hrs_mem (t_hrs_' s)) heap))
      (gets
        (\<lambda>xc.
            roots_length_C (h_val (hrs_mem (t_hrs_' xc)) heap) + 1))
      (gets
        (\<lambda>xc.
            roots_length_C (h_val (hrs_mem (t_hrs_' xc)) other) + 1));
  ret'ptr_to_void'1 \<leftarrow>
    malloc'
     (word_of_nat (size_of TYPE(StrictC'_BinomialTree_C ptr)) *
      UCAST(32 \<rightarrow> 64) bn_max);
  new_roots \<leftarrow>
    return
     (PTR_COERCE(unit \<rightarrow> StrictC'_BinomialTree_C ptr)
       ret'ptr_to_void'1);
  condition (\<lambda>s. new_roots = NULL)
    (return 0)
    (finally
      (do {
         (carry, i, new_roots_length, vals) \<leftarrow>
           bn_ds_carry_loop heap other bn_max new_roots vals;
         i \<leftarrow>
           liftE
            (whileLoop (\<lambda>i s.
                           i < roots_length_C
                                (h_val (hrs_mem (t_hrs_' s)) heap))
               (\<lambda>i.
                   do {
                     guard
                      (\<lambda>s.
                          c_guard
                           (roots_C
                             (h_val (hrs_mem (t_hrs_' s)) heap) +\<^sub>p
                            uint i));
                     ret \<leftarrow>
                       condition (\<lambda>s.
                                     h_val (hrs_mem (t_hrs_' s))
(roots_C (h_val (hrs_mem (t_hrs_' s)) heap) +\<^sub>p uint i) \<noteq>
                                     NULL)
                         (gets
                           (\<lambda>xi.
                               h_val (hrs_mem (t_hrs_' xi))
                                (roots_C
                                  (h_val (hrs_mem (t_hrs_' xi))
                                    heap) +\<^sub>p
                                 uint i)) >>=
                          binomial_tree_unref')
                         skip;
                     return (i + 1)
                   })
              0);
         ptr \<leftarrow>
           gets
            (\<lambda>xh.
                PTR_COERCE(StrictC'_BinomialTree_C ptr \<rightarrow> unit)
                 (roots_C (h_val (hrs_mem (t_hrs_' xh)) heap)));
         ret \<leftarrow> liftE (free' ptr);
         modify
          (t_hrs_'_update
            (\<lambda>a.
                hrs_mem_update
                 (heap_update heap
                   (roots_C_update (\<lambda>a. new_roots)
                     (h_val (hrs_mem a) heap)))
                 a));
         modify
          (t_hrs_'_update
            (\<lambda>a.
                hrs_mem_update
                 (heap_update heap
                   (roots_length_C_update (\<lambda>a. new_roots_length)
                     (h_val (hrs_mem a) heap)))
                 a));
         throw 1
       }))
}"

end
end
