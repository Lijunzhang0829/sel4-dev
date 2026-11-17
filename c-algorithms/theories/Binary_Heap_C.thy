theory Binary_Heap_C
  imports Heap_Environment
begin

declare [[cpp_path = "", allow_underscore_idents = true]]
install_C_file "../c/binary_heap_input.c" [machinety=calg_allocator]

init-autocorres [skip_heap_abs, skip_word_abs, ts_rules = nondet] "binary_heap_input.c"

overloading malloc_body \<equiv> malloc_body
begin
definition
  "malloc_body \<equiv> exec_spec_monad global_exn_var'_' global_exn_var'_'_update globals
    (\<lambda>s. clookup 0 (locals s))
    (\<lambda>(n::64 word). L2_guarded
      (\<lambda>s. 0 < unat n \<and> allocator_wf (phantom_machine_state_' s))
      (L2_assume (\<lambda>s. {(p::unit ptr, t).
      \<exists>h A. malloc_rel (unat n) (hrs_mem (t_hrs_' s)) (phantom_machine_state_' s)
           (ptr_val p) h A \<and>
        t = phantom_machine_state_'_update (\<lambda>_. A)
          (t_hrs_'_update (hrs_mem_update (\<lambda>_. h)) s)})))
    (\<lambda>upd. locals_update (cupdate 1 upd))"
end
declare_prototype malloc_body_def done

overloading realloc_body \<equiv> realloc_body
begin
definition
  "realloc_body \<equiv> exec_spec_monad global_exn_var'_' global_exn_var'_'_update globals
    (\<lambda>s. (clookup 0 (locals s), clookup 1 (locals s)))
    (\<lambda>(p::unit ptr, n::64 word). L2_guarded
      (\<lambda>s. 0 < unat n \<and> allocator_wf (phantom_machine_state_' s) \<and>
        (p = NULL \<or> live_blocks (phantom_machine_state_' s) (ptr_val p) \<noteq> None))
      (L2_assume (\<lambda>s. {(q::unit ptr, t).
      \<exists>h A. realloc_rel (ptr_val p) (unat n) (hrs_mem (t_hrs_' s))
          (phantom_machine_state_' s) (ptr_val q) h A \<and>
        t = phantom_machine_state_'_update (\<lambda>_. A)
          (t_hrs_'_update (hrs_mem_update (\<lambda>_. h)) s)})))
    (\<lambda>upd. locals_update (cupdate 2 upd))"
end
declare_prototype realloc_body_def done

overloading free_body \<equiv> free_body
begin
definition
  "free_body \<equiv> exec_spec_monad global_exn_var'_' global_exn_var'_'_update globals
    (\<lambda>s. clookup 0 (locals s))
    (\<lambda>(p::unit ptr). L2_guarded
      (\<lambda>s. allocator_wf (phantom_machine_state_' s) \<and>
        (p = NULL \<or> live_blocks (phantom_machine_state_' s) (ptr_val p) \<noteq> None))
      (L2_assume (\<lambda>s. {(r::unit, t).
      \<exists>h A. free_rel (ptr_val p) (hrs_mem (t_hrs_' s)) (phantom_machine_state_' s) h A \<and>
        t = phantom_machine_state_'_update (\<lambda>_. A)
          (t_hrs_'_update (hrs_mem_update (\<lambda>_. h)) s)})))
    (\<lambda>upd. (\<lambda>s. s))"
end
declare_prototype free_body_def done

autocorres "binary_heap_input.c"

thm final_defs
ML \<open>writeln "CALG_BINARY_TRANSLATION_PASS"\<close>
end
