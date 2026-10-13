"""Isolated target-contract A/B windows; production clocks remain untouched."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import re
import signal
import sys
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
    if len(config['cases']) != 2:
        raise ValueError('exactly two diagnostic cases required')
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


def observe(root, output, theory, target, *, budget=3600, wall_limit=10800):
    closure.validate_limits(budget, wall_limit)
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
              'window_policy': 'target GREEN or fixed cumulative effective budget; no progress renewal'}
    start = time.monotonic()
    reason = None
    try:
        while True:
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
                        and reason in {'target_contract_green', 'trial_effective_budget_exhausted'})
            report.update({k: v for k, v in edge.items() if k != 'reason'})
            report['window_result_verified'] = verified
            report['target_success'] = verified and edge['target_success'] and target in final_ids
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
        for strategy in ('baseline', config.get('variant_strategy', 'bounded-audited-prefix-v1')):
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
            row = {**case, 'strategy': strategy, 'run_dir': str(root), 'name': name,
                   'case_name': case['name'], 'initial_state_sha256': digest(initial['state']),
                   'preparation_seconds': time.monotonic()-started}
            rows.append(row)
            pair.append(row)
            durable(control/'prepare-progress.json', {'phase': 'arm_prepared', 'current_arm': name,
                    'completed_arms': [r['name'] for r in rows],
                    'current_arm_seconds': row['preparation_seconds'],
                    'elapsed_seconds': time.monotonic()-preparation_started, 'updated_at_unix': time.time()})
        if pair[0]['initial_state_sha256'] != pair[1]['initial_state_sha256']:
            raise ValueError('A/B inherited states differ')
        if watch.sha(case['checkpoint']) != case['checkpoint_sha256']:
            raise ValueError('checkpoint changed during preparation')
    result = {'arms': rows, 'same_inherited_states': True, 'model_calls': 0, 'native_executions': 0}
    durable(control/'preparation.json', result, immutable=True)
    return result


def command(root, theory, strategy, target):
    return [sys.executable, '-u', '-m', 'regen.execution.proof_session_runner', 'parallel-workspace',
            '--run-dir', str(root), '--theory', theory, '--planner-model', 'gpt-6-astra',
            '--call-timeout', '900', '--check-timeout', '900', '--candidate-timeout', '120',
            '--max-parallel-theories', '1', '--native-slots', '1', '--native-threads', '4',
            '--resident-limit', '1', '--native-cpus', '4', '--native-memory-mb', '16384',
            '--provider-build-timeout', '1800', '--file-planner-interface', 'document',
            '--generation-strategy', strategy, '--focus-contract-id', target, '--allow-external-call']


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


def run_workflow(control, config_path, manifest):
    from regen.infrastructure.audited_llm import model_config_path, model_settings
    control, config_path, manifest = map(lambda p: Path(p).resolve(), (control, config_path, manifest))
    application = Path(__file__).resolve().parents[2]
    verify(application, manifest)
    settings = model_settings()
    if (model_config_path() != application/'regen/config/proof-models.json' or
            settings['role_models']['File Planner'] != 'gpt-6-astra' or settings['reasoning_effort'] != 'medium'):
        raise ValueError('release-bound gpt-6-astra medium required')
    config = configuration(config_path)
    budget, wall = config.get('budget_effective_seconds', 3600), config.get('wall_limit_seconds', 10800)
    control.mkdir(parents=True, exist_ok=False)
    durable(control/'control-receipt.json', {'config': config, 'config_sha256': watch.sha(config_path),
            'application_manifest_sha256': watch.sha(manifest), 'production_resumed': False,
            'historical_costs_reset': False}, immutable=True)
    report = {'comparison_complete': False, 'phase': 'preparing', 'arms': [],
              'budget_effective_seconds': budget, 'wall_limit_seconds': wall,
              'replicates_per_case_strategy': 1, 'historical_costs_reset': False}
    with termination_signals():
        try:
            prepared = prepare(control, config)
            durable(control/'prepared-binding.json', {
                'preparation_sha256': watch.sha(control/'preparation.json'),
                'control_receipt_sha256': watch.sha(control/'control-receipt.json')}, immutable=True)
            for row in prepared['arms']:
                verify(application, manifest)
                root = Path(row['run_dir']).resolve()
                if root.parent != control/'arms':
                    raise ValueError('arm escapes workflow')
                initial = read_artifact(root/'theories'/row['theory']/'acceptance/commits/000001.json')
                if digest(initial['state']) != row['initial_state_sha256']:
                    raise ValueError('inherited state changed')
                durable(root/'experiment.json', {'command': command(root, row['theory'], row['strategy'], row['target_contract_id']),
                        'cwd': str(application), 'resource_requirements': {
                            'min_free_disk_bytes': 10*1024**3, 'min_available_memory_bytes': 18*1024**3},
                        'target_contract_id': row['target_contract_id'],
                        'diagnostic_window_effective_seconds': budget, 'wall_safety_limit_seconds': wall,
                        'application_manifest_sha256': watch.sha(manifest), 'historical_costs_reset': False,
                        'production_resumed': False}, immutable=True)
                report.update(phase='launching', active_arm=row['name'])
                durable(control/'progress.json', report)
                service.start(root)
                report['phase'] = 'observing'
                durable(control/'progress.json', report)
                result = observe(root, control/'comparison'/row['name'], row['theory'],
                                 row['target_contract_id'], budget=budget, wall_limit=wall)
                report['arms'].append({'case_name': row['case_name'], 'strategy': row['strategy'], 'report': result})
                if not result['window_result_verified']:
                    raise RuntimeError('arm did not reach a verified terminal edge')
                durable(control/'progress.json', report)
            report.update(comparison_complete=True, phase='complete', active_arm=None)
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
    parser.add_argument('operation', choices=('prepare-and-run', 'stop-workflow'))
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--application-manifest', type=Path)
    args = parser.parse_args()
    if args.operation == 'prepare-and-run' and (args.config is None or args.application_manifest is None):
        parser.error('prepare-and-run requires --config and --application-manifest')
    value = (stop_workflow(args.control) if args.operation == 'stop-workflow' else
             run_workflow(args.control, args.config, args.application_manifest))
    print(json.dumps(value, indent=2))


if __name__ == '__main__':
    main()
