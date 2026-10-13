"""Bounded diagnostic-window observer around the existing workspace service.

It does not launch a model or reset a production inactivity clock. Each new arm
retains its forked historical costs and has its own explicitly bound trial window.
"""
import argparse
import json
import math
from pathlib import Path
import signal
import re
import subprocess
import sys
import time

from regen.execution.proof_commits import durable
from regen.execution.proof_commits import digest
from regen.infrastructure.incremental_log import read_artifact
from regen.tools import parallel_workspace_service as service
from regen.tools import proof_idle_watch as watch
from regen.tools import theory_effective_clock as clock


def validate_budget(value):
    if not math.isfinite(value) or value <= 0:
        raise ValueError('budget_effective_seconds must be finite and positive')
    return value


def validate_limits(budget_effective_seconds, wall_limit):
    validate_budget(budget_effective_seconds)
    if not math.isfinite(wall_limit) or wall_limit <= 0:
        raise ValueError('wall_limit must be finite and positive')


def native_exit_gate(root, *, grace_seconds=65):
    """Only logged PID/UUID identities with the native owner protocol are stoppable."""
    root=Path(root)
    log=root/'service-runner.log'
    if not log.exists():
        return {'owned_native_containers':[], 'all_exited':True}
    invocation=read_artifact(root/'service-invocation.json')
    pid=invocation['main_pid']
    names=set()
    for line in log.read_text().splitlines():
        if not line.startswith('[regen-event] '):
            continue
        row=json.loads(line[len('[regen-event] '):])
        if row.get('event') not in {'isabelle_build_started','isabelle_started'}:
            continue
        name=row.get('container','')
        if not re.fullmatch(r'regen-isabelle-(?:build|check)-'+str(pid)+r'-[0-9a-f]{32}',name):
            raise RuntimeError('native event ownership does not match service PID/UUID')
        names.add(name)
    resident={}
    for directory in root.rglob('regen-*'):
        if re.fullmatch(r'regen-(?:batch|prefix)-[0-9a-f]{16}',directory.name) and directory.is_dir():
            if not directory.resolve().is_relative_to(root.resolve()) or directory.is_symlink():
                raise RuntimeError('resident ownership directory escapes arm')
            if (directory/'owner_watchdog.sh').is_file():
                resident[directory.name]=directory.resolve()
                names.add(directory.name)
    def running(name):
        result=subprocess.run(['docker','inspect',name],capture_output=True,text=True,timeout=15)
        if result.returncode:
            missing = re.fullmatch(
                r'(?:error(?: response from daemon)?:\s*)?no such (?:object|container):\s*(\S+)',
                result.stderr.strip(), re.IGNORECASE)
            if missing and missing.group(1) == name:
                return False
            raise RuntimeError('native identity inspection failed: '+result.stderr)
        info=json.loads(result.stdout)[0]
        command=info['Config']['Cmd']
        owner=['/bin/sh','/native-owner/owner.sh','60','/native-owner/owner.heartbeat']
        if name in resident:
            owner=['/bin/sh','/worker/owner_watchdog.sh','60','/worker/owner.heartbeat']
            if not any(m.get('Destination')=='/worker' and Path(m['Source']).resolve()==resident[name]
                       for m in info.get('Mounts',[])):
                raise RuntimeError('resident container mount identity differs')
        if (info['Name']!='/'+name or command[:4]!=owner):
            raise RuntimeError('native container owner protocol differs; refusing cleanup')
        return info['State']['Running']
    deadline=time.monotonic()+grace_seconds
    pending={name for name in names if running(name)}
    while pending and time.monotonic()<deadline:
        time.sleep(min(2,max(0,deadline-time.monotonic())))
        pending={name for name in pending if running(name)}
    for name in pending:
        if running(name):
            result=subprocess.run(['docker','stop','--timeout','10',name],capture_output=True,text=True,timeout=20)
            if result.returncode and running(name):
                raise RuntimeError('owned native stop failed: '+name)
        if running(name):
            raise RuntimeError('owned native remains alive: '+name)
    return {'owned_native_containers':sorted(names),'all_exited':True}


def prepare_and_run(control, *, checkpoint=None, application_manifest=None, prior_trial=None,
                    budget_effective_seconds=3600, wall_limit=10800):
    """The persistent service owns preparation and model execution from inception."""
    from regen.tools.application_release import verify
    validate_limits(budget_effective_seconds, wall_limit)
    control=Path(control).resolve()
    application=Path(__file__).resolve().parents[2]
    verify(application,application_manifest)
    control.mkdir(parents=True,exist_ok=False)
    receipt={'checkpoint':str(Path(checkpoint).resolve()),'checkpoint_sha256':watch.sha(checkpoint),
        'application_manifest':str(Path(application_manifest).resolve()),
        'application_manifest_sha256':watch.sha(application_manifest),
        'prior_trial':str(Path(prior_trial).resolve()),
        'prior_trial_costs_preserved':True,'arms':str(control/'arms'),
        'budget_effective_seconds':budget_effective_seconds,'wall_safety_limit_seconds':wall_limit}
    durable(control/'control-receipt.json',receipt,immutable=True)
    old={}
    def terminate(signum,frame):
        raise KeyboardInterrupt('supervised preparation interrupted')
    for signum in (signal.SIGTERM,signal.SIGHUP):
        old[signum]=signal.signal(signum,terminate)
    failure=None
    try:
        prepare_pair(checkpoint,control/'arms')
        if watch.sha(checkpoint)!=receipt['checkpoint_sha256']:
            raise RuntimeError('checkpoint changed during preparation')
        durable(control/'prepared-binding.json',{'control_receipt_sha256':watch.sha(control/'control-receipt.json'),
            'preparation_sha256':watch.sha(control/'arms/preparation.json')},immutable=True)
        return run_pair(control/'arms',control/'comparison',application_manifest,
                        budget_effective_seconds=budget_effective_seconds,wall_limit=wall_limit)
    except BaseException as error:
        failure=error
        raise
    finally:
        try:
            stop_workflow(control)
        except BaseException as error:
            durable(control/'cleanup-error.json',{'type':type(error).__name__,'message':str(error),
                'original_error':None if failure is None else {
                    'type':type(failure).__name__,'message':str(failure)}},immutable=True)
            if failure is None:
                raise
        finally:
            for signum,handler in old.items():
                signal.signal(signum,handler)


def stop_workflow(control):
    control=Path(control).resolve()
    receipt_path=control/'control-receipt.json'
    if not receipt_path.exists():
        if any(control.glob('arms/*/service-launch.json')):
            raise RuntimeError('unbound workflow has service receipts')
        return {'preparation_only':True,'services_launched':False}
    receipt=read_artifact(receipt_path)
    if Path(receipt['arms'])!=control/'arms':
        raise RuntimeError('workflow arms escape control directory')
    binding_path=control/'prepared-binding.json'
    if not binding_path.exists():
        if any((control/'arms').glob('*/service-launch.json')):
            raise RuntimeError('service launched without preparation binding')
        return {'preparation_only':True,'services_launched':False}
    binding=read_artifact(binding_path)
    if binding['control_receipt_sha256']!=watch.sha(receipt_path):
        raise RuntimeError('workflow control receipt changed')
    return stop_pair(control/'arms',binding['preparation_sha256'])


def prepare_pair(checkpoint_path, output):
    from regen.execution.repository_execution import fork_workspace_continuation
    checkpoint_path, output = Path(checkpoint_path).resolve(), Path(output).resolve()
    checkpoint = read_artifact(checkpoint_path)
    source = Path(checkpoint['source_run'])
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('diagnostic forks must be separate from production source')
    output.mkdir(parents=True,exist_ok=False)
    rows = []
    for strategy in ('baseline','helper-closure-v1'):
        destination = output/strategy
        started = time.monotonic()
        run = fork_workspace_continuation(source,destination,checkpoint=checkpoint)
        initial = read_artifact(destination/'theories/Scheduler_Dispatch/acceptance/commits/000001.json')
        rows.append({'strategy':strategy,'run_dir':str(destination),'run_id':run['run_id'],
                     'initial_state_sha256':digest(initial['state']),
                     'preparation_seconds':time.monotonic()-started})
    if rows[0]['initial_state_sha256'] != rows[1]['initial_state_sha256']:
        raise ValueError('A/B inherited states differ')
    result={'checkpoint_sha256':watch.sha(checkpoint_path),'arms':rows,
            'same_inherited_state':True,'native_executions':0,'model_calls':0,
            'source_run':str(source),'historical_costs_reset':False}
    durable(output/'preparation.json',result,immutable=True)
    return result


def run_pair(arms, output, application_manifest, *, budget_effective_seconds=3600, wall_limit=10800):
    """Supervise ordinary frozen services sequentially; never call a model here."""
    from regen.tools.application_release import verify
    from regen.infrastructure.audited_llm import model_config_path, model_settings
    validate_limits(budget_effective_seconds, wall_limit)
    application = Path(__file__).resolve().parents[2]
    verify(application,application_manifest)
    settings=model_settings()
    if (model_config_path() != application/'regen/config/proof-models.json'
            or settings['role_models']['File Planner']!='gpt-6-astra'
            or settings['reasoning_effort']!='medium'):
        raise ValueError('diagnostic pair requires release-bound gpt-6-astra medium model configuration')
    arms, output = Path(arms).resolve(), Path(output).resolve()
    preparation = read_artifact(arms/'preparation.json')
    if (not preparation['same_inherited_state']
            or [a['strategy'] for a in preparation['arms']] != ['baseline','helper-closure-v1']):
        raise ValueError('invalid matched diagnostic pair')
    output.mkdir(parents=True,exist_ok=False)
    report={'application_manifest_sha256':watch.sha(application_manifest),
            'preparation_sha256':watch.sha(arms/'preparation.json'),
            'arms':[],'comparison_complete':False,'replicates_per_arm':1,
            'budget_effective_seconds':budget_effective_seconds,'wall_safety_limit_seconds':wall_limit}
    active = None
    old_signals = {}
    def terminate(signum, frame):
        raise KeyboardInterrupt('diagnostic comparison terminated by signal '+str(signum))
    for signum in (signal.SIGTERM,signal.SIGHUP):
        old_signals[signum]=signal.signal(signum,terminate)
    try:
        for row in preparation['arms']:
            verify(application,application_manifest)
            active = Path(row['run_dir'])
            if active.parent != arms:
                raise ValueError('diagnostic arm escapes prepared pair')
            initial = read_artifact(active/'theories/Scheduler_Dispatch/acceptance/commits/000001.json')
            if digest(initial['state']) != row['initial_state_sha256']:
                raise ValueError('diagnostic inherited state changed')
            command=[sys.executable,'-u','-m','regen.execution.proof_session_runner','parallel-workspace',
                '--run-dir',str(active),'--theory','Scheduler_Dispatch','--planner-model','gpt-6-astra',
                '--call-timeout','900','--check-timeout','900','--candidate-timeout','120',
                '--max-parallel-theories','1','--native-slots','1','--native-threads','4',
                '--resident-limit','1','--native-cpus','4','--native-memory-mb','16384',
                '--provider-build-timeout','1800','--file-planner-interface','document',
                '--generation-strategy',row['strategy'],'--allow-external-call']
            durable(active/'experiment.json',{'command':command,'cwd':str(application),
                'resource_requirements':{'min_free_disk_bytes':10*1024**3,
                                         'min_available_memory_bytes':18*1024**3},
                'diagnostic_window_effective_seconds':budget_effective_seconds,'historical_costs_reset':False,
                'wall_safety_limit_seconds':wall_limit,
                'production_resumed':False,'application_manifest_sha256':report['application_manifest_sha256']},immutable=True)
            report.update(phase='launching',active_strategy=row['strategy'],active_run_dir=str(active))
            durable(output/'progress.json',report)
            service.start(active)
            report['phase']='observing'
            durable(output/'progress.json',report)
            result=observe(active,output/row['strategy'],'Scheduler_Dispatch',
                           budget_effective_seconds=budget_effective_seconds,wall_limit=wall_limit)
            report['arms'].append({'strategy':row['strategy'],'report':result})
            if result['final_service']['running'] or result['cleanup_error']:
                raise RuntimeError('previous diagnostic service has not stopped')
            result['native_exit_gate']=native_exit_gate(active)
            if (not result.get('window_result_verified') or result.get('reason') not in
                    {'first_new_contract','trial_effective_budget_exhausted'}):
                raise RuntimeError('diagnostic arm did not reach a verified normal terminal edge')
            durable(output/'progress.json',report)
            active=None
        report.update(comparison_complete=True,phase='complete',active_strategy=None,active_run_dir=None)
        report['limitations']=['One stochastic run per arm; observed outcome does not establish a general success-rate improvement.',
            'Counterfactual windows start at the same historical accepted state; inherited costs remain in each run.',
            'Only ordinary native acceptance counts as a new contract; helper growth is reported separately.']
    except BaseException as error:
        report.update(phase='failed',error={'type':type(error).__name__,'message':str(error)})
        raise
    finally:
        cleanup_errors=[]
        if active is not None and (active/'service-launch.json').exists():
            try:
                if service.status(active)['running']:
                    service.stop(active)
            except BaseException as error:
                cleanup_errors.append({'stage':'service_stop','type':type(error).__name__,'message':str(error)})
            try:
                native_exit_gate(active)
            except BaseException as error:
                cleanup_errors.append({'stage':'native_exit_gate','type':type(error).__name__,'message':str(error)})
        report['cleanup_errors']=cleanup_errors
        try:
            for signum,handler in old_signals.items():
                signal.signal(signum,handler)
        finally:
            durable(output/'report.json',report,immutable=True)
        if cleanup_errors and 'error' not in report:
            raise RuntimeError('diagnostic cleanup failed: '+str(cleanup_errors))
    return report


def stop_pair(arms, expected_sha256):
    """ExecStopPost fallback: stop only identity-verified prepared child services."""
    arms=Path(arms).resolve()
    if watch.sha(arms/'preparation.json')!=expected_sha256:
        raise ValueError('diagnostic preparation identity changed')
    preparation=read_artifact(arms/'preparation.json')
    errors=[]
    for row in preparation['arms']:
        root=Path(row['run_dir']).resolve()
        if root.parent!=arms or row['strategy'] not in {'baseline','helper-closure-v1'}:
            raise ValueError('diagnostic cleanup target is outside prepared pair')
        if (root/'service-launch.json').exists():
            try:
                observed=service.status(root)
                if not observed['identity_verified'] and observed['status'] not in {'previous_boot','not_loaded'}:
                    raise RuntimeError('diagnostic service cleanup identity unverified')
                if observed['running']:
                    service.stop(root)
                native_exit_gate(root)
            except Exception as error:
                errors.append({'run_dir':str(root),'error':str(error)})
    if errors:
        raise RuntimeError('diagnostic cleanup failed: '+str(errors))
    return {'stopped_prepared_services':True,'preparation_sha256':expected_sha256}


def accepted_ids(root, theory, *, initial=False):
    commits = sorted((Path(root)/'theories'/theory/'acceptance/commits').glob('*.json'))
    if not commits:
        raise ValueError('diagnostic arm lacks inherited acceptance state')
    metadata, proofs = watch.projection(commits[0] if initial else commits[-1])
    if initial and metadata['event'] != 'initial':
        raise ValueError('diagnostic arm must start from inherited initial commit')
    return set(proofs)


def terminal_reason(snapshot, theory, *, budget=3600):
    row = snapshot.get('theories',{}).get(theory,{})
    if (row.get('first_crossing_at') is not None
            and row.get('last_new_contract_at') is not None
            and row['first_crossing_at'] < row['last_new_contract_at']
            and not row.get('deadline_decision_deferred',False)):
        return 'trial_effective_budget_exhausted'
    if row.get('new_contract_count',0):
        return 'first_new_contract'
    if row.get('effective_seconds',0)>=budget and not row.get('deadline_decision_deferred',False):
        return 'trial_effective_budget_exhausted'
    return None


def window_contracts(state, theory, final_new_ids, *, verified=True):
    """Exclude acceptance after the first terminal edge, including shutdown tail."""
    evidence=clock.clock_state(state)
    snapshot_row=evidence.get('snapshot',{}).get('theories',{}).get(theory)
    verified=verified and snapshot_row is not None and not snapshot_row.get('deadline_decision_deferred',False)
    if not verified:
        return {'window_result_verified':False,'in_window_contract_success':False,
            'in_window_new_contract_ids':[],'late_new_contract_ids':[],
            'unclassified_new_contract_ids':sorted(final_new_ids),'trial_cutoff_unix':None}
    rows=[r for r in evidence['progress'] if r['theory']==theory]
    first=min((r['at'] for r in rows),default=None)
    deadline=evidence.get('snapshot',{}).get('theories',{}).get(theory,{}).get('first_crossing_at')
    cutoff=min((x for x in (first,deadline) if x is not None),default=None)
    valid={uid for row in rows if cutoff is not None and row['at']<=cutoff
           for uid in row['proof_ids']} & set(final_new_ids)
    return {'window_result_verified':True,'trial_cutoff_unix':cutoff,'in_window_new_contract_ids':sorted(valid),
            'late_new_contract_ids':sorted(set(final_new_ids)-valid),
            'in_window_contract_success':bool(valid)}


def observe(run_dir, output, theory, *, wall_limit=10800, budget_effective_seconds=3600):
    validate_limits(budget_effective_seconds, wall_limit)
    root, output = Path(run_dir).resolve(), Path(output).resolve()
    if not (root/'checkpoint.json').is_file():
        raise ValueError('diagnostic arm requires exact historical checkpoint receipt')
    if (root/'trial-stop.json').exists():
        raise ValueError('diagnostic arm already stopped; automatic resume forbidden')
    output.mkdir(parents=True,exist_ok=False)
    launch_sha = watch.sha(root/'service-launch.json')
    state = {'cursors':{},'seen':{},'last_progress_at':0,
             'progress_scope':'new_contract','clock_mode':clock.MODE}
    inherited = accepted_ids(root,theory,initial=True)
    report = {'theory':theory,'run_dir':str(root),'budget_effective_seconds':budget_effective_seconds,
              'wall_safety_limit_seconds':wall_limit,'terminal':False,
              'window_policy':f'first new contract OR {budget_effective_seconds:g} cumulative effective seconds',
              'source_launch_sha256':launch_sha,'historical_costs_reset':False}
    report['inherited_accepted_ids'] = sorted(inherited)
    start = time.monotonic()
    reason = None
    try:
        while True:
            if watch.sha(root/'service-launch.json') != launch_sha:
                raise ValueError('bound service launch receipt changed')
            observed = service.status(root)
            if not observed['identity_verified'] and observed['status'] not in {'previous_boot','not_loaded'}:
                raise ValueError('diagnostic service identity unverified')
            clock.ingest([root],state)
            watch.scan([root],state)
            snapshot = clock.evaluate(state,{str(root):observed},time.time(),budget_effective_seconds)
            report.update(effective_clock=snapshot,service=observed,
                          observed_wall_seconds=time.monotonic()-start)
            reason = terminal_reason(snapshot,theory,budget=budget_effective_seconds)
            if not observed['running']:
                reason = reason or 'service_ended'
            if time.monotonic()-start>=wall_limit:
                reason = reason or 'trial_wall_safety_limit'
            durable(output/'progress.json',report)
            durable(output/'clock-state.json',state)
            if reason:
                break
            time.sleep(5)
    except BaseException as error:
        reason = 'observer_error'
        report['error']={'type':type(error).__name__,'message':str(error)}
        raise
    finally:
        # Stop even if a diagnostic write fails; exact service ownership is
        # revalidated by stop(). No broad process termination or source edits.
        cleanup_error = None
        try:
            cleanup_status=service.status(root)
            if not cleanup_status['identity_verified'] and cleanup_status['status'] not in {'previous_boot','not_loaded'}:
                raise RuntimeError('diagnostic service cleanup identity unverified')
            if cleanup_status['running']:
                service.stop(root)
        except BaseException as error:
            cleanup_error = {'type':type(error).__name__,'message':str(error)}
        try:
            final_service=service.status(root)
        except BaseException as error:
            report['final_service_error']={'type':type(error).__name__,'message':str(error)}
            final_service={'running':True,'identity_verified':False,'status':'unknown'}
            cleanup_error=cleanup_error or report['final_service_error']
        report.update(terminal=True,reason=reason,cleanup_error=cleanup_error,final_service=final_service)
        try:
            final_ids = accepted_ids(root,theory)
        except BaseException as error:
            report['acceptance_reconciliation_error']={'type':type(error).__name__,'message':str(error)}
            final_ids=inherited
        report['new_contract_ids'] = sorted(x for x in final_ids-inherited if x.startswith('contract:'))
        report['new_helper_ids'] = sorted(x for x in final_ids-inherited if x.startswith('helper:'))
        try:
            clock.ingest([root],state)
            watch.scan([root],state)
            report['post_stop_effective_clock'] = clock.evaluate(
                state,{str(root):report['final_service']},time.time(),budget_effective_seconds)
            durable(output/'clock-state.json',state)
        except BaseException as error:
            report['post_stop_reconciliation_error'] = str(error)
        report.update(window_contracts(state,theory,report['new_contract_ids'],verified=(
            'post_stop_reconciliation_error' not in report
            and 'acceptance_reconciliation_error' not in report
            and cleanup_error is None
            and reason not in {'observer_error','trial_wall_safety_limit'})))
        report['sampling_notice']='5-second polls and service cleanup can run beyond the terminal edge; late contracts are excluded from window success'
        try:
            durable(root/'trial-stop.json',{'reason':reason,'automatic_resume_allowed':False,
                'policy':report['window_policy'],'effective_clock':report.get('effective_clock')},immutable=True)
        finally:
            durable(output/'report.json',report,immutable=True)
        if cleanup_error and 'error' not in report:
            raise RuntimeError('diagnostic service did not stop: '+str(cleanup_error))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path)
    parser.add_argument('--prepare-checkpoint',type=Path)
    parser.add_argument('--run-pair',type=Path)
    parser.add_argument('--stop-pair',type=Path)
    parser.add_argument('--prepare-and-run',type=Path)
    parser.add_argument('--stop-workflow',type=Path)
    parser.add_argument('--checkpoint',type=Path)
    parser.add_argument('--prior-trial','--prior-invalid-trial',dest='prior_trial',type=Path)
    parser.add_argument('--expected-preparation-sha256')
    parser.add_argument('--application-manifest',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--theory',default='Scheduler_Dispatch')
    parser.add_argument('--budget-effective-seconds',type=float,default=3600)
    parser.add_argument('--wall-limit',type=float,default=10800)
    args=parser.parse_args()
    if sum(x is not None for x in (args.run_dir,args.prepare_checkpoint,args.run_pair,args.stop_pair,args.prepare_and_run,args.stop_workflow))!=1:
        parser.error('provide one of --run-dir, --prepare-checkpoint, --run-pair or --stop-pair')
    if args.stop_pair and not args.expected_preparation_sha256:
        parser.error('--stop-pair requires --expected-preparation-sha256')
    if not (args.stop_pair or args.stop_workflow or args.prepare_and_run) and args.output is None:
        parser.error('--output required')
    if args.run_pair and args.application_manifest is None:
        parser.error('--run-pair requires --application-manifest')
    if args.prepare_and_run and not (args.checkpoint and args.application_manifest and args.prior_trial):
        parser.error('--prepare-and-run requires checkpoint, application manifest and prior trial')
    value = (prepare_and_run(args.prepare_and_run,checkpoint=args.checkpoint,application_manifest=args.application_manifest,prior_trial=args.prior_trial,budget_effective_seconds=args.budget_effective_seconds,wall_limit=args.wall_limit) if args.prepare_and_run else
             stop_workflow(args.stop_workflow) if args.stop_workflow else
             stop_pair(args.stop_pair,args.expected_preparation_sha256) if args.stop_pair else
             run_pair(args.run_pair,args.output,args.application_manifest,budget_effective_seconds=args.budget_effective_seconds,wall_limit=args.wall_limit) if args.run_pair else
             prepare_pair(args.prepare_checkpoint,args.output) if args.prepare_checkpoint else
             observe(args.run_dir,args.output,args.theory,budget_effective_seconds=args.budget_effective_seconds,wall_limit=args.wall_limit))
    print(json.dumps(value,indent=2))
