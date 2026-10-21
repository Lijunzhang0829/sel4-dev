"""Lightweight, restart-safe document navigation derived from accepted history.

This does not modify persisted selections. Legacy checkpoints may still contain
full selected drafts; they are summarized on output, not rewritten or discarded.
"""
from copy import deepcopy

from regen.planning.attempt_views import history_row, selected_attempt_summaries
from regen.planning.planner_context import project_context

INDEX_PAGE_SIZE = 32
INDEX_FIELDS = {'facts':'recalled_fact_index', 'queries':'query_index'}


def context_index_page(context, proposal):
    """Page navigation addresses, never abbreviate a proposition or proof body."""
    from regen.execution.proof_commits import digest
    if set(proposal)-{'action','index','offset','limit','snapshot_sha256'}:
        raise ValueError('read_context_index accepts index, offset, limit and snapshot_sha256')
    kind = proposal.get('index')
    if kind not in INDEX_FIELDS:
        raise ValueError('context index must be facts or queries')
    offset,limit = proposal.get('offset',0),proposal.get('limit',INDEX_PAGE_SIZE)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('index offset must be nonnegative and limit must be 1..100')
    records = context.get(INDEX_FIELDS[kind],[])
    fields = (('record_id','canonical_name','source_class','theory') if kind=='facts'
              else ('query_id','context_node_id'))
    records = [{key:deepcopy(value) for key,value in row.items() if key in fields} for row in records]
    snapshot = digest({'index':kind,'records':records})
    if proposal.get('snapshot_sha256',snapshot) != snapshot:
        raise ValueError('context index snapshot changed; restart at offset 0')
    end = min(offset+limit,len(records))
    has_more = end < len(records)
    return {'status':'available','index':kind,'records':records[offset:end],
        'total':len(records),'offset':offset,'limit':limit,'has_more':has_more,
        'next_offset':end if has_more else None,'snapshot_sha256':snapshot,
        'next_action':({'action':'read_context_index','index':kind,'offset':end,
                        'limit':limit,'snapshot_sha256':snapshot} if has_more else None),
        'notice':'Navigation only, not proof authority. read_facts/read_queries retrieve the '
                 'complete evidence identified here; no proposition or proof body is shortened.'}


def retry_summary(results, theory):
    from regen.planning.attempt_views import excerpt
    previous = next((row for row in reversed(results) if row.get('theory') == theory
                     and row.get('retry_scheduled')), None)
    if previous is None:
        return None
    return {'previous_error_type': previous.get('error_type'),
            'diagnostic': excerpt(previous.get('diagnostic'), 2000),
            'recovery': 'Read the current draft and its lightweight observation indexes. Accepted proofs and charged costs remain unchanged. Necessary evidence may be read again in this new session; do not reconstruct context by scanning all historical request numbers.'}


def compact_history_feedback(value, state):
    from regen.planning.batch_file_planner import compact_feedback
    out = compact_feedback(value)
    if not isinstance(out, dict):
        return out
    graph = state.get('graph', {})
    if isinstance(out.get('attempts'), list):
        out['attempts'] = selected_attempt_summaries(out['attempts'], state.get('batch_history', []),
            theory=graph.get('theory'), run_id=graph.get('run_id'))
    if isinstance(out.get('observations'), list):
        out['observations'] = [{**item, 'result': compact_history_feedback(item['result'], state)}
            if isinstance(item, dict) and 'result' in item else deepcopy(item)
            for item in out['observations']]
    return out


def document_observations(context, state):
    from regen.planning.batch_file_planner import compact_feedback
    history = state.get('batch_history', [])
    rows = {h['request_number']: h for h in history}
    graph = state.get('graph', {})
    out = {k: compact_feedback(context[k]) for k in (
        'query_memory', 'next_proof_observation', 'requested_proof_state', 'resolved_symbols',
        'requested_proof_states', 'symbol_observations',
        'planner_notes', 'repeated_failed_routes', 'progress_signals') if k in context}
    # The whole growing index is available, but no longer repeated in every
    # source read. Each page carries a content binding and an explicit cursor.
    out['indexes'] = {}
    for kind,field in INDEX_FIELDS.items():
        page = context_index_page(context,{'action':'read_context_index','index':kind})
        out[field] = page.pop('records')
        out['indexes'][kind] = page
    out['requested_attempts'] = selected_attempt_summaries(
        context.get('requested_attempts', []), history,
        theory=graph.get('theory'), run_id=graph.get('run_id'))
    # Deduplicate exact observations inside this one response; reference paths
    # are relative to observations. No cross-session "already read" assumption.
    out = project_context(out)
    out['recent_actions'] = []
    for item in history[-12:]:
        row = history_row(item)
        out['recent_actions'].append({k: v for k, v in row.items() if k not in {
            'submitted_entry_ids', 'proposal_sha256', 'feedback_sha256'}})
        out['recent_actions'][-1]['submitted_entry_count'] = len(row['submitted_entry_ids'])
    out['target_memory'] = {}
    for key, memory in context.get('target_memory', {}).items():
        number = memory['request_number']
        row = history_row(rows[number]) if number in rows else None
        out['target_memory'][key] = {
            'last_containing_request': number,
            'checker_reported_failed': key in row['checker_failed_entry_ids'] if row else None,
            'read_action': {'action': 'read_attempts', 'request_numbers': [number], 'view': 'summary'}}
    out['history_notice'] = (
        'target_memory locates the last batch CONTAINING an entry, not necessarily its failure. '
        'requested_attempts are summaries even after explicit full reads; persisted originals are intact. '
        'Use list_attempts for paginated failed history, and read_attempt/read_attempts view=full '
        'for exact proof/context/feedback. Fact/query indexes include their first page only; '
        'follow indexes.*.next_action through proof_action for all addresses. '
        'References here are relative to observations.')
    return out
