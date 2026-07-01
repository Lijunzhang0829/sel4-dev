(* lemma: abd_affects
   thy: proof/infoflow/PolicySystemSAC.thy:234 (proof hot line 241)
   session: InfoFlow
   arm: search  source: db-scan  tier: sweet  tactic: auto
   REASON: db-scan: lemma total 13s, search 13s (frac 1.00), single classical line(s); hot = auto carrying 13s.
*)

lemma abd_affects : "x \<in> {NicA, NicB, NicD} \<Longrightarrow> subjectAffects SACAuthGraph (partition_label x) = partition_label ` abd_affects_set"
   apply (rule subset_antisym)
   defer
   apply (rule abd_affects_bw)
   apply (simp)
   apply (rule subsetI)
     apply (erule subjectAffects.induct)
     by auto
