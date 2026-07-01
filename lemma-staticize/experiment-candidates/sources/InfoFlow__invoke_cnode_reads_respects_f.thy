(* lemma: invoke_cnode_reads_respects_f
   thy: proof/infoflow/Syscall_IF.thy:230 (proof hot line 248)
   session: InfoFlow
   arm: search  source: db-scan  tier: sweet  tactic: auto
   REASON: db-scan: lemma total 15s, search 15s (frac 1.00), single classical line(s); hot = auto carrying 15s.
*)

lemma invoke_cnode_reads_respects_f:
  assumes domains_distinct[wp]: "pas_domains_distinct aag"
  shows "reads_respects_f aag l
           (silc_inv aag st and only_timer_irq_inv irq st' and pas_refined aag and einvs
                            and simple_sched_action and valid_cnode_inv ci
                            and (\<lambda>s. is_subject aag (cur_thread s))
                            and cnode_inv_auth_derivations ci and authorised_cnode_inv aag ci)
           (invoke_cnode ci)"
  unfolding invoke_cnode_def
  apply (rule equiv_valid_guard_imp)
  apply (wpc | wp reads_respects_f[OF cap_insert_reads_respects] cap_insert_silc_inv
                  reads_respects_f[OF cap_move_reads_respects] cap_move_silc_inv get_cap_auth_wp
                  cap_revoke_reads_respects_f cap_delete_reads_respects_f cap_swap_silc_inv
                  reads_respects_f[OF cap_swap_reads_respects] cap_move_cte_wp_at_other
                  reads_respects_f[OF get_cap_rev]  cancel_badged_sends_reads_respects_f
             | simp add: when_def split del: if_split
             | elim conjE, assumption)+
  apply (clarsimp simp: cnode_inv_auth_derivations_def authorised_cnode_inv_def)
  apply (auto intro: real_cte_emptyable_strg[rule_format]
               simp: silc_inv_def reads_equiv_f_def requiv_cur_thread_eq caps_of_state_cteD
                     aag_cap_auth_recycle_EndpointCap cte_wp_at_weak_derived_ReplyCap )
  done
