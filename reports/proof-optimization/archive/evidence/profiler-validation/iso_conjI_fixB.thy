theory iso_conjI_fixB
  imports Main
begin
method_setup tp_fixB = \<open>
  Method.text_closure >> (fn m => fn ctxt =>
    (fn facts => fn cst =>
      Seq.of_list (ML_Profiling.profile_time
        (fn () => Seq.list_of (Method.evaluate m ctxt facts cst)) ())))
\<close>
lemma t: "(P::bool) \<and> Q" by (tp_fixB \<open>rule conjI\<close>)
end
