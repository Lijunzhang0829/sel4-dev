"""Opt-in, evidence-bound transitions from diagnostic probes to proof work."""
import json
from regen.execution.proof_commits import digest, durable


def enabled(store):
    return getattr(store, 'require_phases', False)


def records(store, kind):
    from regen.planning.sketch_workflow import _read
    return [_read(store,kind,p.stem) for p in (store.root/'workflow'/kind).glob('*.json')]


def budget_key(node):
    # Renaming does not refresh a diagnostic allowance for the same proposition.
    statement=node['statement'].split(':',1)[-1]
    return digest({'statement':statement,'context':node['context']})


def probes(store, node):
    return [r for r in records(store,'probes') if r['entry']['id']==node['id']
            or budget_key(r['entry'])==budget_key(node)]


def transition_record(store, failure_id):
    path=store.root/'workflow/transition-index'/(failure_id+'.json')
    if not path.exists():return None
    from regen.planning.sketch_workflow import _read
    return _read(store,'transitions',json.loads(path.read_text())['transition_id'])


def formal_allowed(store, nodes, key):
    if not enabled(store):return False
    from regen.planning.sketch_workflow import _compatible
    return any(r['decision']=='formal_leaf' and r['entry']['id']==key
               and _compatible(store,nodes,key,r) for r in records(store,'transitions'))


def guard_probe(store,nodes,key,proof):
    if not enabled(store):return
    if formal_allowed(store,nodes,key):
        raise ValueError('leaf is in formal proof phase; use review_sketch and check_theory')
    attempts=probes(store,nodes[key])
    if len(attempts)>=2:
        raise ValueError('short probe allowance exhausted; use transition_proof: formal_leaf or structural escalation')
    if attempts:
        from regen.planning.sketch_workflow import _compatible
        if not any(_compatible(store,nodes,key,r) for r in attempts):
            raise ValueError('probe repair must preserve helper interface/context; escalate the interface instead')
        if any(r['proof']==proof for r in attempts):
            raise ValueError('unchanged probe already has evidence; revise the script or transition')


def guard_attempt(store,nodes,proofs,selected):
    if not enabled(store):return
    from regen.planning.sketch_workflow import _pending,_read
    for key in selected:
        failure_id=_pending(store).get(key)
        if not failure_id:continue
        failure=_read(store,'failures',failure_id)
        transition=transition_record(store,failure_id)
        if transition:
            if transition['entry']!= {k:nodes[key][k] for k in ('id','name','role','statement','context')} or transition['proof']!=proofs.get(key):
                raise ValueError('transition is bound to the saved candidate; register current transition')
        elif failure.get('phase_failures',1)>=2:
            raise ValueError('one local repair already failed; transition_proof required: '+failure_id)
        elif {k:nodes[key][k] for k in ('id','name','role','statement','context')} != failure['entry']:
            raise ValueError('local repair preserves interface; register an interface transition')
        elif proofs.get(key)==failure['proof']:
            raise ValueError('local repair must change the failed script')


def transition_proof(store, sha, failure_id, decision, plan):
    from regen.planning.sketch_workflow import _draft,_read,_pending,_save,_node,_group
    from regen.planning.sketch_check import load_groups,prefix_binding,available_prefix
    if not enabled(store):raise ValueError('phase transitions require sketch-phased-v1')
    entries,nodes,proofs=_draft(store,sha);failure=_read(store,'failures',failure_id)
    key=failure['entry']['id']
    if _pending(store).get(key)!=failure_id or key not in nodes:raise ValueError('transition needs a current failed entry')
    if not isinstance(plan,str) or not plan.strip():raise ValueError('transition needs a concrete plan')
    if decision not in {'formal_leaf','revise_consumer','revise_interface','decompose_helper'}:raise ValueError('invalid transition decision')
    if transition_record(store,failure_id):raise ValueError('failure already has a transition; execute its bound candidate')
    same=_node(nodes[key])==failure['entry']
    changed=proofs.get(key)!=failure['proof']
    groups=load_groups(store)
    if decision=='formal_leaf':
        if nodes[key]['role']=='contract' or any(g['target_entry_id']==key for g in groups):raise ValueError('formal_leaf requires a pending leaf helper')
        if not same:raise ValueError('formal transition preserves the failed helper interface')
        if not probes(store,nodes[key]):raise ValueError('formal transition requires prior probe evidence')
        parents=[g for g in groups if key in g['helper_ids']]
        if not parents:raise ValueError('leaf needs a consumer')
        for g in parents:_group(store,nodes,proofs,g['target_entry_id'])
    elif decision=='revise_consumer':
        if not same or not changed:raise ValueError('consumer escalation must preserve interface and change proof')
    elif decision=='revise_interface':
        if same or nodes[key]['role']=='contract':raise ValueError('interface escalation needs a changed helper interface')
    elif not any(g['target_entry_id']==key for g in groups):
        raise ValueError('decompose_helper requires a saved child sketch')
    record={'run_id':store.state['graph']['run_id'],'failure_id':failure_id,'decision':decision,'plan':plan,
            'entry':_node(nodes[key]),'proof':proofs.get(key),'imports':store.imports,
            'prefix':prefix_binding(available_prefix(store.state,entries,key)),
            'source_sha256':sha,'publishable':False,'difficulty_certified':False}
    identity=_save(store,'transitions',record)
    durable(store.root/'workflow/transition-index'/(failure_id+'.json'),{'transition_id':identity})
    return {'status':'proof_phase_transitioned','transition_id':identity,'decision':decision,
            'new_green':[],'budget_reset':False,'next':'assess_sketch/review_sketch then check_theory' if decision=='formal_leaf' else 'recheck changed consumer/sketch before acceptance'}
