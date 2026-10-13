"""Freeze and aggregate a small contract-rooted AND/OR A/B experiment.

This module is deliberately observational.  It never launches a model, an
Isabelle process, a service, or a container.  Both arms use the same frozen
checkpoint, AND/OR problem, model, effort, resources, limits, and
``bounded-audited-prefix-v1`` acceptance policy.  The sole intervention is the
search policy.

The aggregate reads authoritative document-journal transitions for target
GREEN and the last cumulative usage observation in each model session.  It
does not add repeated cumulative snapshots.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import random
import re

from regen.execution.proof_commits import commit_paths, digest, durable
from regen.execution.repository_execution import (
    journal as repository_journal,
    package_for,
)
from regen.infrastructure.incremental_log import read_artifact
from regen.planning.and_or_problem_planner import (
    planning_recipe,
    verify_planning_result,
)
from regen.planning.contract_and_or import SCHEMA as AND_OR_SCHEMA, validate_problem
from regen.planning.online_policy_composition import (
    ARM_ATTESTATION_NAME,
    EXISTING_SEARCH,
    arm_policy,
    verify_arm_attestation,
)


PLAN_SCHEMA = 'contract-and-or-ab-plan-v1'
RESULT_SCHEMA = 'contract-and-or-ab-arm-result-v1'
REPORT_SCHEMA = 'contract-and-or-ab-report-v1'
ACCEPTANCE_POLICY = 'bounded-audited-prefix-v1'
SEARCH_POLICIES = (EXISTING_SEARCH, AND_OR_SCHEMA)
STRATA = ('ordinary', 'long')
TOKEN_FIELDS = (
    'total_tokens',
    'input_tokens',
    'cached_input_tokens',
    'noncached_input_tokens',
    'output_tokens',
    'reasoning_output_tokens',
)
PROVIDER_TOKEN_FIELDS = {
    'total_tokens': 'totalTokens',
    'input_tokens': 'inputTokens',
    'cached_input_tokens': 'cachedInputTokens',
    'output_tokens': 'outputTokens',
    'reasoning_output_tokens': 'reasoningOutputTokens',
}
ROLLOUT_TOKEN_FIELDS = {
    'total_tokens': 'total_tokens',
    'input_tokens': 'input_tokens',
    'cached_input_tokens': 'cached_input_tokens',
    'output_tokens': 'output_tokens',
    'reasoning_output_tokens': 'reasoning_output_tokens',
}
EXECUTION_STATUSES = frozenset({
    'completed', 'budget_censored', 'proof_failure',
    'treatment_planning_failure',
    'infrastructure_failure', 'measurement_invalid', 'incomplete',
})


def _sha256(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def _resolve(path, base):
    value = Path(path)
    if not value.is_absolute():
        value = Path(base) / value
    return value.resolve()


def _bound_file(path, base, expected=None):
    path = _resolve(path, base)
    if not path.is_file():
        raise ValueError('frozen input is not a file: ' + str(path))
    sha = _sha256(path)
    if expected is not None and expected != sha:
        raise ValueError('frozen input hash mismatch: ' + str(path))
    return {'path': str(path), 'sha256': sha}


def _check_reference(reference):
    if not isinstance(reference, dict):
        raise ValueError('invalid frozen file reference')
    path = Path(reference.get('path', ''))
    if not path.is_absolute() or not path.is_file():
        raise ValueError('frozen input is unavailable: ' + str(path))
    if _sha256(path) != reference.get('sha256'):
        raise ValueError('frozen input changed: ' + str(path))
    return path


def _positive_number(value, name):
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or value < 0):
        raise ValueError(name + ' must be a finite non-negative number')
    return float(value)


def _safe_identifier(value, name):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value):
        raise ValueError(name + ' must be a safe identifier')
    return value


def _selection_rng(seed, label):
    derived = hashlib.sha256(f'{seed}:{label}'.encode()).digest()
    return random.Random(int.from_bytes(derived, 'big'))


def _case_binding(raw, base, generation, common):
    if not isinstance(raw, dict):
        raise ValueError('every case must be an object')
    case_id = _safe_identifier(raw.get('case_id'), 'case_id')
    stratum = raw.get('stratum')
    if stratum not in STRATA:
        raise ValueError('case stratum must be ordinary or long: ' + case_id)
    theory = _safe_identifier(raw.get('theory'), 'theory')
    step_sequence = raw.get('step_sequence')
    if (not isinstance(step_sequence, int) or isinstance(step_sequence, bool)
            or step_sequence < 1):
        raise ValueError('case step_sequence must be a positive integer')
    target = raw.get('target_contract_id')
    if not isinstance(target, str) or not target:
        raise ValueError('target_contract_id is required: ' + case_id)
    fixture_value = raw.get('prepared_checkpoint')
    if not isinstance(fixture_value, str) or not fixture_value:
        raise ValueError('prepared_checkpoint is required: ' + case_id)
    fixture = _resolve(fixture_value, base)
    prepared_sha = raw.get('prepared_manifest_sha256')
    pristine_sha = raw.get('pristine_provenance_sha256')
    manifest = _verify_prepared_checkpoint(
        fixture, prepared_sha, pristine_sha,
        theory=theory, target_contract_id=target)
    checkpoint = {
        'prepared_fixture': str(fixture),
        'prepared_manifest': _bound_file(
            fixture/'manifest.json', base, prepared_sha),
        'pristine_provenance_sha256': pristine_sha,
        'source_kind': manifest['source_kind'],
    }
    package = _bound_file(
        raw.get('package'), base, raw.get('package_sha256'))
    package_value = read_artifact(Path(package['path']))
    recipe = planning_recipe(
        package_value, target_contract_id=target,
        checkpoint_sha256=prepared_sha,
        provider=generation['provider'], model=common['model'],
        reasoning_effort=common['reasoning_effort'],
        timeout=generation['timeout_seconds'],
        max_attempts=generation['max_attempts'])
    return {
        'case_id': case_id,
        'stratum': stratum,
        'step_sequence': step_sequence,
        'theory': theory,
        'target_contract_id': target,
        'checkpoint': checkpoint,
        'package': package,
        'problem_generation_recipe': recipe,
    }


def _verify_prepared_checkpoint(fixture, prepared_sha, pristine_sha, *,
                                theory, target_contract_id):
    if (not isinstance(prepared_sha, str) or len(prepared_sha) != 64
            or not isinstance(pristine_sha, str) or len(pristine_sha) != 64):
        raise ValueError('both prepared and pristine checkpoint hashes are required')
    from regen.tools.prepared_checkpoint import verify
    manifest = verify(fixture, prepared_sha)
    receipt = manifest.get('pristine_semantic_receipt')
    if (manifest.get('source_kind') != 'target-pristine-existing-v1'
            or manifest.get('pristine_provenance_sha256') != pristine_sha
            or not isinstance(receipt, dict)
            or receipt.get('theory') != theory
            or receipt.get('target_contract_id') != target_contract_id
            or receipt.get('accepted_proofs') != 0
            or receipt.get('model_calls') != 0):
        raise ValueError(
            'prepared checkpoint is not the requested pristine target')
    return manifest


def _common_configuration(config, base):
    if config.get('acceptance_policy') != ACCEPTANCE_POLICY:
        raise ValueError('acceptance_policy must be ' + ACCEPTANCE_POLICY)
    model = config.get('model')
    effort = config.get('reasoning_effort')
    if not isinstance(model, str) or not model or not isinstance(effort, str) or not effort:
        raise ValueError('model and reasoning_effort must be non-empty strings')
    if model != 'gpt-6-astra' or effort != 'medium':
        raise ValueError('this pre-registration requires gpt-6-astra medium')
    generation = config.get('problem_generation')
    if (not isinstance(generation, dict)
            or set(generation) != {
                'provider', 'timeout_seconds', 'max_attempts'}
            or generation.get('provider') != 'codex'
            or not isinstance(generation.get('timeout_seconds'), int)
            or isinstance(generation.get('timeout_seconds'), bool)
            or generation['timeout_seconds'] < 1
            or generation.get('max_attempts') != 3):
        raise ValueError(
            'problem_generation must freeze codex, a positive timeout, '
            'and exactly three attempts')
    resources = config.get('resources')
    if not isinstance(resources, dict) or not resources:
        raise ValueError('a common non-empty resources object is required')
    limits = config.get('limits')
    if not isinstance(limits, dict):
        raise ValueError('common limits are required')
    effective = _positive_number(limits.get('effective_proving_seconds'),
                                 'limits.effective_proving_seconds')
    wall = _positive_number(limits.get('wall_seconds'), 'limits.wall_seconds')
    if effective <= 0 or wall <= 0:
        raise ValueError('experiment limits must be positive')
    instances_value = config.get('instances_root')
    if not isinstance(instances_value, str) or not instances_value:
        raise ValueError('instances_root must be a non-empty path')
    instances_root = _resolve(instances_value, base)
    application_manifest = _bound_file(
        config.get('application_manifest'), base,
        config.get('application_manifest_sha256'))
    return {
        'model': model,
        'reasoning_effort': effort,
        'problem_generation': deepcopy(generation),
        'resources': deepcopy(resources),
        'acceptance_policy': ACCEPTANCE_POLICY,
        'application_manifest': application_manifest,
        'instance_binding': {
            'instances_root': str(instances_root),
            'checkpoint_policy': 'same-frozen-checkpoint-within-pair',
            'arm_instance_policy': 'fresh-independent-instance-per-arm',
            'shared_mutable_state': False,
        },
        'limits': {
            'effective_proving_seconds': effective,
            'wall_seconds': wall,
        },
    }


def _selected_cases(pool, selection, seed):
    if not isinstance(selection, dict) or set(selection) != set(STRATA):
        raise ValueError('selection must give ordinary and long counts')
    chosen = []
    for stratum in STRATA:
        count = selection[stratum]
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError('each stratum requires a positive selection count')
        candidates = sorted(
            (case for case in pool if case['stratum'] == stratum),
            key=lambda case: (case['step_sequence'], case['case_id']))
        if count > len(candidates):
            raise ValueError('selection exceeds ' + stratum + ' case pool')
        # A full stratum is a census, not a randomized ordering.  Keeping its
        # historical step order also makes the pre-registered population and
        # selected manifest identical for the two-case long stratum.
        chosen.extend(
            candidates if count == len(candidates)
            else random.Random(seed).sample(candidates, count))
    return chosen


def _orders(case_id, seed, design, pairs):
    current, treatment = SEARCH_POLICIES
    if design == 'paired-randomized':
        rows = []
        for pair in range(1, pairs + 1):
            order = [current, treatment]
            if _selection_rng(seed, f'order-{case_id}-{pair}').randrange(2):
                order.reverse()
            rows.append(order)
        return rows
    if design == 'abba':
        order = [current, treatment]
        if _selection_rng(seed, 'order-' + case_id + '-abba').randrange(2):
            order.reverse()
        return [order, list(reversed(order))]
    raise ValueError('order_design must be paired-randomized or abba')


def _execution_cases(selected, seed):
    """Freeze one safety gate, then independently shuffle every other case."""
    ordinary = [case for case in selected if case['stratum'] == 'ordinary']
    if not ordinary:
        raise ValueError('execution order requires an ordinary safety-gate case')
    gate = ordinary[0]
    remainder = [case for case in selected if case is not gate]
    _selection_rng(seed, 'case-execution-order-v1').shuffle(remainder)
    return [gate, *remainder]


def freeze_plan(config_path, output=None):
    """Freeze a stratified, paired plan without executing either arm."""
    config_path = Path(config_path).resolve()
    config = read_artifact(config_path)
    if not isinstance(config, dict):
        raise ValueError('configuration must be an object')
    seed = config.get('random_seed')
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError('random_seed must be a fixed integer')
    common = _common_configuration(config, config_path.parent)
    raw_pool = config.get('cases')
    if not isinstance(raw_pool, list) or not raw_pool:
        raise ValueError('a non-empty case pool is required')
    pool = [
        _case_binding(
            case, config_path.parent, common['problem_generation'], common)
        for case in raw_pool]
    ids = [case['case_id'] for case in pool]
    if len(set(ids)) != len(ids):
        raise ValueError('case_id values must be unique')
    population_keys = [
        (case['stratum'], case['step_sequence']) for case in pool]
    if len(set(population_keys)) != len(population_keys):
        raise ValueError(
            'historical step_sequence values must be unique within a stratum')
    selected = _selected_cases(pool, config.get('selection'), seed)
    execution_cases = _execution_cases(selected, seed)
    design = config.get('order_design', 'paired-randomized')
    pairs = config.get('pairs_per_case', 2 if design == 'abba' else 1)
    if not isinstance(pairs, int) or isinstance(pairs, bool) or pairs < 1:
        raise ValueError('pairs_per_case must be a positive integer')
    if design == 'abba' and pairs != 2:
        raise ValueError('abba requires pairs_per_case=2')

    common_input = digest({'common': common, 'candidate_pool': pool,
                           'selection': config['selection'], 'random_seed': seed})
    arms = []
    sequence = 0
    for case in execution_cases:
        case_input = digest({'common_input_sha256': common_input, 'case': case})
        for pair, policies in enumerate(_orders(case['case_id'], seed, design, pairs), 1):
            for within_pair, policy in enumerate(policies, 1):
                sequence += 1
                suffix = 'current' if policy == EXISTING_SEARCH else 'and-or'
                arm_id = f"{case['case_id']}--pair{pair}--{suffix}"
                instance_path = str(Path(
                    common['instance_binding']['instances_root']) / arm_id)
                arm_instance_id = digest({
                    'arm_id': arm_id,
                    'input_binding_sha256': case_input,
                    'application_manifest_sha256': common['application_manifest']['sha256'],
                })
                arms.append({
                    'arm_id': arm_id,
                    'arm_instance_id': arm_instance_id,
                    'arm_instance_path': instance_path,
                    'planning_staging_path': (
                        instance_path + '--planning-staging'),
                    'application_manifest_sha256': common['application_manifest']['sha256'],
                    'checkpoint_sha256':
                        case['checkpoint']['prepared_manifest']['sha256'],
                    'prepared_manifest_sha256':
                        case['checkpoint']['prepared_manifest']['sha256'],
                    'pristine_provenance_sha256':
                        case['checkpoint']['pristine_provenance_sha256'],
                    'prepared_fixture_path':
                        case['checkpoint']['prepared_fixture'],
                    'problem_generation_recipe_sha256':
                        case['problem_generation_recipe']['recipe_sha256'],
                    'case_id': case['case_id'],
                    'stratum': case['stratum'],
                    'pair': pair,
                    'sequence': sequence,
                    'within_pair': within_pair,
                    'search_policy': policy,
                    'input_binding_sha256': case_input,
                })
    config_ref = _bound_file(config_path, config_path.parent)
    body = {
        'schema': PLAN_SCHEMA,
        'config': config_ref,
        'randomization': {
            'seed': seed,
            'selection_algorithm': 'python-mt19937-direct-seed-v1',
            'selection_population_order': 'historical-step-sequence-ascending',
            'selection': 'fixed-seed stratified random sample; no post-sample shuffle',
            'case_execution_order_algorithm': (
                'first-selected-ordinary-safety-gate-then-'
                'sha256-derived-python-random-mt19937-v1'),
            'case_execution_order_rng_label': 'case-execution-order-v1',
            'arm_order_algorithm': 'sha256-derived-python-random-mt19937-v1',
            'strata': list(STRATA),
            'order_design': design,
            'pairs_per_case': pairs,
            'arm_labels': {'A': EXISTING_SEARCH, 'B': AND_OR_SCHEMA},
        },
        'common': common,
        'only_varying_field': 'scheduler_search_stack',
        'search_policies': list(SEARCH_POLICIES),
        'candidate_pool': pool,
        'selected_cases': selected,
        'execution_case_order': [
            {'case_id': case['case_id'], 'stratum': case['stratum'],
             'step_sequence': case['step_sequence']}
            for case in execution_cases
        ],
        'safety_gate': {
            'case_id': execution_cases[0]['case_id'],
            'step_sequence': execution_cases[0]['step_sequence'],
            'policy': (
                'first selected ordinary pair validates the evidence chain; '
                'it does not authorize tuning or replacement and remains in '
                'final descriptive statistics'),
        },
        'selection_population': {
            stratum: [case['step_sequence'] for case in sorted(
                (row for row in pool if row['stratum'] == stratum),
                key=lambda row: (row['step_sequence'], row['case_id']))]
            for stratum in STRATA
        },
        'selected_step_sequences': {
            stratum: [case['step_sequence'] for case in selected
                      if case['stratum'] == stratum]
            for stratum in STRATA
        },
        'common_input_sha256': common_input,
        'arms': arms,
        'execution_authorized': False,
        'scope': 'freeze-and-aggregate-only; no model or native launch',
        'limitations': [
            'A single pair is exploratory engineering evidence, not a success-rate estimate.',
            'Infrastructure and measurement failures are not proof failures.',
            'Target success requires native GREEN recorded by the acceptance journal.',
            'B-only target problem generation is part of the scheduler/search-stack intervention.',
        ],
    }
    plan = {**body, 'plan_id': digest(body)}
    if output is not None:
        durable(Path(output).resolve(), plan, immutable=True)
    return plan


def load_plan(path):
    """Load a plan and revalidate its identity and every frozen input."""
    plan = read_artifact(Path(path).resolve())
    if not isinstance(plan, dict) or plan.get('schema') != PLAN_SCHEMA:
        raise ValueError('not a contract AND/OR A/B plan')
    body = {key: value for key, value in plan.items() if key != 'plan_id'}
    if digest(body) != plan.get('plan_id'):
        raise ValueError('frozen plan identity mismatch')
    config_path = _check_reference(plan['config'])
    if freeze_plan(config_path) != plan:
        raise ValueError('frozen plan does not exactly replay its configuration')
    _check_reference(plan['common']['application_manifest'])
    for case in plan['candidate_pool']:
        _check_reference(case['checkpoint']['prepared_manifest'])
        _verify_prepared_checkpoint(
            case['checkpoint']['prepared_fixture'],
            case['checkpoint']['prepared_manifest']['sha256'],
            case['checkpoint']['pristine_provenance_sha256'],
            theory=case['theory'],
            target_contract_id=case['target_contract_id'])
        package_path = _check_reference(case['package'])
        expected_recipe = planning_recipe(
            read_artifact(package_path),
            target_contract_id=case['target_contract_id'],
            checkpoint_sha256=
                case['checkpoint']['prepared_manifest']['sha256'],
            provider=plan['common']['problem_generation']['provider'],
            model=plan['common']['model'],
            reasoning_effort=plan['common']['reasoning_effort'],
            timeout=plan['common']['problem_generation']['timeout_seconds'],
            max_attempts=
                plan['common']['problem_generation']['max_attempts'])
        if case.get('problem_generation_recipe') != expected_recipe:
            raise ValueError('frozen problem-generation recipe changed')
    if plan.get('only_varying_field') != 'scheduler_search_stack':
        raise ValueError('plan varies more than the scheduler/search stack')
    if plan.get('common', {}).get('acceptance_policy') != ACCEPTANCE_POLICY:
        raise ValueError('plan does not hold bounded-prefix constant')
    if tuple(plan.get('search_policies', [])) != SEARCH_POLICIES:
        raise ValueError('unexpected search-policy arms')
    expected = {case['case_id']: digest({
        'common_input_sha256': plan['common_input_sha256'], 'case': case,
    }) for case in plan['selected_cases']}
    seen = set()
    for arm in plan['arms']:
        if arm['arm_id'] in seen:
            raise ValueError('duplicate arm identity')
        seen.add(arm['arm_id'])
        if (arm['search_policy'] not in SEARCH_POLICIES
                or arm['input_binding_sha256'] != expected.get(arm['case_id'])):
            raise ValueError('arm does not share its frozen case inputs')
    return plan


def _normalize_usage(raw, names):
    if not isinstance(raw, dict):
        return None
    required = ('total_tokens', 'input_tokens', 'output_tokens')
    if any(names[key] not in raw for key in required):
        return None
    result = {}
    for key in PROVIDER_TOKEN_FIELDS:
        field = names[key]
        value = raw.get(field, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError('invalid cumulative token usage field: ' + field)
        result[key] = value
    if result['cached_input_tokens'] > result['input_tokens']:
        raise ValueError('cached input exceeds cumulative input')
    if result['reasoning_output_tokens'] > result['output_tokens']:
        raise ValueError('reasoning output exceeds cumulative output')
    if result['total_tokens'] != (
            result['input_tokens'] + result['output_tokens']):
        raise ValueError('cumulative total tokens differ from input plus output')
    result['noncached_input_tokens'] = (
        result['input_tokens'] - result['cached_input_tokens'])
    return {key: result[key] for key in TOKEN_FIELDS}


def _provider_message(row):
    message = row.get('message') if isinstance(row, dict) else None
    if isinstance(message, dict):
        parsed = message.get('parsed')
        if isinstance(parsed, dict):
            return parsed
        raw = message.get('raw_json')
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return None
            if isinstance(parsed, dict):
                return parsed
    if isinstance(row, dict) and isinstance(row.get('parsed'), dict):
        return row['parsed']
    return row if isinstance(row, dict) and 'method' in row else None


def _usage_observation(row):
    local = row.get('message') if isinstance(row, dict) else None
    if isinstance(local, dict) and local.get('event') == 'turn_completed':
        raw = (local.get('usage') or {}).get('total')
        usage = _normalize_usage(raw, PROVIDER_TOKEN_FIELDS)
        if usage is not None:
            return local.get('thread_id'), usage
    message = _provider_message(row)
    if isinstance(message, dict) and message.get('method') == 'thread/tokenUsage/updated':
        params = message.get('params', {})
        raw = params.get('tokenUsage', {}).get('total')
        usage = _normalize_usage(raw, PROVIDER_TOKEN_FIELDS)
        if usage is not None:
            return params.get('threadId'), usage
    if isinstance(row, dict) and row.get('type') == 'event_msg':
        payload = row.get('payload', {})
        if payload.get('type') == 'token_count':
            raw = (payload.get('info') or {}).get('total_token_usage')
            usage = _normalize_usage(raw, ROLLOUT_TOKEN_FIELDS)
            if usage is not None:
                return payload.get('thread_id') or payload.get('session_id'), usage
    if isinstance(row, dict) and row.get('type') == 'turn.completed':
        raw = row.get('usage')
        if isinstance(raw, dict) and 'total_tokens' not in raw:
            if (isinstance(raw.get('input_tokens'), int)
                    and isinstance(raw.get('output_tokens'), int)):
                raw = {**raw, 'total_tokens': (
                    raw['input_tokens'] + raw['output_tokens'])}
        usage = _normalize_usage(raw, ROLLOUT_TOKEN_FIELDS)
        if usage is not None:
            return row.get('thread_id') or row.get('threadId'), usage
    return None


def last_cumulative_usage(path):
    """Read one transcript's last cumulative counter, never the sum of snapshots."""
    path = Path(path).resolve()
    last = None
    thread = None
    observations = 0
    previous = None
    with path.open(encoding='utf-8') as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f'invalid transcript JSON at {path}:{line_number}') from error
            if (isinstance(row, dict) and row.get('type') == 'session_meta'
                    and isinstance(row.get('payload'), dict)):
                identity = row['payload'].get('id')
                if isinstance(identity, str) and identity:
                    if thread is not None and thread != identity:
                        raise ValueError(
                            'transcript session metadata conflicts with usage')
                    thread = identity
            if isinstance(row, dict) and row.get('type') == 'thread.started':
                identity = row.get('thread_id') or row.get('threadId')
                if isinstance(identity, str) and identity:
                    if thread is not None and thread != identity:
                        raise ValueError(
                            'transcript thread start conflicts with usage')
                    thread = identity
            found = _usage_observation(row)
            if found is None:
                continue
            found_thread, usage = found
            if thread is not None and found_thread is not None and found_thread != thread:
                raise ValueError('one transcript contains multiple cumulative usage threads')
            thread = found_thread or thread
            if previous is not None and any(usage[key] < previous[key] for key in TOKEN_FIELDS):
                raise ValueError('cumulative transcript usage decreased: ' + str(path))
            previous = usage
            observations += 1
            last = {
                'thread_id': thread,
                'usage': usage,
                'line_number': line_number,
                'time_unix': row.get('time_unix'),
            }
    return {
        'path': str(path),
        'sha256': _sha256(path),
        'observations': observations,
        'thread_id': thread,
        'last': last,
    }


def _transcript_reference(value, base, run_dir):
    if (not isinstance(value, dict)
            or set(value) != {'path', 'sha256'}):
        raise ValueError(
            'session transcript references require path and sha256')
    path = _resolve(value['path'], base)
    if (not path.is_file() or path.is_symlink()
            or not path.is_relative_to(run_dir)):
        raise ValueError(
            'session transcript is missing or outside its arm: ' + str(path))
    observed = _sha256(path)
    if observed != value['sha256']:
        raise ValueError('session transcript hash mismatch: ' + str(path))
    return path


def _codex_exec_meta(path, run_dir):
    if Path(path).suffix != '.ndjson':
        return None
    meta_path = Path(path).with_suffix('.meta.json')
    if (not meta_path.is_file() or meta_path.is_symlink()
            or not meta_path.resolve().is_relative_to(run_dir)):
        raise ValueError('audited Codex exec metadata is missing or unsafe')
    meta = read_artifact(meta_path)
    if meta.get('provider') != 'codex-exec-audited':
        return None
    if meta.get('state') not in {'complete', 'failed', 'interrupted'}:
        raise ValueError('audited Codex exec call is not terminal')
    if meta.get('raw_sha256') != _sha256(path):
        raise ValueError('audited Codex exec raw transcript changed')
    declared = meta.get('raw_path')
    if declared is not None and _resolve(declared, meta_path.parent) != Path(path):
        raise ValueError('audited Codex exec metadata names another raw file')
    try:
        started = datetime.fromisoformat(meta['started_at']).timestamp()
        finished = datetime.fromisoformat(meta['finished_at']).timestamp()
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('audited Codex exec metadata lacks wall boundaries') from error
    if finished < started:
        raise ValueError('audited Codex exec wall clock moved backwards')
    wall_ms = meta.get('wall_ms')
    if wall_ms is not None and (
            not isinstance(wall_ms, int) or isinstance(wall_ms, bool)
            or wall_ms < 0):
        raise ValueError('audited Codex exec has invalid monotonic wall_ms')
    return {
        'path': str(meta_path.resolve()),
        'sha256': _sha256(meta_path),
        'state': meta['state'],
        'stage': meta.get('stage'),
        'timed_out': bool(meta.get('timed_out')),
        'started_at_unix': started,
        'finished_at_unix': finished,
        'wall_seconds': (
            wall_ms / 1000.0 if wall_ms is not None
            else finished - started),
    }


def _transcript_inventory(run_dir):
    paths = []
    for path in run_dir.rglob('session-transcript.jsonl'):
        if path.is_symlink() or not path.is_file():
            raise ValueError('unsafe session transcript inventory entry: ' + str(path))
        resolved = path.resolve()
        if not resolved.is_relative_to(run_dir):
            raise ValueError('session transcript inventory escapes arm: ' + str(path))
        paths.append(resolved)
    for meta_path in run_dir.rglob('*.meta.json'):
        if meta_path.is_symlink() or not meta_path.is_file():
            raise ValueError('unsafe model transcript metadata: ' + str(meta_path))
        meta = read_artifact(meta_path)
        if not isinstance(meta, dict) or meta.get('provider') != 'codex-exec-audited':
            continue
        raw_path = meta_path.with_suffix('').with_suffix('.ndjson').resolve()
        if (not raw_path.is_file() or raw_path.is_symlink()
                or not raw_path.is_relative_to(run_dir)):
            raise ValueError('audited Codex exec raw transcript is missing or unsafe')
        # This validates terminal state, declared path, and raw hash.
        _codex_exec_meta(raw_path, run_dir)
        paths.append(raw_path)
    return sorted(set(paths))


def aggregate_transcript_usage(paths, base, run_dir):
    if not isinstance(paths, list):
        raise ValueError('session_transcripts must be a complete list')
    run_dir = Path(run_dir).resolve()
    references = []
    seen_paths = set()
    for value in paths:
        path = _transcript_reference(value, base, run_dir)
        if path in seen_paths:
            raise ValueError('duplicate session transcript path: ' + str(path))
        seen_paths.add(path)
        observed = last_cumulative_usage(path)
        meta = _codex_exec_meta(path, run_dir)
        if meta is not None:
            observed['metadata'] = meta
        references.append(observed)
    inventory = _transcript_inventory(run_dir)
    if sorted(seen_paths) != inventory:
        missing_inventory = sorted(str(path) for path in set(inventory) - seen_paths)
        foreign_inventory = sorted(str(path) for path in seen_paths - set(inventory))
        raise ValueError(
            'session transcript manifest differs from arm inventory; '
            f'missing={missing_inventory}, foreign={foreign_inventory}')
    missing = [row['path'] for row in references
               if row['last'] is None or not row['thread_id']]
    by_session = {}
    for row in references:
        if row['last'] is None:
            continue
        if not row['thread_id']:
            continue
        session = row['thread_id']
        prior = by_session.get(session)
        if prior is None:
            by_session[session] = row
            continue
        left_time = prior['last'].get('time_unix')
        right_time = row['last'].get('time_unix')
        if left_time is None or right_time is None:
            if prior['last']['usage'] != row['last']['usage']:
                raise ValueError('duplicate session transcripts lack comparable timestamps: ' + session)
            continue
        if right_time >= left_time:
            by_session[session] = row
    known = {key: 0 for key in TOKEN_FIELDS}
    for row in by_session.values():
        for key, value in row['last']['usage'].items():
            known[key] += value
    complete = bool(references) and not missing
    planning = [row['metadata'] for row in references
                if row.get('metadata', {}).get('stage') == 'file-planner']
    return {
        'complete': complete,
        'coverage': ('last cumulative usage per distinct session'
                     if complete else 'incomplete; missing usage is unknown, not zero'),
        'totals': known if complete else {key: None for key in TOKEN_FIELDS},
        'known_subtotal': known,
        'distinct_sessions': len(by_session),
        # Preserve identities even for failed calls with no usage.  Missing
        # usage makes the metric incomplete, but must not let the same thread
        # be silently reused by another arm.
        'session_ids': sorted({
            row['thread_id'] for row in references if row['thread_id']}),
        'transcripts': references,
        'missing_cumulative_usage': missing,
        'planning_started_at_unix': (
            min(row['started_at_unix'] for row in planning)
            if planning else None),
        'planning_finished_at_unix': (
            max(row['finished_at_unix'] for row in planning)
            if planning else None),
        'planning_wall_seconds': sum(
            row['wall_seconds'] for row in planning),
        'accounting': ('cached input is a subset of input; noncached=input-cached; '
                       'reasoning is a subset of output; repeated snapshots are not summed'),
    }


def _repository_authority(run_dir, theory):
    run_path = run_dir/'run.json'
    input_path = run_dir/'execution-input.json'
    if not run_path.is_file() or not input_path.is_file():
        raise ValueError('arm is not a repository execution run')
    run = read_artifact(run_path)
    if run.get('execution_mode') != 'repository-projection-v1':
        raise ValueError('arm is not a repository projection run')
    data = read_artifact(input_path)
    matches = [row for row in data.get('files', [])
               if row.get('theory') == theory]
    if len(matches) != 1:
        raise ValueError('target theory is absent or ambiguous in arm run')
    package = package_for(run, matches[0]['file_slot'])
    authority = repository_journal(run_dir, run, package)
    snapshot = authority.replay_snapshot()
    if snapshot is None:
        raise ValueError('target theory has no initialized proof journal')
    return run, package, authority, snapshot


def _journal_metrics(authority, snapshot, target):
    """Measure only a full repository-validator replay, never a hash-only log."""
    commits = Path(authority.directory).resolve()
    paths, _ = commit_paths(commits)
    if not paths:
        raise ValueError('acceptance journal is empty')
    records = [read_artifact(path) for path in paths]
    if len(snapshot['records']) != len(records):
        raise ValueError('journal changed after authoritative replay')
    if any(record.get('binding') != authority.binding for record in records):
        raise ValueError('acceptance journal binding differs from authority')
    initial_proofs = set(records[0]['state']['proofs'])
    if target in initial_proofs:
        raise ValueError('target was already GREEN in the arm initial state')
    prior = initial_proofs
    accepted = None
    document_evidence = []
    for path, record in zip(paths, records):
        proofs = set(record['state']['proofs'])
        added = proofs - prior
        if added:
            evidence = record.get('evidence') or {}
            bounded = evidence.get('bounded_prefix') or {}
            audited = {
                item.get('node_id') for item in evidence.get('node_results', [])
                if isinstance(item, dict) and item.get('status') == 'audited_green'
            }
            if (record.get('event') != 'document'
                    or bounded.get('strategy') != ACCEPTANCE_POLICY
                    or bounded.get('entry_id') not in added
                    or added != {bounded.get('entry_id')}
                    or evidence.get('check', {}).get('verdict') != 'green'
                    or not added <= audited):
                raise ValueError(
                    'proof entered journal without bounded audited GREEN evidence')
            document_evidence.append({
                'entry_id': bounded['entry_id'],
                'evidence_sha256': digest(evidence),
                'commit_id': record['commit_id'],
            })
        if target in added:
            accepted = {
                'commit_path': str(path.resolve()),
                'commit_sha256': _sha256(path),
                'commit_id': record['commit_id'],
                'sequence': record['sequence'],
            }
        prior = proofs
    # Re-read through ProofCommits so concurrent replacement/appends cannot be
    # hidden between semantic replay and metric extraction.
    final_snapshot = authority.replay_snapshot()
    if (final_snapshot['commit_id'] != snapshot['commit_id']
            or final_snapshot['state'] != snapshot['state']):
        raise ValueError('journal advanced while aggregation was in progress')
    final_proofs = set(snapshot['state']['proofs'])
    if accepted is not None and target not in final_proofs:
        raise ValueError('accepted target disappeared from final journal state')
    return {
        'directory': str(commits),
        'journal_binding_sha256': digest(authority.binding),
        'commit_count': len(records),
        'initial_commit_id': records[0]['commit_id'],
        'initial_state_sha256': digest(records[0]['state']),
        'initial_accepted_ids': sorted(initial_proofs),
        'final_accepted_ids': sorted(final_proofs),
        'new_accepted_ids': sorted(final_proofs - initial_proofs),
        'target_journal_green': accepted is not None,
        'target_acceptance': accepted,
        'document_evidence': document_evidence,
        'commit_manifests': [
            {'path': str(path.resolve()), 'sha256': _sha256(path),
             'commit_id': record['commit_id'], 'sequence': record['sequence']}
            for path, record in zip(paths, records)
        ],
    }


def _verify_application_binding(plan, run):
    manifest_path = _check_reference(plan['common']['application_manifest'])
    manifest = read_artifact(manifest_path)
    files = manifest.get('files') if isinstance(manifest, dict) else None
    implementation = run.get('implementation')
    if not isinstance(files, dict) or not isinstance(implementation, dict):
        raise ValueError('application manifest or run implementation is invalid')
    for relative, identity in implementation.items():
        row = files.get(relative)
        if (not isinstance(row, dict)
                or row.get('sha256') != identity):
            raise ValueError(
                'arm implementation differs from frozen application: '
                + str(relative))
    return {
        'path': str(manifest_path),
        'sha256': plan['common']['application_manifest']['sha256'],
        'matched_run_implementation_files': len(implementation),
    }


def _verify_derived_checkpoint(arm, case, run_dir, run):
    manifest = _verify_prepared_checkpoint(
        arm['prepared_fixture_path'], arm['prepared_manifest_sha256'],
        arm['pristine_provenance_sha256'], theory=case['theory'],
        target_contract_id=case['target_contract_id'])
    instance_path = Path(run_dir)/'arm-instance.json'
    if not instance_path.is_file() or instance_path.is_symlink():
        raise ValueError('derived prepared-checkpoint arm receipt is missing')
    instance = read_artifact(instance_path)
    required = {
        'schema', 'instance_id', 'lineage_run_id', 'fixture',
        'fixture_manifest_sha256', 'journal_binding_unchanged',
        'cold_start_validation_required', 'timing',
    }
    if (not isinstance(instance, dict) or set(instance) != required
            or instance.get('schema') != 'prepared-checkpoint-arm-v1'
            or Path(instance.get('fixture', '')).resolve()
            != Path(arm['prepared_fixture_path']).resolve()
            or instance.get('fixture_manifest_sha256')
            != arm['prepared_manifest_sha256']
            or instance.get('lineage_run_id') != run.get('run_id')
            or instance.get('journal_binding_unchanged') is not True
            or instance.get('cold_start_validation_required') is not True):
        raise ValueError('arm does not derive from its frozen prepared checkpoint')
    relocatable = {'contract_package_dir', 'session_progress_ledger'}
    if ({key: value for key, value in run.items()
         if key not in relocatable} != manifest.get('run_binding')):
        raise ValueError('derived arm runtime differs from prepared checkpoint')
    # Proving adds artifacts and later commits, but every semantic baseline
    # byte that defines packages/imports/the initial journal must remain equal
    # to the immutable fixture inventory.
    checked = []
    for relative, identity in manifest.get('files', {}).items():
        path = Path(relative)
        critical = (
            relative in {'execution-input.json', 'target-pristine.json'}
            or path.parts[:1] == ('packages',)
            or path.name == 'imports.json'
            or ('acceptance' in path.parts and 'commits' in path.parts)
        )
        if not critical:
            continue
        destination = Path(run_dir)/path
        if (not destination.is_file() or destination.is_symlink()
                or _sha256(destination) != identity.get('sha256')):
            raise ValueError(
                'derived arm changed a pristine semantic baseline file: '
                + relative)
        checked.append(relative)
    if not checked:
        raise ValueError('prepared checkpoint has no semantic baseline inventory')
    return {
        'fixture': str(Path(arm['prepared_fixture_path']).resolve()),
        'prepared_manifest_sha256': arm['prepared_manifest_sha256'],
        'pristine_provenance_sha256':
            arm['pristine_provenance_sha256'],
        'arm_instance': {
            'path': str(instance_path.resolve()),
            'sha256': _sha256(instance_path),
            'instance_id': instance['instance_id'],
        },
        'verified_semantic_baseline_files': checked,
    }


def _verify_arm_attestation(plan, arm, case, problem, run_dir, package,
                            journal):
    """Use the online supervisor's one attestation schema and verifier."""
    label = 'A' if arm['search_policy'] == EXISTING_SEARCH else 'B'
    return verify_arm_attestation(
        run_dir/ARM_ATTESTATION_NAME,
        problem=problem,
        package=package,
        journal=journal,
        policy=arm_policy(label),
        plan_id=plan['plan_id'],
        arm_id=arm['arm_id'],
        case_name=arm['case_id'],
        arm_instance_id=arm['arm_instance_id'],
        input_binding_sha256=arm['input_binding_sha256'],
        checkpoint_sha256=arm['checkpoint_sha256'],
        pristine_provenance_sha256=arm[
            'pristine_provenance_sha256'],
        prepared_manifest_sha256=arm['prepared_manifest_sha256'],
        application_manifest_sha256=arm['application_manifest_sha256'],
    )


def _root_reachable_helpers(problem, journal):
    goals = {goal['id']: goal for goal in problem['goals']}
    reachable = set()
    pending = [problem['root_goal']]
    while pending:
        goal_id = pending.pop()
        if goal_id in reachable:
            continue
        reachable.add(goal_id)
        for route in goals[goal_id]['routes']:
            pending.extend(route.get('requires', []))
    helper_goals = {
        goal_id for goal_id in reachable
        if goals[goal_id].get('role', 'helper') in {
            'helper', 'residual', 'theorem'}
    }
    theorem_goals = {
        goal_id for goal_id in helper_goals
        if goals[goal_id].get('role') == 'theorem'}
    ordinary_helpers = helper_goals - theorem_goals
    initial = set(journal['initial_accepted_ids'])
    final = set(journal['final_accepted_ids'])
    return {
        'reachable_goal_count': len(reachable),
        'root_reachable_helper_goal_count': len(helper_goals),
        'final_accepted_ids': sorted(helper_goals & final),
        'final_accepted_count': len(helper_goals & final),
        'new_accepted_ids': sorted(helper_goals & (final - initial)),
        'new_accepted_count': len(helper_goals & (final - initial)),
        'helper_or_residual': {
            'goal_count': len(ordinary_helpers),
            'new_accepted_ids': sorted(
                ordinary_helpers & (final - initial)),
            'new_accepted_count': len(
                ordinary_helpers & (final - initial)),
        },
        'theorem': {
            'goal_count': len(theorem_goals),
            'new_accepted_ids': sorted(
                theorem_goals & (final - initial)),
            'new_accepted_count': len(
                theorem_goals & (final - initial)),
        },
    }


def _new_accepted_auxiliary(journal, target_contract_id):
    """Common arm metric independent of B's realized AND/OR problem."""
    identifiers = sorted(
        set(journal['new_accepted_ids']) - {target_contract_id})
    return {
        'new_accepted_ids': identifiers,
        'new_accepted_count': len(identifiers),
    }


def _artifact_reference(value, base, root, label):
    if (not isinstance(value, dict)
            or set(value) != {'path', 'sha256'}):
        raise ValueError(label + ' references require path and sha256')
    path = _resolve(value.get('path'), base)
    if (not path.is_file() or path.is_symlink()
            or not path.is_relative_to(Path(root).resolve())):
        raise ValueError(label + ' is missing or outside its arm: ' + str(path))
    observed = _sha256(path)
    if value.get('sha256') != observed:
        raise ValueError(label + ' hash mismatch: ' + str(path))
    return path


def _verify_embedded_bytes(value):
    if isinstance(value, list):
        for item in value:
            _verify_embedded_bytes(item)
    elif isinstance(value, dict):
        for field in ('source', 'log'):
            content = value.get(field + '_content')
            if content is not None:
                if (not isinstance(content, str)
                        or hashlib.sha256(content.encode()).hexdigest()
                        != value.get(field + '_sha256')):
                    raise ValueError('native checker evidence bytes mismatch')
        for item in value.values():
            _verify_embedded_bytes(item)


def _native_check_inventory(run_dir, theory):
    theory_root = (Path(run_dir)/'theories'/theory).resolve()
    if not theory_root.is_dir():
        raise ValueError('target theory artifact directory is missing')
    rows = []
    for candidate in theory_root.rglob('check.json'):
        path = candidate.resolve()
        if (candidate.is_symlink() or not candidate.is_file()
                or not path.is_relative_to(theory_root)):
            raise ValueError('unsafe native check artifact: ' + str(candidate))
        value = read_artifact(candidate)
        bounded = value.get('bounded_prefix') if isinstance(value, dict) else None
        if (not isinstance(bounded, dict)
                or bounded.get('strategy') != ACCEPTANCE_POLICY):
            continue
        _verify_embedded_bytes(value)
        check = value.get('check')
        if not isinstance(check, dict):
            raise ValueError('bounded-prefix artifact lacks native check evidence')
        verdict = check.get('verdict')
        if check.get('failure_scope'):
            outcome = 'infrastructure_failure'
        elif verdict == 'green':
            if check.get('returncode') != 0:
                raise ValueError('GREEN native check has a nonzero return code')
            outcome = 'green'
        elif verdict == 'red':
            outcome = 'proof_failure'
        else:
            raise ValueError('native check has no classified verdict')
        entry_id = bounded.get('entry_id')
        if not isinstance(entry_id, str) or not entry_id:
            raise ValueError('bounded-prefix native check lacks entry identity')
        rows.append({
            'path': str(path),
            'sha256': _sha256(path),
            'content_sha256': digest(value),
            'entry_id': entry_id,
            'outcome': outcome,
            'wall_seconds': _positive_number(
                bounded.get('check_wall_seconds'),
                'native check wall_seconds'),
        })
    return sorted(rows, key=lambda row: row['path'])


def _native_checks(references, base, run_dir, theory, journal):
    if not isinstance(references, list):
        raise ValueError('native_checks must be a complete artifact list')
    declared = []
    seen = set()
    for reference in references:
        path = _artifact_reference(
            reference, base, run_dir, 'native check artifact')
        if path in seen:
            raise ValueError('duplicate native check artifact: ' + str(path))
        seen.add(path)
        declared.append(path)
    inventory = _native_check_inventory(run_dir, theory)
    inventory_paths = {Path(row['path']) for row in inventory}
    if set(declared) != inventory_paths:
        raise ValueError('native check manifest differs from arm inventory')
    by_evidence = {row['content_sha256']: row for row in inventory}
    if len(by_evidence) != len(inventory):
        raise ValueError('duplicate native check evidence within one arm')
    missing_commits = [
        row for row in journal['document_evidence']
        if row['evidence_sha256'] not in by_evidence
        or by_evidence[row['evidence_sha256']]['entry_id'] != row['entry_id']
    ]
    if missing_commits:
        raise ValueError(
            'journal document commit lacks its raw native check artifact')
    counts = {'green': 0, 'proof_failure': 0, 'infrastructure_failure': 0}
    for row in inventory:
        counts[row['outcome']] += 1
    return {
        'count': len(inventory),
        'wall_seconds': sum(row['wall_seconds'] for row in inventory),
        'outcomes': counts,
        'artifacts': inventory,
        'coverage': 'complete bounded-prefix check.json inventory under target theory',
    }


def _authoritative_timing(reference, base, run_dir, theory, journal, budget,
                          model_usage):
    """Recompute proof-generation time from raw harness lifecycle events."""
    if (not isinstance(reference, dict)
            or set(reference) != {'service_launch', 'service_log'}):
        raise ValueError(
            'timing must bind service_launch and service_log artifacts')
    launch = _artifact_reference(
        reference['service_launch'], base, run_dir, 'service launch receipt')
    log = _artifact_reference(
        reference['service_log'], base, run_dir, 'service lifecycle log')
    if (launch != Path(run_dir)/'service-launch.json'
            or log != Path(run_dir)/'service-runner.log'):
        raise ValueError('timing evidence is not the arm service authority')
    launch_value = read_artifact(launch)
    if launch_value.get('run_dir') != str(Path(run_dir).resolve()):
        raise ValueError('service launch receipt belongs to another arm')
    try:
        service_started = datetime.fromisoformat(
            launch_value['created_at']).timestamp()
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('service launch receipt lacks its wall boundary') from error

    from regen.tools import proof_idle_watch
    from regen.tools import theory_effective_clock as effective_clock

    state = {
        'cursors': {},
        'seen': {},
        'last_progress_at': 0,
        'progress_scope': 'all',
        'clock_mode': effective_clock.MODE,
    }
    effective_clock.ingest([run_dir], state)
    proof_idle_watch.scan([run_dir], state)
    clock = effective_clock.clock_state(state)
    events = [row for row in clock['events']
              if row['run_dir'] == str(Path(run_dir))]
    starts = [row['at'] for row in events
              if row.get('theory') == theory
              and row['event'] == 'theory_generation_started']
    stops = [row['at'] for row in events
             if row.get('theory') == theory
             and row['event'] == 'theory_generation_stopped']
    if not starts or not stops:
        raise ValueError('timing evidence lacks a closed theory generation lane')
    target_acceptance = journal.get('target_acceptance')
    progress = [] if target_acceptance is None else [
        row for row in clock['progress']
        if row.get('theory') == theory
        and row.get('commit_id') == target_acceptance['commit_id']
    ]
    if target_acceptance is not None:
        if (len(progress) != 1
                or target_acceptance.get('commit_sha256')
                != _sha256(target_acceptance['commit_path'])):
            raise ValueError(
                'target acceptance timing is not bound to its journal commit')
    terminal = max(
        [row['at'] for row in events]
        + [row['at'] for row in clock['progress']]
        + stops)
    snapshot = effective_clock.evaluate(
        state, {str(Path(run_dir)): {'running': False}}, terminal,
        budget, budget_policy='fixed_budget')
    row = snapshot['theories'].get(theory)
    if row is None or not snapshot.get('evidence_trusted'):
        raise ValueError('target theory has no trusted effective-clock result')
    intervals, active, unresolved, supported = effective_clock._root_timeline(
        clock['events'], str(Path(run_dir)), terminal, False)
    if (not supported or theory in active or theory in unresolved):
        raise ValueError('target theory timing has unresolved lifecycle state')
    accepted = None
    accepted_at = None
    if progress:
        accepted_at = progress[0]['at']
        accepted = effective_clock.measure(
            intervals.get(theory, []), end=accepted_at)
    planning_started = model_usage.get('planning_started_at_unix')
    envelope_started = min(
        value for value in (service_started, planning_started)
        if value is not None)
    service_envelope = terminal - service_started
    planning_wall = model_usage['planning_wall_seconds']
    attributable_wall = planning_wall + service_envelope
    if service_envelope < 0 or attributable_wall < 0:
        raise ValueError('timing evidence has a negative wall interval')
    return {
        'effective_proving_seconds': row['effective_seconds'],
        'proof_effective_seconds': row['effective_seconds'],
        'proof_generation_wall_seconds': max(stops) - min(starts),
        'proof_service_envelope_seconds': service_envelope,
        'planning_wall_seconds': planning_wall,
        'elapsed_envelope_started_at_unix': envelope_started,
        'elapsed_envelope_seconds': terminal - envelope_started,
        # Planning for several treatment cases may be staged before their
        # proof services.  Charging an early planner start through the final
        # service terminal would assign queue/idle time to one arm.  The
        # attributable end-to-end measure therefore adds audited planning
        # work to that arm's own proof-service envelope.
        'wall_seconds': attributable_wall,
        'end_to_end_wall_seconds': attributable_wall,
        'end_to_end_attributable_wall_seconds': attributable_wall,
        'target_accepted_effective_seconds': accepted,
        'target_accepted_at_unix': accepted_at,
        'terminal_at_unix': terminal,
        'measurement_status': snapshot['measurement_status'],
        'clock_mode': snapshot['clock_mode'],
        'artifacts': {
            'service_launch': {
                'path': str(launch), 'sha256': _sha256(launch)},
            'service_log': {
                'path': str(log), 'sha256': _sha256(log)},
        },
        'accounting': (
            'attributable end-to-end wall is audited planning wall plus the '
            'arm proof-service envelope; elapsed_envelope_seconds separately '
            'records terminal minus earliest planner/service boundary; proof '
            'effective time is '
            'recomputed from raw theory-effective-v1 lifecycle events; '
            'acceptance time is immutable journal publication time'),
    }


def _probes(rows):
    if not isinstance(rows, list):
        raise ValueError('probes must be a list')
    if rows:
        raise ValueError(
            'parent-probe metrics require the unavailable native probe '
            'producer/verifier; self-reported probes are rejected')
    seen = set()
    counts = {'progress': 0, 'no_progress': 0, 'infrastructure_failure': 0}
    wall = 0.0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('probe entry must be an object')
        probe_id = _safe_identifier(row.get('probe_id'), 'probe_id')
        if probe_id in seen:
            raise ValueError('duplicate probe_id: ' + probe_id)
        seen.add(probe_id)
        verdict = row.get('verdict')
        if verdict not in counts:
            raise ValueError('invalid parent probe verdict')
        counts[verdict] += 1
        wall += _positive_number(row.get('wall_seconds'), 'probe wall_seconds')
    return {'count': len(rows), 'wall_seconds': wall, 'verdicts': counts}


def _planning_evidence(plan, arm, case, result, result_path, run_dir,
                       package):
    """Verify the B-only realized problem; A must not create or consume one."""
    frozen_package = read_artifact(_check_reference(case['package']))
    if package != frozen_package:
        raise ValueError('arm package differs from the frozen case package')
    expected_path = run_dir/'treatment-planning'/'and-or-planning-result.json'
    reference = result.get('planning_result')
    if arm['search_policy'] == EXISTING_SEARCH:
        if reference is not None or expected_path.exists():
            raise ValueError('A arm must not generate or consume an AND/OR problem')
        return ({
            'required': False,
            'status': 'not_applicable',
            'result': None,
            'orchestration_wall_seconds': 0.0,
            'native_preflight_wall_seconds': 0.0,
            'native_preflight_calls': 0,
            'staging_cleanup_verified': None,
            'realized_problem_sha256': None,
        }, None)
    path = _artifact_reference(
        reference, result_path.parent, run_dir, 'B planning result')
    if path != expected_path:
        raise ValueError('B planning result is not at its fixed arm path')
    value = verify_planning_result(
        path, package=package,
        target_contract_id=case['target_contract_id'],
        frozen_recipe=case['problem_generation_recipe'],
        proof_arm_run=run_dir,
        staging_run=Path(arm['planning_staging_path']))
    accounting = value['planning_accounting']
    wall_ms = accounting.get('orchestration_total_wall_ms')
    if (not isinstance(wall_ms, int) or isinstance(wall_ms, bool)
            or wall_ms < 0):
        raise ValueError('B planning result has invalid total wall time')
    problem = value.get('problem') if value['status'] == 'accepted' else None
    return ({
        'required': True,
        'status': value['status'],
        'failure_type': value.get('failure_type'),
        'result': {'path': str(path), 'sha256': _sha256(path),
                   'content_sha256': value['result_sha256']},
        'orchestration_wall_seconds': wall_ms / 1000.0,
        'native_preflight_wall_seconds': (
            accounting['native_preflight_wall_ms'] / 1000.0),
        'native_preflight_calls': accounting['native_preflight_calls'],
        'staging_cleanup_verified':
            accounting['staging_cleanup_verified'],
        'realized_problem_sha256': value.get(
            'realized_problem_sha256'),
    }, problem)


def _result_row(plan, arm, case, result_path):
    result_path = Path(result_path).resolve()
    result = read_artifact(result_path)
    required_binding = {
        'plan_id': plan['plan_id'],
        'arm_id': arm['arm_id'],
        'arm_instance_id': arm['arm_instance_id'],
        'case_id': arm['case_id'],
        'search_policy': arm['search_policy'],
        'input_binding_sha256': arm['input_binding_sha256'],
        'application_manifest_sha256': arm['application_manifest_sha256'],
        'checkpoint_sha256': arm['checkpoint_sha256'],
        'prepared_manifest_sha256': arm['prepared_manifest_sha256'],
        'pristine_provenance_sha256':
            arm['pristine_provenance_sha256'],
        'problem_generation_recipe_sha256':
            arm['problem_generation_recipe_sha256'],
    }
    if result.get('schema') != RESULT_SCHEMA:
        raise ValueError('invalid arm result schema: ' + str(result_path))
    if any(result.get(key) != value for key, value in required_binding.items()):
        raise ValueError('arm result does not bind the frozen plan: ' + str(result_path))
    run_value = result.get('run_dir')
    if not isinstance(run_value, str) or not run_value:
        raise ValueError('arm result requires its frozen run_dir')
    run_dir = _resolve(run_value, result_path.parent)
    if str(run_dir) != arm['arm_instance_path']:
        raise ValueError('arm result run_dir differs from its frozen instance')
    run, package, authority, snapshot = _repository_authority(
        run_dir, case['theory'])
    checkpoint = _verify_derived_checkpoint(
        arm, case, run_dir, run)
    fixed_contracts = {
        row['contract_id'] for row in package.get('contracts', [])}
    if case['target_contract_id'] not in fixed_contracts:
        raise ValueError('target contract is absent from authoritative package')
    planning, problem = _planning_evidence(
        plan, arm, case, result, result_path, run_dir, package)
    journal_path = _resolve(result.get('acceptance_journal'), result_path.parent)
    if journal_path != Path(authority.directory).resolve():
        raise ValueError(
            'acceptance journal is not the target theory authority')
    application = _verify_application_binding(plan, run)
    attestation = _verify_arm_attestation(
        plan, arm, case, problem, run_dir, package, authority)
    if result.get('arm_attestation_sha256') != attestation['raw_sha256']:
        raise ValueError('arm result does not bind its immutable attestation')
    status = result.get('execution_status')
    if status not in EXECUTION_STATUSES:
        raise ValueError('invalid execution_status')
    if (planning['status'] == 'accepted'
            and status == 'treatment_planning_failure'):
        raise ValueError('accepted B planning conflicts with planning failure')
    if (planning['status'] == 'treatment_planning_failure'
            and status != 'treatment_planning_failure'):
        raise ValueError('B planning exhaustion must classify as treatment failure')
    if (planning['status'] == 'infrastructure_failure'
            and status != 'infrastructure_failure'):
        raise ValueError('B planning infrastructure failure was misclassified')
    proof_failures = result.get('proof_failures', [])
    infrastructure_failures = result.get('infrastructure_failures', [])
    if not isinstance(proof_failures, list) or not isinstance(infrastructure_failures, list):
        raise ValueError('failure classes must be separate lists')
    journal = _journal_metrics(
        authority, snapshot, case['target_contract_id'])
    tokens = aggregate_transcript_usage(
        result.get('session_transcripts', []), result_path.parent, run_dir)
    timing = _authoritative_timing(
        result.get('timing'), result_path.parent, run_dir, case['theory'],
        journal, plan['common']['limits']['effective_proving_seconds'],
        tokens)
    effective = timing['effective_proving_seconds']
    wall = timing['wall_seconds']
    if effective > wall + 1e-6:
        raise ValueError('effective proving time exceeds arm wall time')
    if wall > plan['common']['limits']['wall_seconds']:
        raise ValueError('wall time exceeds the frozen safety limit')
    accepted_effective = timing['target_accepted_effective_seconds']
    if journal['target_journal_green']:
        accepted_effective = _positive_number(
            accepted_effective, 'target_accepted_effective_seconds')
        if accepted_effective > effective:
            raise ValueError(
                'target acceptance time exceeds observed effective time')
        if accepted_effective > plan['common']['limits']['effective_proving_seconds']:
            raise ValueError('target acceptance occurred after the frozen effective budget')
        if status in {'budget_censored', 'proof_failure'}:
            raise ValueError(
                'target GREEN conflicts with a proof-non-success status')
    elif accepted_effective is not None:
        raise ValueError('target acceptance time exists without journal GREEN')
    elif status == 'completed':
        raise ValueError('completed arm lacks target journal GREEN')
    if status == 'budget_censored':
        if effective < plan['common']['limits']['effective_proving_seconds']:
            raise ValueError('budget_censored arm did not reach the frozen effective budget')
    elif effective > plan['common']['limits']['effective_proving_seconds']:
        raise ValueError('non-censored arm exceeds the frozen effective budget')
    auxiliary = _new_accepted_auxiliary(
        journal, case['target_contract_id'])
    helper = (
        _root_reachable_helpers(problem, journal)
        if problem is not None else None)
    native = _native_checks(
        result.get('native_checks', []), result_path.parent, run_dir,
        case['theory'], journal)
    probes = _probes(result.get('probes', []))
    planning_failure_observed = (
        planning['status'] == 'treatment_planning_failure')
    proof_observed = (
        native['outcomes']['proof_failure'] > 0
        or planning_failure_observed)
    infrastructure_observed = (bool(infrastructure_failures)
        or native['outcomes']['infrastructure_failure'] > 0
        or probes['verdicts']['infrastructure_failure'] > 0)
    if status in {'proof_failure', 'treatment_planning_failure'} and not proof_observed:
        raise ValueError('proof_failure status lacks proof-failure evidence')
    if status == 'infrastructure_failure' and not infrastructure_observed:
        raise ValueError('infrastructure_failure status lacks infrastructure evidence')
    certified_success = journal['target_journal_green']
    post_acceptance_infrastructure = bool(
        certified_success and infrastructure_observed)
    valid_primary = bool(
        certified_success
        or (status in {
                'budget_censored', 'proof_failure',
                'treatment_planning_failure'}
            and not infrastructure_observed))
    return {
        **required_binding,
        'stratum': arm['stratum'],
        'pair': arm['pair'],
        'sequence': arm['sequence'],
        'result': {'path': str(result_path), 'sha256': _sha256(result_path)},
        'execution_status': status,
        'proof_status': ('target_green' if journal['target_journal_green']
                         else 'target_not_green'),
        'target_journal_green': journal['target_journal_green'],
        'target_acceptance': journal['target_acceptance'],
        'target_accepted_effective_seconds': accepted_effective,
        'effective_proving_seconds': effective,
        'wall_seconds': wall,
        'timing_evidence': timing,
        'native_checks': native,
        'model_tokens': tokens,
        'planning': planning,
        'new_accepted_auxiliary': auxiliary,
        'root_reachable_helpers': helper,
        'probes': probes,
        'failures': {
            'proof': deepcopy(proof_failures),
            'infrastructure': deepcopy(infrastructure_failures),
            'proof_failure_observed': proof_observed,
            'treatment_planning_failure_observed':
                planning_failure_observed,
            'infrastructure_failure_observed': infrastructure_observed,
            'post_acceptance_infrastructure_observed':
                post_acceptance_infrastructure,
        },
        'journal': journal,
        'arm_attestation': attestation,
        'checkpoint_binding': checkpoint,
        'application_binding': application,
        'eligible_for_primary_paired_comparison': valid_primary,
        'eligible_for_token_metric_comparison': (
            valid_primary and tokens['complete']),
        'eligible_for_full_metric_comparison': (
            valid_primary and tokens['complete']),
    }


def _difference(treatment, control):
    both_succeeded = (treatment['target_journal_green']
                      and control['target_journal_green'])
    metrics = {
        'target_journal_green': (int(treatment['target_journal_green'])
                                 - int(control['target_journal_green'])),
        'effective_proving_seconds': (treatment['effective_proving_seconds']
                                      - control['effective_proving_seconds']),
        'wall_seconds': treatment['wall_seconds'] - control['wall_seconds'],
        'target_accepted_effective_seconds': (
            treatment['target_accepted_effective_seconds']
            - control['target_accepted_effective_seconds']
            if both_succeeded else None),
        'native_check_count': treatment['native_checks']['count'] - control['native_checks']['count'],
        'native_check_wall_seconds': (treatment['native_checks']['wall_seconds']
                                      - control['native_checks']['wall_seconds']),
        'new_root_reachable_helper_count': (
            treatment['root_reachable_helpers']['new_accepted_count']
            - control['root_reachable_helpers']['new_accepted_count']),
        'probe_count': treatment['probes']['count'] - control['probes']['count'],
        'probe_wall_seconds': (treatment['probes']['wall_seconds']
                               - control['probes']['wall_seconds']),
    }
    for key in TOKEN_FIELDS:
        left = treatment['model_tokens']['totals'][key]
        right = control['model_tokens']['totals'][key]
        metrics['model_' + key] = None if left is None or right is None else left - right
    return metrics


def _policy_summary(rows, policy):
    selected = [row for row in rows if row['search_policy'] == policy]
    primary_rows = [row for row in selected
                    if row['eligible_for_primary_paired_comparison']]
    token_rows = [row for row in primary_rows
                  if row['eligible_for_token_metric_comparison']]
    target_success = sum(row['target_journal_green'] and
        row['eligible_for_primary_paired_comparison'] for row in selected)
    terminal_proof_failures = sum(
        row['execution_status'] == 'proof_failure' for row in selected)
    decisive = target_success + terminal_proof_failures
    return {
        'arms': len(selected),
        'target_journal_green': sum(row['target_journal_green'] for row in selected),
        'target_success_arms': target_success,
        'budget_censored_arms': sum(
            row['execution_status'] == 'budget_censored' for row in selected),
        'proof_failure_arms': terminal_proof_failures,
        'primary_comparison_eligible': sum(
            row['eligible_for_primary_paired_comparison'] for row in selected),
        'fixed_budget_success_denominator': len(primary_rows),
        'target_success_rate_fixed_budget': (
            None if not primary_rows else target_success / len(primary_rows)),
        'token_metric_eligible_arms': len(token_rows),
        'model_tokens_primary_valid_usage_complete_sum': {
            key: sum(row['model_tokens']['totals'][key] for row in token_rows)
            for key in TOKEN_FIELDS
        },
        'full_metric_comparison_eligible': sum(
            row['eligible_for_full_metric_comparison'] for row in selected),
        'proof_failure_observation_arms': sum(
            row['failures']['proof_failure_observed'] for row in selected),
        'infrastructure_failure_arms': sum(
            row['failures']['infrastructure_failure_observed'] for row in selected),
        'effective_proving_seconds_sum': sum(row['effective_proving_seconds'] for row in selected),
        'wall_seconds_sum': sum(row['wall_seconds'] for row in selected),
        'native_check_count': sum(row['native_checks']['count'] for row in selected),
        'native_check_wall_seconds': sum(row['native_checks']['wall_seconds'] for row in selected),
        'new_root_reachable_helper_count': sum(
            row['root_reachable_helpers']['new_accepted_count'] for row in selected),
        'probe_count': sum(row['probes']['count'] for row in selected),
        'decisive_proof_outcomes': decisive,
        'target_success_rate_decisive_only': (
            None if decisive == 0 else target_success / decisive),
    }


def _validate_transcript_ownership(rows):
    owners = {
        'path': {}, 'sha256': {}, 'session': {},
        'native_path': {}, 'native_sha256': {},
        'timing_path': {}, 'timing_sha256': {},
    }
    for row in rows:
        arm = row['arm_id']
        for transcript in row['model_tokens']['transcripts']:
            for kind, value in (
                    ('path', transcript['path']),
                    ('sha256', transcript['sha256'])):
                prior = owners[kind].get(value)
                if prior is not None and prior != arm:
                    raise ValueError(
                        f'session transcript {kind} is reused across arms: '
                        + str(value))
                owners[kind][value] = arm
        for session in row['model_tokens']['session_ids']:
            prior = owners['session'].get(session)
            if prior is not None and prior != arm:
                raise ValueError(
                    'provider thread/session is reused across arms: '
                    + session)
            owners['session'][session] = arm
        for artifact in row['native_checks']['artifacts']:
            for kind, value in (
                    ('native_path', artifact['path']),
                    ('native_sha256', artifact['sha256'])):
                prior = owners[kind].get(value)
                if prior is not None and prior != arm:
                    raise ValueError(
                        'native check evidence is reused across arms: '
                        + str(value))
                owners[kind][value] = arm
        for artifact in row['timing_evidence']['artifacts'].values():
            for kind, value in (
                    ('timing_path', artifact['path']),
                    ('timing_sha256', artifact['sha256'])):
                prior = owners[kind].get(value)
                if prior is not None and prior != arm:
                    raise ValueError(
                        'timing evidence is reused across arms: '
                        + str(value))
                owners[kind][value] = arm


def aggregate(plan_path, results_dir, output=None):
    """Aggregate already-produced arm manifests; never execute missing arms."""
    plan_path = Path(plan_path).resolve()
    results_dir = Path(results_dir).resolve()
    plan = load_plan(plan_path)
    cases = {case['case_id']: case for case in plan['selected_cases']}
    rows = []
    missing = []
    for arm in plan['arms']:
        path = results_dir / (arm['arm_id'] + '.json')
        if not path.is_file():
            missing.append(arm['arm_id'])
            continue
        rows.append(_result_row(plan, arm, cases[arm['case_id']], path))
    _validate_transcript_ownership(rows)

    grouped = {}
    for row in rows:
        grouped.setdefault((row['case_id'], row['pair']), {})[row['search_policy']] = row
    pairs = []
    for (case_id, pair), values in sorted(grouped.items()):
        current = values.get(EXISTING_SEARCH)
        treatment = values.get(AND_OR_SCHEMA)
        complete = current is not None and treatment is not None
        if complete and any(
                current['journal'][key] != treatment['journal'][key]
                for key in ('initial_commit_id', 'initial_state_sha256')):
            raise ValueError(
                'paired arms do not share the same initial checkpoint state')
        primary = bool(complete and current['eligible_for_primary_paired_comparison']
                       and treatment['eligible_for_primary_paired_comparison'])
        full = bool(primary and current['eligible_for_full_metric_comparison']
                    and treatment['eligible_for_full_metric_comparison'])
        target_time = bool(
            primary and current['target_journal_green']
            and treatment['target_journal_green'])
        pairs.append({
            'case_id': case_id,
            'pair': pair,
            'complete': complete,
            'primary_comparison_eligible': primary,
            'token_comparison_eligible': full,
            'target_time_comparison_eligible': target_time,
            'full_metric_comparison_eligible': full,
            'order': [row['search_policy'] for row in sorted(
                (item for item in (current, treatment) if item is not None),
                key=lambda item: item['sequence'])],
            'treatment_minus_current': (_difference(treatment, current)
                                        if primary else None),
        })
    body = {
        'schema': REPORT_SCHEMA,
        'plan': {'path': str(plan_path), 'sha256': _sha256(plan_path),
                 'plan_id': plan['plan_id']},
        'common': deepcopy(plan['common']),
        'only_varying_field': 'search_policy',
        'complete': not missing,
        'missing_arms': missing,
        'arms': sorted(rows, key=lambda row: row['sequence']),
        'pairs': pairs,
        'summary_by_search_policy': {
            policy: _policy_summary(rows, policy) for policy in SEARCH_POLICIES
        },
        'interpretation': {
            'causal_claim_ready': False,
            'notice': ('This layer freezes and aggregates paired observations only. '
                       'Replicate count and graph provenance determine any later causal claim.'),
            'proof_and_infrastructure_failures_separate': True,
            'token_accounting': 'last cumulative usage per distinct session, never sum snapshots',
        },
    }
    report = {**body, 'report_id': digest(body)}
    if output is not None:
        durable(Path(output).resolve(), report, immutable=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    freeze = commands.add_parser('freeze', help='freeze a paired plan only')
    freeze.add_argument('--config', type=Path, required=True)
    freeze.add_argument('--output', type=Path, required=True)
    collect = commands.add_parser('aggregate', help='aggregate completed arm manifests only')
    collect.add_argument('--plan', type=Path, required=True)
    collect.add_argument('--results-dir', type=Path, required=True)
    collect.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.operation == 'freeze':
        result = freeze_plan(args.config, args.output)
    else:
        result = aggregate(args.plan, args.results_dir, args.output)
    print(json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
