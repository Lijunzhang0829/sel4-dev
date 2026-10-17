"""Fresh serial generic Codex experiment using the common application."""
from contextlib import contextmanager, ExitStack
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import tempfile
import threading
import time
from regen.execution.check_executor import CURRENT_RESOURCES, shared_host_binding
from regen.execution.proof_commits import durable, digest
from regen.infrastructure.incremental_log import enable, read_artifact, FORMAT
from regen.infrastructure.audited_llm import model_settings
from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession,ProviderOverloaded,ProviderHTTPError,SessionStalled
from regen.infrastructure.experiment_ledger import Ledger, RolloutUsageReader
from regen.infrastructure.experiment_activity import UsageSnapshots, experiment_summary
from regen.infrastructure.theory_activity import TheoryActivityStop, POLICY
from regen.infrastructure.generation_activity_bridge import GenerationActivityBridge
from regen.infrastructure.progress_events import observe_events
from regen.infrastructure.provider_http_failure import recovery_decision,recovery_streak,RECOVERY_POLICY
from regen.planning.generic_codex_agent import Controller,registry,instructions


PROVIDER_RECOVERY_SCOPE='__generic_codex_provider__'
DEFAULT_PROVIDER_RECOVERY_WINDOW_SECONDS=1800
DEFAULT_AUTH_LEASE_DIR=Path('/tmp')/f'regen-auth-chain-leases-{os.getuid()}'
_LOCAL_LOCK_FILESYSTEMS=frozenset({'ext2','ext3','ext4','xfs','btrfs',
    'tmpfs','overlay','zfs','f2fs'})


def application_signature():
    """Bind executable sources, not bulky logs or generated proof contents."""
    package=Path(__file__).resolve().parents[1]
    files=[p for folder in ('execution','planning','infrastructure','analysis')
        for p in (package/folder).iterdir() if p.suffix in {'.py','.ML','.sh'}]
    files.append(Path(__file__).resolve())
    return digest({str(p.relative_to(package)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(files)})


def validate_generic_model_roles(settings):
    roles=settings.get('role_models') if isinstance(settings,dict) else None
    if (not isinstance(roles,dict) or not isinstance(roles.get('File Planner'),str)
            or not roles['File Planner'].strip()):
        raise ValueError('generic model config requires a File Planner role before model launch')
    return roles


def provider_recovery_window(args):
    seconds=getattr(args,'provider_recovery_window_seconds',
        DEFAULT_PROVIDER_RECOVERY_WINDOW_SECONDS)
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<=0:
        raise ValueError('provider recovery window must be positive and finite')
    return seconds


def budget_options(args):
    # Old programmatic clients stay legacy unless they explicitly select the new
    # protocol. The public CLI below defaults to the new 48-hour budget.
    mode=getattr(args,'budget_mode','theory-inactivity')
    if mode not in ('effective-time','active-wall','theory-inactivity'):raise ValueError('unknown budget mode')
    seconds=(getattr(args,'wall_seconds',172800) if mode=='active-wall'
             else getattr(args,'effective_seconds',172800))
    authorization=getattr(args,'resume_authorization',None)
    extension=(getattr(args,'extend_wall_seconds',None) if mode=='active-wall'
               else getattr(args,'extend_effective_seconds',None))
    if mode=='active-wall' and getattr(args,'extend_effective_seconds',None) is not None:
        raise ValueError('active-wall uses --extend-wall-seconds')
    if mode!='active-wall' and getattr(args,'extend_wall_seconds',None) is not None:
        raise ValueError('--extend-wall-seconds requires --budget-mode active-wall')
    if mode in ('effective-time','active-wall'):
        import math
        if type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<=0:
            raise ValueError('effective budget must be positive and finite')
        if getattr(args,'continue_from',None):
            if not isinstance(authorization,str) or not authorization.strip():
                raise ValueError('--continue-from requires --resume-authorization')
        elif authorization or extension is not None:
            raise ValueError('resume authorization and extension require --continue-from')
        if extension is not None and (type(extension) not in (int,float) or not math.isfinite(extension) or extension<=0):
            raise ValueError('effective extension must be positive and finite')
    elif authorization or extension is not None:
        raise ValueError('time-budget resume/extension options require a budgeted mode')
    return mode,seconds,authorization,extension


def _copy_private_auth(source,destination):
    """Atomically persist credentials with mode 0600, outside public artifacts."""
    source=Path(source);destination=Path(destination)
    if source.is_symlink() or not source.is_file():
        raise ValueError('provider authentication source must be a regular file')
    fd,temporary=tempfile.mkstemp(prefix='.auth-',dir=destination.parent)
    try:
        with os.fdopen(fd,'wb') as stream,source.open('rb') as original:
            os.fchmod(stream.fileno(),0o600)
            shutil.copyfileobj(original,stream)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,destination)
        directory=os.open(destination.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def _auth_chain_fingerprint(path):
    """Compare active seeds without writing credential material to a receipt."""
    raw=Path(path).read_bytes()
    try:
        data=json.loads(raw)
        tokens=data.get('tokens') if isinstance(data,dict) else None
        refresh=(tokens.get('refresh_token') if isinstance(tokens,dict) else None)
        if not refresh and isinstance(data,dict):refresh=data.get('refresh_token')
        if isinstance(refresh,str) and refresh:
            return hashlib.sha256(('refresh-token\0'+refresh).encode()).hexdigest()
    except (UnicodeError,ValueError,TypeError):
        pass
    return hashlib.sha256(b'auth-file\0'+raw).hexdigest()


def _local_lock_filesystem(path):
    """Resolve the deepest Linux mount; reject remote/unknown lock semantics."""
    target=Path(path).resolve(strict=True)
    selected=(-1,None)
    with open('/proc/self/mountinfo',encoding='utf-8') as stream:
        for line in stream:
            columns=line.split(' - ',1)
            if len(columns)!=2:continue
            left,right=columns
            parts=left.split()
            if len(parts)<5:continue
            mount=Path(re.sub(r'\\([0-7]{3})',
                lambda match:chr(int(match[1],8)),parts[4]))
            if target==mount or target.is_relative_to(mount):
                depth=len(str(mount))
                if depth>selected[0]:selected=(depth,right.split()[0])
    if selected[1] not in _LOCAL_LOCK_FILESYSTEMS:
        raise ValueError('provider auth lease requires a known local filesystem')


def _private_lease_dir(path):
    base=Path(path).absolute()
    parent=base.parent
    for ancestor in (base,*base.parents):
        if ancestor.is_symlink():
            raise ValueError('provider auth lease path cannot contain a symlink')
    for ancestor in (parent,*parent.parents):
        info=ancestor.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('provider auth lease parent must be a directory')
        if info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
            raise ValueError('provider auth lease parent is writable by another user')
    base.mkdir(mode=0o700,exist_ok=True)
    directory=os.open(base,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        info=os.fstat(directory)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid()
                or stat.S_IMODE(info.st_mode)!=0o700):
            raise ValueError('provider auth lease store must be owned and mode 0700')
        _local_lock_filesystem(base)
        return directory
    except BaseException:
        os.close(directory)
        raise


@contextmanager
def provider_auth_lease(root,*sources,lease_dir=None):
    """Refuse concurrent launches from one seed across output parents.

    The default is a host/user-wide private local directory. Both launchers
    must use the *same mounted directory* when they run in different mount
    namespaces; a separate container /tmp is not shared protection. This only
    detects identical initial refresh tokens, not every equivalent account
    session, so independent chains still require prelaunch verification.
    """
    names=set()
    for source in sources:
        path=Path(source)
        if path.is_symlink() or not path.is_file():
            raise ValueError('provider authentication source must be a regular file')
        names.add('path-'+hashlib.sha256(str(path.resolve()).encode()).hexdigest())
        names.add('chain-'+_auth_chain_fingerprint(path))
    base=Path(lease_dir) if lease_dir is not None else DEFAULT_AUTH_LEASE_DIR
    directory=_private_lease_dir(base)
    streams=[]
    try:
        for name in sorted(names):
            fd=os.open(name,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,
                dir_fd=directory)
            stream=os.fdopen(fd,'r+b')
            streams.append(stream)
            info=os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid()
                    or stat.S_IMODE(info.st_mode)!=0o600):
                raise ValueError('provider auth lease file must be owned and mode 0600')
            try:fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError('provider authentication seed is active in another run') from error
        yield
    finally:
        for stream in reversed(streams):
            try:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
            finally:stream.close()
        os.close(directory)


def provider_auth_source(seed_auth,*,continue_from=None):
    seed=Path(seed_auth).absolute()
    if seed.is_symlink() or not seed.is_file():
        raise ValueError('provider authentication seed must be a regular file')
    if continue_from is None:
        return seed,'provided_seed'
    base=seed.parent/'.regen-provider-auth'
    previous=base/hashlib.sha256(str(Path(continue_from).resolve()).encode()).hexdigest()/'auth.json'
    if previous.is_symlink() or not previous.is_file():
        raise ValueError('continuation private provider authentication state is unavailable')
    return previous,'continuation_private_state'


def private_provider_auth(root,seed_auth,*,continue_from=None):
    """One persistent, run-specific auth store beside the supplied private seed.

    The output tree may be exported for analysis; it must contain no credential.
    Only an ephemeral clean HOME is mounted into each model process. Refreshes
    from that HOME are durably copied back here after the process has closed.
    """
    seed=Path(seed_auth).absolute()
    source,origin=provider_auth_source(seed,continue_from=continue_from)
    base=seed.parent/'.regen-provider-auth'
    base.mkdir(mode=0o700,exist_ok=True)
    if base.is_symlink() or not base.is_dir() or base.stat().st_mode & 0o077:
        raise ValueError('provider authentication store must be a private directory')
    run_dir=base/hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()
    run_dir.mkdir(mode=0o700)
    auth=run_dir/'auth.json'
    if auth.exists() or auth.is_symlink():
        raise ValueError('provider authentication state already exists for this output')
    _copy_private_auth(source,auth)
    return auth,origin,source


@contextmanager
def fresh_provider_home(seed_auth):
    """New isolated config home per transport; retain refreshed private auth only."""
    with tempfile.TemporaryDirectory(prefix='regen-provider-auth-') as home:
        auth=Path(home)/'auth.json'
        _copy_private_auth(seed_auth,auth)
        try:
            yield home
        finally:
            _copy_private_auth(auth,seed_auth)


def provider_overload_backoff(stage,ledger,retry,error,decision=None):
    stage.check()
    delay=(decision['delay_seconds'] if decision is not None else
           min(300,30*2**min(retry-1,4)))
    event='provider_http_retry' if isinstance(error,ProviderHTTPError) else 'provider_overload_retry'
    ledger.append(event,attempt=retry,delay_seconds=delay,
        error_type=type(error).__name__,failure=(error.failure if isinstance(error,ProviderHTTPError)
            else {'kind':'provider_capacity','http_status':503} if isinstance(error,ProviderOverloaded)
            else None),retry_policy=decision.get('retry_policy') if decision else None,
        policy='same model; new thread; preserve workspace and costs; no pending-call replay; confirmed recovery excluded from effective generation')
    stage.event.wait(delay)
    stage.check()


def run(args):
    # Ownership must survive all final ledger/checkpoint writes, and be released
    # even when those writes fail (for example, after a disk-full interruption).
    with ExitStack() as ownership:
        return _run(args,ownership)


def _run(args,ownership):
    budget_mode,budget_seconds,resume_authorization,extension_seconds=budget_options(args)
    recovery_window_seconds=provider_recovery_window(args)
    budgeted=budget_mode in ('effective-time','active-wall')
    root=Path(args.output).resolve();root.mkdir(parents=True,exist_ok=False)
    planning_input=Path(args.dataset).resolve()/'repository-planning-input.json'
    expected_contracts=getattr(args,'expected_contracts',None)
    if expected_contracts is not None or budgeted:
        fixed_contract_count=len(json.loads(planning_input.read_text())['contracts'])
        if expected_contracts is not None and fixed_contract_count!=expected_contracts:
            raise ValueError(f'fixed contract inventory mismatch: expected {expected_contracts}, found {fixed_contract_count}')
        planning_input_sha256=hashlib.sha256(planning_input.read_bytes()).hexdigest()
    else:
        fixed_contract_count=planning_input_sha256=None
    prior=os.environ.get('REGEN_MODEL_CONFIG')
    os.environ['REGEN_MODEL_CONFIG']=str(Path(args.model_config).resolve())
    controller=None;stage=None;token=None;ledger=None;bridge=None;event_context=None;source_ownership=None
    report={'passed':False,'entry':'generic-serial-application-v1','turns':[],
        'usage_basis':'latest thread cumulative snapshot, never sum snapshots'}
    started=time.monotonic()
    try:
        enable(root)
        if budgeted:
            from regen.infrastructure.generic_continuation import exclusive_run
            ownership.enter_context(exclusive_run(root))
        ledger=Ledger(root,root.name)
        continuation=None
        settings=model_settings()
        validate_generic_model_roles(settings)
        client=Path(settings['codex_bin']).resolve()
        if str(client)!=settings['codex_bin'] or hashlib.sha256(client.read_bytes()).hexdigest()!=settings['codex_sha256']:
            raise ValueError('deployment client path/hash mismatch')
        config=dict(run_id=root.name,model=args.model,effort=args.reasoning,threads=4,
            transport_idle_timeout=args.idle_timeout,preflight_seconds=getattr(args,'preflight_seconds',900),
            plan_policy='locked-after-acceptance-v1',inactivity_policy=POLICY if budget_mode=='theory-inactivity' else None,
            provider_retry_limit=getattr(args,'provider_retry_limit',1),
            provider_recovery_policy=RECOVERY_POLICY,
            provider_recovery_window_seconds=recovery_window_seconds,
            provider_auth_store='external-private-v1',
            native_slots=1,resident_limit=2,native_cpus=4,native_memory_mb=16384,native_pids_limit=1024,
            provider_build_timeout=1800,check_timeout=args.check_timeout,candidate_timeout=getattr(args,'candidate_timeout',120))
        config.update(shared_host_binding(getattr(args,'shared_host_lock_dir',None),
            getattr(args,'shared_host_slots',1),
            getattr(args,'shared_host_cleanup_timeout',30)))
        if budgeted:
            if budget_mode=='active-wall':
                from regen.infrastructure.active_wall_budget import ActiveWallBudget as TimeBudget, POLICY as TIME_POLICY
            else:
                from regen.infrastructure.effective_time_budget import EffectiveTimeBudget as TimeBudget, POLICY as TIME_POLICY
            config.update(budget_mode=budget_mode,time_budget_policy=TIME_POLICY,
                application_sha256=application_signature(),planning_input_sha256=planning_input_sha256,
                model_config_sha256=hashlib.sha256(Path(args.model_config).read_bytes()).hexdigest())
        if getattr(args,'continue_from',None):
            from regen.infrastructure.generic_continuation import prepare, continuation_ownership
            inherited_config=read_artifact(Path(args.continue_from)/'launch.json')['config']
            for key in ('provider_recovery_policy','provider_recovery_window_seconds',
                        'provider_auth_store'):
                if inherited_config.get(key)!=config[key]:
                    raise ValueError('continuation policy differs: '+key)
            provider_auth_source(args.auth,continue_from=args.continue_from)
            ledger=None
            if budgeted:
                source_ownership=ownership.enter_context(continuation_ownership(args.continue_from))
            ledger,continuation=prepare(root,args.continue_from,args.dataset,config,args.inactivity_seconds,
                restore_before_revision=getattr(args,'restore_before_revision',None),
                effective_seconds=budget_seconds if budget_mode=='effective-time' else None,
                wall_seconds=budget_seconds if budget_mode=='active-wall' else None,
                resume_authorization=resume_authorization,_source_locked=source_ownership is not None)
        from regen.infrastructure.generic_continuation import prompt as continuation_prompt
        prompt_options=({'wall_seconds':budget_seconds} if budget_mode=='active-wall' else
                        {'effective_seconds':budget_seconds} if budgeted else {})
        stage=(TimeBudget(ledger,budget_seconds,resume_authorization=resume_authorization,
            extension_seconds=extension_seconds) if budgeted else
            TheoryActivityStop(ledger,args.inactivity_seconds))
        if budgeted:
            # Extensions alter the authorized cap, not the frozen base binding.
            prompt_options['wall_seconds' if budget_mode=='active-wall' else 'effective_seconds']=stage.snapshot()['seconds']
        initial_prompt=instructions(args.inactivity_seconds,**prompt_options)+(continuation_prompt(continuation) if continuation else '')
        report['preparation_elapsed_seconds']=time.monotonic()-started
        report['continuation']=continuation
        started=time.monotonic()
        ledger.append('experiment_resumed' if continuation else 'experiment_started',model=args.model,
            reasoning_effort=args.reasoning,log_storage=FORMAT,continuation=continuation)
        durable(root/'launch.json',{'config':config,'dataset':str(Path(args.dataset).resolve()),
            'fixed_contract_count':fixed_contract_count,'expected_contract_count':expected_contracts,
            'planning_input_sha256':planning_input_sha256,
            'model_config':str(Path(args.model_config).resolve()),'inactivity_seconds':args.inactivity_seconds,'fresh_only':not bool(continuation),'continuation':continuation,'log_storage':FORMAT,
            'budget_mode':budget_mode,'effective_seconds':budget_seconds if budget_mode=='effective-time' else None,
            'wall_seconds':budget_seconds if budget_mode=='active-wall' else None,
            'resume_authorization':resume_authorization,'extension_seconds':extension_seconds,
            'prompt_sha256':digest(initial_prompt),'registry_sha256':digest(registry()),
            'model_config_sha256':hashlib.sha256(Path(args.model_config).read_bytes()).hexdigest()})
        if continuation and continuation.get('restored_before_revision'):
            ledger.append('plan_restored',epoch=continuation['restored_epoch'],
                cutoff_sequence=continuation['visible_history_cutoff'],
                policy='user-authorized rollback; all historical cost retained')
        bridge=GenerationActivityBridge(stage)
        event_context=observe_events(bridge.handle,publication_guard=bridge.acceptance);event_context.__enter__()
        bridge.recovering()  # Includes journal/controller restoration before the first model turn.
        usage=UsageSnapshots(ledger)
        controller=Controller(root,Path(args.dataset).resolve(),ledger,stage,config)
        if continuation:
            if continuation.get('restored_scope'):controller.scope=continuation['restored_scope']
            controller.observe_updates()
        token=CURRENT_RESOURCES.set(controller.resources)
        def abort():
            try:controller.executor.close()
            finally:controller.planner.native_check.abort()
        stage.start(abort=abort)
        old_term=signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM,lambda *_:stage.cancel())
        try:
            auth_source,_=provider_auth_source(args.auth,
                continue_from=getattr(args,'continue_from',None))
            ownership.enter_context(provider_auth_lease(root,args.auth,auth_source))
            auth,auth_origin,_=private_provider_auth(root,args.auth,
                continue_from=getattr(args,'continue_from',None))
            ledger.append('provider_auth_store_ready',origin=auth_origin,
                storage='external-private-v1',parallel_chain_policy='shared-seed-refused')
            retry=0;consecutive_failures=0;recovery_started_at=None
            while True:
                try:
                    bridge.recovering()
                    with fresh_provider_home(auth) as attempt_home:
                        with CodexWorkspaceSession(tools=registry(),codex_bin=str(client),codex_home=attempt_home,allow_external_call=True,http_retry_mode=True) as session:
                                turn=0
                                binding={}
                                rollout=RolloutUsageReader(attempt_home,binding,ledger,snapshots=usage)
                                def notification(method,params):
                                    binding['thread_id']=session.thread_id
                                    rollout.poll(scope=controller.scope,invocation='generic-session',model=args.model,effort=args.reasoning)
                                    if method=='thread/tokenUsage/updated':
                                        usage.record(params.get('threadId',session.thread_id),
                                            (params.get('tokenUsage') or {}).get('total'),scope=controller.scope,
                                            source='app-server cumulative notification',turn_id=session.turn_id,
                                            evidence=session.last_notification_receipt)
                                try:
                                    while True:
                                        stage.check()
                                        result=session.run(initial_prompt if turn==0 else
                                            'Continue using tool feedback until repository integration is verified.',
                                            controller.handle,artifact_dir=(root/'provider' if retry==0 else root/'provider-retries'/f'{retry:04d}'),model=args.model,
                                            reasoning_effort=args.reasoning,timeout=args.idle_timeout,
                                            cancel_event=stage.event,on_abort=abort,on_notification=notification)
                                        ledger.append('provider_transport_progress',
                                            theory=PROVIDER_RECOVERY_SCOPE,
                                            thread_id=session.thread_id,turn_id=session.turn_id)
                                        consecutive_failures=0;recovery_started_at=None
                                        turn+=1
                                        report['turns'].append(result)
                                        report['usage']=result.get('usage')
                                        durable(root/'model-progress.json',report)
                                        if any(r['kind']=='integrated' and r['data']['verified'] for r in ledger.rows):
                                            if budgeted:stage.finish('verified_integration')
                                            report['passed']=True;break
                                        if getattr(controller,'infrastructure_blocker',None):
                                            raise RuntimeError(str(controller.infrastructure_blocker))
                                finally:
                                    # Preserve last observed counters even if a turn/transport failed.
                                    # Read private rollout receipts before removing the auth home.
                                    report['usage']=session._usage
                                    binding['thread_id']=session.thread_id
                                    try:
                                        usage.record(session.thread_id,(session._usage or {}).get('total'),
                                            scope=controller.scope,source='final session cumulative counter',turn_id=session.turn_id)
                                        rollout.poll(scope=controller.scope,invocation='generic-session',model=args.model,effort=args.reasoning)
                                    except Exception as error:
                                        report['accounting_error']={'type':type(error).__name__,'message':str(error)}
                                        ledger.append('accounting_error',**report['accounting_error'])
                    break
                except (ProviderOverloaded,ProviderHTTPError,SessionStalled) as error:
                    # The session owns the failed transport and native callback.
                    # Its context must close before another controller is created.
                    cleanup_failed=bool(getattr(error,'__notes__',()))
                    if isinstance(error,(ProviderOverloaded,ProviderHTTPError)):
                        failure=(error.failure if isinstance(error,ProviderHTTPError) else
                            {'kind':'provider_capacity','http_status':503})
                        now=time.monotonic()
                        if recovery_started_at is None:recovery_started_at=now
                        previous=recovery_streak(ledger.rows,PROVIDER_RECOVERY_SCOPE,failure)
                        decision=recovery_decision(failure,previous,
                            enabled=config['provider_retry_limit']>0,
                            cleanup_failed=cleanup_failed,
                            elapsed_seconds=now-recovery_started_at,
                            window_seconds=config['provider_recovery_window_seconds'],
                            pause_auth_immediately=True)
                        if not decision['retry']:
                            ledger.append('provider_recovery_paused',reason=decision['reason'],
                                failure=failure,consecutive_failures=previous,
                                retry_policy=RECOVERY_POLICY)
                            stage.cancel(decision['reason'])
                            raise
                        consecutive_failures=decision['consecutive_failures']
                    else:
                        consecutive_failures+=1
                        if (consecutive_failures>config['provider_retry_limit']
                                or cleanup_failed):
                            stage.cancel('provider_recovery_exhausted')
                            raise
                        decision=None
                    retry+=1
                    if decision is not None:
                        ledger.append('provider_http_retry_scheduled',
                            theory=PROVIDER_RECOVERY_SCOPE,
                            consecutive_failures=decision['consecutive_failures'],
                            failure=failure,retry_policy=RECOVERY_POLICY,
                            delay_seconds=decision['delay_seconds'],attempt=retry)
                    # Never reuse this transport/executor or replay a pending call.
                    scope=controller.scope
                    controller.close()
                    CURRENT_RESOURCES.reset(token);token=None
                    provider_overload_backoff(stage,ledger,retry,error,decision)
                    controller=Controller(root,Path(args.dataset).resolve(),ledger,stage,config)
                    controller.scope=scope
                    token=CURRENT_RESOURCES.set(controller.resources)
                    initial_prompt=instructions(args.inactivity_seconds,**prompt_options)+(
                        '\nContinue this existing experiment after a confirmed provider failure. '
                        'Read experiment_status and the current workspace. The accepted '
                        'plan is locked and existing proofs/drafts are retained. '
                        'This is a new provider thread; no pending tool was replayed. '
                        'Do not submit a new repository plan.\n')
        finally:
            signal.signal(signal.SIGTERM,old_term)
    except BaseException as error:
        if stage and budgeted and not stage.event.is_set():
            stage.cancel('infrastructure_error')
        report['error']={'type':type(error).__name__,'message':str(error)}
        if ledger:ledger.append('run_error',**report['error'])
    finally:
        if bridge:
            try:bridge.close()
            except Exception as error:
                report['clock_cleanup_error']=str(error)
                report['passed']=False
        if event_context:
            try:event_context.__exit__(None,None,None)
            except Exception as error:
                report['clock_cleanup_error']=str(error)
                report['passed']=False
        if stage:
            try:stage.close()
            except Exception as error:
                # Failed checkpoint persistence must not skip native cleanup or
                # leave the watchdog active. These flags stop this process only;
                # they do NOT claim that the durable clock was safely saved.
                stage.event.set();stage.closed.set()
                report['clock_persistence_error']={'type':type(error).__name__,'message':str(error)}
                report['clock_cleanup_error']=str(error)
                report['passed']=False
            try:report['generation_clock']=stage.snapshot()
            except Exception as error:
                stage.event.set();stage.closed.set()
                report['clock_snapshot_error']={'type':type(error).__name__,'message':str(error)}
                report['clock_cleanup_error']=str(error)
                report['passed']=False
        report['stop_reason']=stage.reason if stage else None
        try:
            if controller:controller.close()
        except Exception as error:
            report['passed']=False;report['cleanup_error']=str(error)
        finally:
            if token is not None:CURRENT_RESOURCES.reset(token)
            if prior is None:os.environ.pop('REGEN_MODEL_CONFIG',None)
            else:os.environ['REGEN_MODEL_CONFIG']=prior
        report['elapsed_seconds']=time.monotonic()-started
        if budgeted:
            report['status']='completed' if report['passed'] else 'paused'
            report['recovery_requires_reconciliation']=bool(report.get('clock_cleanup_error'))
            report['resumable']=bool(stage and report.get('generation_clock')
                and not report['generation_clock'].get('finished')
                and not report['recovery_requires_reconciliation'])
        if ledger:
            ending='experiment_finished' if not budgeted or report['passed'] else 'experiment_paused'
            if ending=='experiment_paused':
                # Usually the clock already published termination. Initializer
                # or cleanup failures still need a closed accounting segment.
                segment=max((i for i,r in enumerate(ledger.rows)
                    if r['kind'] in ('experiment_started','experiment_resumed')),default=0)
                if not any(r['kind']=='termination' and r['data'].get('disposition')=='pause'
                           for r in ledger.rows[segment:]):
                    ledger.append('termination',reason=report.get('stop_reason') or 'runner_paused',
                        disposition='pause',resumable=report.get('resumable',False))
            ledger.append(ending,passed=report['passed'],reason=report.get('stop_reason'),
                error=report.get('error'),cleanup_error=report.get('cleanup_error'),
                elapsed_seconds=report['elapsed_seconds'],accounting_error=report.get('accounting_error'))
            durable(root/'experiment-summary.json',experiment_summary(ledger))
        durable(root/'result.json',report)
    return report


def main():
    parser=argparse.ArgumentParser()
    for name in ('dataset','output','model-config','auth'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--continue-from',help='Explicit continuation into a fresh output directory; validates and forks existing proof state')
    parser.add_argument('--restore-before-revision',type=int,help='Restore the accepted plan/workspace immediately before this revision; requires --continue-from')
    parser.add_argument('--model',required=True)
    parser.add_argument('--reasoning',required=True)
    parser.add_argument('--expected-contracts',type=int,required=True)
    parser.add_argument('--inactivity-seconds',type=int,default=3600)
    parser.add_argument('--budget-mode',choices=('effective-time','active-wall','theory-inactivity'),default='effective-time',help='Select cumulative effective generation or active wall time; inactivity mode is legacy')
    parser.add_argument('--effective-seconds',type=float,default=172800,help='Frozen base effective-generation allowance; default 48 hours, no token cap')
    parser.add_argument('--wall-seconds',type=float,default=172800,help='Frozen active wall-clock allowance; used with --budget-mode active-wall')
    parser.add_argument('--resume-authorization',help='Explicit authorization/reason for continuing an existing time-budget experiment')
    parser.add_argument('--extend-effective-seconds',type=float,help='Authorized additional effective seconds; requires --continue-from and --resume-authorization')
    parser.add_argument('--extend-wall-seconds',type=float,help='Authorized additional active wall seconds; requires --continue-from and --resume-authorization')
    parser.add_argument('--provider-retry-limit',type=int,default=1,
        help='Enable bounded provider recovery and cap non-HTTP session-stall retries; default 1')
    parser.add_argument('--provider-recovery-window-seconds',type=float,
        default=DEFAULT_PROVIDER_RECOVERY_WINDOW_SECONDS,
        help='Maximum consecutive transient provider recovery interval; default 30 minutes')
    parser.add_argument('--idle-timeout',type=int,default=900)
    parser.add_argument('--preflight-seconds',type=int,default=900,help='Repository declaration preflight wall limit; matches Workspace Planner default')
    parser.add_argument('--check-timeout',type=int,default=900)
    parser.add_argument('--candidate-timeout',type=int,default=120)
    parser.add_argument('--shared-host-lock-dir',
        help='Opt in to one shared native slot directory mounted by every experiment')
    parser.add_argument('--shared-host-slots',type=int,default=1)
    parser.add_argument('--shared-host-cleanup-timeout',type=float,default=30)
    args=parser.parse_args()
    try:
        budget_options(args);provider_recovery_window(args)
        shared_host_binding(args.shared_host_lock_dir,args.shared_host_slots,args.shared_host_cleanup_timeout)
    except ValueError as error:parser.error(str(error))
    if min(args.expected_contracts,args.inactivity_seconds,args.idle_timeout,args.preflight_seconds,args.check_timeout,args.candidate_timeout)<=0:parser.error('budgets and expected contract count must be positive')
    if args.provider_retry_limit<0 or args.provider_retry_limit>3:parser.error('provider retry limit must be between 0 and 3')
    if args.restore_before_revision is not None and not args.continue_from:parser.error('--restore-before-revision requires --continue-from')
    report=run(args)
    display={k:report[k] for k in ('status','passed','resumable','recovery_requires_reconciliation',
        'stop_reason','elapsed_seconds','error','cleanup_error','clock_persistence_error','clock_snapshot_error') if k in report}
    if report.get('generation_clock'):
        clock=report['generation_clock']
        display['generation_clock']={k:clock[k] for k in ('policy','effective_seconds','seconds',
            'remaining_seconds','gate_state','paused','finished','accounting_complete') if k in clock}
    print(json.dumps(display))
    raise SystemExit(not report['passed'])


if __name__=='__main__':main()
