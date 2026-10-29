"""Advisory comparisons of verified failure receipts, never proof authority.

Equal observations mean equal rendered feedback in the same bound context, not
semantic equivalence of proof states. Changed observations do not certify progress.
"""
from difflib import SequenceMatcher
import re

from regen.execution.proof_commits import digest


_LOCATION = re.compile(r'\(line \d+(?: of "[^"\n]*")?\)')
_DETERMINISTIC = (
    ('undefined_fact', 'Undefined fact'),
    ('undefined_constant', 'Undefined constant'),
    ('type_error', 'Type unification failed'),
    ('syntax_error', 'Outer syntax error'),
    ('proof_method_failed', 'Failed to apply'),
    ('residual_goals', 'Failed to finish proof'),
)
_NOTICE = ('Advisory rendered-feedback comparison only. Same text is not semantic '
           'equivalence; changed text is not certified progress. No accepted prefix '
           'or GREEN follows from partial execution. Read failure receipts for full evidence.')


def _observation(record):
    check = record.get('check', {})
    if not isinstance(check, dict):
        return {'kind': 'unknown', 'reason': 'missing_check'}
    if check.get('failure_scope'):
        return {'kind': 'infrastructure_or_scoped_failure',
                'reason': check['failure_scope']}
    if check.get('timed_out') or check.get('returncode') == 124:
        return {'kind': 'timeout', 'reason': 'timeout_is_not_a_residual_obligation'}
    diagnostic = check.get('diagnostic')
    if not isinstance(diagnostic, str) or not diagnostic:
        return {'kind': 'unknown', 'reason': 'missing_diagnostic'}
    if check.get('diagnostic_excerpt') or check.get('diagnostic_excerpt_incomplete'):
        return {'kind': 'unknown', 'reason': 'incomplete_diagnostic'}
    if check.get('verdict') != 'red' or check.get('returncode') != 1:
        return {'kind': 'unknown', 'reason': 'not_a_deterministic_red'}
    kind = next((kind for kind, text in _DETERMINISTIC if text in diagnostic), None)
    if kind is None:
        return {'kind': 'unknown', 'reason': 'unclassified_diagnostic'}
    # Only generated source location wrappers are normalized. Mathematical
    # strings, variable names, command text, goal order and whitespace survive.
    observed = {'diagnostic': _LOCATION.sub('(source location)', diagnostic)}
    native = check.get('native_proof_state_full')
    if isinstance(native, dict) and native.get('origin') != 'unavailable':
        goals, assumptions = native.get('goals'), native.get('local_assumptions', [])
        if (not isinstance(goals, list) or not isinstance(assumptions, list)
                or any(not isinstance(s, str) or '...' in s or '…' in s
                       for s in [*goals, *assumptions])):
            return {'kind': 'unknown', 'reason': 'incomplete_native_state'}
        observed['native'] = {'goals': goals, 'local_assumptions': assumptions,
                              'command': native.get('command')}
    if kind in {'residual_goals', 'proof_method_failed'} and ('...' in diagnostic or '…' in diagnostic):
        return {'kind': 'unknown', 'reason': 'incomplete_diagnostic'}
    return {'kind': kind, 'signature': digest(observed)}


def _changes(current, previous):
    new, old = current.get('proof'), previous.get('proof')
    if not isinstance(new, str) or not isinstance(old, str):
        return {'candidate_changed': None, 'changed_line_spans': [],
                'change_detail': 'proof_unavailable'}
    spans = [dict(kind=tag, previous_start=a+1, previous_end_exclusive=b+1,
                  current_start=c+1, current_end_exclusive=d+1)
             for tag, a, b, c, d in SequenceMatcher(
                 None, old.splitlines(), new.splitlines(), autojunk=False).get_opcodes()
             if tag != 'equal']
    return {'candidate_changed': new != old, 'changed_line_spans': spans[:16],
            'changed_span_count': len(spans), 'changed_spans_complete': len(spans) <= 16,
            'line_numbering': '1-based proof-body lines; empty ranges are insertions/deletions'}


def compare(current, previous, *, include_changes=True):
    """Compare two full receipts without changing retry policy or acceptance."""
    observation, prior = _observation(current), _observation(previous)
    result = {'status': 'unknown', 'current_kind': observation['kind'],
              'previous_kind': prior['kind'], 'notice': _NOTICE}
    if include_changes:
        result.update(_changes(current, previous))
    context, old_context = current.get('obstacle_context'), previous.get('obstacle_context')
    if (not isinstance(context, dict) or not isinstance(old_context, dict)
            or not {'entry', 'imports', 'prefix'} <= context.keys()
            or not {'entry', 'imports', 'prefix'} <= old_context.keys()):
        return {**result, 'reason': 'context_binding_unavailable'}
    if digest(context) != digest(old_context):
        return {**result, 'reason': 'context_changed'}
    if 'signature' not in observation or 'signature' not in prior:
        return {**result, 'reason': 'observations_not_comparable',
                'current_detail': observation.get('reason'),
                'previous_detail': prior.get('reason')}
    result['status'] = ('same_observation' if observation == prior else 'changed_observation')
    return result


def history_view(store, latest_id, *, limit=8):
    """Read a bounded verified receipt chain; legacy context/order stays unknown."""
    if type(limit) is not int or not 1 <= limit <= 32:
        raise ValueError('obstacle history limit must be between 1 and 32')
    from regen.planning.sketch_workflow import _read, _diagnosis
    rows, seen, key = [], set(), latest_id
    while key is not None and len(rows) < limit:
        if key in seen:
            raise ValueError('cyclic obstacle failure history')
        seen.add(key)
        record = _read(store, 'failures', key)
        if rows and record['entry']['id'] != rows[0][1]['entry']['id']:
            raise ValueError('obstacle history crosses target entries')
        rows.append((key, record))
        key = record.get('previous_failure_id')
    latest = rows[0][1]
    recent = []
    repeats = 1 if 'signature' in _observation(latest) else 0
    for index, (identity, record) in enumerate(rows):
        if index and compare(latest, record, include_changes=False)['status'] == 'same_observation':
            repeats += 1
        row = {'failure_id': identity, 'entry_id': record['entry']['id'],
               'kind': _observation(record)['kind'],
               'proof_sha256': digest(record['proof']) if isinstance(record.get('proof'), str) else None,
               'diagnosis': _diagnosis(store, identity),
               'read_action': {'action': 'read_failure', 'failure_id': identity}}
        if index + 1 < len(rows):
            row['comparison_to_previous'] = compare(
                record, rows[index+1][1], include_changes=index == 0)
            row['previous_failure_id'] = rows[index+1][0]
        recent.append(row)
    result = {'entry_id': latest['entry']['id'], 'latest_failure_id': latest_id,
              'recent_failures': recent, 'observed_failure_count': len(rows),
              'same_observation_count_in_window': repeats,
              'window_limit': limit, 'older_failure_id': key,
              'history_complete': key is None and 'previous_failure_id' in rows[-1][1],
              'notice': _NOTICE}
    if len(rows) > 1:
        result['latest_comparison'] = recent[0]['comparison_to_previous']
        result['previous_diagnosis'] = _diagnosis(store, rows[1][0])
    else:
        result['latest_comparison'] = {'status': 'unknown', 'reason': 'no_linked_predecessor'}
    return result
