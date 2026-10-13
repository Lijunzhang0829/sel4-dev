"""Fresh serial generic Codex experiment using the common application."""
from contextlib import contextmanager, ExitStack
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import tempfile
import threading
import time
from regen.execution.check_executor import CURRENT_RESOURCES
from regen.execution.proof_commits import durable, digest
from regen.infrastructure.incremental_log import enable, FORMAT
from regen.infrastructure.audited_llm import model_settings
from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession,ProviderOverloaded,ProviderHTTPError,SessionStalled
from regen.infrastructure.experiment_ledger import Ledger, RolloutUsageReader
from regen.infrastructure.experiment_activity import UsageSnapshots, experiment_summary
from regen.infrastructure.theory_activity import TheoryActivityStop, POLICY
from regen.infrastructure.generation_activity_bridge import GenerationActivityBridge
from regen.infrastructure.progress_events import observe_events
from regen.planning.generic_codex_agent import Controller,registry,instructions


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


@contextmanager
def fresh_provider_home(seed_auth):
    """New isolated config home per transport; retain refreshed private auth only."""
    with tempfile.TemporaryDirectory(prefix='regen-provider-auth-') as home:
        auth=Path(home)/'auth.json'
        shutil.copyfile(seed_auth,auth);auth.chmod(0o600)
        try:
            yield home
        finally:
            if auth.is_file() and not auth.is_symlink():
                shutil.copyfile(auth,seed_auth)
                Path(seed_auth).chmod(0o600)


def provider_overload_backoff(stage,ledger,retry,error):
    stage.check()
    delay=min(300,30*2**min(retry-1,4))
    event='provider_http_retry' if isinstance(error,ProviderHTTPError) else 'provider_overload_retry'
    ledger.append(event,attempt=retry,delay_seconds=delay,
        error_type=type(error).__name__,message=str(error),
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
            native_slots=1,resident_limit=2,native_cpus=4,native_memory_mb=16384,native_pids_limit=1024,
            provider_build_timeout=1800,check_timeout=args.check_timeout,candidate_timeout=getattr(args,'candidate_timeout',120))
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
            with tempfile.TemporaryDirectory(prefix='regen-generic-auth-') as home:
                auth=Path(home)/'auth.json';shutil.copyfile(args.auth,auth);auth.chmod(0o600)
                retry=0;consecutive_failures=0
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
                                        consecutive_failures=0
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
                        consecutive_failures+=1
                        # Do not overlap replacement resources with failed cleanup,
                        # or wait indefinitely while the generation clock is frozen.
                        if (consecutive_failures>config['provider_retry_limit']
                                or getattr(error,'__notes__',())):
                            stage.cancel('provider_recovery_exhausted')
                            raise
                        retry+=1
                        provider_overload_backoff(stage,ledger,retry,error)
                        # The failed session aborts its native resources. Never reuse
                        # that broken transport/executor or replay pending requests.
                        scope=controller.scope
                        controller.close()
                        CURRENT_RESOURCES.reset(token);token=None
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
    parser.add_argument('--provider-retry-limit',type=int,default=1,help='Consecutive confirmed provider failures retried before pausing; same default as A')
    parser.add_argument('--idle-timeout',type=int,default=900)
    parser.add_argument('--preflight-seconds',type=int,default=900,help='Repository declaration preflight wall limit; matches Workspace Planner default')
    parser.add_argument('--check-timeout',type=int,default=900)
    parser.add_argument('--candidate-timeout',type=int,default=120)
    args=parser.parse_args()
    try:budget_options(args)
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
