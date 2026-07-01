(* lemma: invs_asid_update_strg'
   thy: proof/refine/ARM/Finalise_R.thy:2332 (proof hot line 2339)
   session: Refine
   arm: search  source: db-scan  tier: sweet  tactic: auto
   REASON: db-scan: lemma total 10s, search 10s (frac 1.00), single classical line(s); hot = auto carrying 10s.
*)

lemma invs_asid_update_strg':
  "invs' s \<and> tab = armKSASIDTable (ksArchState s) \<longrightarrow>
   invs' (s\<lparr>ksArchState := armKSASIDTable_update
            (\<lambda>_. tab (asid := None)) (ksArchState s)\<rparr>)"
  apply (simp add: invs'_def)
  apply (simp add: valid_state'_def)
  apply (simp add: valid_global_refs'_def global_refs'_def valid_arch_state'_def valid_asid_table'_def valid_machine_state'_def ct_idle_or_in_cur_domain'_def tcb_in_cur_domain'_def)
  apply (auto simp add: ran_def split: if_split_asm)
  done
