"""Versioned stop/disposition receipts, separate from positive proof acceptance.

An unknown outcome is a scheduling decision, not a theorem about provability.
Only the native refutation checker may create a refuted outcome. Neither outcome
changes proofs, contracts, provider visibility, or generation budget accounting.
New runs isolate dispositions to fixed contracts; legacy runs retain the original
whole-theory stop behavior. Export authorization is handled by seal evidence.
"""
from copy import deepcopy
from contextlib import nullcontext
import json
from pathlib import Path

from regen.execution.proof_commits import digest
from regen.infrastructure.experiment_ledger import Ledger

CLASSIFICATIONS = frozenset({'proof_search_exhausted', 'suspected_counterexample',
    'missing_assumptions', 'unsupported_specification', 'no_progress'})
CONTRACT_POLICY = 'contract-dispositions-v1'
DISPOSITION_SCHEMA = 'contract-disposition-v1'


def contract_dispositions_enabled(run):
    """Missing policy denotes the immutable legacy whole-theory semantics."""
    policy = run.get('contract_outcome_policy')
    if policy not in (None, CONTRACT_POLICY):
        raise ValueError('unsupported contract outcome policy')
    return policy == CONTRACT_POLICY


def _disposition_ledger_path(controller, theory):
    return Path(controller.root)/'contract-dispositions'/digest(theory)


def _disposition_receipt(row, directory):
    # Embed the event envelope, not a mutable pointer, so a later export can
    # replay the exact disposition without reading its originating filesystem.
    return {key:row[key] for key in ('event_id', 'sequence', 'parent', 'time_unix', 'kind')} | {
        'directory':str(directory)}


def validate_dispositions(package, state, dispositions, *, expected_imports=None,
                          expected_binding=None):
    """Validate trusted-produced, self-contained per-contract receipts.

    Receipt hashes bind bytes; they do not authenticate model-authored JSON.
    Only the outcome journal/native acceptance path may produce these records.
    Neither unknown nor a checked negation is an available positive premise.
    """
    from regen.execution.contract_refutation import contract_binding, validate_refutation_certificate
    if not isinstance(dispositions, dict):
        raise ValueError('contract dispositions must be a mapping')
    contracts = {c['contract_id']:c for c in package['contracts']}
    if len(contracts) != len(package['contracts']) or set(dispositions)-set(contracts):
        raise ValueError('disposition names a non-fixed or duplicated contract')
    if set(dispositions) & set(state.get('proofs', {})):
        raise ValueError('a contract cannot have both a positive proof and a disposition')
    checked, covered = {}, set()
    for key, outcome in dispositions.items():
        if not isinstance(outcome, dict):
            raise ValueError('invalid contract disposition')
        if (outcome.get('schema') != DISPOSITION_SCHEMA or outcome.get('policy') != CONTRACT_POLICY
                or outcome.get('scope') != 'contract' or outcome.get('status') not in {'unknown', 'refuted'}
                or outcome.get('positive_proofs_added') != 0 or outcome.get('theory_sealed') is not False
                or not isinstance(outcome.get('reason'), str) or not outcome['reason'].strip()):
            raise ValueError('invalid contract disposition semantics')
        ids = outcome.get('entry_ids')
        if (not isinstance(ids, list) or not ids or not all(isinstance(x, str) for x in ids)
                or len(set(ids)) != len(ids) or key not in ids or not set(ids) <= set(contracts)):
            raise ValueError('invalid contract disposition identity')
        binding = outcome.get('binding')
        if (not isinstance(binding, dict) or binding.get('theory') != package['theory']
                or not all(isinstance(binding.get(k), str) and binding[k] for k in
                           ('run_id', 'run_sha256', 'package_sha256', 'accepted_state_sha256'))):
            raise ValueError('invalid contract disposition binding')
        if (expected_binding is not None
                and _disposition_binding(binding) != _disposition_binding(expected_binding)):
            raise ValueError('contract disposition binding mismatch')
        expected_contracts = {cid:contract_binding(contracts[cid]) for cid in ids}
        if outcome.get('contract_bindings') != expected_contracts:
            raise ValueError('contract disposition statement/context mismatch')
        receipt = outcome.get('receipt')
        if (not isinstance(receipt, dict) or receipt.get('kind') != 'contract_disposition'
                or type(receipt.get('sequence')) is not int or receipt['sequence'] < 1):
            raise ValueError('contract disposition lacks a durable receipt')
        core = {k:receipt.get(k) for k in ('sequence', 'parent', 'time_unix', 'kind')}
        core.update(run_id=binding['run_id'], data={k:v for k,v in outcome.items() if k != 'receipt'})
        if digest(core) != receipt.get('event_id'):
            raise ValueError('contract disposition receipt hash mismatch')
        event_id = receipt['event_id']
        if event_id in checked:
            if checked[event_id] != outcome:
                raise ValueError('conflicting copies of a contract disposition')
            continue
        if covered & set(ids):
            raise ValueError('multiple terminal dispositions for one contract')
        covered.update(ids); checked[event_id] = outcome
        if outcome['status'] == 'refuted':
            if (len(ids) != 1 or outcome.get('origin') != 'native_checker'
                    or outcome.get('truth_status') != 'refuted' or outcome.get('classification') is not None):
                raise ValueError('invalid native refutation disposition')
            validate_refutation_certificate(outcome.get('details', {}).get('certificate'),
                contracts[ids[0]], expected_imports=expected_imports)
        elif (outcome.get('origin') not in {'model', 'controller'}
                or outcome.get('truth_status') != 'undetermined'
                or outcome.get('classification') not in CLASSIFICATIONS):
            raise ValueError('unknown disposition cannot establish falsehood')
    if covered != set(dispositions):
        raise ValueError('contract disposition map omitted an entry from its receipt')
    return True


def _saved_dispositions(controller, theory, *, historical_context=False):
    package, state, binding = _snapshot(controller, theory)
    directory = _disposition_ledger_path(controller, theory)
    if not (directory/'events').exists():
        return {}
    outcomes = {}
    for row in Ledger(directory, controller.run['run_id']).rows:
        if row['kind'] != 'contract_disposition':
            raise ValueError('invalid contract disposition journal event')
        outcome = {**row['data'], 'receipt':_disposition_receipt(row, directory)}
        ids = outcome.get('entry_ids')
        if (not isinstance(ids, list) or not all(isinstance(key, str) for key in ids)
                or any(key in outcomes for key in ids)):
            raise ValueError('contract disposition overwritten in journal')
        for key in ids:
            outcomes[key] = outcome
    if historical_context:
        # An import event may retire old GREENs before the planner records an
        # UNKNOWN in the new context. That later receipt cannot contradict the
        # earlier checked prefix during cold replay. Revisions are monotonic;
        # current-context appends still enforce every existing disposition.
        revision = state.get('context_revision', 0)
        if type(revision) is not int or revision < 0:
            raise ValueError('invalid disposition context revision')
        for key, outcome in list(outcomes.items()):
            recorded = outcome.get('binding', {}).get('context_revision', 0)
            if type(recorded) is not int or recorded < 0:
                raise ValueError('invalid disposition context revision')
            if recorded > revision:
                del outcomes[key]
    # This reader is also used against historical states during journal replay.
    # A later negative receipt must not rewrite those earlier context bindings.
    validate_dispositions(package, state, outcomes, expected_binding=binding)
    return outcomes


def current_dispositions(controller, theory=None):
    """Read/recover every terminal contract, independent of later local GREENs."""
    if not contract_dispositions_enabled(controller.run):
        return {}
    theory = theory or controller.theory
    saved = _saved_dispositions(controller, theory)
    package, state = controller._state(theory)
    if 'context_revision' in state:
        from regen.execution.import_revisions import native_imports
        validate_dispositions(package, state, saved,
                              expected_imports=native_imports(controller.run, state['context_imports']))
    if state.get('status') not in {'sealed', 'retired', 'exported_partial'}:
        while _recover_completed_refutation(controller, theory) is not None:
            saved = _saved_dispositions(controller, theory)
    return saved


def pending_contract_ids(controller, theory=None):
    theory = theory or controller.theory
    package, state = controller._state(theory)
    disposed = current_dispositions(controller, theory)
    return [c['contract_id'] for c in package['contracts']
            if c['contract_id'] not in state.get('proofs', {}) and c['contract_id'] not in disposed]


def _publish_disposition(controller, *, status, reason, origin, details, entry_ids,
                         classification=None, metadata=None, theory=None):
    from regen.execution.contract_refutation import contract_binding
    theory = theory or controller.theory
    package, state, binding = _snapshot(controller, theory)
    if state.get('status') in {'sealed', 'retired', 'exported_partial'}:
        raise ValueError('a completed theory cannot acquire a contract disposition')
    previous = _saved_dispositions(controller, theory)
    if entry_ids is None:
        if origin == 'model':
            raise ValueError('model unresolved reports require explicit contract entry_ids')
        entry_ids = [c['contract_id'] for c in package['contracts']
                     if c['contract_id'] not in state.get('proofs', {}) and c['contract_id'] not in previous]
    ids = _entry_ids(package, state, entry_ids)
    if set(ids) & set(previous):
        raise ValueError('contract already has a terminal disposition')
    contracts = {c['contract_id']:c for c in package['contracts']}
    outcome = {'schema':DISPOSITION_SCHEMA, 'policy':CONTRACT_POLICY, 'scope':'contract',
        'status':status, 'reason':reason, 'origin':origin, 'entry_ids':ids, 'binding':binding,
        'contract_bindings':{key:contract_binding(contracts[key]) for key in ids},
        'details':deepcopy(details), 'positive_proofs_added':0, 'theory_sealed':False,
        'truth_status':'refuted' if status == 'refuted' else 'undetermined',
        'classification':classification, 'metadata':deepcopy(metadata or {})}
    directory = _disposition_ledger_path(controller, theory)
    ledger = Ledger(directory, controller.run['run_id'])
    # Validate semantics before publication. The event envelope is selected here
    # only for validation; Ledger remains the sole authority for its real clock,
    # sequence, durable write and publication identity.
    probe = {'run_id':controller.run['run_id'], 'sequence':len(ledger.rows)+1,
             'parent':ledger.rows[-1]['event_id'] if ledger.rows else None,
             'time_unix':0, 'kind':'contract_disposition', 'data':outcome}
    probe['event_id'] = digest(probe)
    probed = {**outcome, 'receipt':_disposition_receipt(probe, directory)}
    validate_dispositions(package, state, {key:probed for key in ids}, expected_binding=binding)
    from regen.execution.check_tasks import CURRENT_TASK
    token = CURRENT_TASK.get() if status == 'refuted' else None
    sequence = len(ledger.rows)+1
    try:
        row = ledger.append('contract_disposition', **outcome)
    except BaseException:
        if token is not None:
            try:
                rows = Ledger(directory, controller.run['run_id']).rows
                if len(rows) == sequence and rows[-1]['data'] == outcome:
                    token.published = True
            except (OSError, ValueError):
                pass
        raise
    if token is not None:
        token.published = True
    return {**outcome, 'receipt':_disposition_receipt(row, directory)}


def _snapshot(controller, theory=None):
    theory = theory or controller.theory
    package, state = controller._state(theory)
    binding = {'run_id': controller.run['run_id'], 'run_sha256': digest(controller.run),
               'theory': theory, 'package_sha256': digest(package),
               'accepted_state_sha256': digest({k: state.get(k) for k in
                                               ('graph', 'proofs', 'accepted_order')})}
    if 'context_revision' in state:
        binding.update(context_revision=state['context_revision'],
                       context_imports=list(state['context_imports']))
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
    if contract_dispositions_enabled(controller.run):
        return _publish_disposition(controller, status=status, reason=reason, origin=origin,
            details=details, entry_ids=entry_ids, classification=classification,
            metadata=metadata, theory=theory)
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


def _disposition_binding(binding):
    # UNKNOWN remains a scheduling decision across checked context revisions;
    # negative proof evidence is independently bound to its checked imports.
    return {key:binding.get(key) for key in ('run_id', 'run_sha256', 'theory', 'package_sha256')}


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
    contract_mode = contract_dispositions_enabled(controller.run)
    disposed = _saved_dispositions(controller, theory) if contract_mode else {}
    snapshot = None
    for path in sorted(task_root.glob('*/task.json')):
        row = json.loads(path.read_text())
        if (row.get('kind') != 'refutation' or row.get('theory') != theory
                or row.get('status') != 'completed'):
            continue
        if snapshot is None:
            snapshot = _snapshot(controller, theory)
        package, state, binding = snapshot
        if state.get('status') in {'sealed', 'retired', 'exported_partial'}:
            return None
        if (not isinstance(row.get('binding'), dict)
                or _disposition_binding(row['binding']) != _disposition_binding(binding)):
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
        checked_binding = result.get('checked_binding', row['binding'])
        if (not isinstance(checked_binding, dict)
                or _refutation_binding(checked_binding) != _refutation_binding(binding)):
            continue
        proposal = row['request'].get('proposal', {})
        if set(proposal) != {'action', 'entry_id', 'proof_body'} or proposal['action'] != 'check_refutation':
            raise ValueError('invalid completed refutation request')
        ids = _entry_ids(package, state, [proposal['entry_id']])
        contract = next(c for c in package['contracts'] if c['contract_id'] == ids[0])
        from regen.execution.contract_refutation import validate_refutation_certificate
        expected_imports = None
        if 'context_revision' in checked_binding:
            from regen.execution.import_revisions import native_imports
            expected_imports = native_imports(controller.run, checked_binding['context_imports'])
        validate_refutation_certificate(result['certificate'], contract,
                                        expected_imports=expected_imports)
        if ids[0] in disposed:
            previous = disposed[ids[0]]
            if (previous['status'] != 'refuted'
                    or previous['details']['certificate'] != result['certificate']):
                raise ValueError('completed refutation conflicts with a contract disposition')
            continue
        return _publish(controller, status='refuted',
            reason='Native-checked negation of the frozen contract', origin='native_checker',
            details={'task_id':row['task_id'], 'certificate':result['certificate'],
                     'recovered_from_completed_task':True}, entry_ids=ids,
            metadata=metadata, theory=theory)
    return None


def current_outcome(controller, theory=None):
    """Recover a bound stop, including legacy completed negative checks."""
    if contract_dispositions_enabled(controller.run):
        # Per-contract receipts are not a whole-theory stop, including when all
        # fixed targets have dispositions: the worker must still publish its
        # checked positive subset before consumers can import the theory.
        current_dispositions(controller, theory)
        return None
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
    contract_mode = contract_dispositions_enabled(controller.run)
    if contract_mode:
        retained = current_dispositions(controller).get(ids[0])
        if retained is not None:
            return _disposition_feedback(controller, retained, retained=True)
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
            with getattr(controller, '_action_lock', nullcontext()):
                if _snapshot(controller)[2] != binding:
                    raise ValueError('refutation context changed before native check')
                runtime = controller._runtime()
                if getattr(controller, 'document_mode', False):
                    _, current_state = controller._state(controller.theory)
                    runtime, _, rejected = controller._apply_pending_imports(runtime, current_state, directory)
                    if rejected is not None:
                        return {**rejected, 'checked_binding': _snapshot(controller)[2]}
                checked_binding = _snapshot(controller)[2]
                result = check_refutation(runtime[4], contract, proof, directory/'negative-check')
                result = {**result, 'checked_binding': checked_binding}
                if result.get('status') == 'refuted':
                    validate_refutation_certificate(result['certificate'], contract, runtime[4])
                    # Publish under the same transaction/cancellation authority
                    # as positive checks. The task's submitted binding remains
                    # intact; checked_binding records any accepted import event.
                    with acceptance_guard():
                        controller._check_session_active()
                        if _snapshot(controller)[2] != checked_binding:
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
        if _snapshot(controller)[2] != result.get('checked_binding', binding):
            raise ValueError('refutation context changed during native check')
        return {**result, 'task_id': task_id, 'theory_stop': False,
                'notice': 'No refutation accepted. Revise the negative proof or report_unresolved; positive goals are unchanged.'}
    outcome = (current_dispositions(controller).get(ids[0]) if contract_mode
               else current_outcome(controller))
    if outcome is None or outcome['status'] != 'refuted':
        raise NativePreparationError('completed refutation has no accepted outcome')
    if contract_mode:
        return {**_disposition_feedback(controller, outcome), 'task_id':task_id}
    return {'status': 'refuted', 'theory_outcome': outcome, 'theory_stop': True,
            'task_id': task_id, 'notice': 'Refutation is separate from positive proofs; this theory remains unsealed.'}


def _disposition_feedback(controller, outcome, *, retained=False):
    remaining = pending_contract_ids(controller)
    return {'status':outcome['status'], 'contract_disposition':outcome,
            'contract_stop':True, 'theory_stop':False,
            'remaining_contract_ids':remaining, 'all_contracts_disposed_or_proved':not remaining,
            'retained_from_prior_invocation':retained,
            'notice':('This contract supplies no positive fact. Continue other pending contracts; '
                      'when none remain, finish to publish the checked positive exports.')}


def handle_outcome(controller, proposal, metadata):
    contract_mode = contract_dispositions_enabled(controller.run)
    if not contract_mode and getattr(controller, 'theory_outcome', None):
        return {'theory_stop': True, 'theory_outcome': controller.theory_outcome}
    action = proposal.get('action')
    if action == 'report_unresolved':
        if set(proposal)-{'action', 'reason', 'classification', 'entry_ids'}:
            raise ValueError('unknown report_unresolved fields')
        outcome = record_unresolved(controller, reason=proposal.get('reason'), origin='model',
            classification=proposal.get('classification', 'proof_search_exhausted'),
            entry_ids=proposal.get('entry_ids'), metadata=metadata)
        if contract_mode:
            return _disposition_feedback(controller, outcome)
        return {'status': 'unknown', 'theory_stop': True, 'theory_outcome': outcome,
                'notice': 'Unresolved report preserved; it does not establish falsehood or unprovability.'}
    if action == 'check_refutation':
        return _check_refutation(controller, proposal, metadata)
    raise ValueError('unknown theory outcome action')
