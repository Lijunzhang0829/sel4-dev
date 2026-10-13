"""Model-facing history views, never proof authority or a replacement journal.

Readers verify original artifacts before calling these pure projections. Summaries
are navigation, not complete propositions. Explicit full reads retain exact bytes
of mathematical strings; journal/hash validation is never skipped for a cache hit.
"""
from copy import deepcopy

from regen.execution.proof_commits import digest


FAILED_STATUSES = frozenset({'rejected_batch', 'rejected_request', 'checked_not_committed'})


def excerpt(value, limit=1800):
    text = value if isinstance(value, str) else ''
    return {'text': text[:limit], 'incomplete': len(text) > limit,
            'total_chars': len(text), 'sha256': digest(text)}


def failed_entries(results):
    results = results if isinstance(results, list) else []
    return list(dict.fromkeys(item.get('node_id', item.get('entry_id'))
        for item in results if isinstance(item, dict) and item.get('status') == 'failed'
        and isinstance(item.get('node_id', item.get('entry_id')), str)))


def history_row(item):
    """A batch containing an entry does NOT identify that entry as the failure."""
    number = item['request_number']
    failed = failed_entries(item.get('node_results', item.get('entry_results', [])))
    return {'request_number': number, 'action': item.get('action'),
            'status': item.get('status'),
            'submitted_entry_ids': list(item.get('proof_node_ids', item.get('proof_entry_ids', []))),
            'checker_failed_entry_ids': failed,
            'failure_localization': 'checker_reported' if failed else 'unknown',
            'proposal_sha256': item.get('proposal_sha256'),
            'feedback_sha256': item.get('feedback_sha256'),
            'reason_excerpt': excerpt(item.get('reason'), 400),
            'read_action': {'action': 'read_attempts', 'request_numbers': [number], 'view': 'summary'}}


def attempt_view(number, candidate, feedback, *, view='summary', theory=None,
                 run_id=None, proposal_sha256=None, feedback_sha256=None):
    if view not in ('summary', 'full'):
        raise ValueError('attempt view must be summary or full')
    bindings = {'run_id': run_id, 'theory': theory, 'request_number': number,
                'proposal_sha256': proposal_sha256 or digest(candidate),
                'feedback_sha256': feedback_sha256 or digest(feedback)}
    result = {'request_number': number, 'view': view, 'reference_only': True,
              'content_id': digest(bindings), 'bindings': bindings,
              'read_action': {'action': 'read_attempts', 'request_numbers': [number], 'view': 'full'},
              'notice': 'Historical evidence only, never a GREEN premise. Summary text is navigation; request full before using omitted proof/context.'}
    if view == 'full':
        return {**result, 'candidate': deepcopy(candidate), 'feedback': deepcopy(feedback)}
    candidate = candidate if isinstance(candidate, dict) else {}
    feedback = feedback if isinstance(feedback, dict) else {}
    check = feedback.get('check', {})
    check = check if isinstance(check, dict) else {}
    proofs = candidate.get('proofs', {})
    source = candidate.get('document_source', '')
    failed = failed_entries(feedback.get('node_results', feedback.get('entry_results', [])))
    result['summary'] = {
        'action': candidate.get('action'), 'status': feedback.get('status'),
        'submitted_entry_ids': list(proofs) if isinstance(proofs, dict) else [],
        'checker_failed_entry_ids': failed,
        'failure_localization': 'checker_reported' if failed else 'unknown',
        'verdict': check.get('verdict'), 'reason_excerpt': excerpt(candidate.get('reason'), 400),
        'diagnostic_excerpt': excerpt(check.get('diagnostic', feedback.get('diagnostic'))),
        'candidate_chars': len(source) if isinstance(source, str) else 0,
    }
    return result


def selected_attempt_summaries(attempts, history, *, theory=None, run_id=None):
    """Also read legacy checkpoints whose selected working set embeds full drafts."""
    rows = {h['request_number']: h for h in history}
    result = []
    for attempt in attempts:
        if not isinstance(attempt, dict) or 'request_number' not in attempt:
            continue
        if attempt.get('view') == 'summary' and 'summary' in attempt:
            result.append(deepcopy(attempt))
            continue
        number = attempt['request_number']
        row = rows.get(number, {})
        result.append(attempt_view(number, attempt.get('candidate'), attempt.get('feedback'),
            theory=theory, run_id=run_id, proposal_sha256=row.get('proposal_sha256'),
            feedback_sha256=row.get('feedback_sha256')))
    return result
