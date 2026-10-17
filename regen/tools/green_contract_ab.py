"""Isolated target-contract A/B windows; production clocks remain untouched."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import json
from pathlib import Path
import re
import signal
import sys
import threading
import time

from regen.execution.proof_commits import durable, digest
from regen.infrastructure.incremental_log import read_artifact
from regen.tools import contract_closure_validation as closure
from regen.tools import parallel_workspace_service as service
from regen.tools import proof_idle_watch as watch
from regen.tools import theory_effective_clock as clock
from regen.tools.application_release import verify


def error_record(error):
    return {'type': type(error).__name__, 'message': str(error)}


@contextmanager
def termination_signals():
    def terminate(signum, frame):
        raise KeyboardInterrupt('diagnostic terminated by signal '+str(signum))
    old = {s: signal.signal(s, terminate) for s in (signal.SIGTERM, signal.SIGHUP)}
    try:
        yield
    finally:
        for s, handler in old.items():
            signal.signal(s, handler)


def configuration(path):
    config = read_artifact(path)
    closure.validate_limits(config.get('budget_effective_seconds', 3600),
                            config.get('wall_limit_seconds', 10800))
    if config.get('incremental_checkpoint_window',False):
        from regen.tools.checkpoint_trial_budget import validate_trial_seconds
        validate_trial_seconds(config.get('budget_effective_seconds',3600))
    if len(config['cases']) not in (1,2):
        raise ValueError('one or two diagnostic cases required')
    names = set()
    for case in config['cases']:
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', case['name']) or case['name'] in names:
            raise ValueError('invalid or duplicate case name')
        names.add(case['name'])
        if not re.fullmatch(r'[A-Za-z0-9_]+', case['theory']):
            raise ValueError('invalid theory name')
        if not case['target_contract_id'].startswith('contract:'):
            raise ValueError('target must be a contract UID')
        checkpoint = Path(case['checkpoint'])
        if not checkpoint.is_absolute() or watch.sha(checkpoint) != case['checkpoint_sha256']:
            raise ValueError('checkpoint identity mismatch')
        if case['theory'] not in read_artifact(checkpoint)['theories']:
            raise ValueError('checkpoint omits target theory')
    if config.get('variant_strategy', 'bounded-audited-prefix-v1') == 'baseline':
        raise ValueError('variant strategy must differ from baseline')
    for key in ('variant_only', 'incremental_checkpoint_window', 'wall_budget'):
        if key in config and type(config[key]) is not bool:
            raise ValueError(key+' must be boolean')
    if type(config.get('independent_case_failures',False)) is not bool:
        raise ValueError('independent_case_failures must be boolean')
    if (not isinstance(config.get('model','gpt-6-astra'),str)
            or not re.fullmatch(r'[A-Za-z0-9_.-]+',config.get('model','gpt-6-astra'))
            or config.get('reasoning_effort','medium') != 'medium'):
        raise ValueError('a named model with medium reasoning is required')
    if type(config.get('case_workers',1)) is not int or config.get('case_workers',1) not in (1,2):
        raise ValueError('case_workers must be 1 or 2')
    memory=config.get('native_memory_mb',16384)
    if type(memory) is not int or memory < 4096:
        raise ValueError('native_memory_mb must be an integer of at least 4096')
    return config


def evaluate_window(state, observations, now, budget):
    """Explicit fixed diagnostic budget; never mutate acceptance evidence."""
    return clock.evaluate(state, observations, now, budget, budget_policy='fixed_budget')


def target_window(state, theory, target, *, budget=3600):
    evidence = clock.clock_state(state)
    row = evidence.get('snapshot', {}).get('theories', {}).get(theory)
    if row is None or row.get('deadline_decision_deferred', False):
        return {'window_result_verified': False, 'reason': None, 'target_success': False}
    target_at = min((p['at'] for p in evidence['progress']
                     if p['theory'] == theory and target in p['proof_ids']), default=None)
    deadline = row.get('first_crossing_at')
    success = target_at is not None and (deadline is None or target_at <= deadline)
    reason = ('target_contract_green' if success else
              'trial_effective_budget_exhausted' if deadline is not None or row['effective_seconds'] >= budget else None)
    return {'window_result_verified': True, 'reason': reason, 'target_success': success,
            'target_accepted_at_unix': target_at, 'budget_crossing_at_unix': deadline,
            'late_target_acceptance': target_at is not None and not success}


def checked_status(root):
    status = service.status(root)
    if not status['identity_verified'] and status['status'] not in {'previous_boot', 'not_loaded'}:
        raise RuntimeError('diagnostic service identity unverified')
    return status


def cleanup_arm(root):
    errors = []
    if not (root/'service-launch.json').exists():
        return errors
    try:
        if checked_status(root)['running']:
            service.stop(root)
    except BaseException as error:
        errors.append({'stage': 'service_stop', **error_record(error)})
    try:
        closure.native_exit_gate(root)
    except BaseException as error:
        errors.append({'stage': 'native_exit_gate', **error_record(error)})
    return errors


def observe(root, output, theory, target, *, budget=3600, wall_limit=10800, wall_budget=False, stop_event=None):
    closure.validate_limits(min(budget,wall_limit) if wall_budget else budget, wall_limit)
    root, output = Path(root).resolve(), Path(output).resolve()
    if not (root/'checkpoint.json').is_file() or (root/'trial-stop.json').exists():
        raise ValueError('observer requires a fresh checkpoint arm')
    inherited = closure.accepted_ids(root, theory, initial=True)
    if target in inherited:
        raise ValueError('target already GREEN at checkpoint')
    launch_sha = watch.sha(root/'service-launch.json')
    output.mkdir(parents=True, exist_ok=False)
    state = {'cursors': {}, 'seen': {}, 'last_progress_at': 0,
             'progress_scope': 'new_contract', 'clock_mode': clock.MODE}
    report = {'theory': theory, 'target_contract_id': target, 'run_dir': str(root),
              'budget_effective_seconds': budget, 'wall_limit_seconds': wall_limit,
              'historical_costs_reset': False, 'inherited_accepted_ids': sorted(inherited),
              'source_launch_sha256': launch_sha, 'terminal': False,
              'wall_budget':wall_budget,
              'window_policy': 'target GREEN or fixed cumulative effective budget; no progress renewal'}
    start = time.monotonic()
    wall_deadline_unix=time.time()+wall_limit
    report['wall_deadline_unix']=wall_deadline_unix
    reason = None
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                reason='peer_or_operator_stop'
                report['outcome_class']='infrastructure_error'
                break
            if watch.sha(root/'service-launch.json') != launch_sha:
                raise ValueError('service launch receipt changed')
            status = checked_status(root)
            clock.ingest([root], state)
            watch.scan([root], state)
            snapshot = evaluate_window(state, {str(root): status}, time.time(), budget)
            edge = target_window(state, theory, target, budget=budget)
            report.update(effective_clock=snapshot, service=status,
                          observed_wall_seconds=time.monotonic()-start)
            reason = edge['reason']
            if (wall_budget and edge.get('target_accepted_at_unix') is not None
                    and edge['target_accepted_at_unix']>wall_deadline_unix):
                reason='trial_wall_safety_limit'
            if not status['running']:
                reason = reason or 'service_ended'
            if time.monotonic()-start >= wall_limit:
                reason = reason or 'trial_wall_safety_limit'
            durable(output/'progress.json', {**report, **edge})
            durable(output/'clock-state.json', state)
            if reason:
                break
            time.sleep(5)
    except BaseException as error:
        reason = 'measurement_invalid' if isinstance(error, clock.ClockEvidenceError) else 'observer_error'
        report['outcome_class'] = 'measurement_invalid' if reason == 'measurement_invalid' else 'infrastructure_error'
        report['error'] = error_record(error)
        raise
    finally:
        report.update(terminal=True, reason=reason, cleanup_errors=cleanup_arm(root))
        try:
            report['final_service'] = checked_status(root)
            final_ids = closure.accepted_ids(root, theory)
            report['new_contract_ids'] = sorted(p for p in final_ids-inherited if p.startswith('contract:'))
            report['new_helper_ids'] = sorted(p for p in final_ids-inherited if p.startswith('helper:'))
            clock.ingest([root], state)
            watch.scan([root], state)
            report['post_stop_effective_clock'] = evaluate_window(
                state, {str(root): report['final_service']}, time.time(), budget)
            edge = target_window(state, theory, target, budget=budget)
            # A shutdown tail cannot rescue an abnormal endpoint.
            verified = (edge['window_result_verified'] and not report['cleanup_errors']
                        and not report['final_service']['running']
                        and (reason in {'target_contract_green', 'trial_effective_budget_exhausted'}
                             or wall_budget and reason == 'trial_wall_safety_limit'))
            report.update({k: v for k, v in edge.items() if k != 'reason'})
            report['window_result_verified'] = verified
            report['target_success'] = (verified and edge['target_success'] and target in final_ids
                and (not wall_budget or reason == 'target_contract_green'))
            if verified:
                report['outcome_class'] = 'target_success' if report['target_success'] else 'budget_censored'
            durable(output/'clock-state.json', state)
        except BaseException as error:
            report.update(reconciliation_error=error_record(error), window_result_verified=False,
                          target_success=False)
        try:
            durable(root/'trial-stop.json', {'reason': reason, 'automatic_resume_allowed': False,
                    'target_contract_id': target, 'budget_effective_seconds': budget}, immutable=True)
        finally:
            durable(output/'report.json', report, immutable=True)
        if report['cleanup_errors'] and 'error' not in report:
            raise RuntimeError('diagnostic cleanup failed: '+str(report['cleanup_errors']))
    return report


def prepare(control, config):
    from regen.execution.repository_execution import fork_workspace_continuation
    rows = []
    preparation_started = time.monotonic()
    for case in config['cases']:
        checkpoint = read_artifact(case['checkpoint'])
        source = Path(checkpoint['source_run']).resolve()
        pair = []
        variant = config.get('variant_strategy', 'bounded-audited-prefix-v1')
        strategies = (variant,) if config.get('variant_only',False) else ('baseline',variant)
        for strategy in strategies:
            name = case['name']+'--'+('baseline' if strategy == 'baseline' else 'variant')
            root = control/'arms'/name
            if root.is_relative_to(source) or source.is_relative_to(root):
                raise ValueError('diagnostic destination overlaps source')
            started = time.monotonic()
            durable(control/'prepare-progress.json', {'phase': 'forking', 'current_arm': name,
                    'completed_arms': [r['name'] for r in rows],
                    'elapsed_seconds': started-preparation_started, 'updated_at_unix': time.time()})
            if watch.sha(case['checkpoint']) != case['checkpoint_sha256']:
                raise ValueError('checkpoint changed before fork')
            fork_workspace_continuation(source, root, checkpoint=checkpoint)
            initial = read_artifact(root/'theories'/case['theory']/'acceptance/commits/000001.json')
            nodes = {n['id'] for n in initial['state']['graph']['nodes']}
            if case['target_contract_id'] not in nodes or case['target_contract_id'] in initial['state']['proofs']:
                raise ValueError('checkpoint target absent or already GREEN')
            if case.get('native_provider_heap_manifest'):
                warm_provider_heaps(root,case,source)
            row = {**case, 'strategy': strategy, 'run_dir': str(root), 'name': name,
                   'case_name': case['name'], 'initial_state_sha256': digest(initial['state']),
                   'preparation_seconds': time.monotonic()-started}
            rows.append(row)
            pair.append(row)
            durable(control/'prepare-progress.json', {'phase': 'arm_prepared', 'current_arm': name,
                    'completed_arms': [r['name'] for r in rows],
                    'current_arm_seconds': row['preparation_seconds'],
                    'elapsed_seconds': time.monotonic()-preparation_started, 'updated_at_unix': time.time()})
        if len(pair)==2 and pair[0]['initial_state_sha256'] != pair[1]['initial_state_sha256']:
            raise ValueError('A/B inherited states differ')
        if watch.sha(case['checkpoint']) != case['checkpoint_sha256']:
            raise ValueError('checkpoint changed during preparation')
    result = {'arms': rows, 'same_inherited_states': not config.get('variant_only',False),
              'variant_only':config.get('variant_only',False), 'model_calls': 0, 'native_executions': 0}
    durable(control/'preparation.json', result, immutable=True)
    return result


def warm_provider_heaps(root, case, source):
    """Reuse only byte-matched sealed-provider heaps; retain the build gate."""
    manifest=Path(case['native_provider_heap_manifest']).resolve()
    if watch.sha(manifest)!=case['native_provider_heap_manifest_sha256']:
        raise ValueError('provider heap manifest changed')
    record=read_artifact(manifest)
    directory=root/'theories'/case['theory']
    source_heaps=Path(record['source_heaps']).resolve()
    if source_heaps != (source/'theories'/case['theory']/'heaps').resolve():
        raise ValueError('provider heap source differs from the checkpoint source')
    if (record.get('provider_session_receipt_copied') is not False
            or record.get('standard_native_build_gate_preserved') is not True
            or record.get('scope') not in {
                'sealed provider session only; target theory was never sealed',
                'sealed provider session only; generated imports verified; target proofs excluded'}):
        raise ValueError('heap reuse is not restricted to sealed providers')
    # Continuation publishes verified sealed providers at the run root. The
    # normal runtime later freezes those sources into the per-theory directory;
    # heap warmup must perform the same byte-preserving materialization first.
    providers = root/'generated'
    expected = record['exact_generated_provider_sha256']
    if any(Path(name).name != name or not name.endswith('.thy') for name in expected):
        raise ValueError('invalid provider source name')
    if ({name:watch.sha(providers/name) for name in expected} != expected
            or read_artifact(directory/'imports.json') != record['imports']):
        raise ValueError('verified continuation providers differ from the validated heap source')
    import shutil
    generated = directory/'generated'
    generated.mkdir(exist_ok=True)
    for name, sha in expected.items():
        target = generated/name
        if target.exists() and watch.sha(target) != sha:
            raise ValueError('frozen provider source drift')
        if not target.exists():
            shutil.copy2(providers/name,target)
    files={p.name:watch.sha(p) for p in (directory/'generated').glob('*.thy')}
    if (not files or files!=record['exact_generated_provider_sha256']
            or read_artifact(directory/'imports.json')!=record['imports']):
        raise ValueError('provider theory bytes or imports differ from the validated heap source')
    from regen.infrastructure.resource_budget import clone_heaps
    copied=clone_heaps(source_heaps,directory/'heaps')
    durable(directory/'native-heap-reuse.json',{
        **{k:v for k,v in record.items() if k!='copy'},'copy':copied,
        'reference_manifest':str(manifest),'reference_manifest_sha256':watch.sha(manifest)},immutable=True)


def command(root, theory, strategy, target, *, memory_mb=16384, model='gpt-6-astra'):
    return [sys.executable, '-u', '-m', 'regen.execution.proof_session_runner', 'parallel-workspace',
            '--run-dir', str(root), '--theory', theory, '--planner-model', model,
            '--call-timeout', '900', '--check-timeout', '900', '--candidate-timeout', '120',
            '--max-parallel-theories', '1', '--native-slots', '1', '--native-threads', '4',
            '--resident-limit', '1', '--native-cpus', '4', '--native-memory-mb', str(memory_mb),
            '--provider-build-timeout', '1800', '--file-planner-interface', 'document',
            '--generation-strategy', strategy, '--focus-contract-id', target, '--allow-external-call']


def diagnostic_window(row, config):
    """Initialize the existing authorized checkpoint clock with normal policy."""
    from regen.execution.check_executor import NativeResources
    from regen.planning.proof_workspace_agent import WorkspaceController
    from regen.planning.parallel_file_planner import generation_strategy, _execution_policy
    from regen.tools.checkpoint_trial_budget import initialize_checkpoint_trial_budget
    root=Path(row['run_dir'])
    overview=WorkspaceController(root,theories=[row['theory']],model=config.get('model','gpt-6-astra'),
        call_timeout=900,check_timeout=900,candidate_timeout=120)
    overview.document_mode=True
    resources=NativeResources(1,4,1,cpus=4.0,memory_mb=config.get('native_memory_mb',16384))
    try:
        binding={'mode':'parallel-file-planner-v1',
            'generation_strategy':generation_strategy(row['strategy']),
            'focus_contract_id':row['target_contract_id'],'selected_theories':overview.selected,
            'max_parallel_theories':1,'theory_tool_execution':'serial','file_planner_interface':'document',
            **resources.policy,**overview.options,'integration_required':True,
            'execution_policy':_execution_policy(None,overview.selected)}
        receipt=initialize_checkpoint_trial_budget(root,overview,binding,
            target_contract_id=row['target_contract_id'],authorization=config['authorization'],
            seconds=config.get('budget_effective_seconds',3600),checkpoint_path=Path(row['checkpoint']))
        durable(root/'diagnostic-window.json',receipt,immutable=True)
        return receipt
    finally:
        resources.close()


def stop_workflow(control):
    control = Path(control).resolve()
    preparation = control/'preparation.json'
    binding = control/'prepared-binding.json'
    if not binding.exists():
        if any((control/'arms').glob('*/service-launch.json')):
            raise RuntimeError('unbound workflow has service receipts')
        return {'preparation_only': True}
    identity = read_artifact(binding)
    if (watch.sha(preparation) != identity['preparation_sha256'] or
            watch.sha(control/'control-receipt.json') != identity['control_receipt_sha256']):
        raise RuntimeError('workflow cleanup binding changed')
    errors = []
    for row in read_artifact(preparation)['arms']:
        root = Path(row['run_dir']).resolve()
        if root.parent != control/'arms' or root.name != row['name']:
            raise RuntimeError('cleanup target escapes prepared arms')
        errors.extend({'run_dir': str(root), **error} for error in cleanup_arm(root))
    if errors:
        raise RuntimeError('workflow cleanup failed: '+str(errors))
    return {'all_prepared_services_stopped': True}


def parallel_admission(control, config):
    """Two independent model/native services need aggregate host headroom."""
    workers=config.get('case_workers',1)
    memory_mb=config.get('native_memory_mb',16384)
    minimum=(memory_mb+2048)*workers*1024**2
    row=service._resource_admission(control,{
        'min_free_disk_bytes':config.get('min_free_disk_bytes',10*1024**3),
        'min_available_memory_bytes':minimum})
    import os
    available=len(os.sched_getaffinity(0))
    if available < workers*4:
        raise RuntimeError('insufficient aggregate CPU capacity for concurrent cases')
    return {**row,'case_workers':workers,'available_cpus':available,
            'native_memory_mb_per_case':memory_mb,'native_cpus_per_case':4}


def run_workflow(control, config_path, manifest, *, reuse_prepared=False):
    from regen.infrastructure.audited_llm import model_config_path, model_settings
    control, config_path, manifest = map(lambda p: Path(p).resolve(), (control, config_path, manifest))
    application = Path(__file__).resolve().parents[2]
    verify(application, manifest)
    settings = model_settings()
    config = configuration(config_path)
    model = config.get('model','gpt-6-astra')
    if (model_config_path() != application/'regen/config/proof-models.json' or
            settings['role_models']['File Planner'] != model or settings['reasoning_effort'] != 'medium'):
        raise ValueError('release-bound ' + model + ' medium required')
    budget, wall = config.get('budget_effective_seconds', 3600), config.get('wall_limit_seconds', 10800)
    control.mkdir(parents=True, exist_ok=reuse_prepared)
    if reuse_prepared:
        if (control/'control-receipt.json').exists() or any((control/'arms').glob('*/service-launch.json')):
            raise ValueError('prepared workflow is single-use; it already has launch/control receipts')
        prepared=read_artifact(control/'preparation.json')
        variant=config.get('variant_strategy','bounded-audited-prefix-v1')
        expected={(case['name']+'--'+label,strategy) for case in config['cases']
            for label,strategy in ([('variant',variant)] if config.get('variant_only',False)
                                  else [('baseline','baseline'),('variant',variant)])}
        if ({(row['name'],row['strategy']) for row in prepared['arms']}!=expected
                or len(prepared['arms'])!=len(expected)
                or prepared.get('model_calls')!=0 or prepared.get('native_executions')!=0):
            raise ValueError('prepared arms differ from the authorized unused experiment')
        cases={case['name']:case for case in config['cases']}
        for row in prepared['arms']:
            case=cases[row['case_name']]
            if any(row[key]!=case[key] for key in ('theory','target_contract_id','checkpoint','checkpoint_sha256')):
                raise ValueError('prepared case binding changed')
    durable(control/'control-receipt.json', {'config': config, 'config_sha256': watch.sha(config_path),
            'application_manifest_sha256': watch.sha(manifest), 'production_resumed': False,
            'historical_costs_reset': False}, immutable=True)
    report = {'comparison_complete': False, 'phase': 'preparing', 'arms': [],
              'budget_effective_seconds': budget, 'wall_limit_seconds': wall,
              'case_workers':config.get('case_workers',1),
              'native_memory_mb_per_case':config.get('native_memory_mb',16384),
              'replicates_per_case_strategy': 1, 'historical_costs_reset': False}
    with termination_signals():
        try:
            if not reuse_prepared:
                prepared = prepare(control, config)
            durable(control/'prepared-binding.json', {
                'preparation_sha256': watch.sha(control/'preparation.json'),
                'control_receipt_sha256': watch.sha(control/'control-receipt.json')}, immutable=True)
            workers=config.get('case_workers',1)
            if workers>1:
                durable(control/'parallel-resource-admission.json',parallel_admission(control,config),immutable=True)
            stop_event=threading.Event()
            def run_arm(row):
                verify(application, manifest)
                root = Path(row['run_dir']).resolve()
                if root.parent != control/'arms':
                    raise ValueError('arm escapes workflow')
                initial = read_artifact(root/'theories'/row['theory']/'acceptance/commits/000001.json')
                if digest(initial['state']) != row['initial_state_sha256']:
                    raise ValueError('inherited state changed')
                argv=command(root,row['theory'],row['strategy'],row['target_contract_id'],
                             memory_mb=config.get('native_memory_mb',16384), model=model)
                if config.get('incremental_checkpoint_window',False):
                    diagnostic_window(row,config)
                    argv += ['--effective-seconds',str(budget),'--resume-authorization',config['authorization']]
                durable(root/'experiment.json', {'command': argv,
                        'cwd': str(application), 'resource_requirements': {
                            'min_free_disk_bytes': config.get('min_free_disk_bytes',10*1024**3),
                            'min_available_memory_bytes': (config.get('native_memory_mb',16384)+2048)*1024**2},
                        'target_contract_id': row['target_contract_id'],
                        'diagnostic_window_effective_seconds': budget, 'wall_safety_limit_seconds': wall,
                        'application_manifest_sha256': watch.sha(manifest), 'historical_costs_reset': False,
                        'production_resumed': False}, immutable=True)
                arm_progress=control/'arm-progress'/(row['name']+'.json')
                durable(arm_progress,{'phase':'launching','name':row['name'],'run_dir':str(root)})
                launch_deadline=time.time()+wall
                if stop_event.is_set():
                    raise RuntimeError('peer or operator stopped before launch')
                service.start(root)
                durable(arm_progress,{'phase':'observing','name':row['name'],'run_dir':str(root),
                                     'wall_deadline_unix':launch_deadline})
                limits={'budget':budget,'wall_limit':wall}
                if config.get('wall_budget',False):
                    limits['wall_budget']=True
                    if row.get('wall_deadline_unix') is not None:
                        limits['wall_limit']=max(.001,min(wall,row['wall_deadline_unix']-time.time()))
                if workers>1:
                    if config.get('wall_budget',False):
                        limits['wall_limit']=max(.001,min(limits['wall_limit'],launch_deadline-time.time()))
                    limits['stop_event']=stop_event
                result = observe(root, control/'comparison'/row['name'], row['theory'],
                                 row['target_contract_id'], **limits)
                if not result['window_result_verified']:
                    raise RuntimeError('arm did not reach a verified terminal edge')
                durable(arm_progress,{'phase':'complete','name':row['name'],'report':result})
                return {'case_name':row['case_name'],'strategy':row['strategy'],'report':result}
            report.update(phase='observing',active_arms=[row['name'] for row in prepared['arms']])
            durable(control/'progress.json',report)
            if workers==1:
                for row in prepared['arms']:
                    report['arms'].append(run_arm(row))
                    durable(control/'progress.json',report)
            else:
                pool=ThreadPoolExecutor(max_workers=workers,thread_name_prefix='diagnostic-case')
                futures={pool.submit(run_arm,row):row for row in prepared['arms']}
                try:
                    for future in as_completed(futures):
                        try:
                            outcome=future.result()
                        except Exception as error:
                            if not config.get('independent_case_failures',False):
                                raise
                            row=futures[future]
                            outcome={'case_name':row['case_name'],'strategy':row['strategy'],
                                     'report':{'window_result_verified':False,'target_success':False,
                                               'outcome_class':'infrastructure_error','error':error_record(error)}}
                        report['arms'].append(outcome)
                        report['active_arms'].remove(futures[future]['name'])
                        durable(control/'progress.json',report)
                finally:
                    stop_event.set()
                    for future in futures:
                        future.cancel()
                    pool.shutdown(wait=True,cancel_futures=True)
            complete=all(row['report']['window_result_verified'] for row in report['arms'])
            report.update(comparison_complete=complete, phase='complete' if complete else 'partial', active_arm=None)
        except BaseException as error:
            report.update(phase='failed', error=error_record(error))
            raise
        finally:
            try:
                stop_workflow(control)
            except BaseException as error:
                report.update(cleanup_error=error_record(error), comparison_complete=False)
                if 'error' not in report:
                    raise
            finally:
                durable(control/'report.json', report, immutable=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare-and-run', 'run-prepared', 'stop-workflow'))
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--application-manifest', type=Path)
    args = parser.parse_args()
    if args.operation != 'stop-workflow' and (args.config is None or args.application_manifest is None):
        parser.error('running requires --config and --application-manifest')
    value = (stop_workflow(args.control) if args.operation == 'stop-workflow' else
             run_workflow(args.control, args.config, args.application_manifest,
                          reuse_prepared=args.operation=='run-prepared'))
    print(json.dumps(value, indent=2))


if __name__ == '__main__':
    main()
