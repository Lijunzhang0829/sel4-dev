(* lemma: cte_wp_at_weak_derived_domain_sep_inv_cap
   thy: proof/access-control/DomainSepInv.thy:226 (proof hot line 230)
   session: Access
   arm: search  source: db-scan  tier: sweet  tactic: force
   REASON: db-scan: lemma total 10s, search 10s (frac 1.00), single classical line(s); hot = force carrying 10s.
*)

lemma cte_wp_at_weak_derived_domain_sep_inv_cap:
  "\<lbrakk> domain_sep_inv irqs st s; cte_wp_at (weak_derived cap) slot s \<rbrakk>
     \<Longrightarrow> domain_sep_inv_cap irqs cap"
  apply (cases slot)
  apply (force simp: domain_sep_inv_def domain_sep_inv_cap_def
              split: cap.splits
               dest: cte_wp_at_norm weak_derived_irq_handler weak_derived_DomainCap)
  done
