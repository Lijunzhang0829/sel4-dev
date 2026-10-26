"""Prepare an authorized CRefine harness repair without launching or resetting it."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from regen.execution.proof_commits import durable, digest
from regen.infrastructure.incremental_log import read_artifact
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--authorization', required=True)
    parser.add_argument('--candidate-timeout', type=int, default=300)
    args = parser.parse_args()
    receipt = prepare(args.source, args.output, args.manifest,
                      authorization=args.authorization, candidate_timeout=args.candidate_timeout)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
