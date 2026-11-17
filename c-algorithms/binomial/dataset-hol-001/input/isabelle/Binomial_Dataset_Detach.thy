theory Binomial_Dataset_Detach
 imports Binomial_Dataset_Interfaces
begin
context binomial_heap_input_global_addresses begin

definition bn_ds_clear_root_fragment :: "StrictC'_BinomialHeap_C ptr \<Rightarrow> 32 word \<Rightarrow> (unit, unit, globals) spec_monad" where
 "bn_ds_clear_root_fragment heap (least_index :: 32 word) =
modify
             (t_hrs_'_update
               (\<lambda>a.
                   hrs_mem_update
                    (heap_update
                      (roots_C (h_val (hrs_mem a) heap) +\<^sub>p
                       uint least_index)
                      NULL)
                    a))"

definition bn_ds_pop_detach :: "StrictC'_BinomialHeap_C ptr \<Rightarrow> StrictC'_BinomialHeap_C ptr \<Rightarrow> 32 word \<Rightarrow> (unit, StrictC'_BinomialTree_C ptr, globals) spec_monad" where
 "bn_ds_pop_detach heap fake (least_index :: 32 word) = do {
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
            guard (\<lambda>s. c_guard fake);
            modify
             (t_hrs_'_update
               (\<lambda>a.
                   hrs_mem_update
                    (heap_update fake
                      (heap_type_C_update
                        (\<lambda>b. heap_type_C (h_val (hrs_mem a) heap))
                        (h_val (hrs_mem a) fake)))
                    a));
            modify
             (t_hrs_'_update
               (\<lambda>a.
                   hrs_mem_update
                    (heap_update fake
                      (compare_func_C_update
                        (\<lambda>b.
                            compare_func_C (h_val (hrs_mem a) heap))
                        (h_val (hrs_mem a) fake)))
                    a));
            guard (\<lambda>s. c_guard least_tree);
            modify
             (t_hrs_'_update
               (\<lambda>a.
                   hrs_mem_update
                    (heap_update fake
                      (roots_C_update
                        (\<lambda>b.
                            subtrees_C (h_val (hrs_mem a) least_tree))
                        (h_val (hrs_mem a) fake)))
                    a));
            modify
             (t_hrs_'_update
               (\<lambda>a.
                   hrs_mem_update
                    (heap_update fake
                      (roots_length_C_update
                        (\<lambda>b.
                            UCAST(16 \<rightarrow> 32)
                             (order_C (h_val (hrs_mem a) least_tree)))
                        (h_val (hrs_mem a) fake)))
                    a));
 return least_tree
}"

definition bn_ds_pop_with_detach where
 "bn_ds_pop_with_detach heap =
globals.with_fresh_stack_ptr 1 (\<lambda>a. UNIV)
 (\<lambda>fake.
     do {
       guard (\<lambda>s. c_guard heap);
       condition (\<lambda>s.
                     num_values_C (h_val (hrs_mem (t_hrs_' s)) heap) = 0)
         (return binomial_heap_null_value)
         (do {
            (i, least_index) \<leftarrow>
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
               (0, 0xFFFFFFFF);
 least_tree \<leftarrow> bn_ds_pop_detach heap fake least_index;
ret'int'1 \<leftarrow>
              binomial_heap_merge' heap fake;
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
end
end
