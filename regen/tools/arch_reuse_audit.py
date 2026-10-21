"""Read-only semantic fingerprints for the opt-in Arch scope experiment.

Fingerprints compare selected observable context data, not arbitrary ML state.
This is deliberately separate from the timed trials.
"""
import re


CATEGORIES = ('facts', 'indexed_facts', 'simp', 'congs', 'weak_congs', 'simprocs',
              'loopers', 'safe_solvers', 'unsafe_solvers', 'wp', 'wp_trips', 'wp_splits',
              'wp_combs', 'wp_unsafe', 'wp_pre', 'spec_rules', 'arch_registration')


def expected_keys(graph):
    by_id = {n['id']: n for n in graph['nodes']}
    prefix, expected = [], set()
    order = graph.get('accepted_order') or graph['execution_order']
    for index, key in enumerate([*order, None]):
        label = 'export' if key is None else 'before-'+str(index)
        expected.update(label+'/'+c for c in CATEGORIES)
        expected.update(label+'/'+kind+n for n in prefix for kind in ('target_', 'deps_'))
        if key is not None:
            prefix.append(by_id[key]['name'])
    return expected


def snapshot(label, *, global_context=False, targets=()):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', label):
        raise ValueError('invalid snapshot label')
    if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_'.]*", n) for n in targets):
        raise ValueError('invalid theorem name')
    names = ', '.join('"'+n+'"' for n in targets)
    return r'''ML \<open>
let
  val thy = @{theory};
  val ctxt = CONTEXT;
  fun hash s = SHA1.rep (SHA1.digest s);
  fun term t = hash (YXML.string_of_body (Term_XML.Encode.term (Sign.consts_of thy) t));
  fun thm t = space_implode "\000" (Thm.raw_derivation_name t ::
    term (Thm.full_prop_of t) :: map term (Thm.hyps_of t));
  fun emit kind rows = writeln ("REGEN_REUSE_SNAPSHOT LABEL " ^ kind ^ " " ^
    string_of_int (length rows) ^ " " ^ SHA1.rep (SHA1.digest
      (YXML.string_of_body (XML.Encode.list XML.Encode.string (map hash rows)))));
  val facts = Facts.dest_static true
    (map Global_Theory.facts_of (Theory.parents_of thy)) (Proof_Context.facts_of ctxt);
  val {simps, procs, congs, weak_congs, loopers, unsafe_solvers, safe_solvers} =
    Raw_Simplifier.dest_ss (simpset_of ctxt);
  val wp = WeakestPre.debug_get ctxt;
  val arch = Locale.intern thy "Arch";
  val _ = emit "facts" (maps (fn (n, ts) => map (fn t => n ^ "\000" ^ thm t) ts) facts);
  val _ = emit "indexed_facts" (map (thm o fst) (Facts.props (Proof_Context.facts_of ctxt)));
  val _ = List.app (fn (n, ts) => List.app (fn (i, t) =>
    writeln ("REGEN_REUSE_FACT LABEL " ^ n ^ " " ^ string_of_int i ^ " " ^
      hash (thm t) ^ " " ^ term (Thm.full_prop_of t) ^ " " ^
      (if Thm.raw_derivation_name t = "" then "-" else Thm.raw_derivation_name t)))
    (map_index I ts)) facts;
  val _ = emit "simp" (map (fn (n, t) => n ^ "\000" ^ thm t) simps);
  val _ = emit "congs" (map (fn ((b, n), t) => Bool.toString b ^ n ^ thm t) congs);
  val _ = emit "weak_congs" (map (fn (b, n) => Bool.toString b ^ n) weak_congs);
  val _ = emit "simprocs" (maps (fn (n, ts) => map (fn t => n ^ term t) ts) procs);
  val _ = emit "loopers" loopers;
  val _ = emit "safe_solvers" safe_solvers;
  val _ = emit "unsafe_solvers" unsafe_solvers;
  val _ = emit "wp" (map thm (WeakestPre.dest_rules (#rules wp)));
  val _ = emit "wp_trips" (map thm (fst (#trips wp)));
  val _ = emit "wp_splits" (map thm (#splits wp));
  val _ = emit "wp_combs" (map thm (#combs wp));
  val _ = emit "wp_unsafe" (map thm (#unsafe_rules wp));
  val _ = emit "wp_pre" (map thm (Named_Theorems.get ctxt @{named_theorems wp_pre}));
  val _ = emit "spec_rules" (map (fn (r: Spec_Rules.spec_rule) =>
    #name r ^ YXML.string_of_body (Spec_Rules.encode_rough_classification (#rough_classification r)) ^
    space_implode "\000" (map term (#terms r) @ map thm (#rules r))) (Spec_Rules.get ctxt));
  val _ = emit "arch_registration" [string_of_int (length
    (Locale.registrations_of (Context.Proof ctxt) arch))];
  fun target n = let
    val ts = Proof_Context.get_thms ctxt n;
    val _ = Thm.consolidate ts;
    val _ = if Thm_Deps.has_skip_proof ts then error "reuse audit: skip proof" else ();
    val _ = emit ("target_" ^ n) (map thm ts);
    val _ = emit ("deps_" ^ n) (sort string_ord (map
      (fn (_, (name, index)) => name ^ ":" ^ string_of_int index) (Thm_Deps.thm_deps thy ts)));
    in () end;
  val _ = List.app target [TARGETS];
in () end
\<close>
'''.replace('CONTEXT', 'Proof_Context.init_global thy' if global_context else '@{context}').replace('LABEL', label).replace('TARGETS', names)


def instrument(source, graph):
    by_id = {n['id']: n for n in graph['nodes']}
    prefix = []
    for index, key in enumerate(graph.get('accepted_order') or graph['execution_order']):
        node = by_id[key]
        statement = node['statement']
        if source.count(statement) != 1:
            raise ValueError('ambiguous statement instrumentation')
        source = source.replace(statement, snapshot('before-'+str(index), targets=prefix)+'\n'+statement)
        prefix.append(node['name'])
    from regen.execution.isabelle_node_checker import append_before_theory_end
    return append_before_theory_end(source, snapshot('export', global_context=True,
        targets=[n['name'] for n in graph['nodes']]))


def parse(log):
    rows = {}
    for label, kind, count, digest in re.findall(
            r'^REGEN_REUSE_SNAPSHOT (\S+) (\S+) (\d+) ([0-9a-f]{40})$', log, re.M):
        key = label+'/'+kind
        if key in rows:
            raise ValueError('duplicate context fingerprint')
        rows[key] = {'count': int(count), 'sha1': digest}
    if not rows:
        raise ValueError('missing context fingerprints')
    return rows
