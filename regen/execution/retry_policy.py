"""Bounded scheduling heuristics; none of these certify a proof."""
import re
from regen.execution.proof_commits import digest


def failure_signature(attempt):
    """Compare checker errors, ignoring locations but preserving goal content."""
    from regen.execution.proof_feedback import semantic_goal
    if attempt.get('check', {}).get('isabelle_called') is False:
        return None
    goal = semantic_goal(attempt.get('check', {}).get('diagnostic', ''))
    return digest(goal) if goal else None


def stagnant(attempts):
    if len(attempts) < 2:
        return False
    a, b = attempts[-2:]
    sig = failure_signature(a)
    return bool(sig and sig == failure_signature(b)
                and set(a.get('retrieved_premises', [])) == set(b.get('retrieved_premises', [])))


def required_context(state, key):
    nodes = {n['id']: n for n in state['graph']['nodes']}
    # Exclude node IDs/names and strategy text from the credit identity.
    return sorted({digest({'proposition': n['statement'].split(':', 1)[-1],
                           'context': n['context']})
                   for dep in nodes[key]['dependencies']
                   if dep in state['proofs'] for n in [nodes[dep]]})


def extra_round_available(state, key, base_limit, reserve=1):
    used = state['rounds'].get(key, 0)
    previous = state.get('attempt_contexts', {}).get(key)
    return (base_limit <= used < base_limit + reserve
            and previous is not None
            and bool(set(required_context(state, key)) - set(previous)))
