theory Binary_Heap_Model
  imports Calg_Binary_Foundation.Binary_Heap_C "HOL-Library.Multiset"
begin

text \<open>The representation reads the actual translated byte heap. The list below
is an observation of C storage, not a replacement implementation of the heap.
Payloads are externally owned, non-null signed C integers; duplicate pointers
and distinct pointers with equal keys are both permitted.\<close>

definition bh_record where
  "bh_record s h = (h_val (hrs_mem (t_hrs_' s)) h :: StrictC'_BinaryHeap_C)"
definition bh_count where "bh_count s h = unat (num_values_C (bh_record s h))"
definition bh_capacity where "bh_capacity s h = unat (alloced_size_C (bh_record s h))"
definition bh_buffer where "bh_buffer s h = values_C (bh_record s h)"
definition bh_values where
  "bh_values s h = map (\<lambda>i. h_val (hrs_mem (t_hrs_' s))
    (bh_buffer s h +\<^sub>p int i)) [0..<bh_count s h]"
definition bh_contents where "bh_contents s h = mset (bh_values s h)"
definition payload_key where
  "payload_key s p = sint (h_val (hrs_mem (t_hrs_' s)) (ptr_coerce p :: 32 signed word ptr))"
definition payload_valid where
  "payload_valid s p \<longleftrightarrow> c_guard (ptr_coerce p :: 32 signed word ptr) \<and>
     block_bytes (ptr_val p) 4 \<subseteq> reserved_bytes (phantom_machine_state_' s)"

definition bh_rep where
  "bh_rep s h M \<longleftrightarrow>
    allocator_wf (phantom_machine_state_' s) \<and> c_guard h \<and>
    c_guard (bh_buffer s h) \<and> ptr_val h \<noteq> ptr_val (bh_buffer s h) \<and>
    live_blocks (phantom_machine_state_' s) (ptr_val h) = Some (size_of TYPE(StrictC'_BinaryHeap_C)) \<and>
    live_blocks (phantom_machine_state_' s) (ptr_val (bh_buffer s h)) =
      Some (size_of TYPE(unit ptr) * bh_capacity s h) \<and>
    0 < bh_capacity s h \<and> bh_capacity s h < 2 ^ 31 \<and>
    bh_count s h \<le> bh_capacity s h \<and>
    heap_type_C (bh_record s h) = BINARY_HEAP_TYPE_MIN \<and>
    compare_func_C (bh_record s h) = binary_heap_input.int_compare \<and>
    (\<forall>p \<in> set (bh_values s h). payload_valid s p) \<and>
    (\<forall>i. 0 < i \<and> i < bh_count s h \<longrightarrow>
       payload_key s (bh_values s h ! ((i - 1) div 2)) \<le>
       payload_key s (bh_values s h ! i)) \<and>
    M = bh_contents s h"

definition insert_admissible where
  "insert_admissible s h \<longleftrightarrow>
    (bh_count s h < bh_capacity s h \<or> 2 * bh_capacity s h < 2 ^ 31)"

definition payload_frame where
  "payload_frame s t \<longleftrightarrow>
    reserved_bytes (phantom_machine_state_' t) = reserved_bytes (phantom_machine_state_' s) \<and>
    (\<forall>a \<in> reserved_bytes (phantom_machine_state_' s).
       hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a)"

definition bh_owned where
  "bh_owned s h = block_bytes (ptr_val h) (size_of TYPE(StrictC'_BinaryHeap_C)) \<union>
    block_bytes (ptr_val (bh_buffer s h)) (size_of TYPE(unit ptr) * bh_capacity s h)"

definition other_blocks_unchanged where
  "other_blocks_unchanged s h t \<longleftrightarrow>
    (\<forall>p. p \<noteq> ptr_val h \<and> p \<noteq> ptr_val (bh_buffer s h) \<longrightarrow>
      (case live_blocks (phantom_machine_state_' s) p of None \<Rightarrow> True
       | Some n \<Rightarrow> live_blocks (phantom_machine_state_' t) p = Some n \<and>
         (\<forall>a \<in> block_bytes p n. hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a)))"

definition insert_storage where
  "insert_storage s h t \<longleftrightarrow>
    phantom_machine_state_' t = (phantom_machine_state_' s)\<lparr>live_blocks :=
      ((live_blocks (phantom_machine_state_' s))(ptr_val (bh_buffer s h) := None))
        (ptr_val (bh_buffer t h) := Some (size_of TYPE(unit ptr) * bh_capacity t h))\<rparr>"

definition existing_blocks_preserved where
  "existing_blocks_preserved s t \<longleftrightarrow>
    (\<forall>p n. live_blocks (phantom_machine_state_' s) p = Some n \<longrightarrow>
      live_blocks (phantom_machine_state_' t) p = Some n \<and>
      (\<forall>a \<in> block_bytes p n. hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a))"

definition new_storage where
  "new_storage s h t \<longleftrightarrow>
    phantom_machine_state_' t = (phantom_machine_state_' s)\<lparr>live_blocks :=
      ((live_blocks (phantom_machine_state_' s))(ptr_val h := Some (size_of TYPE(StrictC'_BinaryHeap_C))))
        (ptr_val (bh_buffer t h) := Some (size_of TYPE(unit ptr) * 16))\<rparr>"

text \<open>Nonfailure and at least one normal outcome are both required, so an empty
assumption relation cannot satisfy a target vacuously. These are semantic
obligations; merely elaborating this definition does not establish them.\<close>
definition calg_contract ::
  "('e::default, 'a, 's) spec_monad \<Rightarrow> 's \<Rightarrow> ('a \<Rightarrow> 's \<Rightarrow> bool) \<Rightarrow> bool" where
  "calg_contract f s Q \<longleftrightarrow>
    runs_to f s (\<lambda>r t. \<exists>v. r = Result v \<and> Q v t) \<and>
    (\<exists>v t. reaches f s (Result v) t)"

end
