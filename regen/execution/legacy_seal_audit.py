"""Exact pre-declaration-bound v3 seal renderer, exclusively for receipt replay.

Never used to generate or certify a new seal. The complete source, successful
native logs, raw theorem receipts, state, imports and build are still validated
by seal_evidence. Keeping these historical emitter bytes separate prevents
future production changes from silently reinterpreting old immutable evidence.
"""
import hashlib
import re

LEGACY_RENDERER_SOURCE_SHA256 = '7218082785f46b319dbc4055052e5eb98db237cfbca7c3c7b75a3724caa9351c'

def automation_audit_ml(
    theorem_name: str, *, dependency_filename: str = "proof-dependencies.tsv",
) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_'.]*", theorem_name):
        raise ValueError(f"unsupported theorem name for audit: {theorem_name}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", dependency_filename):
        raise ValueError(f"unsupported dependency audit filename: {dependency_filename}")
    return rf'''ML \<open>
local
  val thy = @{{theory}};
  val ctxt = Proof_Context.init_global thy;
  val plain_ctxt = Config.put show_markup false ctxt;
  val targets = Proof_Context.get_thms ctxt "{theorem_name}";
  val hex_digits = "0123456789abcdef";
  fun hex_char char =
    let val byte = Char.ord char
    in String.implode
      [String.sub (hex_digits, byte div 16), String.sub (hex_digits, byte mod 16)]
    end;
  fun enc value = String.translate hex_char value;
  fun row stream tag fields =
    TextIO.output (stream, space_implode "\t" (tag :: map enc fields) ^ "\n");
  fun prop_of thm =
    Print_Mode.setmp [] (Syntax.string_of_term plain_ctxt) (Thm.full_prop_of thm);
  fun owner_of thm = Long_Name.qualifier (Thm.raw_derivation_name thm);
  fun thm_row stream tag extras thm =
    row stream tag
      (extras @ [owner_of thm, Thm.get_name_hint thm,
                 Thm.raw_derivation_name thm, prop_of thm]);
  fun bool_text value = if value then "true" else "false";
  fun app_index f =
    let fun apply _ [] = ()
          | apply index (item :: items) =
              (f (index, item); apply (index + 1) items)
    in apply 0 end;
  val context_stream = TextIO.openOut "/audit-output/automation-context.tsv";
  val dependency_stream = TextIO.openOut "/audit-output/{dependency_filename}";
  val {{simps, procs, congs, weak_congs, loopers,
       unsafe_solvers, safe_solvers}} =
    Raw_Simplifier.dest_ss (simpset_of ctxt);
  val wp = WeakestPre.debug_get ctxt;
  val wp_rules = WeakestPre.dest_rules (#rules wp);
  val wp_trips = fst (#trips wp);
  val wp_pre = Named_Theorems.get ctxt @{{named_theorems wp_pre}};
  val _ = if null targets then error "Audit target has no theorems" else ();
  val _ = Thm.consolidate targets;
  val dependencies = Thm_Deps.thm_deps thy targets;
in
  val _ = List.app
    (fn (name, thm) => thm_row context_stream "SIMP_RULE" [name] thm) simps;
  val _ = List.app
    (fn ((weak, name), thm) =>
      thm_row context_stream "SIMP_CONG" [bool_text weak, name] thm) congs;
  val _ = List.app
    (fn (weak, name) =>
      row context_stream "SIMP_WEAK_CONG" [bool_text weak, name]) weak_congs;
  val _ = List.app
    (fn (name, patterns) =>
      app_index (fn (index, pattern) =>
        row context_stream "SIMP_PROC_LHS"
          [name, string_of_int index,
           Print_Mode.setmp [] (Syntax.string_of_term plain_ctxt) pattern]) patterns)
    procs;
  val _ = List.app (fn name => row context_stream "SIMP_LOOPER" [name]) loopers;
  val _ = List.app
    (fn name => row context_stream "SIMP_SAFE_SOLVER" [name]) safe_solvers;
  val _ = List.app
    (fn name => row context_stream "SIMP_UNSAFE_SOLVER" [name]) unsafe_solvers;
  val _ = List.app (thm_row context_stream "WP_RULE" []) wp_rules;
  val _ = List.app (thm_row context_stream "WP_TRIP" []) wp_trips;
  val _ = List.app (thm_row context_stream "WP_SPLIT" []) (#splits wp);
  val _ = List.app (thm_row context_stream "WP_COMB" []) (#combs wp);
  val _ = List.app (thm_row context_stream "WP_UNSAFE" []) (#unsafe_rules wp);
  val _ = List.app (thm_row context_stream "WP_PRE" []) wp_pre;
  val _ = TextIO.closeOut context_stream;
  val _ = List.app
    (fn (_, (name, index)) =>
      row dependency_stream "THM_DEP" [name, string_of_int index]) dependencies;
  val _ = row dependency_stream "HAS_SKIP_PROOF"
    [bool_text (Thm_Deps.has_skip_proof targets)];
  val _ = TextIO.closeOut dependency_stream;
end
\<close>'''


def proof_dependency_audit_ml(
    theorem_name: str, *, dependency_filename: str,
) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_'.]*", theorem_name):
        raise ValueError(f"unsupported theorem name for audit: {theorem_name}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", dependency_filename):
        raise ValueError(f"unsupported dependency audit filename: {dependency_filename}")
    return rf'''ML \<open>
local
  val thy = @{{theory}};
  val ctxt = Proof_Context.init_global thy;
  val targets = Proof_Context.get_thms ctxt "{theorem_name}";
  val hex_digits = "0123456789abcdef";
  fun hex_char char =
    let val byte = Char.ord char
    in String.implode
      [String.sub (hex_digits, byte div 16), String.sub (hex_digits, byte mod 16)]
    end;
  fun enc value = String.translate hex_char value;
  fun row stream tag fields =
    TextIO.output (stream, space_implode "\t" (tag :: map enc fields) ^ "\n");
  fun bool_text value = if value then "true" else "false";
  val stream = TextIO.openOut "/audit-output/{dependency_filename}";
  val _ = if null targets then error "Audit target has no theorems" else ();
  val _ = Thm.consolidate targets;
  val dependencies = Thm_Deps.thm_deps thy targets;
in
  val _ = List.app
    (fn (_, (name, index)) =>
      row stream "THM_DEP" [name, string_of_int index]) dependencies;
  val _ = row stream "HAS_SKIP_PROOF"
    [bool_text (Thm_Deps.has_skip_proof targets)];
  val _ = TextIO.closeOut stream;
end
\<close>'''


def append_before_theory_end(source: str, block: str) -> str:
    head, separator, tail = source.rpartition("\nend\n")
    if not separator:
        raise ValueError("generated theory lacks final end command")
    return head + "\n\n" + block + separator + tail


def legacy_complete_audit_source(renderer, graph, proofs, *, theory_name=None):
    ordered = [next(n for n in graph['nodes'] if n['id'] == key)
               for key in graph['execution_order']]
    if not ordered or any(n['id'] not in proofs for n in ordered):
        raise ValueError('historical v3 audit requires nonempty complete targets')
    blocks = []
    for index, node in enumerate(ordered):
        token = re.sub(r"[^A-Za-z0-9_.-]", "_", node['id'])[-48:]
        token += '-' + hashlib.sha256(node['id'].encode()).hexdigest()[:10]
        filename = 'proof-dependencies-' + token + '.tsv'
        blocks.append(automation_audit_ml(node['name'], dependency_filename=filename)
                      if index == 0 else proof_dependency_audit_ml(node['name'], dependency_filename=filename))
    label = theory_name or 'Complete_Audit_' + graph['file_slot'].removesuffix('.thy')
    return append_before_theory_end(
        renderer._complete_theory_source(graph, proofs, theory_name=label), '\n\n'.join(blocks))
