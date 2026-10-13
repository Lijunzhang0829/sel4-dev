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


def feedback_semantics(feedback):
    """Unambiguous presentation beside legacy feedback, never proof authority."""
    status = feedback.get('status')
    verdict = feedback.get('check', {}).get('verdict')
    if status in {'accepted', 'draft_checked'}:
        verdict = 'green'
    publication = {'accepted': 'accepted', 'draft_checked': 'diagnostic_only',
                   'sealed': 'sealed'}.get(status, 'not_published')
    return {'result_schema_version': 2,
            'tool_status': 'rejected' if status == 'rejected_request' else
                           'failed' if status == 'tool_failed' else 'completed',
            'proof_verdict': verdict, 'publication': publication}


def next_proof_observation(state, *, feedback=None):
    """Automatically expose exact small goals; keep large states accessible by ID."""
    from regen.planning.planner_workspace import diagnostic_goals
    attempt = state.get('batch_proof_feedback') or {}
    if type(attempt.get('request_number')) is not int:
        return None
    if feedback is not None and attempt['request_number'] != state.get('batch_requests', attempt['request_number']):
        return None  # A declaration/query failure must not be bound to an old proof.
    # A just-completed callback can use its already-verified full feedback without
    # rereading the entire theory. Historical prompt reconstruction remains bound
    # to the stored last proof attempt and never expands all prior evidence.
    check = (feedback if feedback is not None else attempt.get('feedback', {})).get('check', {})
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
    native = check.get('native_proof_state_full')
    if isinstance(native, dict) and native.get('origin') != 'unavailable':
        goals, assumptions = native.get('goals', []), native.get('local_assumptions', [])
        texts = [*goals, *assumptions]
        native_complete = all(isinstance(t, str) and '...' not in t and '…' not in t for t in texts)
        result['native_goal_index'] = [{'goal_index': i+1, 'chars': len(text),
            'sha256': digest([assumptions, text])} for i, text in enumerate(goals[:16])]
        result['native_goal_index_complete'] = len(goals) <= 16
        if native_complete and sum(len(text) for text in texts) <= 16000:
            result['complete_native_before_command'] = {
                **{key: deepcopy(native[key]) for key in ('origin', 'command', 'line') if key in native},
                'goals': deepcopy(goals), 'local_assumptions': deepcopy(assumptions)}
        elif goals:
            if len(goals[0]) + sum(len(text) for text in assumptions) <= 60000:
                result['native_read_action'] = {'action': 'read_proof_state',
                    'request_number': attempt['request_number'], 'origin': 'before_command', 'goal_indices': [1]}
            else:
                result['native_read_action'] = {'action': 'read_attempts',
                    'request_numbers': [attempt['request_number']], 'view': 'full'}
    # Small location evidence is useful even when the raw residual is huge.
    from regen.planning.batch_file_planner import compact_feedback
    location = compact_feedback({key: check[key] for key in
        ('source', 'source_content', 'source_sha256', 'diagnostic') if key in check})
    for key in ('source_error_line', 'source_excerpt'):
        if key in location:
            result[key] = location[key]
    if complete and sum(len(g['text']) for g in parsed['goals']) <= 16000:
        result['complete_diagnostic_goals'] = parsed['goals']
    else:
        result['read_action'] = {'action':'read_proof_state', 'request_number':attempt['request_number'],
                                 'origin':'diagnostic'}
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
