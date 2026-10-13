"""Deduplicated target attempt memory; legacy inline states remain readable."""
from copy import deepcopy
from regen.execution.proof_commits import digest


def target_attempts(state):
    table = state.get('batch_attempt_objects', {})
    verified = set()
    for target, value in state.get('batch_target_memory', {}).items():
        if isinstance(value, dict) and set(value) == {'attempt_ref'}:
            key = value['attempt_ref']
            if key not in verified and (key not in table or digest(table[key]) != key):
                raise ValueError('missing or corrupt target attempt: ' + str(key))
            verified.add(key)
            yield target, table[key]
        else:
            yield target, value


def compact_target_memory(state):
    """Normalize a caller-owned state; do not rewrite historical artifacts."""
    if 'batch_target_memory' not in state:
        return state
    table, targets, identities = {}, {}, {}
    # Resolve all old references before replacing the table. Unreferenced entries
    # need not survive here: historical requests/commits retain prior attempts.
    for target, attempt in target_attempts(state):
        identity = id(attempt)
        key = identities.get(identity)
        if key is None:
            key = identities[identity] = digest(attempt)
        if key not in table:
            table[key] = deepcopy(attempt)
        targets[target] = {'attempt_ref': key}
    state['batch_target_memory'] = targets
    state['batch_attempt_objects'] = table
    return state


def expand_target_memory(state):
    result = deepcopy(state)
    result['batch_target_memory'] = {k: deepcopy(v) for k, v in target_attempts(state)}
    result.pop('batch_attempt_objects', None)
    return result
