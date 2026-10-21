"""A contract-rooted AND/OR control layer for document proof generation.

This module deliberately does not alter the document declaration index.  Its
edges are search hypotheses: journal acceptance certifies goals, while a
parent probe is needed to show that a newly accepted helper actually changes
the selected root route.  Isabelle remains the only proof oracle.

The implementation is intentionally pure and JSON-shaped so historical traces
can be replayed without starting a model or a native worker.
"""
from copy import deepcopy


SCHEMA = 'contract-rooted-and-or-v1'
DEFAULT_MAX_FRONTIER_PLANS = 4096
PROBE_POLICIES = frozenset({
    'none',
    'all_requirements_certified',
    'each_accepted_child',
})


class PlanFrontierLimitExceeded(RuntimeError):
    """Exact plan enumeration exceeded its configured safety bound."""


NO_MATH_CHANGE = frozenset({
    'cancelled', 'declaration_rejected', 'infrastructure_failure',
    'not_checked', 'not_reached', 'unknown',
})
CANDIDATE = frozenset({'audited_green', 'completed_uncommitted'})


def _index(problem):
    validate_problem(problem)
    goals = {goal['id']: goal for goal in problem['goals']}
    routes = {}
    route_goal = {}
    for goal in problem['goals']:
        for route in goal['routes']:
            routes[route['id']] = route
            route_goal[route['id']] = goal['id']
    return goals, routes, route_goal


def validate_problem(problem):
    """Reject ambiguous graphs before they can influence scheduling."""
    if not isinstance(problem, dict) or problem.get('schema_version') != 1:
        raise ValueError('AND/OR problem requires schema_version 1')
    if problem.get('method') != SCHEMA:
        raise ValueError('unknown AND/OR method')
    entries = problem.get('goals')
    if not isinstance(entries, list) or not entries:
        raise ValueError('AND/OR problem requires goals')
    ids = [goal.get('id') for goal in entries if isinstance(goal, dict)]
    if len(ids) != len(entries) or any(not isinstance(key, str) or not key for key in ids):
        raise ValueError('every goal requires a non-empty string id')
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate AND/OR goal id')
    if problem.get('root_goal') not in set(ids):
        raise ValueError('root_goal is missing')
    frontier_limit = problem.get('max_frontier_plans', DEFAULT_MAX_FRONTIER_PLANS)
    if (not isinstance(frontier_limit, int) or isinstance(frontier_limit, bool)
            or frontier_limit < 1):
        raise ValueError('max_frontier_plans must be a positive integer')
    route_ids = set()
    for position, goal in enumerate(entries):
        if not isinstance(goal.get('order', position), int):
            raise ValueError('goal order must be an integer')
        if goal.get('role', 'helper') not in {
                'contract', 'helper', 'theorem', 'residual'}:
            raise ValueError('invalid goal role: ' + goal['id'])
        alternatives = goal.get('routes')
        if not isinstance(alternatives, list) or not alternatives:
            raise ValueError('goal requires at least one OR route: ' + goal['id'])
        for route in alternatives:
            if not isinstance(route, dict) or not isinstance(route.get('id'), str):
                raise ValueError('route requires a string id')
            if route['id'] in route_ids:
                raise ValueError('route ids must be globally unique: ' + route['id'])
            route_ids.add(route['id'])
            requires = route.get('requires', [])
            if (not isinstance(requires, list) or len(set(requires)) != len(requires)
                    or any(key not in set(ids) for key in requires)):
                raise ValueError('invalid AND requirements: ' + route['id'])
            defaults = {
                'estimate_seconds': 0.0,
                'recheck_seconds': route.get('estimate_seconds', 0.0),
                'failure_penalty_seconds': 1.0,
                'probe_seconds': 0.0,
            }
            for field, default in defaults.items():
                value = route.get(field, default)
                if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                    raise ValueError(field + ' must be non-negative: ' + route['id'])
                if field == 'failure_penalty_seconds' and value == 0:
                    raise ValueError(field + ' must be positive: ' + route['id'])
            limit = route.get('attempt_limit')
            if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
                raise ValueError('attempt_limit must be a positive integer')
            policy = route.get('probe_policy', 'all_requirements_certified')
            if policy not in PROBE_POLICIES:
                raise ValueError('invalid probe_policy: ' + route['id'])
    # Resolve every goal once.  This detects cycles even if a cheaper acyclic
    # route happens to exist and would otherwise hide malformed input.
    children = {goal['id']: [child for route in goal['routes']
                             for child in route.get('requires', [])]
                for goal in entries}
    visiting, visited = set(), set()
    def walk(key):
        if key in visiting:
            raise ValueError('cyclic AND/OR problem at ' + key)
        if key in visited:
            return
        visiting.add(key)
        for child in children[key]:
            walk(child)
        visiting.remove(key); visited.add(key)
    walk(problem['root_goal'])
    for key in ids:
        walk(key)


def initial_state(problem, *, certified=(), route_attempts=None):
    goals, routes, _ = _index(problem)
    accepted = set(certified)
    if not accepted <= set(goals):
        raise ValueError('certified goal is absent from problem')
    attempts = dict(route_attempts or {})
    if any(key not in routes or not isinstance(value, int) or value < 0
           for key, value in attempts.items()):
        raise ValueError('invalid route attempt state')
    return {
        'schema_version': 1,
        'method': SCHEMA,
        'certified': sorted(accepted),
        'candidate_routes': {},
        'route_attempts': attempts,
        'pending_parent_probes': [],
        'required_graph_revisions': [],
        'disabled_routes': [],
        'validated_parent_edges': [],
        'no_progress_parent_edges': [],
        'observations': [],
    }


def _available(route, state):
    if route['id'] in set(state.get('disabled_routes', [])):
        return False
    limit = route.get('attempt_limit')
    return limit is None or state['route_attempts'].get(route['id'], 0) < limit


def _failure_penalty(route):
    """Return a positive default so a failed cheap OR route cannot starve peers."""
    if 'failure_penalty_seconds' in route:
        return float(route['failure_penalty_seconds'])
    return max(float(route.get('estimate_seconds', 0.0)), 1.0)


def _observed_probe_children(state, goal_id, route_id):
    return {
        item['child_goal']
        for collection in ('validated_parent_edges',
                           'no_progress_parent_edges')
        for item in state.get(collection, [])
        if (item.get('parent_goal') == goal_id
            and item.get('selected_route') == route_id)
    }


def best_plan(problem, state):
    """Return the exact minimum-cost root plan within a bounded frontier.

    Local OR optima are insufficient when sibling routes can share a subgraph,
    so all distinct compatible plans are retained until the root frontier is
    complete.  Proof/recheck cost includes an explicit penalty for prior RED
    attempts.  A normal AND route pays for one parent probe after all direct
    requirements are certified.  The opt-in ``each_accepted_child`` policy
    conservatively budgets one probe per direct child, because it probes an
    explicitly labelled semantic hypothesis before the AND set is complete.
    Exceeding max_frontier_plans raises instead of silently approximating.
    """
    goals, routes, route_goal = _index(problem)
    certified = set(state.get('certified', []))
    candidates = state.get('candidate_routes', {})
    attempts = state.get('route_attempts', {})
    disabled = set(state.get('disabled_routes', []))
    if not certified <= set(goals):
        raise ValueError('state contains unknown certified goals')
    if any(key not in goals for key in candidates):
        raise ValueError('state contains unknown candidate goal')
    if any(route_id not in routes or route_goal[route_id] != key
           for key, route_id in candidates.items()):
        raise ValueError('state contains invalid candidate route')
    if not disabled <= set(routes):
        raise ValueError('state contains unknown disabled route')
    state = {**state, 'route_attempts': attempts}
    frontier_limit = problem.get('max_frontier_plans', DEFAULT_MAX_FRONTIER_PLANS)
    memo = {}

    def signature(candidate):
        return (
            tuple(sorted(candidate['required_goals'])),
            tuple(sorted(candidate['selected_routes'].items())),
            tuple(sorted(candidate['costs'].items())),
            tuple(sorted(candidate['probe_costs'].items())),
        )

    def append_bounded(frontier, seen, candidate, context):
        marker = signature(candidate)
        if marker in seen:
            return
        if len(frontier) >= frontier_limit:
            raise PlanFrontierLimitExceeded(
                'exact AND/OR frontier exceeds max_frontier_plans='
                + str(frontier_limit) + ' at ' + context)
        seen.add(marker)
        frontier.append(candidate)

    def merge(left, right):
        merged_routes = dict(left['selected_routes'])
        for selected_goal, selected_route in right['selected_routes'].items():
            prior = merged_routes.get(selected_goal)
            if prior is not None and prior != selected_route:
                return None
            merged_routes[selected_goal] = selected_route
        merged_costs = dict(left['costs'])
        merged_probes = dict(left['probe_costs'])
        for selected_goal, cost in right['costs'].items():
            prior = merged_costs.get(selected_goal)
            if prior is not None and prior != cost:
                raise ValueError('inconsistent cost for shared goal ' + selected_goal)
            merged_costs[selected_goal] = cost
        for selected_goal, cost in right['probe_costs'].items():
            prior = merged_probes.get(selected_goal)
            if prior is not None and prior != cost:
                raise ValueError('inconsistent probe cost for shared goal ' + selected_goal)
            merged_probes[selected_goal] = cost
        return {
            'required_goals': left['required_goals'] | right['required_goals'],
            'selected_routes': merged_routes,
            'costs': merged_costs,
            'probe_costs': merged_probes,
        }

    def enumerate_goal(key):
        if key in memo:
            return memo[key]
        goal = goals[key]
        if key in certified:
            answer = [{'required_goals': {key}, 'selected_routes': {},
                       'costs': {}, 'probe_costs': {}}]
            memo[key] = answer
            return answer
        choices, choice_signatures = [], set()
        for route in goal['routes']:
            if not _available(route, state):
                continue
            partials = [{'required_goals': set(), 'selected_routes': {},
                         'costs': {}, 'probe_costs': {}}]
            for child in route.get('requires', []):
                child_plans = enumerate_goal(child)
                if not child_plans:
                    partials = []
                    break
                expanded, expanded_signatures = [], set()
                for partial in partials:
                    for child_plan in child_plans:
                        combined = merge(partial, child_plan)
                        if combined is not None:
                            append_bounded(expanded, expanded_signatures, combined,
                                           key + ':' + route['id'])
                partials = expanded
                if not partials:
                    break
            if not partials:
                continue
            base = (route.get('recheck_seconds', route.get('estimate_seconds', 0.0))
                    if candidates.get(key) == route['id']
                    else route.get('estimate_seconds', 0.0))
            penalty = attempts.get(route['id'], 0) * _failure_penalty(route)
            required_children = set(route.get('requires', []))
            observed_children = _observed_probe_children(
                state, key, route['id'])
            probe_policy = route.get(
                'probe_policy', 'all_requirements_certified')
            if (probe_policy == 'none' or not required_children
                    or required_children <= observed_children):
                probe_count = 0
            elif probe_policy == 'each_accepted_child':
                probe_count = len(required_children - observed_children)
            else:
                probe_count = 1
            probe = route.get('probe_seconds', 0.0) * probe_count
            for partial in partials:
                selected_routes = dict(partial['selected_routes'])
                selected_routes[key] = route['id']
                costs = dict(partial['costs'])
                costs[key] = float(base + penalty + probe)
                probe_costs = dict(partial['probe_costs'])
                if probe:
                    probe_costs[key] = float(probe)
                append_bounded(choices, choice_signatures, {
                    'required_goals': partial['required_goals'] | {key},
                    'selected_routes': selected_routes,
                    'costs': costs,
                    'probe_costs': probe_costs,
                }, key)
        memo[key] = choices
        return choices

    frontier = enumerate_goal(problem['root_goal'])
    if not frontier:
        return None
    plan = min(frontier, key=lambda candidate: (
        sum(candidate['costs'].values()),
        tuple(sorted(candidate['selected_routes'].items()))))
    total = sum(plan['costs'].values())
    probe_total = sum(plan['probe_costs'].values())
    return {
        'root_goal': problem['root_goal'],
        'estimated_seconds': total,
        'estimated_proof_seconds': total - probe_total,
        'estimated_probe_seconds': probe_total,
        'required_goals': sorted(plan['required_goals'],
                                 key=lambda key: (goals[key].get('order', 0), key)),
        'selected_routes': dict(sorted(plan['selected_routes'].items())),
        'goal_cost_seconds': dict(sorted(plan['costs'].items())),
        'probe_cost_seconds': dict(sorted(plan['probe_costs'].items())),
    }


def _route_for(goal_id, plan, routes):
    route_id = plan['selected_routes'].get(goal_id)
    return routes.get(route_id)


def next_decision(problem, state):
    """Return a deterministic, root-reachable scheduling decision."""
    goals, routes, _ = _index(problem)
    root = problem['root_goal']
    certified = set(state.get('certified', []))
    if root in certified:
        return {'action': 'SOLVED', 'root_goal': root,
                'notice': 'The root is journal-accepted.'}
    revisions = state.get('required_graph_revisions', [])
    if revisions:
        revision = min(revisions, key=lambda item: (
            goals[item['target_goal']].get('order', 0), item['target_goal'],
            item['selected_route']))
        return {'action': 'REVISE_GRAPH', 'root_goal': root,
                'target_goal': revision['target_goal'],
                'child_goals': list(revision['child_goals']),
                'selected_route': revision['selected_route'],
                'allowed_resolutions': ['disable_selected_route'],
                'notice': 'A parent probe found no progress; revise the search graph before scheduling.'}
    plan = best_plan(problem, state)
    if plan is None:
        return {'action': 'BLOCKED', 'root_goal': root,
                'notice': 'No non-exhausted root route remains.'}
    required = set(plan['required_goals'])
    probes = [probe for probe in state.get('pending_parent_probes', [])
              if (probe['target_goal'] in required
                  and probe.get('selected_route')
                  == plan['selected_routes'].get(probe['target_goal']))]
    if probes:
        probe = min(probes, key=lambda item: (
            goals[item['target_goal']].get('order', 0), item['target_goal'],
            item['selected_route']))
        return {'action': 'PROBE_PARENT', 'root_goal': root,
                'target_goal': probe['target_goal'],
                'child_goals': list(probe['child_goals']),
                'selected_route': probe['selected_route'],
                'notice': 'Validate helper-to-parent progress before exploring another branch.'}

    candidates = state.get('candidate_routes', {})
    ready_candidates = []
    remaining = {key for key in required - certified
                 if candidates.get(key) == plan['selected_routes'].get(key)}
    # A completed command may be rechecked only after all of its mathematical
    # prerequisites are certified or occur earlier in the same source prefix.
    changed = True
    while changed:
        changed = False
        for key in sorted(remaining, key=lambda item: (goals[item].get('order', 0), item)):
            route = _route_for(key, plan, routes)
            if route and set(route.get('requires', [])) <= certified | set(ready_candidates):
                ready_candidates.append(key); remaining.remove(key); changed = True
    if ready_candidates:
        return {'action': 'RECHECK_PREFIX', 'root_goal': root,
                'goal_ids': ready_candidates,
                'route_ids': [plan['selected_routes'][key] for key in ready_candidates],
                'notice': 'Candidates are not certified until the prefix is journal-accepted.'}

    ready = []
    for key in required - certified:
        route = _route_for(key, plan, routes)
        if route and candidates.get(key) != route['id'] and set(route.get('requires', [])) <= certified:
            ready.append(key)
    if not ready:
        return {'action': 'BLOCKED', 'root_goal': root,
                'notice': 'The selected route has no schedulable frontier.'}
    key = min(ready, key=lambda item: (
        plan['goal_cost_seconds'].get(item, 0.0), goals[item].get('order', 0), item))
    return {'action': 'PROVE', 'root_goal': root, 'goal_id': key,
            'route_id': plan['selected_routes'][key],
            'estimated_seconds': plan['goal_cost_seconds'][key],
            'notice': 'This frontier lies on the current minimum-cost root plan.'}


def _nearest_parents(problem, plan, child):
    """Find every selected checkable consumer at the nearest graph depth."""
    goals, routes, _ = _index(problem)
    selected = plan['selected_routes']
    frontier, seen, nearest = [child], {child}, set()
    while frontier:
        next_frontier = []
        for node in frontier:
            parents = []
            for parent, route_id in selected.items():
                if node in routes[route_id].get('requires', []) and parent not in seen:
                    parents.append(parent)
            for parent in sorted(set(parents),
                                 key=lambda key: (goals[key].get('order', 0), key)):
                seen.add(parent)
                if goals[parent].get('checkable', True):
                    nearest.add(parent)
                else:
                    next_frontier.append(parent)
        frontier = next_frontier
    return sorted(nearest, key=lambda key: (goals[key].get('order', 0), key))


def apply_node_results(problem, state, results, *, evidence=None):
    """Apply proof observations without converting RED into falsity.

    ``accepted_green`` means both native GREEN and durable journal acceptance.
    A merely audited or completed command stays a recheck candidate.  Transport,
    cancellation and declaration-protocol failures do not change route attempts.
    """
    goals, routes, route_goal = _index(problem)
    if not isinstance(results, list):
        raise ValueError('node results must be a list')
    out = deepcopy(state)
    certified = set(out.setdefault('certified', []))
    candidates = out.setdefault('candidate_routes', {})
    attempts = out.setdefault('route_attempts', {})
    accepted_now = []
    recorded = []
    for result in results:
        if not isinstance(result, dict):
            raise ValueError('node result must be an object')
        key, route_id, status = result.get('goal_id'), result.get('route_id'), result.get('status')
        if key not in goals or route_id not in routes or route_goal[route_id] != key:
            raise ValueError('node result does not identify a goal route')
        if status == 'accepted_green':
            if key not in certified:
                accepted_now.append(key)
            certified.add(key); candidates.pop(key, None)
        elif status in CANDIDATE:
            candidates[key] = route_id
        elif status == 'failed':
            attempts[route_id] = attempts.get(route_id, 0) + 1
            candidates.pop(key, None)
        elif status not in NO_MATH_CHANGE:
            raise ValueError('unknown node result status: ' + str(status))
        recorded.append({'goal_id': key, 'route_id': route_id, 'status': status})
    out['certified'] = sorted(certified)
    out.setdefault('observations', []).append({'kind': 'node_results',
        'results': recorded, 'evidence': deepcopy(evidence)})

    # Only a journal transition can create a probe obligation.  Batch siblings
    # per selected nearest consumer, but retain every shared-helper consumer.
    plan = best_plan(problem, out)
    if plan is not None:
        grouped = {}
        for child in accepted_now:
            if child == problem['root_goal'] or child not in set(plan['required_goals']):
                continue
            for parent in _nearest_parents(problem, plan, child):
                if parent in certified:
                    continue
                selected_route = plan['selected_routes'][parent]
                route = routes[selected_route]
                required = set(route.get('requires', []))
                policy = route.get(
                    'probe_policy', 'all_requirements_certified')
                if policy == 'none':
                    # A conservative route never uses a helper observation to
                    # prune or reprioritise its parent.  The parent must still
                    # be proved and accepted by the ordinary native boundary.
                    continue
                if policy == 'all_requirements_certified':
                    # A partial AND set cannot safely justify destructive
                    # no-progress pruning while a sibling is still missing.
                    if not required <= certified:
                        continue
                    grouped.setdefault(parent, []).extend(sorted(required))
                else:
                    grouped.setdefault(parent, []).append(child)
        pending = out.setdefault('pending_parent_probes', [])
        for parent, children in grouped.items():
            selected_route = plan['selected_routes'][parent]
            existing = next((item for item in pending
                             if (item['target_goal'] == parent
                                 and item.get('selected_route') == selected_route)), None)
            if existing:
                existing['child_goals'] = sorted(set(existing['child_goals']) | set(children))
            else:
                pending.append({'target_goal': parent,
                                'child_goals': sorted(children),
                                'selected_route': selected_route})
    return out


def apply_parent_probe(problem, state, *, target_goal, verdict,
                       before_fingerprint=None, after_fingerprint=None, evidence=None):
    """Record the checker-observed effect of accepted children on a parent."""
    goals, routes, route_goal = _index(problem)
    if target_goal not in goals:
        raise ValueError('unknown parent probe target')
    if verdict not in {'progress', 'no_progress', 'infrastructure_failure'}:
        raise ValueError('unknown parent probe verdict')
    out = deepcopy(state)
    pending = out.setdefault('pending_parent_probes', [])
    target_matches = [item for item in pending if item['target_goal'] == target_goal]
    if not target_matches:
        raise ValueError('parent probe was not pending')
    selected_routes = {item.get('selected_route') for item in target_matches}
    plan = best_plan(problem, out)
    active_route = (plan['selected_routes'].get(target_goal)
                    if plan is not None else None)
    if active_route in selected_routes:
        selected_route = active_route
    elif len(selected_routes) == 1:
        selected_route = next(iter(selected_routes))
    else:
        raise ValueError('parent probe target has ambiguous selected routes')
    if (selected_route not in routes
            or route_goal[selected_route] != target_goal):
        raise ValueError('parent probe has invalid selected route')
    matches = [item for item in target_matches
               if item.get('selected_route') == selected_route]
    children = sorted({child for item in matches for child in item['child_goals']})
    if verdict != 'infrastructure_failure':
        valid_before = isinstance(before_fingerprint, str) and bool(before_fingerprint)
        valid_after = isinstance(after_fingerprint, str) and bool(after_fingerprint)
        if not valid_before or not valid_after:
            raise ValueError('parent probe requires two non-empty residual fingerprints')
        if verdict == 'progress' and before_fingerprint == after_fingerprint:
            raise ValueError('progress requires two distinct residual fingerprints')
        if verdict == 'no_progress' and before_fingerprint != after_fingerprint:
            raise ValueError('no_progress requires identical residual fingerprints')
    route = routes[selected_route]
    route_required = set(route.get('requires', []))
    route_complete = route_required <= set(out.get('certified', []))
    probe_scope = ('route' if route_required <= set(children)
                   else 'child_hypothesis')
    observation = {'kind': 'parent_probe', 'target_goal': target_goal,
                   'child_goals': children, 'verdict': verdict,
                   'before_fingerprint': before_fingerprint,
                   'after_fingerprint': after_fingerprint,
                   'selected_route': selected_route,
                   'probe_scope': probe_scope,
                   'evidence': deepcopy(evidence)}
    out.setdefault('observations', []).append(observation)
    if verdict == 'infrastructure_failure':
        return out
    if verdict == 'progress':
        out.setdefault('validated_parent_edges', []).extend(
            {'child_goal': child, 'parent_goal': target_goal,
             'selected_route': selected_route,
             'before_fingerprint': before_fingerprint,
             'after_fingerprint': after_fingerprint}
            for child in children)
    else:
        out.setdefault('no_progress_parent_edges', []).extend(
            {'child_goal': child, 'parent_goal': target_goal,
             'selected_route': selected_route,
             'probe_scope': probe_scope,
             'residual_fingerprint': after_fingerprint}
            for child in children)
        progress_children = {
            item['child_goal']
            for item in out.get('validated_parent_edges', [])
            if (item.get('parent_goal') == target_goal
                and item.get('selected_route') == selected_route)
        }
        matching_no_progress = [
            item for item in out.get('no_progress_parent_edges', [])
            if (item.get('parent_goal') == target_goal
                and item.get('selected_route') == selected_route)
        ]
        no_progress_children = {
            item['child_goal'] for item in matching_no_progress
        }
        no_progress_fingerprints = {
            item['residual_fingerprint'] for item in matching_no_progress
        }
        route_no_progress = (
            route_complete
            and not progress_children
            and route_required <= no_progress_children
            and no_progress_fingerprints == {after_fingerprint}
        )
        if not route_no_progress:
            out['pending_parent_probes'] = [item for item in pending
                                            if item not in matches]
            return out
        revisions = out.setdefault('required_graph_revisions', [])
        existing = next((item for item in revisions
                         if (item['target_goal'] == target_goal
                             and item['selected_route'] == selected_route)), None)
        if existing:
            existing['child_goals'] = sorted(
                set(existing['child_goals']) | route_required)
        else:
            revisions.append({
                'target_goal': target_goal,
                'child_goals': sorted(route_required),
                'selected_route': selected_route,
                'reason': 'all_required_probes_no_progress',
            })
    out['pending_parent_probes'] = [item for item in pending
                                    if item not in matches]
    return out


def apply_graph_revision(problem, state, *, target_goal, resolution, evidence=None):
    """Resolve a mandatory graph revision before mathematical search resumes."""
    goals, routes, route_goal = _index(problem)
    if target_goal not in goals:
        raise ValueError('unknown graph revision target')
    if resolution != 'disable_selected_route':
        raise ValueError('unknown graph revision resolution')
    out = deepcopy(state)
    revisions = out.setdefault('required_graph_revisions', [])
    matches = [item for item in revisions if item['target_goal'] == target_goal]
    if not matches:
        raise ValueError('graph revision was not required')
    selected_routes = {item['selected_route'] for item in matches}
    if len(selected_routes) != 1:
        raise ValueError('graph revision target has ambiguous selected routes')
    route_id = next(iter(selected_routes))
    if route_id not in routes or route_goal[route_id] != target_goal:
        raise ValueError('graph revision has invalid selected route')
    disabled = set(out.setdefault('disabled_routes', []))
    disabled.add(route_id)
    out['disabled_routes'] = sorted(disabled)
    candidates = out.setdefault('candidate_routes', {})
    if candidates.get(target_goal) == route_id:
        candidates.pop(target_goal, None)
    out['required_graph_revisions'] = [
        item for item in revisions
        if not (item['target_goal'] == target_goal
                and item['selected_route'] == route_id)
    ]
    out['pending_parent_probes'] = [
        item for item in out.setdefault('pending_parent_probes', [])
        if not (item['target_goal'] == target_goal
                and item.get('selected_route') == route_id)
    ]
    out.setdefault('observations', []).append({
        'kind': 'graph_revision',
        'target_goal': target_goal,
        'selected_route': route_id,
        'resolution': resolution,
        'evidence': deepcopy(evidence),
    })
    return out
