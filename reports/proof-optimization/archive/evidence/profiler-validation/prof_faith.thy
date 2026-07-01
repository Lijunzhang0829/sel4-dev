theory prof_faith
  imports Main
begin
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
lemma faith_tp: "(A::bool) \<Longrightarrow> (B::bool) \<Longrightarrow> A \<and> B" by (time_profile \<open>rule conjI\<close>)
end
