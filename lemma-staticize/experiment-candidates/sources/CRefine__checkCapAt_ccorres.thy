(* lemma: checkCapAt_ccorres
   thy: proof/crefine/ARM/Tcb_C.thy:422 (proof hot line 447)
   session: CRefine
   arm: hard  source: db-scan  tier: hard  tactic: auto
   REASON: db-scan: lemma total 569s, search 172s (frac 0.30), 93 classical line(s); hot = auto carrying 158s.
*)

lemma checkCapAt_ccorres:
  "\<lbrakk> \<And>rv' t t'. ceqv \<Gamma> ret__unsigned_long_' rv' t t' c (c' rv');
      ccorres rvr xf P P' hs (f >>= g) (c' (scast true));
      ccorres rvr xf Q Q' hs (g ()) (c' (scast false));
      guard_is_UNIV dc xfdc (\<lambda>_ _. P' \<inter> Q') \<rbrakk>
    \<Longrightarrow> ccorres rvr xf (invs' and valid_cap' cap and P and Q)
                       (UNIV \<inter> {s. ccap_relation cap cap'} \<inter> {s. slot' = cte_Ptr slot}) hs
         (checkCapAt cap slot f >>= g)
         (Guard C_Guard \<lbrace>hrs_htd \<acute>t_hrs \<Turnstile>\<^sub>t slot'\<rbrace>
            (\<acute>ret__unsigned_long :== CALL sameObjectAs(cap',
                h_val (hrs_mem \<acute>t_hrs) (cap_Ptr &(slot'\<rightarrow>[''cap_C'']))));;c)"
  apply (rule ccorres_gen_asm2)+
  apply (simp add: checkCapAt_def liftM_def bind_assoc del: Collect_const)
  apply (rule ccorres_symb_exec_l' [OF _ getCTE_inv getCTE_sp empty_fail_getCTE])
  apply (rule ccorres_guard_imp2)
   apply (rule ccorres_move_c_guard_cte)
   apply (rule_tac xf'=ret__unsigned_long_' and val="from_bool (sameObjectAs cap (cteCap x))"
                and R="cte_wp_at' ((=) x) slot and valid_cap' cap and invs'"
                 in ccorres_symb_exec_r_known_rv_UNIV[where R'=UNIV])
      apply vcg
      apply (clarsimp simp: cte_wp_at_ctes_of)
      apply (erule(1) cmap_relationE1[OF cmap_relation_cte])
      apply (rule exI, rule conjI, assumption)
      apply (clarsimp simp: typ_heap_simps dest!: ccte_relation_ccap_relation)
      apply (rule exI, rule conjI, assumption)
      apply (auto intro: valid_capAligned dest: ctes_of_valid')[1]
     apply assumption
    apply (simp only: when_def if_to_top_of_bind)
    apply (rule ccorres_if_lhs)
     apply simp
    apply simp
   apply (simp add: guard_is_UNIV_def)
  apply (clarsimp simp: cte_wp_at_ctes_of)
  done
