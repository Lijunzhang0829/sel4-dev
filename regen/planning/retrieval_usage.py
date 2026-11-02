"""Evidence about retrieval and proof-text mentions, never premise authority.

All views are derived from existing verified observations or acceptance state.
Token mentions are deliberately not called Isabelle dependencies, applicability
checks, or training negatives. No failed attempt blacklists a fact.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import re

from regen.execution.proof_commits import digest


def indexed_implementation_binding(request):
    """Version new indexed receipts without changing any legacy query identity."""
    from regen.execution.indexed_retrieval import (
        DEFAULT_CANDIDATE_BUDGET, GENERATED_INDEX_VERSION, UPSTREAM_INDEX_VERSION,
    )
    names = ['indexed_retrieval.py', 'proof_state_recall.py', 'file_planner_recall.py']
    if request.get('structural_rerank'):
        names.append('premise_structure.py')
    directory = Path(__file__).resolve().parent.parent / 'execution'
    hashes = {}
    for name in names:
        with (directory / name).open('rb') as stream:
            hashes[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {
        'policy': 'indexed-v1', 'source_sha256': hashes,
        'generated_index_version': GENERATED_INDEX_VERSION,
        'upstream_index_version': UPSTREAM_INDEX_VERSION,
        'candidate_budget': request.get('candidate_budget', DEFAULT_CANDIDATE_BUDGET),
    }


def target_identity(node):
    if not isinstance(node, dict):
        return None
    identity = node.get('id', node.get('entry_id'))
    if not isinstance(identity, str) or not isinstance(node.get('statement'), str):
        return None
    return digest({'id': identity, 'statement': node['statement'], 'context': node.get('context', [])})


def candidate_identity(record):
    """Small historical address; intentionally excludes complete retired facts."""
    result = {key: deepcopy(record[key]) for key in (
        'record_id', 'canonical_name', 'proposition_digest', 'owner_theory',
        'owner_session', 'source_class', 'proof_commit_id', 'theory_seal_id',
        'proof_sha256') if key in record}
    if isinstance(record.get('exact_statement'), str):
        result['statement_sha256'] = digest(record['exact_statement'])
    result['candidate_version_id'] = digest(result)
    return result


def public_query_binding(binding, state):
    """Expose meaningful binding hashes without catalog paths/stat metadata."""
    target = binding.get('context')
    entry_id = target.get('id') if isinstance(target, dict) else None
    entry = next((node for node in state.get('graph', {}).get('nodes', [])
                  if node['id'] == entry_id), None)
    return {
        'schema_version': 1, 'query_identity': digest(binding),
        'run_id': binding.get('run_id'), 'entry_id': entry_id,
        'entry_target_sha256': target_identity(entry),
        'retrieval_target_sha256': target_identity(target),
        'retrieval_target_kind': 'native_residual' if binding.get('observation') else 'entry',
        'context_revision': binding.get('context_revision'),
        'context_imports': deepcopy(binding.get('context_imports')),
        'provider_bindings_sha256': digest(state.get('context_provider_bindings')),
        'accepted_prefix_sha256': digest(binding.get('accepted_prefix')),
        'database_sha256': binding.get('database', {}).get('content_sha256'),
        'database_wal_sha256': binding.get('database_wal', {}).get('content_sha256'),
        'sealed_records_sha256': binding.get('sealed'),
        **({'retrieval_implementation': deepcopy(binding['retrieval_implementation'])}
           if 'retrieval_implementation' in binding else {}),
        'is_proof_authority': False,
    }


def binding_status(binding, state, entry_id, current_entry=None):
    if not isinstance(binding, dict) or binding.get('schema_version') != 1:
        return {'status': 'unknown_legacy_binding', 'differences': []}
    node = current_entry or next((node for node in state.get('graph', {}).get('nodes', [])
                                  if node['id'] == entry_id), None)
    differences = []
    target = target_identity(node)
    recorded_target = binding.get('entry_target_sha256')
    if recorded_target is None or target is None:
        differences.append('target_binding_unknown')
    elif recorded_target != target:
        differences.append('target_changed')
    if binding.get('context_revision') != state.get('context_revision', 0):
        differences.append('context_revision_changed')
    if binding.get('context_imports') != state.get('context_imports'):
        differences.append('imports_changed')
    if binding.get('provider_bindings_sha256') != digest(state.get('context_provider_bindings')):
        differences.append('provider_versions_changed')
    prefix = [(key, digest(state['proofs'][key])) for key in state.get('accepted_order', [])]
    if binding.get('accepted_prefix_sha256') != digest(prefix):
        differences.append('accepted_prefix_changed')
    return {'status': 'historical_changed' if differences else 'same_recorded_target_and_context',
            'differences': differences,
            'notice': 'This comparison does not recheck database bytes or Isabelle applicability.'}


def observation_history(state, rows):
    """Project completed receipts, including old environments, without facts.

    The caller must supply executor-validated receipts for this run and theory.
    Current cache merging remains separate and environment-filtered.
    """
    queries = {q['query_id']: deepcopy(q) for q in state.get('retrieval_history_queries', [])}
    reads = {event['event_id']: deepcopy(event) for event in state.get('retrieval_read_events', [])}

    def visit(request, result, row, path=()):
        if result.get('status') != 'available':
            return
        for index, child in enumerate(result.get('observations', [])):
            visit(child['request'], child['result'], row, (*path, index))
        if result.get('kind') == 'fact_selection' and result.get('query_identity'):
            identity = result['query_identity']
            old = queries.get(identity, {})
            order = [row.get('submission_sequence', 0), row['submitted_at'], row['task_id']]
            task_ids = list(dict.fromkeys([*old.get('task_ids', []), row['task_id']]))
            if order >= old.get('observation_order', []):
                queries[identity] = {
                    'query_id': identity, 'request': deepcopy(request.get('request', {})),
                    'requests': [], 'task_ids': task_ids, 'identity_bound': True,
                    'last_status': 'available', 'observation_order': order,
                    'returned_record_ids': [r['record_id'] for r in result.get('records', [])],
                    'returned_candidates': [candidate_identity(r) for r in result.get('records', [])],
                    **({'query_binding': deepcopy(result['query_binding'])}
                       if isinstance(result.get('query_binding'), dict) else {}),
                }
            else:
                old['task_ids'] = task_ids
        if request.get('action') == 'read_facts':
            event_id = digest({'task_id': row['task_id'], 'path': list(path)})
            reads[event_id] = {
                'event_id': event_id, 'task_id': row['task_id'],
                'observation_order': [row.get('submission_sequence', 0), row['submitted_at'], row['task_id']],
                'record_ids': [r['record_id'] for r in result.get('records', [])],
                'candidates': [candidate_identity(r) for r in result.get('records', [])],
                'context_revision': row.get('result', {}).get('context_revision',
                    row.get('binding', {}).get('context_revision')),
                'result_sha256': row.get('result_sha256'),
                'evidence': 'explicit_fact_read', 'entry_id': None,
                'notice': 'The read did not name a target; do not infer which target motivated it.',
            }

    for row in rows:
        visit(row['request']['proposal'], row['result'], row)
    return (sorted(queries.values(), key=lambda item: item.get('observation_order', [])),
            sorted(reads.values(), key=lambda item: item.get('observation_order', [])))


def _proof_tokens(proof):
    # Drop nested Isabelle comments, retaining only non-comment text. This is
    # still token evidence, not an Isabelle parser or theorem-name resolver.
    chunks, depth, start, index = [], 0, 0, 0
    while index < len(proof):
        if proof.startswith('(*', index):
            if depth == 0:
                chunks.append(proof[start:index])
            depth += 1
            index += 2
        elif depth and proof.startswith('*)', index):
            depth -= 1
            index += 2
            if depth == 0:
                start = index
        else:
            index += 1
    if depth == 0:
        chunks.append(proof[start:])
    return set(re.findall(r"[A-Za-z][A-Za-z0-9_'.]*(?:\([1-9][0-9]*\))?", ' '.join(chunks)))


def proof_mentions(proof, candidates):
    tokens = _proof_tokens(proof)
    shortnames = {}
    for item in candidates:
        name = item.get('canonical_name')
        if isinstance(name, str):
            shortnames.setdefault(name.rsplit('.', 1)[-1], set()).add(name)
    result = []
    seen = set()
    for item in candidates:
        name = item.get('canonical_name')
        if not isinstance(name, str):
            continue
        short = name.rsplit('.', 1)[-1]
        spelling = name if name in tokens else short if short in tokens and len(shortnames[short]) == 1 else None
        identity = item.get('candidate_version_id') or digest(item)
        if spelling and identity not in seen:
            result.append({**deepcopy(item), 'matched_spelling': spelling,
                           'evidence': 'proof_text_token_mention',
                           'native_dependency_confirmed': False,
                           'candidate_version_resolved_by_prover': False})
            seen.add(identity)
    return result


def entry_usage_evidence(state, entry_id, queries, *, directory=None,
                         current_entry=None, attempt_limit=8):
    """Bounded display backed by retained full receipts, never a negative label."""
    candidates = {}
    for query in queries:
        for item in query.get('returned_candidates', []):
            candidates[item.get('candidate_version_id') or digest(item)] = item
    current_ids = {record_id for q in queries for record_id in q.get('returned_record_ids', [])}
    for item in state.get('batch_facts', []):
        if item['record_id'] in current_ids:
            identity = candidate_identity(item)
            candidates[identity['candidate_version_id']] = identity
    returned = [{'query_id': q['query_id'], 'record_ids': list(q.get('returned_record_ids', [])),
                 'query_binding': deepcopy(q.get('query_binding')),
                 'binding_status': binding_status(q.get('query_binding'), state, entry_id, current_entry)}
                for q in queries]
    reads = [deepcopy(event) for event in state.get('retrieval_read_events', [])
             if set(event['record_ids']) & current_ids]
    history = [row for row in state.get('batch_history', [])
               if entry_id in row.get('proof_node_ids', [])]
    attempts, unavailable = [], []
    if directory is not None:
        from regen.planning.planner_workspace import attempt_evidence
        for row in history[-attempt_limit:]:
            number = row['request_number']
            try:
                proposal, feedback = attempt_evidence(directory, state, number)
            except (ValueError, OSError) as error:
                unavailable.append({'request_number': number, 'reason': str(error)})
                continue
            proof = proposal.get('proofs', {}).get(entry_id)
            if not isinstance(proof, str):
                continue
            graph = proposal.get('graph')
            nodes = graph.get('nodes', []) if isinstance(graph, dict) else []
            node = next((node for node in nodes if isinstance(node, dict)
                         and node.get('id') == entry_id), None) if isinstance(nodes, list) else None
            mentions = proof_mentions(proof, candidates.values())
            attempts.append({
                'request_number': number, 'entry_id': entry_id,
                'status': row.get('status'), 'proof_sha256': digest(proof),
                'proposal_sha256': row.get('proposal_sha256'),
                'feedback_sha256': row.get('feedback_sha256'),
                'target_sha256': row.get('attempt_binding', {}).get('target_sha256', {}).get(
                    entry_id, target_identity(node)),
                'context_revision': row.get('attempt_binding', {}).get('context_revision'),
                'attempt_binding': deepcopy(row.get('attempt_binding')),
                'mentions': mentions[:attempt_limit],
                'mentions_total': len(mentions),
                'has_more_mentions': len(mentions) > attempt_limit,
                'is_negative_training_label': False,
                'read_action': {'action': 'read_attempts', 'request_numbers': [number], 'view': 'full'},
            })
    accepted = None
    if entry_id in state.get('proofs', {}) and entry_id in state.get('accepted_order', []):
        proof = state['proofs'][entry_id]
        node = next((node for node in state.get('graph', {}).get('nodes', []) if node['id'] == entry_id), None)
        mentions = proof_mentions(proof, candidates.values())
        accepted = {
            'evidence': 'current_accepted_proof_text', 'proof_sha256': digest(proof),
            'target_sha256': target_identity(node), 'context_revision': state.get('context_revision', 0),
            'context_imports': deepcopy(state.get('context_imports')),
            'provider_bindings_sha256': digest(state.get('context_provider_bindings')),
            'matches_current_entry': target_identity(node) == target_identity(current_entry or node),
            'mentions': mentions[:attempt_limit],
            'mentions_total': len(mentions),
            'has_more_mentions': len(mentions) > attempt_limit,
            'read_action': {'action': 'read_evidence', 'refs': [{'kind': 'proof', 'id': entry_id}]},
        }
    return {
        'returned': returned[:attempt_limit], 'explicit_fact_reads': reads[-attempt_limit:],
        'attempts': attempts, 'accepted_proof': accepted, 'unavailable_attempts': unavailable,
        'totals': {'returned_queries': len(returned), 'explicit_fact_reads': len(reads),
                   'proof_attempts': len(history)},
        'attempt_display_limit': attempt_limit, 'has_more_attempts': len(history) > attempt_limit,
        'is_proof_authority': False,
        'notice': 'Returned candidates and explicit fact reads are separate events. Fact reads are '
                  'not target-attributed; they are shown here only because this target retrieved the same IDs. '
                  'Proof-text token mentions are not native dependencies or resolved fact versions. '
                  'A failed attempt creates no permanent negative label. Old receipts remain historical '
                  'when target/import/prefix/provider versions change; no retired premise is restored.',
    }
