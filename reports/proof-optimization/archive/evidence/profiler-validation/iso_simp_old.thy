theory iso_simp_old
  imports Main
begin
method_setup tp_old = \<open>
  Method.text_closure >> (fn m => fn ctxt =>
    (fn facts => fn cst =>
      ML_Profiling.profile_time
        (fn () => (case Seq.pull (Method.evaluate m ctxt facts cst) of
            SOME (r, s') => Seq.cons r s' | NONE => Seq.empty)) ()))
\<close>
lemma t: "sum_list [0..<700] = (244650::nat)" by (tp_old \<open>simp\<close>)
end
