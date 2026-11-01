"""Theory-local stop receipts, separate from positive proof acceptance.

An unknown outcome is a scheduling decision, not a theorem about provability.
Only the native refutation checker may create a refuted outcome. Neither outcome
changes proofs, contracts, provider visibility, or generation budget accounting.
"""
from copy import deepcopy
import json
from pathlib import Path

from regen.execution.proof_commits import digest
from regen.infrastructure.experiment_ledger import Ledger

CLASSIFICATIONS = frozenset({'proof_search_exhausted', 'suspected_counterexample',
    'missing_assumptions', 'unsupported_specification', 'no_progress'})


def _snapshot(controller, theory=None):
    theory = theory or controller.theory
    package, state = controller._state(theory)
    binding = {'run_id': controller.run['run_id'], 'run_sha256': digest(controller.run),
               'theory': theory, 'package_sha256': digest(package),
               'accepted_state_sha256': digest({k: state.get(k) for k in
                                               ('graph', 'proofs', 'accepted_order')})}
    return package, state, binding


def _ledger(controller, theory):
    return Ledger(Path(controller.root)/'theory-outcomes'/digest(theory),
                  controller.run['run_id'])


def _entry_ids(package, state, entries):
    fixed = {c['contract_id'] for c in package['contracts']}
    if entries is None:
        return sorted(fixed-set(state.get('proofs', {})))
    if (not isinstance(entries, list) or not entries or
            not all(isinstance(e, str) for e in entries) or len(set(entries)) != len(entries)
            or not set(entries) <= fixed or set(entries) & set(state.get('proofs', {}))):
        raise ValueError('entry_ids must name distinct unproved fixed contracts')
    return list(entries)


def _publish(controller, *, status, reason, origin, details, entry_ids,
             classification=None, metadata=None, theory=None):
    theory = theory or controller.theory
    package, state, binding = _snapshot(controller, theory)
    if state.get('status') in {'sealed', 'retired'}:
        raise ValueError('a completed theory cannot acquire an unresolved outcome')
    previous = _saved_outcome(controller, theory)
    if previous and previous['status'] == 'refuted':
        raise ValueError('a checked refutation cannot be replaced by another local disposition')
    ids = _entry_ids(package, state, entry_ids)
    outcome = {'status': status, 'reason': reason, 'origin': origin,
               'entry_ids': ids, 'binding': binding, 'details': deepcopy(details),
               'positive_proofs_added': 0, 'theory_sealed': False,
               'truth_status': 'refuted' if status == 'refuted' else 'undetermined',
               'classification': classification, 'metadata': deepcopy(metadata or {})}
    ledger = _ledger(controller, binding['theory'])
    sequence = len(ledger.rows) + 1
    from regen.execution.check_tasks import CURRENT_TASK
    token = CURRENT_TASK.get() if status == 'refuted' else None
    try:
        row = ledger.append('theory_outcome', **outcome)
    except BaseException:
        # Like positive proof acceptance, a complete immutable event can become
        # visible before fsync/cleanup reports failure. Retain that publication
        # against cancellation; replay will recover it despite a lost response.
        if token is not None:
            try:
                rows = _ledger(controller, binding['theory']).rows
                if len(rows) == sequence and rows[-1]['data'] == outcome:
                    token.published = True
            except (OSError, ValueError):
                pass
        raise
    if token is not None:
        token.published = True
    result = {**outcome, 'receipt': {'event_id': row['event_id'], 'sequence': row['sequence'],
               'directory': str(_ledger_path(controller, binding['theory']))}}
    if getattr(controller, 'theory', None) == theory:
        controller.theory_outcome = result
    return result


def _ledger_path(controller, theory):
    return Path(controller.root)/'theory-outcomes'/digest(theory)


def record_unresolved(controller, *, reason, origin, details=None, entry_ids=None,
                      classification='proof_search_exhausted', metadata=None):
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('an unresolved report requires a nonempty reason')
    if origin not in {'model', 'controller'} or classification not in CLASSIFICATIONS:
        raise ValueError('invalid unresolved outcome origin or classification')
    # Classification and narrative are reports only; they never certify a false
    # specification, missing premise, or mathematical impossibility.
    return _publish(controller, status='unknown', reason=reason, origin=origin,
                    details=details or {}, entry_ids=entry_ids,
                    classification=classification, metadata=metadata)


def _refutation_binding(binding):
    return {k:v for k,v in binding.items() if k != 'accepted_state_sha256'}


def _saved_outcome(controller, theory):
    """Read the stop journal without reconciling older native task receipts."""
    theory = theory or controller.theory
    if not (_ledger_path(controller, theory)/'events').exists():
        return None
    package, state, binding = _snapshot(controller, theory)
    if state.get('status') in {'sealed', 'retired'}:
        return None
    ledger = _ledger(controller, binding['theory'])
    if not ledger.rows:
        return None
    row = ledger.rows[-1]
    outcome = row['data']
    if row['kind'] != 'theory_outcome' or outcome.get('status') not in {'unknown', 'refuted'}:
        raise ValueError('invalid theory outcome record')
    recorded = outcome.get('binding')
    # Negative checking uses only the frozen parent/sealed imports, never the
    # local positive prefix. New local proofs cannot invalidate its certificate.
    if outcome['status'] == 'refuted':
        if not isinstance(recorded, dict) or _refutation_binding(recorded) != _refutation_binding(binding):
            return None
    elif recorded != binding:
        return None
    ids = outcome.get('entry_ids')
    # The default report can have no pending fixed target (only a helper/seal is
    # unresolved). Explicit reports still require nonempty pending target IDs.
    if ids:
        _entry_ids(package, state, ids)
    elif ids != [] or outcome['status'] == 'refuted':
        raise ValueError('invalid outcome target identity')
    if outcome['status'] == 'refuted':
        from regen.execution.contract_refutation import validate_refutation_certificate
        contract = next(c for c in package['contracts'] if c['contract_id'] == ids[0])
        validate_refutation_certificate(outcome['details']['certificate'], contract)
    return {**outcome, 'receipt': {'event_id': row['event_id'], 'sequence': row['sequence'],
                                 'directory': str(_ledger_path(controller, binding['theory']))}}


def _recover_completed_refutation(controller, theory):
    """Reconcile the old completion-before-outcome window, without native work.

    New checks publish inside the executor's cancellation/acceptance guard before
    completing. Older completed tasks may lack that journal event. Only a fully
    bound, durably completed result and a revalidated certificate may recover it;
    interrupted, cancelled, failed, and merely cached certificate files cannot.
    """
    if getattr(controller, '_abort_requested', None) is not None and controller._abort_requested.is_set():
        return None
    executor = getattr(controller, 'executor', None)
    task_root = Path(executor.root) if executor is not None else Path(controller.root)/'check-tasks'
    snapshot = None
    for path in sorted(task_root.glob('*/task.json')):
        row = json.loads(path.read_text())
        if (row.get('kind') != 'refutation' or row.get('theory') != theory
                or row.get('status') != 'completed'):
            continue
        if snapshot is None:
            snapshot = _snapshot(controller, theory)
        package, state, binding = snapshot
        if state.get('status') in {'sealed', 'retired'}:
            return None
        if (not isinstance(row.get('binding'), dict)
                or _refutation_binding(row['binding']) != _refutation_binding(binding)):
            continue
        identity = {key:row[key] for key in ('kind', 'theory', 'binding', 'request')}
        metadata = row['request'].get('metadata', {})
        call = {key:metadata.get(key) for key in ('thread_id', 'turn_id', 'call_id')}
        expected_id = digest({'kind':'refutation', 'binding':row['binding'], 'call':call})
        if (not all(isinstance(value, str) and value for value in call.values())
                or row.get('identity_hash') != digest(identity)
                or row.get('task_id') != path.parent.name or row['task_id'] != expected_id):
            raise ValueError('completed refutation task identity mismatch')
        completion = json.loads((path.parent/'completion.json').read_text())
        result = json.loads((path.parent/'result.json').read_text())
        if (completion.get('identity_hash') != row['identity_hash']
                or completion.get('result_sha256') != digest(result)
                or row.get('result_sha256') != digest(result)):
            raise ValueError('completed refutation result binding mismatch')
        if result.get('status') != 'refuted':
            continue
        proposal = row['request'].get('proposal', {})
        if set(proposal) != {'action', 'entry_id', 'proof_body'} or proposal['action'] != 'check_refutation':
            raise ValueError('invalid completed refutation request')
        ids = _entry_ids(package, state, [proposal['entry_id']])
        contract = next(c for c in package['contracts'] if c['contract_id'] == ids[0])
        from regen.execution.contract_refutation import validate_refutation_certificate
        validate_refutation_certificate(result['certificate'], contract)
        return _publish(controller, status='refuted',
            reason='Native-checked negation of the frozen contract', origin='native_checker',
            details={'task_id':row['task_id'], 'certificate':result['certificate'],
                     'recovered_from_completed_task':True}, entry_ids=ids,
            metadata=metadata, theory=theory)
    return None


def current_outcome(controller, theory=None):
    """Recover a bound stop, including legacy completed negative checks."""
    theory = theory or controller.theory
    saved = _saved_outcome(controller, theory)
    if saved and saved['status'] == 'refuted':
        return saved
    return _recover_completed_refutation(controller, theory) or saved


def _check_refutation(controller, proposal, metadata):
    from regen.execution.check_executor import CURRENT_RESOURCES
    from regen.execution.check_tasks import (TERMINAL, CheckCancelled, NativePreparationError,
                                             acceptance_guard, check_cancelled)
    from regen.execution.contract_refutation import (check_refutation, validate_refutation_certificate,
                                                       render_refutation, UnsupportedRefutation)
    if set(proposal) != {'action', 'entry_id', 'proof_body'}:
        raise ValueError('check_refutation requires entry_id and proof_body only')
    package, state, binding = _snapshot(controller)
    ids = _entry_ids(package, state, [proposal['entry_id']])
    proof = proposal['proof_body']
    if not isinstance(proof, str) or not proof.strip():
        raise ValueError('check_refutation requires a complete negative proof body')
    readiness = controller._theory_readiness(controller.theory, package=package, state=state)
    if not readiness['ready_for_proof_action']:
        raise ValueError('refutation requires all hard providers sealed')
    contract = next(c for c in package['contracts'] if c['contract_id'] == ids[0])
    # Candidate hygiene errors remain tool validation feedback, before a task
    # can misclassify them as checker infrastructure failure. The placeholder
    # imports/name are syntax-only; native execution binds the real imports.
    try:
        render_refutation(contract, proof, ['Main'], theory_name='Refutation_'+'0'*32,
                          audit_path='/audit-output/refutation.xml')
    except UnsupportedRefutation as error:
        return {'status':'unsupported', 'diagnostic':str(error), 'certificate':None,
                'theory_stop':False, 'notice':'Report unresolved if this scope cannot be checked.'}
    identity = {key: metadata.get(key) for key in ('thread_id', 'turn_id', 'call_id')}
    if not all(isinstance(v, str) and v for v in identity.values()):
        raise ValueError('refutation requires an audited tool call identity')
    task_id = digest({'kind': 'refutation', 'binding': binding, 'call': identity})

    def execute(directory):
        marker = CURRENT_RESOURCES.set(controller.resources)
        try:
            controller._check_session_active()
            check_cancelled()
            if _snapshot(controller)[2] != binding:
                raise ValueError('refutation context changed before native check')
            runtime = controller._runtime()
            result = check_refutation(runtime[4], contract, proof, directory/'negative-check')
            if result.get('status') == 'refuted':
                validate_refutation_certificate(result['certificate'], contract)
                # Publish before executor completion, under the same cancellation
                # authority used by positive commits. A cancellation that wins
                # this guard publishes nothing; an accepted receipt survives a
                # later cancellation, transport error, or executor restart.
                with acceptance_guard():
                    controller._check_session_active()
                    if _snapshot(controller)[2] != binding:
                        raise ValueError('refutation context changed during native check')
                    _publish(controller, status='refuted',
                        reason='Native-checked negation of the frozen contract', origin='native_checker',
                        details={'task_id':task_id, 'certificate':result['certificate']},
                        entry_ids=ids, metadata=metadata)
            return result
        finally:
            CURRENT_RESOURCES.reset(marker)

    controller._check_session_active()
    row = controller.executor.submit(kind='refutation', theory=controller.theory,
        binding=binding, request={'proposal': deepcopy(proposal), 'metadata': deepcopy(metadata)},
        execute=execute, owner=controller.theory, task_id=task_id)
    with controller.executor.lock:
        controller.task_ids.add(task_id)
    if controller._abort_requested.is_set():
        row = controller.executor.cancel(task_id)
    while row['status'] not in TERMINAL:
        row = controller.executor.wait([task_id], timeout=30)[0]
    if row['status'] == 'cancelled':
        raise CheckCancelled(row.get('diagnostic', 'refutation check cancelled'))
    if row['status'] != 'completed':
        raise NativePreparationError(row.get('diagnostic', 'refutation infrastructure failed'))
    if hasattr(controller, '_record_proof_work'):
        controller._record_proof_work(row, {'kind':'refutation', 'proposal':proposal})
    result = row['result']
    if result.get('status') != 'refuted':
        controller._check_session_active()
        if _snapshot(controller)[2] != binding:
            raise ValueError('refutation context changed during native check')
        return {**result, 'task_id': task_id, 'theory_stop': False,
                'notice': 'No refutation accepted. Revise the negative proof or report_unresolved; positive goals are unchanged.'}
    outcome = current_outcome(controller)
    if outcome is None or outcome['status'] != 'refuted':
        raise NativePreparationError('completed refutation has no accepted outcome')
    return {'status': 'refuted', 'theory_outcome': outcome, 'theory_stop': True,
            'task_id': task_id, 'notice': 'Refutation is separate from positive proofs; this theory remains unsealed.'}


def handle_outcome(controller, proposal, metadata):
    if getattr(controller, 'theory_outcome', None):
        return {'theory_stop': True, 'theory_outcome': controller.theory_outcome}
    action = proposal.get('action')
    if action == 'report_unresolved':
        if set(proposal)-{'action', 'reason', 'classification', 'entry_ids'}:
            raise ValueError('unknown report_unresolved fields')
        outcome = record_unresolved(controller, reason=proposal.get('reason'), origin='model',
            classification=proposal.get('classification', 'proof_search_exhausted'),
            entry_ids=proposal.get('entry_ids'), metadata=metadata)
        return {'status': 'unknown', 'theory_stop': True, 'theory_outcome': outcome,
                'notice': 'Unresolved report preserved; it does not establish falsehood or unprovability.'}
    if action == 'check_refutation':
        return _check_refutation(controller, proposal, metadata)
    raise ValueError('unknown theory outcome action')
