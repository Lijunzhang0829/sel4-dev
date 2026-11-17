# 当前定义目录

C 翻译器定义见 input/generated-c-definitions.json。

## bn_ds_node_list

来源：`isabelle/Binomial_Bridge_Targets.thy:5`。

```isabelle
primrec bn_ds_node_list where
 "bn_ds_node_list (BN p cs) = p # concat (map bn_ds_node_list cs)"
```

## bn_ds_forest_nodes

来源：`isabelle/Binomial_Bridge_Targets.thy:7`。

```isabelle
definition bn_ds_forest_nodes where
 "bn_ds_forest_nodes F = set (concat (map (case_option [] bn_ds_node_list) F))"
```

## bn_ds_physical_edges

来源：`isabelle/Binomial_Bridge_Targets.thy:9`。

```isabelle
definition bn_ds_physical_edges where
 "bn_ds_physical_edges s p = map (h_val (hrs_mem (t_hrs_' s)))
    (bn_ds_slots (subtrees_C (bn_tree s p)) (unat (order_C (bn_tree s p))))"
```

## bn_ds_carry_inv

来源：`isabelle/Binomial_Carry_Bridge_Targets.thy:5`。

```isabelle
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
```

## bn_ds_clear_root_fragment

来源：`isabelle/Binomial_Dataset_Detach.thy:6`。

```isabelle
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
```

## bn_ds_pop_detach

来源：`isabelle/Binomial_Dataset_Detach.thy:18`。

```isabelle
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
```

## bn_ds_pop_with_detach

来源：`isabelle/Binomial_Dataset_Detach.thy:85`。

```isabelle
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
```

## bn_ds_pop_success

来源：`isabelle/Binomial_Dataset_Finishes.thy:6`。

```isabelle
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
```

## bn_ds_pop_restore

来源：`isabelle/Binomial_Dataset_Finishes.thy:25`。

```isabelle
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
```

## bn_ds_insert_finish

来源：`isabelle/Binomial_Dataset_Finishes.thy:46`。

```isabelle
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
```

## bn_ds_pop_with_finishes

来源：`isabelle/Binomial_Dataset_Finishes.thy:66`。

```isabelle
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
```

## bn_ds_insert_with_finish

来源：`isabelle/Binomial_Dataset_Finishes.thy:213`。

```isabelle
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
```

## bn_ds_scan

来源：`isabelle/Binomial_Dataset_Fragments.thy:6`。

```isabelle
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
```

## bn_ds_binomial_heap_pop_expanded

来源：`isabelle/Binomial_Dataset_Fragments.thy:75`。

```isabelle
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
```

## bn_ds_carry_loop

来源：`isabelle/Binomial_Dataset_Fragments.thy:189`。

```isabelle
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
```

## bn_ds_binomial_heap_merge_expanded

来源：`isabelle/Binomial_Dataset_Fragments.thy:370`。

```isabelle
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
```

## bn_ds_tree_layout

来源：`isabelle/Binomial_Dataset_Interfaces.thy:5`。

```isabelle
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
```

## bn_ds_slots

来源：`isabelle/Binomial_Dataset_Interfaces.thy:19`。

```isabelle
definition bn_ds_slots where
 "bn_ds_slots a n = map (\<lambda>i. a +\<^sub>p int i) [0..<n]"
```

## bn_ds_forest_view

来源：`isabelle/Binomial_Dataset_Interfaces.thy:22`。

```isabelle
definition bn_ds_forest_view where
 "bn_ds_forest_view s h P F \<longleftrightarrow>
   bn_review_header_view s h F \<and> length F \<le> 32 \<and>
   block_bytes (ptr_val h) (size_of TYPE(StrictC'_BinomialHeap_C)) \<subseteq>
     readable_bytes (phantom_machine_state_' s) \<and>
   block_bytes (ptr_val (roots_C (bn_heap s h))) (8 * length F) \<subseteq>
     readable_bytes (phantom_machine_state_' s) \<and>
   (\<forall>i < length F. case F ! i of None \<Rightarrow> True |
      Some T \<Rightarrow> bn_rank T = i \<and> bn_ds_tree_layout s P T)"
```

## bn_ds_graph_blocks

来源：`isabelle/Binomial_Dataset_Interfaces.thy:32`。

```isabelle
definition bn_ds_graph_blocks where
 "bn_ds_graph_blocks s N = ptr_val ` N \<union>
   {ptr_val (subtrees_C (bn_tree s p)) | p. p \<in> N \<and> order_C (bn_tree s p) \<noteq> 0}"
```

## bn_ds_graph_storage

来源：`isabelle/Binomial_Dataset_Interfaces.thy:36`。

```isabelle
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
```

## bn_ds_restore

来源：`isabelle/Binomial_Dataset_Interfaces.thy:47`。

```isabelle
definition bn_ds_restore where
 "bn_ds_restore s t \<longleftrightarrow>
   phantom_machine_state_' t = phantom_machine_state_' s \<and>
   hrs_htd (t_hrs_' t) = hrs_htd (t_hrs_' s) \<and>
   (\<forall>a \<in> readable_bytes (phantom_machine_state_' s).
     hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a)"
```

## bn_ds_storage_transition

来源：`isabelle/Binomial_Dataset_Interfaces.thy:54`。

```isabelle
definition bn_ds_storage_transition where
 "bn_ds_storage_transition s B t C \<longleftrightarrow>
   reserved_bytes (phantom_machine_state_' t) = reserved_bytes (phantom_machine_state_' s) \<and>
   (\<forall>p. p \<notin> C \<longrightarrow> live_blocks (phantom_machine_state_' t) p =
     (if p \<in> B then None else live_blocks (phantom_machine_state_' s) p)) \<and>
   (\<forall>p \<in> C - B. live_blocks (phantom_machine_state_' s) p = None) \<and>
   (\<forall>p n. live_blocks (phantom_machine_state_' s) p = Some n \<longrightarrow> p \<notin> B \<longrightarrow>
     (\<forall>a \<in> block_bytes p n. hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a))"
```

## bn_ds_old_node_fields

来源：`isabelle/Binomial_Dataset_Interfaces.thy:63`。

```isabelle
definition bn_ds_old_node_fields where
 "bn_ds_old_node_fields s N t M \<longleftrightarrow>
   (\<forall>p \<in> N \<inter> M. bn_tree t p =
     (bn_tree s p)\<lparr>refcount_C := refcount_C (bn_tree t p)\<rparr>)"
```

## bn_ds_tree_merge_pre

来源：`isabelle/Binomial_Dataset_Interfaces.thy:68`。

```isabelle
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
```

## bn_ds_merge_pre

来源：`isabelle/Binomial_Dataset_Interfaces.thy:80`。

```isabelle
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
```

## bn_ds_merge_success

来源：`isabelle/Binomial_Dataset_Interfaces.thy:95`。

```isabelle
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
```

## bn_ds_finish_base

来源：`isabelle/Binomial_Dataset_Interfaces.thy:108`。

```isabelle
definition bn_ds_finish_base where
 "bn_ds_finish_base s h P G N edges slots held \<longleftrightarrow>
   bn_ds_forest_view s h P G \<and> distinct (bn_owned s h G) \<and>
   live_blocks (phantom_machine_state_' s) (ptr_val h) = Some (size_of TYPE(StrictC'_BinomialHeap_C)) \<and>
   (roots_C (bn_heap s h) = NULL \<or> bn_ds_owned_array s (roots_C (bn_heap s h)) (length G)) \<and>
   ptr_val h \<notin> bn_ds_graph_blocks s N \<and>
   ptr_val (roots_C (bn_heap s h)) \<notin> bn_ds_graph_blocks s N \<and>
   bn_ds_graph s P N edges (bn_ds_slots (roots_C (bn_heap s h)) (length G) @ slots) held {} \<and>
   bn_ds_graph_storage s N (bn_ds_slots (roots_C (bn_heap s h)) (length G) @ slots)"
```

## bn_ds_roots_allocation

来源：`isabelle/Binomial_Dataset_Interfaces.thy:118`。

```isabelle
definition bn_ds_roots_allocation where
 "bn_ds_roots_allocation s0 s a n \<longleftrightarrow>
   a \<noteq> NULL \<and> c_guard a \<and> 0 < n \<and>
   malloc_rel (size_of TYPE(StrictC'_BinomialTree_C ptr) * n)
     (hrs_mem (t_hrs_' s0)) (phantom_machine_state_' s0) (ptr_val a)
     (hrs_mem (t_hrs_' s)) (phantom_machine_state_' s) \<and>
   hrs_htd (t_hrs_' s) = hrs_htd (t_hrs_' s0)"
```

## bn_ds_carry_done

来源：`isabelle/Binomial_Dataset_Interfaces.thy:126`。

```isabelle
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
```

## bn_ds_scan_step

来源：`isabelle/Binomial_Dataset_Loop_Steps.thy:6`。

```isabelle
definition bn_ds_scan_step :: "StrictC'_BinomialHeap_C ptr \<Rightarrow> 32 word \<times> 32 word \<Rightarrow> (unit, 32 word \<times> 32 word, globals) spec_monad" where
 "bn_ds_scan_step heap = (\<lambda>(i, least_index).
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
                    })"
```

## bn_ds_scan_from

来源：`isabelle/Binomial_Dataset_Loop_Steps.thy:70`。

```isabelle
definition bn_ds_scan_from where
 "bn_ds_scan_from heap state = whileLoop (\<lambda>(i, least_index) s.
                            i < roots_length_C
                                 (h_val (hrs_mem (t_hrs_' s)) heap)) (bn_ds_scan_step heap) state"
```

## bn_ds_carry_loop_step

来源：`isabelle/Binomial_Dataset_Loop_Steps.thy:75`。

```isabelle
definition bn_ds_carry_loop_step :: "StrictC'_BinomialHeap_C ptr \<Rightarrow> StrictC'_BinomialHeap_C ptr \<Rightarrow> 32 word \<Rightarrow> StrictC'_BinomialTree_C ptr ptr \<Rightarrow> StrictC'_BinomialTree_C ptr \<times> 32 word \<times> 32 word \<times> StrictC'_BinomialTree_C ptr[3] \<Rightarrow> (32 signed word option, StrictC'_BinomialTree_C ptr \<times> 32 word \<times> 32 word \<times> StrictC'_BinomialTree_C ptr[3], globals) spec_monad" where
 "bn_ds_carry_loop_step heap other bn_max new_roots = (\<lambda>(carry, i, new_roots_length, vals).
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
                 })"
```

## bn_ds_carry_loop_from

来源：`isabelle/Binomial_Dataset_Loop_Steps.thy:252`。

```isabelle
definition bn_ds_carry_loop_from where
 "bn_ds_carry_loop_from heap other bn_max new_roots state = whileLoop (\<lambda>(carry, i, new_roots_length, vals) s.
                         i < bn_max) (bn_ds_carry_loop_step heap other bn_max new_roots) state"
```

## bn_ds_slot_handles

来源：`isabelle/Binomial_Dataset_Model.thy:5`。

```isabelle
definition bn_ds_slot_handles where
  "bn_ds_slot_handles s slots = mset (filter (\<lambda>p. p \<noteq> NULL)
    (map (h_val (hrs_mem (t_hrs_' s))) slots))"
```

## bn_ds_graph

来源：`isabelle/Binomial_Dataset_Model.thy:9`。

```isabelle
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
```

## bn_ds_heap_frame

来源：`isabelle/Binomial_Dataset_Model.thy:40`。

```isabelle
definition bn_ds_heap_frame where
  "bn_ds_heap_frame W s t \<longleftrightarrow>
    hrs_htd (t_hrs_' t) = hrs_htd (t_hrs_' s) \<and>
    bytes_frame W (hrs_mem (t_hrs_' s)) (hrs_mem (t_hrs_' t))"
```

## bn_ds_owned_array

来源：`isabelle/Binomial_Dataset_Model.thy:45`。

```isabelle
definition bn_ds_owned_array where
  "bn_ds_owned_array s a n \<longleftrightarrow>
    c_guard a \<and> (\<exists>m. live_blocks (phantom_machine_state_' s) (ptr_val a) = Some m \<and>
      size_of TYPE(StrictC'_BinomialTree_C ptr) * n \<le> m) \<and>
    (\<forall>i < n. c_guard (a +\<^sub>p int i))"
```

## bn_ds_borrowed_array

来源：`isabelle/Binomial_Dataset_Model.thy:51`。

```isabelle
definition bn_ds_borrowed_array where
  "bn_ds_borrowed_array s owner a n \<longleftrightarrow>
    c_guard owner \<and> a = subtrees_C (bn_tree s owner) \<and>
    n = unat (order_C (bn_tree s owner)) \<and>
    (if n = 0 then a = NULL else bn_ds_owned_array s a n)"
```

## bn_ds_detached

来源：`isabelle/Binomial_Dataset_Model.thy:57`。

```isabelle
definition bn_ds_detached where
  "bn_ds_detached entry s h i root \<longleftrightarrow>
    i < unat (roots_length_C (bn_heap entry h)) \<and>
    h_val (hrs_mem (t_hrs_' entry)) (roots_C (bn_heap entry h) +\<^sub>p int i) = root \<and>
    root \<noteq> NULL \<and> s = bn_review_clear_root entry h i"
```

## bn_ds_children

来源：`isabelle/Binomial_Detach_Bridge_Targets.thy:4`。

```isabelle
fun bn_ds_children where "bn_ds_children (BN p cs) = cs"
```

## bn_review_stack_frame

来源：`isabelle/Binomial_Intermediate_Draft.thy:5`。

```isabelle
definition bn_review_stack_frame :: "addr set \<Rightarrow> globals \<Rightarrow> globals \<Rightarrow> bool" where
  "bn_review_stack_frame S s t \<longleftrightarrow>
    phantom_machine_state_' t = phantom_machine_state_' s \<and>
    (\<forall>a. a \<notin> S \<longrightarrow> hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a)"
```

## bn_review_root_array_view

来源：`isabelle/Binomial_Intermediate_Draft.thy:10`。

```isabelle
definition bn_review_root_array_view ::
  "globals \<Rightarrow> StrictC'_BinomialTree_C ptr ptr \<Rightarrow> bn_shape option list \<Rightarrow> bool" where
  "bn_review_root_array_view s a F \<longleftrightarrow>
    (\<forall>i < length F. c_guard (a +\<^sub>p int i) \<and>
      h_val (hrs_mem (t_hrs_' s)) (a +\<^sub>p int i) = case_option NULL bn_root (F ! i))"
```

## bn_review_header_view

来源：`isabelle/Binomial_Intermediate_Draft.thy:16`。

```isabelle
definition bn_review_header_view ::
  "globals \<Rightarrow> StrictC'_BinomialHeap_C ptr \<Rightarrow> bn_shape option list \<Rightarrow> bool" where
  "bn_review_header_view s h F \<longleftrightarrow>
    c_guard h \<and>
    heap_type_C (bn_heap s h) = BINOMIAL_HEAP_TYPE_MIN \<and>
    compare_func_C (bn_heap s h) = binomial_heap_input.int_compare \<and>
    unat (roots_length_C (bn_heap s h)) = length F \<and>
    bn_review_root_array_view s (roots_C (bn_heap s h)) F"
```

## bn_review_reference_ledger

来源：`isabelle/Binomial_Intermediate_Draft.thy:25`。

```isabelle
definition bn_review_reference_ledger ::
  "globals \<Rightarrow> StrictC'_BinomialTree_C ptr set \<Rightarrow>
    (StrictC'_BinomialTree_C ptr \<Rightarrow> StrictC'_BinomialTree_C ptr list) \<Rightarrow>
    StrictC'_BinomialTree_C ptr multiset \<Rightarrow> bool" where
  "bn_review_reference_ledger s N bn_edges H \<longleftrightarrow>
    finite N \<and> set_mset H \<subseteq> N \<and>
    (\<forall>p \<in> N. c_guard p \<and>
      live_blocks (phantom_machine_state_' s) (ptr_val p) =
        Some (size_of TYPE(StrictC'_BinomialTree_C)) \<and>
      set (bn_edges p) \<subseteq> N \<and>
      count H p + (\<Sum>q \<in> N. count (mset (bn_edges q)) p) < 2 ^ 16 \<and>
      unat (refcount_C (bn_tree s p)) =
        count H p + (\<Sum>q \<in> N. count (mset (bn_edges q)) p))"
```

## bn_review_scan_prefix

来源：`isabelle/Binomial_Intermediate_Draft.thy:39`。

```isabelle
definition bn_review_scan_prefix ::
  "globals \<Rightarrow> bn_shape option list \<Rightarrow> nat \<Rightarrow> nat option \<Rightarrow> bool" where
  "bn_review_scan_prefix s F i j \<longleftrightarrow>
    i \<le> length F \<and>
    (case j of None \<Rightarrow> (\<forall>k < i. F ! k = None)
     | Some k \<Rightarrow> k < i \<and> (\<exists>T. F ! k = Some T \<and>
         (\<forall>l < i. \<forall>U. F ! l = Some U \<longrightarrow>
           bn_key s (value_C (bn_tree s (bn_root T))) \<le>
           bn_key s (value_C (bn_tree s (bn_root U))))))"
```

## bn_review_clear_root

来源：`isabelle/Binomial_Intermediate_Draft.thy:49`。

```isabelle
definition bn_review_clear_root ::
  "globals \<Rightarrow> StrictC'_BinomialHeap_C ptr \<Rightarrow> nat \<Rightarrow> globals" where
  "bn_review_clear_root s h i =
    t_hrs_'_update (hrs_mem_update
      (heap_update (roots_C (bn_heap s h) +\<^sub>p int i)
        (NULL :: StrictC'_BinomialTree_C ptr))) s"
```

## bn_review_detached_view

来源：`isabelle/Binomial_Intermediate_Draft.thy:56`。

```isabelle
definition bn_review_detached_view ::
  "globals \<Rightarrow> globals \<Rightarrow> StrictC'_BinomialHeap_C ptr \<Rightarrow> addr set \<Rightarrow>
    bn_shape option list \<Rightarrow> unit ptr multiset \<Rightarrow> nat \<Rightarrow> bn_shape \<Rightarrow> bool" where
  "bn_review_detached_view s0 s h P F M i T \<longleftrightarrow>
    bn_rep s0 h P F M \<and> i < length F \<and> F ! i = Some T \<and>
    s = bn_review_clear_root s0 h i"
```

## bn_review_merge_inventory

来源：`isabelle/Binomial_Intermediate_Draft.thy:63`。

```isabelle
definition bn_review_merge_inventory ::
  "globals \<Rightarrow> bn_shape option list \<Rightarrow> bn_shape option list \<Rightarrow> globals \<Rightarrow>
    bn_shape option list \<Rightarrow> bn_shape option \<Rightarrow> unit ptr multiset \<Rightarrow> bool" where
  "bn_review_merge_inventory s0 F G s bn_prefix bn_carry bn_remaining \<longleftrightarrow>
    mset (bn_forest_values s0 F) + mset (bn_forest_values s0 G) =
      mset (bn_forest_values s bn_prefix) +
      mset (case_option [] (bn_values s) bn_carry) + bn_remaining"
```

## r2_address_configuration

来源：`isabelle/Environment_Parameters.thy:2`。

```isabelle
definition r2_address_configuration ::
  "unit ptr list \<Rightarrow> addr set \<Rightarrow> addr set \<Rightarrow> bool" where
  "r2_address_configuration fs g st = (length fs = 19 \<and> ((((c_fnptr_guard (fs ! 0) \<and>
   c_fnptr_guard (fs ! 1)) \<and>
  c_fnptr_guard (fs ! 2) \<and>
  c_fnptr_guard (fs ! 3) \<and>
  c_fnptr_guard (fs ! 4)) \<and>
 (c_fnptr_guard (fs ! 5) \<and>
  c_fnptr_guard (fs ! 6)) \<and>
 c_fnptr_guard (fs ! 7) \<and>
 c_fnptr_guard (fs ! 8) \<and>
 c_fnptr_guard (fs ! 9)) \<and>
((c_fnptr_guard (fs ! 10) \<and>
  c_fnptr_guard (fs ! 11)) \<and>
 c_fnptr_guard (fs ! 12) \<and>
 c_fnptr_guard (fs ! 13) \<and>
 c_fnptr_guard (fs ! 14)) \<and>
(c_fnptr_guard (fs ! 15) \<and>
 c_fnptr_guard (fs ! 16) \<and>
 c_fnptr_guard (fs ! 17)) \<and>
c_fnptr_guard (fs ! 18) \<and>
all_distinct
 (Node
   (Node
     (Node (Node Tip (fs ! 0) False Tip)
       (fs ! 1) False
       (Node Tip (fs ! 2) False
         (Node Tip (fs ! 3) False Tip)))
     (fs ! 4) False
     (Node (Node Tip (fs ! 5) False Tip)
       (fs ! 6) False
       (Node Tip (fs ! 7) False
         (Node Tip (fs ! 8) False Tip))))
   (fs ! 9) False
   (Node
     (Node (Node Tip (fs ! 10) False Tip)
       (fs ! 11) False
       (Node Tip (fs ! 12) False
         (Node Tip (fs ! 13) False Tip)))
     (fs ! 14) False
     (Node (Node Tip (fs ! 15) False Tip)
       (fs ! 16) False
       (Node Tip (fs ! 17) False
         (Node Tip (fs ! 18) False
           Tip))))) \<and>
g \<inter> st = {}))"
```

## audit_stack_addresses

来源：`isabelle/Environment_Spec_Review.thy:3`。

```isabelle
definition audit_stack_addresses :: "addr set" where
 "audit_stack_addresses = {a. 4096 \<le> unat a \<and> unat a < 4160}"
```

## Calg_Binomial_Reviewed_Stack

来源：`isabelle/Environment_Spec_Review.thy:6`。

```isabelle
locale Calg_Binomial_Reviewed_Stack = binomial_heap_input_global_addresses +
 assumes audit_stack_configuration: "Binomial_Heap_C.\<S> = audit_stack_addresses"
```

## env_insert_leaf

来源：`isabelle/Refutation_Helper_Definitions.thy:6`。

```isabelle
definition env_insert_leaf where
  "env_insert_leaf = StrictC'_BinomialTree_C (Ptr 128) 0 1 NULL"
```

## env_insert_A

来源：`isabelle/Refutation_Helper_Definitions.thy:9`。

```isabelle
definition env_insert_A where
 "env_insert_A = \<lparr>live_blocks = (\<lambda>_. None)(16 := Some 40, 64 := Some 24, 96 := Some 8),
   reserved_bytes = block_bytes 128 4 \<union> audit_stack_addresses\<rparr>"
```

## env_insert_heap

来源：`isabelle/Refutation_Helper_Definitions.thy:13`。

```isabelle
definition env_insert_heap where
 "env_insert_heap = StrictC'_BinomialHeap_C BINOMIAL_HEAP_TYPE_MIN binomial_heap_input.int_compare 1 (Ptr 96) 1"
```

## env_insert_mem

来源：`isabelle/Refutation_Helper_Definitions.thy:16`。

```isabelle
definition env_insert_mem where
 "env_insert_mem = heap_update (Ptr 16) env_insert_heap
   (heap_update (Ptr 64) env_insert_leaf
    (heap_update (Ptr 96 :: StrictC'_BinomialTree_C ptr ptr) (Ptr 64) (\<lambda>_. 0)))"
```

## env_insert_s

来源：`isabelle/Refutation_Helper_Definitions.thy:21`。

```isabelle
definition env_insert_s where
 "env_insert_s = globals.make
   (env_insert_mem, stack_byte_typing)
   env_insert_A undefined"
```

## env_pop_A

来源：`isabelle/Refutation_Helper_Definitions.thy:26`。

```isabelle
definition env_pop_A where
 "env_pop_A = \<lparr>live_blocks = (\<lambda>_. None)(16 := Some 40, 64 := Some 24,
 96 := Some 24, 160 := Some 24, 192 := Some 16, 224 := Some 8),
 reserved_bytes = block_bytes 256 8 \<union> audit_stack_addresses\<rparr>"
```

## env_pop_leaf

来源：`isabelle/Refutation_Helper_Definitions.thy:31`。

```isabelle
definition env_pop_leaf where
 "env_pop_leaf v = StrictC'_BinomialTree_C (Ptr v) 0 1 NULL"
```

## env_pop_parent

来源：`isabelle/Refutation_Helper_Definitions.thy:34`。

```isabelle
definition env_pop_parent where
 "env_pop_parent = StrictC'_BinomialTree_C (Ptr 256) 1 1 (Ptr 224)"
```

## env_pop_heap

来源：`isabelle/Refutation_Helper_Definitions.thy:37`。

```isabelle
definition env_pop_heap where
 "env_pop_heap = StrictC'_BinomialHeap_C BINOMIAL_HEAP_TYPE_MIN binomial_heap_input.int_compare 3 (Ptr 192) 2"
```

## env_pop_mem

来源：`isabelle/Refutation_Helper_Definitions.thy:40`。

```isabelle
definition env_pop_mem where
 "env_pop_mem = heap_update (Ptr 16) env_pop_heap
 (heap_update (Ptr 64) (env_pop_leaf 260)
 (heap_update (Ptr 96) env_pop_parent
 (heap_update (Ptr 160) (env_pop_leaf 256)
 (heap_update (Ptr 192) (Ptr 64 :: StrictC'_BinomialTree_C ptr)
 (heap_update (Ptr 200) (Ptr 96 :: StrictC'_BinomialTree_C ptr)
 (heap_update (Ptr 224) (Ptr 160 :: StrictC'_BinomialTree_C ptr)
 (heap_update (Ptr 260) (1 :: 32 signed word)
 (heap_update (Ptr 256) (0 :: 32 signed word) (\<lambda>_. 0)))))))))"
```

## env_pop_s

来源：`isabelle/Refutation_Helper_Definitions.thy:51`。

```isabelle
definition env_pop_s where
 "env_pop_s = globals.make (env_pop_mem, stack_byte_typing) env_pop_A undefined"
```

## env_pop_F

来源：`isabelle/Refutation_Helper_Definitions.thy:54`。

```isabelle
definition env_pop_F where
 "env_pop_F = [Some (BN (Ptr 64) []), Some (BN (Ptr 96) [BN (Ptr 160) []])]"
```

## bn_r3_old_node_fields

来源：`isabelle/Repair_Field_Model.thy:3`。

```isabelle
definition bn_r3_old_node_fields where
 "bn_r3_old_node_fields s N t M \<longleftrightarrow>
  (\<forall>p\<in>N \<inter> M.
   value_C (bn_tree t p)=value_C (bn_tree s p) \<and>
   order_C (bn_tree t p)=order_C (bn_tree s p) \<and>
   subtrees_C (bn_tree t p)=subtrees_C (bn_tree s p))"
```

## bn_r3_release

来源：`isabelle/Repair_Field_Model.thy:10`。

```isabelle
definition bn_r3_release where
  "bn_r3_release s D W t \<longleftrightarrow>
    allocator_wf (phantom_machine_state_' t) \<and>
    reserved_bytes (phantom_machine_state_' t) = reserved_bytes (phantom_machine_state_' s) \<and>
    hrs_htd (t_hrs_' t) = hrs_htd (t_hrs_' s) \<and>
    (\<forall>q. live_blocks (phantom_machine_state_' t) q =
      (if q\<in>D then None else live_blocks (phantom_machine_state_' s) q)) \<and>
    (\<forall>x\<in>reserved_bytes (phantom_machine_state_' s). hrs_mem (t_hrs_' t) x = hrs_mem (t_hrs_' s) x) \<and>
    (\<forall>q n. live_blocks (phantom_machine_state_' s) q = Some n \<longrightarrow> q\<notin>W \<longrightarrow>
      (\<forall>x\<in>block_bytes q n. hrs_mem (t_hrs_' t) x = hrs_mem (t_hrs_' s) x))"
```

## r2_dec

来源：`isabelle/Repair_Phase_Model.thy:5`。

```isabelle
definition r2_dec where
 "r2_dec s p = t_hrs_'_update (hrs_mem_update
   (heap_update p ((bn_tree s p)\<lparr>refcount_C:=refcount_C (bn_tree s p)-1\<rparr>))) s"
```

## r3_count

来源：`isabelle/Repair_Phase_Model.thy:13`。

```isabelle
definition r3_count where
 "r3_count s h result = (if result=0 then s else
  t_hrs_'_update (hrs_mem_update (heap_update h
   ((bn_heap s h)\<lparr>num_values_C:=num_values_C (bn_heap s h)+1\<rparr>))) s)"
```

## bn_r3_rollback_entry

来源：`isabelle/Repair_Phase_Model.thy:20`。

```isabelle
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
```

## bn_r3_rollback_throw

来源：`isabelle/Repair_Phase_Model.thy:36`。

```isabelle
definition bn_r3_rollback_throw where
 "bn_r3_rollback_throw new_roots i bn_carry = do {
    ret \<leftarrow> liftE (binomial_heap_merge_undo' new_roots i);
    ret \<leftarrow> liftE (binomial_tree_unref' bn_carry);
    throw (0::32 signed word)}"
```

## bn_r3_carry_loop_step

来源：`isabelle/Repair_Phase_Model.thy:46`。

```isabelle
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
```

## bn_r2_allocation_bytes

来源：`isabelle/Revised_Ownership_Model.thy:5`。

```isabelle
definition bn_r2_allocation_bytes where
 "bn_r2_allocation_bytes s b =
   (case live_blocks (phantom_machine_state_' s) b of
      None \<Rightarrow> {} | Some n \<Rightarrow> block_bytes b n)"
```

## bn_r2_released_bytes

来源：`isabelle/Revised_Ownership_Model.thy:10`。

```isabelle
definition bn_r2_released_bytes where
 "bn_r2_released_bytes s B = (\<Union>b\<in>B. bn_r2_allocation_bytes s b)"
```

## bn_r2_span_supported

来源：`isabelle/Revised_Ownership_Model.thy:13`。

```isabelle
definition bn_r2_span_supported where
 "bn_r2_span_supported s W B \<longleftrightarrow>
    W \<subseteq> reserved_bytes (phantom_machine_state_' s) \<or>
    (\<exists>b n. live_blocks (phantom_machine_state_' s) b = Some n \<and>
       b \<notin> B \<and> W \<subseteq> block_bytes b n)"
```

## bn_r2_slot_supported

来源：`isabelle/Revised_Ownership_Model.thy:19`。

```isabelle
definition bn_r2_slot_supported ::
 "globals \<Rightarrow> StrictC'_BinomialTree_C ptr ptr \<Rightarrow> addr set \<Rightarrow> bool" where
 "bn_r2_slot_supported s q B \<longleftrightarrow>
    c_guard q \<and> bn_r2_span_supported s (ptr_span q) B"
```

## bn_r2_slots_supported

来源：`isabelle/Revised_Ownership_Model.thy:24`。

```isabelle
definition bn_r2_slots_supported where
 "bn_r2_slots_supported s qs B \<longleftrightarrow>
    (\<forall>q\<in>set qs. bn_r2_slot_supported s q B)"
```

## bn_r2_graph_storage

来源：`isabelle/Revised_Ownership_Model.thy:28`。

```isabelle
definition bn_r2_graph_storage where
 "bn_r2_graph_storage s N slots \<longleftrightarrow>
    bn_ds_graph_storage s N slots \<and>
    bn_r2_slots_supported s slots (bn_ds_graph_blocks s N)"
```

## bn_r2_tree_merge_pre

来源：`isabelle/Revised_Ownership_Model.thy:33`。

```isabelle
definition bn_r2_tree_merge_pre where
 "bn_r2_tree_merge_pre s h P T U N edges slots held \<longleftrightarrow>
   c_guard h \<and> live_blocks (phantom_machine_state_' s) (ptr_val h) = Some (size_of TYPE(StrictC'_BinomialHeap_C)) \<and>
   ptr_val h \<notin> bn_ds_graph_blocks s N \<and> heap_type_C (bn_heap s h) = BINOMIAL_HEAP_TYPE_MIN \<and>
   compare_func_C (bn_heap s h) = binomial_heap_input.int_compare \<and>
   bn_ds_tree_layout s P T \<and> bn_ds_tree_layout s P U \<and>
   distinct (bn_blocks s T @ bn_blocks s U) \<and>
   bn_rank T = bn_rank U \<and> bn_rank T < 65535 \<and>
   bn_root T \<in> N \<and> bn_root U \<in> N \<and>
   bn_ds_graph s P N edges slots held {} \<and> bn_r2_graph_storage s N slots \<and>
   (\<forall>p \<in> N. unat (refcount_C (bn_tree s p)) < 65535)"
```

## bn_r2_merge_pre

来源：`isabelle/Revised_Ownership_Model.thy:45`。

```isabelle
definition bn_r2_merge_pre where
 "bn_r2_merge_pre s h other P F G ext held N edges \<longleftrightarrow>
   bn_ds_forest_view s h P F \<and> bn_ds_forest_view s other P G \<and>
   distinct (concat (map (case_option [] (bn_blocks s)) (F @ G))) \<and>
   live_blocks (phantom_machine_state_' s) (ptr_val h) = Some (size_of TYPE(StrictC'_BinomialHeap_C)) \<and>
   (roots_C (bn_heap s h) = NULL \<or> bn_ds_owned_array s (roots_C (bn_heap s h)) (length F)) \<and>
   ptr_val h \<notin> bn_ds_graph_blocks s N \<and>
   ptr_val (roots_C (bn_heap s h)) \<notin> bn_ds_graph_blocks s N \<and>
   bn_r2_slots_supported s ext
     (insert (ptr_val h) (insert (ptr_val (roots_C (bn_heap s h))) (bn_ds_graph_blocks s N))) \<and>
   bn_r2_span_supported s (ptr_span other)
     (insert (ptr_val h) (insert (ptr_val (roots_C (bn_heap s h))) (bn_ds_graph_blocks s N))) \<and>
   (roots_C (bn_heap s h) = NULL \<or> ptr_val (roots_C (bn_heap s h)) \<noteq> ptr_val h) \<and>
   h \<noteq> other \<and>
   bn_ds_graph s P N edges (bn_ds_slots (roots_C (bn_heap s h)) (length F) @ ext) held {} \<and>
   bn_r2_graph_storage s N (bn_ds_slots (roots_C (bn_heap s h)) (length F) @ ext) \<and>
   (\<forall>T. Some T \<in> set G \<longrightarrow> bn_root T \<in> N) \<and>
   length (bn_forest_values s F) + length (bn_forest_values s G) < 2^32 \<and>
   (\<forall>p \<in> N. unat (refcount_C (bn_tree s p)) < 65533)"
```

## bn_r2_merge_success

来源：`isabelle/Revised_Ownership_Model.thy:65`。

```isabelle
definition bn_r2_merge_success where
 "bn_r2_merge_success s h P F G ext held N t \<longleftrightarrow>
   (\<exists>H N' edges'. bn_ds_forest_view t h P H \<and> distinct (bn_owned t h H) \<and>
     mset (bn_forest_values t H) = mset (bn_forest_values s F) + mset (bn_forest_values s G) \<and>
     num_values_C (bn_heap t h) = num_values_C (bn_heap s h) \<and>
     bn_ds_graph t P N' edges' (bn_ds_slots (roots_C (bn_heap t h)) (length H) @ ext) held {} \<and>
     bn_r2_graph_storage t N' (bn_ds_slots (roots_C (bn_heap t h)) (length H) @ ext) \<and>
     bn_r3_old_node_fields s N t N' \<and>
     bn_ds_storage_transition s (insert (ptr_val h) (insert (ptr_val (roots_C (bn_heap s h))) (bn_ds_graph_blocks s N)))
       t (insert (ptr_val h) (insert (ptr_val (roots_C (bn_heap t h))) (bn_ds_graph_blocks t N')))) \<and>
   hrs_htd (t_hrs_' t) = hrs_htd (t_hrs_' s) \<and>
   (\<forall>a \<in> P. hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a)"
```

## bn_r2_finish_base

来源：`isabelle/Revised_Ownership_Model.thy:78`。

```isabelle
definition bn_r2_finish_base where
 "bn_r2_finish_base s h P G N edges slots held \<longleftrightarrow>
   bn_ds_forest_view s h P G \<and> distinct (bn_owned s h G) \<and>
   live_blocks (phantom_machine_state_' s) (ptr_val h) = Some (size_of TYPE(StrictC'_BinomialHeap_C)) \<and>
   (roots_C (bn_heap s h) = NULL \<or> bn_ds_owned_array s (roots_C (bn_heap s h)) (length G)) \<and>
   ptr_val h \<notin> bn_ds_graph_blocks s N \<and>
   ptr_val (roots_C (bn_heap s h)) \<notin> bn_ds_graph_blocks s N \<and>
   bn_ds_graph s P N edges (bn_ds_slots (roots_C (bn_heap s h)) (length G) @ slots) held {} \<and>
   bn_r2_graph_storage s N (bn_ds_slots (roots_C (bn_heap s h)) (length G) @ slots)"
```

## bn_r2_carry_done

来源：`isabelle/Revised_Ownership_Model.thy:88`。

```isabelle
definition bn_r2_carry_done where
 "bn_r2_carry_done s0 h P F G ext held N a n result t \<longleftrightarrow>
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
   bn_r2_graph_storage t N'
     (bn_ds_slots (roots_C (bn_heap s0 h)) (length F) @ bn_ds_slots a n @ ext) \<and>
   N \<subseteq> N' \<and> bn_r3_old_node_fields s0 N t N' \<and>
   live_blocks (phantom_machine_state_' t) (ptr_val a) = Some (8*n) \<and>
   bn_ds_storage_transition s0 (bn_ds_graph_blocks s0 N)
      t (insert (ptr_val a) (bn_ds_graph_blocks t N')))"
```

## bn_r2_carry_inv

来源：`isabelle/Revised_Ownership_Model.thy:108`。

```isabelle
definition bn_r2_carry_inv where
 "bn_r2_carry_inv s0 h other P F G ext held N a n state s \<longleftrightarrow>
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
   bn_r2_graph_storage s N'
     (bn_ds_slots (roots_C (bn_heap s0 h)) (length F) @ bn_ds_slots a (unat i) @ ext) \<and>
   N \<subseteq> N' \<and> bn_r3_old_node_fields s0 N s N' \<and>
   live_blocks (phantom_machine_state_' s) (ptr_val a) = Some (8*n) \<and>
   bn_ds_storage_transition s0 (bn_ds_graph_blocks s0 N)
     s (insert (ptr_val a) (bn_ds_graph_blocks s N')) \<and>
   hrs_htd (t_hrs_' s) = hrs_htd (t_hrs_' s0))"
```
