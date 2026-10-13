(* lemma: reads_respects_scheduler_invisible_no_domain_switch
   thy: proof/infoflow/Scheduler_IF.thy:1688 (proof hot line 1721)
   session: InfoFlow
   arm: search  source: db-scan  tier: mid  tactic: fastforce
   REASON: db-scan: lemma total 61s, search 61s (frac 1.00), single classical line(s); hot = fastforce carrying 61s.
*)

lemma reads_respects_scheduler_invisible_no_domain_switch:
  assumes domains_distinct[wp]: "pas_domains_distinct aag"
  shows
  "reads_respects_scheduler aag l
     (\<lambda>s. pas_refined aag s \<and> invs s \<and> valid_sched s \<and> guarded_pas_domain aag s
                            \<and> domain_time s \<noteq> 0 \<and> \<not> reads_scheduler_cur_domain aag l s)
     schedule"
  supply ethread_get_wp[wp del] if_split[split del]
  apply (rule reads_respects_scheduler_unobservable''[where P=Q and P'=Q and Q=Q for Q])
  apply (rule hoare_pre)
     apply (rule scheduler_equiv_lift'[where P="invs and (\<lambda>s. domain_time s \<noteq> 0)"])
          apply (wp schedule_no_domain_switch schedule_no_domain_fields
                    silc_dom_lift | simp)+
   apply (simp add: schedule_def)
   apply (wp guarded_switch_to_lift
             scheduler_equiv_lift
             schedule_choose_new_thread_schedule_affects_no_switch
             set_scheduler_action_unobservable
             tcb_sched_action_unobservable
             switch_to_thread_unobservable silc_dom_lift
             gts_wp
             hoare_vcg_all_lift
             hoare_vcg_disj_lift
          | wpc | simp
          | rule hoare_pre_cont[where f=next_domain]
          | wp (once) hoare_drop_imp[where f="set_scheduler_action choose_new_thread"])+
            (* stop on fastfail calculation *)
            apply (clarsimp simp: conj_ac cong: imp_cong conj_cong)
            apply (wp hoare_drop_imps)[1]
           apply (wp tcb_sched_action_unobservable gts_wp
                     schedule_choose_new_thread_schedule_affects_no_switch)+
   apply (clarsimp simp: if_apply_def2)
   (* slow 15s *)
   by (safe; (fastforce simp: switch_thread_runnable
              | fastforce dest!: switch_to_cur_domain cur_thread_cur_domain
              | fastforce simp: st_tcb_at_def obj_at_def))+
