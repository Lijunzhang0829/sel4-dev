theory Binary_Heap_Targets
  imports Binary_Heap_Model
begin

text \<open>Draft targets: oops checks the declaration and exports no theorem.
No functional target proofs are supplied during preparation.\<close>

context binary_heap_input_global_addresses
begin

lemma bh_count_exact:
  "bh_rep s h M \<Longrightarrow> calg_contract (binary_heap_num_entries' h) s
    (\<lambda>r t. unat r = size M \<and> t = s)"
  oops

lemma bh_insert_correct:
  "bh_rep s h M \<Longrightarrow> payload_valid s p \<Longrightarrow> insert_admissible s h \<Longrightarrow>
   calg_contract (binary_heap_insert' h p) s (\<lambda>r t.
     payload_frame s t \<and> other_blocks_unchanged s h t \<and>
     (if r = 0 then t = s else r = 1 \<and> bh_rep t h (add_mset p M) \<and> insert_storage s h t))"
  oops

lemma bh_insert_without_growth:
  "bh_rep s h M \<Longrightarrow> payload_valid s p \<Longrightarrow> bh_count s h < bh_capacity s h \<Longrightarrow>
   calg_contract (binary_heap_insert' h p) s (\<lambda>r t.
     r = 1 \<and> bh_rep t h (add_mset p M) \<and>
     phantom_machine_state_' t = phantom_machine_state_' s \<and>
     bh_buffer t h = bh_buffer s h \<and> bh_capacity t h = bh_capacity s h \<and>
     bytes_frame (bh_owned s h) (hrs_mem (t_hrs_' s)) (hrs_mem (t_hrs_' t)))"
  oops

lemma bh_pop_correct:
  "bh_rep s h M \<Longrightarrow>
   calg_contract (binary_heap_pop' h) s (\<lambda>p t.
     phantom_machine_state_' t = phantom_machine_state_' s \<and>
     bytes_frame (bh_owned s h) (hrs_mem (t_hrs_' s)) (hrs_mem (t_hrs_' t)) \<and>
     bh_buffer t h = bh_buffer s h \<and> bh_capacity t h = bh_capacity s h \<and>
     (if M = {#} then p = NULL \<and> t = s else
       p \<in># M \<and> (\<forall>q \<in># M. payload_key s p \<le> payload_key s q) \<and>
       bh_rep t h (M - {#p#})))"
  oops

lemma bh_free_owned_storage:
  "bh_rep s h M \<Longrightarrow> calg_contract (binary_heap_free' h) s (\<lambda>r t.
     r = () \<and> payload_frame s t \<and>
     bytes_frame (bh_owned s h) (hrs_mem (t_hrs_' s)) (hrs_mem (t_hrs_' t)) \<and>
     phantom_machine_state_' t = (phantom_machine_state_' s)\<lparr>live_blocks :=
       ((live_blocks (phantom_machine_state_' s))(ptr_val (bh_buffer s h) := None))
         (ptr_val h := None)\<rparr>)"
  oops

lemma bh_new_empty_or_failure:
  "allocator_wf (phantom_machine_state_' s) \<Longrightarrow>
   calg_contract calg_binary_new' s (\<lambda>h t.
     payload_frame s t \<and> existing_blocks_preserved s t \<and>
     (if h = NULL then phantom_machine_state_' t = phantom_machine_state_' s else
       bh_rep t h {#} \<and> bh_capacity t h = 16 \<and> new_storage s h t))"
  oops

end
end
