(* lemma: gets_apply_ready_queues_reads_respects
   thy: proof/infoflow/Finalise_IF.thy:520 (proof hot line 523)
   session: InfoFlow
   arm: search  source: db-scan  tier: sweet  tactic: force
   REASON: db-scan: lemma total 12s, search 12s (frac 1.00), single classical line(s); hot = force carrying 12s.
*)

lemma gets_apply_ready_queues_reads_respects:
  "reads_respects aag l (\<lambda>_. pasSubject aag \<in> pasDomainAbs aag d) (gets_apply ready_queues d)"
  apply (rule gets_apply_ev')
  apply (force elim: reads_equivE simp: equiv_for_def)
  done

(* FIXME: move *)
