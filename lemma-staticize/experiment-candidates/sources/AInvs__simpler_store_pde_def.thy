(* lemma: simpler_store_pde_def
   thy: proof/invariant-abstract/ARM/ArchVSpace_AI.thy:2759 (proof hot line 2766)
   session: AInvs
   arm: work-suspect  source: db-scan  tier: sweet  tactic: auto
   FLAGS: WORK-SUSPECT (cost may be simp-work, not search)
   REASON: db-scan: lemma total 34s, search 34s (frac 1.00), single classical line(s); hot = auto carrying 34s.
*)

lemma simpler_store_pde_def:
  "store_pde p pde s =
    (case kheap s (p && ~~ mask pd_bits) of
          Some (ArchObj (PageDirectory pd)) =>
            ({((), s\<lparr>kheap := (kheap s)(p && ~~ mask pd_bits \<mapsto>
                                        ArchObj (PageDirectory (pd(ucast (p && mask pd_bits >> 2) := pde))))\<rparr>)}, False)
        | _ => ({}, True))"
  by (auto simp: store_pde_def simpler_set_pd_def get_object_def simpler_gets_def assert_def
                 return_def fail_def set_object_def get_def put_def bind_def get_pd_def
           split: Structures_A.kernel_object.splits option.splits arch_kernel_obj.splits if_split_asm)
