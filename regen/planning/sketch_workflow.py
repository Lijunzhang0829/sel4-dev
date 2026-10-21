"""Evidence-bound decomposition reviews, disposable leaf probes and repair gates.

Review judgments belong to the generating agent; structural validation is not a
kernel proof of lower difficulty. Only ordinary audited checking publishes GREEN.
"""
from copy import copy
import json
from uuid import uuid4

from regen.execution.proof_commits import digest, durable, freeze_evidence
from regen.planning.sketch_check import (available_prefix, certificate, interface,
                                        load_groups, prefix_binding)

PROBE_SECONDS = 10


def _node(node):
    return {k: node[k] for k in ('id', 'name', 'role', 'statement', 'context')}


def _save(store, kind, data):
    identity = digest(data)
    durable(store.root / 'workflow' / kind / (identity + '.json'), data, immutable=True)
    return identity


def _read(store, kind, identity):
    if not isinstance(identity, str) or len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity):
        raise ValueError('invalid workflow receipt identity')
    try:
        value = json.loads((store.root / 'workflow' / kind / (identity + '.json')).read_text())
    except FileNotFoundError as error:
        raise ValueError('unknown workflow receipt') from error
    if digest(value) != identity or value['run_id'] != store.state['graph']['run_id']:
        raise ValueError('workflow receipt binding differs')
    return value


def _draft(store, sha):
    from regen.planning.theory_document import parse
    source = store.checked_source(sha)
    entries, proofs = parse(source, store.package, store.imports)
    return entries, {n['id']: n for n in entries}, proofs


def _compatible(store, nodes, key, record):
    prefix = prefix_binding(available_prefix(store.state, list(nodes.values()), key))
    return (record['entry'] == _node(nodes[key]) and record['imports'] == store.imports
            and prefix[:len(record['prefix'])] == record['prefix'])


def probe_helpers(checker, store, sha, entry_ids=None):
    entries, nodes, proofs = _draft(store, sha)
    groups = load_groups(store)
    parents = {g['target_entry_id'] for g in groups}
    leaves = {k for g in groups for k in g['helper_ids']
              if k not in parents and k not in store.state['proofs']}
    selected = sorted(leaves, key=lambda k: entries.index(nodes[k])) if entry_ids is None else entry_ids
    if (not isinstance(selected, list) or not selected
            or any(not isinstance(k, str) or k not in leaves for k in selected)
            or len(set(selected)) != len(selected)):
        raise ValueError('probe_helpers selects pending leaf helpers only')
    from regen.planning.sketch_check import authorize
    plain = copy(store); plain.require_review = False; plain.require_phases = False
    authorize(plain, store.checked_source(sha), selected)
    from regen.execution.proof_batches import _render, _safe_text
    from regen.execution.isabelle_node_checker import wrap_context
    results = []
    for key in selected:
        from regen.execution.check_tasks import check_cancelled
        check_cancelled()
        base = available_prefix(store.state, entries, key)
        label = 'Batch_Check_' + digest(store.imports)[:16]
        prefix = _render(base['graph'], base['proofs'], store.imports, label)
        proof = proofs.get(key, 'by simp')
        _safe_text(proof)
        from regen.planning.sketch_phases import guard_probe, guard_attempt
        guard_probe(store,nodes,key,proof)
        guard_attempt(store,nodes,{**proofs,key:proof},[key])
        binding = {'run_id':store.state['graph']['run_id'], 'entry':_node(nodes[key]),
                   'imports':store.imports, 'prefix':prefix_binding(base), 'proof':proof,
                   'timeout_seconds':PROBE_SECONDS}
        marker = 'REGEN_HELPER_PROBE_CLEAN ' + digest(binding)
        audit = r'''ML \<open>let
          val facts = Global_Theory.get_thms (Proof_Context.theory_of \<^context>) %s;
          val _ = Thm.consolidate facts;
          val _ = if not (null facts) andalso null (Thm_Deps.all_oracles facts)
            then () else error "Probe has an oracle dependency";
        in writeln %s end\<close>''' % (json.dumps(nodes[key]['name']), json.dumps(marker))
        source = prefix[:-len('end\n')] + wrap_context(nodes[key]['context'],
            nodes[key]['statement'] + '\n' + proof) + '\n' + audit + '\nend\n'
        names = {n['id']:n['name'] for n in base['graph']['nodes']}
        checker._sketch_prefix_names = [names[k] for k in base['accepted_order']]
        check = freeze_evidence(checker.check_sketch_source(source, prefix, [], timeout=PROBE_SECONDS))
        if check.get('failure_scope') == 'native_preparation':
            from regen.execution.check_tasks import NativePreparationError
            raise NativePreparationError(check.get('diagnostic', 'probe preparation failed'))
        status = ('proved_in_probe' if check['verdict'] == 'green' and check['returncode'] == 0
                  and marker in check['log_content'].splitlines() else
                  'candidate_timeout' if check['returncode'] == 124 else 'proof_failed')
        record = {**binding, 'status':status, 'source_sha256':sha, 'check':check,
                  'publishable':False, 'new_green':[]}
        identity = _save(store, 'probes', record)
        results.append({'entry_id':key,'probe_id':identity,'status':status,
                        'diagnostic':check.get('diagnostic'),
                        'native_proof_state_full':check.get('native_proof_state_full')})
        if status != 'proved_in_probe':
            results[-1]['failure_id'] = _failure(store, nodes, key, proof, check, 'probe:' + identity)
    return {'status':'probes_recorded','results':results,'publishable':False,'new_green':[]}


def _group(store, nodes, proofs, target):
    group = next((g for g in load_groups(store) if g['target_entry_id'] == target), None)
    cert = certificate(store, nodes, proofs, group) if group else None
    if cert is None:
        raise ValueError('review requires a current conditionally closed sketch')
    return group, cert


def _review(store, nodes, proofs, target, visiting=None):
    visiting = set() if visiting is None else visiting
    if target in visiting:
        raise ValueError('cyclic review dependency')
    group, cert = _group(store, nodes, proofs, target)
    pointer = store.root / 'workflow' / 'review-index' / (cert['sketch_sha256'] + '.json')
    if not pointer.exists():
        raise ValueError('sketch needs a decomposition review: ' + target)
    identity = json.loads(pointer.read_text())['review_id']
    record = _read(store, 'reviews', identity)
    if (record['sketch_sha256'] != cert['sketch_sha256']
            or record['receipt_id'] != cert['receipt_id']):
        raise ValueError('stale decomposition review')
    if getattr(store, 'require_assessment', False):
        from regen.planning.sketch_assessment import validate_review
        validate_review(store, nodes, proofs, target, record['reviews'])
    children = {g['target_entry_id'] for g in load_groups(store)}
    for row in record['reviews']:
        key = row['helper_id']
        if key in store.state['proofs']:
            continue
        if key in children:
            if row['burden'] != 'delegated':
                raise ValueError('helper now has a child decomposition; review it again')
            child = _review(store, nodes, proofs, key, visiting | {target})
            if row.get('child_review_id') != child:
                raise ValueError('child decomposition review changed')
        else:
            if row['burden'] != 'isolated' or ('probe_id' not in row and not getattr(store, 'require_assessment', False)):
                raise ValueError('helper is now a leaf; review it with a native probe')
            probe = _read(store, 'probes', row['probe_id']) if row.get('probe_id') else None
            if probe and not _compatible(store, nodes, key, probe):
                raise ValueError('leaf probe is stale')
    return identity


def review_sketch(store, sha, target, receipt_id, reviews):
    _, nodes, proofs = _draft(store, sha)
    group, cert = _group(store, nodes, proofs, target)
    if receipt_id != cert['receipt_id']:
        raise ValueError('review must reference the current native sketch receipt')
    if (not isinstance(reviews, list) or len(reviews) != len(group['helper_ids'])
            or any(not isinstance(r, dict) for r in reviews)
            or {r.get('helper_id') for r in reviews} != set(group['helper_ids'])):
        raise ValueError('review every group helper exactly once')
    children = {g['target_entry_id'] for g in load_groups(store)}
    for row in reviews:
        allowed = {'helper_id','use_indices','domain_scope','premises','conclusion','generalization',
                   'burden','reduction','probe_id','child_review_id'}
        required = allowed - {'probe_id','child_review_id'}
        if getattr(store, 'require_assessment', False):
            allowed.add('generalization_evidence')
        if set(row) - allowed or not required <= set(row):
            raise ValueError('incomplete local-demand and burden review')
        key = row['helper_id']
        uses = {u['index'] for u in cert.get('helper_uses', []) if u['helper_id'] == key}
        if (not uses or not isinstance(row['use_indices'], list)
                or any(type(i) is not int for i in row['use_indices'])
                or len(set(row['use_indices'])) != len(row['use_indices'])
                or set(row['use_indices']) != uses):
            raise ValueError('review must cover every native observed use of this helper')
        if any(row[field] not in {'local','generalized'} for field in ('domain_scope','premises','conclusion')):
            raise ValueError('demand comparisons must be local or generalized')
        generalized = any(row[f] == 'generalized' for f in ('domain_scope','premises','conclusion'))
        if (generalized and row['generalization'] not in {'induction','reuse'}
                or not generalized and row['generalization'] != 'none'):
            raise ValueError('generalization requires induction or reuse justification')
        if not isinstance(row['reduction'], str) or not row['reduction'].strip():
            raise ValueError('explain which proof burden was reduced')
        if row['burden'] == 'unchanged':
            raise ValueError('unchanged proof burden requires a revised decomposition')
        if key in store.state['proofs']:
            if row['burden'] != 'proved':
                raise ValueError('accepted helpers use burden=proved')
        elif key in children:
            if row['burden'] != 'delegated' or row.get('child_review_id') != _review(store, nodes, proofs, key):
                raise ValueError('delegated burden requires a current reviewed child sketch')
        else:
            if row['burden'] != 'isolated' or ('probe_id' not in row and not getattr(store, 'require_assessment', False)):
                raise ValueError('leaf review requires isolated burden and a native probe')
            probe = _read(store, 'probes', row['probe_id']) if row.get('probe_id') else None
            if probe and not _compatible(store, nodes, key, probe):
                raise ValueError('leaf probe does not match this interface/context')
    if getattr(store, 'require_assessment', False):
        from regen.planning.sketch_assessment import validate_review
        validate_review(store, nodes, proofs, target, reviews)
    record = {'run_id':store.state['graph']['run_id'], 'source_sha256':sha,
              'sketch_sha256':cert['sketch_sha256'], 'receipt_id':receipt_id, 'reviews':reviews,
              'difficulty_certified':False,'judgment_source':'generating-agent', 'publishable':False}
    identity = _save(store, 'reviews', record)
    durable(store.root/'workflow'/'review-index'/(cert['sketch_sha256']+'.json'), {'review_id':identity})
    return {'status':'decomposition_reviewed','review_id':identity,'difficulty_certified':False}


def authorize_reviews(store, nodes, proofs, needed):
    authorized = set()
    for group in load_groups(store):
        if not set(group['helper_ids']) & needed:
            continue
        _review(store, nodes, proofs, group['target_entry_id'])
        authorized.update(group['helper_ids'])
    if needed - authorized:
        raise ValueError('helpers need a current decomposition review')
    for key in needed:
        failure = _pending(store).get(key)
        if failure and not _diagnosis(store, failure):
            raise ValueError('diagnose the failed proof before continuing: ' + failure)


def _pending(store):
    path = store.root/'workflow'/'pending-failures.json'
    return json.loads(path.read_text()) if path.exists() else {}


def _roots(groups, key):
    found = {key}
    while True:
        expanded = found | {g['target_entry_id'] for g in groups if set(g['helper_ids']) & found}
        if expanded == found:
            return found
        found = expanded


def _failure(store, nodes, key, proof, check, evidence_id):
    pending = _pending(store)
    previous_id = pending.get(key)
    previous = _read(store,'failures',previous_id) if previous_id else None
    decision = _diagnosis(store,previous_id) if previous_id else None
    retry_required = bool(previous and proof.strip() == previous['proof'].strip()
                          and (previous.get('local_retry_required')
                               or decision and decision['classification'] in {'script','known_fact'}))
    record = {'run_id':store.state['graph']['run_id'], 'entry':_node(nodes[key]),'proof':proof,
              'check':check,'evidence_id':evidence_id, 'roots':sorted(_roots(load_groups(store), key)),
              'local_retry_required':retry_required}
    if getattr(store,'require_phases',False):
        from regen.planning.sketch_phases import transition_record
        record['phase_failures'] = (previous.get('phase_failures',1)+1 if previous and not transition_record(store,previous_id) else 1)
    identity = _save(store, 'failures', record)
    pending[key] = identity
    durable(store.root/'workflow'/'pending-failures.json', pending)
    return identity


def record_formal_result(store, source, evidence):
    if evidence.get('cached_failure') or evidence['check'].get('failure_scope') == 'native_preparation':
        return
    from regen.planning.theory_document import parse
    entries, proofs = parse(source, store.package, store.imports)
    nodes = {n['id']:n for n in entries}
    pending = _pending(store)
    if evidence['check']['verdict'] == 'green':
        for key in proofs:
            pending.pop(key, None)
        durable(store.root/'workflow'/'pending-failures.json', pending)
    else:
        for row in evidence.get('node_results', []):
            if row['status'] == 'failed' and row['node_id'] in proofs:
                key = row['node_id']
                _failure(store,nodes,key,proofs[key],evidence['check'],digest(evidence))


def record_conditional_result(store, nodes, proofs, target, check, receipt_id, closed):
    """A repaired consumer clears only its earlier conditional-script failure."""
    if check.get('failure_scope') == 'native_preparation':
        return
    if check['verdict'] == 'red':
        _failure(store,nodes,target,proofs[target],check,'sketch:'+receipt_id)
    elif closed:
        pending = _pending(store)
        identity = pending.get(target)
        if identity and _read(store,'failures',identity)['evidence_id'].startswith('sketch:'):
            pending.pop(target)
            durable(store.root/'workflow'/'pending-failures.json',pending)


def _diagnosis(store, failure_id):
    path = store.root/'workflow'/'repair-index'/(failure_id+'.json')
    if not path.exists():
        return None
    return _read(store, 'repairs', json.loads(path.read_text())['repair_id'])


def diagnose_failure(store, failure_id, classification, plan):
    failure = _read(store, 'failures', failure_id)
    if _pending(store).get(failure['entry']['id']) != failure_id:
        raise ValueError('failure is no longer current')
    if classification not in {'script','known_fact','mathematical','candidate_timeout'}:
        raise ValueError('unknown proof failure classification')
    previous = _diagnosis(store,failure_id)
    if (classification in {'mathematical','candidate_timeout'}
            and (failure.get('local_retry_required') or previous
                 and previous['classification'] in {'script','known_fact'})):
        raise ValueError('perform a changed-proof local retry before reclassifying this failure')
    if not isinstance(plan, str) or not plan.strip():
        raise ValueError('a concrete local repair or mathematical diagnosis is required')
    record = {'run_id':store.state['graph']['run_id'], 'failure_id':failure_id,
              'classification':classification,'plan':plan,'judgment_source':'generating-agent'}
    identity = _save(store,'repairs',record)
    durable(store.root/'workflow'/'repair-index'/(failure_id+'.json'), {'repair_id':identity})
    return {'status':'failure_diagnosed','repair_id':identity,
            'local_retry_required':classification in {'script','known_fact'}}


def guard_revision(store, entries, proofs, groups):
    from regen.planning.theory_document import parse
    old, _ = parse(store.path.read_text(), store.package, store.imports)
    previous = {n['id']:n for n in old}
    changed = {n['id'] for n in entries if n['role'] != 'contract'
               and (n['id'] not in previous or _node(n) != _node(previous[n['id']]))}
    # Removing/reassigning a failed helper cannot bypass its original ancestor.
    old_groups = load_groups(store)
    old_members = {g['target_entry_id']:g['helper_ids'] for g in old_groups}
    new_members = {g['target_entry_id']:g['helper_ids'] for g in groups}
    changed.update(k for k in old_members.keys() | new_members.keys()
                   if old_members.get(k) != new_members.get(k))
    changed.update(k for k,n in previous.items() if n['role'] != 'contract'
                   and k not in {e['id'] for e in entries})
    affected = set().union(*(_roots([*old_groups,*groups],k) for k in changed)) if changed else set()
    for key, failure_id in _pending(store).items():
        failure = _read(store,'failures',failure_id)
        if not affected & set(failure['roots']):
            continue
        decision = _diagnosis(store,failure_id)
        if not decision:
            raise ValueError('diagnose failure before revising its decomposition: ' + failure_id)
        if decision['classification'] in {'script','known_fact'} and not (getattr(store,'require_phases',False) and failure.get('phase_failures',1)>=2):
            raise ValueError('attempt the local proof repair before revising this decomposition: ' + key)


def workflow_view(store):
    return {'phased_workflow':getattr(store,'require_phases',False),
            'short_probe_limit':2 if getattr(store,'require_phases',False) else None,
            'escalation_action':'transition_proof' if getattr(store,'require_phases',False) else None,
            'probe_candidate_seconds':PROBE_SECONDS,
            'pending_failures':[{'entry_id':key,'failure_id':identity,
                'diagnosis':_diagnosis(store,identity)} for key,identity in _pending(store).items()],
            'structural_assessment_required':getattr(store, 'require_assessment', False),
            'failure_details_action':'read_failure',
            'difficulty_certified':False}
