"""Progress identities are separate from complete, unchanged audit receipts.

Only known protocol-envelope metadata is omitted. Never recursively remove IDs
from mathematical candidates, facts, assumptions, source text or proof mappings.
"""
from copy import deepcopy


def _without(value, keys):
    if not isinstance(value, dict):
        return deepcopy(value)
    return {k: deepcopy(v) for k, v in value.items() if k not in keys}


def information_payload(name, arguments, value):
    args = deepcopy(arguments)
    result = deepcopy(value)
    if name == 'read_attempt':
        result = _without(result, {'source_commit_id'})
    elif name == 'list_attempts':
        args.pop('snapshot_commit_id', None)
        result = _without(result, {'snapshot_commit_id'})
    elif name in {'proof_action', 'submit_check', 'get_check_result'}:
        if name == 'get_check_result':
            args.pop('task_id', None)
        if name in {'submit_check', 'get_check_result'} and isinstance(result, dict):
            # Task receipts contain submission times and audit paths, not just
            # task_id. Preserve terminal state and evidence, not the transport.
            result = {k: deepcopy(v) for k, v in result.items() if k in {
                'theory', 'kind', 'status', 'result', 'error_type', 'diagnostic'}}
        else:
            result = _without(result, {'task_id'})
    elif name == 'read_theory' and isinstance(result, dict):
        observations = result.get('observations')
        if isinstance(observations, dict):
            observations.pop('progress_signals', None)
            observations.pop('recent_actions', None)  # Navigation, not new evidence.
            for field in ('query_index', 'query_memory'):
                if isinstance(observations.get(field), list):
                    observations[field] = [_without(row, {
                        'task_ids', 'requests', 'last_request', 'count', 'observation_order', 'cache'})
                        for row in observations[field]]
    # The controller wraps independent observations into the same executor path.
    # Normalize a direct observation and an observe(reads=[...]) consistently.
    if name in {'proof_action', 'submit_check'}:
        proposal = args.get('proposal', {})
        action = proposal.get('action')
        read_actions = {'query', 'read_attempts', 'read_facts', 'read_proofs', 'read_queries',
                        'read_diagnostic', 'read_proof_state', 'resolve_symbols'}
        if action in read_actions or action == 'observe':
            reads = proposal.get('reads', []) if action == 'observe' else [proposal]
            args = {'theory': args.get('theory'), 'reads': reads}
            name = 'observations'
    return [name, args, result]


def proof_work(name, arguments, value):
    proposal = arguments.get('proposal', arguments)
    if name not in {'proof_action', 'submit_check'} or proposal.get('action') not in {
            'check_theory', 'submit_batch', 'finish'}:
        return False
    # A real RED check is work. A cached RED or argument rejection is not a new
    # check merely because its tool name contains check/submit.
    pending = [value]
    while pending:
        item = pending.pop()
        if not isinstance(item, dict):
            continue
        if item.get('cached_failure') or item.get('status') in {
                'rejected_request', 'tool_failed', 'cancelled', 'error', 'unavailable'}:
            return False
        pending.extend(item[k] for k in ('feedback', 'result') if k in item)
    return True
