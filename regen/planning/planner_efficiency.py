"""Bound draft editing, RED memoization and useful next-turn observations.

Memoized failures are observations only, never acceptance evidence. No candidate
or proof-state text is inferred or changed by this module.
"""
from copy import deepcopy
from regen.execution.proof_commits import digest


def expand_edits(directory, state, proposal):
    from regen.planning.batch_routes import expand_reuse
    from regen.planning.planner_workspace import attempt_evidence
    out = deepcopy(proposal)
    selections = out.pop('proof_edits', [])
    if not isinstance(selections, list) or not 1 <= len(selections) <= 8:
        raise ValueError('proof_edits requires 1-8 bound proof selections')
    origins = []
    for selection in selections:
        if not isinstance(selection, dict) or set(selection) != {'request_number','node_id','proof_sha256','edits'}:
            raise ValueError('proof_edits requires request_number, node_id, proof_sha256, edits')
        prior, _ = attempt_evidence(directory, state, selection['request_number'])
        key = selection['node_id']
        body = prior.get('proofs', {}).get(key)
        if not isinstance(body, str) or digest(body) != selection['proof_sha256']:
            raise ValueError('draft proof hash mismatch')
        edits = selection['edits']
        if not isinstance(edits, list) or not 1 <= len(edits) <= 32:
            raise ValueError('edits requires 1-32 exact replacements')
        # Reuse performs current declaration/GREEN/duplicate checks as well.
        out.setdefault('reuse_proofs', []).append({'request_number':selection['request_number'], 'node_ids':[key]})
        out = expand_reuse(directory, state, out)
        for edit in edits:
            if (not isinstance(edit, dict) or set(edit) != {'old','new'}
                    or not isinstance(edit['old'], str) or not edit['old']
                    or not isinstance(edit['new'], str) or body.count(edit['old']) != 1):
                raise ValueError('each draft edit must match exactly once, sequentially')
            body = body.replace(edit['old'], edit['new'], 1)
        out['proofs'][key] = body
        origins.append(deepcopy(selection))
    out['edit_origin'] = origins
    return out


def failure_identity(run, checker, old, candidate):
    from regen.execution.proof_batches import _binding
    return digest({'scheme':'same-context-red-v1', 'run':run,
        'old':_binding(old), 'candidate':_binding(candidate),
        'imports':checker.imports(), 'timeout':checker.timeout,
        'candidate_timeout':getattr(checker, 'batch_candidate_timeout', 120),
        'incremental':getattr(checker, 'batch_incremental', False)})


def deterministic_red(check):
    return (check.get('verdict') == 'red' and check.get('returncode') == 1
            and not check.get('timed_out')
            and isinstance(check.get('diagnostic'), str)
            and any(x in check['diagnostic'] for x in
                    ('Failed to finish proof', 'Failed to apply', 'Undefined fact',
                     'Undefined constant', 'Type unification failed', 'Outer syntax error')))


def reused_failure(directory, state, identity, *, retry_reason=None):
    from regen.planning.planner_workspace import attempt_evidence
    if retry_reason is not None:
        if not isinstance(retry_reason, str) or not retry_reason.strip():
            raise ValueError('retry_check requires a nonempty reason')
        return None
    for h in reversed(state.get('batch_history', [])):
        if h.get('failure_identity') != identity:
            continue
        _, feedback = attempt_evidence(directory, state, h['request_number'])
        if feedback.get('failure_identity') != identity or not deterministic_red(feedback.get('check', {})):
            continue
        out = deepcopy(feedback)
        out.pop('cost_seconds', None)
        out.update(status='rejected_batch', cached_failure=True,
            reused_request=feedback.get('reused_request', h['request_number']),
            notice='Same frozen context and candidate already failed. No Isabelle call or new proof acceptance. '
                   'Edit the candidate or use retry_check with a reason for an explicit rerun.')
        return out
    return None


def next_proof_observation(state):
    """Automatically expose exact small goals; keep large states accessible by ID."""
    from regen.planning.planner_workspace import diagnostic_goals
    attempt = state.get('batch_proof_feedback') or {}
    check = attempt.get('feedback', {}).get('check', {})
    if not check or check.get('verdict') != 'red':
        return None
    parsed = diagnostic_goals(check)
    # A compacted diagnostic is never treated as a complete state.
    complete = not check.get('diagnostic_excerpt') and parsed.get('all_goals_present', False)
    result = {'request_number':attempt['request_number'],
        'proof_hashes':{k:digest(v) for k,v in attempt.get('candidate', {}).get('proofs', {}).items()},
        'native_before_command':check.get('native_proof_state'),
        'goal_count':parsed.get('goal_count'),
        'notice':'Observed residuals are obligations, not premises. Use named have/show branches or helper nodes '
                 'to isolate stable subproofs; proof_edits preserves unchanged draft text, but the whole candidate is checked.'}
    if complete and sum(len(g['text']) for g in parsed['goals']) <= 16000:
        result['complete_diagnostic_goals'] = parsed['goals']
    else:
        result['read_action'] = {'action':'read_proof_state', 'request_number':attempt['request_number']}
    return result


def failed_routes(state):
    # Keep navigation to repeated failures beyond the 12-item recent window.
    groups = {}
    for h in state.get('batch_history', []):
        key = h.get('candidate_signature')
        if not key or h.get('status') != 'rejected_batch':
            continue
        g = groups.setdefault(key, {'candidate_signature':key,'requests':[], 'node_ids':h.get('proof_node_ids',[])})
        g['requests'].append(h['request_number'])
    return sorted((g for g in groups.values() if len(g['requests']) > 1),
                  key=lambda g:g['requests'][-1])[-12:]
