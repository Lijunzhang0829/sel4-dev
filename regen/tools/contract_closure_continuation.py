"""Continue only an unstarted second diagnostic arm, preserving the first result.

The repaired supervisor and the proof worker have separate release bindings:
proof generation continues to use the original, verified application directory.
"""
import argparse
from contextlib import contextmanager
from copy import deepcopy
import fcntl
from pathlib import Path
import signal
import time

from regen.execution.proof_commits import digest, durable
from regen.infrastructure.incremental_log import read_artifact
from regen.tools.application_release import verify
from regen.tools import contract_closure_validation as trial
from regen.tools import proof_idle_watch as watch


@contextmanager
def control_lock(control):
    with (control/'continuation-control.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def validate(control, supervisor_manifest):
    """Read-only admission; neither completed trials nor started arms are replayable."""
    control = Path(control).resolve()
    supervisor = Path(__file__).resolve().parents[2]
    verify(supervisor, supervisor_manifest)
    receipt_path = control/'control-receipt.json'
    binding_path = control/'prepared-binding.json'
    receipt, binding = read_artifact(receipt_path), read_artifact(binding_path)
    arms = control/'arms'
    preparation_path = arms/'preparation.json'
    if (Path(receipt['arms']) != arms
            or binding['control_receipt_sha256'] != watch.sha(receipt_path)
            or binding['preparation_sha256'] != watch.sha(preparation_path)):
        raise ValueError('workflow preparation binding changed')
    original_manifest = Path(receipt['application_manifest'])
    if watch.sha(original_manifest) != receipt['application_manifest_sha256']:
        raise ValueError('original application manifest changed')
    if watch.sha(receipt['checkpoint']) != receipt['checkpoint_sha256']:
        raise ValueError('source checkpoint changed')
    preparation = read_artifact(preparation_path)
    rows = preparation['arms']
    if (not preparation['same_inherited_state']
            or preparation.get('historical_costs_reset') is not False
            or preparation['checkpoint_sha256'] != receipt['checkpoint_sha256']
            or [row['strategy'] for row in rows] != ['baseline', 'helper-closure-v1']
            or rows[0]['initial_state_sha256'] != rows[1]['initial_state_sha256']):
        raise ValueError('invalid matched pair')
    for row in rows:
        arm = arms/row['strategy']
        if Path(row['run_dir']) != arm or arm.is_symlink():
            raise ValueError('arm identity differs from prepared pair')
        initial = read_artifact(arm/'theories/Scheduler_Dispatch/acceptance/commits/000001.json')
        if digest(initial['state']) != row['initial_state_sha256']:
            raise ValueError('inherited state changed')
    baseline, pending = arms/'baseline', arms/'helper-closure-v1'
    report_path = control/'comparison/baseline/report.json'
    report = read_artifact(report_path)
    stop = read_artifact(baseline/'trial-stop.json')
    if (report.get('terminal') is not True or not report.get('window_result_verified')
            or report.get('reason') not in {'first_new_contract', 'trial_effective_budget_exhausted'}
            or report.get('cleanup_error') or report['final_service']['running']
            or report.get('run_dir') != str(baseline)
            or report.get('budget_effective_seconds') != 3600
            or report.get('wall_safety_limit_seconds') != 10800
            or stop.get('reason') != report['reason'] or stop.get('automatic_resume_allowed') is not False
            or report['source_launch_sha256'] != watch.sha(baseline/'service-launch.json')):
        raise ValueError('baseline is not a verified completed trial')
    observed = trial.service.status(baseline)
    if observed['running'] or (not observed['identity_verified']
                             and observed['status'] not in {'previous_boot', 'not_loaded'}):
        raise ValueError('baseline service has not verifiably exited')
    forbidden = ('experiment.json', 'service-launch.json', 'service-invocation.json',
                 'service-runner.log', 'trial-stop.json', 'parallel-sessions')
    if any((pending/name).exists() for name in forbidden):
        raise ValueError('optimized arm already started or bound; replay forbidden')
    if ((control/'comparison/helper-closure-v1').exists()
            or (control/'comparison/report.json').exists()):
        raise ValueError('comparison already has second-arm or final output')
    commits = list((pending/'theories/Scheduler_Dispatch/acceptance/commits').glob('*.json'))
    if len(commits) != 1:
        raise ValueError('optimized arm has post-checkpoint acceptance')
    if digest(read_artifact(pending/'checkpoint.json')) != digest(read_artifact(receipt['checkpoint'])):
        raise ValueError('optimized arm checkpoint differs')
    experiment = read_artifact(baseline/'experiment.json')
    launch = read_artifact(baseline/'service-launch.json')
    if (launch['experiment_sha256'] != watch.sha(baseline/'experiment.json')
            or launch['command'] != experiment['command'] or launch['cwd'] != experiment['cwd']
            or experiment['application_manifest_sha256'] != receipt['application_manifest_sha256']
            or experiment['diagnostic_window_effective_seconds'] != 3600
            or experiment['historical_costs_reset'] is not False
            or experiment['production_resumed'] is not False):
        raise ValueError('baseline experiment binding differs')
    worker_application = Path(experiment['cwd'])
    verify(worker_application, original_manifest)
    config_path = worker_application/'regen/config/proof-models.json'
    settings = read_artifact(config_path)
    run = read_artifact(pending/'run.json')
    baseline_run = read_artifact(baseline/'run.json')
    for field in ('budgets', 'execution_models', 'implementation', 'checker_policy',
                  'complete_context_policy', 'image', 'schedule'):
        if run.get(field) != baseline_run.get(field):
            raise ValueError('prepared execution configuration differs: '+field)
    checkpoint = read_artifact(pending/'checkpoint.json')
    draft_sha = checkpoint['theories']['Scheduler_Dispatch'].get('draft_version')
    if draft_sha and watch.sha(pending/'theories/Scheduler_Dispatch/document/draft.thy') != draft_sha:
        raise ValueError('pending draft differs from original checkpoint')
    if (Path(run['model_config_path']) != config_path
            or settings['reasoning_effort'] != 'medium'
            or settings['role_models']['File Planner'] != 'gpt-6-astra'):
        raise ValueError('original model binding differs')
    next_experiment = deepcopy(experiment)
    command = next_experiment['command']
    for option, before, after in (('--run-dir', str(baseline), str(pending)),
                                  ('--generation-strategy', 'baseline', 'helper-closure-v1')):
        if command.count(option) != 1 or command[command.index(option)+1] != before:
            raise ValueError('ambiguous original command: '+option)
        command[command.index(option)+1] = after
    evidence = {
        'schema': 'unstarted-closure-arm-continuation-v1', 'created_at_unix': time.time(),
        'control': str(control), 'active_run_dir': str(pending),
        'control_receipt_sha256': watch.sha(receipt_path),
        'prepared_binding_sha256': watch.sha(binding_path),
        'preparation_sha256': watch.sha(preparation_path),
        'checkpoint_sha256': receipt['checkpoint_sha256'],
        'baseline_report_sha256': watch.sha(report_path),
        'preserved_baseline_artifacts': {
            str(path.relative_to(control)): watch.sha(path) for path in [
                report_path, control/'comparison/baseline/clock-state.json',
                baseline/'trial-stop.json', baseline/'service-launch.json',
                baseline/'service-invocation.json', baseline/'experiment.json',
                *sorted((baseline/'theories/Scheduler_Dispatch/acceptance/commits').glob('*.json')),
            ] if path.is_file()},
        'pending_input_artifacts': {
            str(path.relative_to(pending)): watch.sha(path) for path in [
                pending/'checkpoint.json', pending/'run.json', pending/'workspace-continuation.json',
                *sorted((pending/'theories/Scheduler_Dispatch').rglob('*.json')),
                *sorted((pending/'theories/Scheduler_Dispatch/document').rglob('*.thy')),
            ] if path.is_file()},
        'baseline_trial_stop_sha256': watch.sha(baseline/'trial-stop.json'),
        'original_worker_application': str(worker_application),
        'original_worker_manifest_sha256': watch.sha(original_manifest),
        'supervisor_application': str(supervisor),
        'supervisor_manifest_sha256': watch.sha(supervisor_manifest),
        'pending_initial_state_sha256': rows[1]['initial_state_sha256'],
        'pending_experiment': next_experiment,
        'historical_costs_reset': False, 'baseline_restarted': False,
        'proof_worker_release_changed': False, 'replicates_per_arm': 1,
    }
    return evidence, report


def continue_pending(control, output, supervisor_manifest):
    control, output = Path(control).resolve(), Path(output).resolve()
    with control_lock(control):
        evidence, baseline_report = validate(control, supervisor_manifest)
        evidence['baseline_native_exit_gate'] = trial.native_exit_gate(control/'arms/baseline')
        output.mkdir(parents=True, exist_ok=False)
        durable(output/'continuation-receipt.json', evidence, immutable=True)
        pending = Path(evidence['active_run_dir'])
        report = {'comparison_complete': False, 'phase': 'launching',
                  'continuation_receipt_sha256': watch.sha(output/'continuation-receipt.json'),
                  'arms': [{'strategy': 'baseline', 'report': baseline_report}]}
        old_signals = {}
        def terminate(signum, frame):
            raise KeyboardInterrupt('continuation interrupted by signal '+str(signum))
        for signum in (signal.SIGTERM, signal.SIGHUP):
            old_signals[signum] = signal.signal(signum, terminate)
        try:
            durable(pending/'experiment.json', evidence['pending_experiment'], immutable=True)
            durable(output/'progress.json', report)
            trial.service.start(pending)
            report['phase'] = 'observing'
            durable(output/'progress.json', report)
            result = trial.observe(pending, control/'comparison/helper-closure-v1', 'Scheduler_Dispatch')
            report['arms'].append({'strategy': 'helper-closure-v1', 'report': result})
            if result['final_service']['running'] or result['cleanup_error']:
                raise RuntimeError('optimized arm service has not stopped')
            result['native_exit_gate'] = trial.native_exit_gate(pending)
            if (not result.get('window_result_verified') or result.get('reason') not in
                    {'first_new_contract', 'trial_effective_budget_exhausted'}):
                raise RuntimeError('optimized arm did not reach a verified normal terminal edge')
            for name, expected in evidence['preserved_baseline_artifacts'].items():
                if watch.sha(control/name) != expected:
                    raise RuntimeError('completed baseline artifact changed: '+name)
            report.update(comparison_complete=True, phase='complete', replicates_per_arm=1,
                          limitation='One stochastic run per arm; no general success-rate claim.')
        except BaseException as error:
            report.update(phase='failed', error={'type': type(error).__name__, 'message': str(error)})
            raise
        finally:
            try:
                if (pending/'service-launch.json').exists():
                    if trial.service.status(pending)['running']:
                        trial.service.stop(pending)
                    trial.native_exit_gate(pending)
            except BaseException as error:
                report.update(comparison_complete=False, phase='cleanup_failed', cleanup_error=str(error))
                raise
            finally:
                for signum, handler in old_signals.items():
                    signal.signal(signum, handler)
                durable(output/'progress.json', report)
                durable(output/'report.json', report, immutable=True)
        durable(control/'comparison/report.json', report, immutable=True)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--supervisor-manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    if args.validate_only:
        import json
        print(json.dumps(validate(args.control, args.supervisor_manifest)[0], indent=2))
    else:
        if args.output is None:
            parser.error('--output required')
        continue_pending(args.control, args.output, args.supervisor_manifest)


if __name__ == '__main__':
    main()
