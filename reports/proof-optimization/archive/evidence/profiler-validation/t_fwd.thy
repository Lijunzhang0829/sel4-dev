theory t_fwd
  imports Main
begin
text \<open>pure forward, NO profiling: does wrapping via Method.evaluate alone break
      by-completion? `by simp` on this goal is KNOWN to fail (simp can't evaluate
      sum_list). If `by (fwd <simp>)` SUCCEEDS, the bug is in Method.evaluate
      forwarding; if it FAILS like plain simp, forwarding is faithful.\<close>
method_setup fwd = \<open>Method.text_closure >> (fn m => fn ctxt => Method.evaluate m ctxt)\<close>
lemma t_fwd_simp: "sum_list [0..<700] = (244650::nat)" by (fwd \<open>simp\<close>)
end
