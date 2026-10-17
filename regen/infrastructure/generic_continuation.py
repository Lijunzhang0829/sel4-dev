"""Explicit generic-agent continuation through verified workspace forks.

Never edits a source run or replays its pending tool calls. A new provider thread
receives workspace state; it is not a recovery of the vanished ephemeral thread.
"""
import hashlib
import json
import shutil
import fcntl
from contextlib import contextmanager, nullcontext, ExitStack
from pathlib import Path
from regen.execution.proof_commits import durable,digest
from regen.infrastructure.incremental_log import read_artifact,write_artifact,copy_logged_file,find_root
from regen.infrastructure.experiment_ledger import Ledger


_OVERLOAD_007_MIGRATION = {
    'migration_id': 'codex-007-terminal-overload-clock-repair-v1',
    'source_application_sha256': '3c0186eafd9769852942eab92ac499e78d5de2277a120857332eaabcdb13bd1d',
    'source_session_sha256': '1892de028d58889e57c60ec6ae960facedf2868bde1f326018a790e7d71cd999',
    'repaired_session_sha256': 'f91513e5aa0d9b7ff9633b79683b3709416723d0b61f5cfb722a63781ca43a63',
    'source_last_event_id': '3f98e45714093858423c66be32a07b4af67444abed51cc1f5bec0084f4c1f52a',
    'source_checkpoint_event_id': '527b6fa6ca2f948762cb953aeb121ecb6c425af17dee50819d7b663d35de46de',
    'source_clock_error_event_id': '88ed0f0731525baadd57d1bae6bfeaa75b0bcd359e9f80fb4b21b447bec8f5c0',
}


def _implementation_files(project):
    package=Path(project)/'regen'
    files=[p for folder in ('execution','planning','infrastructure','analysis')
        for p in (package/folder).iterdir() if p.suffix in {'.py','.ML','.sh'}]
    files.append(package/'tools/generic_serial_application.py')
    return {str(p.relative_to(package)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in files}


def _audited_overload_migration(source, old_config, config, history):
    """One exact 007 diagnostic continuation; never reclassify its old ledger."""
    expected=_OVERLOAD_007_MIGRATION
    if (Path(source).name!='codex-sol-medium-active-wall-48h-007'
            or old_config.get('application_sha256')!=expected['source_application_sha256']
            or config.get('budget_mode')!='active-wall'
            or history.rows[-1]['event_id']!=expected['source_last_event_id']
            or history.rows[-1]['kind']!='experiment_paused'
            or history.rows[-1]['data'].get('reason')!='effective_clock_evidence_error'):
        raise ValueError('continuation binding differs: application_sha256 (not audited 007 overload migration)')
    checkpoint=next((r for r in reversed(history.rows)
        if r['kind']=='time_budget_checkpoint'),None)
    clock_error=next((r for r in reversed(history.rows)
        if r['kind']=='generation_clock_untrusted'),None)
    if (checkpoint is None or checkpoint['event_id']!=expected['source_checkpoint_event_id']
            or checkpoint['data'].get('boundary')!='close'
            or clock_error is None or clock_error['event_id']!=expected['source_clock_error_event_id']
            or clock_error['data']['evidence'].get('reason')!='session_stopped_with_open_model_item'):
        raise ValueError('audited overload checkpoint or evidence differs')
    source_files=_implementation_files(Path(source).parent/'project')
    repaired_files=_implementation_files(Path(__file__).resolve().parents[2])
    if (set(source_files)!=set(repaired_files)
            or source_files.get('infrastructure/codex_workspace_session.py')!=expected['source_session_sha256']
            or repaired_files.get('infrastructure/codex_workspace_session.py')!=expected['repaired_session_sha256']
            or digest(source_files)!=old_config['application_sha256']
            or digest(repaired_files)!=config['application_sha256']):
        raise ValueError('audited overload source or repair differs')
    changed={name for name in source_files if source_files[name]!=repaired_files[name]}
    if changed!={'infrastructure/codex_workspace_session.py',
                 'infrastructure/generic_continuation.py'}:
        raise ValueError('audited overload migration changed unrelated executable code')
    return {'migration_id':expected['migration_id'],
        'source_application_sha256':old_config['application_sha256'],
        'repaired_application_sha256':config['application_sha256'],
        'source_session_sha256':expected['source_session_sha256'],
        'repaired_session_sha256':expected['repaired_session_sha256'],
        'source_last_event_id':expected['source_last_event_id'],
        'source_checkpoint_event_id':expected['source_checkpoint_event_id'],
        'source_clock_error_event_id':expected['source_clock_error_event_id'],
        'changed_files':sorted(changed),
        'policy':'retain all old events, proofs, tokens and 105895.31 charged seconds; new thread; no pending call replay'}


@contextmanager
def exclusive_run(root, *, source=False):
    """One writer, or one stable recovery reader, per time-budget run."""
    path=Path(root)/'effective-run.lock'
    if source and not path.is_file():
        raise ValueError('source lacks effective-run ownership lock; explicit reconciliation required')
    with path.open('a+b') as stream:
        try:
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('experiment source/output still has an active owner') from error
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


@contextmanager
def continuation_ownership(source):
    """Hold the complete explicit continuation lineage, not a broad directory.

    Locks live until the caller finishes its resumed run. This prevents two fresh
    output directories from each spending the same inherited budget concurrently,
    including when their immediate source directories differ.
    """
    lineage=[];seen=set();current=Path(source).resolve()
    while True:
        if current in seen:raise ValueError('cyclic continuation lineage')
        if len(lineage)>=128:raise ValueError('continuation lineage exceeds safe lock depth')
        seen.add(current);lineage.append(current)
        launch=read_artifact(current/'launch.json')
        if launch['config'].get('budget_mode') not in ('effective-time','active-wall'):
            raise ValueError('legacy inactivity history cannot be migrated to a time budget')
        origin_path=current/'continuation-origin.json'
        if not origin_path.exists():break
        origin=read_artifact(origin_path)
        previous=origin.get('source_root')
        if not isinstance(previous,str) or not Path(previous).is_absolute():
            raise ValueError('continuation lineage requires an absolute source root')
        current=Path(previous).resolve()
    with ExitStack() as held:
        # A fixed ordering and nonblocking acquisition avoid lock-order deadlocks.
        for path in sorted(lineage):held.enter_context(exclusive_run(path,source=True))
        yield tuple(lineage)


def prepare(root, source, dataset, config, inactivity_seconds, *, restore_before_revision=None,
            effective_seconds=None, wall_seconds=None, resume_authorization=None, _source_locked=False):
    effective=config.get('budget_mode') in ('effective-time','active-wall')
    if effective and (not isinstance(resume_authorization,str) or not resume_authorization.strip()):
        raise ValueError('effective-time continuation requires explicit resume authorization')
    launch=read_artifact(Path(source)/'launch.json')
    if effective and launch['config'].get('budget_mode')!=config.get('budget_mode'):
        if launch['config'].get('budget_mode') not in ('effective-time','active-wall'):
            raise ValueError('legacy inactivity history cannot be migrated to a time budget')
        raise ValueError('continuation time-budget mode differs')
    with continuation_ownership(source) if effective and not _source_locked else nullcontext():
        return _prepare(root,source,dataset,config,inactivity_seconds,
            restore_before_revision=restore_before_revision,effective_seconds=effective_seconds,
            wall_seconds=wall_seconds,resume_authorization=resume_authorization)


def _prepare(root, source, dataset, config, inactivity_seconds, *, restore_before_revision=None,
             effective_seconds=None, wall_seconds=None, resume_authorization=None):
    root,source,dataset=Path(root).resolve(),Path(source).resolve(),Path(dataset).resolve()
    if root==source or root.is_relative_to(source):raise ValueError('continuation needs a separate output root')
    launch=read_artifact(source/'launch.json');old_config=launch['config']
    if Path(launch['dataset']).resolve()!=dataset:raise ValueError('continuation dataset differs')
    for key in ('model','effort','threads','transport_idle_timeout','preflight_seconds','check_timeout','candidate_timeout'):
        if config[key]!=old_config[key]:raise ValueError('continuation config differs: '+key)
    effective=config.get('budget_mode') in ('effective-time','active-wall')
    if effective:
        budget_key='wall_seconds' if config['budget_mode']=='active-wall' else 'effective_seconds'
        requested_seconds=wall_seconds if budget_key=='wall_seconds' else effective_seconds
        if launch.get(budget_key)!=requested_seconds:
            raise ValueError('continuation base effective budget differs; use an explicit extension')
        for key in ('budget_mode','time_budget_policy','model_config_sha256','planning_input_sha256'):
            if not config.get(key) or config[key]!=old_config.get(key):
                raise ValueError('continuation binding differs: '+key)
    elif launch['inactivity_seconds']!=inactivity_seconds:
        raise ValueError('continuation inactivity policy differs')
    for key in ('inactivity_policy','provider_retry_limit','native_slots','resident_limit',
                'native_cpus','native_memory_mb','native_pids_limit','provider_build_timeout',
                'shared_host_lock_dir','shared_host_slots','shared_host_cleanup_timeout'):
        if config.get(key)!=old_config.get(key):
            raise ValueError('continuation policy differs: '+key)
    result=(read_artifact(source/'result.json') if (source/'result.json').exists() else
            {'passed':False,'error':{'message':'unclean shutdown; resume from verified journals'}}) if effective else read_artifact(source/'result.json')
    if result.get('passed'):raise ValueError('completed experiment does not need continuation')
    first=read_artifact(next(iter(sorted((source/'events').glob('*.json')))))
    history=Ledger(source,first['run_id'])
    migration=None
    if effective and config.get('application_sha256')!=old_config.get('application_sha256'):
        migration=_audited_overload_migration(source,old_config,config,history)
    if not history.rows or (not effective and history.rows[-1]['kind']!='experiment_finished'):
        raise ValueError('source experiment has no terminal ledger event')
    if effective:
        states=[r for r in history.rows if r['kind']=='time_budget_checkpoint']
        if not states:
            raise ValueError('source lacks persisted effective-time checkpoint; cannot reset elapsed time')
    elif config.get('inactivity_policy'):
        states=[r for r in history.rows if r['kind']=='theory_activity_state']
        if not states or states[-1]['data'].get('boundary')!='close':
            raise ValueError('effective clock has unobserved shutdown tail; explicit reconciliation required')
    hidden=[]
    if (source/'continuation-origin.json').exists():hidden=read_artifact(source/'continuation-origin.json').get('hidden_event_ranges',[])
    visible=history.rows
    checkpoint=None
    if restore_before_revision is not None:
        cuts=[r for r in history.rows if r['kind']=='plan_revision_started' and r['data']['epoch']==restore_before_revision]
        if len(cuts)!=1:raise ValueError('rollback revision must identify exactly one event')
        cut=cuts[0]
        visible=[r for r in history.rows if r['sequence']<cut['sequence']]
        previous=[r for r in visible if r['kind']=='plan_accepted']
        if not previous:raise ValueError('rollback has no preceding accepted plan')
        current={k:previous[-1]['data'][k] for k in ('epoch','run','plan_id','allocation')}
        current['run']=str(source/'proof-runs'/f"{current['epoch']:04d}")
        hidden.append([cut['sequence'],history.rows[-1]['sequence']])
        checkpoint=rollback_checkpoint(Path(current['run']),current,visible,cut['time_unix'])
    elif (source/'current-plan.json').exists():
        current=read_artifact(source/'current-plan.json')
    elif effective:
        # Planning itself is restarted, not resumed from model fragments. Its
        # historical costs and time-budget journal remain immutable and charged.
        if any(r['kind']=='plan_accepted' for r in history.rows):
            raise ValueError('accepted plan pointer missing; explicit journal reconciliation required')
        current=None
    else:
        current=read_artifact(source/'current-plan.json')
    if current is None:
        return _prepare_unplanned(root,source,history,result,resume_authorization,config.get('budget_mode'),
            requested_seconds if effective else None)
    proof_source=Path(current['run']).resolve()
    if proof_source!=source/'proof-runs'/f"{current['epoch']:04d}":raise ValueError('unexpected current proof run location')
    accepted=[r for r in history.rows if r['kind']=='plan_accepted' and r['data']['epoch']==current['epoch']]
    inherited=False
    if (source/'continuation-origin.json').exists():
        origin=read_artifact(source/'continuation-origin.json')
        original={**current,'run':str(Path(origin['source_root'])/'proof-runs'/f"{current['epoch']:04d}")}
        inherited=digest(original)==origin.get('source_current_plan_sha256')
    keys=('plan_id','allocation') if restore_before_revision is not None or inherited else ('run','plan_id','allocation')
    if not accepted or any(accepted[-1]['data'][k]!=current[k] for k in keys):
        raise ValueError('current plan differs from accepted ledger event')
    # Copy exact logical events, preserving original hashes/timestamps/costs.
    # The continued ledger retains its experiment identity, not the folder name.
    if find_root(source/'launch.json') == source:
        # Copy immutable object bytes once; re-encoding every event fsyncs each
        # object and makes large-history recovery unnecessarily expensive.
        # The source Ledger above and destination Ledger below validate the
        # complete logical chain and referenced object hashes independently.
        if any((root/'log-objects').iterdir()):raise ValueError('continuation object destination is not empty')
        shutil.copytree(source/'log-objects',root/'log-objects',dirs_exist_ok=True)
        shutil.copytree(source/'events',root/'events')
    else:
        for row in history.rows:write_artifact(root/'events'/f"{row['sequence']:09d}.json",row,immutable=True)
    ledger=Ledger(root,history.run_id)
    if restore_before_revision is None:
        shutil.copytree(source/'plans',root/'plans',copy_function=copy_logged_file)
        if (source/'planning-draft.json').exists():copy_logged_file(source/'planning-draft.json',root/'planning-draft.json')
    else:
        for folder in sorted((source/'plans').iterdir()):
            if folder.is_dir() and folder.name.isdigit() and int(folder.name)<=current['epoch']:
                shutil.copytree(folder,root/'plans'/folder.name,copy_function=copy_logged_file)
        durable(root/'planning-draft.json',{'epoch':current['epoch'],'reason':'user-authorized restore; accepted plan locked'})
    from regen.execution.repository_execution import fork_workspace_continuation
    destination=root/'proof-runs'/f"{current['epoch']:04d}"
    if checkpoint is None:fork_workspace_continuation(proof_source,destination)
    else:fork_workspace_continuation(proof_source,destination,checkpoint=checkpoint)
    durable(root/'current-plan.json',{**current,'run':str(destination)},immutable=True)
    # Source epochs are references only, read at their original validated paths.
    references={str(r['data']['epoch']):r['data']['run'] for r in visible if r['kind']=='plan_accepted'}
    scopes=[r['data'] for r in visible if r['kind']=='scope']
    binding={'source_root':str(source),'source_ledger_run_id':history.run_id,
        'source_last_event_id':history.rows[-1]['event_id'],'source_event_count':len(history.rows),
        'source_current_plan_sha256':digest(current),'prior_proof_runs':references,
        'restored_scope':scopes[-1] if scopes else None,
        'recovery_mode':'new-provider-thread-from-verified-workspace',
        'old_thread_restored':False,'pending_tool_replayed':False,
        'reason':'explicit user authorization after infrastructure repair',
        'source_error':(result.get('error') or {}).get('message','').splitlines()[:1]}
    binding.update(hidden_event_ranges=hidden,restored_before_revision=restore_before_revision,
        restored_epoch=current['epoch'],visible_history_cutoff=visible[-1]['sequence'],
        plan_policy='locked-after-acceptance-v1')
    if effective:
        binding.update(budget_mode=config['budget_mode'],resume_authorization=resume_authorization,
            source_checkpoint_event=states[-1]['event_id'],time_budget_base_seconds=requested_seconds,
            source_clean_shutdown=history.rows[-1]['kind'] in ('experiment_finished','experiment_paused'))
    if restore_before_revision is not None:
        binding['source_error']=['User requested rollback before replanning; no post-cutoff proof content supplied.']
        durable(root/'restore-checkpoint.json',checkpoint,immutable=True)
    durable(root/'continuation-origin.json',binding,immutable=True)
    if migration:
        ledger.append('implementation_migration',**migration)
    return ledger,binding


def _prepare_unplanned(root,source,history,result,authorization,budget_mode='effective-time',
                       budget_seconds=None):
    """Restart unaccepted repository planning without erasing prior costs."""
    if any((root/'log-objects').iterdir()):raise ValueError('continuation object destination is not empty')
    if find_root(source/'launch.json')==source:
        shutil.copytree(source/'log-objects',root/'log-objects',dirs_exist_ok=True)
        shutil.copytree(source/'events',root/'events')
    else:
        for row in history.rows:write_artifact(root/'events'/f"{row['sequence']:09d}.json",row,immutable=True)
    ledger=Ledger(root,history.run_id)
    binding={'source_root':str(source),'source_ledger_run_id':history.run_id,
        'source_last_event_id':history.rows[-1]['event_id'],'source_event_count':len(history.rows),
        'restored_scope':None,'prior_proof_runs':{},'pending_tool_replayed':False,
        'old_thread_restored':False,'recovery_mode':'restart-unaccepted-repository-planning',
        'planning_restarted':True,'resume_authorization':authorization,'budget_mode':budget_mode,
        'time_budget_base_seconds':budget_seconds,
        'source_error':(result.get('error') or {}).get('message','').splitlines()[:1]}
    durable(root/'continuation-origin.json',binding,immutable=True)
    return ledger,binding


def prompt(binding):
    if binding.get('planning_restarted'):
        return ('\nThis is an authorized continuation. No repository plan was accepted. '
            'Restart repository planning from the frozen inputs; do not resume incomplete '
            'planning or replay pending tools. Prior time/token costs remain charged.\n')
    return ('\nThis is an explicitly authorized continuation of the SAME experiment. '
        'Its accepted allocation, drafts, verified proofs, seals, notes and costs are retained. '
        'The previous ephemeral model thread ended and cannot be resumed; this is a new thread. '
        'Repository planning has already been accepted. Inspect experiment_status and workspace '
        'tools before editing; continue from current proofs rather than discarding the accepted plan. '
        'The accepted theory allocation and imports are now locked by user instruction. '
        'Do not replan, split or merge theories, or move contracts. Explore proofs and helpers '
        'only within their existing theories. Historical costs remain charged. '
        'Previous error: '+json.dumps(binding['source_error'])+'. '
        'No pending tool was replayed. Search limit remains 1..50 as in the shared A interface; '
        'invalid arguments now receive nonexecution feedback that you can correct.\n')


def rollback_checkpoint(proof_source,current,visible,cut_time):
    """Select accepted journal prefixes and only pre-cutoff draft/feedback files."""
    from regen.execution.shared_proof_workspace import SharedProofWorkspace
    ws=SharedProofWorkspace(proof_source)
    checkpoint={'source_run':str(proof_source),'theories':{}}
    for theory in current['allocation']:
        snap=ws._snapshot(theory)
        records=[r for r in visible if r['kind']=='proof_commit' and
            r['data'].get('epoch')==current['epoch'] and r['data']['theory']==theory]
        if snap is None:
            if records:raise ValueError('missing historical theory state')
            continue
        if not records or snap['commit_id']!=records[-1]['data']['commit_id']:
            raise ValueError('rollback theory has post-cutoff or unobserved commits: '+theory)
        selected={'commit_id':snap['commit_id']}
        directory=Path(snap['directory'])
        if snap['state']['status']!='sealed':
            draft=directory/'document/draft.thy'
            if draft.exists():
                if draft.stat().st_mtime>cut_time:raise ValueError('post-cutoff draft: '+theory)
                version=hashlib.sha256(draft.read_bytes()).hexdigest()
                saved=read_artifact(directory/'document/versions'/version/'source.json')
                if saved['source'].encode()!=draft.read_bytes():raise ValueError('draft/version mismatch')
                selected['draft_version']=version
            artifacts={}
            for path in (directory/'batch-requests').glob('*/*'):
                if not path.is_file():continue
                if path.stat().st_mtime>cut_time:raise ValueError('post-cutoff batch artifact')
                if int(path.parent.name)>snap['state'].get('batch_requests',0):raise ValueError('uncommitted batch artifact')
                artifacts[str(path.relative_to(directory))]=hashlib.sha256(path.read_bytes()).hexdigest()
            selected['artifacts']=artifacts
        checkpoint['theories'][theory]=selected
    return checkpoint
