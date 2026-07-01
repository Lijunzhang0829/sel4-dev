(* lemma: SAC_partsSubjectAffects_exceptT
   thy: proof/infoflow/PolicySystemSAC.thy:909 (proof hot line 921)
   session: InfoFlow
   arm: work-suspect  source: wasted-classical  tier: sweet  tactic: auto
   FLAGS: WORK-SUSPECT (cost may be simp-work, not search)
   REASON: wasted-classical: auto+simp: line at 22.2s, NO split/dest/elim/intro (classical likely wasted), file 962L (baseline-able). Band of the 2 confirmed fastforce->clarsimp wins.
*)

lemma SAC_partsSubjectAffects_exceptT : "x \<noteq> T \<Longrightarrow> partsSubjectAffects SACAuthGraph x = SACFlowDoms"
  apply (rule equalityI)
  defer
  apply (rule subsetI)
    apply (simp add:partsSubjectAffects_def image_def label_can_affect_partition_def)
    apply (case_tac x)
     apply ((erule disjE, clarify, simp add:SAC_affects SAC_reads, blast?)+, simp add:SAC_affects SAC_reads, blast?)+
  apply (rule subsetI)
    apply (simp add:partsSubjectAffects_def image_def label_can_affect_partition_def)
    apply (clarify)
    apply (case_tac x)
      apply (case_tac[!] xaa)
        apply (auto simp: SAC_affects SAC_reads)
done
