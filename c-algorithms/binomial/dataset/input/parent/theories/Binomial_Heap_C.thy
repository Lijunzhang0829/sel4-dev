theory Binomial_Heap_C
  imports Binomial_Environment
begin

declare [[cpp_path = "", allow_underscore_idents = true]]
install_C_file "../c/binomial_heap_input.c" [machinety=calg_allocator]

init-autocorres [skip_heap_abs, skip_word_abs, ts_rules = nondet] "binomial_heap_input.c"

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


overloading calloc_body \<equiv> calloc_body
begin
definition
  "calloc_body \<equiv> exec_spec_monad global_exn_var'_' global_exn_var'_'_update globals
    (\<lambda>s. (clookup 0 (locals s), clookup 1 (locals s)))
    (\<lambda>(n::64 word, w::64 word). L2_guarded
      (\<lambda>s. 0 < unat n \<and> 0 < unat w \<and> unat n * unat w < 2 ^ 64 \<and>
        allocator_wf (phantom_machine_state_' s))
      (L2_assume (\<lambda>s. {(p::unit ptr, t).
      \<exists>h A. calloc_rel (unat n) (unat w) (hrs_mem (t_hrs_' s)) (phantom_machine_state_' s)
           (ptr_val p) h A \<and>
        t = phantom_machine_state_'_update (\<lambda>_. A)
          (t_hrs_'_update (hrs_mem_update (\<lambda>_. h)) s)})))
    (\<lambda>upd. locals_update (cupdate 2 upd))"
end
declare_prototype calloc_body_def done

overloading memcpy_body \<equiv> memcpy_body
begin
definition
  "memcpy_body \<equiv> exec_spec_monad global_exn_var'_' global_exn_var'_'_update globals
    (\<lambda>s. (clookup 0 (locals s), clookup 1 (locals s), clookup 2 (locals s)))
    (\<lambda>(dst::unit ptr, src::unit ptr, n::64 word). L2_guarded
      (\<lambda>s. allocator_wf (phantom_machine_state_' s) \<and>
        memcpy_ok (phantom_machine_state_' s) (ptr_val dst) (ptr_val src) (unat n))
      (L2_assume (\<lambda>s. {(p::unit ptr, t).
      \<exists>h. p = dst \<and> memcpy_rel (phantom_machine_state_' s) (ptr_val dst)
        (ptr_val src) (unat n) (hrs_mem (t_hrs_' s)) h \<and>
        t = t_hrs_'_update (hrs_mem_update (\<lambda>_. h)) s})))
    (\<lambda>upd. locals_update (cupdate 3 upd))"
end
declare_prototype memcpy_body_def done

autocorres "binomial_heap_input.c"

thm final_defs
ML \<open>writeln "CALG_BINOMIAL_TRANSLATION_PASS"\<close>
end
