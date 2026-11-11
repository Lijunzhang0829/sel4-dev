"""Check every selected draft against GREEN facts, committing each success.

Only oops carriers are used for other open goals. No admitted theorem is created.
The saved model draft is never replaced by the isolated check projections.
"""
from copy import deepcopy
from pathlib import Path
from time import monotonic

from regen.execution.proof_commits import durable
from regen.execution.check_tasks import check_cancelled, NativePreparationError
from regen.planning.theory_document import (block, header, candidate, check_document,
    validate_document_commit, source_hash)

POLICY = 'independent-document-entries-v1'


def selected_new_entries(state, proposed):
    """Return independently checkable new bodies; rewrites stay whole-context."""
    if any(proposed['proofs'].get(k) != v for k, v in state['proofs'].items()):
        return []
    before = {n['id']: n for n in state['graph']['nodes']}
    after = {n['id']: n for n in proposed['graph']['nodes']}
    if any(after.get(k) != before[k] for k in state['proofs']):
        return []
    return [k for k in proposed['accepted_order'] if k not in state['proofs']]


def run_checks(package, state, source, checker, directory, *, commit,
               evidence_origin, record_result=None, authorize_entry=None):
    directory = Path(directory)
    # Parse and validate the entire selected draft before any local authorization
    # is considered. Illegal fixed declarations/imports are never local blockers.
    proposed = candidate(package, state, source, checker.imports())
    after = {n['id']: n for n in proposed['graph']['nodes']}
    keys = selected_new_entries(state, proposed)
    if len(keys) < 2:
        return None
    policy = {'policy': POLICY, 'requested_source_sha256': source_hash(source),
              'selected_ids': keys, 'other_open_proofs': 'oops', 'draft_preserved': True}
    durable(directory/'policy.json', policy, immutable=True)
    durable(directory/'original-draft.json', {'source': source}, immutable=True)
    current = state
    results, accepted = [], []
    work = getattr(checker, 'work_dir', None)
    try:
        for number, key in enumerate(keys, 1):
            check_cancelled()
            if hasattr(checker, 'bounded_prefix_cancel_check'):
                checker.bounded_prefix_cancel_check()
            authorization = (authorize_entry(current, key) if authorize_entry is not None
                             else {'status': 'ready', 'sketch_authorizations': []})
            if authorization.get('status') == 'blocked_workflow':
                part = directory/f'{number:06d}'
                row = {**{k:deepcopy(v) for k,v in authorization.items() if k not in {'entry_id','node_id'}}, 'node_id':key,
                       'verdict':'not_checked', 'native_check_executed':False}
                durable(part/'authorization.json', row, immutable=True)
                results.append(row)
                continue
            if authorization.get('status') != 'ready':
                raise ValueError('invalid independent-entry authorization result')
            # Keep the certified prefix order. A newly accepted predecessor can
            # serve later entries; a failed or later candidate never can.
            nodes = {n['id']: n for n in current['graph']['nodes']}
            entries = [nodes[k] for k in current['accepted_order']] + [after[key]]
            entries += [n for n in proposed['graph']['nodes']
                        if n['id'] not in current['proofs'] and n['id'] != key
                        and (n['role'] == 'contract' or n['id'] in nodes)]
            proofs = {**current['proofs'], key: proposed['proofs'][key]}
            isolated = header(package['theory'], checker.imports()) + ''.join(
                block(n, proofs.get(n['id'])) for n in entries) + 'end\n'
            new = candidate(package, current, isolated, checker.imports())
            part = directory/f'{number:06d}'
            durable(part/'candidate.json', new, immutable=True)
            if work is not None:
                checker.work_dir = part/'isabelle'
                checker.work_dir.mkdir(parents=True, exist_ok=True)
            start = monotonic()
            evidence = check_document(checker, current, new, isolated)
            evidence.update(deepcopy(evidence_origin))
            if authorize_entry is not None:
                evidence['sketch_authorizations'] = deepcopy(authorization['sketch_authorizations'])
            evidence['document_publication_policy'] = 'preserve-accepted-ids-v1'
            evidence['independent_entries'] = {**policy, 'entry_id': key, 'segment': number}
            from regen.execution.proof_batches import classify_native_failure
            check = classify_native_failure(deepcopy(evidence['check']))
            evidence['check'] = check
            durable(part/'check.json', evidence, immutable=True)
            results.append({'node_id': key, 'verdict': check['verdict'],
                            'status': 'audited_green' if check['verdict'] == 'green' else 'failed',
                            'check': deepcopy(check),
                            'node_results': evidence.get('node_results', []),
                            'check_wall_seconds': monotonic()-start,
                            'evidence_path': str(part/'check.json')})
            if check.get('failure_scope') == 'native_preparation':
                raise NativePreparationError(check.get('diagnostic', 'native preparation failed'))
            if record_result is not None:
                record_result(isolated, evidence)
            if check['verdict'] == 'green':
                validate_document_commit(package, current, new, evidence)
                check_cancelled()
                if hasattr(checker, 'bounded_prefix_cancel_check'):
                    checker.bounded_prefix_cancel_check()
                new.update(status='generating', reason='', batch_candidate=None,
                           batch_feedback={'status': 'accepted', 'new_green': [key],
                                           'node_results': evidence.get('node_results', []),
                                           'independent_entries': policy})
                current = commit(new, evidence)
                accepted.append(key)
            # Stop unsafe runtime/evidence failures, continue ordinary RED/timeouts.
            if (check.get('failure_scope') not in (None, '', 'candidate', 'proof-search')
                    or check.get('verdict') == 'infrastructure_error'):
                break
    finally:
        if work is not None:
            checker.work_dir = work
    untested = keys[len(results):]
    blocked = [r['node_id'] for r in results if r.get('status') == 'blocked_workflow']
    native_results = [r for r in results if 'check' in r]
    result = {'status': 'accepted' if len(accepted) == len(keys) else 'checked_entries',
              'new_green': accepted, 'node_results': results,
              'check': next((r['check'] for r in native_results if r['verdict'] != 'green'),
                            native_results[-1]['check'] if native_results else {}),
              'independent_entries': {**policy, 'accepted_ids': accepted,
                                      'not_checked_ids': blocked + untested,
                                      'blocked_workflow_ids': blocked},
              'notice': 'Workflow-blocked entries are not proof failures. Each success has a separate native audit and commit. Failed goals remain pending.'}
    if untested:
        result['node_results'].extend({'node_id':key,'status':'not_reached',
                                       'verdict':'not_checked'} for key in untested)
    failure = next((r['check'] for r in results if 'check' in r
                    and r['check'].get('framework_blocked')), None)
    if failure:
        result.update(framework_blocked=True, failure_scope=failure.get('failure_scope'),
                      diagnostic=failure.get('diagnostic'))
    durable(directory/'result.json', result, immutable=True)
    return result
