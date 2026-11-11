"""Read-only, actionable sketch gates; diagnostic views never authorize a proof.

The admission helper calls the ordinary singleton authorization path. Only known
workflow gates become local blocked results; corrupt receipts and malformed
source remain errors for the enclosing operation.
"""
from copy import deepcopy
import json

from regen.planning.sketch_check import (authorize, certificate, certificate_status,
    load_groups)
from regen.execution.proof_commits import digest

POLICY = 'sketch-readiness-v1'

_GATE_PREFIXES = (
    ('helpers need a successful current check_sketch', 'conditional_check_required'),
    ('review requires a current conditionally closed sketch', 'conditional_check_required'),
    ('sketch needs a decomposition review', 'review_missing'),
    ('stale decomposition review', 'review_receipt_changed'),
    ('child decomposition review changed', 'child_review_changed'),
    ('helper now has a child decomposition', 'child_review_required'),
    ('helper is now a leaf', 'leaf_review_required'),
    ('leaf probe uses an old audit policy', 'probe_audit_policy_changed'),
    ('leaf probe is stale', 'probe_context_changed'),
    ('helpers need a current decomposition review', 'review_missing'),
    ('diagnose the failed proof before continuing', 'diagnosis_required'),
    ('risky leaf needs a proved native probe', 'structural_risk_unresolved'),
    ('sketch needs a current structural assessment', 'assessment_required'),
    ('structural assessment context changed', 'assessment_required'),
    ('sketch needs a native structural assessment', 'assessment_required'),
    ('stale structural assessment', 'assessment_required'),
    ('child structural assessment changed', 'assessment_required'),
    ('a failed diagnostic needs', 'phase_transition_required'),
    ('failed formal proof needs', 'phase_transition_required'),
    ('formal proof requires', 'formal_plan_required'),
    ('semantic-critical helper must be proved first', 'critical_helper_pending'),
    ('one local repair already failed; transition_proof required', 'phase_transition_required'),
    ('transition is bound to the saved candidate', 'phase_transition_refresh_required'),
    ('local repair preserves interface', 'interface_transition_required'),
    ('local repair must change the failed script', 'local_proof_repair_required'),
)


def workflow_block_reason(error):
    """Return a workflow code only for expected gates, never integrity failures."""
    from regen.planning.sketch_semantics import WorkflowBlocked
    if isinstance(error, WorkflowBlocked) or getattr(error, 'workflow_blocked', False) is True:
        return getattr(error, 'code', 'workflow_prerequisite_required')
    if type(error) is not ValueError:
        return None
    message = str(error)
    return next((code for prefix,code in _GATE_PREFIXES if message.startswith(prefix)), None)


def _action(action, *, requires=(), **fields):
    return {'request':{'action':action, **fields}, 'requires_agent_fields':list(requires)}


def _context(store, source=None):
    from regen.planning.theory_document import parse, source_hash
    source = store.path.read_text() if source is None else source
    entries,proofs = parse(source, store.package, store.imports)
    return source,source_hash(source),entries,{n['id']:n for n in entries},proofs,load_groups(store)


def _check_action(sha, target):
    return _action('check_sketch',source_sha256=sha,target_entry_ids=[target])


def _review_action(sha,target,cert):
    return _action('review_sketch',source_sha256=sha,target_entry_id=target,
                   receipt_id=cert['receipt_id'], requires=['reviews'])


def route_options(store, target, sha):
    """Existing actions only; these alternatives neither edit nor authorize proofs."""
    return {'difficulty_certified':False,
        'notice':'Conditional closure proves composition under the helper assumptions, not '
                 'reduced proof cost. The File Planner may continue, revise, or withdraw this route. '
                 'Diagnose any pending failure before changing its route; fixed goals, accepted '
                 'proofs and charged costs are preserved.',
        'continue_helpers':_action('check_theory',source_sha256=sha,requires=['entry_ids']),
        'revise':_action('edit_theory',base_sha256=sha,requires=['edits','sketches']),
        'withdraw':{**_action('edit_theory',base_sha256=sha,
            sketches=[{'target_entry_id':target,'helper_ids':[]}],requires=['edits']),
            'edit_requirements':'In the same edit replace the consumer with an independent proof '
                'or oops and remove unused unproved helpers. Shared helpers need another retained '
                'group. Keep all accepted declarations and proofs. Saving grants no GREEN.'}}


def _leaf_diagnostics(store,nodes,group,sha):
    from regen.planning.sketch_workflow import _read,_compatible,PROBE_AUDIT_POLICY
    children={g['target_entry_id'] for g in load_groups(store)}
    records=[]
    for path in (store.root/'workflow/probes').glob('*.json'):
        record=_read(store,'probes',path.stem)
        records.append((path.stat().st_mtime_ns,path.stem,record))
    leaves=[];actions=[];reasons=[]
    for key in group['helper_ids']:
        if key in store.state['proofs'] or key in children:continue
        compatible=[r for r in records if r[2]['entry']['id']==key
                    and r[2].get('audit_policy')==PROBE_AUDIT_POLICY
                    and _compatible(store,nodes,key,r[2])]
        if compatible:
            _,identity,record=max(compatible,key=lambda item:(item[0],item[1]))
            leaves.append({'entry_id':key,'status':record['status'],'probe_id':identity,
                           'feasibility':'proved_in_probe' if record['status']=='proved_in_probe' else 'unestablished'})
        else:
            code='probe_or_formal_plan_required' if getattr(store,'allow_formal_leaf',False) else 'probe_missing'
            reasons.append({'code':code,'entry_id':key})
            leaves.append({'entry_id':key,'status':'not_probed','feasibility':'unestablished',
                           'formal_plan_available':getattr(store,'allow_formal_leaf',False)})
            action=_action('probe_helpers',source_sha256=sha,entry_ids=[key])
            if getattr(store,'allow_formal_leaf',False):
                action['optional']=True
                action['alternative']='Supply proof_mode=formal and proof_plan in review_sketch.'
            actions.append(action)
    return leaves,reasons,actions


def _review_state(store,nodes,proofs,group,cert,sha):
    """Explain current review bindings, retaining ordinary reviewer validation."""
    from regen.planning.sketch_workflow import _read, _review, leaf_review_issue
    from regen.planning.sketch_semantics import feasibility
    target=group['target_entry_id']
    pointer=store.root/'workflow/review-index'/(cert['sketch_sha256']+'.json')
    if not pointer.exists():
        leaves,reasons,actions=_leaf_diagnostics(store,nodes,group,sha)
        children={g['target_entry_id']:g for g in load_groups(store)}
        for key in group['helper_ids']:
            if key in store.state['proofs'] or key not in children:continue
            child=_group_state(store,sha,list(nodes.values()),nodes,proofs,children[key])
            if child['status']!='ready':
                reasons.append({'code':'child_review_blocked','entry_id':key})
                actions.extend(child['next_actions'])
        return {'status':'needs_review','reasons':[{'code':'review_missing'},*reasons],
                'leaf_diagnostics':leaves,'semantic_claims_verified':False,
                'next_actions':[*actions,_review_action(sha,target,cert)]}
    identity=json.loads(pointer.read_text())['review_id']
    record=_read(store,'reviews',identity)
    reasons=[];actions=[];current_feasibility={}
    if record.get('receipt_id')!=cert['receipt_id']:
        reasons.append({'code':'review_receipt_changed'})
    children={g['target_entry_id']:g for g in load_groups(store)}
    for row in record['reviews']:
        key=row['helper_id']
        current_feasibility[key]='unestablished'
        if key in store.state['proofs']:
            current_feasibility[key]=feasibility(store,row)
            continue
        if key in children:
            try: child_id=_review(store,nodes,proofs,key)
            except ValueError as error:
                code=workflow_block_reason(error)
                if code is None:raise
                reasons.append({'code':'child_review_blocked','entry_id':key,'cause':code})
                child_cert=certificate(store,nodes,proofs,children[key])
                actions.append(_review_action(sha,key,child_cert) if child_cert else _check_action(sha,key))
            else:
                if row.get('child_review_id')!=child_id:
                    reasons.append({'code':'child_review_changed','entry_id':key})
        else:
            issue=leaf_review_issue(store,nodes,row)
            if issue is None:
                current_feasibility[key]=feasibility(store,row)
                continue
            reasons.append({'code':issue['code'],'entry_id':key})
            if issue['code']!='leaf_review_required':
                actions.append(_action('probe_helpers',source_sha256=sha,entry_ids=[key]))
    if not reasons:
        try:_review(store,nodes,proofs,target)
        except ValueError as error:
            code=workflow_block_reason(error)
            if code is None:raise
            reasons.append({'code':code})
    if reasons:actions.append(_review_action(sha,target,cert))
    return {'status':'needs_review' if reasons else 'valid','review_id':identity,
            'reasons':reasons,'next_actions':actions,
            'helper_feasibility':current_feasibility,
            'review_helper_feasibility':deepcopy(record.get('helper_feasibility',{})),
            'retained_burden_helper_ids':list(record.get('retained_burden_helper_ids',[])),
            'helper_reviews':[{k:deepcopy(row[k]) for k in ('helper_id','helper_role','proof_mode') if k in row}
                              for row in record['reviews']],
            'semantic_claims_verified':False}


def _group_state(store,sha,entries,nodes,proofs,group):
    target=group['target_entry_id']
    children={g['target_entry_id'] for g in load_groups(store)}
    leaves=[key for key in group['helper_ids'] if key not in children and key not in store.state['proofs']]
    if target in store.state['proofs']:
        return {'target_entry_id':target,'status':'accepted','reasons':[],
                'next_actions':[],'valid_leaf_ids':[]}
    info=certificate_status(store,nodes,proofs,group)
    if info['status']!='valid':
        return {'target_entry_id':target,'status':'needs_sketch_check',
                'certificate':info,'reasons':info['reasons'],'valid_leaf_ids':[],
                'route_options':route_options(store,target,sha),
                'next_actions':[_check_action(sha,target)]}
    cert=certificate(store,nodes,proofs,group)
    review=(_review_state(store,nodes,proofs,group,cert,sha) if store.require_review else
            {'status':'not_required','reasons':[],'next_actions':[]})
    return {'target_entry_id':target,'status':'ready' if review['status'] in {'valid','not_required'} else 'needs_review',
            'certificate':info,'review':review,'reasons':review['reasons'],
            'route_options':route_options(store,target,sha),
            'valid_leaf_ids':leaves,'next_actions':review['next_actions']}


def _blocked_details(store, source, entry_id, code, diagnostic, context=None, group_states=None):
    context=_context(store,source) if context is None else context
    _,sha,entries,nodes,proofs,groups=context
    reasons=[];actions=[]
    if entry_id not in nodes:raise ValueError('unknown document entry: '+str(entry_id))
    for group in groups:
        if entry_id not in group['helper_ids']:continue
        info=(group_states[group['target_entry_id']] if group_states is not None else
              _group_state(store,sha,entries,nodes,proofs,group))
        reasons.extend({**r,'target_entry_id':group['target_entry_id']} for r in info['reasons'])
        actions.extend(info['next_actions'])
    from regen.planning.sketch_workflow import _pending,_diagnosis
    failure=_pending(store).get(entry_id)
    if failure and not _diagnosis(store,failure):
        reasons.append({'code':'diagnosis_required','entry_id':entry_id,'failure_id':failure})
        actions.append(_action('diagnose_failure',failure_id=failure,requires=['classification','plan']))
    if not reasons:reasons=[{'code':code,'entry_id':entry_id}]
    if code=='assessment_required':
        target=next((g['target_entry_id'] for g in groups if entry_id in g['helper_ids']),None)
        if target is not None:actions.insert(0,_action('assess_sketch',source_sha256=sha,target_entry_ids=[target]))
    if code in {'phase_transition_required','phase_transition_refresh_required','interface_transition_required'} and failure:
        actions.insert(0,_action('transition_proof',source_sha256=sha,failure_id=failure,requires=['decision','plan']))
    elif code=='local_proof_repair_required':
        actions.insert(0,_action('edit_proof',entry_id=entry_id,expected_hash=sha,requires=['proof_body']))
    if not actions:
        actions=[_action('read_entry',entry_id=entry_id)]
    # Deduplicate navigation; never auto-fill a mathematical review/plan.
    unique=[];seen=set()
    for action in actions:
        signature=digest(action)
        if signature not in seen:seen.add(signature);unique.append(action)
    return {'entry_id':entry_id,'status':'blocked_workflow','diagnostic':diagnostic,
            'missing_prerequisites':reasons,'next_actions':unique,
            'source_sha256':sha,'proof_authority':False}


def entry_authorization(store, source, entry_id, *, _context_value=None, _group_states=None):
    """Singleton authorization with local workflow gates and fail-closed errors."""
    context=_context(store,source) if _context_value is None else _context_value
    if entry_id not in context[3]:raise ValueError('unknown document entry: '+str(entry_id))
    try:receipts=authorize(store,source,[entry_id])
    except ValueError as error:
        code=workflow_block_reason(error)
        if code is None:raise
        result=_blocked_details(store,source,entry_id,code,str(error),context,_group_states)
        if getattr(error,'details',None):result['gate_details']=deepcopy(error.details)
        if getattr(error,'next_actions',None):result['next_actions']=deepcopy(error.next_actions)
        return result
    return {'entry_id':entry_id,'status':'ready','sketch_authorizations':receipts,
            'missing_prerequisites':[],'next_actions':[], 'source_sha256':context[1],
            'proof_authority':False}


def entry_readiness(store, entry_id, *, _context_value=None, _group_states=None):
    context=_context(store) if _context_value is None else _context_value
    source,sha,entries,nodes,proofs,groups=context
    if entry_id not in nodes:raise ValueError('unknown document entry: '+str(entry_id))
    if entry_id in store.state['proofs'] and proofs.get(entry_id)==store.state['proofs'][entry_id]:
        return {'entry_id':entry_id,'status':'accepted','source_sha256':sha,
                'missing_prerequisites':[],'next_actions':[],'proof_authority':False}
    if nodes[entry_id]['role']=='contract' and not getattr(store,'require_phases',False):
        result={'entry_id':entry_id,'status':'ready','sketch_authorizations':[],
                'missing_prerequisites':[],'next_actions':[],'source_sha256':sha,'proof_authority':False}
    else:
        result=entry_authorization(store,source,entry_id,_context_value=context,_group_states=_group_states)
    if entry_id not in proofs:
        result['proof_slot_open']=True
        if result['status']=='ready':
            result['next_actions'].insert(0,_action('edit_proof',entry_id=entry_id,
                expected_hash=sha,requires=['proof_body']))
            result['status']='open_proof_slot'
    return result


def readiness_view(store):
    context=_context(store)
    source,sha,entries,nodes,proofs,groups=context
    states={g['target_entry_id']:_group_state(store,sha,entries,nodes,proofs,g) for g in groups}
    return {'policy':POLICY,'source_sha256':sha,'proof_authority':False,
            'groups':list(states.values()),
            'entries':{key:entry_readiness(store,key,_context_value=context,_group_states=states) for key in nodes},
            'notice':'Read-only workflow navigation. Complete native checking and ordinary acceptance are still required. '
                     'next_actions.request is executable only when requires_agent_fields is empty.'}
