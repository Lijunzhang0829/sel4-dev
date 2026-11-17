theory Binomial_Heap_C imports Binomial_Environment begin

text \<open>Fixed HOL schema and X64 layouts. No C file is loaded here.\<close>

setup \<open>
  let
    val tree = Type ("Binomial_Heap_C.StrictC'_BinomialTree_C", []);
    fun ptr T = Type (\<^type_name>\<open>ptr\<close>, [T]);
  in RecursiveRecordPackage.define_record_type
    [{record_name = "StrictC'_BinomialTree_C", fields =
      [{fldname="value_C", fldty=ptr \<^typ>\<open>unit\<close>},
       {fldname="order_C", fldty=\<^typ>\<open>16 word\<close>},
       {fldname="refcount_C", fldty=\<^typ>\<open>16 word\<close>},
       {fldname="subtrees_C", fldty=ptr (ptr tree)}]}] end
\<close>

setup \<open>
  RecursiveRecordPackage.define_record_type
    [{record_name = "StrictC'_BinomialHeap_C", fields =
      [{fldname="heap_type_C", fldty=\<^typ>\<open>32 signed word\<close>},
       {fldname="compare_func_C", fldty=\<^typ>\<open>unit ptr\<close>},
       {fldname="num_values_C", fldty=\<^typ>\<open>32 word\<close>},
       {fldname="roots_C", fldty=\<^typ>\<open>StrictC'_BinomialTree_C ptr ptr\<close>},
       {fldname="roots_length_C", fldty=\<^typ>\<open>32 word\<close>}]}]
\<close>

ML_file "HOL_Record_Memory.ML"

local_setup \<open>UMM_Proofs.c_type_name_instantiation "StrictC'_BinomialTree_C"\<close>

definition StrictC'_BinomialTree_C_tag where
  "StrictC'_BinomialTree_C_tag \<equiv>
final_pad 3
 (ti_typ_pad_combine TYPE(StrictC'_BinomialTree_C ptr ptr) subtrees_C
   (subtrees_C_update \<circ> (\<lambda>x _. x)) 0 ''subtrees_C''
   (ti_typ_pad_combine TYPE(16 word) refcount_C
     (refcount_C_update \<circ> (\<lambda>x _. x)) 0 ''refcount_C''
     (ti_typ_pad_combine TYPE(16 word) order_C
       (order_C_update \<circ> (\<lambda>x _. x)) 0 ''order_C''
       (ti_typ_pad_combine TYPE(unit ptr) value_C
         (value_C_update \<circ> (\<lambda>x _. x)) 0 ''value_C''
         (empty_typ_info 0
           [CHR ''S'', CHR ''t'', CHR ''r'', CHR ''i'', CHR ''c'',
            CHR ''t'', CHR ''C'', CHR 0x27, CHR ''_'', CHR ''B'', CHR ''i'',
            CHR ''n'', CHR ''o'', CHR ''m'', CHR ''i'', CHR ''a'',
            CHR ''l'', CHR ''T'', CHR ''r'', CHR ''e'', CHR ''e'',
            CHR ''_'', CHR ''C''])))))"

instantiation StrictC'_BinomialTree_C :: c_type
begin
  definition typ_info_t_StrictC'_BinomialTree_C where
    "typ_info_t_StrictC'_BinomialTree_C (x :: StrictC'_BinomialTree_C itself) = StrictC'_BinomialTree_C_tag"
  instance by standard (simp_all add: typ_info_t_StrictC'_BinomialTree_C_def StrictC'_BinomialTree_C_tag_def)
end

setup \<open>HOL_Record_Memory.certify "StrictC'_BinomialTree_C" 4\<close>
declare StrictC'_BinomialTree_C_tag_def[simp, typ_tag_defs]
declare typ_info_t_StrictC'_BinomialTree_C_def[typ_info_simps]


local_setup \<open>UMM_Proofs.c_type_name_instantiation "StrictC'_BinomialHeap_C"\<close>

definition StrictC'_BinomialHeap_C_tag where
  "StrictC'_BinomialHeap_C_tag \<equiv>
final_pad 3
 (ti_typ_pad_combine TYPE(32 word) roots_length_C
   (roots_length_C_update \<circ> (\<lambda>x _. x)) 0 ''roots_length_C''
   (ti_typ_pad_combine TYPE(StrictC'_BinomialTree_C ptr ptr) roots_C
     (roots_C_update \<circ> (\<lambda>x _. x)) 0 ''roots_C''
     (ti_typ_pad_combine TYPE(32 word) num_values_C
       (num_values_C_update \<circ> (\<lambda>x _. x)) 0 ''num_values_C''
       (ti_typ_pad_combine TYPE(unit ptr) compare_func_C
         (compare_func_C_update \<circ> (\<lambda>x _. x)) 0
         ''compare_func_C''
         (ti_typ_pad_combine TYPE(32 signed word) heap_type_C
           (heap_type_C_update \<circ> (\<lambda>x _. x)) 0 ''heap_type_C''
           (empty_typ_info 0
             [CHR ''S'', CHR ''t'', CHR ''r'', CHR ''i'', CHR ''c'',
              CHR ''t'', CHR ''C'', CHR 0x27, CHR ''_'', CHR ''B'',
              CHR ''i'', CHR ''n'', CHR ''o'', CHR ''m'', CHR ''i'',
              CHR ''a'', CHR ''l'', CHR ''H'', CHR ''e'', CHR ''a'',
              CHR ''p'', CHR ''_'', CHR ''C'']))))))"

instantiation StrictC'_BinomialHeap_C :: c_type
begin
  definition typ_info_t_StrictC'_BinomialHeap_C where
    "typ_info_t_StrictC'_BinomialHeap_C (x :: StrictC'_BinomialHeap_C itself) = StrictC'_BinomialHeap_C_tag"
  instance by standard (simp_all add: typ_info_t_StrictC'_BinomialHeap_C_def StrictC'_BinomialHeap_C_tag_def)
end

setup \<open>HOL_Record_Memory.certify "StrictC'_BinomialHeap_C" 5\<close>
declare StrictC'_BinomialHeap_C_tag_def[simp, typ_tag_defs]
declare typ_info_t_StrictC'_BinomialHeap_C_def[typ_info_simps]



record globals =
  t_hrs_' :: "(64 word \<Rightarrow> 8 word) \<times>
(64 word
 \<Rightarrow> bool \<times>
               (nat
                \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                                unit) typ_desc \<times>
                               bool) option))"
  phantom_machine_state_' :: "calg_allocator"
  ghost'state_' :: "unit"


consts \<S> :: "addr set" \<G> :: "addr set"

interpretation globals: globals_stack_heap_raw_state t_hrs_' t_hrs_'_update \<S> \<G>
  by unfold_locales (simp_all add: hrs_mem_def hrs_mem_update_def hrs_htd_def hrs_htd_update_def case_prod_unfold)

setup \<open>fold (fn n =>
  Theory.specify_const ((Binding.qualify true "binomial_heap_input" (Binding.name n),
    \<^typ>\<open>unit ptr\<close>), NoSyn) #> snd)
  ["binomial_heap_num_entries", "binomial_heap_merge_undo", "binomial_heap_insert", "binomial_tree_unref", "binomial_tree_merge", "binomial_heap_merge", "binomial_heap_free", "calg_binomial_new", "binomial_tree_ref", "binomial_heap_pop", "binomial_heap_new", "binomial_heap_cmp", "init'globals", "int_compare", "int_equal", "memcpy", "malloc", "calloc", "free"]\<close>

locale binomial_heap_input_global_addresses =
  assumes "fnptr_guard": "c_fnptr_guard binomial_heap_input.free"
    "c_fnptr_guard binomial_heap_input.calloc"
    "c_fnptr_guard binomial_heap_input.malloc"
    "c_fnptr_guard binomial_heap_input.memcpy"
    "c_fnptr_guard binomial_heap_input.int_equal"
    "c_fnptr_guard binomial_heap_input.int_compare"
    "c_fnptr_guard binomial_heap_input.init'globals"
    "c_fnptr_guard binomial_heap_input.binomial_heap_cmp"
    "c_fnptr_guard binomial_heap_input.binomial_heap_new"
    "c_fnptr_guard binomial_heap_input.binomial_tree_unref"
    "c_fnptr_guard binomial_heap_input.binomial_heap_merge_undo"
    "c_fnptr_guard binomial_heap_input.binomial_tree_ref"
    "c_fnptr_guard binomial_heap_input.binomial_tree_merge"
    "c_fnptr_guard binomial_heap_input.binomial_heap_merge"
    "c_fnptr_guard binomial_heap_input.binomial_heap_pop"
    "c_fnptr_guard binomial_heap_input.calg_binomial_new"
    "c_fnptr_guard binomial_heap_input.binomial_heap_free"
    "c_fnptr_guard binomial_heap_input.binomial_heap_insert"
    "c_fnptr_guard binomial_heap_input.binomial_heap_num_entries"
  and "all_distinct" ["fun_ptr_distinct"]: "all_distinct
 (Node
   (Node
     (Node (Node Tip binomial_heap_input.free False Tip)
       binomial_heap_input.calloc False
       (Node Tip binomial_heap_input.malloc False
         (Node Tip binomial_heap_input.memcpy False Tip)))
     binomial_heap_input.int_equal False
     (Node (Node Tip binomial_heap_input.int_compare False Tip)
       binomial_heap_input.init'globals False
       (Node Tip binomial_heap_input.binomial_heap_cmp False
         (Node Tip binomial_heap_input.binomial_heap_new False Tip))))
   binomial_heap_input.binomial_tree_unref False
   (Node
     (Node (Node Tip binomial_heap_input.binomial_heap_merge_undo False Tip)
       binomial_heap_input.binomial_tree_ref False
       (Node Tip binomial_heap_input.binomial_tree_merge False
         (Node Tip binomial_heap_input.binomial_heap_merge False Tip)))
     binomial_heap_input.binomial_heap_pop False
     (Node (Node Tip binomial_heap_input.calg_binomial_new False Tip)
       binomial_heap_input.binomial_heap_free False
       (Node Tip binomial_heap_input.binomial_heap_insert False
         (Node Tip binomial_heap_input.binomial_heap_num_entries False
           Tip)))))"
  and "disjoint_\<G>_\<S>" ["disjoint_\<G>_\<S>"]: "\<G> \<inter> \<S> = {}"
begin
end


definition BINOMIAL_HEAP_TYPE_MIN :: "32 signed word" where
  "BINOMIAL_HEAP_TYPE_MIN \<equiv> 0"
definition BINOMIAL_HEAP_TYPE_MAX :: "32 signed word" where
  "BINOMIAL_HEAP_TYPE_MAX \<equiv> 1"
definition binomial_heap_null_value :: "unit ptr" where
  "binomial_heap_null_value \<equiv> NULL"
definition binomial_heap_null_value_initial_value :: "unit ptr" where
  "binomial_heap_null_value_initial_value \<equiv> NULL"

setup \<open>Sign.add_path "binomial_heap_input" #> Sign.add_path "malloc"\<close>
definition size_' :: nat where "size_' \<equiv> 0"
setup \<open>Sign.parent_path #> Sign.parent_path\<close>
setup \<open>Sign.add_path "binomial_heap_input" #> Sign.add_path "calloc"\<close>
definition count_' :: nat where "count_' \<equiv> 0"
definition ret'_' :: nat where "ret'_' \<equiv> 2"
definition size_' :: nat where "size_' \<equiv> 1"
setup \<open>Sign.parent_path #> Sign.parent_path\<close>

definition binomial_heap_num_entries' :: "StrictC'_BinomialHeap_C ptr
\<Rightarrow> (unit, 32 word, globals) spec_monad" where
  "binomial_heap_num_entries' \<equiv> \<lambda>heap::StrictC'_BinomialHeap_C ptr.
   do {
     guard (\<lambda>s::globals. c_guard heap);
     gets
      (\<lambda>s::globals. num_values_C (h_val (hrs_mem (t_hrs_' s)) heap))
   }"

definition binomial_tree_ref' :: "StrictC'_BinomialTree_C ptr \<Rightarrow> (unit, unit, globals) spec_monad" where
  "binomial_tree_ref' \<equiv> \<lambda>tree::StrictC'_BinomialTree_C ptr.
   unless (tree = NULL)
    (do {
       guard (\<lambda>s::globals. c_guard tree);
       guard
        (\<lambda>s::globals.
            uint (refcount_C (h_val (hrs_mem (t_hrs_' s)) tree)) < INT_MAX);
       modify
        (t_hrs_'_update
          (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                       (64 word
                        \<Rightarrow> bool \<times>
(nat
 \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                 unit) typ_desc \<times>
                bool) option)).
              hrs_mem_update
               (heap_update tree
                 (refcount_C_update (\<lambda>a::16 word. a + 1)
                   (h_val (hrs_mem a) tree)))
               a))
     })"

definition memcpy' :: "unit ptr
\<Rightarrow> unit ptr
              \<Rightarrow> 64 word
                            \<Rightarrow> (unit, unit ptr,
     globals) spec_monad" where
  "memcpy' \<equiv> \<lambda>(dest::unit ptr) (src::unit ptr) count::64 word.
   do {
     guard (\<lambda>s::globals. allocator_wf (phantom_machine_state_' s));
     guard
      (\<lambda>s::globals.
          memcpy_ok (phantom_machine_state_' s) (ptr_val dest) (ptr_val src)
           (unat count));
     assume_result_and_state
      (\<lambda>s::globals.
          {dest} \<times>
          {t::globals.
           \<exists>h::64 word \<Rightarrow> 8 word.
              memcpy_rel (phantom_machine_state_' s) (ptr_val dest)
               (ptr_val src) (unat count) (hrs_mem (t_hrs_' s)) h \<and>
              t = t_hrs_'_update (hrs_mem_update (\<lambda>_. h)) s})
   }"

definition free' :: "unit ptr \<Rightarrow> (unit, unit, globals) spec_monad" where
  "free' \<equiv> \<lambda>ptr::unit ptr.
   do {
     guard (\<lambda>s::globals. allocator_wf (phantom_machine_state_' s));
     guard
      (\<lambda>s::globals.
          ptr = NULL \<or>
          (\<exists>y::nat.
              live_blocks (phantom_machine_state_' s) (ptr_val ptr) =
              Some y));
     assume_result_and_state
      (\<lambda>s::globals.
          {(r::unit, t::globals).
           \<exists>(h::64 word \<Rightarrow> 8 word) A::calg_allocator.
              free_rel (ptr_val ptr) (hrs_mem (t_hrs_' s))
               (phantom_machine_state_' s) h A \<and>
              t =
              t_hrs_'_update (hrs_mem_update (\<lambda>_. h)) s
              \<lparr>phantom_machine_state_' := A\<rparr>})
   }"

definition binomial_tree_unref'_functional :: "(StrictC'_BinomialTree_C ptr \<Rightarrow> (unit, unit, globals) spec_monad)
\<Rightarrow> StrictC'_BinomialTree_C ptr
              \<Rightarrow> (unit, unit, globals) spec_monad" where
  "binomial_tree_unref'_functional \<equiv> \<lambda>binomial_tree_unref'::StrictC'_BinomialTree_C ptr
                               \<Rightarrow> (unit, unit,
        globals) spec_monad.
   gfp.lub_fun
    (\<Union>
      {{r::StrictC'_BinomialTree_C ptr
           \<Rightarrow> (unit, unit, globals) spec_monad.
        r =
        (\<lambda>tree::StrictC'_BinomialTree_C ptr.
            unless (tree = NULL)
             (do {
                x::unit \<leftarrow>
                  guard (\<lambda>s::globals. c_guard tree);
                x::unit \<leftarrow>
                  guard
                   (\<lambda>s::globals.
                       - (INT_MAX + 1)
                       < uint
                          (refcount_C (h_val (hrs_mem (t_hrs_' s)) tree)));
                guard
                 (\<lambda>s::globals.
                     uint (refcount_C (h_val (hrs_mem (t_hrs_' s)) tree))
                     \<le> INT_MAX + 1);
                modify
                 (\<lambda>s::globals.
                     t_hrs_'_update
                      (hrs_mem_update
                        (heap_update tree
                          (refcount_C_update (\<lambda>a::16 word. a - 1)
                            (h_val (hrs_mem (t_hrs_' s)) tree))))
                      s);
                condition (\<lambda>s::globals.
                              UCAST(16 \<rightarrow> 32 signed)
                               (refcount_C
                                 (h_val (hrs_mem (t_hrs_' s)) tree)) =
                              0)
                  (do {
                     i::32 signed word \<leftarrow>
                       whileLoop (\<lambda>(i::32 signed word) s::globals.
                                     i <s UCAST(16 \<rightarrow> 32 signed)
     (order_C (h_val (hrs_mem (t_hrs_' s)) tree)))
                         (\<lambda>i::32 signed word.
                             do {
                               guard
                                (\<lambda>s::globals.
                                    c_guard
                                     (subtrees_C
 (h_val (hrs_mem (t_hrs_' s)) tree) +\<^sub>p
sint i));
                               tree::StrictC'_BinomialTree_C ptr \<leftarrow>
                                 gets
                                  (\<lambda>s::globals.
h_val (hrs_mem (t_hrs_' s))
 (subtrees_C (h_val (hrs_mem (t_hrs_' s)) tree) +\<^sub>p sint i));
                               ret::unit \<leftarrow>
                                 binomial_tree_unref' tree;
                               x::unit \<leftarrow>
                                 guard
                                  (\<lambda>_.
0 \<le> (2147483649::int) + sint i);
                               guard (\<lambda>_. sint i \<le> INT_MAX - 1);
                               return (i + 1)
                             })
                        0;
                     ptr::unit ptr \<leftarrow>
                       gets
                        (\<lambda>s::globals.
                            PTR_COERCE
                             (StrictC'_BinomialTree_C ptr \<rightarrow> unit)
                             (subtrees_C
                               (h_val (hrs_mem (t_hrs_' s)) tree)));
                     ret::unit \<leftarrow> free' ptr;
                     free'
                      (PTR_COERCE
                        (StrictC'_BinomialTree_C \<rightarrow> unit)
                        tree)
                   })
                  skip
              }))}})"

definition fixp_binomial_tree_unref' :: "StrictC'_BinomialTree_C ptr \<Rightarrow> (unit, unit, globals) spec_monad" where
  "fixp_binomial_tree_unref' \<equiv> gfp.fixp_fun binomial_tree_unref'_functional"

definition binomial_tree_unref' :: "StrictC'_BinomialTree_C ptr \<Rightarrow> (unit, unit, globals) spec_monad" where
  "binomial_tree_unref' \<equiv> fixp_binomial_tree_unref'"

definition malloc' :: "64 word \<Rightarrow> (unit, unit ptr, globals) spec_monad" where
  "malloc' \<equiv> \<lambda>size::64 word.
   do {
     guard
      (\<lambda>s::globals. binomial_heap_input.malloc.size_' < unat size);
     guard (\<lambda>s::globals. allocator_wf (phantom_machine_state_' s));
     assume_result_and_state
      (\<lambda>s::globals.
          {(p::unit ptr, t::globals).
           \<exists>(h::64 word \<Rightarrow> 8 word) A::calg_allocator.
              malloc_rel (unat size) (hrs_mem (t_hrs_' s))
               (phantom_machine_state_' s) (ptr_val p) h A \<and>
              t =
              t_hrs_'_update (hrs_mem_update (\<lambda>_. h)) s
              \<lparr>phantom_machine_state_' := A\<rparr>})
   }"

definition int_compare' :: "unit ptr
\<Rightarrow> unit ptr
              \<Rightarrow> (unit, 32 signed word, globals) spec_monad" where
  "int_compare' \<equiv> \<lambda>(vlocation1::unit ptr) vlocation2::unit ptr.
   do {
     location1::32 signed word ptr \<leftarrow>
       return (PTR_COERCE(unit \<rightarrow> 32 signed word) vlocation1);
     location2::32 signed word ptr \<leftarrow>
       return (PTR_COERCE(unit \<rightarrow> 32 signed word) vlocation2);
     x::unit \<leftarrow> guard (\<lambda>_. c_guard location2);
     guard (\<lambda>_. c_guard location1);
     condition (\<lambda>s::globals.
                   h_val (hrs_mem (t_hrs_' s)) location1
                   <s h_val (hrs_mem (t_hrs_' s)) location2)
       (return (- 1))
       (condition (\<lambda>s::globals.
                      h_val (hrs_mem (t_hrs_' s)) location2
                      <s h_val (hrs_mem (t_hrs_' s)) location1)
          (return 1)
          (return 0))
   }"

definition binomial_heap_cmp' :: "StrictC'_BinomialHeap_C ptr
\<Rightarrow> unit ptr
              \<Rightarrow> unit ptr
                            \<Rightarrow> (unit, 32 signed word,
     globals) spec_monad" where
  "binomial_heap_cmp' \<equiv> \<lambda>(heap::StrictC'_BinomialHeap_C ptr) (data1::unit ptr)
   data2::unit ptr.
   do {
     guard (\<lambda>s::globals. c_guard heap);
     condition (\<lambda>s::globals.
                   heap_type_C (h_val (hrs_mem (t_hrs_' s)) heap) =
                   BINOMIAL_HEAP_TYPE_MIN)
       (do {
          p::unit ptr \<leftarrow>
            gets
             (\<lambda>xb::globals.
                 compare_func_C (h_val (hrs_mem (t_hrs_' xb)) heap));
          map_of_default \<top>
           [(binomial_heap_input.int_compare, int_compare')] p data1 data2
        })
       (do {
          p::unit ptr \<leftarrow>
            gets
             (\<lambda>xb::globals.
                 compare_func_C (h_val (hrs_mem (t_hrs_' xb)) heap));
          ret'int'1::32 signed word \<leftarrow>
            map_of_default \<top>
             [(binomial_heap_input.int_compare, int_compare')] p data1
             data2;
          x::unit \<leftarrow>
            guard (\<lambda>_. sint ret'int'1 \<le> INT_MAX + 1);
          guard (\<lambda>_. - sint ret'int'1 \<le> INT_MAX);
          return (- ret'int'1)
        })
   }"

definition binomial_tree_merge' :: "StrictC'_BinomialHeap_C ptr
\<Rightarrow> StrictC'_BinomialTree_C ptr
              \<Rightarrow> StrictC'_BinomialTree_C ptr
                            \<Rightarrow> (unit,
     StrictC'_BinomialTree_C ptr, globals) spec_monad" where
  "binomial_tree_merge' \<equiv> \<lambda>(heap::StrictC'_BinomialHeap_C ptr)
   (tree1::StrictC'_BinomialTree_C ptr) tree2::StrictC'_BinomialTree_C ptr.
   do {
     guard (\<lambda>s::globals. c_guard tree1);
     guard (\<lambda>s::globals. c_guard tree2);
     data1::unit ptr \<leftarrow>
       gets
        (\<lambda>xb::globals.
            value_C (h_val (hrs_mem (t_hrs_' xb)) tree1));
     data2::unit ptr \<leftarrow>
       gets
        (\<lambda>xc::globals.
            value_C (h_val (hrs_mem (t_hrs_' xc)) tree2));
     ret'int'1::32 signed word \<leftarrow>
       binomial_heap_cmp' heap data1 data2;
     (tree1::StrictC'_BinomialTree_C ptr,
       tree2::StrictC'_BinomialTree_C ptr) \<leftarrow>
       return (if 0 <s ret'int'1 then (tree2, tree1) else (tree1, tree2));
     ret'ptr_to_void'1::unit ptr \<leftarrow>
       malloc' (word_of_nat (size_of TYPE(StrictC'_BinomialTree_C)));
     new_tree::StrictC'_BinomialTree_C ptr \<leftarrow>
       return
        (PTR_COERCE(unit \<rightarrow> StrictC'_BinomialTree_C)
          ret'ptr_to_void'1);
     condition (\<lambda>s::globals. new_tree = NULL)
       (return NULL)
       (do {
          guard (\<lambda>s::globals. c_guard new_tree);
          modify
           (t_hrs_'_update
             (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                          (64 word
                           \<Rightarrow> bool \<times>
   (nat
    \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                    unit) typ_desc \<times>
                   bool) option)).
                 hrs_mem_update
                  (heap_update new_tree
                    (refcount_C_update (\<lambda>a::16 word. 0)
                      (h_val (hrs_mem a) new_tree)))
                  a));
          guard
           (\<lambda>s::globals.
               uint (order_C (h_val (hrs_mem (t_hrs_' s)) tree1))
               < INT_MAX);
          guard (\<lambda>s::globals. c_guard tree1);
          modify
           (t_hrs_'_update
             (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                          (64 word
                           \<Rightarrow> bool \<times>
   (nat
    \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                    unit) typ_desc \<times>
                   bool) option)).
                 hrs_mem_update
                  (heap_update new_tree
                    (order_C_update
                      (\<lambda>b::16 word.
                          order_C (h_val (hrs_mem a) tree1) + 1)
                      (h_val (hrs_mem a) new_tree)))
                  a));
          modify
           (t_hrs_'_update
             (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                          (64 word
                           \<Rightarrow> bool \<times>
   (nat
    \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                    unit) typ_desc \<times>
                   bool) option)).
                 hrs_mem_update
                  (heap_update new_tree
                    (value_C_update
                      (\<lambda>b::unit ptr.
                          value_C (h_val (hrs_mem a) tree1))
                      (h_val (hrs_mem a) new_tree)))
                  a));
          size::64 word \<leftarrow>
            gets
             (\<lambda>xm::globals.
                 word_of_nat (size_of TYPE(StrictC'_BinomialTree_C ptr)) *
                 UCAST(16 \<rightarrow> 64)
                  (order_C (h_val (hrs_mem (t_hrs_' xm)) new_tree)));
          ret'ptr_to_void'1::unit ptr \<leftarrow> malloc' size;
          modify
           (t_hrs_'_update
             (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                          (64 word
                           \<Rightarrow> bool \<times>
   (nat
    \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                    unit) typ_desc \<times>
                   bool) option)).
                 hrs_mem_update
                  (heap_update new_tree
                    (subtrees_C_update
                      (\<lambda>a::StrictC'_BinomialTree_C ptr ptr.
                          PTR_COERCE
                           (unit \<rightarrow> StrictC'_BinomialTree_C ptr)
                           ret'ptr_to_void'1)
                      (h_val (hrs_mem a) new_tree)))
                  a));
          condition (\<lambda>s::globals.
                        subtrees_C (h_val (hrs_mem (t_hrs_' s)) new_tree) =
                        NULL)
            (do {
               ret::unit \<leftarrow>
                 free'
                  (PTR_COERCE(StrictC'_BinomialTree_C \<rightarrow> unit)
                    new_tree);
               return NULL
             })
            (do {
               dest::unit ptr \<leftarrow>
                 gets
                  (\<lambda>xq::globals.
                      PTR_COERCE
                       (StrictC'_BinomialTree_C ptr \<rightarrow> unit)
                       (subtrees_C
                         (h_val (hrs_mem (t_hrs_' xq)) new_tree)));
               src::unit ptr \<leftarrow>
                 gets
                  (\<lambda>xr::globals.
                      PTR_COERCE
                       (StrictC'_BinomialTree_C ptr \<rightarrow> unit)
                       (subtrees_C (h_val (hrs_mem (t_hrs_' xr)) tree1)));
               count::64 word \<leftarrow>
                 gets
                  (\<lambda>xs::globals.
                      word_of_nat
                       (size_of TYPE(StrictC'_BinomialTree_C ptr)) *
                      UCAST(16 \<rightarrow> 64)
                       (order_C (h_val (hrs_mem (t_hrs_' xs)) tree1)));
               ret::unit ptr \<leftarrow> memcpy' dest src count;
               guard
                (\<lambda>s::globals.
                    c_guard
                     (subtrees_C
                       (h_val (hrs_mem (t_hrs_' s)) new_tree) +\<^sub>p
                      sint
                       (UCAST(16 \<rightarrow> 32 signed)
                         (order_C (h_val (hrs_mem (t_hrs_' s)) new_tree)) -
                        1)));
               guard
                (\<lambda>s::globals.
                    - INT_MAX - 1
                    < uint
                       (order_C (h_val (hrs_mem (t_hrs_' s)) new_tree)));
               guard
                (\<lambda>s::globals.
                    uint (order_C (h_val (hrs_mem (t_hrs_' s)) new_tree))
                    \<le> INT_MAX + 1);
               modify
                (t_hrs_'_update
                  (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                               (64 word
                                \<Rightarrow> bool \<times>
        (nat
         \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                         unit) typ_desc \<times>
                        bool) option)).
                      hrs_mem_update
                       (heap_update
                         (subtrees_C (h_val (hrs_mem a) new_tree) +\<^sub>p
                          sint
                           (UCAST(16 \<rightarrow> 32 signed)
                             (order_C (h_val (hrs_mem a) new_tree)) -
                            1))
                         tree2)
                       a));
               i::32 signed word \<leftarrow>
                 whileLoop (\<lambda>(i::32 signed word) s::globals.
                               i <s UCAST(16 \<rightarrow> 32 signed)
                                     (order_C
 (h_val (hrs_mem (t_hrs_' s)) new_tree)))
                   (\<lambda>i::32 signed word.
                       do {
                         guard
                          (\<lambda>s::globals.
                              c_guard
                               (subtrees_C
                                 (h_val (hrs_mem (t_hrs_' s))
                                   new_tree) +\<^sub>p
                                sint i));
                         tree::StrictC'_BinomialTree_C ptr \<leftarrow>
                           gets
                            (\<lambda>xx::globals.
                                h_val (hrs_mem (t_hrs_' xx))
                                 (subtrees_C
                                   (h_val (hrs_mem (t_hrs_' xx))
                                     new_tree) +\<^sub>p
                                  sint i));
                         ret::unit \<leftarrow> binomial_tree_ref' tree;
                         guard
                          (\<lambda>s::globals.
                              0 \<le> (2147483649::int) + sint i);
                         guard (\<lambda>s::globals. sint i < INT_MAX);
                         return (i + 1)
                       })
                  0;
               return new_tree
             })
        })
   }"

definition binomial_heap_merge_undo' :: "StrictC'_BinomialTree_C ptr ptr
\<Rightarrow> 32 word \<Rightarrow> (unit, unit, globals) spec_monad" where
  "binomial_heap_merge_undo' \<equiv> \<lambda>(new_roots::StrictC'_BinomialTree_C ptr ptr) count::32 word.
   do {
     i::32 word \<leftarrow>
       whileLoop (\<lambda>(i::32 word) s::globals. i \<le> count)
         (\<lambda>i::32 word.
             do {
               guard (\<lambda>_. c_guard (new_roots +\<^sub>p uint i));
               tree::StrictC'_BinomialTree_C ptr \<leftarrow>
                 gets
                  (\<lambda>s::globals.
                      h_val (hrs_mem (t_hrs_' s))
                       (new_roots +\<^sub>p uint i));
               ret::unit \<leftarrow> binomial_tree_unref' tree;
               return (i + 1)
             })
        0;
     free'
      (PTR_COERCE(StrictC'_BinomialTree_C ptr \<rightarrow> unit) new_roots)
   }"

definition binomial_heap_merge' :: "StrictC'_BinomialHeap_C ptr
\<Rightarrow> StrictC'_BinomialHeap_C ptr
              \<Rightarrow> (unit, 32 signed word, globals) spec_monad" where
  "binomial_heap_merge' \<equiv> \<lambda>(heap::StrictC'_BinomialHeap_C ptr)
   other::StrictC'_BinomialHeap_C ptr.
   do {
     vals::StrictC'_BinomialTree_C ptr[3] \<leftarrow> unknown;
     guard (\<lambda>s::globals. c_guard heap);
     guard (\<lambda>s::globals. c_guard other);
     max::32 word \<leftarrow>
       condition (\<lambda>s::globals.
                     roots_length_C (h_val (hrs_mem (t_hrs_' s)) other)
                     < roots_length_C (h_val (hrs_mem (t_hrs_' s)) heap))
         (gets
           (\<lambda>xc::globals.
               roots_length_C (h_val (hrs_mem (t_hrs_' xc)) heap) + 1))
         (gets
           (\<lambda>xc::globals.
               roots_length_C (h_val (hrs_mem (t_hrs_' xc)) other) + 1));
     ret'ptr_to_void'1::unit ptr \<leftarrow>
       malloc'
        (word_of_nat (size_of TYPE(StrictC'_BinomialTree_C ptr)) *
         UCAST(32 \<rightarrow> 64) max);
     new_roots::StrictC'_BinomialTree_C ptr ptr \<leftarrow>
       return
        (PTR_COERCE(unit \<rightarrow> StrictC'_BinomialTree_C ptr)
          ret'ptr_to_void'1);
     condition (\<lambda>s::globals. new_roots = NULL)
       (return 0)
       (finally
         (do {
            (carry::StrictC'_BinomialTree_C ptr, i::32 word,
              new_roots_length::32 word,
              vals::StrictC'_BinomialTree_C ptr[3]) \<leftarrow>
              whileLoop (\<lambda>(carry::StrictC'_BinomialTree_C ptr,
                            i::32 word, new_roots_length::32 word,
                            vals::StrictC'_BinomialTree_C ptr[3])
                            s::globals. i < max)
                (\<lambda>(carry::StrictC'_BinomialTree_C ptr, i::32 word,
                    new_roots_length::32 word,
                    vals::StrictC'_BinomialTree_C ptr[3]).
                    do {
                      ret::bool \<leftarrow>
                        gets
                         (\<lambda>xg::globals.
                             i < roots_length_C
                                  (h_val (hrs_mem (t_hrs_' xg)) heap));
                      ret::32 signed word \<leftarrow>
                        liftE
                         (do {
                            guard
                             (\<lambda>xi::globals.
                                 ret \<longrightarrow>
                                 c_guard
                                  (roots_C
                                    (h_val (hrs_mem (t_hrs_' xi))
heap) +\<^sub>p
                                   uint i));
                            gets
                             (\<lambda>xi::globals.
                                 if ret \<and>
                                    h_val (hrs_mem (t_hrs_' xi))
                                     (roots_C
 (h_val (hrs_mem (t_hrs_' xi)) heap) +\<^sub>p
uint i) \<noteq>
                                    NULL
                                 then 1 else 0)
                          });
                      (num_vals::32 signed word,
                        vals::StrictC'_BinomialTree_C ptr[3]) \<leftarrow>
                        liftE
                         (condition (\<lambda>s::globals. ret \<noteq> 0)
                            (do {
                               guard
                                (\<lambda>s::globals.
                                    c_guard
                                     (roots_C
 (h_val (hrs_mem (t_hrs_' s)) heap) +\<^sub>p
uint i));
                               vals::StrictC'_BinomialTree_C ptr[3] \<leftarrow>
                                 gets
                                  (\<lambda>xj::globals.
Arrays.update vals 0
 (h_val (hrs_mem (t_hrs_' xj))
   (roots_C (h_val (hrs_mem (t_hrs_' xj)) heap) +\<^sub>p uint i)));
                               return (1, vals)
                             })
                            (return (0, vals)));
                      ret::bool \<leftarrow>
                        gets
                         (\<lambda>xi::globals.
                             i < roots_length_C
                                  (h_val (hrs_mem (t_hrs_' xi)) other));
                      ret::32 signed word \<leftarrow>
                        liftE
                         (do {
                            guard
                             (\<lambda>xk::globals.
                                 ret \<longrightarrow>
                                 c_guard
                                  (roots_C
                                    (h_val (hrs_mem (t_hrs_' xk))
other) +\<^sub>p
                                   uint i));
                            gets
                             (\<lambda>xk::globals.
                                 if ret \<and>
                                    h_val (hrs_mem (t_hrs_' xk))
                                     (roots_C
 (h_val (hrs_mem (t_hrs_' xk)) other) +\<^sub>p
uint i) \<noteq>
                                    NULL
                                 then 1 else 0)
                          });
                      (num_vals::32 signed word,
                        vals::StrictC'_BinomialTree_C ptr[3]) \<leftarrow>
                        liftE
                         (condition (\<lambda>s::globals. ret \<noteq> 0)
                            (do {
                               guard
                                (\<lambda>s::globals.
                                    num_vals <s (3::32 signed word));
                               guard
                                (\<lambda>s::globals. 0 \<le>s num_vals);
                               guard
                                (\<lambda>s::globals.
                                    c_guard
                                     (roots_C
 (h_val (hrs_mem (t_hrs_' s)) other) +\<^sub>p
uint i));
                               guard
                                (\<lambda>s::globals.
                                    sint num_vals < INT_MAX);
                               vals::StrictC'_BinomialTree_C ptr[3] \<leftarrow>
                                 gets
                                  (\<lambda>xl::globals.
Arrays.update vals (unat num_vals)
 (h_val (hrs_mem (t_hrs_' xl))
   (roots_C (h_val (hrs_mem (t_hrs_' xl)) other) +\<^sub>p uint i)));
                               return (num_vals + 1, vals)
                             })
                            (return (num_vals, vals)));
                      (num_vals::32 signed word,
                        vals::StrictC'_BinomialTree_C ptr[3]) \<leftarrow>
                        liftE
                         (condition (\<lambda>s::globals.
  carry \<noteq> NULL)
                            (do {
                               guard
                                (\<lambda>s::globals.
                                    num_vals <s (3::32 signed word));
                               guard
                                (\<lambda>s::globals. 0 \<le>s num_vals);
                               guard
                                (\<lambda>s::globals.
                                    sint num_vals < INT_MAX);
                               return
                                (num_vals + 1,
                                 Arrays.update vals (unat num_vals) carry)
                             })
                            (return (num_vals, vals)));
                      new_roots_length::32 word \<leftarrow>
                        liftE
                         (condition (\<lambda>s::globals. num_vals && 1 = 1)
                            (do {
                               guard
                                (\<lambda>s::globals.
                                    c_guard (new_roots +\<^sub>p uint i));
                               guard
                                (\<lambda>s::globals.
                                    num_vals - 1 <s (3::32 signed word));
                               guard
                                (\<lambda>s::globals.
                                    0 \<le>s num_vals - 1);
                               guard
                                (\<lambda>s::globals.
                                    - INT_MAX - 1 < sint num_vals);
                               guard
                                (\<lambda>_.
                                    sint num_vals \<le> INT_MAX + 1);
                               modify
                                (t_hrs_'_update
                                  (hrs_mem_update
                                    (heap_update
(new_roots +\<^sub>p uint i) (vals.[unat (num_vals - 1)]))));
                               tree::StrictC'_BinomialTree_C ptr \<leftarrow>
                                 gets
                                  (\<lambda>xm::globals.
h_val (hrs_mem (t_hrs_' xm)) (new_roots +\<^sub>p uint i));
                               ret::unit \<leftarrow>
                                 binomial_tree_ref' tree;
                               return (i + 1)
                             })
                            (do {
                               guard
                                (\<lambda>_.
                                    c_guard (new_roots +\<^sub>p uint i));
                               modify
                                (t_hrs_'_update
                                  (hrs_mem_update
                                    (heap_update
(new_roots +\<^sub>p uint i) NULL)));
                               return new_roots_length
                             }));
                      new_carry::StrictC'_BinomialTree_C ptr \<leftarrow>
                        condition (\<lambda>s::globals.
num_vals && (2::32 signed word) \<noteq> 0)
                          (do {
                             new_carry::StrictC'_BinomialTree_C ptr \<leftarrow>
                               liftE
                                (binomial_tree_merge' heap (vals.[0])
                                  (vals.[1]));
                             ret::unit \<leftarrow>
                               when (new_carry = NULL)
                                (do {
                                   ret::unit \<leftarrow>
                                     liftE
(binomial_heap_merge_undo' new_roots i);
                                   ret::unit \<leftarrow>
                                     liftE (binomial_tree_unref' carry);
                                   throw 0
                                 });
                             return new_carry
                           })
                          (return NULL);
                      liftE
                       (do {
                          ret::unit \<leftarrow> binomial_tree_unref' carry;
                          ret::unit \<leftarrow>
                            binomial_tree_ref' new_carry;
                          return (new_carry, i + 1, new_roots_length, vals)
                        })
                    })
               (NULL, 0, 0, vals);
            i::32 word \<leftarrow>
              liftE
               (whileLoop (\<lambda>(i::32 word) s::globals.
                              i < roots_length_C
                                   (h_val (hrs_mem (t_hrs_' s)) heap))
                  (\<lambda>i::32 word.
                      do {
                        guard
                         (\<lambda>s::globals.
                             c_guard
                              (roots_C
                                (h_val (hrs_mem (t_hrs_' s)) heap) +\<^sub>p
                               uint i));
                        ret::unit \<leftarrow>
                          condition (\<lambda>s::globals.
  h_val (hrs_mem (t_hrs_' s))
   (roots_C (h_val (hrs_mem (t_hrs_' s)) heap) +\<^sub>p uint i) \<noteq>
  NULL)
                            (gets
                              (\<lambda>xi::globals.
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
            ptr::unit ptr \<leftarrow>
              gets
               (\<lambda>xh::globals.
                   PTR_COERCE
                    (StrictC'_BinomialTree_C ptr \<rightarrow> unit)
                    (roots_C (h_val (hrs_mem (t_hrs_' xh)) heap)));
            ret::unit \<leftarrow> liftE (free' ptr);
            modify
             (t_hrs_'_update
               (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                            (64 word
                             \<Rightarrow> bool \<times>
     (nat
      \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                      unit) typ_desc \<times>
                     bool) option)).
                   hrs_mem_update
                    (heap_update heap
                      (roots_C_update
                        (\<lambda>a::StrictC'_BinomialTree_C ptr ptr.
                            new_roots)
                        (h_val (hrs_mem a) heap)))
                    a));
            modify
             (t_hrs_'_update
               (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                            (64 word
                             \<Rightarrow> bool \<times>
     (nat
      \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                      unit) typ_desc \<times>
                     bool) option)).
                   hrs_mem_update
                    (heap_update heap
                      (roots_length_C_update
                        (\<lambda>a::32 word. new_roots_length)
                        (h_val (hrs_mem a) heap)))
                    a));
            throw 1
          }))
   }"

definition binomial_heap_insert' :: "StrictC'_BinomialHeap_C ptr
\<Rightarrow> unit ptr
              \<Rightarrow> (unit, 32 signed word, globals) spec_monad" where
  "binomial_heap_insert' \<equiv> \<lambda>(heap::StrictC'_BinomialHeap_C ptr) value::unit ptr.
   globals.with_fresh_stack_ptr 1 (\<lambda>a::globals. UNIV)
    (\<lambda>fake_heap\<^sub>p::StrictC'_BinomialHeap_C ptr.
        globals.with_fresh_stack_ptr 1 (\<lambda>s::globals. UNIV)
         (\<lambda>new_tree\<^sub>p::StrictC'_BinomialTree_C ptr ptr.
             do {
               ret'ptr_to_void'1::unit ptr \<leftarrow>
                 malloc'
                  (word_of_nat (size_of TYPE(StrictC'_BinomialTree_C)));
               guard (\<lambda>s::globals. c_guard new_tree\<^sub>p);
               modify
                (t_hrs_'_update
                  (hrs_mem_update
                    (heap_update new_tree\<^sub>p
                      (PTR_COERCE
                        (unit \<rightarrow> StrictC'_BinomialTree_C)
                        ret'ptr_to_void'1))));
               condition (\<lambda>s::globals.
                             h_val (hrs_mem (t_hrs_' s)) new_tree\<^sub>p =
                             NULL)
                 (return 0)
                 (do {
                    guard
                     (\<lambda>s::globals.
                         c_guard
                          (h_val (hrs_mem (t_hrs_' s)) new_tree\<^sub>p));
                    modify
                     (t_hrs_'_update
                       (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                                    (64 word
                                     \<Rightarrow> bool \<times>
             (nat
              \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                              unit) typ_desc \<times>
                             bool) option)).
                           hrs_mem_update
                            (heap_update
                              (PTR(unit ptr)
                                &(h_val (hrs_mem a)
                                   new_tree\<^sub>p\<rightarrow>[''value_C'']))
                              value)
                            a));
                    guard
                     (\<lambda>s::globals.
                         c_guard
                          (h_val (hrs_mem (t_hrs_' s)) new_tree\<^sub>p));
                    modify
                     (t_hrs_'_update
                       (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                                    (64 word
                                     \<Rightarrow> bool \<times>
             (nat
              \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                              unit) typ_desc \<times>
                             bool) option)).
                           hrs_mem_update
                            (heap_update
                              (PTR(16 word)
                                &(h_val (hrs_mem a)
                                   new_tree\<^sub>p\<rightarrow>[''order_C'']))
                              0)
                            a));
                    guard
                     (\<lambda>s::globals.
                         c_guard
                          (h_val (hrs_mem (t_hrs_' s)) new_tree\<^sub>p));
                    modify
                     (t_hrs_'_update
                       (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                                    (64 word
                                     \<Rightarrow> bool \<times>
             (nat
              \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                              unit) typ_desc \<times>
                             bool) option)).
                           hrs_mem_update
                            (heap_update
                              (PTR(16 word)
                                &(h_val (hrs_mem a)
                                   new_tree\<^sub>p\<rightarrow>[''refcount_C'']))
                              1)
                            a));
                    guard
                     (\<lambda>s::globals.
                         c_guard
                          (h_val (hrs_mem (t_hrs_' s)) new_tree\<^sub>p));
                    modify
                     (t_hrs_'_update
                       (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                                    (64 word
                                     \<Rightarrow> bool \<times>
             (nat
              \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                              unit) typ_desc \<times>
                             bool) option)).
                           hrs_mem_update
                            (heap_update
                              (PTR(StrictC'_BinomialTree_C ptr ptr)
                                &(h_val (hrs_mem a)
                                   new_tree\<^sub>p\<rightarrow>[''subtrees_C'']))
                              NULL)
                            a));
                    guard (\<lambda>s::globals. c_guard fake_heap\<^sub>p);
                    guard (\<lambda>s::globals. c_guard heap);
                    modify
                     (t_hrs_'_update
                       (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                                    (64 word
                                     \<Rightarrow> bool \<times>
             (nat
              \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                              unit) typ_desc \<times>
                             bool) option)).
                           hrs_mem_update
                            (heap_update fake_heap\<^sub>p
                              (heap_type_C_update
                                (\<lambda>b::32 signed word.
                                    heap_type_C (h_val (hrs_mem a) heap))
                                (h_val (hrs_mem a) fake_heap\<^sub>p)))
                            a));
                    modify
                     (t_hrs_'_update
                       (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                                    (64 word
                                     \<Rightarrow> bool \<times>
             (nat
              \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                              unit) typ_desc \<times>
                             bool) option)).
                           hrs_mem_update
                            (heap_update fake_heap\<^sub>p
                              (compare_func_C_update
                                (\<lambda>b::unit ptr.
                                    compare_func_C (h_val (hrs_mem a) heap))
                                (h_val (hrs_mem a) fake_heap\<^sub>p)))
                            a));
                    modify
                     (t_hrs_'_update
                       (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                                    (64 word
                                     \<Rightarrow> bool \<times>
             (nat
              \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                              unit) typ_desc \<times>
                             bool) option)).
                           hrs_mem_update
                            (heap_update fake_heap\<^sub>p
                              (num_values_C_update (\<lambda>a::32 word. 1)
                                (h_val (hrs_mem a) fake_heap\<^sub>p)))
                            a));
                    modify
                     (t_hrs_'_update
                       (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                                    (64 word
                                     \<Rightarrow> bool \<times>
             (nat
              \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                              unit) typ_desc \<times>
                             bool) option)).
                           hrs_mem_update
                            (heap_update fake_heap\<^sub>p
                              (roots_C_update
                                (\<lambda>a::StrictC'_BinomialTree_C ptr ptr.
                                    new_tree\<^sub>p)
                                (h_val (hrs_mem a) fake_heap\<^sub>p)))
                            a));
                    modify
                     (t_hrs_'_update
                       (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                                    (64 word
                                     \<Rightarrow> bool \<times>
             (nat
              \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                              unit) typ_desc \<times>
                             bool) option)).
                           hrs_mem_update
                            (heap_update fake_heap\<^sub>p
                              (roots_length_C_update
                                (\<lambda>a::32 word. 1)
                                (h_val (hrs_mem a) fake_heap\<^sub>p)))
                            a));
                    result::32 signed word \<leftarrow>
                      binomial_heap_merge' heap fake_heap\<^sub>p;
                    unless (result = 0)
                     (modify
                       (t_hrs_'_update
                         (\<lambda>a::(64 word
 \<Rightarrow> 8 word) \<times>
(64 word
 \<Rightarrow> bool \<times>
               (nat
                \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                                unit) typ_desc \<times>
                               bool) option)).
                             hrs_mem_update
                              (heap_update heap
                                (num_values_C_update
                                  (\<lambda>a::32 word. a + 1)
                                  (h_val (hrs_mem a) heap)))
                              a)));
                    tree::StrictC'_BinomialTree_C ptr \<leftarrow>
                      gets
                       (\<lambda>xu::globals.
                           h_val (hrs_mem (t_hrs_' xu)) new_tree\<^sub>p);
                    ret::unit \<leftarrow> binomial_tree_unref' tree;
                    return result
                  })
             }))"

definition binomial_heap_pop' :: "StrictC'_BinomialHeap_C ptr
\<Rightarrow> (unit, unit ptr, globals) spec_monad" where
  "binomial_heap_pop' \<equiv> \<lambda>heap::StrictC'_BinomialHeap_C ptr.
   globals.with_fresh_stack_ptr 1 (\<lambda>a::globals. UNIV)
    (\<lambda>fake_heap\<^sub>p::StrictC'_BinomialHeap_C ptr.
        do {
          guard (\<lambda>s::globals. c_guard heap);
          condition (\<lambda>s::globals.
                        num_values_C (h_val (hrs_mem (t_hrs_' s)) heap) = 0)
            (return binomial_heap_null_value)
            (do {
               (i::32 word, least_index::32 word) \<leftarrow>
                 whileLoop (\<lambda>(i::32 word, least_index::32 word)
                               s::globals.
                               i < roots_length_C
                                    (h_val (hrs_mem (t_hrs_' s)) heap))
                   (\<lambda>(i::32 word, least_index::32 word).
                       do {
                         guard
                          (\<lambda>s::globals.
                              c_guard
                               (roots_C
                                 (h_val (hrs_mem (t_hrs_' s))
                                   heap) +\<^sub>p
                                uint i));
                         least_index::32 word \<leftarrow>
                           condition (\<lambda>s::globals.
   h_val (hrs_mem (t_hrs_' s))
    (roots_C (h_val (hrs_mem (t_hrs_' s)) heap) +\<^sub>p uint i) =
   NULL)
                             (return least_index)
                             (do {
                                ret::32 signed word \<leftarrow>
                                  condition (\<lambda>s::globals.
          least_index = (0xFFFFFFFF::32 word))
                                    (return 1)
                                    (do {
                                       guard
  (\<lambda>s::globals.
      c_guard
       (h_val (hrs_mem (t_hrs_' s))
         (roots_C (h_val (hrs_mem (t_hrs_' s)) heap) +\<^sub>p uint i)));
 x::unit \<leftarrow>
   guard
    (\<lambda>s::globals.
        c_guard
         (h_val (hrs_mem (t_hrs_' s))
           (roots_C (h_val (hrs_mem (t_hrs_' s)) heap) +\<^sub>p
            uint least_index)));
 guard
  (\<lambda>s::globals.
      c_guard
       (roots_C (h_val (hrs_mem (t_hrs_' s)) heap) +\<^sub>p
        uint least_index));
 data1::unit ptr \<leftarrow>
   gets
    (\<lambda>xe::globals.
        value_C
         (h_val (hrs_mem (t_hrs_' xe))
           (h_val (hrs_mem (t_hrs_' xe))
             (roots_C (h_val (hrs_mem (t_hrs_' xe)) heap) +\<^sub>p
              uint i))));
 data2::unit ptr \<leftarrow>
   gets
    (\<lambda>xf::globals.
        value_C
         (h_val (hrs_mem (t_hrs_' xf))
           (h_val (hrs_mem (t_hrs_' xf))
             (roots_C (h_val (hrs_mem (t_hrs_' xf)) heap) +\<^sub>p
              uint least_index))));
 ret'int'2::32 signed word \<leftarrow> binomial_heap_cmp' heap data1 data2;
 return (if ret'int'2 <s 0 then 1 else 0)
                                     });
                                return
                                 (if ret \<noteq> 0 then i else least_index)
                              });
                         return (i + 1, least_index)
                       })
                  (0, 0xFFFFFFFF::32 word);
               guard
                (\<lambda>s::globals.
                    c_guard
                     (roots_C (h_val (hrs_mem (t_hrs_' s)) heap) +\<^sub>p
                      uint least_index));
               least_tree::StrictC'_BinomialTree_C ptr \<leftarrow>
                 gets
                  (\<lambda>xd::globals.
                      h_val (hrs_mem (t_hrs_' xd))
                       (roots_C
                         (h_val (hrs_mem (t_hrs_' xd)) heap) +\<^sub>p
                        uint least_index));
               modify
                (t_hrs_'_update
                  (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                               (64 word
                                \<Rightarrow> bool \<times>
        (nat
         \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                         unit) typ_desc \<times>
                        bool) option)).
                      hrs_mem_update
                       (heap_update
                         (roots_C (h_val (hrs_mem a) heap) +\<^sub>p
                          uint least_index)
                         NULL)
                       a));
               guard (\<lambda>s::globals. c_guard fake_heap\<^sub>p);
               modify
                (t_hrs_'_update
                  (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                               (64 word
                                \<Rightarrow> bool \<times>
        (nat
         \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                         unit) typ_desc \<times>
                        bool) option)).
                      hrs_mem_update
                       (heap_update fake_heap\<^sub>p
                         (heap_type_C_update
                           (\<lambda>b::32 signed word.
                               heap_type_C (h_val (hrs_mem a) heap))
                           (h_val (hrs_mem a) fake_heap\<^sub>p)))
                       a));
               modify
                (t_hrs_'_update
                  (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                               (64 word
                                \<Rightarrow> bool \<times>
        (nat
         \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                         unit) typ_desc \<times>
                        bool) option)).
                      hrs_mem_update
                       (heap_update fake_heap\<^sub>p
                         (compare_func_C_update
                           (\<lambda>b::unit ptr.
                               compare_func_C (h_val (hrs_mem a) heap))
                           (h_val (hrs_mem a) fake_heap\<^sub>p)))
                       a));
               guard (\<lambda>s::globals. c_guard least_tree);
               modify
                (t_hrs_'_update
                  (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                               (64 word
                                \<Rightarrow> bool \<times>
        (nat
         \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                         unit) typ_desc \<times>
                        bool) option)).
                      hrs_mem_update
                       (heap_update fake_heap\<^sub>p
                         (roots_C_update
                           (\<lambda>b::StrictC'_BinomialTree_C ptr ptr.
                               subtrees_C (h_val (hrs_mem a) least_tree))
                           (h_val (hrs_mem a) fake_heap\<^sub>p)))
                       a));
               modify
                (t_hrs_'_update
                  (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                               (64 word
                                \<Rightarrow> bool \<times>
        (nat
         \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                         unit) typ_desc \<times>
                        bool) option)).
                      hrs_mem_update
                       (heap_update fake_heap\<^sub>p
                         (roots_length_C_update
                           (\<lambda>b::32 word.
                               UCAST(16 \<rightarrow> 32)
                                (order_C (h_val (hrs_mem a) least_tree)))
                           (h_val (hrs_mem a) fake_heap\<^sub>p)))
                       a));
               ret'int'1::32 signed word \<leftarrow>
                 binomial_heap_merge' heap fake_heap\<^sub>p;
               condition (\<lambda>s::globals. ret'int'1 \<noteq> 0)
                 (do {
                    result::unit ptr \<leftarrow>
                      gets
                       (\<lambda>xm::globals.
                           value_C
                            (h_val (hrs_mem (t_hrs_' xm)) least_tree));
                    ret::unit \<leftarrow> binomial_tree_unref' least_tree;
                    modify
                     (t_hrs_'_update
                       (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                                    (64 word
                                     \<Rightarrow> bool \<times>
             (nat
              \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                              unit) typ_desc \<times>
                             bool) option)).
                           hrs_mem_update
                            (heap_update heap
                              (num_values_C_update
                                (\<lambda>a::32 word. a - 1)
                                (h_val (hrs_mem a) heap)))
                            a));
                    return result
                  })
                 (do {
                    guard
                     (\<lambda>s::globals.
                         c_guard
                          (roots_C
                            (h_val (hrs_mem (t_hrs_' s)) heap) +\<^sub>p
                           uint least_index));
                    modify
                     (t_hrs_'_update
                       (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                                    (64 word
                                     \<Rightarrow> bool \<times>
             (nat
              \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                              unit) typ_desc \<times>
                             bool) option)).
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

definition binomial_heap_free' :: "StrictC'_BinomialHeap_C ptr \<Rightarrow> (unit, unit, globals) spec_monad" where
  "binomial_heap_free' \<equiv> \<lambda>heap::StrictC'_BinomialHeap_C ptr.
   do {
     guard (\<lambda>s::globals. c_guard heap);
     i::32 word \<leftarrow>
       whileLoop (\<lambda>(i::32 word) s::globals.
                     i < roots_length_C (h_val (hrs_mem (t_hrs_' s)) heap))
         (\<lambda>i::32 word.
             do {
               guard
                (\<lambda>s::globals.
                    c_guard
                     (roots_C (h_val (hrs_mem (t_hrs_' s)) heap) +\<^sub>p
                      uint i));
               tree::StrictC'_BinomialTree_C ptr \<leftarrow>
                 gets
                  (\<lambda>s::globals.
                      h_val (hrs_mem (t_hrs_' s))
                       (roots_C (h_val (hrs_mem (t_hrs_' s)) heap) +\<^sub>p
                        uint i));
               ret::unit \<leftarrow> binomial_tree_unref' tree;
               return (i + 1)
             })
        0;
     ptr::unit ptr \<leftarrow>
       gets
        (\<lambda>s::globals.
            PTR_COERCE(StrictC'_BinomialTree_C ptr \<rightarrow> unit)
             (roots_C (h_val (hrs_mem (t_hrs_' s)) heap)));
     ret::unit \<leftarrow> free' ptr;
     free' (PTR_COERCE(StrictC'_BinomialHeap_C \<rightarrow> unit) heap)
   }"

definition calloc' :: "64 word
\<Rightarrow> 64 word \<Rightarrow> (unit, unit ptr, globals) spec_monad" where
  "calloc' \<equiv> \<lambda>(count::64 word) size::64 word.
   do {
     guard
      (\<lambda>s::globals.
          binomial_heap_input.calloc.count_' < unat count);
     x::unit \<leftarrow>
       guard
        (\<lambda>s::globals.
            binomial_heap_input.calloc.count_' < unat size);
     x::unit \<leftarrow>
       guard
        (\<lambda>s::globals.
            unat count * unat size
            < binomial_heap_input.calloc.ret'_' ^ (64::nat));
     guard (\<lambda>s::globals. allocator_wf (phantom_machine_state_' s));
     assume_result_and_state
      (\<lambda>s::globals.
          {(p::unit ptr, t::globals).
           \<exists>(h::64 word \<Rightarrow> 8 word) A::calg_allocator.
              calloc_rel (unat count) (unat size) (hrs_mem (t_hrs_' s))
               (phantom_machine_state_' s) (ptr_val p) h A \<and>
              t =
              t_hrs_'_update (hrs_mem_update (\<lambda>_. h)) s
              \<lparr>phantom_machine_state_' := A\<rparr>})
   }"

definition binomial_heap_new' :: "32 signed word
\<Rightarrow> unit ptr
              \<Rightarrow> (unit, StrictC'_BinomialHeap_C ptr,
                             globals) spec_monad" where
  "binomial_heap_new' \<equiv> \<lambda>(heap_type::32 signed word) compare_func::unit ptr.
   do {
     ret'ptr_to_void'1::unit ptr \<leftarrow>
       calloc' 1 (word_of_nat (size_of TYPE(StrictC'_BinomialHeap_C)));
     new_heap::StrictC'_BinomialHeap_C ptr \<leftarrow>
       return
        (PTR_COERCE(unit \<rightarrow> StrictC'_BinomialHeap_C)
          ret'ptr_to_void'1);
     condition (\<lambda>s::globals. new_heap = NULL)
       (return NULL)
       (do {
          guard (\<lambda>s::globals. c_guard new_heap);
          modify
           (t_hrs_'_update
             (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                          (64 word
                           \<Rightarrow> bool \<times>
   (nat
    \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                    unit) typ_desc \<times>
                   bool) option)).
                 hrs_mem_update
                  (heap_update new_heap
                    (heap_type_C_update
                      (\<lambda>a::32 signed word. heap_type)
                      (h_val (hrs_mem a) new_heap)))
                  a));
          modify
           (t_hrs_'_update
             (\<lambda>a::(64 word \<Rightarrow> 8 word) \<times>
                          (64 word
                           \<Rightarrow> bool \<times>
   (nat
    \<Rightarrow> ((8 word list \<Rightarrow> 8 word list,
                    unit) typ_desc \<times>
                   bool) option)).
                 hrs_mem_update
                  (heap_update new_heap
                    (compare_func_C_update
                      (\<lambda>a::unit ptr. compare_func)
                      (h_val (hrs_mem a) new_heap)))
                  a));
          return new_heap
        })
   }"

definition calg_binomial_new' :: "(unit, StrictC'_BinomialHeap_C ptr, globals) spec_monad" where
  "calg_binomial_new' \<equiv> binomial_heap_new' BINOMIAL_HEAP_TYPE_MIN binomial_heap_input.int_compare"

fixed_point (spec_monad_gfp) unref_replay ::
  "StrictC'_BinomialTree_C ptr \<Rightarrow> (unit, unit, globals) spec_monad"
where
  "unref_replay = (\<lambda>tree::StrictC'_BinomialTree_C ptr.
   unless (tree = NULL)
    (do {
       x::unit \<leftarrow> guard (\<lambda>s::globals. c_guard tree);
       x::unit \<leftarrow>
         guard
          (\<lambda>s::globals.
              - (INT_MAX + 1)
              < uint (refcount_C (h_val (hrs_mem (t_hrs_' s)) tree)));
       guard
        (\<lambda>s::globals.
            uint (refcount_C (h_val (hrs_mem (t_hrs_' s)) tree))
            \<le> INT_MAX + 1);
       modify
        (\<lambda>s::globals.
            t_hrs_'_update
             (hrs_mem_update
               (heap_update tree
                 (refcount_C_update (\<lambda>a::16 word. a - 1)
                   (h_val (hrs_mem (t_hrs_' s)) tree))))
             s);
       condition (\<lambda>s::globals.
                     UCAST(16 \<rightarrow> 32 signed)
                      (refcount_C (h_val (hrs_mem (t_hrs_' s)) tree)) =
                     0)
         (do {
            i::32 signed word \<leftarrow>
              whileLoop (\<lambda>(i::32 signed word) s::globals.
                            i <s UCAST(16 \<rightarrow> 32 signed)
                                  (order_C
                                    (h_val (hrs_mem (t_hrs_' s)) tree)))
                (\<lambda>i::32 signed word.
                    do {
                      guard
                       (\<lambda>s::globals.
                           c_guard
                            (subtrees_C
                              (h_val (hrs_mem (t_hrs_' s)) tree) +\<^sub>p
                             sint i));
                      tree::StrictC'_BinomialTree_C ptr \<leftarrow>
                        gets
                         (\<lambda>s::globals.
                             h_val (hrs_mem (t_hrs_' s))
                              (subtrees_C
                                (h_val (hrs_mem (t_hrs_' s)) tree) +\<^sub>p
                               sint i));
                      ret::unit \<leftarrow>
                        (unref_replay::StrictC'_BinomialTree_C ptr
 \<Rightarrow> (unit, unit, globals) spec_monad)
                         tree;
                      x::unit \<leftarrow>
                        guard
                         (\<lambda>_. 0 \<le> (2147483649::int) + sint i);
                      guard (\<lambda>_. sint i \<le> INT_MAX - 1);
                      return (i + 1)
                    })
               0;
            ptr::unit ptr \<leftarrow>
              gets
               (\<lambda>s::globals.
                   PTR_COERCE
                    (StrictC'_BinomialTree_C ptr \<rightarrow> unit)
                    (subtrees_C (h_val (hrs_mem (t_hrs_' s)) tree)));
            ret::unit \<leftarrow> free' ptr;
            free'
             (PTR_COERCE(StrictC'_BinomialTree_C \<rightarrow> unit) tree)
          })
         skip
     }))"
  by simp_all

lemma unref_replay_eq: "unref_replay = binomial_tree_unref'"
  by (simp only: gfp_lub_fun gfp_le_fun
    unref_replay_def fixp_unref_replay_def unref_replay_functional_def
    binomial_tree_unref'_def fixp_binomial_tree_unref'_def binomial_tree_unref'_functional_def)

lemmas bn_unref_equation = unref_replay.simps[unfolded unref_replay_eq]
setup \<open>Global_Theory.alias_fact
  (Binding.qualify true "binomial_tree_unref'" (Binding.name "simps"))
  "Binomial_Heap_C.bn_unref_equation"\<close>

end
