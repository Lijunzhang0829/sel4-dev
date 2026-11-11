"""Fresh, bounded model-driven sketch diagnostic through production tools.

The allocation is deliberately manual. This is not repository-planning quality,
natural sketch adoption, or a benchmark score. No historical candidate is read.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import threading
import time

from regen.execution.proof_commits import durable


MODEL = 'gpt-6-astra'
EFFORT = 'low'
STRATEGY = 'sketch-reviewed-v2'
TARGETS = {'0056': 'spec_0056', '0066': 'spec_0066'}
GiB = 1024 ** 3
ADMISSION = {'min_free_disk_bytes': 5 * GiB,
             'min_available_memory_bytes': 18 * GiB}
ACTIVE_RESERVE = {'min_free_disk_bytes': 5 * GiB,
                  'min_available_memory_bytes': 6 * GiB}

DIAGNOSTIC_INSTRUCTIONS = '''
THIS IS A FORCED-SKETCH DIAGNOSTIC, NOT A WHOLE-REPOSITORY PROVING RUN.
Only work on the assigned fixed contract. Its exact statement and owned context
must remain unchanged. No historical proof, helper, route or expected outcome is
provided. Read the target and necessary definitions, then prioritize designing
your own meaningful helper declarations and consumer proof and actually invoke
check_sketch. Do not first spend the budget proving the helpers independently.
Do not rename or copy the full parent obligation into a helper: explain the
smaller reasoning obligation and how the consumer uses it. Record the required
sketch group and use sites through the normal edit interface. Temporary admission
is owned by the checker; never insert sorry/axioms/ML into your proof source.
If check_sketch rejects the design, read its exact feedback and repair the failed
declaration or consumer, then check again. A single failed candidate is feedback,
not a reason to stop. If the conditional sketch closes, follow the normal v2
review workflow, choose a supporting helper, write a real proof body, and submit
at least one ordinary check_theory selecting that helper. A diagnostic probe is
not this formal helper check. You do not need to finish the parent or seal a
theory. The host stops after a conditionally closed sketch and an actually
executed ordinary helper check, even if that helper check is RED. Conditional
closure is not GREEN and does not prove or publish a helper or the parent.
Until then, continue repairing from feedback within the session budget; budget
expiry leaves work pending. Do not use finish, report_unresolved, check_refutation,
or switch to another theory. Nothing in this diagnostic claims the target true
or false. Use only the supplied production tools and current legal premises.
'''


def allocation(payload):
    """Cover the complete scope bundle while running only two assigned cases."""
    contracts = payload['contracts']
    by_name = {row['name']: row for row in contracts}
    if len(contracts) != 91 or len(by_name) != 91:
        raise ValueError('this diagnostic requires the current 91-contract input')
    selected = {key: by_name[name]['contract_id'] for key, name in TARGETS.items()}
    if len(set(selected.values())) != 2:
        raise ValueError('diagnostic target identities must be distinct')
    profiles = [row['context_profile_id'] for row in payload['context_profiles']]
    if not profiles:
        raise ValueError('missing prepared context profile')
    theories = []
    for name, ids in [*(('Sketch_' + key, [cid]) for key, cid in selected.items()),
                      ('Dormant', [c['contract_id'] for c in contracts
                                   if c['contract_id'] not in selected.values()])]:
        theories.append({'name': name, 'purpose': 'Manual diagnostic allocation only.',
            'grouping_reason': 'Keep each selected contract isolated; do not run Dormant.',
            'contract_ids': ids, 'context_profile_ids': profiles,
            'helper_decisions': [], 'suggested_predecessors': []})
    return {'schema_version': 4, 'ordering_rationale':
            'Serial, forced-sketch diagnostic; not an LLM repository plan.',
            'contract_relations': [], 'unresolved_retrieval_questions': [],
            'theories': theories}, selected


def resource_gate(root, *, active=False):
    from regen.tools.parallel_workspace_service import _resource_admission
    from regen.infrastructure.resource_budget import ResourceBudgetError
    try:
        return _resource_admission(root, ACTIVE_RESERVE if active else ADMISSION)
    except RuntimeError as error:
        raise ResourceBudgetError(str(error)) from error


@contextmanager
def frozen_models(output):
    from regen.infrastructure.audited_llm import model_settings
    config = deepcopy(model_settings())
    config['reasoning_effort'] = EFFORT
    config['role_models'] = {role: MODEL for role in config['role_models']}
    path = output / 'models.json'
    durable(path, config, immutable=True)
    previous = os.environ.get('REGEN_MODEL_CONFIG')
    os.environ['REGEN_MODEL_CONFIG'] = str(path)
    try:
        yield config
    finally:
        if previous is None:
            os.environ.pop('REGEN_MODEL_CONFIG', None)
        else:
            os.environ['REGEN_MODEL_CONFIG'] = previous


def initialize(output, dataset):
    from regen.planning.theory_plan import build_bundle
    from regen.planning.plan_revisions import start_plan, append_revision
    from regen.planning.repository_planner import _bind_direct_artifacts
    from regen.execution.repository_execution import init_run
    payload_path = dataset / 'repository-planning-input.json'
    payload = json.loads(payload_path.read_text())
    proposal, selected = allocation(payload)
    artifacts = build_bundle(payload, proposal)
    planning = output / 'allocation'
    identity = start_plan(planning / 'planning', payload['benchmark']['id'])
    current = append_revision(planning / 'planning',
        {**artifacts['repository-plan.json'], 'plan_id': identity['plan_id']})
    artifacts = _bind_direct_artifacts(artifacts, current, planning)
    for name, value in artifacts.items():
        if name != 'repository-plan.json':
            durable(planning / name, value, immutable=True)
    durable(planning / 'diagnostic-proposal.json', proposal, immutable=True)
    proof = output / 'proof'
    run = init_run(proof, dataset, output.name,
        planning / 'file-generation-list.json',
        execution_models={'File Planner': MODEL},
        contract_outcome_policy='contract-dispositions-v1',
        unresolved_evidence_policy='proof-or-refutation-v1')
    return proof, run, selected


def _checked_ids(value):
    """Use executed leaf evidence, never an independent-check wrapper's status."""
    if isinstance(value, list):
        return set().union(*(_checked_ids(v) for v in value)) if value else set()
    if not isinstance(value, dict):
        return set()
    if (value.get('cached_failure') or value.get('framework_blocked')
            or value.get('native_check_executed') is False
            or value.get('verdict') == 'infrastructure_error'
            or value.get('failure_scope') not in (None, '', 'candidate', 'proof-search')):
        return set()
    check = value.get('check', {})
    if isinstance(check, dict) and (check.get('cached_failure')
            or check.get('framework_blocked')
            or check.get('verdict') == 'infrastructure_error'
            or check.get('failure_scope') not in (None, '', 'candidate', 'proof-search')):
        return set()
    # An explicit empty list is authoritative; do not recover stale nested IDs.
    if 'checked_delta_ids' in value:
        return {key for key in value['checked_delta_ids'] if isinstance(key, str)}
    # Independent checks wrap all REDs as failed, including declaration rejection.
    # Only their nested execution rows say whether a proof body was reached.
    for key in ('entry_results', 'node_results'):
        if key in value:
            return _checked_ids(value[key])
    entry = value.get('entry_id', value.get('node_id'))
    if (isinstance(entry, str)
            and value.get('status') in {'audited_green', 'completed_uncommitted', 'failed'}):
        return {entry}
    return set().union(*(_checked_ids(value[key]) for key in ('feedback', 'result')
                         if key in value))


def native_checks(value):
    """One copy per equal checker result within this task; retain timing fields."""
    found = {}
    def visit(item):
        if isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, dict):
            if (item.get('reused_sketch_check') or item.get('cached_failure')
                    or item.get('native_check_executed') is False):
                return
            if 'verdict' in item and ('wall_ms' in item or 'execution_wall_ms' in item):
                fields = ('verdict', 'returncode', 'wall_ms', 'execution_wall_ms',
                    'candidate_execution_ms', 'prefix_prepare_ms', 'check_scope',
                    'failure_scope', 'source_sha256', 'log_sha256', 'source', 'log',
                    'failure_stage', 'failure_kind', 'failure_owner',
                    'consumer_check_started', 'diagnostic')
                row = {key: item[key] for key in fields if key in item}
                found[json.dumps(row, sort_keys=True)] = row
                return
            for child in item.values():
                visit(child)
    visit(value)
    return list(found.values())


class DiagnosticTrace:
    def __init__(self, target):
        self.target = target
        self.actions = []
        self.sketch_calls = 0
        self.closed_helpers = set()
        self.formal_helpers_checked = set()
        self._counted_check_tasks = set()

    def observe(self, name, arguments, result, elapsed):
        proposal = arguments.get('proposal', {})
        action = proposal.get('action', name)
        task_id = result.get('task_id') if isinstance(result, dict) else None
        # Reads can return exact old receipts. A new executing proof_action task
        # is the accounting boundary, not the presence of a verdict in a response.
        fresh_task = (name == 'proof_action'
            and action in {'check_sketch', 'check_theory', 'assess_sketch',
                           'probe_helpers', 'resolve_symbols'}
            and isinstance(task_id, str) and bool(task_id)
            and task_id not in self._counted_check_tasks)
        if fresh_task:
            self._counted_check_tasks.add(task_id)
        checks = native_checks(result) if fresh_task else []
        if name == 'proof_action' and action == 'check_sketch':
            groups = result.get('groups', []) if isinstance(result, dict) else []
            if fresh_task and groups and any(group.get('check') and not group.get('reused_sketch_check')
                              for group in groups):
                self.sketch_calls += 1
            # A revised interface may revoke prior conditional closure.
            for group in groups:
                if group.get('target_entry_id') == self.target:
                    self.closed_helpers = (set(group.get('helper_ids', []))
                        if group.get('status') == 'conditional_closed' else set())
        if fresh_task and action == 'check_theory' and self.closed_helpers:
            self.formal_helpers_checked.update(_checked_ids(result) & self.closed_helpers)
        self.actions.append({'tool': name, 'action': action,
            'elapsed_seconds': elapsed,
            'task_id': task_id, 'native_task_counted': fresh_task,
            'status': result.get('status') if isinstance(result, dict) else None,
            'native_checks': checks})

    @property
    def complete(self):
        return bool(self.closed_helpers & self.formal_helpers_checked)

    def summary(self):
        checks = [c for row in self.actions for c in row['native_checks']]
        return {'actual_check_sketch_calls': self.sketch_calls,
            'closed_helper_ids': sorted(self.closed_helpers),
            'formally_checked_helper_ids': sorted(self.formal_helpers_checked),
            'conditional_plus_formal_check_completed': self.complete,
            'tool_counts': dict(Counter(row['tool'] for row in self.actions)),
            'action_counts': dict(Counter(row['action'] for row in self.actions)),
            'tool_seconds': sum(row['elapsed_seconds'] for row in self.actions),
            'reported_native_wall_ms': sum(c.get('execution_wall_ms', c.get('wall_ms', 0))
                                           for c in checks),
            'timing_scope': 'Unique native check results per executing proof_action task ID; '
                            'read-back and cached receipts excluded. Native observations/retrieval '
                            'and unreported setup excluded. Tool wall includes setup and must not '
                            'be added to native wall.',
            'actions': self.actions}


def diagnostic_prompt(overview, strategy, run, target):
    from regen.planning.parallel_file_planner import DOCUMENT_INSTRUCTIONS
    from regen.planning.document_interface import observation_instructions, proof_outcome_instructions
    return (DOCUMENT_INSTRUCTIONS + '\n' + strategy['instructions'] + '\n'
        + observation_instructions(run=run) + '\n' + proof_outcome_instructions(run)
        + '\nASSIGNED WORKSPACE:\n' + json.dumps(overview, ensure_ascii=False)
        + '\nFOCUS FIXED CONTRACT ID: ' + target + '\n' + DIAGNOSTIC_INSTRUCTIONS)


def run_case(proof, run, key, target, output, settings, *, session_seconds,
             check_timeout, candidate_timeout, codex_home=None):
    from regen.execution.check_executor import CheckExecutor, NativeResources
    from regen.planning.parallel_file_planner import TheoryController, tools
    from regen.planning.generation_policy import generation_strategy
    from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession, SessionCancelled
    from regen.infrastructure.private_auth_home import clean_codex_home
    from regen.infrastructure.progress_events import event_scope
    from regen.infrastructure.workspace_transcript import audit_workspace_session
    from regen.execution.check_tasks import CheckCancelled, NativePreparationError
    from regen.infrastructure.resource_budget import ResourceBudgetError
    directory = output / ('case-' + key)
    directory.mkdir()
    durable(directory / 'resource-admission.json', resource_gate(output), immutable=True)
    resources = NativeResources(1, 4, 1, cpus=4, memory_mb=16384,
        provider_build_timeout=check_timeout, build_ml_heap_mb=8192)
    executor = controller = None
    trace = DiagnosticTrace(target)
    result = {'target': key, 'contract_id': target, 'model': MODEL, 'effort': EFFORT,
              'strategy': STRATEGY, 'status': 'starting', 'provider_turns': []}
    cancel = threading.Event()
    timer = session = model_started = None
    start = time.monotonic()
    try:
        executor = CheckExecutor(proof / 'check-tasks', workers=1)
        controller = TheoryController(proof, 'Sketch_' + key, resources=resources,
            executor=executor, document_mode=True, generation_strategy_name=STRATEGY,
            model=MODEL, call_timeout=session_seconds, check_timeout=check_timeout,
            candidate_timeout=candidate_timeout, focus_contract_id=target)
        strategy = generation_strategy(STRATEGY)
        overview = controller.handle('list_workspace', {'assigned_only': True}, {})
        prompt = diagnostic_prompt(overview, strategy, run, target)
        durable(directory / 'policy.json', {'model': MODEL, 'reasoning_effort': EFFORT,
            'generation_strategy': strategy, 'forced_sketch_diagnostic': True,
            'session_seconds': session_seconds, 'check_timeout': check_timeout,
            'candidate_timeout': candidate_timeout, 'resources': resources.policy,
            'historical_candidates_supplied': False, 'expected_outcomes_supplied': False,
            'stop_condition': 'conditional closure plus one actually executed formal helper check'},
            immutable=True)
        def handle(name, arguments, metadata):
            resource_gate(output, active=True)
            if arguments.get('theory', controller.theory) != controller.theory:
                raise ValueError('this diagnostic only exposes the assigned theory')
            action = arguments.get('proposal', {}).get('action')
            if action in {'finish', 'report_unresolved', 'check_refutation', 'switch_target'}:
                raise ValueError('this diagnostic ends at sketch/helper evidence, not a terminal contract outcome')
            if action == 'check_theory' and not trace.closed_helpers:
                raise ValueError('first design and close the conditional sketch; formal helper proofs follow')
            started = time.monotonic()
            try:
                value = controller.handle(name, arguments, metadata)
            except Exception as error:
                trace.actions.append({'tool': name, 'action': action or name,
                    'elapsed_seconds': time.monotonic() - started,
                    'error_type': type(error).__name__, 'diagnostic': str(error),
                    'native_checks': []})
                durable(directory / 'diagnostic-trace.json', trace.summary())
                raise
            trace.observe(name, arguments, value, time.monotonic() - started)
            durable(directory / 'diagnostic-trace.json', trace.summary())
            return value
        with event_scope(directory, run_id=run['run_id'], theory=controller.theory), \
                clean_codex_home(codex_home, prefix='regen-sketch-diagnostic-',
                                forbidden_roots=(proof, output)) as auth:
            with CodexWorkspaceSession(tools=tools(True), codex_bin=settings['codex_bin'],
                    codex_home=auth.home, allow_external_call=True, progress_timeout=0,
                    feedback_retry_mode=True) as session:
                auth.require_stopped(lambda: session._closed is True)
                model_started = time.monotonic()
                deadline = model_started + session_seconds
                timer = threading.Timer(session_seconds, cancel.set)
                timer.daemon = True
                timer.start()
                while not trace.complete:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        result['status'] = 'budget_expired_pending'
                        break
                    provider = session.run(prompt, handle, artifact_dir=directory,
                        model=MODEL, reasoning_effort=EFFORT, timeout=remaining,
                        cancel_event=cancel, on_abort=controller.abort_pending,
                        stop_when=lambda value: 'diagnostic_evidence_collected' if trace.complete
                            else 'framework_blocked' if isinstance(value, dict)
                            and value.get('framework_blocked') else None)
                    result['provider_turns'].append(provider)
                    result['token_usage'] = provider.get('usage')  # cumulative thread total; never sum turns
                    if provider.get('stop_reason') == 'framework_blocked':
                        result['status'] = 'framework_blocked_pending'
                        break
                    if trace.complete:
                        result['status'] = 'conditional_closed_and_formal_helper_checked'
                        break
                    prompt = ('Continue the same forced-sketch diagnostic from the saved draft and '
                        'actual checker feedback. ' + ('No successful conditional sketch yet: repair '
                        'the precise helper declaration/consumer failure and call check_sketch. '
                        if not trace.closed_helpers else 'The sketch closed: complete the normal v2 '
                        'review and make an ordinary formal helper check. ') +
                        'A final response alone does not complete this diagnostic. Do not seal.')
                result['model_session_wall_seconds'] = time.monotonic() - model_started
            transcript = directory / 'session-transcript.jsonl'
            if transcript.exists():
                result['transcript_audit'] = audit_workspace_session(transcript,
                    require_terminal=False, require_real=True)
    except (TimeoutError, CheckCancelled, SessionCancelled) as error:
        result.update(status='budget_expired_pending' if cancel.is_set() else 'interrupted_pending',
                      error_type=type(error).__name__, diagnostic=str(error))
    except (NativePreparationError, ResourceBudgetError) as error:
        result.update(status='infrastructure_blocked_pending', error_type=type(error).__name__,
                      diagnostic=str(error))
    except Exception as error:
        result.update(status='failed_pending', error_type=type(error).__name__, diagnostic=str(error))
    finally:
        if timer is not None:
            timer.cancel()
        if model_started is not None:
            result['model_session_wall_seconds'] = time.monotonic() - model_started
        if session is not None:
            result['token_usage'] = deepcopy(session._usage)
        transcript = directory / 'session-transcript.jsonl'
        if transcript.exists() and 'transcript_audit' not in result:
            try:
                result['transcript_audit'] = audit_workspace_session(transcript,
                    require_terminal=False, require_real=True)
            except Exception as error:
                result['transcript_audit_error'] = {
                    'error_type': type(error).__name__, 'diagnostic': str(error)}
        errors = []
        for resource in (controller, executor, resources):
            if resource is not None:
                try:
                    resource.close()
                except Exception as error:
                    errors.append({'error_type': type(error).__name__, 'diagnostic': str(error)})
        if errors:
            result.update(status='cleanup_failed', cleanup_errors=errors)
        result.update(wall_seconds=time.monotonic() - start, diagnostic_trace=trace.summary(),
                      native_resource_metrics=resources.metrics)
        durable(directory / 'result.json', result, immutable=True)
    return result


def run(dataset, output, *, session_seconds=1200, check_timeout=120,
        candidate_timeout=60, allow_external_call=False, codex_home=None):
    if not allow_external_call:
        raise PermissionError('real-model diagnostic requires --allow-external-call')
    for value, limit in [(session_seconds, 1200), (check_timeout, 120), (candidate_timeout, 60)]:
        if type(value) is not int or not 1 <= value <= limit:
            raise ValueError('diagnostic timeouts exceed their fixed short-test bound')
    dataset, output = Path(dataset).resolve(), Path(output).resolve()
    if output.exists() or output.is_symlink():
        raise ValueError('fresh independent output required; diagnostic cannot resume an old run')
    if output == dataset or output.is_relative_to(dataset) or dataset.is_relative_to(output):
        raise ValueError('output must be independent of prepared input')
    # Resource admission precedes initialization; no lowered disk/heap reserve.
    resource_gate(output.parent)
    output.mkdir(parents=True)
    from regen.execution.proof_session_runner import acquire_run_mutation_lock, release_run_mutation_lock
    from regen.infrastructure.codex_runtime import runtime_verified_settings
    from regen.infrastructure.progress_events import configure_event_monitor, close_event_monitor
    proof = output / 'proof'
    token = acquire_run_mutation_lock(proof, operation='real-sketch-diagnostic')
    report = {'kind': 'forced-real-model-sketch-diagnostic', 'dataset': str(dataset),
              'not_benchmark_score': True, 'manual_allocation': True, 'cases': []}
    try:
        with frozen_models(output) as config:
            settings = runtime_verified_settings(config)
            proof, run_config, selected = initialize(output, dataset)
            report['run_id'] = run_config['run_id']
            report['checker_context_id'] = run_config['checker_context_id']
            report['driver_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            configure_event_monitor(proof, run_id=run_config['run_id'])
            for key, target in selected.items():
                result = run_case(proof, run_config, key, target, output, settings,
                    session_seconds=session_seconds, check_timeout=check_timeout,
                    candidate_timeout=candidate_timeout, codex_home=codex_home)
                report['cases'].append(result)
                durable(output / 'report.json', report)
                if result['status'] in {'cleanup_failed', 'infrastructure_blocked_pending',
                                         'framework_blocked_pending', 'failed_pending'}:
                    break
            return report
    except BaseException as error:
        durable(output / 'failure.json', {'error_type': type(error).__name__,
                'diagnostic': str(error)}, immutable=True)
        raise
    finally:
        try:
            close_event_monitor('diagnostic-finished')
        finally:
            release_run_mutation_lock(token)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--session-seconds', type=int, default=1200)
    parser.add_argument('--check-timeout', type=int, default=120)
    parser.add_argument('--candidate-timeout', type=int, default=60)
    parser.add_argument('--codex-home', type=Path)
    parser.add_argument('--allow-external-call', action='store_true')
    args = parser.parse_args()
    result = run(**vars(args))
    print(json.dumps({'cases': [{'target': c['target'], 'status': c['status']}
                               for c in result['cases']]}, ensure_ascii=False))
    return 0 if len(result['cases']) == 2 and all(c['status'] ==
        'conditional_closed_and_formal_helper_checked' for c in result['cases']) else 2


if __name__ == '__main__':
    raise SystemExit(main())
