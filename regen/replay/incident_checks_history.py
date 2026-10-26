"""Replay public historical observations without executing machine-A code.

The oracle predates this replay. Queue components and affected-state intervals
are deliberately separate from callback durations and never become budget credit.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime
import json
import math
from pathlib import Path


ORDINARY_PROOF_CATEGORIES = frozenset({
    'proof_method_failure', 'open_goals', 'type_or_binding',
    'syntax_or_proof_mode', 'unknown_fact', 'unknown_method',
})
AUDIT_CATEGORIES = frozenset({'namespace_fact_lookup', 'audit_empty_target'})


def _load(root: Path, name: str):
    return json.loads((root / name).read_text())


def _near(actual, expected, label):
    if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-5):
        raise ValueError(f'{label}: {actual!r} != historical oracle {expected!r}')


def _intervals(rows):
    return [(r['clipped_start_unix'], r['clipped_end_unix']) for r in rows]


def _union_seconds(intervals):
    total = 0.0
    left = right = None
    for start, end in sorted(intervals):
        if not math.isfinite(start) or not math.isfinite(end) or end < start:
            raise ValueError('invalid historical interval')
        if left is None:
            left, right = start, end
        elif start > right:
            total += right - left
            left, right = start, end
        else:
            right = max(right, end)
    return total + (right - left if left is not None else 0.0)


def _summary(rows):
    intervals = _intervals(rows)
    return {'count': len(rows),
            'worker_sum_seconds': sum(end - start for start, end in intervals),
            'wall_union_seconds': _union_seconds(intervals)}


def _compare(rows, expected, label):
    actual = _summary(rows)
    if actual['count'] != expected['count']:
        raise ValueError(f'{label}: wrong occurrence count')
    for field in ('worker_sum_seconds', 'wall_union_seconds'):
        _near(actual[field], expected[field], f'{label}.{field}')
    return actual


def _validate_rows(rows, oracle):
    ids = [r['id'] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate historical occurrence ID')
    for row in rows:
        window = oracle['by_window'][str(row['window'])]
        start = datetime.fromisoformat(window['start_shanghai']).timestamp()
        end = datetime.fromisoformat(window['end_shanghai']).timestamp()
        clipped_start = max(row['start_unix'], start)
        clipped_end = min(row['end_unix'], end)
        if clipped_end < clipped_start:
            raise ValueError('historical occurrence falls outside its window')
        _near(row['clipped_start_unix'], clipped_start, 'clipped start')
        _near(row['clipped_end_unix'], clipped_end, 'clipped end')
        _near(row['seconds_in_window'], clipped_end - clipped_start, 'clipped duration')
        if (not row['evidence']
                or any(not e.get('path') or (e.get('line') is not None and e['line'] < 1)
                       for e in row['evidence'])
                or not any(e.get('line') is not None for e in row['evidence'])):
            raise ValueError('missing source file/line identity')


def _statistics(rows, queue, oracle):
    _validate_rows(rows, oracle)
    _validate_rows(queue, oracle)
    if set(Counter(r['category'] for r in rows)) != set(oracle['by_category']):
        raise ValueError('historical category set changed')
    if dict(Counter(r['kind'] for r in rows)) != oracle['kind_counts']:
        raise ValueError('failed callbacks/provider failures/observations conflated')
    categories = {}
    for category, expected in oracle['by_category'].items():
        categories[category] = _compare([r for r in rows if r['category'] == category],
                                        expected, category)
    failures = [r for r in rows if r['kind'] != 'performance_observation']
    _compare(rows, oracle['totals'], 'all occurrences')
    _compare(failures, oracle['failures'], 'failures')
    _compare(queue, oracle['native_queue_component'], 'queue component')
    windows = {}
    for window_id, expected in oracle['by_window'].items():
        selected = [r for r in rows if str(r['window']) == window_id]
        windows[window_id] = _compare(selected, expected['all_records'], 'window ' + window_id)
        _compare([r for r in selected if r['kind'] != 'performance_observation'],
                 expected['failures'], 'window failures ' + window_id)
        _compare([r for r in selected if r['category'] == 'slow_retrieval'],
                 expected['slow_retrieval'], 'window retrieval ' + window_id)
        _compare([r for r in queue if str(r['window']) == window_id],
                 expected['native_queue_component'], 'window queue ' + window_id)
        for category, category_expected in expected['categories'].items():
            _compare([r for r in selected if r['category'] == category],
                     category_expected, f'window {window_id}.{category}')
    return categories, windows


def _must_reject(call):
    try:
        call()
    except ValueError:
        return
    raise AssertionError('corrupted historical statistics accepted')


def public_event_statistics(case, fixture_root: Path, scratch: Path):
    p = case['_replay']['parameters']
    rows = [json.loads(line) for line in (fixture_root / p['occurrences']).read_text().splitlines()]
    oracle = _load(fixture_root, p['summary'])
    queue = _load(fixture_root, p['queue'])
    blocked = _load(fixture_root, p['blocked'])
    auto_continue = _load(fixture_root, p['auto_continue'])
    budget = _load(fixture_root, p['budget'])
    categories, windows = _statistics(rows, queue, oracle)
    assert len(rows) == 770
    assert oracle['kind_counts'] == {'performance_observation': 108,
                                     'failed_callback': 659, 'provider_failure': 3}
    ordinary = [r for r in rows if r['category'] in ORDINARY_PROOF_CATEGORIES]
    audit = [r for r in rows if r['category'] in AUDIT_CATEGORIES]
    assert len(ordinary) == 407 and len(audit) == 21
    assert ORDINARY_PROOF_CATEGORIES.isdisjoint(AUDIT_CATEGORIES)
    # Proof-body fact errors and post-check namespace audit errors are distinct.
    assert categories['unknown_fact']['count'] == 18
    assert categories['namespace_fact_lookup']['count'] == 19
    assert len(blocked) == oracle['blocked_state_count'] == 4
    assert len(auto_continue) == oracle['auto_continuation_observation_count'] == 4
    for name, records, role in [
        ('blocked', blocked, 'affected_state_or_route_span_including_other_work'),
        ('auto_continue', auto_continue, 'notification_to_auto_continuation_latency_only'),
    ]:
        _validate_rows(records, oracle)
        for row in records:
            assert row['duration_role'] == role, name
            assert row['includes_other_work'] and row['not_causal_wasted_time'], name
    queue_ids, issue_ids = {r['id'] for r in queue}, {r['id'] for r in rows}
    assert queue_ids.isdisjoint(issue_ids)
    # The output intentionally has no total created by adding these components.
    # Category wall unions overlap, so summing them is also not a global clock.
    category_sum = sum(v['wall_union_seconds'] for v in categories.values())
    assert category_sum > oracle['totals']['wall_union_seconds']
    assert budget['state']['excluded_infrastructure_seconds'] == 0.0
    assert budget['state']['excluded_infrastructure_worker_seconds'] == 0.0
    assert budget['state']['seconds'] == 172800.0
    # Independent corruption probes ensure the reconstruction is not a count-only read.
    _must_reject(lambda: _statistics(rows + [rows[0]], queue, oracle))
    shifted = deepcopy(rows)
    shifted[0]['clipped_end_unix'] += 1.0
    _must_reject(lambda: _statistics(shifted, queue, oracle))
    conflated = deepcopy(rows)
    next(r for r in conflated if r['category'] == 'unknown_fact')['category'] = 'namespace_fact_lookup'
    _must_reject(lambda: _statistics(conflated, queue, oracle))
    result = {
        'machine_scope': 'A historical', 'occurrence_count': len(rows),
        'distinct_bug_count': None, 'kind_counts': oracle['kind_counts'],
        'ordinary_proof_failures': len(ordinary), 'post_check_audit_symptoms': len(audit),
        'categories': categories, 'windows': windows,
        'native_queue_components': _summary(queue),
        'blocked_state_spans': _summary(blocked),
        'auto_continue_notifications': _summary(auto_continue),
        'cost_addition_policy': 'components and affected-state durations are not additive charges',
        'budget_credit_computed': False, 'current_machine_a_implementation_tested': False,
        'native_invocations': 0, 'model_invocations': 0,
        'corruption_probes_rejected': 3,
    }
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / 'historical-statistics.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


HANDLERS = {'history.public_event_statistics': public_event_statistics}
