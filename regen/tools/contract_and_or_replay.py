"""Zero-model replay for two contract-rooted AND/OR case studies.

Only checker verdicts, node statuses and timing metadata are emitted.  The
deleteObjects overlay is reconstructed from accepted proof references; the
Scheduler overlay mixes one such edge with explicitly labelled semantic
hypotheses.  Neither overlay is supplied to a proof-generating model.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from regen.infrastructure.incremental_log import read_artifact
from regen.planning.contract_and_or import (
    SCHEMA, apply_node_results, initial_state, next_decision,
)


REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE_RUN = Path('reports/regen/refine-astra-medium-20261005-006/proof')
RUN = SOURCE_RUN / 'theories'
DELETE_ROOT = 'contract:Refine.Detype_R:deleteObjects_corres:5ee4763a2f70'
SCHEDULE_ROOT = 'contract:Refine.Schedule_R:schedule_corres:21d69356e61c'

# A batch checker result is not acceptance authority.  These explicit
# historical bindings connect a checker transcript to the append-only
# acceptance journal.  Keep this table deliberately closed: an ``accepted``
# timing status without an entry here remains only ``audited_green``.
ACCEPTANCE_COMMIT_BY_REQUEST = {
    ('Untyped_Object_Lifecycle', 67): 55,
    ('Scheduler_Dispatch', 68): 50,
    ('Scheduler_Dispatch', 76): 69,
    ('Scheduler_Dispatch', 85): 89,
    ('Scheduler_Dispatch', 90): 99,
    ('Scheduler_Dispatch', 94): 108,
}
SOURCE_RUN_BINDING_FILES = (
    'run.json',
    'execution-input.json',
    'experiment.json',
    'log-storage.json',
)


def _goal(key, order, routes, *, role='helper'):
    return {'id': key, 'order': order, 'role': role, 'routes': routes}


def _route(key, requires=(), estimate=30.0, *, recheck=12.0,
           failure_penalty=None, probe=12.0,
           probe_policy='all_requirements_certified', attempt_limit=None):
    value = {'id': key, 'requires': list(requires),
             'estimate_seconds': float(estimate),
             'recheck_seconds': float(recheck),
             'failure_penalty_seconds': float(
                 max(estimate, 1.0) if failure_penalty is None else failure_penalty)}
    if requires:
        value['probe_seconds'] = float(probe)
        value['probe_policy'] = probe_policy
    if attempt_limit is not None:
        value['attempt_limit'] = attempt_limit
    return value


def delete_objects_problem():
    aligned = 'helper:ul_aligned_range_card'
    cut = 'helper:ul_arch_cut_bits'
    bridge = 'helper:ul_arch_range_bits'
    return {
        'schema_version': 1,
        'method': SCHEMA,
        'root_goal': DELETE_ROOT,
        'oracle_scope': 'posthoc-accepted-proof-references',
        'cost_basis': ('manual mechanism-test heuristic; '
                       'not a historical wall-time measurement'),
        'goals': [
            _goal(aligned, 10, [_route('delete/aligned', estimate=30)]),
            _goal(cut, 20, [_route('delete/cut', estimate=35)]),
            _goal(bridge, 30, [_route('delete/bridge', (aligned, cut), estimate=55)]),
            _goal(DELETE_ROOT, 40, [
                _route('delete/root-direct', estimate=100, attempt_limit=1),
                _route('delete/root-architecture', (bridge,), estimate=85),
            ], role='contract'),
        ],
    }


RESUME_HELPERS = [
    'helper:dispatch_state_gets_commute',
    'helper:dispatch_runnable_bind',
]
RESUME = 'helper:dispatch_resume'
INVARIANT_HELPERS = [
    'helper:dispatch_tcb_clear_update',
    'helper:dispatch_tcb_type_update',
    'helper:dispatch_tcb_at_update',
    'helper:dispatch_tcb_ko_wp_update',
    'helper:dispatch_tcb_cap_update',
    'helper:dispatch_tcb_obj_frame',
    'helper:dispatch_tcb_objs_update',
    'helper:dispatch_tcb_layout_update',
    'helper:dispatch_sched_tcb_slots',
    'helper:dispatch_sched_tcb_valid',
    'helper:dispatch_sched_ctes',
    'helper:dispatch_sched_load_cte',
    'helper:dispatch_cte_wp_load',
    'helper:dispatch_lookup_replace',
    'helper:dispatch_lookup_member',
    'helper:dispatch_sched_cte_wp',
    'helper:dispatch_sched_cap_invariants',
    'helper:dispatch_sched_refs',
    'helper:dispatch_sched_liveness',
    'helper:dispatch_tcb_obj_at_update',
    'helper:dispatch_sched_thread_invariants',
]
ENQUEUE = 'helper:dispatch_enqueue_invs'
DEEP_FRONTIER = [
    'helper:dispatch_tcb_memory_frame',
    'helper:dispatch_getObject_tcb_result',
    'helper:dispatch_threadSet_wp',
]


def schedule_problem():
    goals = []
    order = 10
    for key in RESUME_HELPERS:
        goals.append(_goal(key, order, [_route('schedule/' + key.removeprefix('helper:'),
                                                     estimate=20 + order / 10)]))
        order += 10
    goals.append(_goal(RESUME, order, [_route('schedule/resume', RESUME_HELPERS, estimate=35)]))
    order += 10
    for key in INVARIANT_HELPERS + DEEP_FRONTIER:
        goals.append(_goal(key, order, [_route('schedule/' + key.removeprefix('helper:'),
                                                     estimate=40 + order / 20)]))
        order += 10
    goals.append(_goal(ENQUEUE, order, [_route(
        'schedule/enqueue', INVARIANT_HELPERS + DEEP_FRONTIER, estimate=120)]))
    order += 10
    goals.append(_goal(SCHEDULE_ROOT, order, [
        _route('schedule/root-direct', estimate=80, attempt_limit=1),
        _route('schedule/root-actions', (RESUME, ENQUEUE), estimate=150,
               probe_policy='each_accepted_child'),
    ], role='contract'))
    return {
        'schema_version': 1,
        'method': SCHEMA,
        'root_goal': SCHEDULE_ROOT,
        'edge_basis': 'mixed-accepted-reference-and-semantic-hypothesis',
        'cost_basis': ('manual mechanism-test heuristic; '
                       'not a historical wall-time measurement'),
        'goals': goals,
    }


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_run_binding(repository):
    repository = Path(repository)
    artifacts = []
    for name in SOURCE_RUN_BINDING_FILES:
        path = repository / SOURCE_RUN / name
        if not path.is_file():
            raise ValueError(f'missing source-run binding artifact: {path}')
        artifacts.append({
            'path': str(path.relative_to(repository)),
            'raw_sha256': _sha256(path),
        })
    return {
        'run_path': str(SOURCE_RUN),
        'artifacts': artifacts,
    }


def _request_dir(repository, theory, number):
    return Path(repository) / RUN / theory / 'batch-requests' / f'{number:06d}'


def _acceptance_journal_binding(repository, theory, number):
    sequence = ACCEPTANCE_COMMIT_BY_REQUEST.get((theory, number))
    if sequence is None:
        return None
    path = (Path(repository) / RUN / theory / 'acceptance' / 'commits'
            / f'{sequence:06d}.json')
    if not path.is_file():
        raise ValueError(f'missing acceptance journal commit: {path}')
    commit = read_artifact(path)
    evidence = commit.get('evidence', {})
    node_results = evidence.get('node_results')
    proofs = commit.get('state', {}).get('proofs')
    transcript_prefix = evidence.get('transcript_prefix')
    expected_suffix = f'/batch-requests/{number:06d}/call'
    errors = []
    accepted_node_ids = []
    if not isinstance(node_results, list):
        errors.append('evidence.node_results is not a list')
    else:
        for index, item in enumerate(node_results):
            if not isinstance(item, dict):
                errors.append(f'evidence.node_results[{index}] is not an object')
                continue
            if item.get('status') != 'audited_green':
                continue
            node_id = item.get('node_id')
            if not isinstance(node_id, str) or not node_id:
                errors.append(f'evidence.node_results[{index}] has invalid node_id')
                continue
            if node_id in accepted_node_ids:
                errors.append(f'duplicate audited_green node_id: {node_id}')
                continue
            accepted_node_ids.append(node_id)
    if not accepted_node_ids:
        errors.append('evidence.node_results has no audited_green nodes')
    if not isinstance(proofs, dict):
        errors.append('state.proofs is not an object')
    else:
        missing_proofs = [node_id for node_id in accepted_node_ids
                          if node_id not in proofs]
        if missing_proofs:
            errors.append('audited_green nodes missing from state.proofs: '
                          + ', '.join(missing_proofs))
    if commit.get('event') != 'document':
        errors.append('event is not document')
    if commit.get('sequence') != sequence:
        errors.append(f'sequence is not {sequence}')
    if evidence.get('check', {}).get('verdict') != 'green':
        errors.append('evidence.check.verdict is not green')
    if not isinstance(transcript_prefix, str) or not transcript_prefix.endswith(expected_suffix):
        errors.append(f'evidence.transcript_prefix does not end with {expected_suffix}')
    commit_id = commit.get('commit_id')
    if not isinstance(commit_id, str) or not re.fullmatch(r'[0-9a-f]{64}', commit_id):
        errors.append('commit_id is not a SHA-256 identity')
    if errors:
        raise ValueError(f'invalid acceptance journal binding {path}: ' + '; '.join(errors))
    return {
        'commit_path': str(path.relative_to(repository)),
        'raw_manifest_sha256': _sha256(path),
        'sequence': sequence,
        'commit_id': commit_id,
        'accepted_node_ids': accepted_node_ids,
    }


def request_evidence(repository, theory, number):
    directory = _request_dir(repository, theory, number)
    candidate_path = directory / 'candidate.json'
    check_path, timing_path = directory / 'check.json', directory / 'timing.json'
    if not candidate_path.is_file():
        raise ValueError(f'missing request candidate artifact: {candidate_path}')
    if not timing_path.is_file():
        raise ValueError(f'missing request timing artifact: {timing_path}')
    timing = read_artifact(timing_path)
    if check_path.is_file():
        check = read_artifact(check_path)
    elif timing.get('exception_type') == 'CheckCancelled':
        check = {}
    else:
        raise ValueError(f'missing request check artifact: {check_path}')
    relative = directory.relative_to(repository)
    return {
        'theory': theory,
        'request_number': number,
        'request_path': str(relative),
        'candidate_manifest_sha256': _sha256(candidate_path),
        'check_manifest_sha256': _sha256(check_path) if check_path.is_file() else None,
        'timing_manifest_sha256': _sha256(timing_path),
        'check_verdict': check.get('check', {}).get('verdict'),
        'request_status': timing.get('status'),
        'exception_type': timing.get('exception_type'),
        'provider_called': any(call.get('provider_called', False)
                               for call in timing.get('calls', [])),
        'total_wall_seconds': timing.get('total_wall_seconds'),
        'acceptance_journal': _acceptance_journal_binding(
            repository, theory, number),
        'node_results': [
            {'node_id': item.get('node_id'), 'status': item.get('status')}
            for item in check.get('node_results', [])
        ],
        '_check': check,
    }


def _public(evidence):
    return {key: value for key, value in evidence.items() if not key.startswith('_')}


def _apply(problem, state, evidence, route_by_goal, route_overrides=None):
    accepted_request = (evidence['request_status'] == 'accepted'
                        and evidence['acceptance_journal'] is not None)
    accepted_node_ids = set(evidence['acceptance_journal']['accepted_node_ids']) \
        if accepted_request else set()
    overrides = route_overrides or {}
    results = []
    for item in evidence['node_results']:
        key, status = item['node_id'], item['status']
        route = overrides.get(key, route_by_goal.get(key))
        if route is None:
            continue
        if status == 'audited_green' and key in accepted_node_ids:
            status = 'accepted_green'
        results.append({'goal_id': key, 'route_id': route, 'status': status})
    return apply_node_results(problem, state, results, evidence={
        'request_path': evidence['request_path'],
        'check_manifest_sha256': evidence['check_manifest_sha256'],
        'timing_manifest_sha256': evidence['timing_manifest_sha256'],
        'acceptance_journal': evidence['acceptance_journal'],
    })


def _decision_record(number, decision):
    return {'after_request': number, 'decision': decision}


def replay_delete_objects(repository=REPOSITORY):
    problem = delete_objects_problem()
    routes = {
        'helper:ul_aligned_range_card': 'delete/aligned',
        'helper:ul_arch_cut_bits': 'delete/cut',
        'helper:ul_arch_range_bits': 'delete/bridge',
        DELETE_ROOT: 'delete/root-architecture',
    }
    state = initial_state(problem)
    evidence, decisions = [], []
    snapshots = {}
    for number in range(61, 68):
        item = request_evidence(repository, 'Untyped_Object_Lifecycle', number)
        overrides = {DELETE_ROOT: 'delete/root-direct'} if number == 61 else None
        state = _apply(problem, state, item, routes, overrides)
        decisions.append(_decision_record(number, next_decision(problem, state)))
        evidence.append(_public(item))
        snapshots[number] = deepcopy(state)

    # What bounded-prefix acceptance of the first completed helper would have
    # required.  This is a counterfactual control decision, not historical proof
    # evidence and therefore never enters the certified result above.
    bounded = apply_node_results(problem, snapshots[63], [{
        'goal_id': 'helper:ul_aligned_range_card',
        'route_id': 'delete/aligned',
        'status': 'accepted_green',
    }], evidence={'kind': 'counterfactual-bounded-prefix-recheck'})
    completed_counts, repeated = {}, 0
    for item in evidence:
        for node in item['node_results']:
            if node['status'] not in {'completed_uncommitted', 'audited_green'}:
                continue
            key = node['node_id']
            if key in routes:
                repeated += int(completed_counts.get(key, 0) > 0)
                completed_counts[key] = completed_counts.get(key, 0) + 1
    return {
        'case': 'deleteObjects_corres',
        'classification': 'closed complex contract / AND-decomposition case',
        'posthoc_dependency_oracle': True,
        'edge_basis': {
            'kind': 'posthoc_evaluator_reconstruction',
            'journal_attests_edges': False,
            'notice': ('The journal attests accepted nodes, not these manually '
                       'reconstructed proof-body reference edges.'),
            'accepted_reference_posthoc': [
                {
                    'route_id': 'delete/bridge',
                    'requires': ['helper:ul_aligned_range_card',
                                 'helper:ul_arch_cut_bits'],
                },
                {
                    'route_id': 'delete/root-architecture',
                    'requires': ['helper:ul_arch_range_bits'],
                },
            ],
            'semantic_hypothesis': [],
        },
        'model_calls': 0,
        'problem': problem,
        'evidence': evidence,
        'policy_decisions': decisions,
        'counterfactual_after_first_prefix_acceptance': next_decision(problem, bounded),
        'measurements': {
            'request_wall_seconds_61_67': sum(item['total_wall_seconds'] or 0 for item in evidence),
            'repeated_completed_node_executions': repeated,
            'measurement_notice': ('The request wall sum mixes model, checking and waiting. '
                                   'It is not effective proving time; repeated executions '
                                   'are not converted into a saving claim.'),
        },
        'final_decision': next_decision(problem, state),
    }


def _subgoals(evidence):
    diagnostic = evidence.get('_check', {}).get('check', {}).get('diagnostic', '')
    match = re.search(r'goal \((\d+) subgoals\)', diagnostic)
    return int(match.group(1)) if match else None


def replay_schedule(repository=REPOSITORY):
    problem = schedule_problem()
    route_by_goal = {goal['id']: goal['routes'][0]['id'] for goal in problem['goals']}
    route_by_goal[SCHEDULE_ROOT] = 'schedule/root-actions'
    state = initial_state(problem)
    request66 = request_evidence(repository, 'Scheduler_Dispatch', 66)
    state = _apply(problem, state, request66, route_by_goal,
                   {SCHEDULE_ROOT: 'schedule/root-direct'})
    after66 = next_decision(problem, state)
    request68 = request_evidence(repository, 'Scheduler_Dispatch', 68)
    state = _apply(problem, state, request68, route_by_goal)
    after68 = next_decision(problem, state)

    accepted_batches, accepted_helpers = [], []
    journal_bound_batches = []
    root_rechecks = []
    scanned = []
    for number in range(67, 98):
        item = request_evidence(repository, 'Scheduler_Dispatch', number)
        scanned.append(item)
        if any(node['node_id'] == SCHEDULE_ROOT for node in item['node_results']):
            root_rechecks.append(number)
        if (item['request_status'] == 'accepted'
                and item['acceptance_journal'] is not None):
            accepted_node_ids = set(
                item['acceptance_journal']['accepted_node_ids'])
            helpers = [node['node_id'] for node in item['node_results']
                       if node['status'] == 'audited_green'
                       and node['node_id'] in accepted_node_ids
                       and node['node_id'].startswith('helper:')]
            if helpers:
                accepted_batches.append(number)
                accepted_helpers.extend(helpers)
                journal_bound_batches.append({
                    'request_number': number,
                    'acceptance_journal': item['acceptance_journal'],
                })
    request95 = next(item for item in scanned if item['request_number'] == 95)
    request96 = next(item for item in scanned if item['request_number'] == 96)
    request97 = next(item for item in scanned if item['request_number'] == 97)
    return {
        'case': 'schedule_corres',
        'classification': 'open complex contract / helper-drift case',
        'posthoc_dependency_oracle': False,
        'edge_basis': {
            'kind': 'mixed',
            'accepted_reference_posthoc': [
                {
                    'route_id': 'schedule/resume',
                    'requires': list(RESUME_HELPERS),
                },
            ],
            'semantic_hypothesis': [
                {
                    'route_id': 'schedule/enqueue',
                    'requires': list(INVARIANT_HELPERS + DEEP_FRONTIER),
                },
                {
                    'route_id': 'schedule/root-actions',
                    'requires': [RESUME, ENQUEUE],
                },
            ],
            'notice': ('Semantic-hypothesis edges guide search but are not '
                       'accepted-reference dependency evidence.'),
        },
        'model_calls': 0,
        'problem': problem,
        'evidence': [_public(request66), _public(request68),
                     _public(request95), _public(request96), _public(request97)],
        'policy_decisions': [
            _decision_record(66, after66),
            _decision_record(68, after68),
        ],
        'root_direct_attempt': {
            'request_number': 66,
            'residual_count': _subgoals(request66),
        },
        'historical_drift_audit': {
            'accepted_helper_batch_requests': accepted_batches,
            'accepted_helper_count': len(accepted_helpers),
            'journal_bound_helper_batches': journal_bound_batches,
            'root_recheck_requests_67_97': root_rechecks,
            'controller_would_probe_after_request': 68,
        },
        'deep_frontier': {
            'goal_id': 'helper:dispatch_tcb_memory_frame',
            'request_95_residual_count': _subgoals(request95),
            'request_96_residual_count': _subgoals(request96),
            'request_97_outcome': request97['exception_type'],
            'notice': ('CheckCancelled is infrastructure control flow and does not update '
                       'the mathematical search state.'),
        },
        'final_historical_status': 'open',
    }


def report(repository=REPOSITORY, case='all'):
    run_binding = source_run_binding(repository)
    cases = []
    if case in {'all', 'deleteObjects'}:
        cases.append(replay_delete_objects(repository))
    if case in {'all', 'schedule'}:
        cases.append(replay_schedule(repository))
    return {
        'schema_version': 1,
        'method': SCHEMA,
        'mode': 'zero-model-retrospective-replay',
        'edge_basis': 'mixed',
        'source_run_binding': run_binding,
        'authority_notice': ('Search decisions are hypotheses. Only journal-accepted native '
                             'GREEN declarations are certified.'),
        'cases': cases,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', type=Path, default=REPOSITORY)
    parser.add_argument('--case', choices=('all', 'deleteObjects', 'schedule'), default='all')
    args = parser.parse_args(argv)
    print(json.dumps(report(args.repository.resolve(), args.case), indent=2,
                     sort_keys=True, ensure_ascii=False))


if __name__ == '__main__':
    main()
