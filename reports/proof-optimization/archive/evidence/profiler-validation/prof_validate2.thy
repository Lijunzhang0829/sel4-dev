theory prof_validate2
  imports Main
begin

(* FAITHFUL snippet (Fix B) — identical to time_profile.snippet.thy *)
method_setup time_profile = \<open>
  Method.text_closure >> (fn m => fn ctxt =>
    (fn facts => fn cst =>
      Seq.of_list (ML_Profiling.profile_time
        (fn () => Seq.list_of (Method.evaluate m ctxt facts cst)) ())))
\<close>

(* ---- V1 simp-heavy goal that SUCCEEDS (many rewrites: n+0 -> n, x3000) ---- *)
lemma tp_simp:    "map (\<lambda>n. n + 0) [0..<3000] = [0..<3000]" by (time_profile \<open>simp\<close>)
lemma plain_simp: "map (\<lambda>n. n + 0) [0..<3000] = [0..<3000]" by simp

(* ---- V2 classical/blast goal that SUCCEEDS (FOL resolution, no simp) ---- *)
lemma tp_blast:
  "(\<forall>x. P x \<longrightarrow> Q x) \<Longrightarrow> (\<forall>x. Q x \<longrightarrow> R x) \<Longrightarrow> (\<forall>x. R x \<longrightarrow> T x) \<Longrightarrow>
   (\<forall>x. T x \<longrightarrow> U x) \<Longrightarrow> (\<forall>x. U x \<longrightarrow> V x) \<Longrightarrow> P a \<Longrightarrow> V a \<and> U a \<and> T a \<and> R a \<and> Q a"
  by (time_profile \<open>blast\<close>)
lemma plain_blast:
  "(\<forall>x. P x \<longrightarrow> Q x) \<Longrightarrow> (\<forall>x. Q x \<longrightarrow> R x) \<Longrightarrow> (\<forall>x. R x \<longrightarrow> T x) \<Longrightarrow>
   (\<forall>x. T x \<longrightarrow> U x) \<Longrightarrow> (\<forall>x. U x \<longrightarrow> V x) \<Longrightarrow> P a \<Longrightarrow> V a \<and> U a \<and> T a \<and> R a \<and> Q a"
  by blast

end
