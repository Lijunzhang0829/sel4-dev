"""Prepare an authorized CRefine harness repair without launching or resetting it."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from regen.execution.proof_commits import durable, digest
from regen.infrastructure.incremental_log import read_artifact, logged_copy_batch
from regen.tools.application_release import verify
from regen.tools.provider_feedback_continuation import replacement_experiment


def repair_experiment(source, destination, application, authorization, candidate_timeout):
    if type(candidate_timeout) is not int or candidate_timeout <= 0:
        raise ValueError('candidate timeout must be a positive integer')
    experiment = replacement_experiment(source, destination, application, authorization)
    command = experiment['command']
    if command.count('--candidate-timeout') != 1:
        raise ValueError('source command requires one explicit candidate timeout')
    index = command.index('--candidate-timeout') + 1
    previous = int(command[index])
    if candidate_timeout <= previous:
        raise ValueError('candidate timeout repair must increase the existing limit')
    command[index] = str(candidate_timeout)
    experiment['candidate_timeout_repair'] = {'previous': previous, 'current': candidate_timeout,
                                             'authorization': authorization}
    return experiment


def prepare(source, destination, manifest, *, authorization, candidate_timeout=300):
    from regen.execution.proof_session_runner import acquire_run_mutation_lock, release_run_mutation_lock
    from regen.execution.repository_execution import fork_workspace_continuation
    from regen.execution.check_tasks import TERMINAL
    from regen.infrastructure.audited_llm import model_settings
    from regen.infrastructure.parallel_generation_budget import carry_parallel_budget, review_parallel_budget_clock
    from regen.planning.parallel_file_planner import generation_strategy
    from regen.tools import parallel_workspace_service as service

    source, destination, manifest = [Path(p).resolve() for p in (source, destination, manifest)]
    application = Path(__file__).resolve().parents[2]
    if not isinstance(authorization, str) or not authorization.strip():
        raise ValueError('explicit repair continuation authorization required')
    if destination.exists() or destination == source or destination.is_relative_to(source):
        raise ValueError('repair requires a fresh separate continuation directory')
    verify(application, manifest)
    expected = application/'regen/config/proof-models-crefine-owner-blind-sol-xhigh-local.json'
    if Path(os.environ.get('REGEN_MODEL_CONFIG', '')).resolve() != expected:
        raise ValueError('select the new frozen local model profile')
    settings = model_settings()
    if settings['reasoning_effort'] != 'xhigh' or set(settings['role_models'].values()) != {'gpt-5.6-sol'}:
        raise ValueError('repair must retain Sol/xhigh model bindings')
    experiment = repair_experiment(source, destination, application, authorization, candidate_timeout)
    observed = service.status(source)
    if observed['running'] or (not observed['identity_verified']
                              and observed['status'] not in {'previous_boot', 'not_loaded'}):
        raise ValueError('source service must have verifiably exited')
    lock = acquire_run_mutation_lock(source, operation='parallel-harness-repair-continuation')
    try:
        source_run = read_artifact(source/'run.json')
        if source_run['run_id'] == destination.name:
            raise ValueError('destination directory must provide a new execution identity')
        for path in (source/'check-tasks').glob('*/task.json'):
            if read_artifact(path).get('status') not in TERMINAL:
                raise ValueError('source task remains unsettled: '+str(path))
        dataset = Path(source_run['checker_context_root'])
        input_hash = hashlib.sha256((dataset/'repository-planning-input.json').read_bytes()).hexdigest()
        if input_hash != 'ed996b3328b67f63a789a737e434d44e4052453274c0a62b62ae20b8539d94f1':
            raise ValueError('authorized owner-blind CRefine input changed')
        source_policy = read_artifact(source/'parallel-workspace-policy.json')
        if source_policy['candidate_timeout'] != experiment['candidate_timeout_repair']['previous']:
            raise ValueError('source launch and frozen candidate timeout differ')
        reviewed = review_parallel_budget_clock(source)
        strategy = generation_strategy(source_policy['generation_strategy']['name'])
        with logged_copy_batch():
            fork_workspace_continuation(source, destination)
        budget = carry_parallel_budget(source, destination, authorization=authorization,
            clock_review=reviewed['clock_review'], target_candidate_timeout=candidate_timeout,
            target_strategy=strategy if strategy != source_policy['generation_strategy'] else None)
        durable(destination/'provider-feedback-execution-policy.json',
                source_policy['execution_policy'], immutable=True)
        experiment.update(source_run=str(source),
            application_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
            harness_repair_policy='proof-body-edit-and-declaration-fact-audit-v1')
        durable(destination/'experiment.json', experiment, immutable=True)
        receipt = {'state': 'prepared', 'service_started': False,
            'authorization': authorization, 'source_run': str(source), 'run_dir': str(destination),
            'source_service': observed, 'budget': budget,
            'dataset_sha256': input_hash, 'experiment_sha256': digest(experiment),
            'application_manifest': str(manifest),
            'application_manifest_sha256': experiment['application_manifest_sha256'],
            'mathematical_proof_edits': False, 'historical_costs_reset': False,
            'planning_repeated': False, 'candidate_timeout': candidate_timeout}
        durable(destination/'harness-repair-prepared.json', receipt, immutable=True)
        return receipt
    finally:
        release_run_mutation_lock(lock)


def _implementation_repair_review(source, application, manifest, *, authorization, allow_unchanged=False):
    """Describe the exact code replacement for an operator-reviewed repair.

    Model settings, statements, plan and limits are not implementation repairs.
    A caller must inspect and save this content, then supply it to prepare.
    """
    from regen.infrastructure.audited_llm import model_config_path, model_settings
    source, application, manifest = map(lambda p: Path(p).resolve(), (source, application, manifest))
    if not isinstance(authorization, str) or not authorization.strip():
        raise ValueError('explicit native watchdog repair authorization required')
    release = json.loads(manifest.read_text())
    verify(application, manifest)
    run = read_artifact(source/'run.json')
    original = read_artifact(source/'experiment.json')
    settings = model_settings()
    if (set(run.get('execution_models', {}).values()) != {original.get('model')}
            or set(settings.get('role_models', {}).values()) != {original.get('model')}
            or settings.get('reasoning_effort') != original.get('reasoning_effort')):
        raise ValueError('native watchdog repair must retain source model and reasoning effort')
    previous_config = Path(run['model_config_path']).resolve()
    selected_config = model_config_path().resolve()
    config_hash = hashlib.sha256(previous_config.read_bytes()).hexdigest()
    if hashlib.sha256(selected_config.read_bytes()).hexdigest() != config_hash:
        raise ValueError('native watchdog repair must retain exact model configuration content')
    previous_app = Path(original['cwd']).resolve()
    previous_key = str(previous_config.relative_to(previous_app)) if previous_config.is_relative_to(previous_app) else str(previous_config)
    if run['implementation'].get(previous_key) != config_hash:
        raise ValueError('source model configuration no longer matches its implementation binding')
    target = {}
    for folder in ('execution', 'planning', 'infrastructure', 'analysis'):
        for path in sorted((application/'regen'/folder).iterdir()):
            if path.suffix not in {'.py', '.ML', '.sh'}:
                continue
            name = str(path.relative_to(application))
            value = hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_symlink() or release['files'].get(name, {}).get('sha256') != value:
                raise ValueError('target implementation is not covered by the reviewed application manifest')
            target[name] = value
    config_key = str(selected_config.relative_to(application)) if selected_config.is_relative_to(application) else str(selected_config)
    target[config_key] = config_hash
    old = run['implementation']
    changes = [dict(path=name, source_sha256=old.get(name), target_sha256=target.get(name))
               for name in sorted(set(old)|set(target)) if old.get(name) != target.get(name)]
    if not changes and not allow_unchanged:
        raise ValueError('native watchdog repair requires a reviewed implementation change')
    return dict(policy='native-watchdog-harness-repair-v1', authorization=authorization,
        source_run=str(source), source_run_sha256=digest(run),
        source_implementation_sha256=digest(old), target_implementation_sha256=digest(target),
        target_application_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        implementation_changes=changes, model_configuration_sha256=config_hash,
        model=original['model'], reasoning_effort=original['reasoning_effort'],
        limits_changed=False, mathematical_proof_edits=False, refund_seconds=0)


def native_watchdog_repair_review(source, application, manifest, *, authorization):
    return _implementation_repair_review(source, application, manifest, authorization=authorization)


def native_watchdog_repair_experiment(source, destination, application, authorization):
    """Relocate only execution identity, policy file and implementation cwd."""
    source, destination, application = map(Path, (source, destination, application))
    original = read_artifact(source/'experiment.json')
    policy = read_artifact(source/'parallel-workspace-policy.json')
    command = list(original['command'])
    def option(name):
        if command.count(name) != 1 or command.index(name)+1 >= len(command):
            raise ValueError('native watchdog repair requires one explicit '+name)
        return command.index(name)+1
    if (not isinstance(authorization, str) or not authorization.strip()
            or command[option('--run-dir')] != str(source)
            or command[option('--planner-model')] != original.get('model')
            or policy.get('model') != original.get('model')
            or float(command[option('--effective-seconds')]) != original.get('effective_seconds')
            or int(command[option('--candidate-timeout')]) != policy.get('candidate_timeout')
            or original.get('include_planning') is not True
            or '--extend-effective-seconds' in command):
        raise ValueError('native watchdog repair source command/configuration differs')
    command[option('--run-dir')] = str(destination)
    for name in ('--planning-budget', '--execution-policy', '--resume-authorization'):
        if command.count(name) > 1:
            raise ValueError('ambiguous source command option: '+name)
        if name in command:
            index = command.index(name)
            del command[index:index+2]
    command.extend(['--execution-policy', str(destination/'harness-repair-execution-policy.json'),
                    '--resume-authorization', authorization])
    return {**original, 'command':command, 'cwd':str(application),
        'historical_costs_reset':False, 'planning_repeated':False,
        'native_watchdog_repair_policy':'native-watchdog-harness-repair-v1'}


def prepare_native_watchdog(source, destination, manifest, *, authorization, implementation_review, run_id=None):
    """Prepare a latest-state repair; never launch, roll back or refund it."""
    from regen.execution.proof_session_runner import acquire_run_mutation_lock, release_run_mutation_lock
    from regen.execution.repository_execution import fork_workspace_continuation
    from regen.execution.check_tasks import TERMINAL
    from regen.infrastructure.parallel_generation_budget import carry_parallel_budget, review_parallel_budget_clock
    from regen.tools import parallel_workspace_service as service

    source, destination, manifest = map(lambda p: Path(p).resolve(), (source, destination, manifest))
    application = Path(__file__).resolve().parents[2]
    if destination.exists() or source == destination or destination.is_relative_to(source):
        raise ValueError('native watchdog repair requires a fresh separate continuation directory')
    expected = native_watchdog_repair_review(source, application, manifest, authorization=authorization)
    supplied = read_artifact(Path(implementation_review).resolve())
    if supplied != expected:
        raise ValueError('implementation review does not bind the exact source and repaired release')
    experiment = native_watchdog_repair_experiment(source, destination, application, authorization)
    observed = service.status(source)
    if observed['running'] or (not observed['identity_verified']
                              and observed['status'] not in {'previous_boot', 'not_loaded'}):
        raise ValueError('source service must have verifiably exited')
    lock = acquire_run_mutation_lock(source, operation='native-watchdog-repair-continuation')
    try:
        if (source/'native-watchdog-budget-transfer.json').exists():
            raise ValueError('native watchdog source budget was already claimed')
        for path in (source/'check-tasks').glob('*/task.json'):
            if read_artifact(path).get('status') not in TERMINAL:
                raise ValueError('source native task remains unsettled: '+path.parent.name)
        reviewed = review_parallel_budget_clock(source)
        witnesses = reviewed['inherited_reviews']
        if not witnesses or any(row.get('policy') != 'verified-native-watchdog-interruption-no-refund-v1' for row in witnesses):
            raise ValueError('native watchdog repair requires directly audited watchdog interruption evidence')
        source_policy = read_artifact(source/'parallel-workspace-policy.json')
        # Freeze the reviewed replacement before any new proof state is created.
        if native_watchdog_repair_review(source, application, manifest, authorization=authorization) != supplied:
            raise ValueError('repair implementation changed during preflight')
        with logged_copy_batch():
            run = fork_workspace_continuation(source, destination, run_id=run_id)
        if digest(run['implementation']) != supplied['target_implementation_sha256']:
            raise ValueError('continuation implementation differs from the reviewed repaired release')
        budget = carry_parallel_budget(source, destination, authorization=authorization,
                                      clock_review=reviewed['clock_review'])
        durable(destination/'harness-repair-execution-policy.json', source_policy['execution_policy'], immutable=True)
        durable(destination/'harness-repair-implementation-review.json', supplied, immutable=True)
        experiment.update(source_run=str(source), harness_repair_policy=supplied['policy'],
                          implementation_review_sha256=digest(supplied),
                          application_manifest_sha256=supplied['target_application_manifest_sha256'])
        durable(destination/'experiment.json', experiment, immutable=True)
        receipt = dict(state='prepared', service_started=False, authorization=authorization,
            source_run=str(source), run_dir=str(destination), source_service=observed,
            budget=budget, implementation_review_sha256=digest(supplied),
            application_manifest=str(manifest),
            application_manifest_sha256=supplied['target_application_manifest_sha256'],
            experiment_sha256=digest(experiment), mathematical_proof_edits=False,
            historical_costs_reset=False, planning_repeated=False,
            latest_accepted_state_and_saved_drafts=True, limits_changed=False)
        durable(destination/'harness-repair-prepared.json', receipt, immutable=True)
        return receipt
    finally:
        release_run_mutation_lock(lock)


def resource_reduction_repair_review(source, application, manifest, *, authorization, target_resources):
    """Review resource-only changes; proof execution code stays fixed."""
    from regen.infrastructure.audited_llm import model_config_path
    from regen.infrastructure.parallel_generation_budget import _resource_reduction_policy
    source, application = Path(source).resolve(), Path(application).resolve()
    result = _implementation_repair_review(source, application, manifest,
        authorization=authorization, allow_unchanged=True)
    run = read_artifact(source/'run.json')
    original = read_artifact(source/'experiment.json')
    old_app, old_config = Path(original['cwd']).resolve(), Path(run['model_config_path']).resolve()
    selected = model_config_path().resolve()
    old_key = str(old_config.relative_to(old_app)) if old_config.is_relative_to(old_app) else str(old_config)
    new_key = str(selected.relative_to(application)) if selected.is_relative_to(application) else str(selected)
    allowed = {'regen/infrastructure/parallel_generation_budget.py', old_key, new_key}
    if any(row['path'] not in allowed for row in result['implementation_changes']):
        raise ValueError('resource-only continuation cannot replace proof execution implementation')
    source_policy = read_artifact(source/'parallel-workspace-policy.json')
    target_policy, _ = _resource_reduction_policy(source_policy, target_resources)
    return {**result, 'policy':'resource-reduction-continuation-v1', 'limits_changed':True,
        'target_resources':dict(target_resources), 'source_policy_sha256':digest(source_policy),
        'target_policy_sha256':digest(target_policy)}


def resource_reduction_experiment(source, destination, application, authorization, target_resources):
    from regen.infrastructure.parallel_generation_budget import _resource_reduction_policy
    source = Path(source)
    policy = read_artifact(source/'parallel-workspace-policy.json')
    _resource_reduction_policy(policy, target_resources)
    result = native_watchdog_repair_experiment(source, destination, application, authorization)
    command = result['command']
    for option, key in (('--max-parallel-theories','max_parallel_theories'),
                        ('--resident-limit','resident_limit'), ('--native-slots','native_slots')):
        if command.count(option) != 1 or command.index(option)+1 >= len(command):
            raise ValueError('resource reduction requires one explicit '+option)
        index = command.index(option)+1
        if int(command[index]) != policy[key]:
            raise ValueError('source command and frozen resource policy differ')
        command[index] = str(target_resources[key])
    result.pop('native_watchdog_repair_policy', None)
    result['resource_reduction_policy'] = 'resource-reduction-continuation-v1'
    return result


def prepare_resource_reduction(source, destination, manifest, *, authorization,
                               implementation_review, target_resources, run_id=None):
    """Prepare a stopped latest-state continuation with only smaller resources."""
    from regen.execution.proof_session_runner import acquire_run_mutation_lock, release_run_mutation_lock
    from regen.execution.repository_execution import fork_workspace_continuation
    from regen.execution.check_tasks import TERMINAL
    from regen.infrastructure.parallel_generation_budget import carry_parallel_budget, review_parallel_budget_clock
    from regen.tools import parallel_workspace_service as service

    source, destination, manifest = map(lambda p:Path(p).resolve(), (source, destination, manifest))
    application = Path(__file__).resolve().parents[2]
    if destination.exists() or source == destination or destination.is_relative_to(source):
        raise ValueError('resource reduction requires a fresh separate continuation directory')
    expected = resource_reduction_repair_review(source, application, manifest,
        authorization=authorization, target_resources=target_resources)
    supplied = read_artifact(Path(implementation_review).resolve())
    if supplied != expected:
        raise ValueError('resource reduction review does not bind the exact source, release and limits')
    experiment = resource_reduction_experiment(source, destination, application, authorization, target_resources)
    observed = service.status(source)
    if observed['running'] or (not observed['identity_verified']
                              and observed['status'] not in {'previous_boot','not_loaded'}):
        raise ValueError('source service must have verifiably exited')
    lock = acquire_run_mutation_lock(source, operation='resource-reduction-continuation')
    try:
        if any((source/name).exists() for name in ('resource-reduction-budget-transfer.json',
                'native-watchdog-budget-transfer.json','provider-feedback-transfer.json')):
            raise ValueError('source budget was already claimed by a continuation')
        for path in (source/'check-tasks').glob('*/task.json'):
            if read_artifact(path).get('status') not in TERMINAL:
                raise ValueError('source native task remains unsettled: '+path.parent.name)
        reviewed = review_parallel_budget_clock(source)
        source_policy = read_artifact(source/'parallel-workspace-policy.json')
        if resource_reduction_repair_review(source, application, manifest,
                authorization=authorization, target_resources=target_resources) != supplied:
            raise ValueError('resource reduction implementation changed during preflight')
        with logged_copy_batch():
            run = fork_workspace_continuation(source, destination, run_id=run_id)
        if digest(run['implementation']) != supplied['target_implementation_sha256']:
            raise ValueError('continuation implementation differs from the reviewed release')
        budget = carry_parallel_budget(source, destination, authorization=authorization,
            clock_review=reviewed['clock_review'], target_resources=target_resources)
        durable(destination/'parallel-workspace-policy.json',
                {**source_policy, **target_resources}, immutable=True)
        durable(destination/'harness-repair-execution-policy.json', source_policy['execution_policy'], immutable=True)
        durable(destination/'harness-repair-implementation-review.json', supplied, immutable=True)
        experiment.update(source_run=str(source), harness_repair_policy=supplied['policy'],
            implementation_review_sha256=digest(supplied),
            application_manifest_sha256=supplied['target_application_manifest_sha256'])
        durable(destination/'experiment.json', experiment, immutable=True)
        receipt = dict(state='prepared', service_started=False, authorization=authorization,
            source_run=str(source), run_dir=str(destination), source_service=observed,
            budget=budget, implementation_review_sha256=digest(supplied),
            application_manifest=str(manifest),
            application_manifest_sha256=supplied['target_application_manifest_sha256'],
            experiment_sha256=digest(experiment), mathematical_proof_edits=False,
            historical_costs_reset=False, planning_repeated=False,
            latest_accepted_state_and_saved_drafts=True,
            target_resources=dict(target_resources), limits_changed=True)
        durable(destination/'harness-repair-prepared.json', receipt, immutable=True)
        return receipt
    finally:
        release_run_mutation_lock(lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--authorization', required=True)
    parser.add_argument('--candidate-timeout', type=int, default=300)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--native-watchdog-repair', action='store_true')
    modes.add_argument('--resource-reduction', action='store_true')
    parser.add_argument('--max-parallel-theories', type=int)
    parser.add_argument('--resident-limit', type=int)
    parser.add_argument('--native-slots', type=int)
    parser.add_argument('--implementation-review', type=Path)
    parser.add_argument('--run-id', help='new execution identity for a reviewed continuation')
    args = parser.parse_args()
    resources = {'max_parallel_theories':args.max_parallel_theories,
                 'resident_limit':args.resident_limit, 'native_slots':args.native_slots}
    if args.resource_reduction:
        if args.implementation_review is None or any(value is None for value in resources.values()):
            parser.error('--resource-reduction requires --implementation-review and all three resource limits')
        if args.candidate_timeout != 300:
            parser.error('--resource-reduction preserves the source candidate timeout')
        receipt = prepare_resource_reduction(args.source, args.output, args.manifest,
            authorization=args.authorization, implementation_review=args.implementation_review,
            target_resources=resources, run_id=args.run_id)
    elif args.native_watchdog_repair:
        if any(value is not None for value in resources.values()):
            parser.error('resource limits belong to --resource-reduction')
        if args.implementation_review is None:
            parser.error('--native-watchdog-repair requires --implementation-review')
        receipt = prepare_native_watchdog(args.source, args.output, args.manifest,
            authorization=args.authorization, implementation_review=args.implementation_review, run_id=args.run_id)
    else:
        if any(value is not None for value in resources.values()):
            parser.error('resource limits belong to --resource-reduction')
        if args.run_id is not None:
            parser.error('--run-id belongs to --native-watchdog-repair')
        if args.implementation_review is not None:
            parser.error('--implementation-review belongs to --native-watchdog-repair')
        receipt = prepare(args.source, args.output, args.manifest,
                          authorization=args.authorization, candidate_timeout=args.candidate_timeout)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
