(* lemma: arm_asid_table_delete_ev2
   thy: proof/infoflow/ARM/ArchArch_IF.thy:861 (proof hot line 872)
   session: InfoFlow
   arm: search  source: db-scan  tier: sweet  tactic: auto
   REASON: db-scan: lemma total 19s, search 19s (frac 1.00), single classical line(s); hot = auto carrying 19s.
*)

lemma arm_asid_table_delete_ev2:
  "equiv_valid_2 (reads_equiv aag) (affects_equiv aag l) (affects_equiv aag l) \<top>\<top>
     (\<lambda>s. rv = arm_asid_table (arch_state s)) (\<lambda>s. rv' = arm_asid_table (arch_state s))
     (modify (\<lambda>s. s\<lparr>arch_state := arch_state s\<lparr>arm_asid_table := \<lambda>a. if a = asid_high_bits_of base
                                                                     then None
                                                                     else rv a\<rparr>\<rparr>))
     (modify (\<lambda>s. s\<lparr>arch_state := arch_state s\<lparr>arm_asid_table := \<lambda>a. if a = asid_high_bits_of base
                                                                     then None
                                                                     else rv' a\<rparr>\<rparr>))"
  apply (rule modify_ev2)
  (* slow 15s *)
  by (auto simp: reads_equiv_def2 affects_equiv_def2
         intro!: states_equiv_forI equiv_forI equiv_asids_arm_asid_table_delete
          elim!: states_equiv_forE equiv_forE
           elim: is_subject_kheap_eq[simplified reads_equiv_def2 states_equiv_for_def, rotated])
