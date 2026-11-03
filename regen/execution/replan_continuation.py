"""Continue a reviewed repository plan without promoting old-context proofs.

The first version keeps every theory's fixed contract sequence. Advice, order and
required providers may change. Verified source histories retain their original
packages; new imports are draft edits and use the ordinary native transaction.
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import shutil
import hashlib

from regen.execution.proof_commits import digest, durable


def validate_package_transition(previous, current, *, sealed=False):
    from regen.planning.import_contract import required_imports
    for package in (previous, current):
        body = {k:v for k,v in package.items() if k != 'package_payload_sha256'}
        if digest(body) != package.get('package_payload_sha256'):
            raise ValueError('replanning package digest mismatch')
    for key in ('theory', 'file_slot', 'contracts'):
        if previous.get(key) != current.get(key):
            raise ValueError('replanning continuation changes fixed '+key)
    if sealed and required_imports(previous['repository_projection']) != required_imports(current['repository_projection']):
        raise ValueError('replanning cannot change sealed imports')


def _packages(schedule):
    from regen.execution.repository_execution import read, projection_package
    from regen.planning.review_handoff import validate_handoff
    plan = read(schedule.parent/'repository-plan.json')
    handoff = read(schedule.parent/'review-handoff.json')
    validate_handoff(handoff, plan)
    if handoff.get('legacy') or any(o.get('disposition') is None or o.get('status') == 'unresolved'
            for row in handoff['theories'].values() for o in row['obligations']):
        raise ValueError('replanning requires a reviewed nonlegacy plan')
    receipt = read(schedule.parent.parent/'frozen-plan.json')
    closure = read(schedule.parent.parent/'review-closure.json')
    if (receipt.get('state') != 'frozen' or closure.get('state') != 'closed'
            or receipt.get('review_closure_policy') != 'review-obligations-v1'
            or Path(receipt['plan']).resolve() != (schedule.parent/'repository-plan.json').resolve()
            or receipt.get('plan_sha256') != hashlib.sha256((schedule.parent/'repository-plan.json').read_bytes()).hexdigest()
            or receipt.get('review_handoff_sha256') != hashlib.sha256((schedule.parent/'review-handoff.json').read_bytes()).hexdigest()):
        raise ValueError('replanning requires a bound closed planning receipt')
    listing = read(schedule)
    result = {}
    for entry in listing['files']:
        projection = read(schedule.parent/entry['projection'])
        if digest(projection) != entry['projection_sha256']:
            raise ValueError('replanning projection changed')
        package = projection_package(projection, plan, review_handoff=handoff)
        if package['theory'] in result:
            raise ValueError('duplicate replanned theory')
        result[package['theory']] = package
    return result


def _stage_imports(destination, run, package, state, initial, desired, metadata):
    from regen.execution.import_revisions import effective_imports, native_imports
    from regen.planning.theory_document import DocumentStore
    before = effective_imports(state, initial)
    if before == desired:
        return None
    store = DocumentStore(destination/'theories'/Path(package['file_slot']).stem/'document',
        package, state, native_imports(run, before), allow_import_edits=True)
    document = store.read()
    result = store.edit({'action':'edit_theory', 'base_sha256':document['source_sha256'],
        'imports':native_imports(run, desired)}, metadata)
    return {'before':before, 'desired':desired,
            'previous_context_revision':state.get('context_revision',0),
            'draft_source_sha256':result['source_sha256'],
            'accepted_proofs_reused_in_new_context':False}


def pending_replanned_transition(root, run, package, state):
    """Return a still-unapplied import change, including removal of all providers."""
    from regen.execution.repository_execution import read
    path = Path(root)/'replanning-continuation.json'
    if not path.exists():
        return None
    receipt = read(path)
    if receipt['target_run_sha256'] != digest(run):
        raise ValueError('replanning continuation run changed')
    transition = receipt['pending_imports'].get(package['theory'])
    if transition is None or state.get('context_revision',0) > transition['previous_context_revision']:
        return None
    return transition


def pending_replanned_providers(root, run, package, state):
    """Hold the new hard dependency until its first native context transition."""
    from regen.planning.import_contract import required_imports
    if pending_replanned_transition(root,run,package,state) is None:
        return []
    return [p['theory'] for p in required_imports(package['repository_projection'])]


def carry_pending_replanning(source_dir, destination, run):
    """Ordinary continuation must retain pending plan imports and their epoch."""
    from regen.execution import repository_execution as ex
    source_dir,destination = Path(source_dir),Path(destination)
    path = source_dir/'replanning-continuation.json'
    if not path.exists():
        return None
    previous = ex.read(path); source = ex.read(source_dir/'run.json')
    if previous['target_run_sha256'] != digest(source):
        raise ValueError('source replanning continuation run changed')
    if run['session_schedule'] != source['session_schedule']:
        raise ValueError('ordinary continuation changed replanning schedule')
    receipt = {**previous,'source_run':str(source_dir.resolve()),'source_run_sha256':digest(source),
        'target_run_sha256':digest(run),'continued_replanning_receipt':deepcopy(previous)}
    durable(destination/'replanning-continuation.json',receipt,immutable=True)
    return digest(receipt)


def preflight_replanned_workspace(source_dir, schedule):
    """Read-only package/accepted-chain compatibility report; creates no run."""
    from regen.execution import repository_execution as ex
    source_dir, schedule = Path(source_dir).resolve(), Path(schedule).resolve()
    source = ex.read(source_dir/'run.json')
    ex._check_disposition_fork_support(source_dir,source)
    proposed = _packages(schedule)
    entries = ex.read(source_dir/'execution-input.json')['files']
    if set(proposed) != {e['theory'] for e in entries}:
        raise ValueError('replanning continuation preserves theory allocation')
    rows = []
    for entry in entries:
        previous = ex.package_for(source,entry['file_slot'])
        descriptor = ex._preflight_workspace_continuation(source_dir,source,entry,None)
        validate_package_transition(previous,proposed[entry['theory']],
            sealed=descriptor is not None and descriptor['status']=='sealed')
        rows.append({'theory':entry['theory'],'source_package_sha256':previous['package_payload_sha256'],
            'target_package_sha256':proposed[entry['theory']]['package_payload_sha256'],
            'source_commit':descriptor['source_commit'] if descriptor else None,
            'status':descriptor['status'] if descriptor else 'unstarted'})
    return {'source_run':str(source_dir),'schedule':str(schedule),'compatible':True,'theories':rows}


def fork_replanned_workspace(source_dir, destination, schedule, *, run_id, authorization):
    """Verify all old chains, publish new plan packages and stage changed imports.

    Caller holds the stopped source run's mutation lock. No native/model calls
    occur here. The effective budget must then be carried and planning charged
    before starting the new service. A partial destination is never replayable.
    """
    from regen.execution import repository_execution as ex
    from regen.execution.checker_policy import policy_binding, continuation_outcome_arguments
    from regen.execution.proof_session_runner import generated_checkpoint_theory
    from regen.infrastructure.incremental_log import copy_logged_file, policy_arguments
    from regen.planning.import_contract import required_imports
    source_dir, destination, schedule = map(lambda p:Path(p).resolve(), (source_dir,destination,schedule))
    if destination.exists():
        raise ValueError('replanning destination already exists')
    if not isinstance(authorization,str) or not authorization.strip():
        raise ValueError('replanning requires explicit authorization')
    source = ex.read(source_dir/'run.json')
    if not isinstance(run_id,str) or not run_id.strip() or run_id == source['run_id']:
        raise ValueError('replanning requires a new run identity')
    ex._check_disposition_fork_support(source_dir, source)
    proposed = _packages(schedule)
    entries = ex.read(source_dir/'execution-input.json')['files']
    if set(proposed) != {e['theory'] for e in entries}:
        raise ValueError('replanning continuation preserves theory allocation')
    descriptors, outcomes = [], []
    for entry in entries:
        previous = ex.package_for(source, entry['file_slot'])
        descriptor = ex._preflight_workspace_continuation(source_dir,source,entry,None)
        sealed = descriptor is not None and descriptor['status'] == 'sealed'
        validate_package_transition(previous,proposed[entry['theory']],sealed=sealed)
        if descriptor is not None:
            descriptors.append(descriptor)
        outcome = ex._continuation_outcome(source_dir,source,entry)
        if outcome:
            if outcome['outcome']['status'] != 'unknown':
                raise ValueError('replanning does not recertify refuted outcomes')
            outcomes.append(outcome)
    if not descriptors:
        raise ValueError('replanning source has no verified proof history')
    run = ex.init_run(destination,Path(source.get('checker_context_root') or source['environment_run']),
        run_id,schedule,**policy_binding(source),**continuation_outcome_arguments(source),
        **({'execution_models':source['execution_models']} if 'execution_models' in source else {}),
        **policy_arguments(source))
    if any(run.get(k) != source.get(k) for k in ex.CONTINUATION_ENV_FIELDS):
        raise ValueError('replanning changed semantic environment')
    target_entries = {e['theory']:e for e in ex.read(destination/'execution-input.json')['files']}
    carried, sealed_theories, transitions = [], [], {}
    for descriptor in descriptors:
        previous, origin, state, _ = ex._workspace_continuation_input(
            source_dir,source,descriptor['entry'],None,expected=descriptor)
        package = ex.package_for(run,target_entries[previous['theory']]['file_slot'])
        if package != proposed[package['theory']]:
            raise ValueError('replanning package changed during initialization')
        origin['replanned_from_package'] = previous
        if descriptor['status'] == 'sealed':
            carried.append(ex._reuse_sealed_theory(destination,run,package,origin,state))
            sealed_theories.append(package['theory'])
            continue
        old_dir = source_dir/'theories'/Path(previous['file_slot']).stem
        new_dir = destination/'theories'/Path(package['file_slot']).stem
        shutil.copytree(old_dir,new_dir,copy_function=copy_logged_file,ignore=shutil.ignore_patterns(
            'acceptance','isabelle','heaps','provider-session.json','progress.json','active-graph.json',
            'declaration-index.json','runtime-policy.json','batch-policy.json',
            'execution-stop.json','PROOF-RUN-REPORT.md'))
        graph_check = next((r['evidence']['graph_check'] if r['event']=='batch' else r['evidence']['check'])
            for r in reversed(origin['commits']) if r['event'] in {'initial','revision','revoke'}
            or r['event']=='batch' and r['evidence'].get('graph_check') is not None)
        evidence = {'check':graph_check,'continuation':origin}
        if any(n['statement'] not in graph_check['source_content'] for n in state['graph']['nodes']):
            evidence['declaration_coverage'] = ex.continuation_declaration_coverage(origin,state)
        ex.journal(destination,run,package).append('initial',state,evidence)
        # Adopt explicit plan changes only. Unchanged plans retain any accepted
        # runtime import revisions rather than resetting them to old advice.
        if (required_imports(previous['repository_projection']) != required_imports(package['repository_projection'])
                or pending_replanned_transition(source_dir,source,previous,state) is not None):
            desired = [generated_checkpoint_theory(target_entries[p['theory']]['file_slot'])
                       for p in required_imports(package['repository_projection'])]
            transition = _stage_imports(destination,run,package,state,origin['imports'],desired,
                {'origin':'reviewed-replanning','authorization':authorization,'plan_id':package['plan_id']})
            if transition:
                transitions[package['theory']] = transition
        carried.append({'theory':package['theory'],'source_commit':descriptor['source_commit'],
            'status':state['status'],'source_package_sha256':previous['package_payload_sha256'],
            'target_package_sha256':package['package_payload_sha256']})
    # Old UNKNOWN is retained as an explicit stop and then reopened once under
    # this authorization. New review decisions supersede old advice, not proofs.
    reopened = []
    from regen.planning.theory_outcomes import _publish, reopen_unresolved
    for item in outcomes:
        if ex._continuation_outcome(source_dir,source,item['entry']) != item:
            raise ValueError('source outcome changed during replanning')
        package = ex.package_for(run,item['entry']['file_slot'])
        state = ex.journal(destination,run,package).replay(repair=False)
        if state is None:
            state = ex._unstarted_outcome_state(package,run,document_mode=True)
        view = SimpleNamespace(root=destination,run=run,theory=package['theory'],
            _state=lambda _,p=package,s=state:(deepcopy(p),deepcopy(s)))
        previous = item['outcome']
        _publish(view,status='unknown',reason=previous['reason'],origin=previous['origin'],
            details=previous['details'],entry_ids=previous['entry_ids'] or None,
            classification=previous['classification'],metadata={**previous['metadata'],
                'replanning_source_outcome':previous,'source_run':str(source_dir)})
        event = reopen_unresolved(view,package['theory'],authorization=authorization,
            reason='Reviewed repository plan revision; preserve old UNKNOWN and all charged history')
        reopened.append({'theory':package['theory'],'event_id':event['event_id']})
    history = {}
    for folder in ('review-handoff','review-decisions','provider-waits','theory-outcomes'):
        history[folder] = {str(path.relative_to(source_dir)):digest(ex.read(path))
            for path in sorted((source_dir/folder).glob('**/events/*.json'))}
    durable(destination/'replanning-continuation.json',{
        'schema_version':1,'source_run':str(source_dir),'source_run_sha256':digest(source),
        'target_run_sha256':digest(run),'authorization':authorization,
        'source_schedule':source['session_schedule'],'target_schedule':run['session_schedule'],
        'theories':carried,'pending_imports':transitions,'reopened_unknown':reopened,
        'superseded_coordination':history,'proofs_recertified':False,'budget_reset':False},immutable=True)
    durable(destination/'workspace-continuation.json',{
        'source_run':str(source_dir),'theories':[c for c in carried if c['theory'] not in sealed_theories],
        'sealed_theories':sealed_theories,'reason':'explicitly authorized reviewed plan continuation',
        'replanning_receipt_sha256':digest(ex.read(destination/'replanning-continuation.json')),
        'policy':'verify old packages and chains; changed imports remain pending native revalidation'},immutable=True)
    ex.progress(destination)
    return run
