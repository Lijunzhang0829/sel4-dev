"""Target-driven external facts and certified sealed proof examples.

The target comes from the accepted graph or a bound native observation, never model-supplied proof-state text.
Lexical rankings are suggestions; they confer neither visibility nor proof validity.
"""
import json
from pathlib import Path

from regen.execution.proof_commits import digest, durable
from regen.execution.proof_state_recall import recall_for_proof_state, vector
from regen.planning.premise_retrieval import _is_public_record


def recall_for_node(database, node, request, *, generated_records, audit_path, observation=None):
    allowed = {'goal_query', 'context_node_id', 'purpose', 'scope', 'use_vectors', 'proof_hint'}
    if set(request) - allowed or request.get('goal_query') is not True:
        raise ValueError('goal_query must be true and used without premise/proof-state queries')
    scope = request.get('scope', 'all')
    purpose = request.get('purpose', 'helper')
    hint = request.get('proof_hint', '')
    if scope not in {'all', 'upstream', 'sealed'} or purpose not in {'helper', 'proof_example'}:
        raise ValueError('invalid external recall scope or purpose')
    if not isinstance(hint, str) or len(hint) > 8000 or (hint and purpose != 'proof_example'):
        raise ValueError('proof_hint is optional text for proof_example only (at most 8000 characters)')
    use_vectors = request.get('use_vectors', False)
    if not isinstance(use_vectors, bool):
        raise ValueError('use_vectors must be boolean')
    if use_vectors and (scope == 'sealed' or purpose == 'proof_example'):
        raise ValueError('optional vector index applies to upstream helper recall only')
    target = '\n'.join([*node.get('context', []), node['statement']])
    upstream = []
    if scope in {'all', 'upstream'} and purpose == 'helper':
        index = Path(database).parent/'premise-vectors.sqlite' if use_vectors else None
        if index is not None and not index.is_file():
            raise ValueError('requested vector index unavailable')
        upstream = recall_for_proof_state(database, target, limit=5, vector_index=index)['records']
    q = vector(target + '\n' + hint, 65536)
    sealed = []
    if scope in {'all', 'sealed'} and Path(generated_records).is_file():
        for item in json.loads(Path(generated_records).read_text()).get('records', []):
            if (item.get('source_class') != 'generated_theorem' or not item.get('theory_seal_id')
                    or item.get('status') != 'sealed' or not _is_public_record(item)):
                continue
            item = dict(item)
            body = item.get('proof_body')
            certified_example = (isinstance(body, str) and bool(body.strip())
                and bool(item.get('proof_commit_id')) and digest(body) == item.get('proof_sha256'))
            if purpose == 'proof_example' and not certified_example:
                continue
            if not certified_example:
                item.pop('proof_body', None)
            text = '\n'.join([item['canonical_name'], item['exact_statement'], *item.get('context', [])])
            if purpose == 'proof_example':
                text += '\n' + body
            v = vector(text, 65536)
            score = sum(value*v.get(key, 0) for key, value in q.items())
            if score > 0:
                sealed.append({**item, 'score':score, 'match_kind':purpose,
                    'availability':'frozen_import_candidate_requires_current_context_check'})
    sealed.sort(key=lambda item:(-item['score'], item['record_id']))
    # Interleave source ranks. Scores from FTS and lexical cosine are not comparable.
    records = []
    for rank in range(5):
        for page in (upstream, sealed):
            if rank < len(page) and len(records) < 5:
                records.append(page[rank])
    summary = {'mode':'intermediate_goal' if observation else 'node_goal', 'purpose':purpose, 'scope':scope,
        'context_node_id':node['id'], 'target_sha256':digest(target),
        **({'observation':observation} if observation else {}),
        'status':'matched' if records else 'no_match',
        'selected_record_ids':[item['record_id'] for item in records]}
    result = {'kind':'fact_selection', 'status':'available', 'records':records,
        'queries':[summary], 'ranking':'upstream-fts-rrf; sealed-lexical-cosine; source-rank-interleave',
        'notice':('Target is an exact bound native diagnostic residual. ' if observation else
                  'Target is the graph entry goal, not a live intermediate proof state. ')+
                 'Proof examples come only from imported sealed generated proofs; upstream proof bodies are not stored. '
                 'Lexical similarity is not a certificate of equivalent proof strategy or applicability.'}
    durable(Path(audit_path), {'request':request, 'target':target, **result}, immutable=True)
    return result
