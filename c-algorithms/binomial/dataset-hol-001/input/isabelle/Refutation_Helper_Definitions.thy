theory Refutation_Helper_Definitions
 imports Environment_Spec_Review
begin
context Calg_Binomial_Reviewed_Stack begin

definition env_insert_leaf where
  "env_insert_leaf = StrictC'_BinomialTree_C (Ptr 128) 0 1 NULL"

definition env_insert_A where
 "env_insert_A = \<lparr>live_blocks = (\<lambda>_. None)(16 := Some 40, 64 := Some 24, 96 := Some 8),
   reserved_bytes = block_bytes 128 4 \<union> audit_stack_addresses\<rparr>"

definition env_insert_heap where
 "env_insert_heap = StrictC'_BinomialHeap_C BINOMIAL_HEAP_TYPE_MIN binomial_heap_input.int_compare 1 (Ptr 96) 1"

definition env_insert_mem where
 "env_insert_mem = heap_update (Ptr 16) env_insert_heap
   (heap_update (Ptr 64) env_insert_leaf
    (heap_update (Ptr 96 :: StrictC'_BinomialTree_C ptr ptr) (Ptr 64) (\<lambda>_. 0)))"

definition env_insert_s where
 "env_insert_s = globals.make
   (env_insert_mem, stack_byte_typing)
   env_insert_A undefined"

definition env_pop_A where
 "env_pop_A = \<lparr>live_blocks = (\<lambda>_. None)(16 := Some 40, 64 := Some 24,
 96 := Some 24, 160 := Some 24, 192 := Some 16, 224 := Some 8),
 reserved_bytes = block_bytes 256 8 \<union> audit_stack_addresses\<rparr>"

definition env_pop_leaf where
 "env_pop_leaf v = StrictC'_BinomialTree_C (Ptr v) 0 1 NULL"

definition env_pop_parent where
 "env_pop_parent = StrictC'_BinomialTree_C (Ptr 256) 1 1 (Ptr 224)"

definition env_pop_heap where
 "env_pop_heap = StrictC'_BinomialHeap_C BINOMIAL_HEAP_TYPE_MIN binomial_heap_input.int_compare 3 (Ptr 192) 2"

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

definition env_pop_s where
 "env_pop_s = globals.make (env_pop_mem, stack_byte_typing) env_pop_A undefined"

definition env_pop_F where
 "env_pop_F = [Some (BN (Ptr 64) []), Some (BN (Ptr 96) [BN (Ptr 160) []])]"

end
end
