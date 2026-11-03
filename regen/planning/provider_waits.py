"""Durable dependency waits; these receipts grant no premise or import authority."""
from copy import deepcopy
from pathlib import Path
import threading

from regen.execution.proof_commits import digest
from regen.execution.checker_policy import exports_available, theory_finished
from regen.infrastructure.experiment_ledger import Ledger

_LOCK = threading.RLock()  # The coordinator already owns the cross-process run lock.


def _ledger(controller, theory):
    return Ledger(Path(controller.root)/'provider-waits'/digest(theory), controller.run['run_id'])


def _provider(controller, theory):
    package, state = controller._state(theory)
    return {'theory':theory, 'package_sha256':digest(package), 'status':state['status'],
            'seal_id':state.get('seal_id'), 'exports_available':exports_available(state['status'])}


def current_wait(controller, theory=None):
    from regen.planning.theory_outcomes import _snapshot
    theory = theory or controller.theory
    rows = _ledger(controller, theory).rows
    if not rows or rows[-1]['kind'] != 'provider_wait_started':
        return None
    row = rows[-1]
    wait = deepcopy(row['data'])
    if wait['binding'] != _snapshot(controller, theory)[2]:
        return None  # An accepted local/environment change makes this old wait stale.
    providers = [_provider(controller, item['theory']) for item in wait['providers']]
    if any(a['package_sha256'] != b['package_sha256'] for a,b in zip(wait['providers'],providers)):
        raise ValueError('waiting provider package changed')
    return {**wait, 'receipt_id':row['event_id'], 'providers_current':providers,
            'ready':all(p['exports_available'] for p in providers),
            'unavailable_providers':[p['theory'] for p in providers if not p['exports_available']]}


def _acyclic(controller, theory, providers):
    edges = {}
    for name in controller.shared.entries:
        package, state = controller._state(name)
        edges[name] = set(controller._effective_providers(package, state))
        wait = current_wait(controller, name)
        if wait:
            edges[name].update(p['theory'] for p in wait['providers'])
    edges[theory].update(providers)
    visiting, visited = set(), set()
    def visit(name):
        if name in visiting:
            raise ValueError('provider wait would create a dependency cycle')
        if name in visited:
            return
        visiting.add(name)
        for parent in edges.get(name, ()):
            visit(parent)
        visiting.remove(name); visited.add(name)
    for name in edges:
        visit(name)


def record_wait(controller, proposal, metadata):
    from regen.planning.theory_outcomes import _snapshot, _entry_ids, current_outcome, pending_contract_ids
    if set(proposal)-{'action','providers','entry_ids','reason','review_issue_ids'}:
        raise ValueError('unknown wait_for_providers fields')
    providers, reason = proposal.get('providers'), proposal.get('reason')
    if (not isinstance(providers,list) or not providers or not all(isinstance(p,str) for p in providers)
            or len(set(providers)) != len(providers)
            or any(p not in controller.shared.entries or p == controller.theory for p in providers)):
        raise ValueError('providers must name distinct other theories in this run')
    if not isinstance(reason,str) or not reason.strip():
        raise ValueError('provider wait requires a concrete reason')
    with _LOCK:
        package, state, binding = _snapshot(controller)
        if theory_finished(state['status']):
            raise ValueError('completed theory cannot wait')
        ids = _entry_ids(package,state,proposal.get('entry_ids'))
        if not ids:
            raise ValueError('provider wait requires pending fixed contracts')
        if not set(ids)<=set(pending_contract_ids(controller)):
            raise ValueError('provider wait requires pending fixed contracts; terminal dispositions cannot wait')
        for name in providers:
            outcome = current_outcome(controller,name)
            if outcome and outcome['status']=='refuted':
                raise ValueError('refuted provider cannot supply a future positive export')
        current = [_provider(controller,p) for p in providers]
        if all(p['exports_available'] for p in current):
            raise ValueError('providers already exported; inspect their imports instead of waiting')
        _acyclic(controller,controller.theory,providers)
        previous = current_wait(controller)
        if previous:
            raise ValueError('theory already has an active provider wait')
        from regen.planning.review_handoff import validate_wait_issues, record_wait_decisions
        issues = validate_wait_issues(controller,proposal.get('review_issue_ids',[]),providers,ids)
        row = _ledger(controller,controller.theory).append('provider_wait_started',
            schema='provider-wait-v1',binding=binding,providers=current,entry_ids=ids,
            reason=reason,review_issue_ids=issues,metadata=deepcopy(metadata),
            positive_proofs_added=0,imports_added=0)
        wait = current_wait(controller)
        controller.provider_wait = wait
        record_wait_decisions(controller,wait)
        return {'status':'waiting_for_provider','theory_wait':True,'provider_wait':wait,
                'notice':'Accepted proofs and costs are retained. The worker is released; resume only after all named providers publish certified exports. Imports still require native validation.'}


def claim_ready_wait(controller, theory=None):
    theory = theory or controller.theory
    with _LOCK:
        wait = current_wait(controller,theory)
        if wait is None:
            return None
        if not wait['ready']:
            raise ValueError('provider wait is not ready')
        row = _ledger(controller,theory).append('provider_wait_resumed',
            wait_receipt_id=wait['receipt_id'],providers=wait['providers_current'],
            accepted_state_preserved=True,imports_added=0)
        return {**wait,'resume_receipt_id':row['event_id']}


def carry_coordination(source_dir, destination):
    """Rebind current coordination receipts after the proof continuation verifies.

    No graph, proof, source package, import or budget record is rewritten. Embedded
    source events retain the exact original identities for later audit.
    """
    from regen.planning.proof_workspace_agent import WorkspaceController
    from regen.execution.shared_proof_workspace import SharedProofWorkspace
    from regen.planning.theory_outcomes import _snapshot
    from regen.planning.review_handoff import _handoff, _ledger as decision_ledger
    from regen.execution.proof_commits import durable
    def view(root):
        obj=WorkspaceController.__new__(WorkspaceController)
        obj.root=Path(root);obj.shared=SharedProofWorkspace(root);obj.run=obj.shared.run
        obj.document_mode=True;obj.selected=list(obj.shared.entries)
        return obj
    old,new=view(source_dir),view(destination)
    carried=[]
    old_handoff=Ledger(Path(source_dir)/'review-handoff',old.run['run_id'])
    if old_handoff.rows:
        if len(old_handoff.rows)!=1 or old_handoff.rows[0]['kind']!='review_handoff_installed':
            raise ValueError('invalid source review handoff')
        data=deepcopy(old_handoff.rows[0]['data'])
        if data['run_sha256']!=digest(old.run):
            raise ValueError('source handoff binding changed')
        data.update(run_sha256=digest(new.run),continuation_source=old_handoff.rows[0])
        Ledger(Path(destination)/'review-handoff',new.run['run_id']).append('review_handoff_installed',**data)
    for theory in old.shared.entries:
        wait=current_wait(old,theory)
        if wait:
            data={key:deepcopy(value) for key,value in wait.items() if key not in
                  {'receipt_id','providers_current','ready','unavailable_providers'}}
            data.update(binding=_snapshot(new,theory)[2],
                providers=[_provider(new,p['theory']) for p in wait['providers']],
                continuation_source={'run_id':old.run['run_id'],'events':_ledger(old,theory).rows})
            row=_ledger(new,theory).append('provider_wait_started',**data)
            carried.append({'theory':theory,'kind':'provider_wait','source_receipt':wait['receipt_id'],
                            'receipt':row['event_id']})
        decisions=decision_ledger(old,theory).rows
        if decisions:
            if _handoff(old,theory)!=_handoff(new,theory):
                raise ValueError('review continuation changed obligations')
            for source in decisions:
                if source['kind']!='review_decision':
                    raise ValueError('invalid source review decision')
                data=deepcopy(source['data'])
                if data['handoff_sha256']!=digest(_handoff(old,theory)):
                    raise ValueError('source review decision binding changed')
                data.update(binding=_snapshot(new,theory)[2],continuation_source=source)
                row=decision_ledger(new,theory).append('review_decision',**data)
                carried.append({'theory':theory,'kind':'review_decision','source_receipt':source['event_id'],
                                'receipt':row['event_id']})
    if carried or old_handoff.rows:
        durable(Path(destination)/'coordination-continuation.json',{
            'source_run':str(source_dir),'receipts':carried,'positive_proofs_added':0,
            'budget_reset':False},immutable=True)
