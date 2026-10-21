"""Native, bounded structural screening of conditional decompositions.

Scores are transparent heuristics, not solvability probabilities or proof authority.
No target proofs, sibling assumptions, or learned premise pools are consulted.
"""
import json
import re
import xml.etree.ElementTree as ET

from regen.execution.proof_commits import digest, durable, freeze_evidence
from regen.planning.sketch_check import available_prefix, prefix_binding

VERSION = 'native-structure-v1'
SECONDS = 10
LOGICAL = {'Pure.prop', 'Pure.term', 'Pure.imp', 'Pure.all', 'Pure.eq', 'HOL.Trueprop', 'HOL.eq', 'HOL.conj',
           'HOL.disj', 'HOL.implies', 'HOL.All', 'HOL.Ex', 'HOL.Not', 'HOL.True', 'HOL.False'}


def profile_source(statement, marker):
    # statement/context have already passed document parsing. ML is trusted code.
    return statement + r'''
ML_val \<open>let
  val ctxt = \<^context>;
  val {goal, ...} = @{Isar.goal};
  val t = Thm.prop_of goal;
  val frees = Term.add_frees t [];
  val vars = Term.add_vars t [];
  fun canonical (Free (n, T)) = Free ("f" ^ Int.toString (find_index (fn x => x = (n,T)) frees), T)
    | canonical (Var (n, T)) = Var (("v", find_index (fn x => x = (n,T)) vars), T)
    | canonical x = x;
  fun norm (Abs (_, T, b)) = Abs ("", T, norm b)
    | norm (a $ b) = norm a $ norm b
    | norm x = canonical x;
  val names = sort_strings (Term.add_const_names t []);
  fun definition name =
    (case try (Proof_Context.get_thms ctxt) (name ^ "_def") of
      SOME thms => map Thm.prop_of thms | NONE => []);
  (* One layer only; absent _def equations are unknown, never assumed simple. *)
  val definitions = map (fn n => (n, definition n)) (take 64 names);
  val bodies = maps #2 definitions;
  val expanded = sort_strings (fold Term.add_const_names bodies names);
  fun elem n s = XML.Elem ((n, []), [XML.Text s]);
  val payload = XML.string_of (XML.Elem (("profile", []),
    [elem "canonical" (ML_Syntax.print_term (norm t)),
     elem "term_size" (Int.toString (Term.size_of_term t)),
     elem "definition_size" (Int.toString (fold (fn b => fn n => n + Term.size_of_term b) bodies 0)),
     elem "definition_count" (Int.toString (length (filter (not o null o #2) definitions))),
     XML.Elem (("constants", []), map (elem "constant") names),
     XML.Elem (("expanded_constants", []), map (elem "constant") expanded)]));
in writeln (''' + json.dumps('REGEN_STRUCTURE_BEGIN ' + marker + '\n') + r''' ^ payload ^ ''' + json.dumps('\nREGEN_STRUCTURE_END ' + marker) + r''') end\<close>
oops
'''


def parse_profile(log, marker):
    matches = re.findall(r'REGEN_STRUCTURE_BEGIN ' + re.escape(marker) + r'\n(.*?)\n'
                         + r'REGEN_STRUCTURE_END ' + re.escape(marker), log, re.S)
    if len(matches) != 1:
        raise ValueError('native structural observation is missing or ambiguous')
    root = ET.fromstring(matches[0])
    if root.tag != 'profile' or not root.findtext('canonical'):
        raise ValueError('invalid native structural observation')
    result = {k: int(root.findtext(k)) for k in ('term_size', 'definition_size', 'definition_count')}
    if any(v < 0 for v in result.values()):
        raise ValueError('negative native structure count')
    result.update(canonical_sha256=digest(root.findtext('canonical')),
                  constants=[e.text for e in root.findall('./constants/constant')],
                  expanded_constants=[e.text for e in root.findall('./expanded_constants/constant')])
    return result


def _profile(checker, store, entries, nodes, key):
    from regen.planning.sketch_workflow import _node, _save
    from regen.execution.proof_batches import _render
    from regen.execution.isabelle_node_checker import wrap_context
    from regen.execution.check_tasks import check_cancelled, NativePreparationError
    check_cancelled()
    base = available_prefix(store.state, entries, key)
    binding = {'run_id': store.state['graph']['run_id'], 'entry': _node(nodes[key]),
               'imports': store.imports, 'prefix': prefix_binding(base), 'version': VERSION}
    marker = digest(binding)
    prefix = _render(base['graph'], base['proofs'], store.imports, 'Batch_Check_' + digest(store.imports)[:16])
    source = prefix[:-len('end\n')] + wrap_context(nodes[key]['context'],
        profile_source(nodes[key]['statement'], marker)) + '\nend\n'
    checker._sketch_prefix_names = [n['name'] for k in base['accepted_order']
                                    for n in base['graph']['nodes'] if n['id'] == k]
    check = freeze_evidence(checker.check_sketch_source(source, prefix, [], timeout=SECONDS))
    if check.get('failure_scope') == 'native_preparation':
        raise NativePreparationError(check.get('diagnostic', 'structure preparation failed'))
    result = {'status': 'unknown'}
    if check['verdict'] == 'green' and check['returncode'] == 0:
        result = {'status': 'observed', **parse_profile(check['log_content'], marker)}
    record = {**binding, 'profile': result, 'check': check, 'publishable': False}
    return {'profile_id': _save(store, 'profiles', record), **result}


def compare(parent, leaf):
    """Conservative signals; a smaller score is not a difficulty certificate."""
    if parent['status'] != 'observed' or leaf['status'] != 'observed':
        return {'score': None, 'risks': ['structure_unknown'], 'retained_operations': []}
    operations = set(parent['constants']) - LOGICAL
    retained = sorted(operations & set(leaf['expanded_constants']))
    flags = []
    if parent['canonical_sha256'] == leaf['canonical_sha256']:
        flags.append('same_obligation')
    # Multi-operation carry-over is a screening signal, not semantic complexity.
    if len(operations) >= 3 and len(retained) / len(operations) >= .75:
        flags.append('main_operations_retained')
    iterations = sorted(n for n in leaf['expanded_constants'] if re.search(
        r'(?:^|\.)(?:whileLoop\w*|mapME?\w*|fold\w*|rec_list)$', n))
    if iterations:
        flags.append('iteration_requires_invariant')
    score = (leaf['term_size'] + min(leaf['definition_size'], 10000)
             + 4 * len(set(leaf['expanded_constants']) - LOGICAL) + 100 * len(iterations))
    return {'score': score, 'risks': flags, 'retained_operations': retained,
            'iteration_constants': iterations}


def assess_sketch(checker, store, sha, targets=None):
    from regen.planning.sketch_workflow import _draft, _group, _save
    from regen.planning.sketch_check import load_groups
    entries, nodes, proofs = _draft(store, sha)
    groups = load_groups(store)
    selected = [g['target_entry_id'] for g in groups] if targets is None else targets
    if (not isinstance(selected, list) or not selected or len(set(selected)) != len(selected)
            or any(not isinstance(k, str) or k not in nodes for k in selected)):
        raise ValueError('select unique current sketch targets')
    children = {g['target_entry_id'] for g in groups}
    profiles = {}
    results = []
    # Helpers precede parents in the document; assess descendants bottom-up.
    for target in sorted(selected, key=lambda key: entries.index(nodes[key])):
        group, cert = _group(store, nodes, proofs, target)
        for key in [target, *group['helper_ids']]:
            if key not in profiles:
                profiles[key] = _profile(checker, store, entries, nodes, key)
        leaves = []
        for key in group['helper_ids']:
            row = {'helper_id': key, **compare(profiles[target], profiles[key])}
            if key in store.state['proofs']:
                row.update(status='proved', score=0, risks=[])
            elif key in children:
                child = current_assessment(store, nodes, proofs, key)
                child_key = child['rank_key']
                row.update(status='delegated', child_assessment_id=digest(child),
                           score=child_key[0] if child_key is not None else None,
                           estimated_total=child_key[1] if child_key is not None else None,
                           pending_leaf_count=child_key[2] if child_key is not None else None)
            else:
                row['status'] = 'pending_leaf'
            leaves.append(row)
        pending = [r for r in leaves if r['status'] != 'proved']
        known = all(r['score'] is not None for r in pending)
        score = [max((r['score'] for r in pending), default=0),
                 sum(r.get('estimated_total', r['score']) for r in pending),
                 sum(r.get('pending_leaf_count', 1) for r in pending)] if known else None
        record = {'run_id': store.state['graph']['run_id'], 'source_sha256': sha,
                  'sketch_sha256': cert['sketch_sha256'], 'receipt_id': cert['receipt_id'],
                  'interface': cert['interface'], 'imports': store.imports, 'prefix': cert['prefix'],
                  'version': VERSION, 'profiles': {k: profiles[k] for k in [target, *group['helper_ids']]},
                  'helpers': leaves, 'rank_key': score, 'difficulty_certified': False,
                  'publishable': False}
        identity = _save(store, 'assessments', record)
        durable(store.root/'workflow'/'assessment-index'/(cert['sketch_sha256']+'.json'),
                {'assessment_id': identity})
        recommended = sorted((r for r in leaves if r['status'] == 'pending_leaf'),
                             key=lambda r: (r['score'] is None, r['score'] or 0), reverse=True)[:2]
        results.append({'target_entry_id': target, 'assessment_id': identity,
                        'helpers': leaves, 'rank_key': score,
                        'ranked_alternatives': rank_alternatives(store, record),
                        'recommended_probe_ids': [r['helper_id'] for r in recommended]})
    return {'status': 'sketches_assessed', 'results': results,
            'difficulty_certified': False, 'publishable': False, 'new_green': []}


def rank_alternatives(store, current):
    """Only the same exact parent/imports/prefix is comparable. Old drafts are references."""
    from regen.planning.sketch_workflow import _read
    alternatives = {}
    for path in sorted((store.root/'workflow'/'assessments').glob('*.json')):
        record = _read(store, 'assessments', path.stem)
        if (record['interface']['target'] != current['interface']['target']
                or record['imports'] != current['imports'] or record['prefix'] != current['prefix']
                or record['version'] != VERSION):
            continue
        alternatives[record['sketch_sha256']] = {
            'assessment_id': path.stem, 'source_sha256': record['source_sha256'],
            'sketch_sha256': record['sketch_sha256'], 'rank_key': record['rank_key'],
            'requires_current_sketch_check_and_review': True}
    return sorted(alternatives.values(), key=lambda r: (r['rank_key'] is None,
                                                       r['rank_key'] or [], r['sketch_sha256']))


def current_assessment(store, nodes, proofs, target):
    from regen.planning.sketch_workflow import _group, _read, _compatible
    _, cert = _group(store, nodes, proofs, target)
    path = store.root/'workflow'/'assessment-index'/(cert['sketch_sha256']+'.json')
    if not path.exists():
        raise ValueError('sketch needs a native structural assessment')
    record = _read(store, 'assessments', json.loads(path.read_text())['assessment_id'])
    if record['receipt_id'] != cert['receipt_id'] or record['version'] != VERSION:
        raise ValueError('stale structural assessment')
    for key, profile in record['profiles'].items():
        native = _read(store, 'profiles', profile['profile_id'])
        if not _compatible(store, nodes, key, native):
            raise ValueError('structural assessment context changed')
    for row in record['helpers']:
        if 'child_assessment_id' in row:
            child = current_assessment(store, nodes, proofs, row['helper_id'])
            if digest(child) != row['child_assessment_id']:
                raise ValueError('child structural assessment changed; reassess the parent')
    return record


def validate_review(store, nodes, proofs, target, reviews):
    """Enforce explicit evidence for suspicious leaves; no self-label override."""
    from regen.planning.sketch_workflow import _read
    assessment = current_assessment(store, nodes, proofs, target)
    signals = {r['helper_id']: r for r in assessment['helpers']}
    for row in reviews:
        key = row['helper_id']
        if key in store.state['proofs']:
            continue
        generalized = any(row[f] == 'generalized' for f in ('domain_scope', 'premises', 'conclusion'))
        if generalized:
            warrant = row.get('generalization_evidence')
            fields = ({'variable', 'invariant', 'base_case', 'step_case'}
                      if row['generalization'] == 'induction' else {'consumers', 'shared_pattern'})
            if (not isinstance(warrant, dict) or set(warrant) != fields
                    or any(not isinstance(v, str) or not v.strip() for v in warrant.values())):
                raise ValueError('generalization needs a concrete induction/reuse mechanism')
        if row['burden'] == 'delegated':
            continue  # _review binds the current child recursively.
        probe = _read(store, 'probes', row['probe_id']) if row.get('probe_id') else None
        proved = probe is not None and probe['status'] == 'proved_in_probe'
        from regen.planning.sketch_phases import formal_allowed
        if signals[key]['risks'] and not proved and not formal_allowed(store,nodes,key):
            raise ValueError('risky leaf needs a proved native probe or reviewed child sketch: '
                             + key + ' (' + ', '.join(signals[key]['risks']) + ')')


def read_failure(store, failure_id):
    from regen.planning.sketch_workflow import _read, _pending, _diagnosis
    record = _read(store, 'failures', failure_id)
    return {**record, 'failure_id': failure_id,
            'current': _pending(store).get(record['entry']['id']) == failure_id,
            'diagnosis': _diagnosis(store, failure_id),
            'repair_scope': record['entry']['id'],
            'notice': 'Repair this exact failed entry using its native state. Keep accepted siblings; '
                      'partial execution is not an accepted proof or reusable tactic checkpoint.'}
