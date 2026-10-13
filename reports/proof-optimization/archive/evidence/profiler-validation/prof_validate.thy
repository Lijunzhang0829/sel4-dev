theory prof_validate
  imports Main
begin

(* EXACT current snippet ML *)
method_setup time_profile = \<open>
  Method.text_closure >> (fn m => fn ctxt =>
    (fn facts => fn cst =>
      ML_Profiling.profile_time
        (fn () =>
          (case Seq.pull (Method.evaluate m ctxt facts cst) of
            SOME (r, s') => Seq.cons r s'
          | NONE => Seq.empty))
        ()))
\<close>

(* V1: simp-heavy goal, proved BOTH ways (semantic-equivalence: both must build) *)
lemma tp_simp:    "sum_list [0..<700] = (244650::nat)" by (time_profile \<open>simp\<close>)
lemma plain_simp: "sum_list [0..<700] = (244650::nat)" by simp

(* V2: classical/blast goal (no simp content), proved BOTH ways *)
lemma tp_blast:
  "(\<forall>x. P x \<longrightarrow> Q x) \<Longrightarrow> (\<forall>x. Q x \<longrightarrow> R x) \<Longrightarrow> (\<forall>x. R x \<longrightarrow> S x) \<Longrightarrow> P a \<Longrightarrow> S a \<and> R a \<and> Q a"
  by (time_profile \<open>blast\<close>)
lemma plain_blast:
  "(\<forall>x. P x \<longrightarrow> Q x) \<Longrightarrow> (\<forall>x. Q x \<longrightarrow> R x) \<Longrightarrow> (\<forall>x. R x \<longrightarrow> S x) \<Longrightarrow> P a \<Longrightarrow> S a \<and> R a \<and> Q a"
  by blast

(* oracle-cleanliness: time_profile proofs must NOT be sorry/oracle-tainted *)
ML \<open>
  fun chk nm th =
    let val st = Thm.peek_status th in
      if #oracle st orelse #failed st
      then error ("@@CHK " ^ nm ^ " TAINTED oracle=" ^ Bool.toString (#oracle st)
                  ^ " failed=" ^ Bool.toString (#failed st))
      else writeln ("@@CHK " ^ nm ^ " CLEAN (real proof, oracle=false failed=false)")
    end;
  chk "tp_simp"  @{thm tp_simp};
  chk "tp_blast" @{thm tp_blast};
\<close>

end
