theory Environment_Sanity
  imports Binary_Heap_Model
begin

text \<open>These are preparation witnesses and are excluded from scored targets.\<close>

definition empty_allocator where
  "empty_allocator = \<lparr>live_blocks = (\<lambda>_. None), reserved_bytes = {}\<rparr>"
definition one_allocator where
  "one_allocator = empty_allocator\<lparr>live_blocks := (\<lambda>_. None)(16 := Some 16)\<rparr>"

lemma empty_allocator_wf: "allocator_wf empty_allocator"
  by (simp add: allocator_wf_def empty_allocator_def)

lemma fresh_block_witness: "fresh_block empty_allocator 16 16"
  by (simp add: fresh_block_def block_ok_def empty_allocator_def)

lemma malloc_success_witness:
  "malloc_rel 16 h empty_allocator 16 h one_allocator"
  using malloc_success_available[OF empty_allocator_wf fresh_block_witness, of h]
  by (simp add: one_allocator_def empty_allocator_def)

lemma one_allocator_wf: "allocator_wf one_allocator"
  by (auto simp: allocator_wf_def one_allocator_def empty_allocator_def block_ok_def)

lemma realloc_in_place_witness:
  "realloc_rel 16 32 h one_allocator 16 h
    (empty_allocator\<lparr>live_blocks := (\<lambda>_. None)(16 := Some 32)\<rparr>)"
  by (auto simp: realloc_rel_def allocator_wf_def fresh_block_def block_ok_def
      one_allocator_def empty_allocator_def bytes_frame_def fun_eq_iff)

lemma realloc_move_witness:
  "realloc_rel 16 32 (\<lambda>_. 0) one_allocator 64 (\<lambda>_. 0)
    (empty_allocator\<lparr>live_blocks := (\<lambda>_. None)(64 := Some 32)\<rparr>)"
  by (auto simp: realloc_rel_def allocator_wf_def fresh_block_def block_ok_def
      one_allocator_def empty_allocator_def bytes_frame_def block_bytes_def fun_eq_iff)

lemma free_success_witness:
  "free_rel 16 h one_allocator h empty_allocator"
  by (auto simp: free_rel_def allocator_wf_def block_ok_def one_allocator_def empty_allocator_def
      bytes_frame_def fun_eq_iff)

lemma invalid_free_rejected:
  "\<not> free_rel 16 h empty_allocator h' A'"
  by (simp add: free_rel_def empty_allocator_def)

lemma malloc_failure_execution:
  "allocator_wf (phantom_machine_state_' s) \<Longrightarrow>
    reaches (malloc' 16) s (Result NULL) s"
  by (cases s; auto simp: malloc'_def[unfold_locals binary_heap_input.malloc] reaches_bind succeeds_bind malloc_rel_def
      hrs_mem_update_def hrs_mem_def split: prod.splits)

lemma malloc_success_execution:
  "phantom_machine_state_' s = empty_allocator \<Longrightarrow>
    reaches (malloc' 16) s (Result (Ptr 16)) (s\<lparr>phantom_machine_state_' := one_allocator\<rparr>)"
  by (cases s; auto simp: malloc'_def[unfold_locals binary_heap_input.malloc] reaches_bind succeeds_bind malloc_rel_def
      fresh_block_def block_ok_def allocator_wf_def one_allocator_def empty_allocator_def
      bytes_frame_def hrs_mem_update_def hrs_mem_def split: prod.splits)

definition witness_heap_record :: StrictC'_BinaryHeap_C where
  "witness_heap_record = heap_type_C_update (\<lambda>_. BINARY_HEAP_TYPE_MIN)
    (values_C_update (\<lambda>_. Ptr 64) (num_values_C_update (\<lambda>_. 0)
      (alloced_size_C_update (\<lambda>_. 16)
        (compare_func_C_update (\<lambda>_. binary_heap_input.int_compare) undefined))))"
definition witness_allocator where
  "witness_allocator = empty_allocator\<lparr>live_blocks :=
    ((\<lambda>_. None)(16 := Some (size_of TYPE(StrictC'_BinaryHeap_C))))(64 := Some 128),
    reserved_bytes := block_bytes 256 4\<rparr>"
definition witness_state :: globals where
  "witness_state = undefined\<lparr>
    t_hrs_' := (heap_update (Ptr 16) witness_heap_record (\<lambda>_. 0), undefined),
    phantom_machine_state_' := witness_allocator\<rparr>"

lemma representation_witness:
  "bh_rep witness_state (Ptr 16) {#}"
  unfolding bh_rep_def bh_values_def bh_contents_def bh_count_def bh_capacity_def
    bh_buffer_def bh_record_def witness_state_def witness_heap_record_def
    witness_allocator_def empty_allocator_def allocator_wf_def block_ok_def
  by (auto simp: hrs_mem_def c_guard_def ptr_aligned_def c_null_guard_def block_bytes_def intvl_no_overflow)

lemma insertion_precondition_witness:
  "bh_rep witness_state (Ptr 16) {#} \<and>
    payload_valid witness_state (PTR(unit) 256) \<and>
    insert_admissible witness_state (Ptr 16)"
  using representation_witness
  by (auto simp: payload_valid_def insert_admissible_def bh_count_def bh_capacity_def
      bh_record_def witness_state_def witness_allocator_def witness_heap_record_def
      hrs_mem_def c_guard_def ptr_aligned_def c_null_guard_def intvl_no_overflow)

end
