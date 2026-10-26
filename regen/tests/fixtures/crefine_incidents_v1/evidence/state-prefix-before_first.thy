theory Vero_Thread_Core
imports Vero_Support
(* VERO START Vero_Thread_Core imports *)



(* VERO END Vero_Thread_Core imports *)
begin


ML \<open>writeln "VERO_THEORY_ENTER Vero_Thread_Core"\<close>

(* VERO START Vero_Thread_Core global_aux *)



(* VERO END Vero_Thread_Core global_aux *)

context kernel_m begin

(* VERO START c0003 prove_aux *)



(* VERO END c0003 prove_aux *)

vero_prove "c0003" c0003_prove:
  "ccorres ((=) \<circ> tcb_ptr_to_ctcb_ptr) thread_'
       \<top> UNIV hs getCurThread (\<acute>thread :== \<acute>ksCurThread)"

(* VERO START c0003 prove *)

apply (rule ccorres_from_vcg)
apply (intro allI)
apply (rule HoarePartial.Basic)
apply (clarsimp simp: getCurThread_def gets_def get_def bind_def return_def rf_sr_def
                      state_rel.cstate_relation_def)
apply (simp only: Let_def)
done

(* VERO END c0003 prove *)

(* VERO START c0003 disprove_aux *)



(* VERO END c0003 disprove_aux *)

vero_disprove "c0003" c0003_disprove:
  "ccorres ((=) \<circ> tcb_ptr_to_ctcb_ptr) thread_'
       \<top> UNIV hs getCurThread (\<acute>thread :== \<acute>ksCurThread)"

(* VERO START c0003 disprove *)

oops

(* VERO END c0003 disprove *)

(* VERO START c0013 prove_aux *)



(* VERO END c0013 prove_aux *)

vero_prove "c0013" c0013_prove [corres]:
  "ccorres dc xfdc (tcb_at' thread) {s. thread' s = tcb_ptr_to_ctcb_ptr thread \<and> unat (v' s) = v} hs
           (threadSet (tcbTimeSlice_update (\<lambda>_. v)) thread)
           (Basic (\<lambda>s. globals_update (t_hrs_'_update (hrs_mem_update (heap_update (Ptr &(thread' s\<rightarrow>[''tcbTimeSlice_C''])::word32 ptr) (v' s)))) s))"

(* VERO START c0013 prove *)

oops

(* VERO END c0013 prove *)

(* VERO START c0013 disprove_aux *)



(* VERO END c0013 disprove_aux *)

vero_disprove "c0013" c0013_disprove [corres]:
  "ccorres dc xfdc (tcb_at' thread) {s. thread' s = tcb_ptr_to_ctcb_ptr thread \<and> unat (v' s) = v} hs
           (threadSet (tcbTimeSlice_update (\<lambda>_. v)) thread)
           (Basic (\<lambda>s. globals_update (t_hrs_'_update (hrs_mem_update (heap_update (Ptr &(thread' s\<rightarrow>[''tcbTimeSlice_C''])::word32 ptr) (v' s)))) s))"

(* VERO START c0013 disprove *)

oops

(* VERO END c0013 disprove *)

(* VERO START c0014 prove_aux *)



(* VERO END c0014 prove_aux *)

vero_prove "c0014" c0014_prove [corres]:
  "ccorres (=) handlerCPtr_' (tcb_at' tptr) UNIV hs
           (threadGet tcbFaultHandler tptr)
           (Guard C_Guard \<lbrace>hrs_htd \<acute>t_hrs \<Turnstile>\<^sub>t (tcb_ptr_to_ctcb_ptr tptr)\<rbrace>
               (\<acute>handlerCPtr :==
                  h_val (hrs_mem \<acute>t_hrs)
                   (Ptr &(tcb_ptr_to_ctcb_ptr tptr\<rightarrow>[''tcbFaultHandler_C''])::word32 ptr)))"

(* VERO START c0014 prove *)

apply (rule ccorres_from_vcg)
apply (rule allI)
subgoal for s0
  apply (rule HoarePartial.Guard[where R="{s. tcb_at' tptr s0 \<and> (s0, s) \<in> rf_sr}"])
   apply (rule subsetI)
   subgoal premises pre for x
   proof -
     have htcb: "tcb_at' tptr s0" using pre by simp
     have hrf: "(s0, x) \<in> rf_sr" using pre by simp
     have hcps:
       "cpspace_relation (ksPSpace s0) (underlying_memory (ksMachineState s0))
          (t_hrs_' (globals x))"
       using hrf by (clarsimp simp: rf_sr_def state_rel.cstate_relation_def Let_def)
     have htcbrel:
       "cmap_relation (map_to_tcbs (ksPSpace s0)) (clift (t_hrs_' (globals x)))
          tcb_ptr_to_ctcb_ptr ctcb_relation"
       using hcps by (clarsimp simp: cpspace_relation_def)
     obtain ko obj where hk: "ksPSpace s0 tptr = Some ko"
       and hp: "tcb_of' ko = Some obj"
       using htcb by (clarsimp simp: obj_at'_def projectKO_eq)
     have ko: "ko = KOTCB obj" using hp by (rule projectKO_opt_tcbD)
     have hm: "map_to_tcbs (ksPSpace s0) tptr = Some obj"
       using hk ko by (simp add: comp_def)
     have hd:
       "tcb_ptr_to_ctcb_ptr ` dom (map_to_tcbs (ksPSpace s0)) =
          dom (clift (t_hrs_' (globals x)))"
       using htcbrel by (clarsimp simp: cmap_relation_def)
     have hin: "tcb_ptr_to_ctcb_ptr tptr \<in> dom (clift (t_hrs_' (globals x)))"
       using hm hd by (auto simp: dom_def)
     show "x \<in> {s. s \<Turnstile>\<^sub>c tcb_ptr_to_ctcb_ptr tptr} \<inter>
                    {s. tcb_at' tptr s0 \<and> (s0, s) \<in> rf_sr}"
       using pre hin by (auto simp: dom_def clift_Some_eq_valid)
   qed
  apply (rule HoarePartial.Basic)
  apply (clarsimp simp: threadGet_def liftM_def bind_def return_def)
  subgoal premises pre for x
  proof -
    have htcb: "tcb_at' tptr s0" using pre by simp
    have hrf: "(s0, x) \<in> rf_sr" using pre by simp
    obtain ko tcb where hk: "ksPSpace s0 tptr = Some ko"
      and ha: "is_aligned tptr (objBitsKO ko)"
      and hp: "tcb_of' ko = Some tcb"
      and hc: "ps_clear tptr (objBitsKO ko) s0"
      using htcb by (clarsimp simp: obj_at'_def projectKO_eq)
    have ko: "ko = KOTCB tcb" using hp by (rule projectKO_opt_tcbD)
    have hko: "ko_wp_at' ((=) (KOTCB tcb)) tptr s0"
      using hk ha hc ko by (clarsimp simp: ko_wp_at'_def)
    have hget: "getObject tptr s0 = ({(tcb, s0)}, False)"
      using hko by (rule Detype_R.getTCB_det)
    have hcps:
      "cpspace_relation (ksPSpace s0) (underlying_memory (ksMachineState s0))
         (t_hrs_' (globals x))"
      using hrf by (clarsimp simp: rf_sr_def state_rel.cstate_relation_def Let_def)
    have htcbrel:
      "cmap_relation (map_to_tcbs (ksPSpace s0)) (clift (t_hrs_' (globals x)))
         tcb_ptr_to_ctcb_ptr ctcb_relation"
      using hcps by (clarsimp simp: cpspace_relation_def)
    have hm: "map_to_tcbs (ksPSpace s0) tptr = Some tcb"
      using hk ko by (simp add: comp_def)
    have hmdom: "tptr \<in> dom (map_to_tcbs (ksPSpace s0))"
      using hm by (auto simp: dom_def)
    have hd:
      "tcb_ptr_to_ctcb_ptr ` dom (map_to_tcbs (ksPSpace s0)) =
         dom (clift (t_hrs_' (globals x)))"
      using htcbrel by (clarsimp simp: cmap_relation_def)
    have hin: "tcb_ptr_to_ctcb_ptr tptr \<in> dom (clift (t_hrs_' (globals x)))"
      using hm hd by (auto simp: dom_def)
    obtain ctcb where hcl:
      "clift (t_hrs_' (globals x)) (tcb_ptr_to_ctcb_ptr tptr) = Some ctcb"
      using hin by (auto simp: dom_def)
    have hall:
      "\<forall>p\<in>dom (map_to_tcbs (ksPSpace s0)).
         ctcb_relation (the (map_to_tcbs (ksPSpace s0) p))
           (the (clift (t_hrs_' (globals x)) (tcb_ptr_to_ctcb_ptr p)))"
      using htcbrel by (clarsimp simp: cmap_relation_def)
    have hspec:
      "ctcb_relation (the (map_to_tcbs (ksPSpace s0) tptr))
         (the (clift (t_hrs_' (globals x)) (tcb_ptr_to_ctcb_ptr tptr)))"
      using hall hmdom by blast
    have hct: "ctcb_relation tcb ctcb"
      using hspec hm hcl by simp
    have heq: "tcbFaultHandler tcb = tcbFaultHandler_C ctcb"
      using hct by (clarsimp simp: ctcb_relation_def)
    have hfield:
      "h_val (hrs_mem (t_hrs_' (globals x)))
         (Ptr &(tcb_ptr_to_ctcb_ptr tptr\<rightarrow>[''tcbFaultHandler_C''])::word32 ptr) =
       tcbFaultHandler_C ctcb"
      using hcl
      by (simp add: h_val_field_clift' tcb_C_tcbFaultHandler_C_fl_ti)
    have hrf':
      "(s0, handlerCPtr_'_update
              (\<lambda>_. h_val (hrs_mem (t_hrs_' (globals x)))
                    (Ptr &(tcb_ptr_to_ctcb_ptr tptr\<rightarrow>[''tcbFaultHandler_C''])::word32 ptr)) x)
        \<in> rf_sr"
      using hrf by (simp add: rf_sr_def)
    show ?thesis using hget hrf' heq hfield by simp
  qed
  done
done

(* VERO END c0014 prove *)

(* VERO START c0014 disprove_aux *)



(* VERO END c0014 disprove_aux *)

vero_disprove "c0014" c0014_disprove [corres]:
  "ccorres (=) handlerCPtr_' (tcb_at' tptr) UNIV hs
           (threadGet tcbFaultHandler tptr)
           (Guard C_Guard \<lbrace>hrs_htd \<acute>t_hrs \<Turnstile>\<^sub>t (tcb_ptr_to_ctcb_ptr tptr)\<rbrace>
               (\<acute>handlerCPtr :==
                  h_val (hrs_mem \<acute>t_hrs)
                   (Ptr &(tcb_ptr_to_ctcb_ptr tptr\<rightarrow>[''tcbFaultHandler_C''])::word32 ptr)))"

(* VERO START c0014 disprove *)

oops

(* VERO END c0014 disprove *)

end

context kernel
begin

(* VERO START c0016 prove_aux *)

lemma vero_aux_c0016_asUser_nofail_tcb_at:
  "\<not> snd (asUser t m s) \<Longrightarrow> tcb_at' t s"
  apply (subst (asm) submonad_asUser.stateAssert_fn[symmetric])
  apply (cases "tcb_at' t s")
   apply assumption
  apply (simp add: stateAssert_def get_def bind_def assert_def fail_def return_def)
  done

lemma vero_aux_c0016_tcb_h_t_valid:
  assumes htcb: "tcb_at' t s"
      and hrf: "(s, c) \<in> rf_sr"
  shows "hrs_htd (t_hrs_' (globals c)) \<Turnstile>\<^sub>t tcb_ptr_to_ctcb_ptr t"
proof -
  have hcps:
    "cpspace_relation (ksPSpace s) (underlying_memory (ksMachineState s))
       (t_hrs_' (globals c))"
    using hrf by (clarsimp simp: rf_sr_def state_rel.cstate_relation_def Let_def)
  have htcbrel:
    "cmap_relation (map_to_tcbs (ksPSpace s)) (clift (t_hrs_' (globals c)))
       tcb_ptr_to_ctcb_ptr ctcb_relation"
    using hcps by (clarsimp simp: cpspace_relation_def)
  obtain ko obj where hk: "ksPSpace s t = Some ko"
    and hp: "tcb_of' ko = Some obj"
    using htcb by (clarsimp simp: obj_at'_def projectKO_eq)
  have ko: "ko = KOTCB obj" using hp by (rule projectKO_opt_tcbD)
  have hm: "map_to_tcbs (ksPSpace s) t = Some obj"
    using hk ko by (simp add: comp_def)
  have hd:
    "tcb_ptr_to_ctcb_ptr ` dom (map_to_tcbs (ksPSpace s)) =
       dom (clift (t_hrs_' (globals c)))"
    using htcbrel by (clarsimp simp: cmap_relation_def)
  have hin: "tcb_ptr_to_ctcb_ptr t \<in> dom (clift (t_hrs_' (globals c)))"
    using hm hd by (auto simp: dom_def)
  show ?thesis using hin by (auto simp: dom_def clift_Some_eq_valid)
qed

lemma vero_aux_c0016_nofail_h_t_valid:
  assumes hrf: "(s, c) \<in> rf_sr"
      and hnf: "\<not> snd (asUser t m s)"
  shows "hrs_htd (t_hrs_' (globals c)) \<Turnstile>\<^sub>t tcb_ptr_to_ctcb_ptr t"
  apply (rule vero_aux_c0016_tcb_h_t_valid[OF _ hrf])
  apply (rule vero_aux_c0016_asUser_nofail_tcb_at[OF hnf])
  done

lemma vero_aux_c0016_nofail_h_t_valid_general:
  assumes hrf: "(s, c) \<in> rf_sr"
      and hnf: "\<not> snd (asUser t m s)"
      and hhrs: "hrs = t_hrs_' (globals c)"
      and ht: "ct = tcb_ptr_to_ctcb_ptr t"
  shows "hrs_htd hrs \<Turnstile>\<^sub>t ct"
  using vero_aux_c0016_nofail_h_t_valid[OF hrf hnf] hhrs ht by simp

lemma vero_aux_c0016_rf_sr_same_globals:
  assumes hrf: "(a, s) \<in> rf_sr"
      and hg: "globals s' = globals s"
  shows "(a, s') \<in> rf_sr"
  using hrf hg unfolding rf_sr_def by simp

lemma vero_aux_c0016_rf_sr_ret_update:
  "(a, s) \<in> rf_sr \<Longrightarrow>
   (a, ret__unsigned_long_'_update (\<lambda>_. v) s) \<in> rf_sr"
  apply (erule vero_aux_c0016_rf_sr_same_globals)
  apply simp
  done

lemma vero_aux_c0016_restartPC_value:
  assumes hrf: "(s, c) \<in> rf_sr"
      and hnf: "\<not> snd (asUser t getRestartPC s)"
  shows
    "asUser t getRestartPC s =
       ({((h_val (hrs_mem (t_hrs_' (globals c)))
              (PTR(32 word[20])
                &(PTR(user_context_C)
                  &(PTR(arch_tcb_C)
                    &(tcb_ptr_to_ctcb_ptr t\<rightarrow>[''tcbArch_C''])
                     \<rightarrow>[''tcbContext_C''])
                   \<rightarrow>[''registers_C'']))).[17], s)}, False)"
proof -
  have htcb: "tcb_at' t s"
    by (rule vero_aux_c0016_asUser_nofail_tcb_at[OF hnf])
  obtain ko atcb where hk: "ksPSpace s t = Some ko"
    and hp: "tcb_of' ko = Some atcb"
    using htcb by (clarsimp simp: obj_at'_def projectKO_eq)
  have ko: "ko = KOTCB atcb" using hp by (rule projectKO_opt_tcbD)
  have hcps:
    "cpspace_relation (ksPSpace s) (underlying_memory (ksMachineState s))
       (t_hrs_' (globals c))"
    using hrf by (clarsimp simp: rf_sr_def state_rel.cstate_relation_def Let_def)
  have htcbrel:
    "cmap_relation (map_to_tcbs (ksPSpace s)) (clift (t_hrs_' (globals c)))
       tcb_ptr_to_ctcb_ptr ctcb_relation"
    using hcps by (clarsimp simp: cpspace_relation_def)
  have hm: "map_to_tcbs (ksPSpace s) t = Some atcb"
    using hk ko by (simp add: comp_def)
  have hmdom: "t \<in> dom (map_to_tcbs (ksPSpace s))"
    using hm by (auto simp: dom_def)
  have hd:
    "tcb_ptr_to_ctcb_ptr ` dom (map_to_tcbs (ksPSpace s)) =
       dom (clift (t_hrs_' (globals c)))"
    using htcbrel by (clarsimp simp: cmap_relation_def)
  have hin:
    "tcb_ptr_to_ctcb_ptr t \<in> dom (clift (t_hrs_' (globals c)))"
    using hmdom hd by blast
  obtain ctcb where hcl:
    "clift (t_hrs_' (globals c)) (tcb_ptr_to_ctcb_ptr t) = Some ctcb"
    using hin by (auto simp: dom_def)
  have hall:
    "\<forall>p\<in>dom (map_to_tcbs (ksPSpace s)).
       ctcb_relation (the (map_to_tcbs (ksPSpace s) p))
         (the (clift (t_hrs_' (globals c)) (tcb_ptr_to_ctcb_ptr p)))"
    using htcbrel by (clarsimp simp: cmap_relation_def)
  have hspec:
    "ctcb_relation (the (map_to_tcbs (ksPSpace s) t))
       (the (clift (t_hrs_' (globals c)) (tcb_ptr_to_ctcb_ptr t)))"
    using hall hmdom by blast
  have hct: "ctcb_relation atcb ctcb"
    using hspec hm hcl by simp
  have hreg:
    "ARM.user_regs (atcbContextGet (tcbArch atcb)) ARM.FaultIP =
       (registers_C (tcbContext_C (tcbArch_C ctcb))).[17]"
    using hct
    by (clarsimp simp: ctcb_relation_def carch_tcb_relation_def
                       ccontext_relation_def cregs_relation_def
                       Kernel_C.FaultIP_def)
  have harch:
    "clift (t_hrs_' (globals c))
       (PTR(arch_tcb_C) &(tcb_ptr_to_ctcb_ptr t\<rightarrow>[''tcbArch_C''])) =
       Some (tcbArch_C ctcb)"
    using hcl by (simp add: clift_field tcb_C_tcbArch_C_fl_ti)
  have hctx:
    "clift (t_hrs_' (globals c))
       (PTR(user_context_C)
         &(PTR(arch_tcb_C) &(tcb_ptr_to_ctcb_ptr t\<rightarrow>[''tcbArch_C''])
           \<rightarrow>[''tcbContext_C''])) =
       Some (tcbContext_C (tcbArch_C ctcb))"
    using harch by (simp add: clift_field arch_tcb_C_tcbContext_C_fl_ti)
  have hregs:
    "h_val (hrs_mem (t_hrs_' (globals c)))
       (PTR(32 word[20])
         &(PTR(user_context_C)
           &(PTR(arch_tcb_C) &(tcb_ptr_to_ctcb_ptr t\<rightarrow>[''tcbArch_C''])
             \<rightarrow>[''tcbContext_C''])
            \<rightarrow>[''registers_C''])) =
       registers_C (tcbContext_C (tcbArch_C ctcb))"
    using hctx
    by (simp add: h_val_field_clift' user_context_C_registers_C_fl_ti)
  have huser:
    "asUser t getRestartPC s =
       ({(ARM.user_regs (atcbContextGet (tcbArch atcb)) ARM.FaultIP, s)}, False)"
    unfolding getRestartPC_def getRegister_def
    apply (subst submonad_asUser.guarded_gets[OF htcb])
    using hk ko
    apply (simp add: gets_def get_def bind_def return_def
                     asUser_fetch_def comp_def)
    done
  show ?thesis using huser hreg hregs by simp
qed

lemma vero_aux_c0016_restartPC_result:
  assumes hrf: "(s, c) \<in> rf_sr"
      and hnf: "\<not> snd (asUser t getRestartPC s)"
      and hg: "globals c' = globals c"
  shows
    "\<exists>(rv, s')\<in>fst (asUser t getRestartPC s).
       (s', c') \<in> rf_sr \<and>
       rv =
         (h_val (hrs_mem (t_hrs_' (globals c)))
            (PTR(32 word[20])
              &(PTR(user_context_C)
                &(PTR(arch_tcb_C)
                  &(tcb_ptr_to_ctcb_ptr t\<rightarrow>[''tcbArch_C''])
                   \<rightarrow>[''tcbContext_C''])
                 \<rightarrow>[''registers_C'']))).[17]"
proof -
  note hv = vero_aux_c0016_restartPC_value[OF hrf hnf]
  have hrf': "(s, c') \<in> rf_sr"
    by (rule vero_aux_c0016_rf_sr_same_globals[OF hrf hg])
  show ?thesis using hv hrf' by simp
qed

lemma vero_aux_c0016_restartPC_result_value:
  assumes hrf: "(s, c) \<in> rf_sr"
      and hnf: "\<not> snd (asUser t getRestartPC s)"
      and hg: "globals c' = globals c"
      and hv:
        "v =
          (h_val (hrs_mem (t_hrs_' (globals c)))
             (PTR(32 word[20])
               &(PTR(user_context_C)
                 &(PTR(arch_tcb_C)
                   &(tcb_ptr_to_ctcb_ptr t\<rightarrow>[''tcbArch_C''])
                    \<rightarrow>[''tcbContext_C''])
                  \<rightarrow>[''registers_C'']))).[17]"
  shows
    "\<exists>(rv, s')\<in>fst (asUser t getRestartPC s).
       (s', c') \<in> rf_sr \<and> rv = v"
  using vero_aux_c0016_restartPC_result[OF hrf hnf hg] hv by simp

lemma vero_aux_c0016_restartPC_result_general:
  assumes hrf: "(s, c) \<in> rf_sr"
      and hnf: "\<not> snd (asUser t getRestartPC s)"
      and hg: "globals c' = globals c"
      and hhrs: "hrs = t_hrs_' (globals c)"
      and ht: "ct = tcb_ptr_to_ctcb_ptr t"
      and hr: "cr = (0x11 :: 32 word)"
  shows
    "\<exists>(rv, s')\<in>fst (asUser t getRestartPC s).
       (s', c') \<in> rf_sr \<and>
       rv =
         (h_val (hrs_mem hrs)
            (PTR(32 word[20])
              &(PTR(user_context_C)
                &(PTR(arch_tcb_C)
                  &(ct\<rightarrow>[''tcbArch_C''])
                   \<rightarrow>[''tcbContext_C''])
                 \<rightarrow>[''registers_C'']))).[unat cr]"
proof -
  note hresult = vero_aux_c0016_restartPC_result[OF hrf hnf hg]
  show ?thesis using hresult hhrs ht hr by simp
qed

(* VERO END c0016 prove_aux *)

vero_prove "c0016" c0016_prove [corres]:
  "ccorres (=) ret__unsigned_long_' \<top> \<lbrace>\<acute>thread = tcb_ptr_to_ctcb_ptr thread\<rbrace> []
           (asUser thread getRestartPC) (Call getRestartPC_'proc)"

(* VERO START c0016 prove *)

apply (rule ccorres_Call_call_for_vcg)
apply (rule ccorres_from_vcg_nofail2)
apply (intro allI)
apply vcg
apply (intro conjI)
 apply (simp add: Kernel_C.FaultIP_def)
apply (rule vero_aux_c0016_nofail_h_t_valid_general)
    apply assumption
   apply assumption
  apply simp
 apply simp
apply (rule vero_aux_c0016_restartPC_result_value)
   apply assumption
  apply assumption
 apply simp
apply (simp add: Kernel_C.FaultIP_def)
done

(* VERO END c0016 prove *)

(* VERO START c0016 disprove_aux *)



(* VERO END c0016 disprove_aux *)

vero_disprove "c0016" c0016_disprove [corres]:
  "ccorres (=) ret__unsigned_long_' \<top> \<lbrace>\<acute>thread = tcb_ptr_to_ctcb_ptr thread\<rbrace> []
           (asUser thread getRestartPC) (Call getRestartPC_'proc)"

(* VERO START c0016 disprove *)

oops

(* VERO END c0016 disprove *)

(* VERO START c0021 prove_aux *)

lemma vero_aux_c0021_register_bound:
  "register_from_H r < (0x14 :: 32 word)"
  by (cases r; simp add: register_from_H.simps
                         Kernel_C.R0_def Kernel_C.R1_def Kernel_C.R2_def
                         Kernel_C.R3_def Kernel_C.R4_def Kernel_C.R5_def
                         Kernel_C.R6_def Kernel_C.R7_def Kernel_C.R8_def
                         Kernel_C.R9_def Kernel_C.R10_def Kernel_C.R11_def
                         Kernel_C.R12_def Kernel_C.SP_def Kernel_C.LR_def
                         Kernel_C.NextIP_def Kernel_C.CPSR_def
                         Kernel_C.TPIDRURW_def Kernel_C.TPIDRURO_def
                         Kernel_C.FaultIP_def)

lemma vero_aux_c0021_getRegister_not_tcb:
  assumes hn: "\<not> tcb_at' t s"
  shows "asUser t (getRegister r) s = ({}, True)"
  unfolding getRegister_def
  apply (subst submonad_asUser.gets)
  using hn
  apply (simp add: stateAssert_def get_def bind_def assert_def fail_def return_def)
  done

lemma vero_aux_c0021_getRegister_empty_fail:
  "empty_fail (asUser t (getRegister r))"
  unfolding empty_fail_def
proof (intro allI impI)
  fix s
  assume hempty: "fst (asUser t (getRegister r) s) = {}"
  show "snd (asUser t (getRegister r) s)"
  proof (cases "tcb_at' t s")
    case True
    have hact:
      "asUser t (getRegister r) s =
       gets ((\<lambda>uc. ARM.user_regs uc r) \<circ> asUser_fetch t) s"
      unfolding getRegister_def
      by (rule submonad_asUser.guarded_gets[OF True])
    from hempty hact show ?thesis
      by (simp add: gets_def get_def bind_def return_def)
  next
    case False
    then show ?thesis
      by (simp add: vero_aux_c0021_getRegister_not_tcb)
  qed
qed

lemma vero_aux_c0021_getRegister_no_result_without_tcb:
  "\<lbrace>\<lambda>s. \<not> tcb_at' t s\<rbrace>
     asUser t (getRegister r) \<lbrace>\<lambda>_ _. False\<rbrace>"
  unfolding valid_def
  by (simp add: vero_aux_c0021_getRegister_not_tcb)

lemma vero_aux_c0021_getRegister_nofail:
  assumes htcb: "tcb_at' t s"
  shows "\<not> snd (asUser t (getRegister r) s)"
  unfolding getRegister_def
  apply (subst submonad_asUser.guarded_gets[OF htcb])
  apply (simp add: gets_def get_def bind_def return_def)
  done

lemma vero_aux_c0021_getRegister_value:
  assumes hrf: "(s, c) \<in> rf_sr"
      and hnf: "\<not> snd (asUser t (getRegister r) s)"
  shows
    "asUser t (getRegister r) s =
       ({((h_val (hrs_mem (t_hrs_' (globals c)))
              (PTR(32 word[20])
                &(PTR(user_context_C)
                  &(PTR(arch_tcb_C)
                    &(tcb_ptr_to_ctcb_ptr t\<rightarrow>[''tcbArch_C''])
                     \<rightarrow>[''tcbContext_C''])
                   \<rightarrow>[''registers_C'']))).[unat (register_from_H r)], s)}, False)"
proof -
  have htcb: "tcb_at' t s"
    by (rule vero_aux_c0016_asUser_nofail_tcb_at[OF hnf])
  obtain ko atcb where hk: "ksPSpace s t = Some ko"
    and hp: "tcb_of' ko = Some atcb"
    using htcb by (clarsimp simp: obj_at'_def projectKO_eq)
  have ko: "ko = KOTCB atcb" using hp by (rule projectKO_opt_tcbD)
  have hcps:
    "cpspace_relation (ksPSpace s) (underlying_memory (ksMachineState s))
       (t_hrs_' (globals c))"
    using hrf by (clarsimp simp: rf_sr_def state_rel.cstate_relation_def Let_def)
  have htcbrel:
    "cmap_relation (map_to_tcbs (ksPSpace s)) (clift (t_hrs_' (globals c)))
       tcb_ptr_to_ctcb_ptr ctcb_relation"
    using hcps by (clarsimp simp: cpspace_relation_def)
  have hm: "map_to_tcbs (ksPSpace s) t = Some atcb"
    using hk ko by (simp add: comp_def)
  have hmdom: "t \<in> dom (map_to_tcbs (ksPSpace s))"
    using hm by (auto simp: dom_def)
  have hd:
    "tcb_ptr_to_ctcb_ptr ` dom (map_to_tcbs (ksPSpace s)) =
       dom (clift (t_hrs_' (globals c)))"
    using htcbrel by (clarsimp simp: cmap_relation_def)
  have hin:
    "tcb_ptr_to_ctcb_ptr t \<in> dom (clift (t_hrs_' (globals c)))"
    using hmdom hd by blast
  obtain ctcb where hcl:
    "clift (t_hrs_' (globals c)) (tcb_ptr_to_ctcb_ptr t) = Some ctcb"
    using hin by (auto simp: dom_def)
  have hall:
    "\<forall>p\<in>dom (map_to_tcbs (ksPSpace s)).
       ctcb_relation (the (map_to_tcbs (ksPSpace s) p))
         (the (clift (t_hrs_' (globals c)) (tcb_ptr_to_ctcb_ptr p)))"
    using htcbrel by (clarsimp simp: cmap_relation_def)
  have hspec:
    "ctcb_relation (the (map_to_tcbs (ksPSpace s) t))
       (the (clift (t_hrs_' (globals c)) (tcb_ptr_to_ctcb_ptr t)))"
    using hall hmdom by blast
  have hct: "ctcb_relation atcb ctcb"
    using hspec hm hcl by simp
  have hreg:
    "ARM.user_regs (atcbContextGet (tcbArch atcb)) r =
       (registers_C (tcbContext_C (tcbArch_C ctcb))).[unat (register_from_H r)]"
    using hct
    by (clarsimp simp: ctcb_relation_def carch_tcb_relation_def
                       ccontext_relation_def cregs_relation_def)
  have harch:
    "clift (t_hrs_' (globals c))
       (PTR(arch_tcb_C) &(tcb_ptr_to_ctcb_ptr t\<rightarrow>[''tcbArch_C''])) =
       Some (tcbArch_C ctcb)"
    using hcl by (simp add: clift_field tcb_C_tcbArch_C_fl_ti)
  have hctx:
    "clift (t_hrs_' (globals c))
       (PTR(user_context_C)
         &(PTR(arch_tcb_C) &(tcb_ptr_to_ctcb_ptr t\<rightarrow>[''tcbArch_C''])
           \<rightarrow>[''tcbContext_C''])) =
       Some (tcbContext_C (tcbArch_C ctcb))"
    using harch by (simp add: clift_field arch_tcb_C_tcbContext_C_fl_ti)
  have hregs:
    "h_val (hrs_mem (t_hrs_' (globals c)))
       (PTR(32 word[20])
         &(PTR(user_context_C)
           &(PTR(arch_tcb_C) &(tcb_ptr_to_ctcb_ptr t\<rightarrow>[''tcbArch_C''])
             \<rightarrow>[''tcbContext_C''])
            \<rightarrow>[''registers_C''])) =
       registers_C (tcbContext_C (tcbArch_C ctcb))"
    using hctx
    by (simp add: h_val_field_clift' user_context_C_registers_C_fl_ti)
  have huser:
    "asUser t (getRegister r) s =
       ({(ARM.user_regs (atcbContextGet (tcbArch atcb)) r, s)}, False)"
    unfolding getRegister_def
    apply (subst submonad_asUser.guarded_gets[OF htcb])
    using hk ko
    apply (simp add: gets_def get_def bind_def return_def
                     asUser_fetch_def comp_def)
    done
  show ?thesis using huser hreg hregs by simp
qed

lemma vero_aux_c0021_getRegister_result_value:
  assumes hrf: "(s, c) \<in> rf_sr"
      and hnf: "\<not> snd (asUser t (getRegister r) s)"
      and hg: "globals c' = globals c"
      and hv:
        "v =
          (h_val (hrs_mem (t_hrs_' (globals c)))
             (PTR(32 word[20])
               &(PTR(user_context_C)
                 &(PTR(arch_tcb_C)
                   &(tcb_ptr_to_ctcb_ptr t\<rightarrow>[''tcbArch_C''])
                    \<rightarrow>[''tcbContext_C''])
                  \<rightarrow>[''registers_C'']))).[unat (register_from_H r)]"
  shows
    "\<exists>(rv, s')\<in>fst (asUser t (getRegister r) s).
       (s', c') \<in> rf_sr \<and> rv = v"
proof -
  note hvalue = vero_aux_c0021_getRegister_value[OF hrf hnf]
  have hrf': "(s, c') \<in> rf_sr"
    by (rule vero_aux_c0016_rf_sr_same_globals[OF hrf hg])
  show ?thesis using hvalue hrf' hv by simp
qed

lemma vero_aux_c0021_tcb_h_t_valid_general:
  assumes htcb: "tcb_at' t s"
      and hrf: "(s, c) \<in> rf_sr"
      and hhrs: "hrs = t_hrs_' (globals c)"
      and ht: "ct = tcb_ptr_to_ctcb_ptr t"
  shows "hrs_htd hrs \<Turnstile>\<^sub>t ct"
  using vero_aux_c0016_tcb_h_t_valid[OF htcb hrf] hhrs ht by simp

lemma vero_aux_c0021_getRegister_result_value_tcb:
  assumes hrf: "(s, c) \<in> rf_sr"
      and htcb: "tcb_at' t s"
      and hg: "globals c' = globals c"
      and hv:
        "v =
          (h_val (hrs_mem (t_hrs_' (globals c)))
             (PTR(32 word[20])
               &(PTR(user_context_C)
                 &(PTR(arch_tcb_C)
                   &(tcb_ptr_to_ctcb_ptr t\<rightarrow>[''tcbArch_C''])
                    \<rightarrow>[''tcbContext_C''])
                  \<rightarrow>[''registers_C'']))).[unat (register_from_H r)]"
  shows
    "\<exists>(rv, s')\<in>fst (asUser t (getRegister r) s).
       (s', c') \<in> rf_sr \<and> rv = v"
  apply (rule vero_aux_c0021_getRegister_result_value[OF hrf])
    apply (rule vero_aux_c0021_getRegister_nofail[OF htcb])
   apply (rule hg)
  apply (rule hv)
  done

(* VERO END c0021 prove_aux *)

vero_prove "c0021" c0021_prove [corres]:
  "ccorres (=) ret__unsigned_long_' \<top>
             ({s. thread_' s = tcb_ptr_to_ctcb_ptr thread} \<inter> {s. reg_' s = register_from_H reg}) []
             (asUser thread (getRegister reg)) (Call getRegister_'proc)"

(* VERO START c0021 prove *)

oops

(* VERO END c0021 prove *)

(* VERO START c0021 disprove_aux *)



(* VERO END c0021 disprove_aux *)

vero_disprove "c0021" c0021_disprove [corres]:
  "ccorres (=) ret__unsigned_long_' \<top>
             ({s. thread_' s = tcb_ptr_to_ctcb_ptr thread} \<inter> {s. reg_' s = register_from_H reg}) []
             (asUser thread (getRegister reg)) (Call getRegister_'proc)"

(* VERO START c0021 disprove *)

oops

(* VERO END c0021 disprove *)

end

context kernel_m
begin

(* VERO START c0022 prove_aux *)

definition vero_aux_c0022_stopped_C :: "32 word \<Rightarrow> bool" where
  "vero_aux_c0022_stopped_C x \<longleftrightarrow>
     x = SCAST(32 signed \<rightarrow> 32) ThreadState_BlockedOnReply \<or>
     x = SCAST(32 signed \<rightarrow> 32) ThreadState_BlockedOnNotification \<or>
     x = SCAST(32 signed \<rightarrow> 32) ThreadState_BlockedOnSend \<or>
     x = SCAST(32 signed \<rightarrow> 32) ThreadState_BlockedOnReceive \<or>
     x = SCAST(32 signed \<rightarrow> 32) ThreadState_Inactive"

lemma vero_aux_c0022_stopped_relation:
  assumes "cthread_state_relation ts (cts, cf)"
  shows "Not (activatable' ts) =
         vero_aux_c0022_stopped_C (tsType_CL (thread_state_lift cts))"
  using assms
  by (cases ts; simp add: cthread_state_relation_def
                          vero_aux_c0022_stopped_C_def
                          ThreadState_Running_def ThreadState_Inactive_def
                          ThreadState_Restart_def
                          ThreadState_BlockedOnReceive_def
                          ThreadState_BlockedOnSend_def
                          ThreadState_BlockedOnReply_def
                          ThreadState_BlockedOnNotification_def
                          ThreadState_IdleThreadState_def)

lemma vero_aux_c0022_tcb_h_t_valid:
  assumes htcb: "tcb_at' thread sigma"
      and hrf: "(sigma, s) \<in> rf_sr"
  shows "hrs_htd (t_hrs_' (globals s)) \<Turnstile>\<^sub>t
           tcb_ptr_to_ctcb_ptr thread"
proof -
  have hcps:
    "cpspace_relation (ksPSpace sigma)
       (underlying_memory (ksMachineState sigma))
       (t_hrs_' (globals s))"
    using hrf by (clarsimp simp: rf_sr_def state_rel.cstate_relation_def Let_def)
  have htcbrel:
    "cmap_relation (map_to_tcbs (ksPSpace sigma))
       (clift (t_hrs_' (globals s)))
       tcb_ptr_to_ctcb_ptr ctcb_relation"
    using hcps by (clarsimp simp: cpspace_relation_def)
  obtain ko obj where hk: "ksPSpace sigma thread = Some ko"
    and hp: "tcb_of' ko = Some obj"
    using htcb by (clarsimp simp: obj_at'_def projectKO_eq)
  have ko: "ko = KOTCB obj" using hp by (rule projectKO_opt_tcbD)
  have hm: "map_to_tcbs (ksPSpace sigma) thread = Some obj"
    using hk ko by (simp add: comp_def)
  have hd:
    "tcb_ptr_to_ctcb_ptr ` dom (map_to_tcbs (ksPSpace sigma)) =
       dom (clift (t_hrs_' (globals s)))"
    using htcbrel by (clarsimp simp: cmap_relation_def)
  have hin:
    "tcb_ptr_to_ctcb_ptr thread \<in> dom (clift (t_hrs_' (globals s)))"
    using hm hd by (auto simp: dom_def)
  show ?thesis using hin by (auto simp: dom_def clift_Some_eq_valid)
qed

lemma vero_aux_c0022_isStopped_result:
  assumes htcb: "tcb_at' thread sigma"
      and hrf: "(sigma, s) \<in> rf_sr"
      and hg: "globals x = globals s"
  shows
    "\<exists>(rv, sigma')\<in>fst (isStopped thread sigma).
       (sigma', x) \<in> rf_sr \<and>
       rv =
         vero_aux_c0022_stopped_C
           (tsType_CL
             (thread_state_lift
               (h_val (hrs_mem (t_hrs_' (globals s)))
                 (PTR(thread_state_C)
                   &(tcb_ptr_to_ctcb_ptr thread\<rightarrow>[''tcbState_C''])))))"
proof -
  obtain ko atcb where hk: "ksPSpace sigma thread = Some ko"
    and hp: "tcb_of' ko = Some atcb"
    using htcb by (clarsimp simp: obj_at'_def projectKO_eq)
  have ko: "ko = KOTCB atcb" using hp by (rule projectKO_opt_tcbD)
  have hko: "ko_wp_at' ((=) (KOTCB atcb)) thread sigma"
    using htcb hk ko by (clarsimp simp: obj_at'_def ko_wp_at'_def)
  have hget: "getObject thread sigma = ({(atcb, sigma)}, False)"
    using hko by (rule Detype_R.getTCB_det)
  have hcps:
    "cpspace_relation (ksPSpace sigma)
       (underlying_memory (ksMachineState sigma))
       (t_hrs_' (globals s))"
    using hrf by (clarsimp simp: rf_sr_def state_rel.cstate_relation_def Let_def)
  have htcbrel:
    "cmap_relation (map_to_tcbs (ksPSpace sigma))
       (clift (t_hrs_' (globals s)))
       tcb_ptr_to_ctcb_ptr ctcb_relation"
    using hcps by (clarsimp simp: cpspace_relation_def)
  have hm: "map_to_tcbs (ksPSpace sigma) thread = Some atcb"
    using hk ko by (simp add: comp_def)
  have hmdom: "thread \<in> dom (map_to_tcbs (ksPSpace sigma))"
    using hm by (auto simp: dom_def)
  have hd:
    "tcb_ptr_to_ctcb_ptr ` dom (map_to_tcbs (ksPSpace sigma)) =
       dom (clift (t_hrs_' (globals s)))"
    using htcbrel by (clarsimp simp: cmap_relation_def)
  have hin:
    "tcb_ptr_to_ctcb_ptr thread \<in> dom (clift (t_hrs_' (globals s)))"
    using hmdom hd by blast
  obtain ctcb where hcl:
    "clift (t_hrs_' (globals s)) (tcb_ptr_to_ctcb_ptr thread) = Some ctcb"
    using hin by (auto simp: dom_def)
  have hall:
    "\<forall>p\<in>dom (map_to_tcbs (ksPSpace sigma)).
       ctcb_relation (the (map_to_tcbs (ksPSpace sigma) p))
         (the (clift (t_hrs_' (globals s)) (tcb_ptr_to_ctcb_ptr p)))"
    using htcbrel by (clarsimp simp: cmap_relation_def)
  have hspec:
    "ctcb_relation (the (map_to_tcbs (ksPSpace sigma) thread))
       (the (clift (t_hrs_' (globals s))
         (tcb_ptr_to_ctcb_ptr thread)))"
    using hall hmdom by blast
  have hct: "ctcb_relation atcb ctcb"
    using hspec hm hcl by simp
  have hstate:
    "cthread_state_relation (tcbState atcb)
       (tcbState_C ctcb, tcbFault_C ctcb)"
    using hct by (clarsimp simp: ctcb_relation_def)
  have hfield:
    "h_val (hrs_mem (t_hrs_' (globals s)))
       (PTR(thread_state_C)
         &(tcb_ptr_to_ctcb_ptr thread\<rightarrow>[''tcbState_C''])) =
     tcbState_C ctcb"
    using hcl by (simp add: h_val_field_clift' tcb_C_tcbState_C_fl_ti)
  have hstop:
    "Not (activatable' (tcbState atcb)) =
       vero_aux_c0022_stopped_C
         (tsType_CL
           (thread_state_lift
             (h_val (hrs_mem (t_hrs_' (globals s)))
               (PTR(thread_state_C)
                 &(tcb_ptr_to_ctcb_ptr thread\<rightarrow>[''tcbState_C''])))))"
    using vero_aux_c0022_stopped_relation[OF hstate] hfield by simp
  have his:
    "isStopped thread sigma =
       ({(Not (activatable' (tcbState atcb)), sigma)}, False)"
    unfolding isStopped_def2 getThreadState_def threadGet_def liftM_def
    using hget by (simp add: bind_def return_def)
  have hrf': "(sigma, x) \<in> rf_sr"
    using hrf hg by (simp add: rf_sr_def)
  show ?thesis using his hrf' hstop by simp
qed

lemma vero_aux_c0022_isStopped_C:
  "\<forall>s0. \<Gamma> \<turnstile>
    {s. s = s0 \<and>
        hrs_htd (t_hrs_' (globals s0)) \<Turnstile>\<^sub>t thread_' s0}
    Call isStopped_'proc
    {t. ret__unsigned_long_' t =
          from_bool
            (vero_aux_c0022_stopped_C
              (tsType_CL
                (thread_state_lift
                  (h_val (hrs_mem (t_hrs_' (globals s0)))
                    (PTR(thread_state_C)
                      &(thread_' s0\<rightarrow>[''tcbState_C''])))))) \<and>
        globals t = globals s0}"
  apply (intro allI)
  apply vcg
  apply (simp add: vero_aux_c0022_stopped_C_def from_bool_def
                   true_def false_def)
  done

(* VERO END c0022 prove_aux *)

vero_prove "c0022" c0022_prove [corres]:
  "ccorres (\<lambda>r r'. r = to_bool r') ret__unsigned_long_'
  (tcb_at' thread) (UNIV \<inter> {s. thread_' s = tcb_ptr_to_ctcb_ptr thread})  []
  (isStopped thread) (Call isStopped_'proc)"

(* VERO START c0022 prove *)

apply (rule ccorres_from_vcg_nofail2)
apply (intro allI)
apply (rule HoarePartial.conseq)
 apply (rule vero_aux_c0022_isStopped_C)
apply clarsimp
apply (intro conjI)
 apply (rule vero_aux_c0022_tcb_h_t_valid)
  apply assumption
 apply assumption
apply (rule subsetI)
apply clarsimp
apply (rule vero_aux_c0022_isStopped_result)
  apply assumption
 apply assumption
apply assumption
done

(* VERO END c0022 prove *)

(* VERO START c0022 disprove_aux *)



(* VERO END c0022 disprove_aux *)

vero_disprove "c0022" c0022_disprove [corres]:
  "ccorres (\<lambda>r r'. r = to_bool r') ret__unsigned_long_'
  (tcb_at' thread) (UNIV \<inter> {s. thread_' s = tcb_ptr_to_ctcb_ptr thread})  []
  (isStopped thread) (Call isStopped_'proc)"

(* VERO START c0022 disprove *)

oops

(* VERO END c0022 disprove *)

end

context kernel_m begin

(* VERO START c0057 prove_aux *)

lemma vero_aux_c0057_threadSet_eval:
  assumes ht: "tcb_at' p s"
  shows "\<exists>tcb. ksPSpace s p = Some (KOTCB tcb) \<and>
           threadSet f p s =
             ({((), ksPSpace_update
                      (\<lambda>ps. ps(p \<mapsto> KOTCB (f tcb))) s)}, False)"
proof -
  obtain ko tcb where hk0: "ksPSpace s p = Some ko"
    and hp: "tcb_of' ko = Some tcb"
    using ht by (clarsimp simp: obj_at'_def projectKO_eq)
  have hko: "ko = KOTCB tcb"
    using hp by (rule projectKO_opt_tcbD)
  have hk: "ksPSpace s p = Some (KOTCB tcb)"
    using hk0 hko by simp
  have hdet:
    "threadSet f p s =
       modify
        (ksPSpace_update
          (\<lambda>ps. ps(p \<mapsto>
             case ps p of Some (KOTCB t) \<Rightarrow> KOTCB (f t)))) s"
    using ht by (rule Detype_R.threadSet_det)
  show ?thesis
    using hk hdet by (simp add: Nondet_Monad.simpler_modify_def)
qed

lemma vero_aux_c0057_clift_domain_update:
  assumes hcl: "clift hp p = Some ctcb"
  shows
    "clift
       (hrs_mem_update
         (heap_update (Ptr &(p\<rightarrow>[''tcbDomain_C'']) :: word32 ptr) v) hp) =
     (clift hp)(p \<mapsto> tcbDomain_C_update (\<lambda>_. v) ctcb)"
  apply (subst TypHeapLib.clift_field_update)
     apply (rule Kernel_C.tcb_C_tcbDomain_C_fl_ti)
    apply simp
   apply (rule hcl)
  apply simp
  done

lemma vero_aux_c0057_ctcb_relation_domain_update:
  assumes hrel: "ctcb_relation tcb ctcb"
  shows
    "ctcb_relation (tcbDomain_update (\<lambda>_. d) tcb)
       (tcbDomain_C_update (\<lambda>_. ucast d) ctcb)"
  using hrel
  by (clarsimp simp: ctcb_relation_def)

lemma vero_aux_c0057_map_to_tcbs_update:
  "map_to_tcbs (hp(p \<mapsto> KOTCB tcb)) =
   (map_to_tcbs hp)(p \<mapsto> tcb)"
  by (rule ext; simp add: map_comp_def)

lemma vero_aux_c0057_map_to_ctes_domain_update_raw:
  assumes hk: "hp p = Some (KOTCB tcb)"
  shows
    "map_to_ctes (hp(p \<mapsto> KOTCB (tcbDomain_update f tcb))) =
     map_to_ctes hp"
proof -
  have hcases:
    "map_option
       (\<lambda>(getF, setF). getF (tcbDomain_update f tcb))
       (tcb_cte_cases z) =
     map_option (\<lambda>(getF, setF). getF tcb) (tcb_cte_cases z)"
    for z
    by (simp add: tcb_cte_cases_def)
  show ?thesis
  proof (rule ext)
    fix x
    have hdom:
      "dom (hp(p \<mapsto> KOTCB (tcbDomain_update f tcb))) = dom hp"
      using hk by (auto simp: dom_def)
    show
      "map_to_ctes (hp(p \<mapsto> KOTCB (tcbDomain_update f tcb))) x =
       map_to_ctes hp x"
    proof (cases "x = p")
      case True
      then show ?thesis
        unfolding map_to_ctes_def Let_def
        apply (simp only: hdom)
        apply (cases
          "x && ~~ mask (objBitsKO (KOTCB undefined)) = p")
         using hk hcases apply simp
        using hk apply simp
        done
    next
      case xne: False
      have hx:
        "(hp(p \<mapsto> KOTCB (tcbDomain_update f tcb))) x = hp x"
        using xne by simp
      show ?thesis
      proof (cases
        "x && ~~ mask (objBitsKO (KOTCB undefined)) = p")
        case True
        then show ?thesis
          unfolding map_to_ctes_def Let_def
          using hk hdom hcases hx
          by (simp; blast)
      next
        case yne: False
        have hy:
          "(hp(p \<mapsto> KOTCB (tcbDomain_update f tcb)))
             (x && ~~ mask (objBitsKO (KOTCB undefined))) =
           hp (x && ~~ mask (objBitsKO (KOTCB undefined)))"
          using yne by simp
        show ?thesis
          unfolding map_to_ctes_def Let_def
          using hdom hx hy
          by (simp; blast)
      qed
    qed
  qed
qed

lemma vero_aux_c0057_cmap_relation_overwrite:
  assumes hr: "cmap_relation am cm addr rel"
    and ha: "am p = Some a"
    and hc: "cm (addr p) = Some c"
    and hn: "rel a' c'"
    and hi: "inj addr"
  shows
    "cmap_relation (am(p := Some a')) (cm(addr p := Some c')) addr rel"
proof -
  have hdom: "addr ` dom am = dom cm"
    using hr unfolding cmap_relation_def by simp
  have hpoint:
    "\<forall>x\<in>dom am. rel (the (am x)) (the (cm (addr x)))"
    using hr unfolding cmap_relation_def by simp
  have hdam: "dom (am(p := Some a')) = dom am"
    using ha by (auto simp: dom_def)
  have hdcm: "dom (cm(addr p := Some c')) = dom cm"
    using hc by (auto simp: dom_def)
  show ?thesis
    unfolding cmap_relation_def
    apply (intro conjI)
     apply (simp only: hdam hdcm hdom)
    apply (intro ballI)
    apply (rename_tac x)
    apply (case_tac "x = p")
     apply (simp add: hn)
    apply (subgoal_tac "x \<in> dom am")
     prefer 2
     apply (simp add: hdam)
    apply (subgoal_tac "addr x \<noteq> addr p")
     prefer 2
    apply (metis hi injD)
    apply (simp add: hpoint)
    done
qed

lemma vero_aux_c0057_tcb_queue_relation_update:
  assumes hq: "tcb_queue_relation getNext getPrev hp q before qhead"
    and hp: "hp p = Some tcb"
    and hn: "getNext (upd tcb) = getNext tcb"
    and hv: "getPrev (upd tcb) = getPrev tcb"
  shows
    "tcb_queue_relation getNext getPrev (hp(p := Some (upd tcb)))
       q before qhead"
  using hq
proof (induct q arbitrary: before qhead)
  case Nil
  then show ?case by simp
next
  case (Cons x xs)
  then obtain t where
    hhead: "qhead = tcb_ptr_to_ctcb_ptr x"
    and hpt: "hp qhead = Some t"
    and hprev: "getPrev t = before"
    and htail:
      "tcb_queue_relation getNext getPrev hp xs qhead (getNext t)"
    by clarsimp
  show ?case
  proof (cases "qhead = p")
    case True
    then have ht: "t = tcb"
      using hpt hp by simp
    have htail':
      "tcb_queue_relation getNext getPrev (hp(p := Some (upd tcb)))
         xs qhead (getNext t)"
      using Cons.hyps[OF htail] by blast
    have hu:
      "(hp(p := Some (upd tcb))) qhead = Some (upd tcb)"
      using True by simp
    have hpv: "getPrev (upd tcb) = before"
      using hprev ht hv by simp
    have hnext: "getNext (upd tcb) = getNext t"
      using ht hn by simp
    show ?thesis
      unfolding tcb_queue_relation.simps
      apply (intro conjI)
       apply (rule hhead)
      apply (rule exI[where x="upd tcb"])
      using hu hpv htail' hnext
      apply simp
      done
  next
    case False
    have htail':
      "tcb_queue_relation getNext getPrev (hp(p := Some (upd tcb)))
         xs qhead (getNext t)"
      using Cons.hyps[OF htail] by blast
    have hu: "(hp(p := Some (upd tcb))) qhead = Some t"
      using False hpt by simp
    show ?thesis
      unfolding tcb_queue_relation.simps
      apply (intro conjI)
       apply (rule hhead)
      apply (rule exI[where x=t])
      using hu hprev htail'
      apply simp
      done
  qed
qed

lemma vero_aux_c0057_ep_queue_relation_update:
  assumes hq: "ep_queue_relation' hp q qhead qlast"
    and hp: "hp p = Some tcb"
  shows
    "ep_queue_relation'
       (hp(p := Some (tcbDomain_C_update f tcb))) q qhead qlast"
proof -
  have hq0:
    "tcb_queue_relation tcbEPNext_C tcbEPPrev_C hp q NULL qhead"
    using hq unfolding tcb_queue_relation'_def by blast
  have hsame:
    "tcb_queue_relation tcbEPNext_C tcbEPPrev_C
       (hp(p := Some (tcbDomain_C_update f tcb))) q NULL qhead"
    apply (rule vero_aux_c0057_tcb_queue_relation_update[OF hq0 hp])
     apply simp
    apply simp
    done
  show ?thesis
    using hq hsame
    unfolding tcb_queue_relation'_def by blast
qed

lemma vero_aux_c0057_tcb_ptr_inj:
  "inj tcb_ptr_to_ctcb_ptr"
  by (rule injI; simp add: tcb_ptr_to_ctcb_ptr_def)

lemma vero_aux_c0057_cpspace_tcb_relation_eq:
  "cpspace_tcb_relation hp hrs =
   cmap_relation (map_to_tcbs hp) (clift hrs)
     tcb_ptr_to_ctcb_ptr ctcb_relation"
  by simp

lemma vero_aux_c0057_cpspace_tcb_relation_update:
  assumes hr: "cpspace_tcb_relation hp hrs"
    and hk: "hp p = Some (KOTCB tcb)"
    and hcl:
      "clift hrs (tcb_ptr_to_ctcb_ptr p) = Some ctcb"
    and hn:
      "ctcb_relation (tcbDomain_update (\<lambda>_. d) tcb)
         (tcbDomain_C_update (\<lambda>_. ucast d) ctcb)"
  shows
    "cpspace_tcb_relation
       (hp(p := Some (KOTCB (tcbDomain_update (\<lambda>_. d) tcb))))
       (hrs_mem_update
         (heap_update
           (Ptr &(tcb_ptr_to_ctcb_ptr p\<rightarrow>[''tcbDomain_C'']) :: word32 ptr)
           (ucast d)) hrs)"
proof -
  have hmap:
    "map_to_tcbs hp p = Some tcb"
    using hk by (simp add: map_comp_def)
  have hrel:
    "cmap_relation (map_to_tcbs hp) (clift hrs)
       tcb_ptr_to_ctcb_ptr ctcb_relation"
    by (rule iffD1[OF vero_aux_c0057_cpspace_tcb_relation_eq hr])
  have hnew:
    "cmap_relation
       ((map_to_tcbs hp)(p := Some (tcbDomain_update (\<lambda>_. d) tcb)))
       ((clift hrs)(tcb_ptr_to_ctcb_ptr p :=
          Some (tcbDomain_C_update (\<lambda>_. ucast d) ctcb)))
       tcb_ptr_to_ctcb_ptr ctcb_relation"
    by (rule vero_aux_c0057_cmap_relation_overwrite[OF hrel hmap hcl hn
          vero_aux_c0057_tcb_ptr_inj])
  show ?thesis
    apply (rule iffD2[OF vero_aux_c0057_cpspace_tcb_relation_eq])
    apply (subst vero_aux_c0057_map_to_tcbs_update)
    apply (subst vero_aux_c0057_clift_domain_update[OF hcl])
    apply (rule hnew)
    done
qed

lemma vero_aux_c0057_cendpoint_relation_update:
  assumes hr: "cendpoint_relation hp ep cep"
    and hcl: "hp p = Some ctcb"
  shows
    "cendpoint_relation
       (hp(p := Some (tcbDomain_C_update f ctcb))) ep cep"
  using hr hcl
  by (cases ep;
      auto simp: cendpoint_relation_def Let_def
           intro: vero_aux_c0057_ep_queue_relation_update)

lemma vero_aux_c0057_cnotification_relation_update:
  assumes hr: "cnotification_relation hp ntfn cntfn"
    and hcl: "hp p = Some ctcb"
  shows
    "cnotification_relation
       (hp(p := Some (tcbDomain_C_update f ctcb))) ntfn cntfn"
  using hr hcl
  by (cases "ntfnObj ntfn";
      auto simp: cnotification_relation_def Let_def
           intro: vero_aux_c0057_ep_queue_relation_update)

lemma vero_aux_c0057_cmap_endpoint_relation_update:
  assumes hr:
    "cmap_relation am cm ep_Ptr (cendpoint_relation hp)"
    and hcl: "hp p = Some ctcb"
  shows
    "cmap_relation am cm ep_Ptr
       (cendpoint_relation
         (hp(p := Some (tcbDomain_C_update f ctcb))))"
  using hr hcl
  unfolding cmap_relation_def
  by (auto intro: vero_aux_c0057_cendpoint_relation_update)

lemma vero_aux_c0057_cmap_notification_relation_update:
  assumes hr:
    "cmap_relation am cm ntfn_Ptr (cnotification_relation hp)"
    and hcl: "hp p = Some ctcb"
  shows
    "cmap_relation am cm ntfn_Ptr
       (cnotification_relation
         (hp(p := Some (tcbDomain_C_update f ctcb))))"
  using hr hcl
  unfolding cmap_relation_def
  by (auto intro: vero_aux_c0057_cnotification_relation_update)

lemma vero_aux_c0057_other_clift:
  fixes p :: "tcb_C ptr" and v :: word32
  assumes hcl: "clift hrs p = Some ctcb"
    and hnsub: "\<not> TYPE('a::mem_type) <\<^sub>\<tau> TYPE(tcb_C)"
    and hnfield: "\<And>r::'a ptr. \<not> field_of_t p r"
  shows
    "(clift
       (hrs_mem_update
        (heap_update (Ptr &(p\<rightarrow>[''tcbDomain_C'']) :: word32 ptr) v) hrs)
       :: 'a typ_heap) = clift hrs"
proof -
  have htcb0: "hrs_htd hrs \<Turnstile>\<^sub>t p"
    using hcl by (rule TypHeapLib.h_t_valid_clift)
  have hfl:
    "field_lookup (typ_info_t TYPE(tcb_C)) [''tcbDomain_C''] 0 =
     Some
      (adjust_ti (typ_info_t TYPE(word32)) tcbDomain_C
        (tcbDomain_C_update \<circ> (\<lambda>x _. x)), 112)"
    by (simp only: Kernel_C.tcb_C_tcbDomain_C_fl_Some)
  have hqsub:
    "{ptr_val (Ptr &(p\<rightarrow>[''tcbDomain_C'']) :: word32 ptr)
       ..+size_of TYPE(word32)}
     \<subseteq> {ptr_val p..+size_of TYPE(tcb_C)}"
    using TypHeap.field_tag_sub[OF hfl, where p=p]
    by simp
  obtain mem htd where hhrs: "hrs = (mem, htd)"
    by (cases hrs)
  have htcb: "htd \<Turnstile>\<^sub>t p"
    using htcb0 by (simp add: hhrs HeapRawState.hrs_htd_def)
  show ?thesis
  proof (rule ext)
    fix r
    show
      "(clift
         (hrs_mem_update
          (heap_update
            (Ptr &(p\<rightarrow>[''tcbDomain_C'']) :: word32 ptr) v) hrs)
         :: 'a typ_heap) r = clift hrs r"
    proof (cases "htd, c_guard \<Turnstile>\<^sub>t r")
      case False
      then show ?thesis
        by (simp add: hhrs HeapRawState.hrs_mem_update_def
            TypHeap.lift_t_if)
    next
      case True
      have hd:
        "{ptr_val p..+size_of TYPE(tcb_C)} \<inter>
         {ptr_val r..+size_of TYPE('a)} = {}"
        apply (rule TypHeap.h_t_valid_neq_disjoint[OF htcb True])
         apply (rule hnsub)
        apply (rule hnfield)
        done
      have hdq:
        "{ptr_val (Ptr &(p\<rightarrow>[''tcbDomain_C'']) :: word32 ptr)
            ..+size_of TYPE(word32)} \<inter>
         {ptr_val r..+size_of TYPE('a)} = {}"
        using hqsub hd by blast
      have hv:
        "h_val
          (heap_update
            (Ptr &(p\<rightarrow>[''tcbDomain_C'']) :: word32 ptr) v mem) r =
         h_val mem r"
        by (rule TypHeapSimple.h_val_update_regions_disjoint[OF hdq])
      show ?thesis
        using True hv
        by (simp add: hhrs HeapRawState.hrs_mem_update_def
            TypHeap.lift_t_if)
    qed
  qed
qed

lemma vero_aux_c0057_not_sub_by_name:
  assumes hn:
    "typ_name (typ_info_t TYPE('a::mem_type)) \<notin>
       td_names (typ_info_t TYPE('b::mem_type))"
    and hpad: "typ_name (typ_info_t TYPE('a)) \<noteq> pad_typ_name"
  shows "\<not> TYPE('a) <\<^sub>\<tau> TYPE('b)"
proof -
  have hnle:
    "\<not> typ_uinfo_t TYPE('a) \<le> typ_uinfo_t TYPE('b)"
    by (rule TypHeapLib.tag_not_le_via_td_name[OF hn hpad])
  then show ?thesis
    by (auto simp: sub_typ_proper_def)
qed

lemma vero_aux_c0057_not_field_by_name:
  fixes p :: "'a::mem_type ptr" and q :: "'b::mem_type ptr"
  assumes hn:
    "typ_name (typ_info_t TYPE('a)) \<notin>
       td_names (typ_info_t TYPE('b))"
    and hpad: "typ_name (typ_info_t TYPE('a)) \<noteq> pad_typ_name"
  shows "\<not> field_of_t p q"
proof
  assume hf: "field_of_t p q"
  have hle:
    "typ_uinfo_t TYPE('a) \<le> typ_uinfo_t TYPE('b)"
    using hf
    by (auto simp: field_of_t_def dest: TypHeap.field_of_sub)
  have hnle:
    "\<not> typ_uinfo_t TYPE('a) \<le> typ_uinfo_t TYPE('b)"
    by (rule TypHeapLib.tag_not_le_via_td_name[OF hn hpad])
  show False
    using hnle hle by blast
qed

lemma vero_aux_c0057_other_clifts:
  fixes v :: word32
  assumes hcl: "clift hrs p = Some (ctcb :: tcb_C)"
  defines
    "hrs' \<equiv>
       hrs_mem_update
        (heap_update (Ptr &(p\<rightarrow>[''tcbDomain_C'']) :: word32 ptr) v) hrs"
  shows
    "(clift hrs' :: cte_C typ_heap) = clift hrs \<and>
     (clift hrs' :: endpoint_C typ_heap) = clift hrs \<and>
     (clift hrs' :: notification_C typ_heap) = clift hrs \<and>
     (clift hrs' :: pde_C typ_heap) = clift hrs \<and>
     (clift hrs' :: pte_C typ_heap) = clift hrs \<and>
     (clift hrs' :: asid_pool_C typ_heap) = clift hrs \<and>
     (clift hrs' :: user_data_C typ_heap) = clift hrs \<and>
     (clift hrs' :: user_data_device_C typ_heap) = clift hrs"
proof -
  have hcte: "(clift hrs' :: cte_C typ_heap) = clift hrs"
    unfolding hrs'_def
    apply (rule vero_aux_c0057_other_clift[OF hcl])
     apply (rule vero_aux_c0057_not_sub_by_name)
      apply simp
     apply (simp add: pad_typ_name_def)
    apply (rule vero_aux_c0057_not_field_by_name)
     apply simp
    apply (simp add: pad_typ_name_def)
    done
  have hep: "(clift hrs' :: endpoint_C typ_heap) = clift hrs"
    unfolding hrs'_def
    apply (rule vero_aux_c0057_other_clift[OF hcl])
     apply (rule vero_aux_c0057_not_sub_by_name)
      apply simp
     apply (simp add: pad_typ_name_def)
    apply (rule vero_aux_c0057_not_field_by_name)
     apply simp
    apply (simp add: pad_typ_name_def)
    done
  have hntfn: "(clift hrs' :: notification_C typ_heap) = clift hrs"
    unfolding hrs'_def
    apply (rule vero_aux_c0057_other_clift[OF hcl])
     apply (rule vero_aux_c0057_not_sub_by_name)
      apply simp
     apply (simp add: pad_typ_name_def)
    apply (rule vero_aux_c0057_not_field_by_name)
     apply simp
    apply (simp add: pad_typ_name_def)
    done
  have hpde: "(clift hrs' :: pde_C typ_heap) = clift hrs"
    unfolding hrs'_def
    apply (rule vero_aux_c0057_other_clift[OF hcl])
     apply (rule vero_aux_c0057_not_sub_by_name)
      apply simp
     apply (simp add: pad_typ_name_def)
    apply (rule vero_aux_c0057_not_field_by_name)
     apply simp
    apply (simp add: pad_typ_name_def)
    done
  have hpte: "(clift hrs' :: pte_C typ_heap) = clift hrs"
    unfolding hrs'_def
    apply (rule vero_aux_c0057_other_clift[OF hcl])
     apply (rule vero_aux_c0057_not_sub_by_name)
      apply simp
     apply (simp add: pad_typ_name_def)
    apply (rule vero_aux_c0057_not_field_by_name)
     apply simp
    apply (simp add: pad_typ_name_def)
    done
  have hasid: "(clift hrs' :: asid_pool_C typ_heap) = clift hrs"
    unfolding hrs'_def
    apply (rule vero_aux_c0057_other_clift[OF hcl])
     apply (rule vero_aux_c0057_not_sub_by_name)
      apply simp
     apply (simp add: pad_typ_name_def)
    apply (rule vero_aux_c0057_not_field_by_name)
     apply simp
    apply (simp add: pad_typ_name_def)
    done
  have huser: "(clift hrs' :: user_data_C typ_heap) = clift hrs"
    unfolding hrs'_def
    apply (rule vero_aux_c0057_other_clift[OF hcl])
     apply (rule vero_aux_c0057_not_sub_by_name)
      apply simp
     apply (simp add: pad_typ_name_def)
    apply (rule vero_aux_c0057_not_field_by_name)
     apply simp
    apply (simp add: pad_typ_name_def)
    done
  have hdevice:
    "(clift hrs' :: user_data_device_C typ_heap) = clift hrs"
    unfolding hrs'_def
    apply (rule vero_aux_c0057_other_clift[OF hcl])
     apply (rule vero_aux_c0057_not_sub_by_name)
      apply simp
     apply (simp add: pad_typ_name_def)
    apply (rule vero_aux_c0057_not_field_by_name)
     apply simp
    apply (simp add: pad_typ_name_def)
    done
  show ?thesis
    using hcte hep hntfn hpde hpte hasid huser hdevice by blast
qed

lemma vero_aux_c0057_region_byte_valid:
  assumes hr: "region_actually_is_bytes' start len htd"
    and hx: "x \<in> {start..+len}"
  shows "htd, \<lambda>_. True \<Turnstile>\<^sub>t (Ptr x :: 8 word ptr)"
  using hr hx
  unfolding region_actually_is_bytes'_def h_t_valid_def
    valid_footprint_def
  by (simp add: typ_uinfo_t_def export_uinfo_def typ_info_word
      word_tag_def list_map_def map_le_def)

lemma vero_aux_c0057_word32_region_disjoint:
  fixes q :: "word32 ptr"
  assumes hv: "htd \<Turnstile>\<^sub>t q"
    and hr: "region_actually_is_bytes' start len htd"
  shows
    "{ptr_val q..+size_of TYPE(word32)} \<inter> {start..+len} = {}"
proof (rule ccontr)
  assume hn: "{ptr_val q..+size_of TYPE(word32)} \<inter> {start..+len} \<noteq> {}"
  then obtain x where hq: "x \<in> {ptr_val q..+size_of TYPE(word32)}"
    and hx: "x \<in> {start..+len}"
    by blast
  have hb: "htd, \<lambda>_. True \<Turnstile>\<^sub>t (Ptr x :: 8 word ptr)"
    by (rule vero_aux_c0057_region_byte_valid[OF hr hx])
  have hd:
    "{ptr_val q..+size_of TYPE(word32)} \<inter>
       {ptr_val (Ptr x :: 8 word ptr)..+size_of TYPE(8 word)} = {}"
    apply (rule TypHeap.h_t_valid_neq_disjoint[OF hv hb])
     apply simp
    apply (rule notI)
    apply (drule CTypes.field_of_t_simple)
    using TypHeap.h_t_valid_neq_disjoint_simple[OF hv hb]
    apply simp
    done
  have hbyte:
    "x \<in> {ptr_val (Ptr x :: 8 word ptr)..+size_of TYPE(8 word)}"
    using CTypes.mem_type_self[where p="Ptr x :: 8 word ptr"]
    by simp
  show False
    using hd hq hbyte by blast
qed

lemma vero_aux_c0057_zero_ranges_update:
  fixes q :: "word32 ptr"
  assumes hz: "zero_ranges_are_zero rs hrs"
    and hv: "hrs_htd hrs \<Turnstile>\<^sub>t q"
  shows
    "zero_ranges_are_zero rs
       (hrs_mem_update (heap_update q v) hrs)"
proof -
  show ?thesis
    unfolding zero_ranges_are_zero_def
  proof (intro ballI)
    fix r
    assume hrset: "r \<in> rs"
    obtain start stop where rr: "r = (start, stop)"
      by (cases r)
    obtain mem htd0 where hhrs: "hrs = (mem, htd0)"
      by (cases hrs)
    have hold:
      "region_actually_is_bytes' start (unat (stop + 1 - start))
         (hrs_htd hrs) \<and>
       heap_list (hrs_mem hrs) (unat (stop + 1 - start)) start =
         replicate (unat (stop + 1 - start)) 0"
      using hz hrset rr
      unfolding zero_ranges_are_zero_def
      by blast
    have hd:
      "{ptr_val q..+size_of TYPE(word32)} \<inter>
       {start..+unat (stop + 1 - start)} = {}"
      by (rule vero_aux_c0057_word32_region_disjoint[OF hv conjunct1[OF hold]])
    have hm:
      "heap_list (heap_update q v (hrs_mem hrs))
         (unat (stop + 1 - start)) start =
       heap_list (hrs_mem hrs) (unat (stop + 1 - start)) start"
      unfolding heap_update_def
      apply (rule TypHeap.heap_list_update_disjoint_same)
      using hd by simp
    show
      "case r of
         (start, stop) \<Rightarrow>
           region_actually_is_bytes' start (unat (stop + 1 - start))
             (hrs_htd (hrs_mem_update (heap_update q v) hrs)) \<and>
           heap_list (hrs_mem (hrs_mem_update (heap_update q v) hrs))
             (unat (stop + 1 - start)) start =
           replicate (unat (stop + 1 - start)) 0"
      using hold hm
      by (simp add: rr hhrs HeapRawState.hrs_mem_update_def
          HeapRawState.hrs_htd_def HeapRawState.hrs_mem_def)
  qed
qed

lemma vero_aux_c0057_cvariable_array_overwrite:
  assumes hr:
    "cvariable_array_map_relation am (\<lambda>_. n) ptr htd"
    and ha: "am p = Some a"
  shows
    "cvariable_array_map_relation
       (am(p := Some a')) (\<lambda>_. n) ptr htd"
  using hr ha
  unfolding cvariable_array_map_relation_def
  by auto

lemma vero_aux_c0057_cpspace_relation_update:
  fixes d :: domain
  assumes hr: "cpspace_relation hp bh hrs"
    and hk: "hp p = Some (KOTCB tcb)"
    and hcl:
      "clift hrs (tcb_ptr_to_ctcb_ptr p) = Some ctcb"
    and hrel: "ctcb_relation tcb ctcb"
  defines
    "hp' \<equiv>
       hp(p := Some
         (KOTCB (tcbDomain_update (\<lambda>_. d) tcb)))"
    and
    "hrs' \<equiv>
       hrs_mem_update
        (heap_update
          (Ptr &(tcb_ptr_to_ctcb_ptr p\<rightarrow>[''tcbDomain_C'']) :: word32 ptr)
          (ucast d)) hrs"
  shows "cpspace_relation hp' bh hrs'"
proof -
  have hctes: "map_to_ctes hp' = map_to_ctes hp"
    unfolding hp'_def
    by (rule
      vero_aux_c0057_map_to_ctes_domain_update_raw
       [where hp=hp and p=p and tcb=tcb and f="\<lambda>_. d", OF hk])
  have heps: "map_to_eps hp' = map_to_eps hp"
    unfolding hp'_def
    apply (rule ext)
    using hk
    apply (simp add: map_comp_def ObjectInstances_H.projectKO_opt_ep)
    done
  have hntfns: "map_to_ntfns hp' = map_to_ntfns hp"
    unfolding hp'_def
    apply (rule ext)
    using hk
    apply (simp add: map_comp_def ObjectInstances_H.projectKO_opt_ntfn)
    done
  have hpdes: "map_to_pdes hp' = map_to_pdes hp"
    unfolding hp'_def
    apply (rule ext)
    using hk
    apply (simp add: map_comp_def ArchObjInsts_H.projectKO_opt_pde)
    done
  have hptes: "map_to_ptes hp' = map_to_ptes hp"
    unfolding hp'_def
    apply (rule ext)
    using hk
    apply (simp add: map_comp_def ArchObjInsts_H.projectKO_opt_pte)
    done
  have hasidpools: "map_to_asidpools hp' = map_to_asidpools hp"
    unfolding hp'_def
    apply (rule ext)
    using hk
    apply (simp add: map_comp_def ArchObjInsts_H.projectKO_opt_asidpool)
    done
  have husers: "map_to_user_data hp' = map_to_user_data hp"
    unfolding hp'_def
    apply (rule ext)
    using hk
    apply (simp add: map_comp_def ObjectInstances_H.projectKO_opt_user_data)
    done
  have hdevices:
    "map_to_user_data_device hp' = map_to_user_data_device hp"
    unfolding hp'_def
    apply (rule ext)
    using hk
    apply (simp add: map_comp_def
        ObjectInstances_H.projectKO_opt_user_data_device)
    done
  have hmaps:
    "map_to_eps hp' = map_to_eps hp \<and>
     map_to_ntfns hp' = map_to_ntfns hp \<and>
     map_to_pdes hp' = map_to_pdes hp \<and>
     map_to_ptes hp' = map_to_ptes hp \<and>
     map_to_asidpools hp' = map_to_asidpools hp \<and>
     map_to_user_data hp' = map_to_user_data hp \<and>
     map_to_user_data_device hp' = map_to_user_data_device hp"
    using heps hntfns hpdes hptes hasidpools husers hdevices by blast
  have hnrel:
    "ctcb_relation (tcbDomain_update (\<lambda>_. d) tcb)
       (tcbDomain_C_update (\<lambda>_. ucast d) ctcb)"
    by (rule vero_aux_c0057_ctcb_relation_domain_update[OF hrel])
  have htcb:
    "cpspace_tcb_relation hp' hrs'"
    unfolding hp'_def hrs'_def
    apply (rule
      vero_aux_c0057_cpspace_tcb_relation_update
       [where hp=hp and hrs=hrs and p=p and tcb=tcb and ctcb=ctcb and d=d,
        OF _ hk hcl hnrel])
    using hr by (simp add: cpspace_relation_def)
  have hcltcb:
    "(clift hrs' :: tcb_C typ_heap) =
       (clift hrs)
        (tcb_ptr_to_ctcb_ptr p :=
          Some (tcbDomain_C_update (\<lambda>_. ucast d) ctcb))"
    unfolding hrs'_def
    using vero_aux_c0057_clift_domain_update[OF hcl] by simp
  have hother:
    "(clift hrs' :: cte_C typ_heap) = clift hrs \<and>
     (clift hrs' :: endpoint_C typ_heap) = clift hrs \<and>
     (clift hrs' :: notification_C typ_heap) = clift hrs \<and>
     (clift hrs' :: pde_C typ_heap) = clift hrs \<and>
     (clift hrs' :: pte_C typ_heap) = clift hrs \<and>
     (clift hrs' :: asid_pool_C typ_heap) = clift hrs \<and>
     (clift hrs' :: user_data_C typ_heap) = clift hrs \<and>
     (clift hrs' :: user_data_device_C typ_heap) = clift hrs"
    unfolding hrs'_def
    by (rule vero_aux_c0057_other_clifts[OF hcl])
  have hep:
    "cmap_relation (map_to_eps hp) (clift hrs) ep_Ptr
       (cendpoint_relation (clift hrs))"
    using hr by (simp add: cpspace_relation_def)
  have hepup:
    "cmap_relation (map_to_eps hp) (clift hrs) ep_Ptr
       (cendpoint_relation
         ((clift hrs)
          (tcb_ptr_to_ctcb_ptr p :=
            Some (tcbDomain_C_update (\<lambda>_. ucast d) ctcb))))"
    by (rule vero_aux_c0057_cmap_endpoint_relation_update[OF hep hcl])
  have hntfn:
    "cmap_relation (map_to_ntfns hp) (clift hrs) ntfn_Ptr
       (cnotification_relation (clift hrs))"
    using hr by (simp add: cpspace_relation_def)
  have hntfnup:
    "cmap_relation (map_to_ntfns hp) (clift hrs) ntfn_Ptr
       (cnotification_relation
         ((clift hrs)
          (tcb_ptr_to_ctcb_ptr p :=
            Some (tcbDomain_C_update (\<lambda>_. ucast d) ctcb))))"
    by (rule vero_aux_c0057_cmap_notification_relation_update[OF hntfn hcl])
  have hheapuser:
    "heap_to_user_data hp' bh = heap_to_user_data hp bh"
    unfolding hp'_def heap_to_user_data_def
    apply (rule ext)
    using hk
    apply (simp add: map_comp_def
        ObjectInstances_H.projectKO_opt_user_data)
    done
  have hheapdevice:
    "heap_to_device_data hp' bh = heap_to_device_data hp bh"
    unfolding hp'_def heap_to_device_data_def
    apply (rule ext)
    using hk
    apply (simp add: map_comp_def
        ObjectInstances_H.projectKO_opt_user_data_device)
    done
  have husercl:
    "(clift hrs' :: user_data_C typ_heap) = clift hrs"
    using hother by blast
  have hdevicecl:
    "(clift hrs' :: user_data_device_C typ_heap) = clift hrs"
    using hother by blast
  have hhtd: "hrs_htd hrs' = hrs_htd hrs"
    unfolding hrs'_def by simp
  have huserold:
    "cpspace_user_data_relation hp bh hrs"
    using hr unfolding cpspace_relation_def by blast
  have hdeviceold:
    "cpspace_device_data_relation hp bh hrs"
    using hr unfolding cpspace_relation_def by blast
  have hpdeold:
    "cpspace_pde_array_relation hp hrs"
    using hr unfolding cpspace_relation_def by blast
  have hpteold:
    "cpspace_pte_array_relation hp hrs"
    using hr unfolding cpspace_relation_def by blast
  have huserrel:
    "cpspace_user_data_relation hp' bh hrs'"
    apply (subst hheapuser)
    apply (subst husercl)
    apply (rule huserold)
    done
  have hdevicerel:
    "cpspace_device_data_relation hp' bh hrs'"
    apply (subst hheapdevice)
    apply (subst hdevicecl)
    apply (rule hdeviceold)
    done
  have hpdearray:
    "cpspace_pde_array_relation hp' hrs'"
    apply (subst hpdes)
    apply (subst hhtd)
    apply (rule hpdeold)
    done
  have hptearray:
    "cpspace_pte_array_relation hp' hrs'"
    apply (subst hptes)
    apply (subst hhtd)
    apply (rule hpteold)
    done
  have hcteold:
    "cmap_relation (map_to_ctes hp) (clift hrs) cte_Ptr
       ccte_relation"
    using hr unfolding cpspace_relation_def by blast
  have hpdeoldmap:
    "cmap_relation (map_to_pdes hp) (clift hrs) pde_Ptr
       cpde_relation"
    using hr unfolding cpspace_relation_def by blast
  have hpteoldmap:
    "cmap_relation (map_to_ptes hp) (clift hrs) pte_Ptr
       cpte_relation"
    using hr unfolding cpspace_relation_def by blast
  have hasidoldmap:
    "cmap_relation (map_to_asidpools hp) (clift hrs) ap_Ptr
       casid_pool_relation"
    using hr unfolding cpspace_relation_def by blast
  have hctecl: "(clift hrs' :: cte_C typ_heap) = clift hrs"
    using hother by blast
  have hepcl: "(clift hrs' :: endpoint_C typ_heap) = clift hrs"
    using hother by blast
  have hntfncl: "(clift hrs' :: notification_C typ_heap) = clift hrs"
    using hother by blast
  have hpdecl: "(clift hrs' :: pde_C typ_heap) = clift hrs"
    using hother by blast
  have hptecl: "(clift hrs' :: pte_C typ_heap) = clift hrs"
    using hother by blast
  have hasidcl: "(clift hrs' :: asid_pool_C typ_heap) = clift hrs"
    using hother by blast
  have hcterel:
    "cmap_relation (map_to_ctes hp') (clift hrs') cte_Ptr
       ccte_relation"
    apply (subst hctes)
    apply (subst hctecl)
    apply (rule hcteold)
    done
  have heprel:
    "cmap_relation (map_to_eps hp') (clift hrs') ep_Ptr
       (cendpoint_relation (clift hrs'))"
    apply (subst heps)
    apply (subst hepcl)
    apply (subst hcltcb)
    apply (rule hepup)
    done
  have hntfnrel:
    "cmap_relation (map_to_ntfns hp') (clift hrs') ntfn_Ptr
       (cnotification_relation (clift hrs'))"
    apply (subst hntfns)
    apply (subst hntfncl)
    apply (subst hcltcb)
    apply (rule hntfnup)
    done
  have hpderel:
    "cmap_relation (map_to_pdes hp') (clift hrs') pde_Ptr
       cpde_relation"
    apply (subst hpdes)
    apply (subst hpdecl)
    apply (rule hpdeoldmap)
    done
  have hpterel:
    "cmap_relation (map_to_ptes hp') (clift hrs') pte_Ptr
       cpte_relation"
    apply (subst hptes)
    apply (subst hptecl)
    apply (rule hpteoldmap)
    done
  have hasidrel:
    "cmap_relation (map_to_asidpools hp') (clift hrs') ap_Ptr
       casid_pool_relation"
    apply (subst hasidpools)
    apply (subst hasidcl)
    apply (rule hasidoldmap)
    done
  show ?thesis
    unfolding cpspace_relation_def
    using hcterel htcb heprel hntfnrel hpderel hpterel hasidrel
      huserrel hdevicerel hpdearray hptearray
    by blast
qed

lemma vero_aux_c0057_rf_sr_update:
  fixes d :: domain
  assumes hrf: "(s, c) \<in> rf_sr"
    and hk: "ksPSpace s p = Some (KOTCB tcb)"
  shows
    "(ksPSpace_update
       (\<lambda>ps. ps(p \<mapsto>
          KOTCB (tcbDomain_update (\<lambda>_. d) tcb))) s,
      globals_update
       (t_hrs_'_update
        (hrs_mem_update
         (heap_update
          (Ptr &(tcb_ptr_to_ctcb_ptr p\<rightarrow>[''tcbDomain_C'']) :: word32 ptr)
          (ucast d)))) c) \<in> rf_sr"
proof -
  let ?hrs = "t_hrs_' (globals c)"
  let ?q =
    "(Ptr &(tcb_ptr_to_ctcb_ptr p\<rightarrow>[''tcbDomain_C'']) :: word32 ptr)"
  let ?hrs' = "hrs_mem_update (heap_update ?q (ucast d)) ?hrs"
  have hold:
    "state_rel.cstate_relation symbol_table domain armKSKernelVSpace_C
       s (globals c)"
    using hrf by (simp add: rf_sr_def)
  have hcps:
    "cpspace_relation (ksPSpace s)
       (underlying_memory (ksMachineState s)) ?hrs"
    using hold
    unfolding state_rel.cstate_relation_def Let_def
    by (rule conjunct1)
  have htcbrel:
    "cpspace_tcb_relation (ksPSpace s) ?hrs"
    using hcps by (simp add: cpspace_relation_def)
  have hcm:
    "cmap_relation (map_to_tcbs (ksPSpace s)) (clift ?hrs)
       tcb_ptr_to_ctcb_ptr ctcb_relation"
    by (rule iffD1[OF vero_aux_c0057_cpspace_tcb_relation_eq htcbrel])
  have hmap: "map_to_tcbs (ksPSpace s) p = Some tcb"
    using hk by (simp add: map_comp_def)
  have hpdom: "p \<in> dom (map_to_tcbs (ksPSpace s))"
    using hmap by (auto simp: dom_def)
  have hdom:
    "tcb_ptr_to_ctcb_ptr ` dom (map_to_tcbs (ksPSpace s)) =
       dom (clift ?hrs)"
    using hcm unfolding cmap_relation_def by (rule conjunct1)
  have hcdom: "tcb_ptr_to_ctcb_ptr p \<in> dom (clift ?hrs)"
    using hpdom hdom by blast
  obtain ctcb where hcl:
    "clift ?hrs (tcb_ptr_to_ctcb_ptr p) = Some ctcb"
    using hcdom by (auto simp: dom_def)
  have hrel: "ctcb_relation tcb ctcb"
  proof -
    have hpoints:
      "\<forall>x\<in>dom (map_to_tcbs (ksPSpace s)).
         ctcb_relation
          (the (map_to_tcbs (ksPSpace s) x))
          (the (clift ?hrs (tcb_ptr_to_ctcb_ptr x)))"
      using hcm unfolding cmap_relation_def by (rule conjunct2)
    have hpoint:
      "ctcb_relation
        (the (map_to_tcbs (ksPSpace s) p))
        (the (clift ?hrs (tcb_ptr_to_ctcb_ptr p)))"
      using hpoints hpdom by blast
    show ?thesis
      using hpoint hmap hcl by simp
  qed
  have hcps':
    "cpspace_relation
       ((ksPSpace s)
        (p := Some (KOTCB (tcbDomain_update (\<lambda>_. d) tcb))))
       (underlying_memory (ksMachineState s))
       ?hrs'"
    by (rule vero_aux_c0057_cpspace_relation_update[OF hcps hk hcl hrel])
  have htcbvalid:
    "hrs_htd ?hrs \<Turnstile>\<^sub>t tcb_ptr_to_ctcb_ptr p"
    using hcl by (rule TypHeapLib.h_t_valid_clift)
  have hfield: "hrs_htd ?hrs \<Turnstile>\<^sub>t ?q"
    apply (rule TypHeapLib.h_t_valid_field[OF htcbvalid])
     apply (rule Kernel_C.tcb_C_tcbDomain_C_fl_ti)
    apply simp
    done
  have hzero:
    "zero_ranges_are_zero (gsUntypedZeroRanges s) ?hrs"
    using hold
    unfolding state_rel.cstate_relation_def Let_def
    by blast
  have hzero':
    "zero_ranges_are_zero (gsUntypedZeroRanges s) ?hrs'"
    by (rule vero_aux_c0057_zero_ranges_update[OF hzero hfield])
  have harch:
    "state_rel.carch_state_relation symbol_table armKSKernelVSpace_C
       (ksArchState s) (globals c)"
    using hold
    unfolding state_rel.cstate_relation_def Let_def
    by blast
  have hpdeclift:
    "(clift ?hrs' :: pde_C typ_heap) = clift ?hrs"
    using vero_aux_c0057_other_clifts[OF hcl, where v="ucast d"]
    by simp
  have harch':
    "state_rel.carch_state_relation symbol_table armKSKernelVSpace_C
       (ksArchState s)
       (t_hrs_'_update (hrs_mem_update (heap_update ?q (ucast d)))
         (globals c))"
    using harch hpdeclift
    unfolding state_rel.carch_state_relation_def
    by simp
  have hmachine:
    "cmachine_state_relation (ksMachineState s) (globals c)"
    using hold
    unfolding state_rel.cstate_relation_def Let_def
    by blast
  have hmachine':
    "cmachine_state_relation (ksMachineState s)
       (t_hrs_'_update (hrs_mem_update (heap_update ?q (ucast d)))
         (globals c))"
    using hmachine
    unfolding cmachine_state_relation_def
    by simp
  have htcbarr:
    "cvariable_array_map_relation (map_to_tcbs (ksPSpace s))
       (\<lambda>_. 5) cte_Ptr (hrs_htd ?hrs)"
    using hold
    unfolding state_rel.cstate_relation_def Let_def
    by blast
  have htcbarr':
    "cvariable_array_map_relation
       (map_to_tcbs
         ((ksPSpace s)
          (p := Some (KOTCB (tcbDomain_update (\<lambda>_. d) tcb)))))
       (\<lambda>_. 5) cte_Ptr (hrs_htd ?hrs)"
    apply (subst vero_aux_c0057_map_to_tcbs_update)
    apply (rule vero_aux_c0057_cvariable_array_overwrite[OF htcbarr hmap])
    done
  show ?thesis
    using hold hcps' hzero' harch' hmachine' htcbarr'
    unfolding rf_sr_def state_rel.cstate_relation_def Let_def
    by simp
qed

(* VERO END c0057 prove_aux *)

vero_prove "c0057" c0057_prove [corres]:
  "ccorres dc xfdc (tcb_at' thread) {s. thread' s = tcb_ptr_to_ctcb_ptr thread \<and> d' s = ucast d} hs
           (threadSet (tcbDomain_update (\<lambda>_. d)) thread)
           (Basic (\<lambda>s. globals_update (t_hrs_'_update (hrs_mem_update (heap_update (Ptr &(thread' s\<rightarrow>[''tcbDomain_C''])::word32 ptr) (d' s)))) s))"

(* VERO START c0057 prove *)

apply (rule ccorres_from_vcg)
apply (intro allI)
apply (rule HoarePartial.Basic)
apply clarsimp
subgoal premises pre for sigma x
proof -
  have ht: "tcb_at' thread sigma" using pre by simp
  obtain tcb where hk: "ksPSpace sigma thread = Some (KOTCB tcb)"
    and heval:
      "threadSet (tcbDomain_update (\<lambda>_. d)) thread sigma =
        ({((), ksPSpace_update
                 (\<lambda>ps. ps(thread \<mapsto>
                    KOTCB (tcbDomain_update (\<lambda>_. d) tcb))) sigma)}, False)"
    using vero_aux_c0057_threadSet_eval[OF ht] by blast
  have hrf: "(sigma, x) \<in> rf_sr"
    using pre by simp
  have hthread: "thread' x = tcb_ptr_to_ctcb_ptr thread"
    using pre by simp
  have hd: "d' x = ucast d"
    using pre by simp
  have hup:
    "(ksPSpace_update
       (\<lambda>ps. ps(thread \<mapsto>
          KOTCB (tcbDomain_update (\<lambda>_. d) tcb))) sigma,
      globals_update
       (t_hrs_'_update
        (hrs_mem_update
         (heap_update
          (Ptr &(tcb_ptr_to_ctcb_ptr thread\<rightarrow>[''tcbDomain_C'']) :: word32 ptr)
          (ucast d)))) x) \<in> rf_sr"
    by (rule vero_aux_c0057_rf_sr_update[OF hrf hk])
  show ?thesis
    apply (rule bexI[where x=
      "((), ksPSpace_update
         (\<lambda>ps. ps(thread \<mapsto>
            KOTCB (tcbDomain_update (\<lambda>_. d) tcb))) sigma)"])
     apply (simp only: case_prod_conv hthread hd)
     apply (rule hup)
    using heval
    by simp
qed
done

(* VERO END c0057 prove *)

(* VERO START c0057 disprove_aux *)



(* VERO END c0057 disprove_aux *)

vero_disprove "c0057" c0057_disprove [corres]:
  "ccorres dc xfdc (tcb_at' thread) {s. thread' s = tcb_ptr_to_ctcb_ptr thread \<and> d' s = ucast d} hs
           (threadSet (tcbDomain_update (\<lambda>_. d)) thread)
           (Basic (\<lambda>s. globals_update (t_hrs_'_update (hrs_mem_update (heap_update (Ptr &(thread' s\<rightarrow>[''tcbDomain_C''])::word32 ptr) (d' s)))) s))"

(* VERO START c0057 disprove *)

oops

(* VERO END c0057 disprove *)

end

context kernel_m
begin

(* VERO START c0088 prove_aux *)

lemma vero_aux_c0088_if_not:
  "(if \<not> P then x else y) = (if P then y else x)"
  by (cases P; simp)

lemma vero_aux_c0088_nat_word_guard:
  assumes "n < 120"
  shows "(\<not> n < 4) = ((4 :: word32) \<le> of_nat n)"
proof -
  have bound: "n < 2 ^ LENGTH(32)"
    using assms by simp
  show ?thesis
    using bound by (simp add: Word.word_le_nat_alt Word.unat_of_nat not_less)
qed

lemma vero_aux_c0088_rf_sr_return_locals:
  assumes "(s, c) \<in> rf_sr"
  shows "(s, c\<lparr>ret__unsigned_' := r, global_exn_var_' := e\<rparr>) \<in> rf_sr"
  using assms
  unfolding rf_sr_def state_rel.cstate_relation_def Let_def
  by simp

lemma vero_aux_c0088_msgRegister_index:
  assumes "n < length TCB_H.msgRegisters"
  shows "kernel_all_global_addresses.msgRegisters.[unat (of_nat n :: word32)] =
         register_from_H (TCB_H.msgRegisters ! n)"
proof -
  have "n = 0 \<or> n = 1 \<or> n = 2 \<or> n = 3"
    using assms
    by (simp add: TcbAcc_R.msgRegisters_unfold ARM.msgRegisters_def; presburger)
  then show ?thesis
    by (elim disjE;
        simp add: TcbAcc_R.msgRegisters_unfold ARM.msgRegisters_def
                  kernel_all_global_addresses.msgRegisters_def fupdate_def)
qed

lemma vero_aux_c0088_suc_return:
  assumes "n < length TCB_H.msgRegisters"
  shows "Suc n =
         unat (((of_nat n :: word32) + 1) && mask msgLengthBits)"
proof -
  have "n = 0 \<or> n = 1 \<or> n = 2 \<or> n = 3"
    using assms
    by (simp add: TcbAcc_R.msgRegisters_unfold ARM.msgRegisters_def; presburger)
  then show ?thesis
    by (elim disjE; simp add: msgLengthBits_def mask_def)
qed

lemma vero_aux_c0088_suc_return_bounded:
  assumes "n < 120"
  shows "Suc n =
         unat (((of_nat n :: word32) + 1) && mask msgLengthBits)"
proof -
  have hunat:
    "unat ((1 :: word32) + of_nat n) = Suc n"
  proof -
    have hn: "n < 2 ^ LENGTH(32)"
      using assms by simp
    have hnat: "unat (of_nat n :: word32) = n"
      by (rule unat_of_nat_eq[OF hn])
    have hsum:
      "unat (1 :: word32) + unat (of_nat n :: word32) < 2 ^ LENGTH(32)"
      using assms hnat by simp
    have hadd:
      "unat ((1 :: word32) + of_nat n) =
       unat (1 :: word32) + unat (of_nat n :: word32)"
      by (rule unat_add_lem'[OF hsum])
    show ?thesis
      using hadd hnat by simp
  qed
  have hle: "(of_nat (Suc n) :: word32) \<le> mask 7"
    using assms hunat
    by (simp add: mask_def Word.word_le_nat_alt)
  have hmask:
    "(of_nat (Suc n) :: word32) && mask 7 = of_nat (Suc n)"
    using hle by (simp add: and_mask_eq_iff_le_mask)
  show ?thesis
    using hunat hmask
    by (simp add: msgLengthBits_def add.commute)
qed

lemma vero_aux_c0088_setRegister_args_ccorres:
  assumes off: "offset < length TCB_H.msgRegisters"
  shows
   "ccorres_underlying rf_sr \<Gamma>
      (\<lambda>_ r'. r' = of_nat offset) offset_' dc ret__unsigned_' \<top>
      (UNIV \<inter> {s. receiver_' s = tcb_ptr_to_ctcb_ptr thread}
            \<inter> {s. offset_' s = of_nat offset}
            \<inter> {s. reg_' s = val}) [SKIP]
      (asUser thread (setRegister (TCB_H.msgRegisters ! offset) val))
      (CALL setRegister(\<acute>receiver,
         kernel_all_global_addresses.msgRegisters.[unat \<acute>offset],
         \<acute>reg))"
  apply (rule ccorres_from_vcg_nofail)
  apply (intro allI)
  apply vcg
  using off
  apply (clarsimp simp: vero_aux_c0088_msgRegister_index)
  done

lemma vero_aux_c0088_offset_return_ccorres:
  assumes off: "offset < length TCB_H.msgRegisters"
  shows
   "ccorres (\<lambda>r r'. r = unat (r' && mask msgLengthBits))
      ret__unsigned_' \<top> {s. offset_' s = of_nat offset} [SKIP]
      (return (Suc offset))
      (\<acute>ret__unsigned :== \<acute>offset + 1;;
       (\<acute>global_exn_var :== Return;; THROW))"
  apply (rule ccorres_from_vcg_throws)
  apply (intro allI)
  apply (rule HoarePartial.Seq)
   prefer 2
   apply (rule HoarePartial.Seq)
    apply (rule HoarePartialDef.hoarep.Basic)
   apply (rule HoarePartialDef.hoarep.Throw)
  apply (rule HoarePartial.Basic)
  using off
  apply (clarsimp simp: return_def vero_aux_c0088_suc_return)
  apply (erule vero_aux_c0088_rf_sr_return_locals)
  done

lemma vero_aux_c0088_offset_return_bounded_ccorres:
   "ccorres (\<lambda>r r'. r = unat (r' && mask msgLengthBits))
      ret__unsigned_' (\<lambda>_. offset < 120)
      {s. offset_' s = of_nat offset} [SKIP]
      (return (Suc offset))
      (\<acute>ret__unsigned :== \<acute>offset + 1;;
       (\<acute>global_exn_var :== Return;; THROW))"
  apply (rule ccorres_from_vcg_throws)
  apply (intro allI)
  apply (rule HoarePartial.Seq)
   prefer 2
   apply (rule HoarePartial.Seq)
    apply (rule HoarePartialDef.hoarep.Basic)
   apply (rule HoarePartialDef.hoarep.Throw)
  apply (rule HoarePartial.Basic)
  apply (clarsimp simp: return_def vero_aux_c0088_suc_return_bounded)
  apply (erule vero_aux_c0088_rf_sr_return_locals)
  done

lemma vero_aux_c0088_store_buffer_ccorres:
  "ccorres_underlying rf_sr \<Gamma> dc xfdc dc ret__unsigned_'
      (valid_pspace' and valid_ipc_buffer_ptr' a and
       (\<lambda>_. offset < 120))
      (UNIV \<inter> {s. offset_' s = of_nat offset}
            \<inter> {s. reg_' s = v}
            \<inter> {s. receiveIPCBuffer_' s = option_to_ptr (Some a)}) [SKIP]
      (storeWordUser (a + (1 + of_nat offset) * word_size) v)
      (Guard MemorySafety
        \<lbrace>ptr_add_assertion' \<acute>receiveIPCBuffer
           (uint (\<acute>offset + 1)) False (hrs_htd \<acute>t_hrs)\<rbrace>
        (Guard C_Guard
          {s. s \<Turnstile>\<^sub>c (receiveIPCBuffer_' s +\<^sub>p
                     uint (offset_' s + 1))}
          (Guard MemorySafety
            \<lbrace>ptr_add_assertion' \<acute>receiveIPCBuffer
               (uint (\<acute>offset + 1)) True (hrs_htd \<acute>t_hrs)\<rbrace>
            (Basic (\<lambda>s. globals_update
              (t_hrs_'_update
                (hrs_mem_update
                  (heap_update
                    (receiveIPCBuffer_' s +\<^sub>p uint (offset_' s + 1))
                    (reg_' s)))) s)))))"
  apply (rule ccorres_from_vcg_nofail2)
  apply (intro allI)
  apply (vcg; clarsimp simp: storeWordUser_def stateAssert_def
                              doMachineOp_def storeWord_def bind_def'
                              get_def simpler_gets_def simpler_modify_def
                              select_f_def assert_def return_def fail_def
                              option_to_ptr_def option_to_0_def word_size_def
                              CTypesDefs.ptr_add_def add.commute)
  done

(* VERO END c0088 prove_aux *)

vero_prove "c0088" c0088_prove:
shows
  "ccorres (\<lambda>r r'. r = unat (r' && mask msgLengthBits)) ret__unsigned_'
     (valid_pspace' and  case_option \<top> valid_ipc_buffer_ptr' buf
           and (\<lambda>s. offset < msgMaxLength))
     (UNIV \<inter> {s. offset_' s = of_nat offset} \<inter> {s. reg_' s = v}
             \<inter> {s. receiver_' s = tcb_ptr_to_ctcb_ptr thread}
             \<inter> {s. receiveIPCBuffer_' s = option_to_ptr buf}) []
     (setMR thread buf offset v) (Call setMR_'proc)"

(* VERO START c0088 prove *)

apply (rule ccorres_Call)
 apply (simp add: setMR_impl setMR_body_def)
apply (rule ccorres_split_throws[where R'=UNIV, simplified])
 defer
 apply vcg
 apply simp
apply (cases buf)
 apply hypsubst
 apply (simp only: option.simps setMR_def)
 apply (subst vero_aux_c0088_if_not[symmetric])
 apply simp
 apply (intro conjI impI)
  apply (rule ccorres_guard_imp2
      [where Q="valid_pspace' and (\<lambda>s. offset < msgMaxLength)"
       and Q'="{s. offset_' s = of_nat offset} \<inter> {s. reg_' s = v}
                   \<inter> {s. receiver_' s = tcb_ptr_to_ctcb_ptr thread}
                   \<inter> {s. receiveIPCBuffer_' s = option_to_ptr None}
                   \<inter> - {s. receiveIPCBuffer_' s \<noteq> NULL}
                   \<inter> {s. scast n_msgRegisters \<le> offset_' s}"])
    apply (rule ccorres_cond_true
      [where R'="{s. offset_' s = of_nat offset} \<inter> {s. reg_' s = v}
                   \<inter> {s. receiver_' s = tcb_ptr_to_ctcb_ptr thread}
                   \<inter> {s. receiveIPCBuffer_' s = option_to_ptr None}
                   \<inter> - {s. receiveIPCBuffer_' s \<noteq> NULL}"])
    apply (rule ccorres_cond_false
      [where R'="{s. offset_' s = of_nat offset} \<inter> {s. reg_' s = v}
                   \<inter> {s. receiver_' s = tcb_ptr_to_ctcb_ptr thread}
                   \<inter> {s. receiveIPCBuffer_' s = option_to_ptr None}"])
    apply (rule ccorres_from_vcg_throws)
    apply (intro allI)
    unfolding return_C_def
    apply (rule HoarePartial.Seq)
     prefer 2
     apply (rule HoarePartial.Seq)
      apply (rule HoarePartialDef.hoarep.Basic)
     apply (rule HoarePartialDef.hoarep.Throw)
    apply (rule HoarePartial.Basic)
    apply (clarsimp simp: TcbAcc_R.msgRegisters_unfold ARM.msgRegisters_def
                          n_msgRegisters_def msgLengthBits_def mask_def return_def)
    apply (erule vero_aux_c0088_rf_sr_return_locals)
  subgoal premises pre for s s'
  proof -
    have hbound: "offset < 120"
      using pre by (simp add: Types_H.msgMaxLength_def)
    have hguard: "(4 :: word32) \<le> of_nat offset"
      using vero_aux_c0088_nat_word_guard[OF hbound] pre
      by (simp add: TcbAcc_R.msgRegisters_unfold ARM.msgRegisters_def)
    show ?thesis
      using pre hguard
      by (simp add: TcbAcc_R.msgRegisters_unfold ARM.msgRegisters_def
                    n_msgRegisters_def Types_H.msgMaxLength_def msgLengthBits_def
                    option_to_ptr_def option_to_0_def)
  qed
  apply (rule ccorres_guard_imp2
      [where Q="\<top>"
       and Q'="(UNIV \<inter> {s. receiver_' s = tcb_ptr_to_ctcb_ptr thread}
                         \<inter> {s. offset_' s = of_nat offset}
                         \<inter> {s. reg_' s = v})
                   \<inter> {s. offset_' s < 4}
                   \<inter> - {s. scast n_msgRegisters \<le> offset_' s}"])
    apply (rule ccorres_cond_false
      [where R'="(UNIV \<inter> {s. receiver_' s = tcb_ptr_to_ctcb_ptr thread}
                         \<inter> {s. offset_' s = of_nat offset}
                         \<inter> {s. reg_' s = v})
                   \<inter> {s. offset_' s < 4}"])
    apply (rule ccorres_split_nothrow'
      [where Q="\<lambda>_. \<top>" and Q'="UNIV"
       and R="\<top>" and R'="UNIV", simplified])
       apply (rule ccorres_Guard)
       apply (rule vero_aux_c0088_setRegister_args_ccorres)
       apply assumption
      apply (rule vero_aux_c0088_offset_return_ccorres)
      apply assumption
     apply wp
    apply vcg
  subgoal premises pre
  proof -
    have hbound: "offset < 120"
      using pre
      by (simp add: TcbAcc_R.msgRegisters_unfold ARM.msgRegisters_def)
    have hguard: "\<not> ((4 :: word32) \<le> of_nat offset)"
      using vero_aux_c0088_nat_word_guard[OF hbound] pre
      by (simp add: TcbAcc_R.msgRegisters_unfold ARM.msgRegisters_def)
    show ?thesis
      using pre hguard
      by (simp add: TcbAcc_R.msgRegisters_unfold ARM.msgRegisters_def
                    n_msgRegisters_def Types_H.msgMaxLength_def)
  qed
  subgoal premises pre for s s'
  proof -
    have hbound: "offset < 120"
      using pre
      by (simp add: TcbAcc_R.msgRegisters_unfold ARM.msgRegisters_def)
    have hguard: "\<not> ((4 :: word32) \<le> of_nat offset)"
      using vero_aux_c0088_nat_word_guard[OF hbound] pre
      by (simp add: TcbAcc_R.msgRegisters_unfold ARM.msgRegisters_def)
    show ?thesis
      using pre hguard
      by (simp add: TcbAcc_R.msgRegisters_unfold ARM.msgRegisters_def
                    n_msgRegisters_def Types_H.msgMaxLength_def)
  qed
 apply (simp only: option.simps setMR_def)
 apply (subst vero_aux_c0088_if_not[symmetric])
 apply simp
 apply (intro conjI impI)
  subgoal premises outer for a
  proof -
    show ?thesis
      apply (rule ccorres_guard_imp2
      [where Q="valid_pspace' and valid_ipc_buffer_ptr' a
                    and (\<lambda>_. offset < 120)"
       and Q'="{s. offset_' s = of_nat offset} \<inter> {s. reg_' s = v}
                   \<inter> {s. receiver_' s = tcb_ptr_to_ctcb_ptr thread}
                   \<inter> {s. receiveIPCBuffer_' s = option_to_ptr (Some a)}
                   \<inter> {s. receiveIPCBuffer_' s \<noteq> NULL}
                   \<inter> {s. scast n_msgRegisters \<le> offset_' s}"])
    apply (rule ccorres_cond_true
      [where R'="{s. offset_' s = of_nat offset} \<inter> {s. reg_' s = v}
                   \<inter> {s. receiver_' s = tcb_ptr_to_ctcb_ptr thread}
                   \<inter> {s. receiveIPCBuffer_' s = option_to_ptr (Some a)}
                   \<inter> {s. receiveIPCBuffer_' s \<noteq> NULL}"])
    apply (rule ccorres_cond_true
      [where R'="{s. offset_' s = of_nat offset} \<inter> {s. reg_' s = v}
                   \<inter> {s. receiver_' s = tcb_ptr_to_ctcb_ptr thread}
                   \<inter> {s. receiveIPCBuffer_' s = option_to_ptr (Some a)}"])
    apply (rule ccorres_split_nothrow'
      [where Q="\<lambda>_ _. offset < 120"
       and Q'="{s. offset_' s = of_nat offset}"
       and P="valid_pspace' and valid_ipc_buffer_ptr' a and
              (\<lambda>_. offset < 120)"
       and P'="{s. offset_' s = of_nat offset} \<inter> {s. reg_' s = v}
                 \<inter> {s. receiver_' s = tcb_ptr_to_ctcb_ptr thread}
                 \<inter> {s. receiveIPCBuffer_' s = option_to_ptr (Some a)}"
       and R="\<lambda>_. offset < 120"
       and R'="{s. offset_' s = of_nat offset} \<inter> {s. reg_' s = v}
                 \<inter> {s. receiver_' s = tcb_ptr_to_ctcb_ptr thread}
                 \<inter> {s. receiveIPCBuffer_' s = option_to_ptr (Some a)}"
       and F=UNIV and r'=dc and xf'=xfdc, simplified])
     subgoal premises pre
     proof -
       show ?thesis
         apply (rule ccorres_guard_imp2
           [where Q="valid_pspace' and valid_ipc_buffer_ptr' a and
                        (\<lambda>_. offset < 120)"
            and Q'="UNIV \<inter> {s. offset_' s = of_nat offset}
                      \<inter> {s. reg_' s = v}
                      \<inter> {s. receiveIPCBuffer_' s = option_to_ptr (Some a)}"])
          apply (rule vero_aux_c0088_store_buffer_ccorres)
         using pre
         apply (clarsimp simp: Types_H.msgMaxLength_def)
         done
     qed
     apply (rule vero_aux_c0088_offset_return_bounded_ccorres)
     apply (wp; simp)
    apply (rule HoarePartial.GuardStripSame)
     apply (rule HoarePartial.GuardStripSame)
      apply (rule HoarePartial.GuardStripSame)
       apply (rule HoarePartial.Basic)
       apply clarsimp
      apply simp
     apply simp
    apply simp
  subgoal premises pre for s s'
  proof -
    have hbound: "offset < 120"
      using pre by (simp add: Types_H.msgMaxLength_def)
    have hguard: "(4 :: word32) \<le> of_nat offset"
      using vero_aux_c0088_nat_word_guard[OF hbound] pre outer
      by (simp add: TcbAcc_R.msgRegisters_unfold ARM.msgRegisters_def)
    have hno0: "no_0_obj' s"
      using pre by (simp add: valid_pspace'_def)
    have htyp: "typ_at' UserDataT (a && ~~ mask pageBits) s"
      using pre by (simp add: valid_ipc_buffer_ptr'_def2)
    have ha0: "a \<noteq> 0"
    proof
      assume az: "a = 0"
      from hno0 htyp az show False
        unfolding no_0_obj'_def typ_at'_def ko_wp_at'_def
        by clarsimp
    qed
    show ?thesis
      using pre hbound hguard ha0
      by (simp add: TcbAcc_R.msgRegisters_unfold ARM.msgRegisters_def
                    n_msgRegisters_def Types_H.msgMaxLength_def
                    option_to_ptr_def option_to_0_def)
  qed
done

(* VERO END c0088 prove *)

(* VERO START c0088 disprove_aux *)



(* VERO END c0088 disprove_aux *)

vero_disprove "c0088" c0088_disprove:
shows
  "ccorres (\<lambda>r r'. r = unat (r' && mask msgLengthBits)) ret__unsigned_'
     (valid_pspace' and  case_option \<top> valid_ipc_buffer_ptr' buf
           and (\<lambda>s. offset < msgMaxLength))
     (UNIV \<inter> {s. offset_' s = of_nat offset} \<inter> {s. reg_' s = v}
             \<inter> {s. receiver_' s = tcb_ptr_to_ctcb_ptr thread}
             \<inter> {s. receiveIPCBuffer_' s = option_to_ptr buf}) []
     (setMR thread buf offset v) (Call setMR_'proc)"

(* VERO START c0088 disprove *)

oops

(* VERO END c0088 disprove *)

end

context kernel_m begin

(* VERO START c0099 prove_aux *)



(* VERO END c0099 prove_aux *)

vero_prove "c0099" c0099_prove [corres]:
  "ccorres (=) w_bufferPtr_' (tcb_at' tptr) UNIV hs
           (threadGet tcbIPCBuffer tptr)
           (Guard C_Guard \<lbrace>hrs_htd \<acute>t_hrs \<Turnstile>\<^sub>t tcb_ptr_to_ctcb_ptr tptr\<rbrace>
               (\<acute>w_bufferPtr :==
                  h_val (hrs_mem \<acute>t_hrs)
                   (Ptr &(tcb_ptr_to_ctcb_ptr tptr\<rightarrow>[''tcbIPCBuffer_C''])::word32 ptr)))"

(* VERO START c0099 prove *)

oops

(* VERO END c0099 prove *)

(* VERO START c0099 disprove_aux *)



(* VERO END c0099 disprove_aux *)

vero_disprove "c0099" c0099_disprove [corres]:
  "ccorres (=) w_bufferPtr_' (tcb_at' tptr) UNIV hs
           (threadGet tcbIPCBuffer tptr)
           (Guard C_Guard \<lbrace>hrs_htd \<acute>t_hrs \<Turnstile>\<^sub>t tcb_ptr_to_ctcb_ptr tptr\<rbrace>
               (\<acute>w_bufferPtr :==
                  h_val (hrs_mem \<acute>t_hrs)
                   (Ptr &(tcb_ptr_to_ctcb_ptr tptr\<rightarrow>[''tcbIPCBuffer_C''])::word32 ptr)))"

(* VERO START c0099 disprove *)

oops

(* VERO END c0099 disprove *)

(* VERO START c0123 prove_aux *)



(* VERO END c0123 prove_aux *)

vero_prove "c0123" c0123_prove:
  "ccorres dc xfdc (tcb_at' thread) \<lbrace> \<acute>tcb = tcb_ptr_to_ctcb_ptr thread \<rbrace> hs
     (updateRestartPC thread) (Call updateRestartPC_'proc)"

(* VERO START c0123 prove *)

oops

(* VERO END c0123 prove *)

(* VERO START c0123 disprove_aux *)



(* VERO END c0123 disprove_aux *)

vero_disprove "c0123" c0123_disprove:
  "ccorres dc xfdc (tcb_at' thread) \<lbrace> \<acute>tcb = tcb_ptr_to_ctcb_ptr thread \<rbrace> hs
     (updateRestartPC thread) (Call updateRestartPC_'proc)"

(* VERO START c0123 disprove *)

oops

(* VERO END c0123 disprove *)

(* VERO START c0134 prove_aux *)



(* VERO END c0134 prove_aux *)

vero_prove "c0134" c0134_prove:
  "ccorres (\<lambda>rv rv'. rv' = of_nat offset + 1) ret__unsigned_'
      (tcb_at' thread and K (TCB_H.msgRegisters ! offset = reg \<and> offset < length msgRegisters))
      (UNIV \<inter> \<lbrace>\<acute>reg = val\<rbrace>
            \<inter> \<lbrace>\<acute>offset = of_nat offset\<rbrace>
            \<inter> \<lbrace>\<acute>receiver = tcb_ptr_to_ctcb_ptr thread\<rbrace>) hs
    (asUser thread (setRegister reg val))
    (Call setMR_'proc)"

(* VERO START c0134 prove *)

oops

(* VERO END c0134 prove *)

(* VERO START c0134 disprove_aux *)



(* VERO END c0134 disprove_aux *)

vero_disprove "c0134" c0134_disprove:
  "ccorres (\<lambda>rv rv'. rv' = of_nat offset + 1) ret__unsigned_'
      (tcb_at' thread and K (TCB_H.msgRegisters ! offset = reg \<and> offset < length msgRegisters))
      (UNIV \<inter> \<lbrace>\<acute>reg = val\<rbrace>
            \<inter> \<lbrace>\<acute>offset = of_nat offset\<rbrace>
            \<inter> \<lbrace>\<acute>receiver = tcb_ptr_to_ctcb_ptr thread\<rbrace>) hs
    (asUser thread (setRegister reg val))
    (Call setMR_'proc)"

(* VERO START c0134 disprove *)

oops

(* VERO END c0134 disprove *)

end

context kernel_m
begin

(* VERO START c0140 prove_aux *)



(* VERO END c0140 prove_aux *)

vero_prove "c0140" c0140_prove[corres]:
   "ccorres dc xfdc
   (\<lambda>s. tcb_at' thread s \<and> valid_objs' s \<and> valid_tcb_state' st s
        \<and> (ksSchedulerAction s = SwitchToThread thread \<longrightarrow> runnable' st)
        \<and> sch_act_wf (ksSchedulerAction s) s \<and> pspace_aligned' s \<and> pspace_distinct' s)
   ({s'. (\<forall>cl fl. cthread_state_relation_lifted st (cl\<lparr>tsType_CL := ts_' s' && mask 4\<rparr>, fl))}
    \<inter> {s. tptr_' s = tcb_ptr_to_ctcb_ptr thread}) hs
   (setThreadState st thread) (Call setThreadState_'proc)"

(* VERO START c0140 prove *)

oops

(* VERO END c0140 prove *)

(* VERO START c0140 disprove_aux *)



(* VERO END c0140 disprove_aux *)

vero_disprove "c0140" c0140_disprove[corres]:
   "ccorres dc xfdc
   (\<lambda>s. tcb_at' thread s \<and> valid_objs' s \<and> valid_tcb_state' st s
        \<and> (ksSchedulerAction s = SwitchToThread thread \<longrightarrow> runnable' st)
        \<and> sch_act_wf (ksSchedulerAction s) s \<and> pspace_aligned' s \<and> pspace_distinct' s)
   ({s'. (\<forall>cl fl. cthread_state_relation_lifted st (cl\<lparr>tsType_CL := ts_' s' && mask 4\<rparr>, fl))}
    \<inter> {s. tptr_' s = tcb_ptr_to_ctcb_ptr thread}) hs
   (setThreadState st thread) (Call setThreadState_'proc)"

(* VERO START c0140 disprove *)

oops

(* VERO END c0140 disprove *)

(* VERO START c0150 prove_aux *)



(* VERO END c0150 prove_aux *)

vero_prove "c0150" c0150_prove:
  "ccorres dc xfdc (tcb_at' thread)
        {s. (\<forall>cl fl. cthread_state_relation_lifted st (cl\<lparr>tsType_CL := v32_' s && mask 4\<rparr>, fl)) \<and>
           thread_state_ptr_' s = Ptr &(tcb_ptr_to_ctcb_ptr thread\<rightarrow>[''tcbState_C''])} []
        (threadSet (tcbState_update (\<lambda>_. st)) thread)  (Call thread_state_ptr_set_tsType_'proc)"

(* VERO START c0150 prove *)

oops

(* VERO END c0150 prove *)

(* VERO START c0150 disprove_aux *)



(* VERO END c0150 disprove_aux *)

vero_disprove "c0150" c0150_disprove:
  "ccorres dc xfdc (tcb_at' thread)
        {s. (\<forall>cl fl. cthread_state_relation_lifted st (cl\<lparr>tsType_CL := v32_' s && mask 4\<rparr>, fl)) \<and>
           thread_state_ptr_' s = Ptr &(tcb_ptr_to_ctcb_ptr thread\<rightarrow>[''tcbState_C''])} []
        (threadSet (tcbState_update (\<lambda>_. st)) thread)  (Call thread_state_ptr_set_tsType_'proc)"

(* VERO START c0150 disprove *)

oops

(* VERO END c0150 disprove *)

(* VERO START c0156 prove_aux *)



(* VERO END c0156 prove_aux *)

vero_prove "c0156" c0156_prove [corres]:
  "ccorres (\<lambda>r r'. r = to_bool r') ret__unsigned_long_'
  (tcb_at' thread) (UNIV \<inter> {s. thread_' s = tcb_ptr_to_ctcb_ptr thread})  []
  (isRunnable thread) (Call isRunnable_'proc)"

(* VERO START c0156 prove *)

oops

(* VERO END c0156 prove *)

(* VERO START c0156 disprove_aux *)



(* VERO END c0156 disprove_aux *)

vero_disprove "c0156" c0156_disprove [corres]:
  "ccorres (\<lambda>r r'. r = to_bool r') ret__unsigned_long_'
  (tcb_at' thread) (UNIV \<inter> {s. thread_' s = tcb_ptr_to_ctcb_ptr thread})  []
  (isRunnable thread) (Call isRunnable_'proc)"

(* VERO START c0156 disprove *)

oops

(* VERO END c0156 disprove *)

(* VERO START c0192 prove_aux *)



(* VERO END c0192 prove_aux *)

vero_prove "c0192" c0192_prove [corres]:
  "ccorres (\<lambda>r r'. r = message_info_to_H r') ret__struct_seL4_MessageInfo_C_' \<top> {s. w_' s = w} []
           (return (messageInfoFromWord w)) (Call messageInfoFromWord_'proc)"

(* VERO START c0192 prove *)

oops

(* VERO END c0192 prove *)

(* VERO START c0192 disprove_aux *)



(* VERO END c0192 disprove_aux *)

vero_disprove "c0192" c0192_disprove [corres]:
  "ccorres (\<lambda>r r'. r = message_info_to_H r') ret__struct_seL4_MessageInfo_C_' \<top> {s. w_' s = w} []
           (return (messageInfoFromWord w)) (Call messageInfoFromWord_'proc)"

(* VERO START c0192 disprove *)

oops

(* VERO END c0192 disprove *)

end

context kernel_m begin

(* VERO START c0224 prove_aux *)



(* VERO END c0224 prove_aux *)

vero_prove "c0224" c0224_prove:
  "ccorres (\<lambda>rv rv'. rv = messageInfoFromWord rv') ret__unsigned_long_' \<top>
       (UNIV \<inter> {s. thread_' s = tcb_ptr_to_ctcb_ptr thread}
             \<inter> {s. reg_' s = register_from_H ARM_H.msgInfoRegister}) []
       (getMessageInfo thread) (Call getRegister_'proc)"

(* VERO START c0224 prove *)

oops

(* VERO END c0224 prove *)

(* VERO START c0224 disprove_aux *)



(* VERO END c0224 disprove_aux *)

vero_disprove "c0224" c0224_disprove:
  "ccorres (\<lambda>rv rv'. rv = messageInfoFromWord rv') ret__unsigned_long_' \<top>
       (UNIV \<inter> {s. thread_' s = tcb_ptr_to_ctcb_ptr thread}
             \<inter> {s. reg_' s = register_from_H ARM_H.msgInfoRegister}) []
       (getMessageInfo thread) (Call getRegister_'proc)"

(* VERO START c0224 disprove *)

oops

(* VERO END c0224 disprove *)

end

context kernel
begin

(* VERO START c0229 prove_aux *)



(* VERO END c0229 prove_aux *)

vero_prove "c0229" c0229_prove[corres]:
  "ccorres (\<lambda>r r'. r' = thread_state_to_tsType r) ret__unsigned_' (tcb_at' thread)
           ({s. f s = tcb_ptr_to_ctcb_ptr thread} \<inter>
            {s. cslift s (Ptr &(f s\<rightarrow>[''tcbState_C''])) = Some (thread_state_' s)}) []
  (getThreadState thread) (Call thread_state_get_tsType_'proc)"

(* VERO START c0229 prove *)

oops

(* VERO END c0229 prove *)

(* VERO START c0229 disprove_aux *)



(* VERO END c0229 disprove_aux *)

vero_disprove "c0229" c0229_disprove[corres]:
  "ccorres (\<lambda>r r'. r' = thread_state_to_tsType r) ret__unsigned_' (tcb_at' thread)
           ({s. f s = tcb_ptr_to_ctcb_ptr thread} \<inter>
            {s. cslift s (Ptr &(f s\<rightarrow>[''tcbState_C''])) = Some (thread_state_' s)}) []
  (getThreadState thread) (Call thread_state_get_tsType_'proc)"

(* VERO START c0229 disprove *)

oops

(* VERO END c0229 disprove *)

end

context kernel_m begin

(* VERO START c0236 prove_aux *)



(* VERO END c0236 prove_aux *)

vero_prove "c0236" c0236_prove:
  "ccorres dc xfdc \<top>
       (UNIV \<inter> \<lbrace>\<acute>thread = tcb_ptr_to_ctcb_ptr thread\<rbrace> \<inter> \<lbrace>\<acute>reg = register_from_H reg\<rbrace>
             \<inter> {s. w_' s = val}) []
       (asUser thread (setRegister reg val))
       (Call setRegister_'proc)"

(* VERO START c0236 prove *)

oops

(* VERO END c0236 prove *)

(* VERO START c0236 disprove_aux *)



(* VERO END c0236 disprove_aux *)

vero_disprove "c0236" c0236_disprove:
  "ccorres dc xfdc \<top>
       (UNIV \<inter> \<lbrace>\<acute>thread = tcb_ptr_to_ctcb_ptr thread\<rbrace> \<inter> \<lbrace>\<acute>reg = register_from_H reg\<rbrace>
             \<inter> {s. w_' s = val}) []
       (asUser thread (setRegister reg val))
       (Call setRegister_'proc)"

(* VERO START c0236 disprove *)

oops

(* VERO END c0236 disprove *)

(* VERO START c0241 prove_aux *)



(* VERO END c0241 prove_aux *)

vero_prove "c0241" c0241_prove[corres]:
  "ccorres ((=) \<circ> option_to_ptr) ret__ptr_to_unsigned_long_'
           (tcb_at' t)
           (UNIV \<inter> {s. thread_' s = tcb_ptr_to_ctcb_ptr t}
                  \<inter> {s. isReceiver_' s = from_bool isReceiver}) []
      (lookupIPCBuffer isReceiver t) (Call lookupIPCBuffer_'proc)"

(* VERO START c0241 prove *)

oops

(* VERO END c0241 prove *)

(* VERO START c0241 disprove_aux *)



(* VERO END c0241 disprove_aux *)

vero_disprove "c0241" c0241_disprove[corres]:
  "ccorres ((=) \<circ> option_to_ptr) ret__ptr_to_unsigned_long_'
           (tcb_at' t)
           (UNIV \<inter> {s. thread_' s = tcb_ptr_to_ctcb_ptr t}
                  \<inter> {s. isReceiver_' s = from_bool isReceiver}) []
      (lookupIPCBuffer isReceiver t) (Call lookupIPCBuffer_'proc)"

(* VERO START c0241 disprove *)

oops

(* VERO END c0241 disprove *)

(* VERO START c0269 prove_aux *)



(* VERO END c0269 prove_aux *)

vero_prove "c0269" c0269_prove [corres]:
  "\<And>mi. ccorres (=) ret__unsigned_long_' \<top> {s. mi = message_info_to_H (mi_' s)} []
           (return (wordFromMessageInfo mi)) (Call wordFromMessageInfo_'proc)"

(* VERO START c0269 prove *)

oops

(* VERO END c0269 prove *)

(* VERO START c0269 disprove_aux *)



(* VERO END c0269 disprove_aux *)

vero_disprove "c0269" c0269_disprove [corres]:
  "\<And>mi. ccorres (=) ret__unsigned_long_' \<top> {s. mi = message_info_to_H (mi_' s)} []
           (return (wordFromMessageInfo mi)) (Call wordFromMessageInfo_'proc)"

(* VERO START c0269 disprove *)

oops

(* VERO END c0269 disprove *)

(* VERO START c0275 prove_aux *)



(* VERO END c0275 prove_aux *)

vero_prove "c0275" c0275_prove:
  "ccorres dc xfdc (tcb_at' thread)
           (UNIV \<inter> \<lbrace>mi = message_info_to_H mi'\<rbrace>) hs
           (setMessageInfo thread mi)
           (\<acute>ret__unsigned_long :== CALL wordFromMessageInfo(mi');;
            CALL setRegister(tcb_ptr_to_ctcb_ptr thread, scast Kernel_C.msgInfoRegister, \<acute>ret__unsigned_long))"

(* VERO START c0275 prove *)

oops

(* VERO END c0275 prove *)

(* VERO START c0275 disprove_aux *)



(* VERO END c0275 disprove_aux *)

vero_disprove "c0275" c0275_disprove:
  "ccorres dc xfdc (tcb_at' thread)
           (UNIV \<inter> \<lbrace>mi = message_info_to_H mi'\<rbrace>) hs
           (setMessageInfo thread mi)
           (\<acute>ret__unsigned_long :== CALL wordFromMessageInfo(mi');;
            CALL setRegister(tcb_ptr_to_ctcb_ptr thread, scast Kernel_C.msgInfoRegister, \<acute>ret__unsigned_long))"

(* VERO START c0275 disprove *)

oops

(* VERO END c0275 disprove *)

end

context kernel_m
begin

(* VERO START c0293 prove_aux *)



(* VERO END c0293 prove_aux *)

vero_prove "c0293" c0293_prove:
  "ccorres (\<lambda>r r'. r = message_info_to_H r') tag_'
           (tcb_at' sender) UNIV hs (getMessageInfo sender)
           (\<acute>ret__unsigned_long :== CALL getRegister(tcb_ptr_to_ctcb_ptr sender,scast Kernel_C.msgInfoRegister);;
            \<acute>tag :== CALL messageInfoFromWord(\<acute>ret__unsigned_long))"

(* VERO START c0293 prove *)

oops

(* VERO END c0293 prove *)

(* VERO START c0293 disprove_aux *)



(* VERO END c0293 disprove_aux *)

vero_disprove "c0293" c0293_disprove:
  "ccorres (\<lambda>r r'. r = message_info_to_H r') tag_'
           (tcb_at' sender) UNIV hs (getMessageInfo sender)
           (\<acute>ret__unsigned_long :== CALL getRegister(tcb_ptr_to_ctcb_ptr sender,scast Kernel_C.msgInfoRegister);;
            \<acute>tag :== CALL messageInfoFromWord(\<acute>ret__unsigned_long))"

(* VERO START c0293 disprove *)

oops

(* VERO END c0293 disprove *)

(* VERO START c0296 prove_aux *)



(* VERO END c0296 prove_aux *)

vero_prove "c0296" c0296_prove:
  "ccorres dc xfdc \<top>
       (UNIV \<inter> \<lbrace>\<acute>thread = tcb_ptr_to_ctcb_ptr thread\<rbrace>
             \<inter> {s. v_' s = val}) []
       (asUser thread (setNextPC val))
       (Call setNextPC_'proc)"

(* VERO START c0296 prove *)

oops

(* VERO END c0296 prove *)

(* VERO START c0296 disprove_aux *)



(* VERO END c0296 disprove_aux *)

vero_disprove "c0296" c0296_disprove:
  "ccorres dc xfdc \<top>
       (UNIV \<inter> \<lbrace>\<acute>thread = tcb_ptr_to_ctcb_ptr thread\<rbrace>
             \<inter> {s. v_' s = val}) []
       (asUser thread (setNextPC val))
       (Call setNextPC_'proc)"

(* VERO START c0296 disprove *)

oops

(* VERO END c0296 disprove *)

end

end
