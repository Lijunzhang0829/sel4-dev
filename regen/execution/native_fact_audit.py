"""Resolve the exported fact introduced at one native declaration boundary.

Capture and resolution run in the declaration's context. Isabelle's background
naming supplies locale qualifications; no basename search or old fact fallback
is allowed. Audits receive the exported theorems, with locale assumptions and
parameters discharged by Isabelle's ordinary declaration machinery.
"""
import json
import re


FACT_SNAPSHOT_ML = r'''ML \<open>
val regen_audit_facts_before =
  let val facts = Global_Theory.facts_of \<^theory> in fn () => facts end;
\<close>'''


def target_resolution_ml(theorem_name: str) -> str:
    if not isinstance(theorem_name, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_'.]*", theorem_name):
        raise ValueError(f"unsupported theorem name for audit: {theorem_name!r}")
    name = json.dumps(theorem_name)
    return rf'''
  val ctxt = \<^context>;
  val thy = Proof_Context.theory_of ctxt;
  val global_facts = Global_Theory.facts_of thy;
  val declared_name =
    if Local_Theory.level ctxt > 0
    then Local_Theory.full_name ctxt (Binding.qualified_name {name})
    else Sign.full_name thy (Binding.qualified_name {name});
  val resolved_name = Facts.intern global_facts {name};
  fun newly_bound name =
    Facts.defined global_facts name andalso
      not (Facts.defined (regen_audit_facts_before ()) name);
  val names = filter newly_bound (distinct (op =) [declared_name, resolved_name]);
  val target_name =
    (case names of [name] => name
    | [] => error ("Missing newly declared audit fact: " ^ quote {name})
    | _ => error ("Ambiguous newly declared audit fact: " ^ quote {name}));
  val targets = Global_Theory.get_thms thy target_name;
  val _ = if null targets
    then error ("Audit target has no theorems: " ^ quote {name}) else ();
  val _ = List.app (fn thm => if null (Thm.hyps_of thm) then ()
    else error ("Audit target retains local hypotheses: " ^ quote target_name)) targets;
'''
