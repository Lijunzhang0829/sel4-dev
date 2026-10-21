"""Online source-order checks of model-authored append candidates.

The model's draft is never rewritten. Each accepted declaration has its own
ordinary document-check evidence and immutable journal commit. This module
neither retrieves proofs nor supplies mathematical statements or proof bodies.
"""
from copy import deepcopy
from pathlib import Path
import time

from regen.execution.proof_commits import durable
from regen.planning.theory_document import (
    baseline, block, candidate, check_document, header, source_hash,
    validate_document_commit,
)

STRATEGY = 'bounded-audited-prefix-v1'


def eligible_declarations(current, projected, eligible_ids=None):
    """Return new proof IDs admitted by an orthogonal search policy.

    ``None`` preserves the historical bounded-prefix behaviour. A concrete
    collection is a fail-closed online scheduling boundary: declarations stay
    in the model-authored draft, but only IDs selected by the search policy may
    reach native checking and journal acceptance in this action.
    """
    keys = [key for key in projected['accepted_order']
            if key not in current['proofs']]
    if eligible_ids is None:
        return keys
    if (not isinstance(eligible_ids, list)
            or any(not isinstance(key, str) or not key for key in eligible_ids)
            or len(set(eligible_ids)) != len(eligible_ids)):
        raise ValueError('eligible_ids must be a unique list of declaration IDs')
    known = {node['id'] for node in projected['graph']['nodes']}
    unknown = set(eligible_ids) - known
    if unknown:
        raise ValueError('search policy selected unknown declarations: '
                         + ', '.join(sorted(unknown)))
    allowed = set(eligible_ids)
    return [key for key in keys if key in allowed]


def one_declaration(package, current, proposed, key, imports):
    """One proposed proof plus all existing unproved obligations, never deletion."""
    if baseline(current, proposed)[1] != 'append':
        raise ValueError('bounded prefix requires an unchanged accepted prefix')
    before = {n['id']:n for n in current['graph']['nodes']}
    after = {n['id']:n for n in proposed['graph']['nodes']}
    if key in current['proofs'] or key not in proposed['proofs']:
        raise ValueError('bounded prefix requires a new model-authored proof')
    entries = [before[k] for k in current['accepted_order']]
    entries.append(after[key])
    entries.extend(n for n in current['graph']['nodes']
                   if n['id'] not in current['proofs'] and n['id'] != key)
    proofs = {**current['proofs'], key:proposed['proofs'][key]}
    source = header(package['theory'], imports)+''.join(
        block(n, proofs.get(n['id'])) for n in entries)+'end\n'
    new = candidate(package, current, source, imports)
    if baseline(current, new)[1] != 'append':
        raise ValueError('bounded source projection changed the accepted prefix')
    return new, source


def run_prefix(package, state, source, checker, directory, *, commit,
               evidence_origin, eligible_ids=None):
    """Return feedback, or None to use the explicitly recorded whole-check fallback.

    commit(new, evidence) must append the normal document journal record and
    update the caller's current state immediately, including before a subsequent
    check can fail or be cancelled. One original model request remains charged;
    per-segment receipts record every independently audited native check.
    """
    from regen.execution.check_tasks import NativePreparationError, check_cancelled
    directory = Path(directory)
    projected = candidate(package, state, source, checker.imports())
    mode = baseline(state, projected)[1]
    draft_keys = [key for key in projected['accepted_order']
                  if key not in state['proofs']]
    keys = eligible_declarations(state, projected, eligible_ids)
    policy = {'strategy':STRATEGY, 'original_source_sha256':source_hash(source),
              'proposed_ids':draft_keys, 'eligible_ids':keys,
              'unselected_ids':[key for key in draft_keys if key not in set(keys)],
              'search_scope_enforced':eligible_ids is not None, 'check_mode':mode,
              'stop_policy':'first-red-or-first-accepted-contract',
              'draft_preserved':True}
    if mode != 'append' or not draft_keys:
        durable(directory/'policy.json', {**policy,
            'status':('search_scope_rejected' if eligible_ids is not None
                      else 'whole_check_fallback'),
            'reason':'non_append_rewrite' if mode != 'append' else 'no_new_proofs'}, immutable=True)
        if eligible_ids is not None:
            feedback = {'status':'search_scope_rejected', 'new_green':[],
                'bounded_prefix':{**policy, 'accepted_ids':[], 'pending_ids':keys,
                    'stop_reason':'non_append_scope' if mode != 'append' else 'no_new_proofs',
                    'checks':[],
                    'notice':'A search-scoped action never falls back to whole-document acceptance.'}}
            durable(directory/'result.json', feedback, immutable=True)
            return feedback
        return None
    if eligible_ids is not None and not keys:
        durable(directory/'policy.json', {**policy, 'status':'search_scope_rejected',
            'reason':'no_eligible_new_proofs'}, immutable=True)
        feedback = {'status':'search_scope_rejected', 'new_green':[],
            'bounded_prefix':{**policy, 'accepted_ids':[], 'pending_ids':[],
                'stop_reason':'no_eligible_new_proofs', 'checks':[],
                'notice':'The draft is preserved, but no off-scope declaration was checked or accepted.'}}
        durable(directory/'result.json', feedback, immutable=True)
        return feedback
    durable(directory/'policy.json', policy, immutable=True)
    durable(directory/'original-draft.json', {'source':source}, immutable=True)
    current = state
    checked, accepted = [], []
    feedback = {'status':'accepted_prefix'}
    stop = 'candidate_exhausted'
    original_work_dir = getattr(checker, 'work_dir', None)
    try:
        for number, key in enumerate(keys, 1):
            check_cancelled()
            if hasattr(checker, 'bounded_prefix_cancel_check'):
                checker.bounded_prefix_cancel_check()
            part = directory/f'{number:06d}'
            new, selected_source = one_declaration(package, current, projected, key, checker.imports())
            durable(part/'candidate.json', new, immutable=True)
            durable(part/'document-source.json', {'source':selected_source}, immutable=True)
            if original_work_dir is not None:
                checker.work_dir = part/'isabelle'
                checker.work_dir.mkdir(parents=True, exist_ok=True)
            start = time.monotonic()
            evidence = check_document(checker, current, new, selected_source)
            elapsed = time.monotonic()-start
            evidence.update(deepcopy(evidence_origin))
            evidence['bounded_prefix'] = {**policy, 'segment':number, 'entry_id':key,
                                           'check_wall_seconds':elapsed}
            durable(part/'check.json', evidence, immutable=True)
            checked.append({'entry_id':key, 'verdict':evidence['check']['verdict'],
                            'check_wall_seconds':elapsed, 'evidence_path':str(part/'check.json')})
            if evidence['check'].get('failure_scope') == 'native_preparation':
                raise NativePreparationError(evidence['check'].get('diagnostic', 'native prefix preparation failed'))
            if evidence['check']['verdict'] != 'green':
                feedback = {'status':'rejected_batch', 'check':evidence['check'],
                            'node_results':evidence.get('node_results', [])}
                stop = 'first_red'
                break
            validate_document_commit(package, current, new, evidence)
            check_cancelled()
            if hasattr(checker, 'bounded_prefix_cancel_check'):
                checker.bounded_prefix_cancel_check()
            accepted.append(key)
            new.update(status='generating', reason='', batch_candidate=None,
                batch_feedback={'status':'accepted_prefix', 'new_green':[key],
                    'bounded_prefix':{**policy, 'accepted_ids':list(accepted),
                        'pending_ids':[k for k in keys if k not in accepted],
                        'check_wall_seconds':elapsed},
                    'source_sha256':new['document_source_sha256'],
                    'node_results':evidence.get('node_results', [])})
            new.pop('revision_rejection', None)
            current = commit(new, evidence)
            if next(n for n in new['graph']['nodes'] if n['id']==key)['role']=='contract':
                stop = 'first_accepted_contract'
                break
    finally:
        if original_work_dir is not None:
            checker.work_dir = original_work_dir
    feedback.update(new_green=accepted, bounded_prefix={**policy,
        'accepted_ids':accepted, 'pending_ids':[key for key in keys if key not in accepted],
        'stop_reason':stop, 'checks':checked,
        'notice':'The full original draft remains current; unaccepted proof bodies are candidates only.'})
    durable(directory/'result.json', feedback, immutable=True)
    return feedback
