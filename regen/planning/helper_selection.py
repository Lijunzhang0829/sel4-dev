"""Mandatory execution frontier over model-proposed local search hypotheses.

No proof authority, premise retrieval, generated mathematics or budget changes.
Only the caller's validated journal state may certify an unchanged draft prefix.
The saved search graph is rebuildable advice, never an acceptance journal.
"""
from copy import deepcopy
import json
import math

from regen.execution.proof_commits import digest, durable
from regen.planning.contract_and_or import PlanFrontierLimitExceeded, initial_state, next_decision, validate_problem
from regen.planning.theory_document import parse

STRATEGY = 'and-or-helper-selection-v1'


def declarations(store):
    current = store.read()
    entries, proofs = parse(current['source'], store.package, store.imports)
    return current, entries, proofs


def validate_selection(problem, package, entries):
    if not isinstance(problem, dict) or len(json.dumps(problem)) > 100000:
        raise ValueError('bounded AND/OR problem object required')
    if set(problem) - {'schema_version', 'method', 'root_goal', 'goals', 'max_frontier_plans'}:
        raise ValueError('problem must not supply certified state or observations')
    goals = problem.get('goals')
    if not isinstance(goals, list) or not 1 <= len(goals) <= 128:
        raise ValueError('AND/OR selection requires 1..128 local goals')
    limit = problem.get('max_frontier_plans', 512)
    if type(limit) is not int or not 1 <= limit <= 512:
        raise ValueError('AND/OR frontier limit must be at most 512')
    local = {entry['id']: entry for entry in entries}
    contracts = {c['contract_id'] for c in package['contracts']}
    if problem.get('root_goal') not in contracts:
        raise ValueError('root must be a fixed contract in this theory')
    count = 0
    for goal in goals:
        if not isinstance(goal, dict) or set(goal) - {'id', 'role', 'order', 'routes'}:
            raise ValueError('invalid local goal fields')
        if goal.get('id') not in local or goal.get('role') != local[goal['id']]['role']:
            raise ValueError('goal identity/role must match a saved local declaration')
        if not isinstance(goal.get('routes'), list):
            raise ValueError('goal routes must be a list')
        count += len(goal['routes'])
        for route in goal['routes']:
            if not isinstance(route, dict) or set(route) - {'id', 'requires', 'estimate_seconds'}:
                raise ValueError('routes accept id, requires and estimate_seconds only')
            if not isinstance(route.get('id'), str) or not route['id']:
                raise ValueError('nonempty route ID required')
            requires = route.get('requires', [])
            if not isinstance(requires, list) or any(not isinstance(k, str) for k in requires):
                raise ValueError('requires must list local goal IDs')
            cost = route.get('estimate_seconds', 0)
            if type(cost) not in (int, float) or not math.isfinite(cost) or not 0 <= cost <= 172800:
                raise ValueError('finite bounded model cost estimate required')
    if count > 256:
        raise ValueError('at most 256 candidate routes allowed')
    normalized = deepcopy(problem)
    normalized.setdefault('max_frontier_plans', 512)
    validate_problem(normalized)
    return normalized


def route_key(package, entries, goal, route):
    local = {n['id']: n for n in entries}
    ids = sorted({goal['id'], *route.get('requires', [])})
    return digest({'package': digest(package), 'goal': goal['id'],
                   'requires': sorted(route.get('requires', [])),
                   'declarations': [{k: local[i].get(k) for k in
                                     ('id', 'role', 'statement', 'context')} for i in ids]})


def recommend(problem, package, entries, draft_proofs, state):
    problem = validate_selection(problem, package, entries)
    attempts = {}
    costs = state.get('helper_route_costs', {})
    for goal in problem['goals']:
        for route in goal['routes']:
            history = costs.get(route_key(package, entries, goal, route), {})
            failures = history.get('failures', 0)
            attempts[route['id']] = failures
            if failures:
                # Cumulative observed failed-check cost, with a positive floor
                # for failed checks whose native duration could not be isolated.
                route['failure_penalty_seconds'] = max(1.0, history.get('failed_seconds', 0) / failures)
    accepted = state.get('accepted_order', [])
    old = {n['id']: n for n in state['graph']['nodes']}
    new = {n['id']: n for n in entries}
    order = [n['id'] for n in entries if n['id'] in draft_proofs]
    unchanged = order[:len(accepted)] == accepted and all(
        key in new and key in old and draft_proofs.get(key) == state['proofs'][key]
        and all(new[key].get(f) == old[key].get(f) for f in ('id', 'role', 'statement', 'context'))
        for key in accepted)
    known = {goal['id'] for goal in problem['goals']}
    certified = known & set(state['proofs']) if unchanged else set()
    status = 'available'
    try:
        decision = next_decision(problem, initial_state(problem, certified=certified,
                                                        route_attempts=attempts))
    except PlanFrontierLimitExceeded:
        status = 'frontier_limit_exceeded'
        decision = {'action': 'REVISE_SELECTION', 'root_goal': problem['root_goal'],
                    'notice': 'Exact search exceeded the bound. Simplify candidate routes; no approximate plan was chosen.'}
    return {'policy': STRATEGY, 'status': status, 'advisory_only': True,
            'mandatory_execution': True, 'cost_policy': 'journal-native-failure-cost-v2',
            'decision': decision, 'certified_ids': sorted(certified),
            'accepted_prefix_unchanged': unchanged,
            'authority_sha256': digest({'graph': state['graph'], 'proofs': state['proofs']}),
            'cost_source': 'model_estimate_plus_journaled_failed_native_check_seconds',
            'route_attempts': attempts,
            'notice': 'Routes are hypotheses, not verified dependencies. Prove/check the selected goal '
                      'normally; helpers never certify their parent. No automatic parent probe or budget reset.'}


def install(store, state, proposal, provenance):
    if set(proposal) != {'action', 'source_sha256', 'problem'}:
        raise ValueError('select_helpers requires action, source_sha256 and problem only')
    current, entries, proofs = declarations(store)
    if proposal['source_sha256'] != current['source_sha256']:
        raise ValueError('stale helper selection; read the current draft')
    problem = validate_selection(proposal['problem'], store.package, entries)
    result = recommend(problem, store.package, entries, proofs, state)
    value = {'policy': STRATEGY, 'package_sha256': digest(store.package),
             'declarations_sha256': digest(entries), 'problem': problem,
             'provenance': provenance}
    value['selection_sha256'] = digest(value)
    durable(store.root/'helper-selection.json', value)
    return result


def current_selection(store, state):
    path = store.root/'helper-selection.json'
    if not path.exists():
        return {'policy': STRATEGY, 'status': 'not_configured', 'advisory_only': True}
    value = json.loads(path.read_text())
    signature = value.pop('selection_sha256')
    if digest(value) != signature or value['policy'] != STRATEGY or value['package_sha256'] != digest(store.package):
        raise ValueError('helper selection binding mismatch')
    _, entries, proofs = declarations(store)
    if digest(entries) != value['declarations_sha256']:
        return {'policy': STRATEGY, 'status': 'stale_declarations', 'advisory_only': True,
                'notice': 'Declarations changed; submit a new select_helpers problem. No old advice reused.'}
    return recommend(value['problem'], store.package, entries, proofs, state)


def require_attempt(store, state, proposal):
    """Bind one mandatory frontier attempt before any native check is dispatched."""
    advice = current_selection(store, state)
    decision = advice.get('decision', {})
    if advice['status'] != 'available' or decision.get('action') != 'PROVE':
        return None, advice
    current, entries, proofs = declarations(store)
    if current['source_sha256'] != proposal.get('source_sha256'):
        raise ValueError('stale document; read_theory before checking')
    goal_id = decision['goal_id']
    if not advice['accepted_prefix_unchanged'] or set(proofs) - set(state['proofs']) != {goal_id}:
        return None, {**advice, 'status': 'frontier_edit_required',
            'notice': 'Preserve the accepted prefix. Supply only the selected goal proof; '
                      'leave other unaccepted entries as oops, or revise select_helpers routes.'}
    value = json.loads((store.root/'helper-selection.json').read_text())
    goal = next(g for g in value['problem']['goals'] if g['id'] == goal_id)
    route = next(r for r in goal['routes'] if r['id'] == decision['route_id'])
    return {'key': route_key(store.package, entries, goal, route), 'goal_id': goal_id,
            'route_id': route['id'], 'source_sha256': current['source_sha256']}, advice


def record_attempt(state, feedback, number):
    """Update costs in the SAME journal transaction as acceptance/feedback.

    No separate mutable counter: crash/retry/recovery cannot double-charge an
    already recorded request. Unknown and infrastructure outcomes are not RED.
    """
    attempt = state.get('helper_route_attempt')
    if not attempt or attempt.get('request_number') != number:
        return
    costs = state.setdefault('helper_route_costs', {})
    history = costs.setdefault(attempt['key'], {'failures': 0, 'failed_seconds': 0.0})
    if history.get('last_request') == number:
        return
    history['last_request'] = number
    check = feedback.get('check', {})
    failed = any(row.get('node_id') == attempt['goal_id'] and row.get('status') == 'failed'
                 for row in feedback.get('node_results', []))
    if (failed and not check.get('failure_scope') and not feedback.get('cached_failure')
            and feedback.get('status') == 'rejected_batch'):
        history['failures'] += 1
        elapsed = check.get('candidate_execution_ms')
        if type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0:
            history['failed_seconds'] += elapsed / 1000
        else:
            history['unattributed_failures'] = history.get('unattributed_failures', 0) + 1
    history['last_status'] = feedback.get('status')
