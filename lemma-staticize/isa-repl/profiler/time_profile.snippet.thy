(* INJECTED by the intra-tactic profiler harness, at top level before the target
   lemma. time_profile runs the inner method under PolyML ProfileTime, forcing the
   method's FULL result sequence inside the profiled region (so the real search/
   simp work is sampled while completion semantics are preserved), and emits the
   per-ML-function CPU-sample table via tracing (parsed by parse_profile.py).
   NOTE: ASCII-only comment on purpose - this Isabelle setup rejects literal
   U+2039/U+203A cartouche chars even inside comments. *)
method_setup time_profile = \<open>
  Method.text_closure >> (fn m => fn ctxt =>
    (fn facts => fn cst =>
      (* FAITHFUL wrapper: force the ENTIRE result sequence to a list inside the
         profiled region (so the tactic's real work is sampled), then rebuild the
         sequence so `by`/`apply` see EXACTLY the results they would lazily — i.e.
         completion semantics are preserved. The earlier `Seq.pull |> Seq.cons`
         version forced only the first result and kept the tail lazy, which made
         `by` WRONGLY accept incomplete proofs (validated bug). *)
      Seq.of_list (ML_Profiling.profile_time
        (fn () => Seq.list_of (Method.evaluate m ctxt facts cst)) ())))
\<close>
