theory Binomial_Dataset_Finishes
 imports Binomial_Dataset_Fragments
begin
context binomial_heap_input_global_addresses begin

definition bn_ds_pop_success where
 "bn_ds_pop_success heap least_tree =
do {
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
               }"

definition bn_ds_pop_restore :: "StrictC'_BinomialHeap_C ptr \<Rightarrow> 32 word \<Rightarrow> StrictC'_BinomialTree_C ptr \<Rightarrow> (unit, unit ptr, globals) spec_monad" where
 "bn_ds_pop_restore heap least_index least_tree =
do {
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
               }"

definition bn_ds_insert_finish where
 "bn_ds_insert_finish heap bn_new_treep (result :: 32 signed word) =
do {
unless (result = 0)
                  (modify
                    (t_hrs_'_update
                      (\<lambda>a.
                          hrs_mem_update
                           (heap_update heap
                             (num_values_C_update (\<lambda>a. a + 1)
                               (h_val (hrs_mem a) heap)))
                           a)));
                 tree \<leftarrow>
                   gets
                    (\<lambda>xu.
                        h_val (hrs_mem (t_hrs_' xu)) bn_new_treep);
                 ret \<leftarrow> binomial_tree_unref' tree;
                 return result
}"

definition bn_ds_pop_with_finishes where
 "bn_ds_pop_with_finishes heap =
globals.with_fresh_stack_ptr 1 (\<lambda>a. UNIV)
 (\<lambda>fake_heap\<^sub>p.
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
              (bn_ds_pop_success heap least_tree)
              (bn_ds_pop_restore heap least_index least_tree)
          })
     })"

definition bn_ds_insert_with_finish where
 "bn_ds_insert_with_finish heap value =
globals.with_fresh_stack_ptr 1 (\<lambda>a. UNIV)
 (\<lambda>fake_heap\<^sub>p.
     globals.with_fresh_stack_ptr 1 (\<lambda>s. UNIV)
      (\<lambda>bn_new_treep.
          do {
            ret'ptr_to_void'1 \<leftarrow>
              malloc' (word_of_nat (size_of TYPE(StrictC'_BinomialTree_C)));
            guard (\<lambda>s. c_guard bn_new_treep);
            modify
             (t_hrs_'_update
               (hrs_mem_update
                 (heap_update bn_new_treep
                   (PTR_COERCE(unit \<rightarrow> StrictC'_BinomialTree_C)
                     ret'ptr_to_void'1))));
            condition (\<lambda>s.
                          h_val (hrs_mem (t_hrs_' s)) bn_new_treep =
                          NULL)
              (return 0)
              (do {
                 guard
                  (\<lambda>s.
                      c_guard
                       (h_val (hrs_mem (t_hrs_' s)) bn_new_treep));
                 modify
                  (t_hrs_'_update
                    (\<lambda>a.
                        hrs_mem_update
                         (heap_update
                           (PTR(unit ptr)
                             &(h_val (hrs_mem a)
                                bn_new_treep\<rightarrow>[''value_C'']))
                           value)
                         a));
                 guard
                  (\<lambda>s.
                      c_guard
                       (h_val (hrs_mem (t_hrs_' s)) bn_new_treep));
                 modify
                  (t_hrs_'_update
                    (\<lambda>a.
                        hrs_mem_update
                         (heap_update
                           (PTR(16 word)
                             &(h_val (hrs_mem a)
                                bn_new_treep\<rightarrow>[''order_C'']))
                           0)
                         a));
                 guard
                  (\<lambda>s.
                      c_guard
                       (h_val (hrs_mem (t_hrs_' s)) bn_new_treep));
                 modify
                  (t_hrs_'_update
                    (\<lambda>a.
                        hrs_mem_update
                         (heap_update
                           (PTR(16 word)
                             &(h_val (hrs_mem a)
                                bn_new_treep\<rightarrow>[''refcount_C'']))
                           1)
                         a));
                 guard
                  (\<lambda>s.
                      c_guard
                       (h_val (hrs_mem (t_hrs_' s)) bn_new_treep));
                 modify
                  (t_hrs_'_update
                    (\<lambda>a.
                        hrs_mem_update
                         (heap_update
                           (PTR(StrictC'_BinomialTree_C ptr ptr)
                             &(h_val (hrs_mem a)
                                bn_new_treep\<rightarrow>[''subtrees_C'']))
                           NULL)
                         a));
                 guard (\<lambda>s. c_guard fake_heap\<^sub>p);
                 guard (\<lambda>s. c_guard heap);
                 modify
                  (t_hrs_'_update
                    (\<lambda>a.
                        hrs_mem_update
                         (heap_update fake_heap\<^sub>p
                           (heap_type_C_update
                             (\<lambda>b.
                                 heap_type_C (h_val (hrs_mem a) heap))
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
                 modify
                  (t_hrs_'_update
                    (\<lambda>a.
                        hrs_mem_update
                         (heap_update fake_heap\<^sub>p
                           (num_values_C_update (\<lambda>a. 1)
                             (h_val (hrs_mem a) fake_heap\<^sub>p)))
                         a));
                 modify
                  (t_hrs_'_update
                    (\<lambda>a.
                        hrs_mem_update
                         (heap_update fake_heap\<^sub>p
                           (roots_C_update (\<lambda>a. bn_new_treep)
                             (h_val (hrs_mem a) fake_heap\<^sub>p)))
                         a));
                 modify
                  (t_hrs_'_update
                    (\<lambda>a.
                        hrs_mem_update
                         (heap_update fake_heap\<^sub>p
                           (roots_length_C_update (\<lambda>a. 1)
                             (h_val (hrs_mem a) fake_heap\<^sub>p)))
                         a));
                 result \<leftarrow>
                   binomial_heap_merge' heap fake_heap\<^sub>p;
                 bn_ds_insert_finish heap bn_new_treep result
               })
          }))"

end
end
