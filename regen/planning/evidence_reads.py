"""Normalize public evidence recovery into existing read-only memory operations.

This adapter has no database, checker, or journal access. It preserves exact IDs
and leaves existence/authority checks and memory updates to the existing readers.
"""

EVIDENCE_INSTRUCTIONS = '''Use query to find new candidates; use read_evidence to recover known evidence.
{"action":"read_evidence","refs":[{"kind":"fact","id":"previously recalled record ID"},
{"kind":"query","id":"saved query ID"},{"kind":"proof","id":"accepted local entry ID"}]}
Mixed refs are allowed: at most 24 total, at most 4 queries and 4 proofs. Copy exact
IDs from returned evidence or its index. fact returns the complete recalled fact;
query returns the saved query summary; proof returns an accepted local declaration,
context and proof body. Recovery does not rerun retrieval or prove applicability.
{"action":"read_evidence","index":"facts","offset":0,"limit":32}
Use index="facts" or "queries" to page known addresses, then recover chosen refs.
Add entry_id to an index request to browse retrieval history for that target.
Follow next_action/snapshot_sha256 for subsequent pages. Index reads do not return
the complete statements. refs and index are mutually exclusive.'''

_KINDS = {'fact': ('read_facts', 'record_ids'),
          'query': ('read_queries', 'query_ids'),
          'proof': ('read_proofs', 'node_ids')}
_LIMITS = {'fact': 24, 'query': 4, 'proof': 4}


def evidence_schema():
    """Fresh JSON schema for the public action; includes mixed-ref count limits."""
    identity = {'type': 'string', 'minLength': 1, 'pattern': r'\S'}
    action = {'const': 'read_evidence'}
    refs = {'type': 'array', 'minItems': 1, 'maxItems': 24, 'uniqueItems': True,
        'items': {'type': 'object', 'properties': {
            'kind': {'enum': list(_KINDS)}, 'id': identity},
            'required': ['kind', 'id'], 'additionalProperties': False},
        'allOf': [{'contains': {'type': 'object', 'properties': {'kind': {'const': kind}},
                              'required': ['kind']}, 'minContains': 0, 'maxContains': 4}
                  for kind in ('query', 'proof')]}
    return {'type': 'object', 'oneOf': [
        {'type': 'object', 'properties': {'action': action, 'refs': refs},
         'required': ['action', 'refs'], 'additionalProperties': False},
        {'type': 'object', 'properties': {'action': action,
            'index': {'enum': ['facts', 'queries']},
            'offset': {'type': 'integer', 'minimum': 0},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
            'entry_id': identity,
            'snapshot_sha256': identity},
         'required': ['action', 'index'], 'additionalProperties': False},
    ]}


def evidence_reads(proposal):
    """Return independent legacy read requests without executing any of them.

    Kind groups follow their first occurrence; exact IDs retain their input order
    within each group. The caller executes groups against one accepted snapshot.
    Index requests retain optional pagination fields for the existing page reader.
    """
    if not isinstance(proposal, dict) or proposal.get('action') != 'read_evidence':
        raise ValueError('evidence recovery requires action=read_evidence')
    if ('refs' in proposal) == ('index' in proposal):
        raise ValueError('read_evidence requires exactly one of refs or index')
    if 'index' in proposal:
        if set(proposal) - {'action', 'index', 'offset', 'limit', 'snapshot_sha256', 'entry_id'}:
            raise ValueError('read_evidence index accepts only action, index, offset, limit, snapshot_sha256 and entry_id')
        if proposal['index'] not in ('facts', 'queries'):
            raise ValueError('read_evidence index must be facts or queries')
        offset, limit = proposal.get('offset', 0), proposal.get('limit', 32)
        if type(offset) is not int or offset < 0:
            raise ValueError('read_evidence offset must be a nonnegative integer')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('read_evidence limit must be an integer between 1 and 100')
        if 'snapshot_sha256' in proposal and not _identity(proposal['snapshot_sha256']):
            raise ValueError('read_evidence snapshot_sha256 must be a nonempty string')
        if 'entry_id' in proposal and not _identity(proposal['entry_id']):
            raise ValueError('read_evidence entry_id must be a nonempty exact string ID')
        return [{**proposal, 'action': 'read_context_index'}]
    if set(proposal) != {'action', 'refs'}:
        raise ValueError('read_evidence refs accepts only action and refs')
    refs = proposal['refs']
    if not isinstance(refs, list) or not 1 <= len(refs) <= 24:
        raise ValueError('read_evidence refs must contain 1 to 24 references')
    groups, seen = {}, set()
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {'kind', 'id'}:
            raise ValueError('each evidence ref must contain exactly kind and id')
        kind, identity = ref['kind'], ref['id']
        if not isinstance(kind, str) or kind not in _KINDS:
            raise ValueError('evidence ref kind must be fact, query or proof')
        if not _identity(identity):
            raise ValueError('evidence ref id must be a nonempty exact string ID')
        key = (kind, identity)
        if key in seen:
            raise ValueError('duplicate evidence ref: ' + kind + ' ' + identity)
        seen.add(key)
        groups.setdefault(kind, []).append(identity)
        if len(groups[kind]) > _LIMITS[kind]:
            raise ValueError(f'read_evidence supports at most {_LIMITS[kind]} {kind} refs')
    return [{'action': _KINDS[kind][0], _KINDS[kind][1]: ids}
            for kind, ids in groups.items()]


def _identity(value):
    return isinstance(value, str) and bool(value.strip())
