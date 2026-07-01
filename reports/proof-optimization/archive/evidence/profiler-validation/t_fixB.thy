theory t_fixB
  imports Main
begin
text \<open>Fix B on the KNOWN-failing simp goal. profile only wraps the force; the
      full result list is rebuilt so `by` sees identical results.\<close>
method_setup tp_fixB = \<open>
  Method.text_closure >> (fn m => fn ctxt =>
    (fn facts => fn cst =>
      Seq.of_list (ML_Profiling.profile_time
        (fn () => Seq.list_of (Method.evaluate m ctxt facts cst)) ())))
\<close>
lemma t_fixB_simp: "sum_list [0..<700] = (244650::nat)" by (tp_fixB \<open>simp\<close>)
end
