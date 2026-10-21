"""Version-bound conditional helper groups; diagnostic receipts never publish proofs.

Only the trusted renderer introduces temporary admissions, in a disposable native
transaction. Ordinary document parsing and proof acceptance keep their guards.
There is no route search or model-supplied cost/dependency graph here.
"""
from copy import deepcopy
import json
from uuid import uuid4

from regen.execution.proof_commits import digest, durable, freeze_evidence

STRATEGY = 'sketch-groups-v1'


def strategy_options(name):
    if name == 'sketch-phased-v1':
        return {**strategy_options('sketch-guided-v1'), 'require_phases': True}
    result = dict(require_sketch=name in {STRATEGY, 'sketch-local-demands-v1', 'sketch-reviewed-v1', 'sketch-guided-v1'},
                  require_use_sites=name in {'sketch-local-demands-v1', 'sketch-reviewed-v1', 'sketch-guided-v1'},
                  require_review=name in {'sketch-reviewed-v1', 'sketch-guided-v1'})
    if name == 'sketch-guided-v1':
        result['require_assessment'] = True
    return result


def load_groups(store):
    pointer = store.root / 'last-edit.json'
    if not pointer.exists():
        return []
    data = json.loads(pointer.read_text())
    key = data.get('sketch_plan_snapshot')
    if key is None:
        return []  # Historical documents have no sketch metadata.
    if (not isinstance(key, str) or len(key) != 64
            or any(c not in '0123456789abcdef' for c in key)):
        raise ValueError('invalid sketch plan identity')
    snapshot = json.loads((store.root / 'sketch-plans' / (key + '.json')).read_text())
    if digest(snapshot) != key or snapshot['source_sha256'] != data['source_sha256']:
        raise ValueError('sketch plan snapshot binding differs')
    return snapshot['groups']


def merge_groups(store, entries, proofs, updates):
    if not isinstance(updates, list):
        raise ValueError('sketches must be a list of helper groups')
    nodes = {n['id']: n for n in entries}
    positions = {n['id']: i for i, n in enumerate(entries)}
    groups = {g['target_entry_id']: g for g in load_groups(store)
              if g['target_entry_id'] in nodes and all(k in nodes for k in g['helper_ids'])}
    seen = set()
    for group in updates:
        if (not isinstance(group, dict)
                or not {'target_entry_id', 'helper_ids'} <= set(group)
                or set(group) - {'target_entry_id', 'helper_ids', 'use_sites'}):
            raise ValueError('each sketch has target_entry_id, helper_ids and optional use_sites')
        target, helpers = group['target_entry_id'], group['helper_ids']
        if not isinstance(target, str) or target not in nodes or target in seen:
            raise ValueError('sketch targets must be known and unique')
        seen.add(target)
        if (not isinstance(helpers, list) or not helpers
                or any(not isinstance(k, str) or k not in nodes for k in helpers)
                or len(set(helpers)) != len(helpers)):
            raise ValueError('helper_ids must be a nonempty unique list of known IDs')
        if target in store.state['proofs']:
            raise ValueError('a new sketch must serve an unaccepted parent')
        if any(nodes[k]['role'] == 'contract' or positions[k] >= positions[target]
               for k in helpers):
            raise ValueError('sketch helpers must be auxiliaries before their parent')
        if target not in proofs:
            raise ValueError('a sketch requires an executable parent proof body, not oops')
        groups[target] = deepcopy(group)
    for target, group in groups.items():
        if any(nodes[k]['role'] == 'contract' or positions[k] >= positions[target]
               for k in group['helper_ids']):
            raise ValueError('sketch helpers must stay before their parent')
        if group['helper_ids'] != sorted(group['helper_ids'],key=positions.__getitem__):
            raise ValueError('helper_ids must follow their declaration order')
        from regen.planning.helper_use_sites import validate
        if (getattr(store, 'require_use_sites', False) and target not in store.state['proofs']
                and 'use_sites' not in group):
            raise ValueError('local-demands strategy requires use_sites for each pending sketch')
        if target not in store.state['proofs']:
            validate(group, nodes, proofs.get(target, ''))
    if store.require_sketch:
        # Source order already excludes cycles. Require a path to a fixed goal,
        # including nested groups; a free-standing auxiliary cannot authorize itself.
        reachable = {n['id'] for n in entries if n['role'] == 'contract'}
        while True:
            expanded = reachable | {k for t, g in groups.items() if t in reachable
                                    for k in g['helper_ids']}
            if expanded == reachable:
                break
            reachable = expanded
        orphans = {n['id'] for n in entries if n['role'] != 'contract'
                   and n['id'] not in store.state['proofs']} - reachable
        if orphans:
            raise ValueError('new helpers require a parent sketch group: ' + ', '.join(sorted(orphans)))
        for group in groups.values():
            if (group['target_entry_id'] not in store.state['proofs']
                    and group['target_entry_id'] not in proofs):
                raise ValueError('retain each pending sketch consumer script in the draft')
    return list(groups.values())


def interface(nodes, proofs, group):
    fields = ('id', 'name', 'role', 'statement', 'context')
    target = group['target_entry_id']
    result = {'target': {k: nodes[target][k] for k in fields},
            'consumer_proof': proofs.get(target),
            'helpers': [{k: nodes[key][k] for k in fields} for key in group['helper_ids']]}
    if 'use_sites' in group:
        result['use_sites'] = deepcopy(group['use_sites'])
    return result


def prefix_binding(state):
    nodes = {n['id']: n for n in state['graph']['nodes']}
    return [{'entry': {k: nodes[key][k] for k in ('id', 'name', 'role', 'statement', 'context')},
             'proof': state['proofs'][key]} for key in state['accepted_order']]


def available_prefix(state, entries, target):
    """Only an unchanged committed prefix located before this consumer is legal."""
    positions={n['id']:i for i,n in enumerate(entries)}
    current={n['id']:n for n in entries}
    original={n['id']:n for n in state['graph']['nodes']}
    prefix=[]
    previous=-1
    for key in state['accepted_order']:
        if (key not in positions or positions[key]>=positions[target] or positions[key]<=previous
                or any(current[key][f]!=original[key][f]
                       for f in ('name','role','statement','context'))):
            break
        prefix.append(key)
        previous=positions[key]
    base=deepcopy(state)
    base['accepted_order']=prefix
    base['proofs']={key:state['proofs'][key] for key in prefix}
    base['graph']['accepted_order']=prefix
    return base


def certificate(store, nodes, proofs, group):
    if getattr(store, 'require_use_sites', False) and 'use_sites' not in group:
        return None
    signature = digest(interface(nodes, proofs, group))
    path = store.root / 'sketch-certificates' / (signature + '.json')
    if not path.exists():
        return None
    record = json.loads(path.read_text())
    checksum = record.pop('receipt_sha256', None)
    if digest(record) != checksum or record.get('sketch_sha256') != signature:
        raise ValueError('sketch certificate integrity mismatch')
    current_prefix = prefix_binding(available_prefix(store.state,list(nodes.values()),group['target_entry_id']))
    if (record['imports'] != store.imports
            or record.get('run_id') != store.state['graph']['run_id']
            or current_prefix[:len(record['prefix'])] != record['prefix']):
        return None
    check = record['check']
    marker = 'REGEN_SKETCH_CLOSED ' + signature
    if (record['status'] != 'conditional_closed'
            or check['verdict'] != 'green' or check['returncode'] != 0
            or marker not in check['log_content'].splitlines()):
        return None
    for field in ('source', 'log'):
        from regen.planning.theory_document import source_hash
        if source_hash(check[field + '_content']) != check[field + '_sha256']:
            raise ValueError('sketch native evidence bytes mismatch')
    if digest(record['interface']) != signature:
        raise ValueError('sketch interface differs from certificate')
    if 'use_sites' in group:
        from regen.planning.helper_use_sites import observations
        uses = observations(check['log_content'], group, signature)
        if (record.get('helper_uses') != uses
                or len(uses) != len(group['use_sites'])
                or {u['index'] for u in uses} != set(range(len(group['use_sites'])))):
            raise ValueError('sketch native use-site evidence differs')
    historical = deepcopy(store.state)
    historical['accepted_order'] = [r['entry']['id'] for r in record['prefix']]
    historical['proofs'] = {r['entry']['id']: r['proof'] for r in record['prefix']}
    historical['graph']['accepted_order'] = historical['accepted_order']
    historical['graph']['execution_order'] = [*historical['accepted_order'],
        *(k for k in historical['graph']['execution_order'] if k not in historical['proofs'])]
    expected, _, _ = render_check(historical, nodes, proofs, group, store.imports, signature)
    if check['source_content'] != expected:
        raise ValueError('sketch native source differs from exact conditional rendering')
    return record


def group_view(store, entries, proofs):
    nodes = {n['id']: n for n in entries}
    result = []
    for group in load_groups(store):
        if group['target_entry_id'] not in nodes or any(k not in nodes for k in group['helper_ids']):
            continue
        cert = certificate(store, nodes, proofs, group)
        from regen.planning.helper_use_sites import coverage
        result.append({**group, 'sketch_sha256': digest(interface(nodes, proofs, group)),
                       'status': 'conditional_closed' if cert else 'needs_sketch_check',
                       'helper_use_coverage': coverage(group, cert.get('helper_uses', []) if cert else []),
                       'unproved_helper_ids': [k for k in group['helper_ids'] if k not in store.state['proofs']]})
    return result


def authorize(store, source, entry_ids=None):
    """Gate native proof attempts, even if the model ignores the workflow prompt."""
    if not store.require_sketch:
        return []
    from regen.planning.theory_document import parse
    entries, proofs = parse(source, store.package, store.imports)
    nodes = {n['id']: n for n in entries}
    selected = set(proofs) if entry_ids is None else set(entry_ids)
    from regen.planning.sketch_phases import guard_attempt
    guard_attempt(store,nodes,proofs,selected - set(store.state['proofs']))
    needed = {k for k in selected if k not in store.state['proofs'] and nodes[k]['role'] != 'contract'}
    authorized, receipts = set(), []
    for group in load_groups(store):
        if (group['target_entry_id'] not in nodes
                or any(k not in nodes for k in group['helper_ids'])):
            continue
        cert = certificate(store, nodes, proofs, group)
        if cert:
            authorized.update(group['helper_ids'])
            receipts.append(cert['sketch_sha256'])
    if needed - authorized:
        raise ValueError('helpers need a successful current check_sketch before proving: '
                         + ', '.join(sorted(needed - authorized)))
    if getattr(store, 'require_review', False):
        from regen.planning.sketch_workflow import authorize_reviews
        authorize_reviews(store, nodes, proofs, needed)
    return receipts


def render_check(state, nodes, proofs, group, imports, signature):
    from regen.execution.proof_batches import _render, _safe_text
    from regen.execution.isabelle_node_checker import wrap_context
    label = 'Batch_Check_' + digest(imports)[:16]
    prefix_source = _render(state['graph'], state['proofs'], imports, label)
    target = group['target_entry_id']
    if target in state['proofs'] or target not in proofs:
        raise ValueError('check_sketch requires an unaccepted parent with a proof body')
    if set(group['helper_ids']) & {target}:
        raise ValueError('a sketch cannot assume its own parent')
    delta = []
    admitted = []
    for key in group['helper_ids']:
        if key in state['proofs']:
            continue
        node = nodes[key]
        _safe_text(node['statement'], statement=True)
        # This sole admission is harness-authored and never written to draft.thy.
        delta.append(wrap_context(node['context'], node['statement'] + '\nsorry'))
        admitted.append(node['name'])
    node = nodes[target]
    _safe_text(node['statement'], statement=True)
    _safe_text(proofs[target])
    from regen.planning.helper_use_sites import instrument
    delta.append(wrap_context(node['context'], node['statement'] + '\n' +
                              instrument(proofs[target], group, signature)))
    audit = '''ML \\<open> let
      val regen_sketch_thms = Global_Theory.get_thms (Proof_Context.theory_of \\<^context>) %s;
      val _ = if null regen_sketch_thms then error "Sketch has no result" else ();
      val _ = Thm.consolidate regen_sketch_thms;
      val _ = if forall (fn ((name, _), _) => name = "Pure.skip_proof")
          (Thm_Deps.all_oracles regen_sketch_thms) then () else error "Unexpected sketch oracle";
    in writeln %s end
    \\<close>''' % (json.dumps(node['name']), json.dumps('REGEN_SKETCH_CLOSED ' + signature))
    delta.append(audit)
    source = prefix_source[:-len('end\n')] + '\n'.join(delta) + '\nend\n'
    return source, prefix_source, admitted


def check_groups(checker, store, source_sha256, target_entry_ids=None):
    from regen.planning.theory_document import parse
    source = store.checked_source(source_sha256)
    entries, proofs = parse(source, store.package, store.imports)
    nodes = {n['id']: n for n in entries}
    groups = load_groups(store)
    if target_entry_ids is not None:
        if (not isinstance(target_entry_ids, list) or not target_entry_ids
                or any(not isinstance(k, str) for k in target_entry_ids)
                or len(set(target_entry_ids)) != len(target_entry_ids)
                or set(target_entry_ids) - {g['target_entry_id'] for g in groups}):
            raise ValueError('target_entry_ids must select known unique sketch groups')
        groups = [g for g in groups if g['target_entry_id'] in target_entry_ids]
    else:
        groups = [g for g in groups if g['target_entry_id'] not in store.state['proofs']
                  or any(k not in store.state['proofs'] for k in g['helper_ids'])]
    if not groups:
        raise ValueError('no pending sketch groups to check')
    results = []
    for group in groups:
        from regen.planning.sketch_phases import guard_attempt
        guard_attempt(store,nodes,proofs,[group['target_entry_id']])
        from regen.execution.check_tasks import check_cancelled
        check_cancelled()
        from regen.planning.helper_use_sites import validate
        if getattr(store, 'require_use_sites', False) and 'use_sites' not in group:
            raise ValueError('local-demands strategy requires use_sites before check_sketch')
        validate(group, nodes, proofs.get(group['target_entry_id'], ''))
        prior = certificate(store, nodes, proofs, group)
        if prior is not None:
            if getattr(store, 'require_review', False):
                from regen.planning.sketch_workflow import record_conditional_result
                record_conditional_result(store,nodes,proofs,group['target_entry_id'],
                                          prior['check'],prior['receipt_id'],True)
            from regen.planning.helper_use_sites import coverage
            results.append({**group, 'status':'conditional_closed',
                'sketch_sha256':prior['sketch_sha256'], 'check':brief_check(prior['check']),
                'receipt_id':prior['receipt_id'],
                'helper_use_coverage': coverage(group, prior.get('helper_uses', [])),
                'reused_sketch_check':True})
            continue
        signature = digest(interface(nodes, proofs, group))
        base=available_prefix(store.state,entries,group['target_entry_id'])
        accepted={n['id']:n['name'] for n in base['graph']['nodes']}
        checker._sketch_prefix_names=[accepted[k] for k in base['accepted_order']]
        generated, prefix, admitted = render_check(base, nodes, proofs, group, store.imports, signature)
        check = freeze_evidence(checker.check_sketch_source(generated, prefix, admitted))
        closed = (check.get('verdict') == 'green' and check.get('returncode') == 0
                  and 'REGEN_SKETCH_CLOSED ' + signature in check['log_content'].splitlines())
        from regen.planning.helper_use_sites import observations, coverage
        uses = observations(check['log_content'], group, signature)
        if 'use_sites' in group and (len(uses) != len(group['use_sites'])
                or {u['index'] for u in uses} != set(range(len(group['use_sites'])))):
            closed = False
        receipt_id=uuid4().hex
        record = {'status': 'conditional_closed' if closed else 'sketch_rejected',
                  'receipt_id':receipt_id, 'run_id':store.state['graph']['run_id'],
                  'evidence_kind': 'conditional-sketch-v1', 'publishable': False,
                  'source_sha256': source_sha256, 'sketch_sha256': signature,
                  'interface': interface(nodes, proofs, group), 'imports': store.imports,
                  'prefix': prefix_binding(base), 'group': group,
                  'temporarily_admitted_names': admitted, 'check': check}
        if 'use_sites' in group:
            record['helper_uses'] = uses
        receipt = {**record, 'receipt_sha256': digest(record)}
        durable(store.root / 'sketch-checks' / receipt_id / 'receipt.json', receipt, immutable=True)
        if getattr(store, 'require_review', False):
            from regen.planning.sketch_workflow import record_conditional_result
            record_conditional_result(store,nodes,proofs,group['target_entry_id'],check,receipt_id,closed)
        if closed:
            durable(store.root / 'sketch-certificates' / (signature + '.json'), receipt)
        results.append({**group, 'status': record['status'], 'sketch_sha256': signature,
                        'receipt_id':receipt_id, 'check':brief_check(check),
                        'helper_use_coverage': coverage(group, uses)})
    return {'status': 'conditional_closed' if all(r['status'] == 'conditional_closed' for r in results)
            else 'sketch_rejected', 'publishable': False, 'groups': results, 'new_green': []}


def brief_check(check):
    fields=('verdict','returncode','diagnostic','wall_ms','check_scope','incremental',
            'native_proof_state_full')
    return {key:check[key] for key in fields if key in check}


def read_receipt(store, receipt_id, view='summary'):
    from regen.planning.helper_use_sites import coverage
    if (not isinstance(receipt_id,str) or len(receipt_id)!=32
            or any(c not in '0123456789abcdef' for c in receipt_id)):
        raise ValueError('receipt_id must be a local sketch receipt identity')
    if view not in {'summary','full'}:
        raise ValueError('sketch receipt view must be summary or full')
    record=json.loads((store.root/'sketch-checks'/receipt_id/'receipt.json').read_text())
    checksum=record.pop('receipt_sha256',None)
    if (digest(record)!=checksum or record['receipt_id']!=receipt_id
            or record['run_id']!=store.state['graph']['run_id']):
        raise ValueError('sketch receipt binding mismatch')
    if view=='full':
        return record
    return {key:record[key] for key in ('receipt_id','status','publishable','group','source_sha256','sketch_sha256')} | {
        'check':brief_check(record['check']), 'full_available':True,
        'helper_use_coverage': coverage(record['group'], record.get('helper_uses', []))}
