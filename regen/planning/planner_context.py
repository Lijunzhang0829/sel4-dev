"""A smaller model-facing projection; authoritative state and artifacts stay intact.

Only identical complete objects share an in-input reference. Statements, contexts
and proof bodies are never shortened, and navigation never replaces the sole
visible copy of an attempt's diagnostic.
"""
from copy import deepcopy
import json


def project_context(payload):
    """Deduplicate explicit evidence views without changing their mathematical text."""
    out = deepcopy(payload)
    seen = {}
    attempts = {}
    references = 0

    def canonical(kind, value, path, transform=None):
        nonlocal references
        if not isinstance(value, (dict, list)) or not value or (isinstance(value, dict) and 'reference' in value):
            return deepcopy(value)
        identity = (kind, json.dumps(value, ensure_ascii=False, sort_keys=True))
        if identity in seen:
            references += 1
            result = {'reference': seen[identity]}
            for field in ('record_id', 'id', 'request_number', 'status', 'query_id'):
                if isinstance(value, dict) and field in value:
                    result[field] = deepcopy(value[field])
            return result
        seen[identity] = path
        return transform(value, path) if transform else deepcopy(value)

    def fact(value, path):
        return canonical('fact', value, path)

    def proof(value, path):
        return canonical('accepted_proof', value, path)

    def feedback(value, path):
        def contents(original, location):
            result = deepcopy(original)
            if isinstance(original.get('records'), list):
                result['records'] = [fact(x, f'{location}.records[{i}]')
                                     for i, x in enumerate(original['records'])]
            if isinstance(original.get('proofs'), list):
                result['proofs'] = [proof(x, f'{location}.proofs[{i}]')
                                    for i, x in enumerate(original['proofs'])]
            if isinstance(original.get('attempts'), list):
                result['attempts'] = [attempt(x, f'{location}.attempts[{i}]')
                                      for i, x in enumerate(original['attempts'])]
            if isinstance(original.get('queries'), list):
                result['queries'] = [canonical('query', x, f'{location}.queries[{i}]')
                                     for i, x in enumerate(original['queries'])]
            for field, kind in (('diagnostic_page', 'diagnostic_page'),
                                ('proof_state', 'proof_state'), ('symbols', 'symbols')):
                if isinstance(original.get(field), (dict, list)):
                    result[field] = canonical(kind, original[field], location+'.'+field)
            if isinstance(original.get('observations'), list):
                result['observations'] = []
                for i, item in enumerate(original['observations']):
                    entry = deepcopy(item)
                    if isinstance(item, dict) and isinstance(item.get('result'), dict):
                        entry['result'] = feedback(item['result'], f'{location}.observations[{i}].result')
                    result['observations'].append(entry)
            return result
        return canonical('feedback', value, path, contents)

    def attempt(value, path):
        def contents(original, location):
            result = deepcopy(original)
            if isinstance(original.get('candidate'), dict):
                result['candidate'] = canonical('candidate', original['candidate'], location+'.candidate')
            if isinstance(original.get('feedback'), dict):
                result['feedback'] = feedback(original['feedback'], location+'.feedback')
            return result
        if isinstance(value, dict) and isinstance(value.get('candidate'), dict) and isinstance(value.get('feedback'), dict):
            attempts.setdefault(value.get('request_number'), []).append((path, value))
        return canonical('attempt', value, path, contents)

    # Stable canonical locations keep the normal reading path short. Object
    # equality includes provenance, statements and contexts, not just IDs.
    if isinstance(out.get('recalled_facts'), list):
        out['recalled_facts'] = [fact(x, f'recalled_facts[{i}]')
                                 for i, x in enumerate(payload['recalled_facts'])]
    if isinstance(out.get('requested_accepted_proofs'), dict):
        out['requested_accepted_proofs'] = {
            key: proof(value, 'requested_accepted_proofs['+json.dumps(key, ensure_ascii=False)+']')
            for key, value in payload['requested_accepted_proofs'].items()}
    for plural, singular, kind in (
            ('diagnostic_pages', 'diagnostic_page', 'diagnostic_page'),
            ('requested_proof_states', 'requested_proof_state', 'proof_state'),
            ('symbol_observations', 'resolved_symbols', 'symbols')):
        if isinstance(payload.get(plural), list):
            out[plural] = [canonical(kind, x, f'{plural}[{i}]') for i, x in enumerate(payload[plural])]
        if isinstance(payload.get(singular), (dict, list)):
            out[singular] = canonical(kind, payload[singular], singular)
    if isinstance(out.get('last_proof_feedback'), dict):
        out['last_proof_feedback'] = attempt(payload['last_proof_feedback'], 'last_proof_feedback')
    if isinstance(out.get('requested_attempts'), list):
        out['requested_attempts'] = [attempt(x, f'requested_attempts[{i}]')
                                    for i, x in enumerate(payload['requested_attempts'])]
    if isinstance(out.get('last_candidate'), dict):
        out['last_candidate'] = canonical('candidate', payload['last_candidate'], 'last_candidate')
    for field in ('query_memory', 'requested_queries'):
        if isinstance(out.get(field), list):
            out[field] = [canonical('query', x, f'{field}[{i}]') for i, x in enumerate(payload[field])]
    if isinstance(out.get('last_feedback'), dict):
        out['last_feedback'] = feedback(payload['last_feedback'], 'last_feedback')

    if isinstance(out.get('target_memory'), dict):
        for node_id, memory in payload['target_memory'].items():
            if not isinstance(memory, dict):
                continue
            for path, full in attempts.get(memory.get('request_number'), []):
                proofs = full['candidate'].get('proofs', {})
                observed = full['feedback']
                text = observed.get('check', {}).get('diagnostic', observed.get('diagnostic', ''))
                preview = memory.get('diagnostic_excerpt', '')
                # Request identity alone is insufficient for a malformed view.
                # Preserve an unmatched preview rather than hide unique evidence.
                if (isinstance(proofs, dict) and node_id in proofs
                        and isinstance(text, str) and isinstance(preview, str)
                        and text.startswith(preview)):
                    out['target_memory'][node_id] = {
                        'request_number': memory['request_number'],
                        'status': memory.get('status'), 'reference': path}
                    references += 1
                    break

    def navigation(item):
        if not isinstance(item, dict):
            return deepcopy(item)
        fields = ('request_number', 'action', 'status', 'item_index', 'node_ids',
                  'proof_node_ids', 'returned_record_ids', 'query_identity',
                  'candidate_signature', 'failure_signature', 'cost_seconds',
                  'node_results')
        result = {key: deepcopy(item[key]) for key in fields if key in item}
        if isinstance(item.get('request_number'), int):
            result['read_action'] = {'action': 'read_attempts',
                                     'request_numbers': [item['request_number']]}
        if isinstance(item.get('observations'), list):
            result['observations'] = [navigation(x) for x in item['observations']]
        return result

    if isinstance(out.get('recent_actions'), list):
        out['recent_actions'] = [navigation(x) for x in payload['recent_actions']]
    if references or 'context_projection_notice' in out:
        out['context_projection_notice'] = (
            'reference points to an already included object at that input path; follow it without another read. '
            'Identical evidence is expanded once. recent_actions is navigation; read_attempts retrieves bound '
            'original candidate/feedback, and read_queries retrieves indexed query details. '
            'Target previews without a reference remain explicitly incomplete when marked.')
    return out
