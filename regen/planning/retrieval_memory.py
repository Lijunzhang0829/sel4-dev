"""Read-only target retrieval memory, derived from existing observation receipts.

A remembered query associates candidates with an entry, not with a valid proof
context. In particular, this view never restores caches retired by an import
revision or infers a target for global queries.
"""
from copy import deepcopy


DEFAULT_LIMIT = 8
MAX_LIMIT = 24


def _target(request):
    """Accept either protocol vocabulary, without guessing an ambiguous target."""
    if not isinstance(request, dict):
        return None
    identities = [request[key] for key in ('context_node_id', 'context_entry_id')
                  if key in request]
    if (not identities or any(not isinstance(value, str) or not value for value in identities)
            or len(set(identities)) != 1):
        return None
    return identities[0]


def _entry_id(entry_id):
    if not isinstance(entry_id, str) or not entry_id.strip():
        raise ValueError('retrieval memory requires a nonempty entry_id')


def _queries(state):
    from regen.planning.batch_file_planner import observation_summary
    from regen.planning.observation_memory import memory_value
    queries, _ = observation_summary(state)
    orders = {row['query_id']: row.get('observation_order')
              for row in [*state.get('retrieval_history_queries', []), *memory_value(state, 0, [])]}
    recorded_orders = state.get('batch_observation_orders', {})

    def recent(query):
        order = orders.get(query['query_id'])
        numbers = [n for n in query.get('requests', []) if type(n) is int]
        number = max(numbers, default=0)
        if order is None:
            order = recorded_orders.get(str(number))
        if isinstance(order, (list, tuple)) and len(order) == 3:
            return tuple(order)
        return (number, 0, query['query_id'])

    return sorted(queries, key=recent, reverse=True)


def _selection(state, entry_id):
    _entry_id(entry_id)
    all_queries = _queries(state)
    queries = [query for query in all_queries if _target(query.get('request')) == entry_id]
    global_count = sum(_target(query.get('request')) is None for query in all_queries)
    facts = {record['record_id']: record for record in state.get('batch_facts', [])}
    references = {}
    # Query recency comes first, then the unchanged ranking within each result.
    for query in queries:
        for record_id in query.get('returned_record_ids', []):
            references.setdefault(record_id, []).append(query['query_id'])
    return queries, references, facts, global_count


def retrieval_index_context(state, entry_id):
    """Supply target-filtered indexes to the existing snapshot-bound pager.

    Only IDs backed by a current stored full record are offered to read_facts.
    Historical queries can remain navigable after their fact cache was retired.
    No full statement or proof is abbreviated in an index.
    """
    queries, references, facts, _ = _selection(state, entry_id)
    return {
        'recalled_fact_index': [{key: deepcopy(value) for key, value in facts[record_id].items()
            if key in {'record_id', 'canonical_name', 'source_class', 'theory'}}
            for record_id in references if record_id in facts],
        'query_index': [{'query_id': query['query_id'], 'context_node_id': entry_id}
                        for query in queries],
    }


def entry_retrieval_memory(state, entry_id, *, limit=DEFAULT_LIMIT,
                           current_entry=None, directory=None):
    """Restore a bounded number of complete candidates for an explicit target.

    ``limit`` bounds each displayed collection's record count, never proposition
    length. Further items remain available through read_evidence indexes. This
    view does not track what a prior model session happened to have seen.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f'retrieval memory limit must be an integer from 1 to {MAX_LIMIT}')
    queries, references, facts, global_count = _selection(state, entry_id)
    revision = state.get('context_revision', 0)
    available_ids = [record_id for record_id in references if record_id in facts]
    from regen.planning.retrieval_usage import binding_status, entry_usage_evidence

    def index_action(kind):
        return {'action': 'read_evidence', 'index': kind, 'entry_id': entry_id,
                'offset': 0, 'limit': limit}

    selected_queries = []
    for query in queries[:limit]:
        # observation_summary already excludes private cache paths. Keep only
        # the documented query receipt metadata, including complete requests.
        selected = {key: deepcopy(query[key]) for key in (
            'query_id', 'request', 'requests', 'task_ids', 'identity_bound',
            'last_status', 'returned_record_ids', 'query_binding', 'returned_candidates') if key in query}
        binding = query.get('query_binding')
        context = binding_status(binding, state, entry_id, current_entry)
        selected.update(
            query_context_revision=binding.get('context_revision') if isinstance(binding, dict) else None,
            context_status=context['status'],
            context_comparison=context,
            is_proof_authority=False,
            read_action={'action': 'read_evidence',
                         'refs': [{'kind': 'query', 'id': query['query_id']}]},
        )
        selected_queries.append(selected)
    selected_facts = []
    for record_id in available_ids[:limit]:
        record = deepcopy(facts[record_id])
        remembered_availability = record.get('availability')
        related = [query for query in queries if query['query_id'] in references[record_id]]
        latest_binding = related[0].get('query_binding') if related else None
        record.update(
            availability='requires_current_context_check',
            is_proof_authority=False,
            memory_context={
                'current_context_revision': revision,
                'query_context_revision': (latest_binding.get('context_revision')
                                           if isinstance(latest_binding, dict) else None),
                'query_ids': list(references[record_id][:limit]),
                'query_total': len(related),
                'has_more_queries': len(related) > limit,
                'query_index_action': index_action('queries'),
                'query_contexts': [{'query_id': query['query_id'],
                    'binding': deepcopy(query.get('query_binding')),
                    'comparison': binding_status(query.get('query_binding'), state, entry_id, current_entry)}
                    for query in related[:limit]],
                'retrieved_availability': remembered_availability,
                'notice': 'A target-bound historical query returned this candidate; '
                          'this read has not revalidated its visibility or applicability.',
            },
            read_action={'action': 'read_evidence',
                         'refs': [{'kind': 'fact', 'id': record_id}]},
        )
        selected_facts.append(record)

    return {
        'schema_version': 1, 'entry_id': entry_id, 'context_revision': revision,
        'is_proof_authority': False,
        'queries': selected_queries, 'facts': selected_facts,
        'usage_evidence': entry_usage_evidence(state, entry_id, queries, directory=directory,
                                               current_entry=current_entry, attempt_limit=limit),
        'totals': {'queries': len(queries), 'facts': len(available_ids),
                   'referenced_fact_ids': len(references),
                   'unavailable_fact_records': len(references) - len(available_ids)},
        'display_limit': limit,
        'has_more': {'queries': len(queries) > limit, 'facts': len(available_ids) > limit},
        'continuation': {'queries': index_action('queries'), 'facts': index_action('facts')},
        'global_queries': {'total': global_count,
            'index_action': {'action': 'read_evidence', 'index': 'queries',
                             'offset': 0, 'limit': limit},
            'notice': 'Unattributed queries are counted only; the unfiltered index also '
                      'contains queries explicitly bound to other entries.'},
        'notice': 'Only explicitly target-bound queries are associated with this entry. '
                  'Stored candidates are not current premises and must be checked in the actual context. '
                  'New receipts expose target and context bindings; absent legacy bindings remain unknown. '
                  'Import revisions retire current query/fact caches; retired records are not restored by '
                  'this view. Older evidence remains accessible only through retained historical receipts. '
                  'Limits count complete records, never characters; session restarts do not suppress facts.',
    }
