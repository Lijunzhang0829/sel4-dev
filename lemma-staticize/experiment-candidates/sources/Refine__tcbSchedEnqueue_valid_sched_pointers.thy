(* lemma: tcbSchedEnqueue_valid_sched_pointers
   thy: proof/refine/ARM/TcbAcc_R.thy:5371 (proof hot line 5393)
   session: Refine
   arm: search  source: db-scan  tier: sweet  tactic: force
   REASON: db-scan: lemma total 16s, search 16s (frac 1.00), single classical line(s); hot = force carrying 16s.
*)

lemma tcbSchedEnqueue_valid_sched_pointers[wp]:
  "tcbSchedEnqueue tcbPtr \<lbrace>valid_sched_pointers\<rbrace>"
  apply (clarsimp simp: tcbSchedEnqueue_def getQueue_def unless_def)
  \<comment> \<open>we step forwards until we can step over the addToBitmap in order to avoid state blow-up\<close>
  apply (intro bind_wp[OF _ stateAssert_sp] bind_wp[OF _ isRunnable_inv]
               bind_wp[OF _ assert_sp] bind_wp[OF _ threadGet_sp]
               bind_wp[OF _ gets_sp]
         | rule hoare_when_cases, fastforce)+
  apply (forward_inv_step wp: hoare_vcg_ex_lift)
  supply if_split[split del]
  apply (wpsimp wp: getTCB_wp
              simp: threadSet_def setObject_def updateObject_default_def tcbQueuePrepend_def
                    setQueue_def)
  apply (clarsimp simp: valid_sched_pointers_def)
  apply (intro conjI impI)
   apply (fastforce simp: opt_pred_def opt_map_def split: if_splits)
  apply normalise_obj_at'
  apply (clarsimp simp: ready_queue_relation_def ksReadyQueues_asrt_def)
  apply (drule_tac x="tcbDomain tcb" in spec)
  apply (drule_tac x="tcbPriority tcb" in spec)
  apply (clarsimp simp: valid_sched_pointers_def list_queue_relation_def)
  apply (case_tac "ts = []", fastforce simp: tcbQueueEmpty_def)
  by (intro conjI impI;
      force dest!: hd_in_set heap_path_head
             simp: inQ_def opt_pred_def opt_map_def obj_at'_def projectKOs split: if_splits)
