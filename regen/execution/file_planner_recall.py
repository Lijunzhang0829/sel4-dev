"""Target-driven external facts and certified sealed proof examples.

The target comes from the accepted graph or a bound native observation, never model-supplied proof-state text.
Lexical rankings are suggestions; they confer neither visibility nor proof validity.
"""
import json
from pathlib import Path

from regen.execution.proof_commits import digest, durable
from regen.execution.proof_state_recall import recall_for_proof_state, vector
from regen.planning.premise_retrieval import _is_public_record


def recall_for_node(database, node, request, *, generated_records, audit_path, observation=None,
                    candidate_reranker=None, generated_index=None, lexical_index=None,
                    candidate_resolver=None):
    allowed = {'goal_query', 'context_node_id', 'purpose', 'scope', 'use_vectors', 'proof_hint',
               'retrieval_policy', 'candidate_budget'}
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
    from regen.execution.indexed_retrieval import (
        POLICY, LEGACY_POLICY, DEFAULT_CANDIDATE_BUDGET, validate_policy,
        generated_index_for, generated_candidates, reorder_candidates, upstream_index_for,
    )
    policy = request.get('retrieval_policy', LEGACY_POLICY)
    budget = request.get('candidate_budget', DEFAULT_CANDIDATE_BUDGET)
    validate_policy(policy, budget)
    if policy != POLICY and ('candidate_budget' in request or candidate_reranker is not None):
        raise ValueError('candidate budgets and rerankers require indexed-v1')
    target = '\n'.join([*node.get('context', []), node['statement']])
    upstream = []
    pipeline = {'retrieval_policy': policy, 'candidate_budget': budget}
    if scope in {'all', 'upstream'} and purpose == 'helper':
        index = Path(database).parent/'premise-vectors.sqlite' if use_vectors else None
        if index is not None and not index.is_file():
            raise ValueError('requested vector index unavailable')
        source_index = (lexical_index or upstream_index_for(database,
            Path(audit_path).parent / 'upstream-candidate-indexes')) if policy == POLICY else None
        page = recall_for_proof_state(database, target, limit=budget if policy == POLICY else 5,
                                      vector_index=index, retrieval_policy=policy, candidate_budget=budget,
                                      lexical_index=source_index)
        upstream = page['records']
        pipeline['upstream'] = page.get('candidate_pipeline')
    q = vector(target + '\n' + hint, 65536)
    sealed = []
    if scope in {'all', 'sealed'} and Path(generated_records).is_file():
        bounded_generated = policy == POLICY or candidate_resolver is not None
        if bounded_generated:
            index_path = generated_index or generated_index_for(
                generated_records, Path(audit_path).parent / 'generated-candidate-indexes')
            page = generated_candidates(generated_records, index_path, target + '\n' + hint,
                                        purpose=purpose, candidate_budget=budget)
            generated_pool = page['records']
            pipeline['generated'] = {key: value for key, value in page.items() if key != 'records'}
        else:
            generated_pool = json.loads(Path(generated_records).read_text()).get('records', [])
        scored = rejected = 0
        for item in generated_pool:
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
            if candidate_resolver is not None:
                item = candidate_resolver(item)
                if item is None:
                    rejected += 1
                    continue
            text = '\n'.join([item['canonical_name'], item['exact_statement'], *item.get('context', [])])
            if purpose == 'proof_example':
                text += '\n' + body
            v = vector(text, 65536)
            scored += 1
            score = sum(value*v.get(key, 0) for key, value in q.items())
            if score > 0:
                sealed.append({**item, 'score':score, 'match_kind':purpose,
                    'availability':item.get('availability','frozen_import_candidate_requires_current_context_check')})
        if bounded_generated:
            pipeline['generated']['vector_candidates_scored'] = scored
            pipeline['generated']['unverified_candidates_rejected'] = rejected
    sealed.sort(key=lambda item:(-item['score'], item['record_id']))
    # Interleave source ranks. Scores from FTS and lexical cosine are not comparable.
    records = []
    combined_limit = budget if policy == POLICY else 5
    for rank in range(max(len(upstream), len(sealed))):
        for page in (upstream, sealed):
            if rank < len(page) and len(records) < combined_limit:
                records.append(page[rank])
    if policy == POLICY:
        records, rerank = reorder_candidates(records, candidate_reranker,
            {'target': target, 'node': node, 'observation': observation,
             'retrieval_policy': policy, 'candidate_budget': budget})
        pipeline['combined_candidates'] = len(records)
        pipeline['structural_rerank'] = rerank
        if candidate_resolver is not None and rerank is not None:
            rerank['eligible_for_native_matching'] = sum(
                'visibility' not in row or row.get('can_use_as_current_premise', False) for row in records)
            rerank['deferred_requires_import'] = sum(bool(row.get('requires_import')) for row in records)
        records = records[:5]
    summary = {'mode':'intermediate_goal' if observation else 'node_goal', 'purpose':purpose, 'scope':scope,
        'context_node_id':node['id'], 'target_sha256':digest(target),
        **({'observation':observation} if observation else {}),
        'status':'matched' if records else 'no_match',
        'selected_record_ids':[item['record_id'] for item in records]}
    result = {'kind':'fact_selection', 'status':'available', 'records':records,
        'queries':[summary], 'ranking':'upstream-fts-rrf; sealed-lexical-cosine; source-rank-interleave',
        'notice':('Target is an exact bound native diagnostic residual. ' if observation else
                  'Target is the graph entry goal, not a live intermediate proof state. ')+
                 'Generated candidates and proof examples require certified provider exports. '
                 'Inspect visibility/requires_import; discovery alone does not expose a theorem. '
                 'Upstream proof bodies are not stored. '
                 'Lexical similarity is not a certificate of equivalent proof strategy or applicability.'}
    if policy == POLICY:
        result['candidate_pipeline'] = pipeline
        result['ranking'] = ('bounded-upstream-fts-rrf; generated-normalized-fts-then-cosine; '
                             'source-rank-interleave' + ('; bounded-candidate-reranker' if candidate_reranker else ''))
        summary.update(retrieval_policy=policy, candidate_budget=budget)
    elif candidate_resolver is not None:
        result['candidate_pipeline'] = pipeline
        result['ranking'] = 'upstream-legacy; repository-sealed-bounded-index-then-cosine; source-rank-interleave'
    durable(Path(audit_path), {'request':request, 'target':target, **result}, immutable=True)
    return result
