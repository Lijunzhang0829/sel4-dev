"""Model-facing views; snapshots are disposable, never acceptance authority."""
from copy import deepcopy
from regen.execution.proof_commits import digest

SECTIONS = ('workflow', 'observations', 'planning_review', 'planning_advice', 'feedback')


def project(value, *, view='summary', section=None):
    if view not in ('summary', 'full') or section not in (None, *SECTIONS):
        raise ValueError('invalid theory view or section')
    out = deepcopy(value)
    theory = value['theory']
    if section:
        body = value.get('document', {}).get(section) if section == 'workflow' else value.get(section)
        return {'theory': theory, 'section': section, 'content': deepcopy(body),
                'context_revision': value.get('context_revision'),
                'source_sha256': value.get('document', {}).get('source_sha256')}
    if view == 'full':
        return out
    for name in SECTIONS:
        container = out.get('document', {}) if name == 'workflow' else out
        if name not in container:
            continue
        body = container[name]
        # Keep current check feedback intact: diagnosis is actionable, not history.
        if name == 'feedback':
            continue
        navigation = {'available': True, 'content_sha256': digest(body),
            'read': {'tool': 'read_theory', 'arguments': {'theory': theory, 'section': name}}}
        if name == 'workflow' and isinstance(body, dict):
            navigation['entries'] = {key: {field: deepcopy(row[field]) for field in
                ('entry_id', 'status', 'missing_prerequisites', 'reason', 'code') if field in row}
                for key, row in body.get('readiness', {}).get('entries', {}).items()}
            navigation['pending_failures'] = [{k: deepcopy(row[k]) for k in
                ('entry_id', 'failure_id', 'active_in_draft', 'route_withdrawn') if k in row}
                for row in body.get('pending_failures', [])]
        if name == 'planning_review' and isinstance(body, dict):
            navigation['obligations'] = [{k: deepcopy(v) for k, v in row.items()
                if k in {'issue_id', 'requires_generation_decision', 'generation_status',
                         'pending_contract_ids', 'completed_contract_ids', 'disposed_contract_ids',
                         'decision_invalid_reason'}} for row in body.get('obligations', [])]
        if name == 'observations' and isinstance(body, dict):
            for key in ('progress_signals', 'repeated_failed_routes', 'next_proof_observation'):
                if key in body:
                    navigation[key] = deepcopy(body[key])
        container[name] = navigation
    return out


def incremental(controller, value, arguments):
    """Top-level delta: changed nested objects are replaced in full, never merged.

    The cursor binds the complete projected state, not only the source hash.
    A new session or a stale/foreign cursor receives a complete replacement.
    """
    view, section = arguments.get('view', 'summary'), arguments.get('section')
    if arguments.get('since_cursor') and arguments.get('if_source_sha256'):
        raise ValueError('use since_cursor or if_source_sha256, not both')
    projected = project(value, view=view, section=section)
    binding = {'run': controller.run['run_id'], 'theory': controller.theory,
               'view': view, 'section': section, 'schema': 'workspace-view-v1'}
    cursor = digest({'binding': binding, 'state': projected})
    key = (view, section)
    cache = getattr(controller, '_model_view_snapshots', {})
    old = cache.get(key)
    requested = arguments.get('since_cursor')
    if requested is not None and (not isinstance(requested, str) or not requested):
        raise ValueError('since_cursor must be a nonempty cursor')
    if requested and old and old[0] == requested:
        before = old[1]
        result = {'view_mode': 'delta', 'cursor': cursor,
                  'changed': {k: v for k, v in projected.items() if k not in before or before[k] != v},
                  'removed': sorted(set(before)-set(projected)),
                  'unchanged': cursor == requested,
                  'delta_semantics': 'Replace changed top-level values; remove removed keys. Null is an explicit value.'}
    else:
        result = {**projected, 'view_mode': 'snapshot', 'cursor': cursor,
                  'cursor_reset': bool(requested), 'view_schema': 'workspace-view-v1'}
    cache[key] = (cursor, deepcopy(projected))
    controller._model_view_snapshots = cache
    return result
