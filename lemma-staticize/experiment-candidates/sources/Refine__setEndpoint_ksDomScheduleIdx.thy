(* lemma: setEndpoint_ksDomScheduleIdx
   thy: proof/refine/ARM/IpcCancel_R.thy:787 (proof hot line 870)
   session: Refine
   arm: search  source: db-scan  tier: sweet  tactic: auto
   REASON: db-scan: lemma total 13s, search 13s (frac 1.00), single classical line(s); hot = auto carrying 13s.
*)

lemma setEndpoint_ksDomScheduleIdx[wp]:
  "setEndpoint ptr ep \<lbrace>\<lambda>s. P (ksDomScheduleIdx s)\<rbrace>"
  apply (simp add: setEndpoint_def setObject_def split_def)
  apply (wp updateObject_default_inv | simp)+
  done
