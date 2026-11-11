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
    available = exports_available(state['status'])
    terminal_reason = None
    if not available:
        if theory_finished(state['status']):
            terminal_reason = 'completed_without_positive_exports'
        else:
            from regen.planning.theory_outcomes import current_outcome
            outcome = current_outcome(controller, theory)
            # Legacy UNKNOWN can be explicitly reopened; retain that wait.
            # A refutation or final retirement cannot publish this interface.
            if outcome and outcome['status'] == 'refuted':
                terminal_reason = 'refuted_provider'
    return {'theory':theory, 'package_sha256':digest(package), 'status':state['status'],
            'seal_id':state.get('seal_id'), 'exports_available':available,
            'terminal_unavailable':terminal_reason is not None, 'terminal_reason':terminal_reason}


def _entry_binding(controller, theory):
    """Sibling proofs do not change a fixed entry's optional dependency wait."""
    from regen.planning.theory_outcomes import _snapshot
    _, _, binding = _snapshot(controller, theory)
    return {key:value for key,value in binding.items() if key != 'accepted_state_sha256'}


def _active_waits(controller, theory):
    from regen.planning.theory_outcomes import _snapshot, pending_contract_ids
    active = {}
    for row in _ledger(controller, theory).rows:
        if row['kind'] == 'provider_wait_started':
            active[row['event_id']] = row
        elif row['kind'] in {'provider_wait_resumed', 'provider_wait_invalidated'}:
            active.pop(row['data']['wait_receipt_id'], None)
        else:
            raise ValueError('invalid provider wait ledger')
    if not active:
        return []
    pending = set(pending_contract_ids(controller, theory))
    result = []
    for row in active.values():
        wait = deepcopy(row['data'])
        expected = (_entry_binding(controller, theory) if wait.get('schema') == 'provider-wait-v2'
                    else _snapshot(controller, theory)[2])
        if wait['binding'] != expected:
            continue  # Preserve historical evidence, but do not reuse another context.
        ids = sorted(set(wait['entry_ids']) & pending)
        if not ids:
            continue
        providers = [_provider(controller, item['theory']) for item in wait['providers']]
        if any(a['package_sha256'] != b['package_sha256'] for a,b in zip(wait['providers'],providers)):
            raise ValueError('waiting provider package changed')
        failed = [p for p in providers if p['terminal_unavailable']]
        result.append({**wait, 'entry_ids':ids, 'receipt_id':row['event_id'],
            'providers_current':providers, 'ready':all(p['exports_available'] for p in providers),
            'invalidated':bool(failed), 'failed_providers':failed,
            'requires_route_reselection':bool(failed),
            'unavailable_providers':[p['theory'] for p in providers if not p['exports_available']]})
    return result


def current_wait(controller, theory=None):
    """Project optional entry waits without treating them as theory imports."""
    from regen.planning.theory_outcomes import pending_contract_ids
    theory = theory or controller.theory
    waits = _active_waits(controller, theory)
    if not waits:
        return None
    blocked = sorted({key for wait in waits if not wait['ready'] and not wait['invalidated']
                      for key in wait['entry_ids']})
    runnable = sorted(set(pending_contract_ids(controller, theory)) - set(blocked))
    summary = deepcopy(waits[0])
    if len(waits) > 1:
        summary.update(receipt_id='provider-waits:'+digest([w['receipt_id'] for w in waits]),
            entry_ids=sorted({key for w in waits for key in w['entry_ids']}),
            providers=list({p['theory']:p for w in waits for p in w['providers']}.values()),
            providers_current=list({p['theory']:p for w in waits for p in w['providers_current']}.values()),
            unavailable_providers=sorted({p for w in waits for p in w['unavailable_providers']}))
    failed = list({p['theory']:p for w in waits for p in w['failed_providers']}.values())
    return {**summary, 'waits':waits, 'ready':all(w['ready'] for w in waits),
            'invalidated':any(w['invalidated'] for w in waits), 'failed_providers':failed,
            'requires_route_reselection':bool(failed),
            'blocked_entry_ids':blocked, 'runnable_entry_ids':runnable,
            'theory_wait':bool(blocked) and not runnable}


def blocked_wait_contract_ids(controller, theory=None):
    wait = current_wait(controller, theory)
    return wait['blocked_entry_ids'] if wait else []


def runnable_contract_ids(controller, theory=None):
    from regen.planning.theory_outcomes import pending_contract_ids
    theory = theory or controller.theory
    return sorted(set(pending_contract_ids(controller, theory)) -
                  set(blocked_wait_contract_ids(controller, theory)))


def _acyclic(controller, theory, providers):
    edges = {}
    for name in controller.shared.entries:
        package, state = controller._state(name)
        edges[name] = set(controller._effective_providers(package, state))
        wait = current_wait(controller, name)
        if wait:
            edges[name].update(p['theory'] for item in wait['waits'] if not item['invalidated']
                               for p in item['providers'])
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
        if any(p['terminal_unavailable'] for p in current):
            raise ValueError('provider completed without positive exports; select another route')
        if all(p['exports_available'] for p in current):
            raise ValueError('providers already exported; inspect their imports instead of waiting')
        _acyclic(controller,controller.theory,providers)
        previous = current_wait(controller)
        live_ids = {key for item in previous['waits'] if not item['invalidated']
                    for key in item['entry_ids']} if previous else set()
        if set(ids) & live_ids:
            raise ValueError('contract already has an active provider wait')
        from regen.planning.review_handoff import validate_wait_issues, record_wait_decisions
        issues = validate_wait_issues(controller,proposal.get('review_issue_ids',[]),providers,ids)
        row = _ledger(controller,controller.theory).append('provider_wait_started',
            schema='provider-wait-v2',binding=_entry_binding(controller,controller.theory),providers=current,entry_ids=ids,
            reason=reason,review_issue_ids=issues,metadata=deepcopy(metadata),
            positive_proofs_added=0,imports_added=0)
        wait = current_wait(controller)
        controller.provider_wait = wait if wait['theory_wait'] else None
        recorded = next(w for w in wait['waits'] if w['receipt_id'] == row['event_id'])
        record_wait_decisions(controller,recorded)
        return {'status':'waiting_for_provider' if wait['theory_wait'] else 'entries_waiting_for_provider',
                'theory_wait':wait['theory_wait'],'provider_wait':wait,
                'runnable_entry_ids':wait['runnable_entry_ids'],
                'notice':'Only the named pending contracts wait. Continue runnable siblings; release the worker only when none remain. Accepted proofs and costs are retained. Provider publication creates no import; imports still require native validation.'}


def claim_ready_wait(controller, theory=None):
    """Consume published or terminally invalid waits; neither adds an import."""
    theory = theory or controller.theory
    with _LOCK:
        wait = current_wait(controller,theory)
        if wait is None:
            return None
        ready = [w for w in wait['waits'] if w['ready']]
        invalid = [w for w in wait['waits'] if w['invalidated']]
        if not ready and not invalid:
            raise ValueError('provider wait is not ready')
        resumed = []
        for item in ready:
            row = _ledger(controller,theory).append('provider_wait_resumed',
                wait_receipt_id=item['receipt_id'],providers=item['providers_current'],
                accepted_state_preserved=True,imports_added=0)
            resumed.append({**item,'resume_receipt_id':row['event_id']})
        invalidated = []
        for item in invalid:
            row = _ledger(controller,theory).append('provider_wait_invalidated',
                wait_receipt_id=item['receipt_id'], providers=item['providers_current'],
                failed_providers=item['failed_providers'], entry_ids=item['entry_ids'],
                reason='provider_terminal_without_positive_exports',
                accepted_state_preserved=True, imports_added=0, positive_proofs_added=0)
            invalidated.append({**item,'invalidation_receipt_id':row['event_id']})
        return {**(resumed or invalidated)[0], 'resumed_waits':resumed,
                'invalidated_waits':invalidated,
                'requires_route_reselection':bool(invalidated),
                'notice':'Only published exports are ready. Invalidated waits restore the named pending entries for another proof route; they supply no facts or imports. Reassess affected review decisions.'}


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
            for item in wait['waits']:
                data={key:deepcopy(value) for key,value in item.items() if key not in
                      {'receipt_id','providers_current','ready','unavailable_providers',
                       'invalidated','failed_providers','requires_route_reselection'}}
                binding=(_entry_binding(new,theory) if data.get('schema')=='provider-wait-v2'
                         else _snapshot(new,theory)[2])
                data.update(binding=binding,
                    providers=[_provider(new,p['theory']) for p in item['providers']],
                    continuation_source={'run_id':old.run['run_id'],'events':_ledger(old,theory).rows})
                row=_ledger(new,theory).append('provider_wait_started',**data)
                carried.append({'theory':theory,'kind':'provider_wait','source_receipt':item['receipt_id'],
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
