"""Evidence-bound, single-use gates for controlled online proof experiments."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import time

from regen.execution.proof_commits import digest, durable
from regen.infrastructure.incremental_log import read_artifact
from regen.tools import green_contract_ab as runner
from regen.tools import proof_idle_watch as watch
from regen.tools.application_release import verify

DEFAULT_OUTPUT = Path('reports/regen/controlled-proof-experiments-20261007/workflow')
GATES = {
    'clock_recovery': {
        'checks': ('production_same_theory_contract_reset', 'target_fixed_budget',
                   'target_audit_counted', 'seal_excluded', 'untrusted_clock_pauses',
                   'accepted_state_preserved', 'checkpoint_recovery_verified'),
        'artifacts': ('clock_validation', 'recovery_validation')},
    'mechanism_replay': {
        'checks': ('real_size_inputs', 'identical_trace', 'cold_and_warm_separate',
                   'same_application_both_arms', 'native_acceptance_verified',
                   'costs_preserved', 'mechanism_improved'),
        'artifacts': ('mechanism_replay',)},
}


def bound_file(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError('required evidence file missing: '+str(path))
    return {'path': str(path), 'sha256': watch.sha(path)}


def check_file(reference):
    path = Path(reference['path'])
    if not path.is_absolute() or watch.sha(path) != reference['sha256']:
        raise ValueError('bound file changed: '+str(path))
    return path


def freeze_plan(config_path, manifest_path, application, output=DEFAULT_OUTPUT):
    config_path, manifest_path, application, output = map(lambda p: Path(p).resolve(),
                                                        (config_path, manifest_path, application, output))
    verify(application, manifest_path)
    config = read_artifact(config_path)
    if config.get('budget_effective_seconds', 3600) != 3600:
        raise ValueError('controlled stage budget is fixed at 3600 seconds per arm')
    if len(config['formal_cases']) != 2:
        raise ValueError('formal stage requires exactly two cases')
    if config.get('variant_strategy') != 'bounded-audited-prefix-v1':
        raise ValueError('unexpected intervention')
    runner.closure.validate_limits(3600, config['wall_limit_seconds'])
    cases = [config['pilot_case'], *config['formal_cases']]
    names = set()
    for case in cases:
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', case['name']) or case['name'] in names:
            raise ValueError('case names must be unique safe identifiers')
        names.add(case['name'])
        if not re.fullmatch(r'[A-Za-z0-9_]+', case['theory']) or not case['target_contract_id'].startswith('contract:'):
            raise ValueError('invalid theory or target')
        check_file({'path': case['checkpoint'], 'sha256': case['checkpoint_sha256']})
        # A prepared fixture receipt is optional while designing, mandatory online.
        if 'prepared_fixture' in case:
            check_file(case['prepared_fixture'])
    if ':chooseThread_corres:' not in config['pilot_case']['target_contract_id']:
        raise ValueError('pilot must be the declared chooseThread positive control')
    offline_inputs = config.get('offline_inputs', [])
    if not offline_inputs:
        raise ValueError('explicit frozen offline replay inputs required')
    for reference in offline_inputs:
        check_file(reference)
    binding = {'application_manifest_sha256': watch.sha(manifest_path),
               'config_sha256': watch.sha(config_path),
               'input_sha256': digest({'online_cases': cases, 'offline_inputs': offline_inputs})}
    rows = []
    for stage, selected, pairs in (('pilot', [config['pilot_case']], 1), ('formal', config['formal_cases'], 2)):
        for case in selected:
            for pair in range(pairs):
                order = ('baseline', config['variant_strategy']) if pair == 0 else (config['variant_strategy'], 'baseline')
                for strategy in order:
                    rows.append({'stage': stage, 'case': case, 'pair': pair+1, 'strategy': strategy,
                                 'name': case['name']+'--pair'+str(pair+1)+'--'+('baseline' if strategy == 'baseline' else 'variant'),
                                 'budget_effective_seconds': 3600})
    plan = {'schema': 'controlled-proof-plan-v1', 'binding': binding,
            'application': str(application), 'manifest': bound_file(manifest_path),
            'config': bound_file(config_path), 'arms': rows, 'wall_limit_seconds': config['wall_limit_seconds'],
            'policies': {'production': 'no_new_contract', 'experiment': 'fixed_budget',
                         'production_timeout_seconds': 3600, 'model_lifecycle_policy': 2,
                         'target_audit_counted': True, 'final_seal_excluded': True},
            'effective_arm_hour_caps': {'pilot': 2, 'formal': 8},
            'blocking_units': config.get('blocking_units', []),
            'automatic_replacements': 0, 'automatic_budget_expansion': False,
            'formal_requires_frozen_authorization': True, 'production_resumed': False,
            'limitations': ['Two pairs per case are engineering evidence, not a general success-rate estimate.',
                            'Budget censoring and measurement invalidity are not proof failures.']}
    plan['plan_id'] = digest(plan)
    output.mkdir(parents=True, exist_ok=True)
    durable(output/'plan.json', plan, immutable=True)
    return plan


def load_plan(path):
    plan = read_artifact(path)
    if plan.get('schema') != 'controlled-proof-plan-v1' or digest({k: v for k, v in plan.items() if k != 'plan_id'}) != plan['plan_id']:
        raise ValueError('invalid frozen plan identity')
    verify(Path(plan['application']), check_file(plan['manifest']))
    config = read_artifact(check_file(plan['config']))
    cases = [config['pilot_case'], *config['formal_cases']]
    offline_inputs = config['offline_inputs']
    if digest({'online_cases': cases, 'offline_inputs': offline_inputs}) != plan['binding']['input_sha256']:
        raise ValueError('plan input binding changed')
    for case in cases:
        check_file({'path': case['checkpoint'], 'sha256': case['checkpoint_sha256']})
    for reference in offline_inputs:
        check_file(reference)
    return plan


def validate_evidence(plan, stage, path):
    """An attestation must reference inspectable, hash-bound typed raw evidence."""
    reference = bound_file(path)
    evidence = read_artifact(path)
    if (evidence.get('schema') != 'controlled-proof-gate-evidence-v1' or
            evidence.get('stage') != stage or evidence.get('binding') != plan['binding'] or
            evidence.get('plan_id') != plan['plan_id'] or evidence.get('status') != 'passed'):
        raise ValueError('gate evidence does not bind this stage, release, configuration and inputs')
    required = GATES[stage]
    if any(evidence.get('checks', {}).get(check) is not True for check in required['checks']):
        raise ValueError('gate lacks required verified checks: '+stage)
    artifacts = evidence.get('artifacts', [])
    kinds = {a.get('kind') for a in artifacts}
    if not set(required['artifacts']) <= kinds:
        raise ValueError('gate lacks typed raw evidence')
    for artifact in artifacts:
        raw = read_artifact(check_file(artifact))
        validate_raw_evidence(plan, artifact['kind'], raw)
        # Freeze explicit measured assertions from the underlying report, not a
        # standalone passed:true wrapper referencing an unrelated arbitrary file.
        assertions = artifact.get('assertions')
        if not isinstance(assertions, dict) or not assertions:
            raise ValueError('raw evidence must contain inspectable assertions')
        for dotted, expected in assertions.items():
            value = raw
            for key in dotted.split('.'):
                value = value[int(key)] if isinstance(value, list) else value[key]
            if value != expected:
                raise ValueError('raw evidence assertion differs: '+dotted)
        if not any(key.startswith(('tests', 'measurements', 'cases', 'results', 'checks', 'source_sha256')) for key in assertions):
            raise ValueError('gate assertions must include measured checks or test evidence')
    return reference


def validate_raw_evidence(plan, kind, raw):
    release_files = read_artifact(plan['manifest']['path'])['files']
    if kind in {'clock_validation', 'recovery_validation'}:
        tests = raw.get('tests', {})
        if (raw.get('schema_version') != 1 or raw.get('status') != 'passed' or
                tests.get('run', 0) <= 0 or tests.get('failures') != 0 or tests.get('errors') != 0 or
                not raw.get('source_sha256')):
            raise ValueError('test gate requires source-bound executed test results')
        for path, sha in raw['source_sha256'].items():
            if release_files.get(path, {}).get('sha256') != sha:
                raise ValueError('test evidence does not describe frozen source: '+path)
        if kind == 'clock_validation':
            checks = raw.get('production_and_target_clock_policy_tests', {})
            if (raw.get('model_lifecycle_policy') != 2 or
                    set(checks.get('policies', [])) != {'no_new_contract', 'fixed_budget'} or
                    any(checks.get(k) is not True for k in ('progress_array_unchanged',
                        'unknown_silence_charged', 'missing_model_lifecycle_refuses_budget_conclusion',
                        'candidate_audit_charged_full_seal_excluded'))):
                raise ValueError('raw clock evidence omits required policy/trust tests')
        elif not {'test_prepared_checkpoint'} <= set(tests.get('modules', [])):
            raise ValueError('recovery tests do not cover prepared checkpoint implementation')
    elif kind == 'mechanism_replay':
        if (raw.get('schema') != 'controlled-native-replay-v1' or raw.get('pass') is not True or
                raw.get('model_calls') != 0 or raw.get('production_acceptance_writes') != 0 or
                raw.get('source_unchanged') is not True or raw.get('native_cleanup_verified') is not True or
                raw.get('order') != ['A', 'B', 'B', 'A'] or len(raw.get('arms', [])) != 4 or
                raw.get('initial_prefix_facts', 0) <= 0):
            raise ValueError('raw native replay lacks real-size paired cleanup/acceptance evidence')
        config = read_artifact(plan['config']['path'])
        matching = [ref for ref in config['offline_inputs'] if ref['sha256'] == raw.get('fixture_manifest_sha256')]
        if len(matching) != 1:
            raise ValueError('native replay fixture is not one of the frozen offline inputs')
        fixture = read_artifact(check_file(matching[0]))
        if not fixture.get('application') or any(release_files.get(k, {}).get('sha256') != v
                                               for k, v in fixture['application'].items()):
            raise ValueError('native replay fixture belongs to another application')
        if (not any(a.get('cold_recovery_verified') for a in raw['arms']) or
                any(not a.get('pass') or not a.get('original_costs_preserved') or
                    not a.get('journal_replay_verified') or not a.get('requests') for a in raw['arms'])):
            raise ValueError('native replay lacks verified recovery and preserved costs')
    else:
        raise ValueError('unsupported raw evidence kind')


def gates(plan_path, evidence_dir, stage):
    if stage not in ('pilot', 'formal'):
        raise ValueError('unknown online stage')
    plan = load_plan(plan_path)
    references = {name: validate_evidence(plan, name, Path(evidence_dir)/(name+'.json')) for name in GATES}
    if stage == 'formal':
        pilot_path = Path(plan_path).parent/'pilot/report.json'
        pilot = read_artifact(pilot_path)
        expected_pilot = [r for r in plan['arms'] if r['stage'] == 'pilot']
        if (pilot.get('schema') != 'controlled-proof-stage-report-v1' or pilot.get('stage') != 'pilot' or
                pilot.get('binding') != plan['binding'] or
                [a.get('name') for a in pilot.get('arms', [])] != [r['name'] for r in expected_pilot] or
                pilot.get('plan_id') != plan['plan_id'] or not pilot.get('stage_complete') or
                pilot.get('cleanup_error') or len(pilot.get('arms', [])) != 2 or
                any(not a['report'].get('window_result_verified') or a['report'].get('cleanup_errors')
                    for a in pilot['arms']) or
                not any(a['strategy'] == 'baseline' and a['report'].get('target_success') for a in pilot['arms'])):
            raise ValueError('pilot has not established a clean successful positive-control baseline')
        references['pilot'] = bound_file(pilot_path)
        authorization_path = Path(evidence_dir)/'formal-authorization.json'
        authorization = read_artifact(authorization_path)
        if authorization != {'schema': 'controlled-proof-formal-authorization-v1',
                             'plan_id': plan['plan_id'], 'binding': plan['binding'],
                             'max_arms': 8, 'max_effective_arm_seconds': 28800,
                             'automatic_replacements': 0, 'authorized': True}:
            raise ValueError('formal launch requires exact frozen scope authorization')
        references['authorization'] = bound_file(authorization_path)
    return {'plan_id': plan['plan_id'], 'stage': stage, 'ready': True, 'evidence': references}


def clone_case(case, destination, application_manifest):
    from regen.tools.prepared_checkpoint import derive, application_binding
    receipt = check_file(case['prepared_fixture'])
    fixture = read_artifact(receipt)
    checkpoint = read_artifact(case['checkpoint'])
    if (fixture.get('checkpoint_sha256') != case['checkpoint_sha256'] or
            fixture.get('checkpoint') != checkpoint or fixture.get('source_run') != checkpoint['source_run'] or
            case['theory'] not in checkpoint['theories'] or fixture.get('application') != application_binding()):
        raise ValueError('prepared fixture does not bind case checkpoint, source, theory and application')
    verify(Path(__file__).resolve().parents[2], application_manifest)
    result = derive(receipt.parent, destination, expected_manifest_sha256=case['prepared_fixture']['sha256'])
    # Check target declaration without decoding embedded predecessor histories.
    path = destination/'theories'/case['theory']/'acceptance/commits/000001.json'
    from regen.infrastructure.incremental_log import ObjectStore, find_root
    value = json.loads(path.read_text())
    if '$log_format' in value:
        store = ObjectStore(find_root(path))
        def resolve(node):
            while node[0] == 'ref':
                node = store.node(node[1])[0]
            return node
        node = ['ref', value['root']]
        for field in ('state', 'graph', 'nodes'):
            node = resolve(node)[1][field]
        ids = [resolve(resolve(item)[1]['id'])[1] for item in resolve(node)[1]]
    else:
        ids = [n['id'] for n in value['state']['graph']['nodes']]
    if case['target_contract_id'] not in ids:
        raise ValueError('target declaration missing from prepared checkpoint')
    return result


def resource_gate(plan, directory):
    """Read-only admission; a busy machine never triggers automatic teardown."""
    for unit in plan['blocking_units']:
        if not re.fullmatch(r'[A-Za-z0-9_.@-]+\.service', unit):
            raise ValueError('invalid blocking service identity')
        result = subprocess.run(['systemctl', '--user', 'show', unit,
                                 '--property=LoadState,ActiveState,SubState,MainPID'],
                                capture_output=True, text=True, timeout=15, check=False)
        props = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if result.returncode and props.get('LoadState') != 'not-found':
            raise RuntimeError('cannot verify previous experiment service: '+unit+'; '+result.stderr.strip())
        finished = (props.get('LoadState') == 'not-found' or
                    props.get('ActiveState') in {'inactive', 'failed'} or
                    props.get('SubState') == 'exited' and props.get('MainPID') == '0')
        if not finished:
            raise RuntimeError('previous experiment remains active: '+unit)
    containers = subprocess.run(['docker', 'ps', '--format', '{{.Names}}'],
                                capture_output=True, text=True, timeout=15, check=True)
    if containers.stdout.strip():
        raise RuntimeError('running containers block serial experiment admission')
    available = next(int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines()
                     if line.startswith('MemAvailable:'))
    free = shutil.disk_usage(directory).free
    if available < 18*1024**3 or free < 10*1024**3:
        raise RuntimeError('insufficient experiment memory or disk')
    return {'available_memory_bytes': available, 'free_disk_bytes': free,
            'blocking_units_checked': plan['blocking_units'], 'running_containers': []}


def run_stage(plan_path, evidence_dir, stage):
    """One bounded stage, through the existing service; never retry automatically."""
    plan_path = Path(plan_path).resolve()
    gate = gates(plan_path, evidence_dir, stage)
    plan = load_plan(plan_path)
    if Path(__file__).resolve().parents[2] != Path(plan['application']):
        raise ValueError('online stage must run from its frozen application')
    from regen.infrastructure.audited_llm import model_config_path, model_settings
    settings = model_settings()
    if (model_config_path() != Path(plan['application'])/'regen/config/proof-models.json' or
            settings['role_models']['File Planner'] != 'gpt-6-astra' or settings['reasoning_effort'] != 'medium'):
        raise ValueError('release-bound gpt-6-astra medium required')
    admission = resource_gate(plan, plan_path.parent)
    selected = [r for r in plan['arms'] if r['stage'] == stage]
    for row in selected:
        check_file(row['case']['prepared_fixture'])
    control = plan_path.parent/stage
    control.mkdir(parents=True, exist_ok=False)
    durable(control/'control-receipt.json', {'plan_id': plan['plan_id'], 'gate': gate, 'resource_admission': admission,
            'stage': stage, 'no_automatic_replacements': True}, immutable=True)
    report = {'schema': 'controlled-proof-stage-report-v1', 'plan_id': plan['plan_id'],
              'binding': plan['binding'], 'stage': stage, 'stage_complete': False, 'arms': [],
              'outcome_class': 'running', 'historical_costs_reset': False, 'gate': gate,
              'effective_arm_seconds_cap': 3600*len(selected)}
    prepared = []
    started = time.monotonic()
    with runner.termination_signals():
        try:
            # Prepare just the next arm. Pair clones use the same independently
            # verified fixture; all four expensive forks no longer precede pilot.
            for row in selected:
                resource_gate(plan, control)
                case = row['case']
                root = control/'arms'/row['name']
                report.update(active_arm=row['name'], phase='preparing')
                durable(control/'progress.json', report)
                clone_case(case, root, Path(plan['manifest']['path']))
                prepared.append({'name': root.name, 'run_dir': str(root)})
                # Updating preparation is safe only before the next service is
                # launched; it binds all previously owned arms for ExecStopPost.
                durable(control/'preparation.json', {'arms': prepared})
                durable(control/'prepared-binding.json', {
                    'preparation_sha256': watch.sha(control/'preparation.json'),
                    'control_receipt_sha256': watch.sha(control/'control-receipt.json')})
                initial = runner.closure.accepted_ids(root, case['theory'], initial=True)
                if case['target_contract_id'] in initial:
                    raise ValueError('fixture target already accepted')
                durable(root/'experiment.json', {'cwd': plan['application'],
                    'command': runner.command(root, case['theory'], row['strategy'], case['target_contract_id']),
                    'resource_requirements': {'min_free_disk_bytes': 10*1024**3,
                                              'min_available_memory_bytes': 18*1024**3},
                    'application_manifest_sha256': plan['binding']['application_manifest_sha256'],
                    'clock_policy': 'fixed_budget', 'diagnostic_window_effective_seconds': 3600,
                    'wall_safety_limit_seconds': plan['wall_limit_seconds'], 'target_contract_id': case['target_contract_id'],
                    'historical_costs_reset': False, 'production_resumed': False}, immutable=True)
                runner.service.start(root)
                report['phase'] = 'observing'
                durable(control/'progress.json', report)
                result = runner.observe(root, control/'comparison'/root.name, case['theory'],
                                        case['target_contract_id'], budget=3600, wall_limit=plan['wall_limit_seconds'])
                report['arms'].append({'name': row['name'], 'case_name': case['name'],
                                      'pair': row['pair'], 'strategy': row['strategy'], 'report': result})
                if not result['window_result_verified']:
                    raise RuntimeError('stage paused after invalid arm; no automatic replacement')
            report.update(stage_complete=True, phase='complete', outcome_class='completed', active_arm=None)
        except BaseException as error:
            report.update(phase='safety_paused', error=runner.error_record(error),
                          outcome_class='measurement_invalid' if isinstance(error, runner.clock.ClockEvidenceError)
                          else 'infrastructure_error')
            raise
        finally:
            report['observed_wall_seconds'] = time.monotonic()-started
            try:
                runner.stop_workflow(control)
            except BaseException as error:
                report.update(cleanup_error=runner.error_record(error), stage_complete=False)
                if 'error' not in report:
                    raise
            finally:
                durable(control/'report.json', report, immutable=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('freeze-plan', 'gates', 'run-stage', 'stop-stage'))
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--application', type=Path)
    parser.add_argument('--application-manifest', type=Path)
    parser.add_argument('--evidence-dir', type=Path)
    parser.add_argument('--stage', choices=('pilot', 'formal'))
    args = parser.parse_args()
    if args.operation == 'freeze-plan':
        if not all((args.config, args.application, args.application_manifest)):
            parser.error('freeze-plan requires config, application and application-manifest')
        result = freeze_plan(args.config, args.application_manifest, args.application, args.output)
    else:
        if args.stage is None or (args.operation != 'stop-stage' and args.evidence_dir is None):
            parser.error('stage and evidence-dir required')
        result = (runner.stop_workflow(args.output/args.stage) if args.operation == 'stop-stage' else
                  gates(args.output/'plan.json', args.evidence_dir, args.stage) if args.operation == 'gates' else
                  run_stage(args.output/'plan.json', args.evidence_dir, args.stage))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
