"""Freeze and replay orthogonal search/acceptance control events.

This command never starts a provider, native worker, service, or experiment.
It verifies existing immutable checker and ProofCommits artifacts. The current
free-running File Planner is not a stepwise AND/OR supervisor.
"""
import argparse
import hashlib
import json
from pathlib import Path

from regen.execution.proof_commits import ProofCommits, digest, durable
from regen.execution.repository_execution import validator
from regen.infrastructure.incremental_log import read_artifact
from regen.planning.online_policy_composition import (
    PLAN_SCHEMA,
    apply_online_event,
    arm_policy,
    comparison_plan,
    next_online_directive,
    verify_artifact_locator,
)


CONFIG_SCHEMA = 'contract-and-or-controlled-config-v1'


def _read(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError('required JSON artifact is missing: ' + str(path))
    return read_artifact(path)


def freeze_plan(config_path, output_path):
    """Write a new immutable plan; never reinterpret an existing frozen run."""
    config_path = Path(config_path).resolve()
    output_path = Path(output_path).resolve()
    config = _read(config_path)
    allowed = {
        'schema', 'application_manifest_sha256', 'model',
        'reasoning_effort', 'order', 'effective_seconds', 'wall_seconds',
        'cases',
    }
    if not isinstance(config, dict) or config.get('schema') != CONFIG_SCHEMA:
        raise ValueError('invalid Contract-rooted AND/OR experiment config')
    if set(config) - allowed:
        raise ValueError(
            'unexpected config fields: '
            + ', '.join(sorted(set(config) - allowed)))
    plan = comparison_plan(
        config.get('cases'),
        application_manifest_sha256=config.get(
            'application_manifest_sha256'),
        model=config.get('model', 'gpt-6-astra'),
        reasoning_effort=config.get('reasoning_effort', 'medium'),
        order=tuple(config.get('order', ('A', 'B', 'B', 'A'))),
        effective_seconds=config.get('effective_seconds', 3600),
        wall_seconds=config.get('wall_seconds', 10800),
    )
    plan.pop('plan_id')
    plan.update(
        source_config=str(config_path),
        source_config_raw_sha256=hashlib.sha256(
            config_path.read_bytes()).hexdigest(),
    )
    plan['plan_id'] = digest(plan)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    durable(output_path, plan, immutable=True)
    return plan


def _journal_from_directive(directive, package):
    binding = directive.get('execution_binding', {})
    if digest(package) != binding.get('package_sha256'):
        raise ValueError('package differs from directive binding')
    directory = Path(binding.get('journal_directory', '')).resolve()
    first_path = directory/'commits'/'000001.json'
    if (not first_path.is_file() or first_path.is_symlink()
            or directory.is_symlink()):
        raise ValueError('bound proof journal is missing or unsafe')
    first = read_artifact(first_path)
    journal_binding = first.get('binding')
    if digest(journal_binding) != binding.get('journal_binding_sha256'):
        raise ValueError('proof journal binding differs from directive')
    journal = ProofCommits(
        directory, journal_binding, validator(package))
    journal.replay_snapshot()
    return journal


def apply_event(problem, state, policy, directive, event, package):
    """Replay one event and return state plus its transition receipt."""
    kind = event.get('kind') if isinstance(event, dict) else None
    journal = (
        _journal_from_directive(directive, package)
        if kind == 'acceptance' else None)
    # No parent-probe producer exists in the current application layer. Keep
    # that transition fail-closed instead of treating arbitrary JSON as native
    # residual evidence.
    return apply_online_event(
        problem, state, policy, directive['execution_binding'],
        directive, event, package=package, journal=journal,
        native_artifact_verifier=verify_artifact_locator,
        probe_artifact_verifier=None)


def _emit(value, output=None):
    if output is None:
        print(json.dumps(
            value, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        output = Path(output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        durable(output, value, immutable=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)

    freeze = sub.add_parser(
        'freeze-plan', help='Freeze a new, non-authorizing ABBA plan')
    freeze.add_argument('--config', type=Path, required=True)
    freeze.add_argument('--output', type=Path, required=True)

    decision = sub.add_parser(
        'next-directive', help='Compute one bound search directive')
    decision.add_argument('--problem', type=Path, required=True)
    decision.add_argument('--state', type=Path, required=True)
    decision.add_argument('--binding', type=Path, required=True)
    decision.add_argument('--arm', choices=('A', 'B'), required=True)
    decision.add_argument('--output', type=Path)

    transition = sub.add_parser(
        'apply-event', help='Replay one bound control event')
    transition.add_argument('--problem', type=Path, required=True)
    transition.add_argument('--state', type=Path, required=True)
    transition.add_argument('--directive', type=Path, required=True)
    transition.add_argument('--event', type=Path, required=True)
    transition.add_argument('--package', type=Path, required=True)
    transition.add_argument('--arm', choices=('A', 'B'), required=True)
    transition.add_argument('--output', type=Path)

    args = parser.parse_args(argv)
    if args.operation == 'freeze-plan':
        value = freeze_plan(args.config, args.output)
        if value.get('schema') != PLAN_SCHEMA:
            raise AssertionError('unexpected frozen plan schema')
        print(json.dumps({
            'plan_id': value['plan_id'],
            'output': str(args.output.resolve()),
            'online_authorized': value['online_authorized'],
            'runtime_blockers': value['runtime_blockers'],
        }, indent=2, sort_keys=True))
        return value
    problem, state = _read(args.problem), _read(args.state)
    policy = arm_policy(args.arm)
    if args.operation == 'next-directive':
        value = next_online_directive(
            problem, state, policy, _read(args.binding))
    else:
        value = apply_event(
            problem, state, policy, _read(args.directive),
            _read(args.event), _read(args.package))
    _emit(value, args.output)
    return value


if __name__ == '__main__':
    main()
