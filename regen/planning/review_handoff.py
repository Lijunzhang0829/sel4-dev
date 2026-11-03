"""Plan review obligations and explicit File Planner decisions, never proof evidence."""
from copy import deepcopy
from pathlib import Path
import threading

from regen.execution.proof_commits import digest
from regen.infrastructure.experiment_ledger import Ledger

_LOCK = threading.RLock()


def validate_handoff(handoff, plan):
    from regen.planning.plan_review import digest as plan_digest
    if (not isinstance(handoff,dict) or handoff.get('schema_version') != 1
            or handoff.get('policy') != 'review-obligations-v1'
            or handoff.get('plan_sha256') != plan_digest(plan)
            or set(handoff.get('theories',{})) != {t['name'] for t in plan['theories']}):
        raise ValueError('review handoff differs from frozen plan')
    for name, row in handoff['theories'].items():
        if not isinstance(row.get('assessment'),str) or not isinstance(row.get('obligations'),list):
            raise ValueError('invalid per-theory review handoff')
        ids = [o.get('issue_id') for o in row['obligations']]
        if (not all(isinstance(i,str) and i for i in ids) or len(set(ids))!=len(ids)
                or any(name not in o.get('issue',{}).get('theories',[])
                       and not set(o.get('issue',{}).get('contract_ids',[])) &
                           {c['contract_id'] for t in plan['theories'] if t['name']==name
                            for c in t.get('contracts',[])} for o in row['obligations'])):
            raise ValueError('invalid review obligation ownership')
    return True


def install_review_handoff(controller, handoff, *, plan, authorization):
    """Install missing legacy review context without rewriting any proof package."""
    validate_handoff(handoff,plan)
    if not isinstance(authorization,str) or not authorization.strip():
        raise ValueError('legacy handoff needs explicit recovery authorization')
    if set(handoff['theories']) != set(controller.shared.entries):
        raise ValueError('handoff theory set differs from run')
    for theory in controller.shared.entries:
        package,_ = controller._state(theory)
        if package['plan_id'] != plan['plan_id']:
            raise ValueError('handoff plan identity differs from run')
        expected = next(t for t in plan['theories'] if t['name']==theory)
        ids = {c['contract_id'] for c in package['contracts']}
        expected_ids = {c['contract_id'] for c in expected.get('contracts',[])}
        if ids != expected_ids or package['contracts'] != expected.get('contracts',[]):
            raise ValueError('handoff contract declarations/coverage differ from run')
    with _LOCK:
        ledger = Ledger(Path(controller.root)/'review-handoff',controller.run['run_id'])
        if ledger.rows:
            raise ValueError('review handoff already installed')
        return ledger.append('review_handoff_installed',handoff=handoff,
            run_sha256=digest(controller.run),authorization=authorization,
            accepted_state_preserved=True)


def _handoff(controller,theory):
    package,_ = controller._state(theory)
    embedded = package.get('review_obligations')
    ledger = Ledger(Path(controller.root)/'review-handoff',controller.run['run_id'])
    if ledger.rows:
        if len(ledger.rows)!=1 or ledger.rows[0]['kind']!='review_handoff_installed':
            raise ValueError('invalid review handoff ledger')
        data=ledger.rows[0]['data']
        if data['run_sha256'] != digest(controller.run):
            raise ValueError('review handoff run binding changed')
        if embedded is not None:
            raise ValueError('duplicate embedded and installed review handoff')
        embedded=data['handoff']['theories'][theory]
    return deepcopy(embedded or {'assessment':'','obligations':[]})


def _ledger(controller,theory):
    return Ledger(Path(controller.root)/'review-decisions'/digest(theory),controller.run['run_id'])


def review_context(controller,theory=None):
    theory=theory or controller.theory
    handoff=_handoff(controller,theory)
    package,state=controller._state(theory)
    latest={}
    for row in _ledger(controller,theory).rows:
        if row['kind']!='review_decision':
            raise ValueError('invalid review decision ledger')
        if row['data']['handoff_sha256']!=digest(handoff):
            raise ValueError('review decision handoff drift')
        latest[row['data']['issue_id']]={**row['data'],'receipt_id':row['event_id']}
    for issue in handoff['obligations']:
        issue['generation_decision']=latest.get(issue['issue_id'])
        ids=set(issue['issue'].get('contract_ids',[])) & {c['contract_id'] for c in package['contracts']}
        issue['pending_contract_ids']=sorted(ids-set(state.get('proofs',{})))
        # A planning local/retain decision still carries a task, not GREEN.
        decision=issue['generation_decision']
        waiting_again=False
        if decision and decision['decision']=='wait':
            from regen.planning.provider_waits import current_wait
            waiting_again=current_wait(controller,theory) is None
        issue['requires_generation_decision']=bool(ids) and (decision is None or waiting_again)
    return handoff


def require_review_decisions(controller):
    missing=[i['issue_id'] for i in review_context(controller)['obligations']
             if i['requires_generation_decision'] and i['pending_contract_ids']]
    if missing:
        raise ValueError('record_review_decision required for pending review obligations: '+', '.join(missing))


def record_review_decision(controller,proposal,metadata):
    from regen.planning.theory_outcomes import _entry_ids, _snapshot
    if set(proposal)-{'action','issue_id','decision','reason','entry_ids','obligations','providers'}:
        raise ValueError('unknown review decision fields')
    context=review_context(controller)
    issue=next((i for i in context['obligations'] if i['issue_id']==proposal.get('issue_id')),None)
    if issue is None:
        raise ValueError('unknown assigned review issue')
    choice=proposal.get('decision')
    if choice not in {'import','local','retain','wait'}:
        raise ValueError('review decision must be import/local/retain/wait')
    obligations=proposal.get('obligations')
    if (not isinstance(proposal.get('reason'),str) or not proposal['reason'].strip()
            or not isinstance(obligations,list) or not obligations
            or not all(isinstance(o,str) and o.strip() for o in obligations)):
        raise ValueError('decision requires concrete reason and remaining proof obligations')
    package,state,binding=_snapshot(controller)
    ids=_entry_ids(package,state,proposal.get('entry_ids'))
    relevant=set(issue['issue'].get('contract_ids',[])) & {c['contract_id'] for c in package['contracts']}
    pending=relevant-set(state.get('proofs',{}))
    if not ids or set(ids)!=pending:
        raise ValueError('review decision must cover all of its own pending fixed contracts')
    providers=proposal.get('providers',[])
    if (not isinstance(providers,list) or not all(isinstance(p,str) for p in providers)
            or len(set(providers))!=len(providers)
            or any(p==controller.theory or p not in controller.shared.entries for p in providers)):
        raise ValueError('invalid review provider selection')
    if choice=='import':
        from regen.execution.checker_policy import exports_available
        visible=set();todo=list(controller._effective_providers(package,state))
        while todo:
            provider=todo.pop()
            if provider in visible:
                continue
            p,s=controller._state(provider)
            if not exports_available(s['status']):
                raise ValueError('import decision requires certified provider exports')
            visible.add(provider);todo.extend(controller._effective_providers(p,s))
        if not providers or not set(providers)<=visible:
            raise ValueError('import decision requires already accepted provider imports')
    if choice=='wait':
        from regen.planning.provider_waits import current_wait
        wait=current_wait(controller)
        if not wait or not providers or not set(providers)<={p['theory'] for p in wait['providers']}:
            raise ValueError('wait decision requires a registered provider wait')
    with _LOCK:
        row=_ledger(controller,controller.theory).append('review_decision',
            handoff_sha256=digest(_handoff(controller,controller.theory)),issue_id=issue['issue_id'],
            decision=choice,reason=proposal['reason'],entry_ids=ids,obligations=obligations,
            providers=providers,binding=binding,metadata=deepcopy(metadata),positive_proofs_added=0)
    return {'status':'recorded','decision':row['data'],'receipt_id':row['event_id'],
            'notice':'This allocates proof work; it does not prove the obligations or import facts.'}


def validate_wait_issues(controller,issues,providers,entry_ids):
    if not isinstance(issues,list) or not all(isinstance(i,str) for i in issues) or len(set(issues))!=len(issues):
        raise ValueError('review_issue_ids must be distinct IDs')
    known={i['issue_id']:i for i in review_context(controller)['obligations']}
    for identity in issues:
        if identity not in known or not set(providers)&set(known[identity]['issue'].get('theories',[])):
            raise ValueError('provider wait must match the assigned review issue')
        if not set(known[identity]['pending_contract_ids'])<=set(entry_ids):
            raise ValueError('review wait must cover all affected pending contracts')
    return issues


def record_wait_decisions(controller,wait):
    context=review_context(controller)
    for identity in wait['review_issue_ids']:
        issue=next(i for i in context['obligations'] if i['issue_id']==identity)
        ids=sorted(set(wait['entry_ids']) & set(issue['issue'].get('contract_ids',[])))
        if not ids:
            continue
        record_review_decision(controller,{'action':'record_review_decision','issue_id':identity,
            'decision':'wait','reason':wait['reason'],'entry_ids':ids,
            'providers':[p['theory'] for p in wait['providers']],
            'obligations':['After provider publication, verify exact premises and select imports or a local proof.']},
            {'provider_wait_receipt_id':wait['receipt_id']})
